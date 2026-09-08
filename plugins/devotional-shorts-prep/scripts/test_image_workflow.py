#!/usr/bin/env python3
"""Approval-gated image generation, persistence, recovery, and delivery checks."""

from __future__ import annotations

import hashlib
import json
import struct
import tempfile
import unittest
import zlib
from datetime import datetime, timezone
from pathlib import Path

import image_workflow
import job_store
import telegram_bot


FIXED_NOW = datetime(2026, 8, 12, 18, 0, 0, tzinfo=timezone.utc)
JOB_ID = "ds-20260812-180000-abcddcba"
CONFIG = telegram_bot.TelegramConfig("123456:" + "A" * 30, -100123, (42,))


def selection(prefix: str) -> dict[str, object]:
    candidates = [{"text": f"{prefix} {number}", "score": 101 - number} for number in range(1, 11)]
    return {
        "selected": candidates[0]["text"],
        "alternatives": [item["text"] for item in candidates[1:4]],
        "candidates": candidates,
    }


def content() -> dict[str, object]:
    return {
        "devotional_points": {
            "background": "본문 배경이다.",
            "interpretation": "본문 해석이다.",
            "core_sentence": "하나님은 진실한 마음을 보신다.",
            "application": "오늘 진실하게 순종한다.",
            "application_questions": ["오늘 무엇에 순종할 것인가?"],
        },
        "script": {
            "core_sentence": "하나님은 진실한 마음을 보신다.",
            "thumbnail": selection("진실한 마음"),
            "opening": selection("무엇을 보고 계실까"),
            "bridge": "본문을 함께 살펴보자.",
            "context": "본문 시대의 배경이다.",
            "passage_summary": "본문은 진실한 드림을 보여준다.",
            "interpretation_application": "오늘 진실한 마음으로 순종한다.",
            "conclusion": "나는 오늘 무엇에 순종할 것인가?",
            "prayer": "주님, 진실하게 살게 하소서.",
            "cta": job_store.FIXED_CTA,
            "full_text": "본문을 듣고 오늘 진실하게 순종한다.",
            "estimated_seconds": 110,
            "duration_exception": False,
            "duration_exception_reason": None,
        },
        "scenes": [
            {
                "source_text": "본문 시대의 배경과 의미를 함께 살펴본다.",
                "section": "biblical",
                "setting": "biblical_era",
                "description_ko": "고대 근동 배경의 성경 장면",
                "prompt_en": "An ancient devotional scene",
            },
            {
                "source_text": "오늘 진실한 마음으로 순종한다.",
                "section": "modern_application",
                "setting": "modern",
                "description_ko": "말씀 앞에 선 현대 인물",
                "prompt_en": "A modern adult reflecting on Scripture",
            },
            {
                "source_text": "주님, 진실하게 살게 하소서.",
                "section": "prayer",
                "setting": "modern",
                "description_ko": "조용히 기도하는 현대 인물",
                "prompt_en": "A modern adult praying",
            },
        ],
    }


def png(width: int = 9, height: int = 16) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)

    rows = b"".join(b"\x00" + b"\x80\x40\x20" * width for _ in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def jpeg(width: int = 9, height: int = 16) -> bytes:
    sof = b"\x08" + struct.pack(">HH", height, width) + b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00"
    sos = b"\x03\x01\x00\x02\x11\x03\x11\x00\x3f\x00"
    return b"\xff\xd8\xff\xc0" + struct.pack(">H", len(sof) + 2) + sof + b"\xff\xda" + struct.pack(">H", len(sos) + 2) + sos + b"\x00\xff\xd9"


def webp(width: int = 9, height: int = 16) -> bytes:
    packed = bytes(
        (
            (width - 1) & 0xFF,
            ((width - 1) >> 8) | (((height - 1) & 0x3) << 6),
            ((height - 1) >> 2) & 0xFF,
            ((height - 1) >> 10) & 0x0F,
        )
    )
    payload = b"\x2f" + packed
    chunk = b"VP8L" + struct.pack("<I", len(payload)) + payload + b"\x00"
    return b"RIFF" + struct.pack("<I", len(chunk) + 4) + b"WEBP" + chunk


class FakeAPI:
    def __init__(self, *, fail_scene: int | None = None) -> None:
        self.calls: list[tuple[str, dict[str, object], dict[str, Path]]] = []
        self.next_id = 9000
        self.photo_number = 0
        self.fail_scene = fail_scene

    def call(self, method: str, params: dict[str, object] | None = None, files: dict[str, Path] | None = None) -> object:
        self.calls.append((method, dict(params or {}), dict(files or {})))
        if method == "getMe":
            return {"id": 777, "is_bot": True}
        if method == "getWebhookInfo":
            return {"url": "", "pending_update_count": 0}
        if method == "getChat":
            return {
                "id": CONFIG.chat_id,
                "type": "supergroup",
                "permissions": {
                    "can_send_messages": True,
                    "can_send_documents": True,
                    "can_send_photos": True,
                },
            }
        if method == "getChatMember":
            return {"status": "administrator"}
        if method == "sendPhoto":
            self.photo_number += 1
            if self.photo_number == self.fail_scene:
                raise telegram_bot.TelegramError("사진 전송 실패")
        if method in {"sendPhoto", "sendMessage"}:
            self.next_id += 1
            return {"message_id": self.next_id}
        raise AssertionError(method)


class ImageWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary.name) / "project"
        self.project.mkdir()
        self.method = Path(self.temporary.name) / "method.md"
        self.method.write_text("# 방법론\n\n방법론 버전: `1.0.0`\n", encoding="utf-8")
        self.input_data = {
            "passage_text": "가난한 과부가 두 렙돈을 드렸다.",
            "passage_reference": "누가복음 21:1-4",
            "title": "두 렙돈",
            "date": "2026-08-12",
            "special_instructions": [],
            "visual_overrides": None,
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def create(self, *, approve: bool = False) -> dict[str, object]:
        job = job_store.create_draft(
            self.project,
            self.input_data,
            content(),
            "# 검토 보고서\n",
            self.method,
            job_id=JOB_ID,
            now=FIXED_NOW,
        )
        if approve:
            job_store.mark_report_sent(self.project, JOB_ID, 7001, report_sent_at=FIXED_NOW)
            job = job_store.record_decision(
                self.project,
                JOB_ID,
                "approved",
                7001,
                approver_ids=[42],
                feedback=["승인합니다"],
                idempotency_key="approval:image-workflow",
                decided_at=FIXED_NOW,
            )
        return job

    def image(self, name: str, *, width: int = 9, height: int = 16) -> Path:
        path = Path(self.temporary.name) / name
        path.write_bytes(png(width, height))
        return path

    def start(self) -> None:
        image_workflow.start_generation(self.project, JOB_ID, now=FIXED_NOW)

    def invoke_imagegen_contract(self, job_id: str, calls: list[str]) -> None:
        image_workflow.start_generation(self.project, job_id, now=FIXED_NOW)
        item = image_workflow.next_scene(self.project, job_id, now=FIXED_NOW)
        if item["ready"]:
            calls.append(item["prompt_en"])

    def generate_scene(self, source: Path | None = None) -> dict[str, object]:
        next_item = image_workflow.next_scene(self.project, JOB_ID, now=FIXED_NOW)
        return image_workflow.record_success(
            self.project,
            JOB_ID,
            next_item["scene_number"],
            source or self.image(f"source-{next_item['scene_number']}.png"),
            now=FIXED_NOW,
        )

    def test_visual_prompt_resolution_is_stable_and_respects_override(self) -> None:
        scenes = content()["scenes"]
        first = image_workflow.resolve_visual_prompts(self.input_data, scenes, JOB_ID)
        second = image_workflow.resolve_visual_prompts(self.input_data, scenes, JOB_ID)
        self.assertEqual(first, second)
        self.assertIn("historically accurate ancient Near Eastern setting", first[0]["prompt_en"])
        self.assertIn("first-century eastern Mediterranean", first[0]["prompt_en"])
        self.assertIn("contemporary Korean setting", first[1]["prompt_en"])
        self.assertIn("safe top and bottom caption space", first[2]["prompt_en"])
        self.assertEqual(first[0]["setting"], "biblical_era")
        self.assertEqual([scene["setting"] for scene in first[1:]], ["modern", "modern"])
        modern = [scene["prompt_en"] for scene in first[1:]]
        assigned = [persona for persona in image_workflow.PERSONAS if any(persona in prompt for prompt in modern)]
        self.assertEqual(len(assigned), 2)
        self.assertNotEqual(assigned[0], assigned[1])

        overridden_input = {
            **self.input_data,
            "visual_overrides": {
                "style": "watercolor",
                "person": "a Korean woman in her 40s",
                "scene_instructions": {"2": "close-up framing"},
            },
        }
        overridden = image_workflow.resolve_visual_prompts(overridden_input, scenes, JOB_ID)
        self.assertNotIn("photorealistic", overridden[1]["prompt_en"])
        self.assertIn("watercolor", overridden[1]["prompt_en"])
        self.assertIn("close-up framing", overridden[1]["prompt_en"])
        self.assertIn("a Korean woman in her 40s", overridden[1]["prompt_en"])
        self.assertIn("9:16 vertical composition", overridden[1]["prompt_en"])
        self.assertEqual(overridden[1]["source_text"], scenes[1]["source_text"])
        self.assertEqual(overridden[1]["description_ko"], scenes[1]["description_ko"])

    def test_png_jpeg_and_webp_actual_formats_are_identified_without_filename_trust(self) -> None:
        fixtures = (("wrong.bin", png(), "png"), ("wrong.data", jpeg(), "jpg"), ("wrong.raw", webp(), "webp"))
        for name, payload, extension in fixtures:
            with self.subTest(extension=extension):
                path = Path(self.temporary.name) / name
                path.write_bytes(payload)
                metadata = job_store.inspect_image(path)
                self.assertEqual(metadata["extension"], extension)
                self.assertEqual((metadata["width"], metadata["height"]), (9, 16))

    def test_approval_gate_rejects_unapproved_and_tampered_jobs_without_attempt(self) -> None:
        self.create()
        imagegen_calls: list[str] = []
        with self.assertRaisesRegex(image_workflow.ImageWorkflowError, "승인"):
            self.invoke_imagegen_contract(JOB_ID, imagegen_calls)
        self.assertEqual(imagegen_calls, [])
        job = job_store.load_job(self.project, JOB_ID)
        self.assertEqual(job["status"], "DRAFT")
        self.assertFalse(any(event["type"] == "scene_generation_started" for event in job["events"]))

        job_path = self.project / "jobs" / JOB_ID / "job.json"
        job_store.mark_report_sent(self.project, JOB_ID, 7001, report_sent_at=FIXED_NOW)
        job_store.record_decision(
            self.project,
            JOB_ID,
            "approved",
            7001,
            approver_ids=[42],
            idempotency_key="approval:tamper",
            decided_at=FIXED_NOW,
        )
        tampered = json.loads(job_path.read_text(encoding="utf-8"))
        scene = tampered["revisions"][0]["scenes"][0]
        scene["prompt_en"] = "Changed after approval"
        scene["prompt_hash"] = hashlib.sha256(scene["prompt_en"].encode()).hexdigest()
        job_path.write_text(json.dumps(tampered, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(image_workflow.ImageWorkflowError, "무결성"):
            self.invoke_imagegen_contract(JOB_ID, imagegen_calls)
        self.assertEqual(imagegen_calls, [])
        self.assertEqual(job_store.load_job(self.project, JOB_ID)["status"], "NEEDS_REVIEW")

    def test_approval_gate_rejects_every_nonapproved_workflow_state_without_attempt(self) -> None:
        scenarios = (
            ("DRAFT", None),
            ("SENT_FOR_APPROVAL", None),
            ("REVISION_REQUESTED", "revision_requested"),
            ("HOLD", "hold"),
        )
        for index, (expected_state, decision) in enumerate(scenarios, 1):
            with self.subTest(state=expected_state):
                job_id = f"ds-20260812-18000{index}-abcddc{index:02x}"
                job_store.create_draft(
                    self.project,
                    self.input_data,
                    content(),
                    "# 검토 보고서\n",
                    self.method,
                    job_id=job_id,
                    now=FIXED_NOW,
                )
                if expected_state != "DRAFT":
                    job_store.mark_report_sent(
                        self.project, job_id, 7000 + index, report_sent_at=FIXED_NOW
                    )
                if decision:
                    job_store.record_decision(
                        self.project,
                        job_id,
                        decision,
                        7000 + index,
                        approver_ids=[42],
                        idempotency_key=f"approval:blocked:{index}",
                        decided_at=FIXED_NOW,
                    )
                imagegen_calls: list[str] = []
                with self.assertRaisesRegex(image_workflow.ImageWorkflowError, "승인"):
                    self.invoke_imagegen_contract(job_id, imagegen_calls)
                self.assertEqual(imagegen_calls, [])
                stored = job_store.load_job(self.project, job_id)
                self.assertEqual(stored["status"], expected_state)
                self.assertFalse(
                    any(event["type"] == "scene_generation_started" for event in stored["events"])
                )

    def test_approval_gate_rejects_later_hold_event_without_generation_attempt(self) -> None:
        self.create(approve=True)
        job_path = self.project / "jobs" / JOB_ID / "job.json"
        job = json.loads(job_path.read_text(encoding="utf-8"))
        job["events"].append(
            {
                "at": FIXED_NOW.isoformat(),
                "type": "approval_decision",
                "revision": 1,
                "from": "APPROVED",
                "to": "HOLD",
                "idempotency_key": "approval:later-hold",
                "details": {"decision": "hold"},
            }
        )
        job_path.write_text(json.dumps(job, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        with self.assertRaisesRegex(image_workflow.ImageWorkflowError, "수정 요청 또는 보류"):
            image_workflow.start_generation(self.project, JOB_ID, now=FIXED_NOW)

        stored = job_store.load_job(self.project, JOB_ID)
        self.assertEqual(stored["status"], "APPROVED")
        self.assertFalse(any(event["type"] == "scene_generation_started" for event in stored["events"]))

    def test_start_next_success_and_idempotency_preserve_normal_image(self) -> None:
        self.create(approve=True)
        first_start = image_workflow.start_generation(self.project, JOB_ID, now=FIXED_NOW)
        repeated_start = image_workflow.start_generation(self.project, JOB_ID, now=FIXED_NOW)
        self.assertEqual(first_start["status"], "GENERATING")
        self.assertTrue(repeated_start["skipped"])
        item = image_workflow.next_scene(self.project, JOB_ID, now=FIXED_NOW)
        self.assertEqual(item["scene_number"], 1)
        repeated_item = image_workflow.next_scene(self.project, JOB_ID, now=FIXED_NOW)
        self.assertEqual(repeated_item["attempt"], 1)
        self.assertTrue(repeated_item["attempt_already_reserved"])
        source = self.image("generated.png")
        result = image_workflow.record_success(
            self.project, JOB_ID, 1, source, now=FIXED_NOW
        )
        saved = self.project / result["local_path"]
        before = saved.read_bytes()
        repeated = image_workflow.record_success(
            self.project, JOB_ID, 1, source, now=FIXED_NOW
        )
        self.assertEqual(repeated["image_hash"], result["image_hash"])
        self.assertEqual(saved.read_bytes(), before)
        self.assertEqual(job_store.validate_job(self.project, JOB_ID), [])

    def test_file_validation_rejects_corrupt_horizontal_and_overwrite(self) -> None:
        self.create(approve=True)
        self.start()
        image_workflow.next_scene(self.project, JOB_ID, now=FIXED_NOW)
        corrupt = Path(self.temporary.name) / "corrupt.png"
        corrupt.write_bytes(b"not an image")
        with self.assertRaisesRegex(image_workflow.ImageWorkflowError, "형식"):
            image_workflow.record_success(self.project, JOB_ID, 1, corrupt, now=FIXED_NOW)
        with self.assertRaisesRegex(image_workflow.ImageWorkflowError, "세로형"):
            image_workflow.record_success(
                self.project, JOB_ID, 1, self.image("wide.png", width=16, height=9), now=FIXED_NOW
            )
        first = image_workflow.record_success(
            self.project, JOB_ID, 1, self.image("first.png"), now=FIXED_NOW
        )
        with self.assertRaisesRegex(image_workflow.ImageWorkflowError, "덮어쓸"):
            image_workflow.record_success(
                self.project, JOB_ID, 1, self.image("changed.png", width=10, height=18), now=FIXED_NOW
            )
        self.assertTrue((self.project / first["local_path"]).is_file())

    def test_failure_retries_once_then_needs_review_and_resume_only_failed(self) -> None:
        self.create(approve=True)
        self.start()
        first = image_workflow.next_scene(self.project, JOB_ID, now=FIXED_NOW)
        failed_once = image_workflow.record_failure(
            self.project, JOB_ID, first["scene_number"], "일시 생성 실패", now=FIXED_NOW
        )
        self.assertTrue(failed_once["retry_allowed"])
        second = image_workflow.next_scene(self.project, JOB_ID, now=FIXED_NOW)
        self.assertEqual(second["scene_number"], first["scene_number"])
        self.assertEqual(second["attempt"], 2)
        final = image_workflow.record_failure(
            self.project, JOB_ID, second["scene_number"], "두 번째 실패", now=FIXED_NOW
        )
        self.assertEqual(final["status"], "NEEDS_REVIEW")
        self.assertFalse(final["retry_allowed"])

        resumed = image_workflow.resume_failed(
            self.project,
            JOB_ID,
            "동일 승인 프롬프트 재개",
            "resume:failed:1",
            now=FIXED_NOW,
        )
        self.assertEqual(resumed["status"], "GENERATING")
        stored = job_store.current_revision(job_store.load_job(self.project, JOB_ID))["scenes"]
        self.assertEqual(stored[0]["status"], "PENDING")
        self.assertEqual(stored[0]["attempts"], 0)
        self.assertEqual(stored[1]["status"], "PENDING")

    def test_first_failure_then_second_attempt_success(self) -> None:
        self.create(approve=True)
        self.start()
        first = image_workflow.next_scene(self.project, JOB_ID, now=FIXED_NOW)
        failure = image_workflow.record_failure(
            self.project, JOB_ID, first["scene_number"], "첫 생성 실패", now=FIXED_NOW
        )
        self.assertTrue(failure["retry_allowed"])
        retry = image_workflow.next_scene(self.project, JOB_ID, now=FIXED_NOW)
        self.assertEqual(retry["attempt"], 2)

        result = image_workflow.record_success(
            self.project,
            JOB_ID,
            retry["scene_number"],
            self.image("retry-success.png"),
            now=FIXED_NOW,
        )

        self.assertEqual(result["scene_status"], "GENERATED")
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(job_store.validate_job(self.project, JOB_ID), [])

    def test_unavailable_returns_to_approved_before_any_attempt(self) -> None:
        self.create(approve=True)
        self.start()
        result = image_workflow.abort_unavailable(
            self.project, JOB_ID, "내장 ImageGen 사용 불가", now=FIXED_NOW
        )
        self.assertEqual(result["status"], "APPROVED")
        job = job_store.load_job(self.project, JOB_ID)
        self.assertTrue(all(scene["attempts"] == 0 for scene in job_store.current_revision(job)["scenes"]))

    def test_delivery_summary_and_final_state_are_idempotent(self) -> None:
        self.create(approve=True)
        self.start()
        while True:
            item = image_workflow.next_scene(self.project, JOB_ID, now=FIXED_NOW)
            if not item["ready"]:
                break
            image_workflow.record_success(
                self.project,
                JOB_ID,
                item["scene_number"],
                self.image(f"scene-source-{item['scene_number']}.png"),
                now=FIXED_NOW,
            )
        api = FakeAPI()
        result = image_workflow.deliver(
            self.project, JOB_ID, CONFIG, api=api, now=FIXED_NOW
        )
        self.assertEqual(result["status"], "DELIVERED")
        self.assertEqual([call[0] for call in api.calls].count("sendPhoto"), 3)
        self.assertEqual([call[0] for call in api.calls].count("sendMessage"), 1)
        self.assertIn("로컬 폴더:", api.calls[-1][1]["text"])

        no_network = FakeAPI()
        repeated = image_workflow.deliver(
            self.project, JOB_ID, CONFIG, api=no_network, now=FIXED_NOW
        )
        self.assertTrue(repeated["skipped"])
        self.assertEqual(no_network.calls, [])
        self.assertEqual(job_store.validate_job(self.project, JOB_ID), [])

    def test_partial_telegram_failure_preserves_images_and_only_retries_delivery(self) -> None:
        self.create(approve=True)
        self.start()
        while True:
            item = image_workflow.next_scene(self.project, JOB_ID, now=FIXED_NOW)
            if not item["ready"]:
                break
            image_workflow.record_success(
                self.project,
                JOB_ID,
                item["scene_number"],
                self.image(f"partial-{item['scene_number']}.png"),
                now=FIXED_NOW,
            )
        first_api = FakeAPI(fail_scene=2)
        first = image_workflow.deliver(
            self.project, JOB_ID, CONFIG, api=first_api, now=FIXED_NOW
        )
        self.assertEqual(first["status"], "GENERATING")
        self.assertTrue(first["transport_failures"])
        stored = job_store.current_revision(job_store.load_job(self.project, JOB_ID))["scenes"]
        self.assertEqual([scene["status"] for scene in stored], ["DELIVERED", "GENERATED", "GENERATED"])

        retry_api = FakeAPI()
        result = image_workflow.deliver(
            self.project, JOB_ID, CONFIG, api=retry_api, now=FIXED_NOW
        )
        self.assertEqual(result["status"], "DELIVERED")
        self.assertEqual([call[0] for call in retry_api.calls].count("sendPhoto"), 2)

    def test_generation_failure_delivers_successes_and_deduplicates_failure_summary(self) -> None:
        self.create(approve=True)
        self.start()
        self.generate_scene()
        failed_scene = image_workflow.next_scene(self.project, JOB_ID, now=FIXED_NOW)
        image_workflow.record_failure(
            self.project, JOB_ID, failed_scene["scene_number"], "첫 실패", now=FIXED_NOW
        )
        retry = image_workflow.next_scene(self.project, JOB_ID, now=FIXED_NOW)
        image_workflow.record_failure(
            self.project, JOB_ID, retry["scene_number"], "두 번째 실패", now=FIXED_NOW
        )

        first_api = FakeAPI()
        first = image_workflow.deliver(
            self.project, JOB_ID, CONFIG, api=first_api, now=FIXED_NOW
        )
        self.assertEqual(first["status"], "NEEDS_REVIEW")
        self.assertEqual([call[0] for call in first_api.calls].count("sendPhoto"), 1)
        self.assertEqual([call[0] for call in first_api.calls].count("sendMessage"), 1)
        self.assertIn("실패: 1개", first_api.calls[-1][1]["text"])
        self.assertIn("미생성: 1개", first_api.calls[-1][1]["text"])

        repeated_api = FakeAPI()
        repeated = image_workflow.deliver(
            self.project, JOB_ID, CONFIG, api=repeated_api, now=FIXED_NOW
        )
        self.assertTrue(repeated["summary_skipped"])
        self.assertFalse(any(call[0].startswith("send") for call in repeated_api.calls))


if __name__ == "__main__":
    unittest.main()
