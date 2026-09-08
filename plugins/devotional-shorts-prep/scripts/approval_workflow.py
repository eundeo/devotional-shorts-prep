#!/usr/bin/env python3
"""Classify Telegram replies and apply the safe aggregate approval decision."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import job_store
import telegram_bot


REVISION_RE = re.compile(
    r"수정|바꾸|변경|고치|조정|삭제|추가|줄이|줄여|늘리|늘려|교체|재작성|보완|반영|다시\s*(?:써|작성|만들)"
)
HOLD_RE = re.compile(r"보류|대기|잠시|나중에|추후|기다려|기다리")
APPROVAL_RE = re.compile(
    r"(?:^|\s)/approve(?:@[a-z0-9_]+)?(?:\s|$)|승인|\bapproved\b|"
    r"그대로\s*진행|이대로\s*(?:진행|가)|원안대로|"
    r"진행(?:해\s*주|해주|하세|합시|바랍|시켜)|확정"
)
NEGATED_REVISION_RE = re.compile(
    r"(?:수정|변경|조정)(?:할)?\s*(?:필요(?:가|는)?\s*)?(?:없|없이)|"
    r"(?:고칠|바꿀)\s*(?:것|게)?\s*(?:없|없이)"
)
NEGATED_HOLD_RE = re.compile(r"보류\s*(?:없이|하지\s*말고)")
CLASSIFICATION_PRIORITY = {
    "revision_requested": 3,
    "hold": 2,
    "approved": 1,
    "unclear": 0,
}


class ApprovalError(ValueError):
    """A safe, user-correctable approval command error."""


def classify_reply(text: str) -> str:
    if not isinstance(text, str) or not text.strip():
        return "unclear"
    normalized = unicodedata.normalize("NFKC", text).casefold()
    revision_checked = NEGATED_REVISION_RE.sub("", normalized)
    hold_checked = NEGATED_HOLD_RE.sub("", revision_checked)
    if REVISION_RE.search(revision_checked):
        return "revision_requested"
    if HOLD_RE.search(hold_checked):
        return "hold"
    if APPROVAL_RE.search(hold_checked):
        return "approved"
    return "unclear"


def aggregate_classifications(values: Iterable[str]) -> str | None:
    classifications = list(values)
    if not classifications:
        return None
    unknown = set(classifications) - set(CLASSIFICATION_PRIORITY)
    if unknown:
        raise ApprovalError("알 수 없는 답장 분류: " + ", ".join(sorted(unknown)))
    return max(classifications, key=CLASSIFICATION_PRIORITY.__getitem__)


def resolve_job_id(
    project_root: Path | str,
    job_id: str | None,
    *,
    required_status: str = "SENT_FOR_APPROVAL",
) -> str:
    if job_id:
        try:
            job = job_store.load_job(project_root, job_id)
        except job_store.JobStoreError as exc:
            raise ApprovalError(str(exc)) from exc
        if required_status and job.get("status") != required_status:
            raise ApprovalError(
                f"작업 {job_id}의 상태가 {required_status}가 아님. 현재 상태: {job.get('status')}"
            )
        return job_id
    candidates = job_store.job_ids_with_status(project_root, required_status)
    if len(candidates) != 1:
        if not candidates:
            raise ApprovalError(
                f"{required_status} 작업이 없음. 복구: 작업 ID와 보고 상태를 확인하십시오."
            )
        raise ApprovalError(
            f"{required_status} 작업이 {len(candidates)}개임. 복구: 작업 ID를 명시하십시오."
        )
    return candidates[0]


def check_approval(
    project_root: Path | str,
    job_id: str | None,
    config: telegram_bot.TelegramConfig,
    *,
    api: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    resolved = resolve_job_id(project_root, job_id)
    pending = job_store.job_ids_with_status(project_root, "SENT_FOR_APPROVAL")
    if pending != [resolved]:
        raise ApprovalError(
            "Telegram 승인 대기 작업이 여러 개임. 복구: 봇당 승인 대기 작업을 하나만 유지하고 "
            "나머지는 수정 요청 또는 보류로 정리한 뒤 다시 실행하십시오."
        )
    replies = telegram_bot.check_replies(
        project_root, resolved, config, api=api, now=now
    )
    classified = [
        {**reply, "classification": classify_reply(reply["text"])}
        for reply in replies["valid_replies"]
    ]
    decision = aggregate_classifications(item["classification"] for item in classified)
    if decision is None:
        current = job_store.status_summary(project_root, resolved)
        return {
            **replies,
            "classified_replies": [],
            "decision": None,
            "status": current["status"],
            "message_ko": "새로운 유효 담당자 답장이 없습니다.",
        }

    signature = [
        {
            "update_id": item["update_id"],
            "message_id": item["message_id"],
            "classification": item["classification"],
        }
        for item in classified
    ]
    digest = hashlib.sha256(
        json.dumps(signature, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:20]
    try:
        job = job_store.record_decision(
            project_root,
            resolved,
            decision,
            replies["report_message_id"],
            approver_ids=[item["user_id"] for item in classified],
            feedback=[item["text"] for item in classified],
            idempotency_key=f"telegram:decision:{digest}",
            decided_at=now,
        )
    except job_store.JobStoreError as exc:
        raise ApprovalError(str(exc)) from exc
    messages = {
        "approved": "현재 리비전이 명확히 승인되었습니다. 이미지 생성 전 승인 게이트를 다시 확인해야 합니다.",
        "revision_requested": "수정 요청이 우선 적용되었습니다. 이미지를 생성하지 말고 새 리비전을 작성하십시오.",
        "hold": "보류 의견이 우선 적용되었습니다. 이미지를 생성하지 말고 재보고 지시를 기다리십시오.",
        "unclear": "명확한 진행 의사가 없어 승인 대기 상태를 유지합니다.",
    }
    return {
        **replies,
        "classified_replies": classified,
        "decision": decision,
        "status": job["status"],
        "message_ko": messages[decision],
    }


def resend_report(
    project_root: Path | str,
    job_id: str | None,
    config: telegram_bot.TelegramConfig,
    *,
    api: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    resolved = (
        resolve_job_id(project_root, job_id, required_status="")
        if job_id
        else resolve_job_id(project_root, None)
    )
    return telegram_bot.send_report(
        project_root, resolved, config, api=api, resend=True, now=now
    )


def status_result(project_root: Path | str, job_id: str | None) -> dict[str, Any]:
    if job_id:
        if not job_store.JOB_ID_RE.fullmatch(job_id):
            raise ApprovalError("작업 ID 형식이 잘못됨. 복구: ds-YYYYMMDD-HHMMSS-xxxxxxxx 형식을 사용하십시오.")
        resolved = job_id
    else:
        resolved = resolve_job_id(project_root, None)
    try:
        summary = job_store.status_summary(project_root, resolved)
    except job_store.JobStoreError as exc:
        raise ApprovalError(str(exc)) from exc
    decision_labels = {
        None: "아직 결정 없음",
        "approved": "승인",
        "revision_requested": "수정 요청",
        "hold": "보류",
        "unclear": "불명확",
    }
    revision_label = (
        f"r{summary['current_revision']:03d}"
        if isinstance(summary.get("current_revision"), int)
        else "리비전 확인 불가"
    )
    summary["message_ko"] = (
        f"작업 {resolved} · {revision_label} · 상태 {summary['status']} · "
        f"보고 시각 {summary['report_sent_at'] or '없음'} · "
        f"승인 {decision_labels.get(summary['approval_decision'], summary['approval_decision'])} · "
        f"실패 장면 {summary['scene_counts'].get('FAILED', 0)}개"
    )
    try:
        events = job_store.load_job(project_root, resolved).get("events", [])
    except job_store.JobStoreError:
        events = []
    if summary["status"] == "SENT_FOR_APPROVAL" and any(
        event.get("type") == "approval_window_expired"
        and event.get("revision") == summary.get("current_revision")
        for event in events
    ):
        summary["message_ko"] += " · 승인 확인 기간 만료: 보고 재전송을 실행하십시오"
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="묵상 쇼츠 Telegram 승인·재전송·상태 워크플로")
    commands = parser.add_subparsers(dest="command", required=True)

    classify = commands.add_parser("classify", help="답장 한 건을 안전 규칙으로 분류")
    classify.add_argument("--text", required=True)

    check = commands.add_parser("check-approval", help="승인 확인 사용자 명령 실행")
    check.add_argument("--project-root", default=".")
    check.add_argument("--job-id")

    resend = commands.add_parser("resend-report", help="보고 재전송 사용자 명령 실행")
    resend.add_argument("--project-root", default=".")
    resend.add_argument("--job-id")

    status = commands.add_parser("status", help="작업 상태 사용자 명령 실행")
    status.add_argument("--project-root", default=".")
    status.add_argument("--job-id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "classify":
            result = {"text": args.text, "classification": classify_reply(args.text)}
        elif args.command == "check-approval":
            result = check_approval(
                args.project_root, args.job_id, telegram_bot.load_config()
            )
        elif args.command == "resend-report":
            result = resend_report(
                args.project_root, args.job_id, telegram_bot.load_config()
            )
        else:
            result = status_result(args.project_root, args.job_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (
        ApprovalError,
        telegram_bot.TelegramError,
        job_store.JobStoreError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        print(f"승인 워크플로 오류: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
