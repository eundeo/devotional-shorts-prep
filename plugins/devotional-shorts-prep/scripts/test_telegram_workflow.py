#!/usr/bin/env python3
"""Mocked Telegram transport and approval workflow regression checks."""

from __future__ import annotations

import hashlib
import json
import struct
import tempfile
import unittest
import urllib.error
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import approval_workflow
import image_workflow
import job_store
import telegram_bot


FIXED_NOW = datetime(2026, 8, 12, 15, 0, 0, tzinfo=timezone.utc)
JOB_ID = "ds-20260812-150000-aaaabbbb"
TOKEN = "123456:" + "A" * 30
CONFIG = telegram_bot.TelegramConfig(TOKEN, -100123, (42, 43))


def png(width: int = 9, height: int = 16) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    rows = b"".join(b"\x00" + b"\x80\x40\x20" * width for _ in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


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
            "interpretation": "핵심 해석이다.",
            "core_sentence": "하나님은 진실한 마음을 보신다.",
            "application": "오늘 진실하게 순종한다.",
            "application_questions": ["오늘 무엇에 순종할 것인가?"],
        },
        "script": {
            "core_sentence": "하나님은 진실한 마음을 보신다.",
            "thumbnail": selection("진실한 마음"),
            "opening": selection("무엇을 보고 계실까"),
            "bridge": "본문을 함께 살펴보자.",
            "context": "",
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
                "source_text": "본문을 듣고 오늘 진실하게 순종한다.",
                "section": "modern_application",
                "setting": "modern",
                "description_ko": "말씀을 묵상하는 현대인",
                "prompt_en": "A modern adult reflecting on Scripture, vertical 9:16",
            },
            {
                "source_text": "주님, 진실하게 살게 하소서.",
                "section": "prayer",
                "setting": "modern",
                "description_ko": "기도하는 현대인",
                "prompt_en": "A modern adult praying in warm light, vertical 9:16",
            },
        ],
    }


class FakeAPI:
    def __init__(self, plans: dict[str, list[object]] | None = None) -> None:
        self.plans = {key: list(values) for key, values in (plans or {}).items()}
        self.calls: list[tuple[str, dict[str, object], dict[str, Path]]] = []
        self.next_message_id = 7000

    def call(
        self,
        method: str,
        params: dict[str, object] | None = None,
        files: dict[str, Path] | None = None,
    ) -> object:
        self.calls.append((method, dict(params or {}), dict(files or {})))
        if self.plans.get(method):
            result = self.plans[method].pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        if method == "getMe":
            return {"id": 777, "is_bot": True, "username": "devotional_test_bot"}
        if method == "getWebhookInfo":
            return {"url": "", "pending_update_count": 0}
        if method == "getChat":
            return {
                "id": CONFIG.chat_id,
                "type": "supergroup",
                "title": "검토 그룹",
                "permissions": {
                    "can_send_messages": True,
                    "can_send_documents": True,
                    "can_send_photos": True,
                },
            }
        if method == "getChatMember":
            return {"status": "administrator"}
        if method == "getUpdates":
            return []
        if method in {"sendMessage", "sendDocument", "sendPhoto"}:
            self.next_message_id += 1
            return {"message_id": self.next_message_id}
        raise AssertionError(f"예상하지 못한 API 메서드: {method}")


def update(
    update_id: int,
    text: str,
    *,
    report_message_id: int,
    user_id: int = 42,
    chat_id: int = CONFIG.chat_id,
    message_id: int | None = None,
) -> dict[str, object]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": message_id or 8000 + update_id,
            "date": int(FIXED_NOW.timestamp()),
            "chat": {"id": chat_id, "type": "supergroup"},
            "from": {"id": user_id, "is_bot": False},
            "reply_to_message": {"message_id": report_message_id},
            "text": text,
        },
    }


class Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = json.dumps(payload).encode()

    def __enter__(self) -> "Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


class TelegramWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary.name) / "project"
        self.project.mkdir()
        self.method = Path(self.temporary.name) / "method.md"
        self.method.write_text("# 방법론\n\n방법론 버전: `1.0.0`\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def create(self, job_id: str = JOB_ID) -> dict[str, object]:
        return job_store.create_draft(
            self.project,
            {
                "passage_text": "가난한 과부가 두 렙돈을 넣었다.",
                "passage_reference": "누가복음 21:1-4",
                "title": "두 렙돈",
                "date": "2026-08-12",
                "special_instructions": [],
                "visual_overrides": None,
            },
            content(),
            "# 검토 보고서\n\n## 검토 주의사항\n\n없음\n\n## 담당자 답장 안내\n",
            self.method,
            job_id=job_id,
            now=FIXED_NOW,
        )

    def send(self, job_id: str = JOB_ID, api: FakeAPI | None = None) -> tuple[dict[str, object], FakeAPI]:
        client = api or FakeAPI()
        result = telegram_bot.send_report(
            self.project, job_id, CONFIG, api=client, now=FIXED_NOW
        )
        return result, client

    def test_missing_and_malformed_environment_fail_before_network(self) -> None:
        with self.assertRaisesRegex(telegram_bot.TelegramError, "TELEGRAM_BOT_TOKEN"):
            telegram_bot.load_config({})
        with self.assertRaisesRegex(telegram_bot.TelegramError, "TELEGRAM_CHAT_ID"):
            telegram_bot.load_config(
                {
                    "TELEGRAM_BOT_TOKEN": TOKEN,
                    "TELEGRAM_CHAT_ID": "group",
                    "TELEGRAM_APPROVER_IDS": "42",
                }
            )
        with self.assertRaisesRegex(telegram_bot.TelegramError, "TELEGRAM_APPROVER_IDS"):
            telegram_bot.load_config(
                {
                    "TELEGRAM_BOT_TOKEN": TOKEN,
                    "TELEGRAM_CHAT_ID": "-100123",
                    "TELEGRAM_APPROVER_IDS": "42,",
                }
            )

    def test_api_retries_once_and_redacts_token(self) -> None:
        calls = 0

        def transient(_request: object, timeout: float) -> Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise urllib.error.URLError(f"연결 실패 {TOKEN}")
            return Response({"ok": True, "result": {"id": 777}})

        api = telegram_bot.TelegramAPI(TOKEN, opener=transient)
        self.assertEqual(api.call("getMe"), {"id": 777})
        self.assertEqual(calls, 2)

        def always_fail(_request: object, timeout: float) -> Response:
            raise urllib.error.URLError(f"연결 실패 {TOKEN}")

        with self.assertRaises(telegram_bot.TelegramError) as caught:
            telegram_bot.TelegramAPI(TOKEN, opener=always_fail).call("getMe")
        self.assertNotIn(TOKEN, str(caught.exception))
        self.assertIn("[REDACTED]", str(caught.exception))

        send_calls = 0

        def uncertain_send(_request: object, timeout: float) -> Response:
            nonlocal send_calls
            send_calls += 1
            raise urllib.error.URLError("응답 유실")

        with self.assertRaisesRegex(telegram_bot.TelegramError, "전송 여부가 불명확"):
            telegram_bot.TelegramAPI(TOKEN, opener=uncertain_send).call("sendMessage")
        self.assertEqual(send_calls, 1)

        rate_calls = 0

        def rate_limited(_request: object, timeout: float) -> Response:
            nonlocal rate_calls
            rate_calls += 1
            return Response(
                {
                    "ok": False,
                    "error_code": 429,
                    "description": "Too Many Requests",
                    "parameters": {"retry_after": 17},
                }
            )

        with self.assertRaisesRegex(telegram_bot.TelegramError, "17초 뒤"):
            telegram_bot.TelegramAPI(TOKEN, opener=rate_limited).call("getMe")
        self.assertEqual(rate_calls, 1)

    def test_invalid_token_preflight_sends_no_report(self) -> None:
        invalid = FakeAPI({"getMe": [telegram_bot.TelegramError("인증 실패 [REDACTED]")]})
        with self.assertRaisesRegex(telegram_bot.TelegramError, "인증 실패"):
            telegram_bot.preflight(CONFIG, api=invalid)
        self.assertEqual([call[0] for call in invalid.calls], ["getMe"])
        self.assertFalse(any(call[0].startswith("send") for call in invalid.calls))

    def test_preflight_sends_no_message_and_rejects_explicit_permission_denial(self) -> None:
        api = FakeAPI()
        result = telegram_bot.preflight(CONFIG, api=api)
        self.assertEqual(result["messages_sent"], 0)
        self.assertEqual(result["approver_count"], 2)
        self.assertFalse(result["webhook_configured"])
        self.assertEqual(
            [call[0] for call in api.calls],
            ["getMe", "getWebhookInfo", "getChat", "getChatMember"],
        )

        webhook = FakeAPI({"getWebhookInfo": [{"url": "https://example.test/hook"}]})
        with self.assertRaisesRegex(telegram_bot.TelegramError, "outgoing webhook"):
            telegram_bot.preflight(CONFIG, api=webhook)
        self.assertEqual([call[0] for call in webhook.calls], ["getMe", "getWebhookInfo"])

        denied = FakeAPI(
            {
                "getChat": [
                    {
                        "id": CONFIG.chat_id,
                        "type": "supergroup",
                        "permissions": {
                            "can_send_messages": True,
                            "can_send_documents": False,
                            "can_send_photos": True,
                        },
                    }
                ],
                "getChatMember": [{"status": "member"}],
            }
        )
        with self.assertRaisesRegex(telegram_bot.TelegramError, "전송 권한"):
            telegram_bot.preflight(CONFIG, api=denied)
        self.assertFalse(any(call[0].startswith("send") for call in denied.calls))

    def test_normal_report_is_ordered_saved_and_repeated_without_network(self) -> None:
        self.create()
        result, api = self.send()
        methods = [call[0] for call in api.calls]
        self.assertEqual(
            methods,
            ["getMe", "getWebhookInfo", "getChat", "getChatMember", "sendMessage", "sendDocument"],
        )
        self.assertEqual(result["status"], "SENT_FOR_APPROVAL")
        summary = api.calls[4][1]["text"]
        self.assertIn(JOB_ID, summary)
        self.assertIn("r001", summary)
        self.assertIn("예상 낭독: 110초", summary)
        self.assertIn("예상 장면: 2개", summary)
        self.assertIn("직접 답장", summary)
        document_params = api.calls[5][1]
        self.assertEqual(document_params["reply_parameters"]["message_id"], result["summary_message_id"])
        stored = job_store.load_job(self.project, JOB_ID)
        self.assertEqual(stored["status"], "SENT_FOR_APPROVAL")
        self.assertEqual(
            stored["revisions"][0]["telegram"]["report_message_id"],
            result["summary_message_id"],
        )
        self.assertTrue(any(event["type"] == "report_document_sent" for event in stored["events"]))

        no_network = FakeAPI()
        repeated = telegram_bot.send_report(self.project, JOB_ID, CONFIG, api=no_network)
        self.assertTrue(repeated["skipped"])
        self.assertEqual(no_network.calls, [])

    def test_document_failure_is_recorded_and_retry_reuses_summary(self) -> None:
        self.create()
        api = FakeAPI({"sendDocument": [telegram_bot.TelegramError(f"실패 {TOKEN}")]})
        with self.assertRaisesRegex(telegram_bot.TelegramError, "파일만 재시도"):
            telegram_bot.send_report(
                self.project, JOB_ID, CONFIG, api=api, now=FIXED_NOW
            )
        failed_job = job_store.load_job(self.project, JOB_ID)
        self.assertEqual(failed_job["status"], "DRAFT")
        serialized = json.dumps(failed_job, ensure_ascii=False)
        self.assertNotIn(TOKEN, serialized)
        summary_event = next(
            event for event in failed_job["events"] if event["type"] == "report_summary_sent"
        )
        summary_id = summary_event["details"]["summary_message_id"]

        retry = FakeAPI()
        result = telegram_bot.send_report(
            self.project, JOB_ID, CONFIG, api=retry, now=FIXED_NOW
        )
        self.assertEqual(result["summary_message_id"], summary_id)
        self.assertNotIn("sendMessage", [call[0] for call in retry.calls])
        self.assertIn("sendDocument", [call[0] for call in retry.calls])
        self.assertEqual(job_store.load_job(self.project, JOB_ID)["status"], "SENT_FOR_APPROVAL")

    def test_completed_transport_checkpoints_prevent_duplicate_sends_after_crash(self) -> None:
        self.create()
        base_key = job_store.report_idempotency_key(JOB_ID, 1)
        job_store.record_report_summary_sent(
            self.project,
            JOB_ID,
            7401,
            idempotency_key=f"{base_key}:summary",
            sent_at=FIXED_NOW,
        )
        job_store.record_report_document_sent(
            self.project,
            JOB_ID,
            7401,
            7402,
            idempotency_key=f"{base_key}:document",
            sent_at=FIXED_NOW,
        )

        api = FakeAPI()
        result = telegram_bot.send_report(
            self.project, JOB_ID, CONFIG, api=api, now=FIXED_NOW
        )

        self.assertEqual(result["summary_message_id"], 7401)
        self.assertEqual(result["report_document_message_id"], 7402)
        self.assertEqual(
            [call[0] for call in api.calls],
            ["getMe", "getWebhookInfo", "getChat", "getChatMember"],
        )
        self.assertEqual(job_store.load_job(self.project, JOB_ID)["status"], "SENT_FOR_APPROVAL")

    def test_resend_replaces_current_message_and_old_reply_is_invalid(self) -> None:
        self.create()
        first, _api = self.send()
        old_id = first["summary_message_id"]
        telegram_bot.check_replies(
            self.project,
            JOB_ID,
            CONFIG,
            api=FakeAPI(
                {"getUpdates": [[update(1, "내용이 좋습니다", report_message_id=old_id)]]}
            ),
            now=FIXED_NOW,
        )
        resend_api = FakeAPI({"sendMessage": [{"message_id": old_id + 10}]})
        resent = telegram_bot.send_report(
            self.project, JOB_ID, CONFIG, api=resend_api, resend=True, now=FIXED_NOW
        )
        self.assertNotEqual(resent["summary_message_id"], old_id)
        stored = job_store.load_job(self.project, JOB_ID)
        self.assertEqual(stored["current_revision"], 1)
        self.assertEqual(stored["revisions"][0]["telegram"]["report_message_id"], resent["summary_message_id"])
        self.assertEqual(stored["revisions"][0]["telegram"]["authorized_reply_ids"], [])
        self.assertEqual(stored["revisions"][0]["telegram"]["last_update_id"], 1)

        reply_api = FakeAPI(
            {"getUpdates": [[update(2, "승인", report_message_id=old_id)]]}
        )
        replies = telegram_bot.check_replies(
            self.project, JOB_ID, CONFIG, api=reply_api, now=FIXED_NOW
        )
        self.assertEqual(replies["valid_replies"], [])
        self.assertEqual(replies["invalid_replies"][0]["reason"], "현재 보고 메시지에 대한 직접 답장이 아님")

    def test_reply_filter_records_only_current_authorized_direct_reply(self) -> None:
        self.create()
        report, _api = self.send()
        report_id = report["summary_message_id"]
        updates = [
            update(1, "승인", report_message_id=report_id, chat_id=-999),
            update(2, "승인", report_message_id=report_id, user_id=99),
            update(3, "승인", report_message_id=1, user_id=42),
            update(4, "승인", report_message_id=report_id, user_id=42),
        ]
        api = FakeAPI({"getUpdates": [updates, updates]})
        result = telegram_bot.check_replies(
            self.project, JOB_ID, CONFIG, api=api, now=FIXED_NOW
        )
        self.assertEqual([item["update_id"] for item in result["valid_replies"]], [4])
        self.assertEqual(len(result["invalid_replies"]), 3)
        job = job_store.load_job(self.project, JOB_ID)
        self.assertEqual(job["revisions"][0]["telegram"]["last_update_id"], 4)
        self.assertEqual(job["revisions"][0]["telegram"]["authorized_reply_ids"], [8004])
        observation_count = sum(event["type"] == "telegram_reply_observed" for event in job["events"])
        telegram_bot.check_replies(self.project, JOB_ID, CONFIG, api=api, now=FIXED_NOW)
        repeated = job_store.load_job(self.project, JOB_ID)
        self.assertEqual(
            sum(event["type"] == "telegram_reply_observed" for event in repeated["events"]),
            observation_count,
        )

    def test_classifier_and_priority_aggregation(self) -> None:
        cases = {
            "승인합니다": "approved",
            "그대로 진행해 주세요": "approved",
            "/approve": "approved",
            "/approve@euntj_bot": "approved",
            "/approve@euntj_bot 수정해 주세요": "revision_requested",
            "/approve@euntj_bot 잠시 보류합니다": "hold",
            "수정 필요 없이 승인합니다": "approved",
            "수정해서 진행해 주세요": "revision_requested",
            "이 문장을 바꾸면 승인합니다": "revision_requested",
            "잠시 보류합니다": "hold",
            "좋아요": "unclear",
            "감사합니다": "unclear",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(approval_workflow.classify_reply(text), expected)
        self.assertEqual(
            approval_workflow.aggregate_classifications(["approved", "hold"]), "hold"
        )
        self.assertEqual(
            approval_workflow.aggregate_classifications(["approved", "revision_requested"]),
            "revision_requested",
        )

    def test_high_level_decisions_and_no_duplicate_approval(self) -> None:
        scenarios = [
            (["승인합니다", "이 부분을 수정해 주세요"], "REVISION_REQUESTED", "revision_requested"),
            (["승인합니다", "잠시 보류합니다"], "HOLD", "hold"),
            (["좋은 내용입니다"], "SENT_FOR_APPROVAL", "unclear"),
        ]
        for index, (texts, expected_status, expected_decision) in enumerate(scenarios, 1):
            job_id = f"ds-20260812-15000{index}-aaaabb{index:02x}"
            self.create(job_id)
            report, _api = self.send(job_id)
            report_id = report["summary_message_id"]
            updates = [
                update(100 * index + number, text, report_message_id=report_id, user_id=42 + (number % 2))
                for number, text in enumerate(texts, 1)
            ]
            result = approval_workflow.check_approval(
                self.project,
                job_id,
                CONFIG,
                api=FakeAPI({"getUpdates": [updates]}),
                now=FIXED_NOW,
            )
            self.assertEqual(result["status"], expected_status)
            self.assertEqual(result["decision"], expected_decision)
            if expected_status == "SENT_FOR_APPROVAL":
                job_store.record_decision(
                    self.project,
                    job_id,
                    "hold",
                    report_id,
                    approver_ids=[42],
                    idempotency_key=f"telegram:decision:cleanup:{index}",
                    decided_at=FIXED_NOW,
                )

        approved_id = "ds-20260812-150009-aaaabb09"
        self.create(approved_id)
        report, _api = self.send(approved_id)
        report_id = report["summary_message_id"]
        approved_update = [update(999, "승인합니다", report_message_id=report_id)]
        first = approval_workflow.check_approval(
            self.project,
            approved_id,
            CONFIG,
            api=FakeAPI({"getUpdates": [approved_update]}),
            now=FIXED_NOW,
        )
        self.assertEqual(first["status"], "APPROVED")
        before = len(job_store.load_job(self.project, approved_id)["events"])
        no_network = FakeAPI({"getUpdates": [approved_update]})
        with self.assertRaisesRegex(approval_workflow.ApprovalError, "상태가 SENT_FOR_APPROVAL"):
            approval_workflow.check_approval(
                self.project, approved_id, CONFIG, api=no_network, now=FIXED_NOW
            )
        self.assertEqual(no_network.calls, [])
        self.assertEqual(len(job_store.load_job(self.project, approved_id)["events"]), before)

    def test_24_hour_expiry_stays_pending_without_network(self) -> None:
        self.create()
        self.send()
        api = FakeAPI()
        with self.assertRaises(telegram_bot.ApprovalWindowExpired):
            approval_workflow.check_approval(
                self.project,
                JOB_ID,
                CONFIG,
                api=api,
                now=FIXED_NOW + timedelta(hours=24, seconds=1),
            )
        self.assertEqual(api.calls, [])
        job = job_store.load_job(self.project, JOB_ID)
        self.assertEqual(job["status"], "SENT_FOR_APPROVAL")
        self.assertTrue(any(event["type"] == "approval_window_expired" for event in job["events"]))
        status = approval_workflow.status_result(self.project, JOB_ID)
        self.assertIn("보고 재전송", status["message_ko"])

    def test_reply_check_blocks_tampered_local_job_before_network(self) -> None:
        self.create()
        self.send()
        job_path = self.project / "jobs" / JOB_ID / "job.json"
        tampered = json.loads(job_path.read_text(encoding="utf-8"))
        tampered["revisions"][0]["scenes"][0]["prompt_en"] = "A changed prompt without a matching hash"
        job_path.write_text(
            json.dumps(tampered, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        api = FakeAPI()

        with self.assertRaisesRegex(telegram_bot.TelegramError, "로컬 작업 검증 실패"):
            telegram_bot.check_replies(
                self.project, JOB_ID, CONFIG, api=api, now=FIXED_NOW
            )

        self.assertEqual(api.calls, [])
        self.assertEqual(job_store.load_job(self.project, JOB_ID)["status"], "NEEDS_REVIEW")

    def test_new_revision_inherits_update_cursor_and_approval_signature_detects_tampering(self) -> None:
        self.create()
        report, _api = self.send()
        report_id = report["summary_message_id"]
        approval_workflow.check_approval(
            self.project,
            JOB_ID,
            CONFIG,
            api=FakeAPI({"getUpdates": [[update(50, "수정해 주세요", report_message_id=report_id)]]}),
            now=FIXED_NOW,
        )
        revised = job_store.new_revision(
            self.project,
            JOB_ID,
            content(),
            "# 수정 보고서\n",
            self.method,
            feedback=["수정해 주세요"],
            idempotency_key=f"{JOB_ID}:2:cursor-test",
            now=FIXED_NOW,
        )
        self.assertEqual(revised["revisions"][1]["telegram"]["last_update_id"], 50)

        second_report = telegram_bot.send_report(
            self.project,
            JOB_ID,
            CONFIG,
            api=FakeAPI({"sendMessage": [{"message_id": 7200}]}),
            now=FIXED_NOW,
        )
        job_store.record_decision(
            self.project,
            JOB_ID,
            "approved",
            second_report["summary_message_id"],
            approver_ids=[42],
            feedback=["승인합니다"],
            idempotency_key="telegram:decision:signature-test",
            decided_at=FIXED_NOW,
        )
        path = self.project / "jobs" / JOB_ID / "job.json"
        tampered = json.loads(path.read_text(encoding="utf-8"))
        scene = tampered["revisions"][1]["scenes"][0]
        scene["prompt_en"] = "A changed prompt after approval"
        scene["prompt_hash"] = hashlib.sha256(scene["prompt_en"].encode()).hexdigest()
        path.write_text(json.dumps(tampered, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        errors = job_store.validate_job(self.project, JOB_ID)
        self.assertTrue(any("승인 후 콘텐츠 서명 불일치" in error for error in errors))

    def test_mocked_end_to_end_revision_current_approval_imagegen_and_delivery(self) -> None:
        self.create()
        first_report, _api = self.send()
        first_report_id = first_report["summary_message_id"]
        approval_workflow.check_approval(
            self.project,
            JOB_ID,
            CONFIG,
            api=FakeAPI(
                {
                    "getUpdates": [
                        [update(60, "적용을 구체적으로 수정해 주세요", report_message_id=first_report_id)]
                    ]
                }
            ),
            now=FIXED_NOW,
        )
        revised_content = content()
        revised_content["devotional_points"]["application"] = (
            "오늘 저녁 8시에 도움이 필요한 한 사람에게 전화한다."
        )
        job_store.new_revision(
            self.project,
            JOB_ID,
            revised_content,
            "# 수정 보고서\n",
            self.method,
            feedback=["적용을 구체적으로 수정해 주세요"],
            idempotency_key=f"{JOB_ID}:2:e2e",
            now=FIXED_NOW,
        )
        second_report = telegram_bot.send_report(
            self.project,
            JOB_ID,
            CONFIG,
            api=FakeAPI({"sendMessage": [{"message_id": 7200}]}),
            now=FIXED_NOW,
        )
        self.assertEqual(second_report["summary_message_id"], 7200)

        ignored = approval_workflow.check_approval(
            self.project,
            JOB_ID,
            CONFIG,
            api=FakeAPI(
                {"getUpdates": [[update(61, "승인", report_message_id=first_report_id)]]}
            ),
            now=FIXED_NOW,
        )
        self.assertEqual(ignored["status"], "SENT_FOR_APPROVAL")
        self.assertIsNone(ignored["decision"])

        approved = approval_workflow.check_approval(
            self.project,
            JOB_ID,
            CONFIG,
            api=FakeAPI({"getUpdates": [[update(62, "승인", report_message_id=7200)]]}),
            now=FIXED_NOW,
        )
        self.assertEqual(approved["status"], "APPROVED")

        image_root = self.project / "jobs" / JOB_ID / "revisions/r002/images"
        self.assertEqual(list(image_root.iterdir()), [])
        image_workflow.start_generation(self.project, JOB_ID, now=FIXED_NOW)
        imagegen_calls: list[str] = []
        while True:
            item = image_workflow.next_scene(self.project, JOB_ID, now=FIXED_NOW)
            if not item["ready"]:
                break
            imagegen_calls.append(item["prompt_en"])
            source = Path(self.temporary.name) / f"imagegen-{item['scene_number']:03d}.png"
            source.write_bytes(png())
            image_workflow.record_success(
                self.project,
                JOB_ID,
                item["scene_number"],
                source,
                now=FIXED_NOW,
            )
        self.assertEqual(len(imagegen_calls), len(revised_content["scenes"]))

        delivery_api = FakeAPI()
        result = image_workflow.deliver(
            self.project, JOB_ID, CONFIG, api=delivery_api, now=FIXED_NOW
        )
        self.assertEqual(result["status"], "DELIVERED")
        self.assertEqual(
            [call[0] for call in delivery_api.calls].count("sendPhoto"),
            len(revised_content["scenes"]),
        )
        self.assertEqual(job_store.validate_job(self.project, JOB_ID), [])

    def test_job_resolution_and_korean_status(self) -> None:
        self.create()
        with self.assertRaisesRegex(approval_workflow.ApprovalError, "SENT_FOR_APPROVAL 작업이 없음"):
            approval_workflow.resolve_job_id(self.project, None)
        self.send()
        self.assertEqual(approval_workflow.resolve_job_id(self.project, None), JOB_ID)
        resent = approval_workflow.resend_report(
            self.project,
            None,
            CONFIG,
            api=FakeAPI({"sendMessage": [{"message_id": 7300}]}),
            now=FIXED_NOW,
        )
        self.assertEqual(resent["summary_message_id"], 7300)
        status = approval_workflow.status_result(self.project, JOB_ID)
        self.assertIn(JOB_ID, status["message_ko"])
        self.assertIn("SENT_FOR_APPROVAL", status["message_ko"])

        other = "ds-20260812-150010-aaaabb10"
        self.create(other)
        blocked = FakeAPI()
        with self.assertRaisesRegex(telegram_bot.TelegramError, "다른 Telegram 승인 대기 작업"):
            self.send(other, blocked)
        self.assertEqual(blocked.calls, [])
        self.assertEqual(approval_workflow.resolve_job_id(self.project, None), JOB_ID)

    def test_current_report_warning_section_is_summarized(self) -> None:
        report = "# 보고서\n\n## 검토 메모\n\n### 확인이 필요한 사항\n\n- 본문 위치 확인\n\n## 승인 방법\n"
        self.assertEqual(telegram_bot._extract_warnings(report), ["본문 위치 확인"])

    def test_legacy_multiple_pending_jobs_block_reply_polling(self) -> None:
        self.create()
        self.send()
        other = "ds-20260812-150011-aaaabb11"
        self.create(other)
        job_store.mark_report_sent(
            self.project,
            other,
            8111,
            report_sent_at=FIXED_NOW,
            idempotency_key=f"{other}:1:legacy-pending",
        )
        api = FakeAPI()
        with self.assertRaisesRegex(approval_workflow.ApprovalError, "승인 대기 작업이 여러 개"):
            approval_workflow.check_approval(
                self.project, JOB_ID, CONFIG, api=api, now=FIXED_NOW
            )
        self.assertEqual(api.calls, [])

    def test_status_reads_recovery_marker_when_job_json_is_corrupt(self) -> None:
        self.create()
        job_path = self.project / "jobs" / JOB_ID / "job.json"
        job_path.write_text("{잘못된 JSON", encoding="utf-8")
        errors = job_store.validate_job(self.project, JOB_ID)
        job_store.mark_validation_failure(self.project, JOB_ID, errors, now=FIXED_NOW)

        result = approval_workflow.status_result(self.project, JOB_ID)

        self.assertEqual(result["status"], "NEEDS_REVIEW")
        self.assertIn("NEEDS_REVIEW", result["message_ko"])
        self.assertTrue(result["validation_errors"])

    def test_partial_image_delivery_preserves_success_and_retries_only_failure(self) -> None:
        self.create()
        report, _api = self.send()
        job_store.record_decision(
            self.project,
            JOB_ID,
            "approved",
            report["summary_message_id"],
            approver_ids=[42],
            idempotency_key="telegram:decision:image-test",
            decided_at=FIXED_NOW,
        )
        job_store.transition_generation(
            self.project,
            JOB_ID,
            "GENERATING",
            reason="이미지 전달 테스트",
            idempotency_key="generation:image-test",
            now=FIXED_NOW,
        )
        root = self.project / "jobs" / JOB_ID
        job_path = root / "job.json"
        job = json.loads(job_path.read_text(encoding="utf-8"))
        for scene in job["revisions"][0]["scenes"]:
            image = root / "revisions/r001/images" / f"scene-{scene['scene_number']:03d}.png"
            image.write_bytes(png())
            scene["status"] = "GENERATED"
            scene["attempts"] = 1
            scene["local_path"] = str(image.relative_to(self.project))
            scene["image_hash"] = hashlib.sha256(image.read_bytes()).hexdigest()
        job_path.write_text(json.dumps(job, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        first_api = FakeAPI(
            {"sendPhoto": [{"message_id": 9101}, telegram_bot.TelegramError("두 번째 실패")]}
        )
        first = telegram_bot.send_images(
            self.project, JOB_ID, CONFIG, api=first_api, now=FIXED_NOW
        )
        self.assertEqual([item["scene_number"] for item in first["sent"]], [1])
        self.assertEqual([item["scene_number"] for item in first["failed"]], [2])
        stored = job_store.load_job(self.project, JOB_ID)
        first_message_id = stored["revisions"][0]["scenes"][0]["telegram_message_id"]
        self.assertEqual(stored["revisions"][0]["scenes"][0]["status"], "DELIVERED")
        self.assertEqual(stored["revisions"][0]["scenes"][1]["status"], "GENERATED")

        second_api = FakeAPI({"sendPhoto": [{"message_id": 9102}]})
        second = telegram_bot.send_images(
            self.project, JOB_ID, CONFIG, api=second_api, now=FIXED_NOW
        )
        self.assertEqual(second["skipped"], [1])
        self.assertEqual([item["scene_number"] for item in second["sent"]], [2])
        self.assertEqual(
            [call[0] for call in second_api.calls].count("sendPhoto"),
            1,
        )
        self.assertEqual(
            job_store.load_job(self.project, JOB_ID)["revisions"][0]["scenes"][0][
                "telegram_message_id"
            ],
            first_message_id,
        )

    def test_multipart_contains_file_without_exposing_path(self) -> None:
        file_path = Path(self.temporary.name) / "report.md"
        file_path.write_text("보고서", encoding="utf-8")
        body, content_type = telegram_bot._multipart(
            {"chat_id": CONFIG.chat_id}, {"document": file_path}
        )
        self.assertIn(b'report.md', body)
        self.assertIn("multipart/form-data", content_type)
        self.assertNotIn(str(file_path.parent).encode(), body)


if __name__ == "__main__":
    unittest.main()
