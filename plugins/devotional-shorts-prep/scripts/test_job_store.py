#!/usr/bin/env python3
"""Standard-library regression checks for job_store.py."""

from __future__ import annotations

import copy
import contextlib
import io
import json
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timezone
from pathlib import Path

import job_store


FIXED_NOW = datetime(2026, 8, 12, 10, 11, 12, tzinfo=timezone.utc)
JOB_ID = "ds-20260812-101112-abcdef12"


def selection(prefix: str) -> dict[str, object]:
    candidates = [{"text": f"{prefix} {number}", "score": 100 - number} for number in range(1, 11)]
    return {
        "selected": candidates[0]["text"],
        "alternatives": [item["text"] for item in candidates[1:4]],
        "candidates": candidates,
    }


def content() -> dict[str, object]:
    return {
        "devotional_points": {
            "background": "본문 배경이다.",
            "interpretation": "핵심 해석이다.",
            "core_sentence": "하나님의 말씀을 신뢰하는 삶이다.",
            "application": "오늘 말씀을 따라 한 걸음 순종한다.",
            "application_questions": ["오늘 무엇에 순종할 것인가?"],
        },
        "script": {
            "core_sentence": "하나님의 말씀을 신뢰하는 삶이다.",
            "thumbnail": selection("썸네일"),
            "opening": selection("오프닝 질문"),
            "bridge": "본문을 함께 살펴보자.",
            "context": "",
            "passage_summary": "본문은 말씀을 신뢰하라고 전한다.",
            "interpretation_application": "오늘 그 말씀을 따라 순종한다.",
            "conclusion": "나는 오늘 무엇에 순종할 것인가?",
            "prayer": "주님, 말씀을 따라 살게 하소서.",
            "cta": job_store.FIXED_CTA,
            "full_text": "본문을 듣고 오늘 말씀을 따라 한 걸음 순종한다.",
            "estimated_seconds": 110,
            "duration_exception": False,
            "duration_exception_reason": None,
        },
        "scenes": [
            {
                "source_text": "본문을 듣고 오늘 말씀을 따라 한 걸음 순종한다.",
                "section": "modern_application",
                "setting": "modern",
                "description_ko": "말씀을 읽고 실천하는 현대 인물",
                "prompt_en": "A modern Korean adult reading Scripture, warm light, vertical 9:16",
            }
        ],
    }


class JobStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary.name) / "project"
        self.project.mkdir()
        self.method = Path(self.temporary.name) / "method.md"
        self.method.write_text("# 방법론\n\n방법론 버전: `1.0.0`\n", encoding="utf-8")
        self.input_data = {
            "passage_text": "복 있는 사람은 여호와의 율법을 즐거워한다.",
            "passage_reference": "시편 1:1-2",
            "title": None,
            "date": "2026-08-12",
            "special_instructions": [],
            "visual_overrides": None,
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def create(self) -> dict[str, object]:
        return job_store.create_draft(
            self.project,
            self.input_data,
            content(),
            "# 검토 보고서\n",
            self.method,
            job_id=JOB_ID,
            now=FIXED_NOW,
        )

    def test_create_draft_and_validate(self) -> None:
        job = self.create()
        root = self.project / "jobs" / JOB_ID
        self.assertEqual(job["status"], "DRAFT")
        self.assertEqual(job["current_revision"], 1)
        self.assertTrue((root / "job.json").is_file())
        self.assertTrue((root / "revisions/r001/report.md").is_file())
        self.assertTrue((root / "revisions/r001/method-snapshot.md").is_file())
        self.assertTrue((root / "revisions/r001/images").is_dir())
        self.assertEqual(job_store.validate_job(self.project, JOB_ID), [])
        scene = job["revisions"][0]["scenes"][0]
        self.assertEqual(scene["scene_number"], 1)
        self.assertEqual(scene["status"], "PENDING")
        self.assertEqual(scene["attempts"], 0)

    def test_identifier_and_image_filename_contract(self) -> None:
        generated = job_store.generate_job_id(FIXED_NOW)
        another = job_store.generate_job_id(FIXED_NOW)
        self.assertRegex(generated, r"^ds-20260812-101112-[0-9a-f]{8}$")
        self.assertNotEqual(generated, another)
        self.assertEqual(job_store.scene_filename(1, "PNG"), "scene-001.png")
        prompt_hash = "a" * 64
        self.assertEqual(
            job_store.report_idempotency_key(JOB_ID, 1),
            f"{JOB_ID}:1:report",
        )
        self.assertEqual(
            job_store.image_idempotency_key(JOB_ID, 1, 3, prompt_hash),
            f"{JOB_ID}:1:3:{prompt_hash}",
        )
        with self.assertRaises(job_store.JobStoreError):
            job_store.scene_filename(1, "gif")

    def test_report_and_decision_are_idempotent(self) -> None:
        self.create()
        first = job_store.mark_report_sent(
            self.project,
            JOB_ID,
            7001,
            report_sent_at=FIXED_NOW,
            idempotency_key=f"{JOB_ID}:1:report",
        )
        event_count = len(first["events"])
        repeated = job_store.mark_report_sent(
            self.project,
            JOB_ID,
            7001,
            report_sent_at=FIXED_NOW,
            idempotency_key=f"{JOB_ID}:1:report",
        )
        self.assertEqual(len(repeated["events"]), event_count)
        approved = job_store.record_decision(
            self.project,
            JOB_ID,
            "approved",
            7001,
            approver_ids=[42],
            feedback=["승인한다."],
            idempotency_key="telegram:update:91",
            decided_at=FIXED_NOW,
        )
        repeated_approval = job_store.record_decision(
            self.project,
            JOB_ID,
            "approved",
            7001,
            approver_ids=[42],
            feedback=["승인한다."],
            idempotency_key="telegram:update:91",
            decided_at=FIXED_NOW,
        )
        self.assertEqual(approved["status"], "APPROVED")
        self.assertEqual(len(repeated_approval["events"]), len(approved["events"]))
        self.assertEqual(job_store.validate_job(self.project, JOB_ID), [])

    def test_conflicting_idempotency_inputs_are_rejected(self) -> None:
        self.create()
        job_store.mark_report_sent(
            self.project,
            JOB_ID,
            7001,
            report_sent_at=FIXED_NOW,
            idempotency_key="report:key",
        )
        with self.assertRaises(job_store.JobStoreError):
            job_store.mark_report_sent(
                self.project,
                JOB_ID,
                7002,
                report_sent_at=FIXED_NOW,
                idempotency_key="report:key",
            )
        job_store.record_decision(
            self.project,
            JOB_ID,
            "approved",
            7001,
            approver_ids=[42],
            idempotency_key="decision:key",
            decided_at=FIXED_NOW,
        )
        with self.assertRaises(job_store.JobStoreError):
            job_store.record_decision(
                self.project,
                JOB_ID,
                "hold",
                7001,
                approver_ids=[42],
                idempotency_key="decision:key",
                decided_at=FIXED_NOW,
            )

    def test_explicit_resend_uses_new_message_and_invalidates_old_decision(self) -> None:
        self.create()
        job_store.mark_report_sent(self.project, JOB_ID, 7001, report_sent_at=FIXED_NOW)
        resent = job_store.mark_report_sent(
            self.project,
            JOB_ID,
            7002,
            report_sent_at=FIXED_NOW,
            resend=True,
        )
        revision = resent["revisions"][0]
        self.assertEqual(resent["current_revision"], 1)
        self.assertEqual(revision["telegram"]["report_message_id"], 7002)
        self.assertIsNone(revision["approval"]["decision"])
        with self.assertRaises(job_store.JobStoreError):
            job_store.mark_report_sent(
                self.project,
                JOB_ID,
                7003,
                report_sent_at=FIXED_NOW,
                idempotency_key="unmarked-resend",
            )
        with self.assertRaises(job_store.JobStoreError):
            job_store.record_decision(
                self.project,
                JOB_ID,
                "approved",
                7001,
                approver_ids=[42],
                idempotency_key="old-message",
                decided_at=FIXED_NOW,
            )

    def test_hold_can_return_to_approval_with_same_revision(self) -> None:
        self.create()
        job_store.mark_report_sent(self.project, JOB_ID, 7001, report_sent_at=FIXED_NOW)
        held = job_store.record_decision(
            self.project,
            JOB_ID,
            "hold",
            7001,
            approver_ids=[42],
            feedback=["잠시 보류한다."],
            idempotency_key="telegram:update:hold",
            decided_at=FIXED_NOW,
        )
        self.assertEqual(held["status"], "HOLD")
        resent = job_store.mark_report_sent(
            self.project,
            JOB_ID,
            7002,
            report_sent_at=FIXED_NOW,
            resend=True,
        )
        self.assertEqual(resent["status"], "SENT_FOR_APPROVAL")
        self.assertEqual(resent["current_revision"], 1)
        self.assertIsNone(resent["revisions"][0]["approval"]["decision"])

    def test_wrong_message_and_invalid_transition_are_rejected(self) -> None:
        self.create()
        with self.assertRaises(job_store.JobStoreError):
            job_store.transition_generation(
                self.project,
                JOB_ID,
                "GENERATING",
                reason="승인 없이 생성 시도",
                idempotency_key="invalid:transition",
                now=FIXED_NOW,
            )
        job_store.mark_report_sent(self.project, JOB_ID, 7001, report_sent_at=FIXED_NOW)
        with self.assertRaises(job_store.JobStoreError):
            job_store.record_decision(
                self.project,
                JOB_ID,
                "approved",
                9999,
                approver_ids=[42],
                idempotency_key="telegram:update:wrong",
                decided_at=FIXED_NOW,
            )

    def test_revision_preserves_previous_files_and_invalidates_approval(self) -> None:
        self.create()
        root = self.project / "jobs" / JOB_ID
        old_report = (root / "revisions/r001/report.md").read_bytes()
        job_store.mark_report_sent(self.project, JOB_ID, 7001, report_sent_at=FIXED_NOW)
        job_store.record_decision(
            self.project,
            JOB_ID,
            "revision_requested",
            7001,
            approver_ids=[42],
            feedback=["적용 질문을 바꿔 달라."],
            idempotency_key="telegram:update:92",
            decided_at=FIXED_NOW,
        )
        revised_content = content()
        revised_content["devotional_points"]["application_questions"] = ["오늘 어디에 순종할 것인가?"]
        revised = job_store.new_revision(
            self.project,
            JOB_ID,
            revised_content,
            "# 검토 보고서 r002\n",
            self.method,
            feedback=["적용 질문을 바꿔 달라."],
            idempotency_key=f"{JOB_ID}:2:revision",
            now=FIXED_NOW,
        )
        self.assertEqual(revised["current_revision"], 2)
        self.assertEqual(revised["status"], "DRAFT")
        self.assertEqual(revised["revisions"][0]["status"], "REVISION_REQUESTED")
        self.assertIsNone(revised["revisions"][1]["approval"]["decision"])
        self.assertEqual((root / "revisions/r001/report.md").read_bytes(), old_report)
        self.assertNotEqual(
            job_store.revision_content_signature(revised["revisions"][0]),
            job_store.revision_content_signature(revised["revisions"][1]),
        )
        self.assertTrue((root / "revisions/r002/report.md").is_file())
        repeated = job_store.new_revision(
            self.project,
            JOB_ID,
            revised_content,
            "# 검토 보고서 r002\n",
            self.method,
            feedback=["적용 질문을 바꿔 달라."],
            idempotency_key=f"{JOB_ID}:2:revision",
            now=FIXED_NOW,
        )
        self.assertEqual(repeated["current_revision"], 2)
        self.assertEqual(len(repeated["revisions"]), 2)
        self.assertFalse((root / "revisions/r003").exists())
        self.assertEqual(job_store.validate_job(self.project, JOB_ID), [])

    def test_generation_transition_and_conflicting_idempotency_key(self) -> None:
        self.create()
        job_store.mark_report_sent(self.project, JOB_ID, 7001, report_sent_at=FIXED_NOW)
        job_store.record_decision(
            self.project,
            JOB_ID,
            "approved",
            7001,
            approver_ids=[42],
            idempotency_key="telegram:update:93",
            decided_at=FIXED_NOW,
        )
        generating = job_store.transition_generation(
            self.project,
            JOB_ID,
            "GENERATING",
            reason="승인 게이트 통과",
            idempotency_key="generation:start:1",
            now=FIXED_NOW,
        )
        self.assertEqual(generating["status"], "GENERATING")
        with self.assertRaises(job_store.JobStoreError):
            job_store.transition_generation(
                self.project,
                JOB_ID,
                "NEEDS_REVIEW",
                reason="다른 전이",
                idempotency_key="generation:start:1",
                now=FIXED_NOW,
            )
        needs_review = job_store.transition_generation(
            self.project,
            JOB_ID,
            "NEEDS_REVIEW",
            reason="한 장면이 두 번 실패함",
            idempotency_key="generation:failed:1",
            now=FIXED_NOW,
        )
        self.assertEqual(needs_review["status"], "NEEDS_REVIEW")
        resumed = job_store.transition_generation(
            self.project,
            JOB_ID,
            "GENERATING",
            reason="실패 장면 재개",
            idempotency_key="generation:resume:1",
            now=FIXED_NOW,
        )
        self.assertEqual(resumed["status"], "GENERATING")
        restored = job_store.transition_generation(
            self.project,
            JOB_ID,
            "APPROVED",
            reason="외부 의존성 사용 불가",
            idempotency_key="generation:dependency-unavailable:1",
            now=FIXED_NOW,
        )
        self.assertEqual(restored["status"], "APPROVED")
        self.assertEqual(job_store.validate_job(self.project, JOB_ID), [])

    def test_validation_detects_tampered_prompt_hash(self) -> None:
        self.create()
        path = self.project / "jobs" / JOB_ID / "job.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["revisions"][0]["scenes"][0]["prompt_hash"] = "0" * 64
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        errors = job_store.validate_job(self.project, JOB_ID)
        self.assertTrue(any("프롬프트 해시 불일치" in error for error in errors))
        job_store.mark_validation_failure(self.project, JOB_ID, errors, now=FIXED_NOW)
        marked = job_store.load_job(self.project, JOB_ID)
        self.assertEqual(marked["status"], "NEEDS_REVIEW")
        self.assertEqual(marked["revisions"][0]["status"], "NEEDS_REVIEW")
        with self.assertRaisesRegex(job_store.JobStoreError, "승인"):
            job_store.transition_generation(
                self.project,
                JOB_ID,
                "GENERATING",
                reason="검증 실패 우회 시도",
                idempotency_key="generation:bypass:blocked",
                now=FIXED_NOW,
            )

    def test_corrupt_job_is_preserved_with_recovery_marker(self) -> None:
        self.create()
        path = self.project / "jobs" / JOB_ID / "job.json"
        corrupt_bytes = b'{"broken": '
        path.write_bytes(corrupt_bytes)
        errors = job_store.validate_and_mark(self.project, JOB_ID)
        marker = path.parent / "validation-error.json"
        self.assertTrue(errors)
        self.assertEqual(path.read_bytes(), corrupt_bytes)
        self.assertTrue(marker.is_file())
        summary = job_store.status_summary(self.project, JOB_ID)
        self.assertEqual(summary["status"], "NEEDS_REVIEW")
        self.assertTrue(summary["validation_errors"])

    def test_atomic_json_replace_failure_preserves_existing_job(self) -> None:
        self.create()
        path = self.project / "jobs" / JOB_ID / "job.json"
        before = path.read_bytes()
        with mock.patch("job_store.os.replace", side_effect=OSError("교체 실패")):
            with self.assertRaisesRegex(OSError, "교체 실패"):
                job_store._atomic_write_json(path, {"broken": True})
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(any(path.parent.glob(f".{path.name}.*")))

    def test_invalid_input_and_content_are_rejected_without_job_directory(self) -> None:
        invalid_input = copy.deepcopy(self.input_data)
        invalid_input["passage_text"] = "   "
        with self.assertRaises(job_store.JobStoreError):
            job_store.create_draft(
                self.project,
                invalid_input,
                content(),
                "# 보고서\n",
                self.method,
                job_id=JOB_ID,
                now=FIXED_NOW,
            )
        self.assertFalse((self.project / "jobs" / JOB_ID).exists())

        invalid_visual = copy.deepcopy(self.input_data)
        invalid_visual["visual_overrides"] = {"palette": "warm"}
        with self.assertRaisesRegex(job_store.JobStoreError, "visual_overrides"):
            job_store.create_draft(
                self.project,
                invalid_visual,
                content(),
                "# 보고서\n",
                self.method,
                job_id=JOB_ID,
                now=FIXED_NOW,
            )
        self.assertFalse((self.project / "jobs" / JOB_ID).exists())

    def test_cli_status_and_validate(self) -> None:
        self.create()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status_code = job_store.main(
                ["status", "--project-root", str(self.project), "--job-id", JOB_ID]
            )
        self.assertEqual(status_code, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "DRAFT")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            validate_code = job_store.main(
                ["validate", "--project-root", str(self.project), "--job-id", JOB_ID]
            )
        self.assertEqual(validate_code, 0)
        self.assertIn("작업 검증 통과", output.getvalue())

    def test_cli_create_draft(self) -> None:
        input_path = Path(self.temporary.name) / "input.json"
        content_path = Path(self.temporary.name) / "content.json"
        report_path = Path(self.temporary.name) / "report.md"
        input_path.write_text(json.dumps(self.input_data, ensure_ascii=False), encoding="utf-8")
        content_path.write_text(json.dumps(content(), ensure_ascii=False), encoding="utf-8")
        report_path.write_text("# 검토 보고서\n", encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = job_store.main(
                [
                    "create-draft",
                    "--project-root",
                    str(self.project),
                    "--input-json",
                    str(input_path),
                    "--content-json",
                    str(content_path),
                    "--report",
                    str(report_path),
                    "--method-file",
                    str(self.method),
                    "--job-id",
                    JOB_ID,
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "DRAFT")
        self.assertEqual(job_store.validate_job(self.project, JOB_ID), [])

    def test_validate_missing_job_does_not_create_ghost_directory(self) -> None:
        with self.assertRaises(job_store.JobStoreError):
            job_store.validate_and_mark(self.project, JOB_ID)
        self.assertFalse((self.project / "jobs" / JOB_ID).exists())


if __name__ == "__main__":
    unittest.main()
