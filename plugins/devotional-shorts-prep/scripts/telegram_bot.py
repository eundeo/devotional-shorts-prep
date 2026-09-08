#!/usr/bin/env python3
"""Serverless Telegram Bot API transport for devotional Shorts approvals."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

import job_store


API_ROOT = "https://api.telegram.org"
TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]{20,}$")
ENV_KEYS = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_APPROVER_IDS")
MAX_SUMMARY_LENGTH = 4096
MAX_DOCUMENT_BYTES = 50 * 1024 * 1024
MAX_PHOTO_BYTES = 10 * 1024 * 1024
APPROVAL_WINDOW = timedelta(hours=24)
SAFE_RETRY_METHODS = {"getMe", "getChat", "getChatMember", "getWebhookInfo", "getUpdates"}


class TelegramError(RuntimeError):
    """A redacted Telegram configuration, transport, or workflow failure."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class ApprovalWindowExpired(TelegramError):
    """The current report can no longer receive approval."""


@dataclass(frozen=True)
class TelegramConfig:
    token: str
    chat_id: int
    approver_ids: tuple[int, ...]


def _redact(value: Any, token: str) -> str:
    text = str(value)
    return text.replace(token, "[REDACTED]") if token else text


def load_config(environ: Mapping[str, str] | None = None) -> TelegramConfig:
    values = os.environ if environ is None else environ
    missing = [key for key in ENV_KEYS if not str(values.get(key, "")).strip()]
    if missing:
        raise TelegramError(
            "필수 환경변수가 없음: " + ", ".join(missing) + ". 복구: 로컬 환경에 값을 설정한 뒤 다시 실행하십시오."
        )
    token = str(values["TELEGRAM_BOT_TOKEN"]).strip()
    if not TOKEN_RE.fullmatch(token):
        raise TelegramError(
            "TELEGRAM_BOT_TOKEN 형식이 잘못됨. 복구: BotFather에서 발급한 토큰을 다시 설정하십시오."
        )
    try:
        chat_id = int(str(values["TELEGRAM_CHAT_ID"]).strip())
    except ValueError as exc:
        raise TelegramError(
            "TELEGRAM_CHAT_ID는 정수형 문자열이어야 함. 복구: 대상 그룹 ID를 확인하십시오."
        ) from exc
    if chat_id == 0:
        raise TelegramError("TELEGRAM_CHAT_ID는 0일 수 없음. 복구: 대상 그룹 ID를 확인하십시오.")

    approvers: list[int] = []
    for raw in str(values["TELEGRAM_APPROVER_IDS"]).split(","):
        item = raw.strip()
        if not item:
            raise TelegramError(
                "TELEGRAM_APPROVER_IDS 형식이 잘못됨. 복구: 쉼표로 구분한 정수 ID만 입력하십시오."
            )
        try:
            approver = int(item)
        except ValueError as exc:
            raise TelegramError(
                "TELEGRAM_APPROVER_IDS 형식이 잘못됨. 복구: 쉼표로 구분한 정수 ID만 입력하십시오."
            ) from exc
        if approver <= 0:
            raise TelegramError(
                "승인 담당자 ID는 양의 정수여야 함. 복구: Telegram 사용자 ID를 확인하십시오."
            )
        approvers.append(approver)
    return TelegramConfig(token, chat_id, tuple(sorted(set(approvers))))


def _multipart(fields: Mapping[str, Any], files: Mapping[str, Path]) -> tuple[bytes, str]:
    boundary = "----devotional-shorts-" + uuid.uuid4().hex
    chunks: list[bytes] = []
    for name, value in fields.items():
        encoded = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
        chunks.extend(
            (
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                encoded.encode("utf-8"),
                b"\r\n",
            )
        )
    for name, path in files.items():
        filename = path.name.replace('"', "")
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        chunks.extend(
            (
                f"--{boundary}\r\n".encode(),
                (
                    f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                    f"Content-Type: {content_type}\r\n\r\n"
                ).encode(),
                path.read_bytes(),
                b"\r\n",
            )
        )
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


class TelegramAPI:
    """Small Bot API client that retries only safe read operations."""

    def __init__(
        self,
        token: str,
        *,
        api_root: str = API_ROOT,
        timeout: float = 20,
        opener: Any = urllib.request.urlopen,
    ) -> None:
        self.token = token
        self.api_root = api_root.rstrip("/")
        self.timeout = timeout
        self.opener = opener

    def call(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        files: Mapping[str, Path] | None = None,
    ) -> Any:
        last_error: TelegramError | None = None
        for attempt in range(2):
            try:
                return self._call_once(method, params or {}, files or {})
            except TelegramError as exc:
                last_error = exc
                if not exc.retryable or attempt == 1 or method not in SAFE_RETRY_METHODS:
                    raise
        raise last_error or TelegramError("Telegram 요청 실패")

    def _call_once(
        self, method: str, params: Mapping[str, Any], files: Mapping[str, Path]
    ) -> Any:
        url = f"{self.api_root}/bot{self.token}/{method}"
        if files:
            try:
                body, content_type = _multipart(params, files)
            except OSError as exc:
                raise TelegramError(
                    f"전송 파일을 읽을 수 없음: {_redact(exc, self.token)}. 복구: 로컬 파일 경로와 권한을 확인하십시오."
                ) from exc
        else:
            body = json.dumps(params, ensure_ascii=False).encode("utf-8")
            content_type = "application/json; charset=utf-8"
        request = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": content_type, "Accept": "application/json"},
            method="POST",
        )
        try:
            with self.opener(request, timeout=self.timeout) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            parsed: dict[str, Any] = {}
            try:
                payload = exc.read()
                parsed = json.loads(payload.decode("utf-8"))
                description = parsed.get("description", "HTTP 오류")
                code = int(parsed.get("error_code", exc.code))
            except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
                description = f"HTTP {exc.code}"
                code = exc.code
            parameters = parsed.get("parameters", {}) if isinstance(parsed, dict) else {}
            retry_after = parameters.get("retry_after") if isinstance(parameters, dict) else None
            recovery = (
                f"{retry_after}초 뒤 다시 실행하십시오."
                if code == 429 and isinstance(retry_after, int) and retry_after > 0
                else "전송 여부가 불명확하므로 실제 그룹과 로컬 상태를 대조하십시오."
                if method.startswith("send") and code >= 500
                else "봇 권한과 Telegram 연결 상태를 확인한 뒤 다시 실행하십시오."
            )
            raise TelegramError(
                f"Telegram API {method} 실패({code}): {_redact(description, self.token)}. "
                f"복구: {recovery}",
                retryable=code >= 500,
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            recovery = (
                "전송 여부가 불명확하므로 실제 그룹과 로컬 상태를 대조하십시오."
                if method.startswith("send")
                else "네트워크를 확인한 뒤 다시 실행하십시오."
            )
            raise TelegramError(
                f"Telegram API {method} 네트워크 실패: {_redact(exc, self.token)}. "
                f"복구: {recovery}",
                retryable=True,
            ) from exc
        try:
            data = json.loads(payload.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise TelegramError(
                f"Telegram API {method} 응답을 해석할 수 없음. 복구: 잠시 후 다시 실행하십시오."
            ) from exc
        if not isinstance(data, dict) or data.get("ok") is not True or "result" not in data:
            code = data.get("error_code") if isinstance(data, dict) else None
            description = data.get("description", "잘못된 API 응답") if isinstance(data, dict) else "잘못된 API 응답"
            numeric_code = code if isinstance(code, int) else 0
            parameters = data.get("parameters", {}) if isinstance(data, dict) else {}
            retry_after = parameters.get("retry_after") if isinstance(parameters, dict) else None
            recovery = (
                f"{retry_after}초 뒤 다시 실행하십시오."
                if numeric_code == 429 and isinstance(retry_after, int) and retry_after > 0
                else "전송 여부가 불명확하므로 실제 그룹과 로컬 상태를 대조하십시오."
                if method.startswith("send") and numeric_code >= 500
                else "설정과 봇 권한을 확인하십시오."
            )
            raise TelegramError(
                f"Telegram API {method} 실패({numeric_code or 'unknown'}): "
                f"{_redact(description, self.token)}. 복구: {recovery}",
                retryable=numeric_code >= 500,
            )
        return data["result"]


def _api_for(config: TelegramConfig, api: Any | None) -> Any:
    return api or TelegramAPI(config.token)


def preflight(config: TelegramConfig, *, api: Any | None = None) -> dict[str, Any]:
    client = _api_for(config, api)
    bot = client.call("getMe")
    if not isinstance(bot, dict) or bot.get("is_bot") is not True or not isinstance(bot.get("id"), int):
        raise TelegramError("getMe 응답이 유효한 봇 정보가 아님. 복구: 봇 토큰을 다시 확인하십시오.")
    webhook = client.call("getWebhookInfo")
    if not isinstance(webhook, dict) or not isinstance(webhook.get("url", ""), str):
        raise TelegramError("getWebhookInfo 응답이 유효하지 않음. 복구: 봇 설정을 확인하십시오.")
    if webhook.get("url"):
        raise TelegramError(
            "봇에 outgoing webhook이 설정되어 getUpdates 승인을 조회할 수 없음. "
            "복구: 이 플러그인 전용 봇을 사용하거나 운영자가 기존 webhook 소유자와 조정하십시오."
        )
    chat = client.call("getChat", {"chat_id": config.chat_id})
    if not isinstance(chat, dict) or chat.get("id") != config.chat_id:
        raise TelegramError("대상 그룹 정보가 설정과 다름. 복구: TELEGRAM_CHAT_ID를 확인하십시오.")
    if chat.get("type") not in {"group", "supergroup"}:
        raise TelegramError("대상 채팅이 그룹이 아님. 복구: 전용 그룹 또는 슈퍼그룹 ID를 설정하십시오.")
    membership = client.call(
        "getChatMember", {"chat_id": config.chat_id, "user_id": bot["id"]}
    )
    if not isinstance(membership, dict) or membership.get("status") in {None, "left", "kicked"}:
        raise TelegramError("봇이 대상 그룹의 활성 멤버가 아님. 복구: 봇을 그룹에 다시 추가하십시오.")

    capability = "확인됨"
    status = membership["status"]
    permission_source = membership if status == "restricted" else chat.get("permissions", {})
    if status not in {"creator", "administrator"}:
        permission_fields = ("can_send_messages", "can_send_documents", "can_send_photos")
        if any(permission_source.get(field) is False for field in permission_fields):
            raise TelegramError(
                "봇의 메시지·문서·사진 전송 권한이 제한됨. 복구: 그룹 관리자에게 전송 권한을 요청하십시오."
            )
        if any(permission_source.get(field) is not True for field in permission_fields):
            capability = "API에서 완전 확인 불가—실제 전송 단계에서 재확인"
    return {
        "bot_id": bot["id"],
        "bot_username": bot.get("username"),
        "chat_id": chat["id"],
        "chat_title": chat.get("title"),
        "chat_type": chat["type"],
        "send_capability": capability,
        "approver_count": len(config.approver_ids),
        "webhook_configured": False,
        "messages_sent": 0,
    }


def _extract_warnings(report_text: str) -> list[str]:
    marker = next(
        (item for item in ("### 확인이 필요한 사항", "## 검토 주의사항") if item in report_text),
        None,
    )
    if marker is None:
        return ["보고서의 확인 필요 섹션을 찾을 수 없음"]
    section = report_text.split(marker, 1)[1].split("\n## ", 1)[0]
    lines = [line.strip().removeprefix("- ") for line in section.splitlines() if line.strip()]
    return [] if lines == ["없음"] else lines


def build_report_summary(job: dict[str, Any], report_text: str) -> str:
    revision = job_store.current_revision(job)
    script = revision["script"]
    input_data = job["input"]
    label = input_data.get("title") or input_data.get("passage_reference") or "본문 위치 미확인"
    exception = (
        f"예 — {script['duration_exception_reason']}" if script["duration_exception"] else "아니오"
    )
    warnings = _extract_warnings(report_text)
    warning_text = "; ".join(warnings) if warnings else "없음"
    lines = [
        "[묵상 쇼츠 검토 요청]",
        f"작업: {label}",
        f"작업 ID: {job['job_id']}",
        f"리비전: r{job['current_revision']:03d}",
        f"핵심 문장: {revision['devotional_points']['core_sentence']}",
        f"오프닝: {script['opening']['selected']}",
        f"썸네일: {script['thumbnail']['selected']}",
        f"예상 낭독: {script['estimated_seconds']}초",
        f"예상 장면: {len(revision['scenes'])}개",
        f"분량 예외: {exception}",
        f"검토 주의사항: {warning_text}",
        "",
        "이 요약 메시지에 직접 답장해 승인, 수정 요청 또는 보류 의견을 남겨주십시오.",
        "판단 문구: 승인 / 수정 요청 / 보류",
        "명확한 승인 전에는 이미지를 생성하지 않습니다.",
    ]
    summary = "\n".join(lines)
    if len(summary) > MAX_SUMMARY_LENGTH:
        lines[-5] = "검토 주의사항: 상세 보고서 참조"
        summary = "\n".join(lines)
    if len(summary) > MAX_SUMMARY_LENGTH:
        raise TelegramError("보고 요약이 4096자를 초과함. 복구: 작업 제목과 핵심 문장을 줄이십시오.")
    return summary


def _message_id(result: Any, label: str) -> int:
    if not isinstance(result, dict) or isinstance(result.get("message_id"), bool) or not isinstance(
        result.get("message_id"), int
    ) or result["message_id"] <= 0:
        raise TelegramError(f"{label} 응답에 메시지 ID가 없음. 복구: Telegram 응답을 확인하십시오.")
    return result["message_id"]


def _ready_job(project_root: Path | str, job_id: str) -> tuple[dict[str, Any], Path, str]:
    errors = job_store.validate_job(project_root, job_id)
    if errors:
        job_store.mark_validation_failure(project_root, job_id, errors)
        raise TelegramError(
            "로컬 작업 검증 실패: " + "; ".join(errors) + ". 복구: NEEDS_REVIEW 원인을 수정하십시오."
        )
    job = job_store.load_job(project_root, job_id)
    revision = job_store.current_revision(job)
    report_path = job_store.job_root(project_root, job_id) / revision["report_path"]
    try:
        report_text = report_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise TelegramError(
            f"보고서를 읽을 수 없음: {exc}. 복구: 현재 리비전 report.md를 복구하십시오."
        ) from exc
    return job, report_path, report_text


def _event(job: dict[str, Any], key: str) -> dict[str, Any] | None:
    return next((item for item in job.get("events", []) if item.get("idempotency_key") == key), None)


def send_report(
    project_root: Path | str,
    job_id: str,
    config: TelegramConfig,
    *,
    api: Any | None = None,
    resend: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    job, report_path, report_text = _ready_job(project_root, job_id)
    revision = job_store.current_revision(job)
    old_message_id = revision["telegram"]["report_message_id"]
    if job["status"] == "SENT_FOR_APPROVAL" and old_message_id is not None and not resend:
        return {
            "job_id": job_id,
            "revision": job["current_revision"],
            "status": job["status"],
            "summary_message_id": old_message_id,
            "report_document_message_id": None,
            "skipped": True,
            "reason": "현재 리비전 보고가 이미 성공함",
        }
    if resend and old_message_id is None:
        raise TelegramError("재전송할 기존 보고가 없음. 복구: 먼저 일반 보고 전송을 실행하십시오.")
    if job["status"] not in ({"SENT_FOR_APPROVAL", "HOLD"} if resend else {"DRAFT"}):
        raise TelegramError(
            f"{job['status']} 상태에서는 {'재전송' if resend else '보고 전송'}할 수 없음. "
            "복구: 작업 상태와 현재 리비전을 확인하십시오."
        )

    other_pending = [
        item
        for item in job_store.job_ids_with_status(project_root, "SENT_FOR_APPROVAL")
        if item != job_id
    ]
    if other_pending:
        raise TelegramError(
            "다른 Telegram 승인 대기 작업이 있음: " + ", ".join(other_pending) + ". "
            "복구: 기존 작업의 승인·수정 요청·보류를 먼저 처리하십시오."
        )

    client = _api_for(config, api)
    preflight(config, api=client)
    if report_path.stat().st_size > MAX_DOCUMENT_BYTES:
        raise TelegramError("보고서 파일이 50MB를 초과함. 복구: 보고서 크기를 줄이십시오.")
    attempt_key = (
        f"{job_id}:{job['current_revision']}:report-resend-from:{old_message_id}"
        if resend
        else job_store.report_idempotency_key(job_id, job["current_revision"])
    )
    summary_key = attempt_key + ":summary"
    checkpoint = _event(job, summary_key)
    if checkpoint:
        summary_message_id = checkpoint["details"]["summary_message_id"]
    else:
        summary_message_id = _message_id(
            client.call("sendMessage", {"chat_id": config.chat_id, "text": build_report_summary(job, report_text)}),
            "요약 전송",
        )
        job_store.record_report_summary_sent(
            project_root,
            job_id,
            summary_message_id,
            idempotency_key=summary_key,
            sent_at=now,
        )

    document_key = attempt_key + ":document"
    document_checkpoint = _event(job_store.load_job(project_root, job_id), document_key)
    if document_checkpoint:
        document_message_id = document_checkpoint["details"]["document_message_id"]
    else:
        try:
            document_message_id = _message_id(
                client.call(
                    "sendDocument",
                    {
                        "chat_id": config.chat_id,
                        "caption": f"{job_id} · r{job['current_revision']:03d} 전체 검토 보고서",
                        "reply_parameters": {"message_id": summary_message_id},
                    },
                    {"document": report_path},
                ),
                "보고서 파일 전송",
            )
            job_store.record_report_document_sent(
                project_root,
                job_id,
                summary_message_id,
                document_message_id,
                idempotency_key=document_key,
                sent_at=now,
            )
        except TelegramError as exc:
            safe_error = _redact(exc, config.token)
            digest = uuid.uuid5(uuid.NAMESPACE_OID, safe_error).hex[:12]
            job_store.record_report_delivery_failed(
                project_root,
                job_id,
                summary_message_id,
                safe_error,
                idempotency_key=f"{attempt_key}:document-failed:{digest}",
                failed_at=now,
            )
            raise TelegramError(
                f"요약 메시지 {summary_message_id} 전송 후 보고서 파일 전송에 실패함. "
                "복구: 그룹에 보고서 파일이 없는지 확인한 뒤에만 같은 send-report 명령을 "
                "다시 실행하십시오. 요약은 재사용되고 파일만 재시도됩니다."
            ) from exc

    job_store.mark_report_sent(
        project_root,
        job_id,
        summary_message_id,
        report_sent_at=now,
        resend=resend,
        idempotency_key=attempt_key,
        report_document_message_id=document_message_id,
    )
    return {
        "job_id": job_id,
        "revision": job["current_revision"],
        "status": "SENT_FOR_APPROVAL",
        "summary_message_id": summary_message_id,
        "report_document_message_id": document_message_id,
        "skipped": False,
    }


def _report_age(revision: dict[str, Any], now: datetime | None = None) -> timedelta:
    sent_at = revision["telegram"].get("report_sent_at")
    if not isinstance(sent_at, str):
        raise TelegramError("보고 전송 시각이 없음. 복구: 현재 리비전 보고서를 다시 전송하십시오.")
    try:
        sent = datetime.fromisoformat(sent_at)
    except ValueError as exc:
        raise TelegramError("보고 전송 시각 형식이 잘못됨. 복구: 작업 검증을 실행하십시오.") from exc
    current = now or datetime.now().astimezone()
    if current.tzinfo is None or sent.tzinfo is None:
        raise TelegramError("보고 전송 시각에 타임존이 없음. 복구: 작업 검증을 실행하십시오.")
    age = current - sent
    if age < timedelta(0):
        raise TelegramError("보고 전송 시각이 현재보다 미래임. 복구: 시스템 시각을 확인하십시오.")
    return age


def approval_window_expired(job: dict[str, Any], *, now: datetime | None = None) -> bool:
    return _report_age(job_store.current_revision(job), now) > APPROVAL_WINDOW


def check_replies(
    project_root: Path | str,
    job_id: str,
    config: TelegramConfig,
    *,
    api: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    errors = job_store.validate_job(project_root, job_id)
    if errors:
        job_store.mark_validation_failure(project_root, job_id, errors)
        raise TelegramError(
            "로컬 작업 검증 실패: " + "; ".join(errors) + ". 복구: NEEDS_REVIEW 원인을 수정하십시오."
        )
    job = job_store.load_job(project_root, job_id)
    if job["status"] != "SENT_FOR_APPROVAL":
        raise TelegramError("승인 대기 상태가 아님. 복구: 현재 리비전 보고 상태를 확인하십시오.")
    revision = job_store.current_revision(job)
    report_message_id = revision["telegram"]["report_message_id"]
    if approval_window_expired(job, now=now):
        job_store.record_approval_expired(
            project_root, job_id, report_message_id, expired_at=now
        )
        raise ApprovalWindowExpired(
            "보고 후 24시간이 지나 답장을 승인으로 처리하지 않음. 복구: 보고 재전송 명령을 실행하십시오."
        )
    params: dict[str, Any] = {"timeout": 0, "limit": 100, "allowed_updates": ["message"]}
    last_update_id = revision["telegram"]["last_update_id"]
    if last_update_id is not None:
        params["offset"] = last_update_id + 1
    updates = _api_for(config, api).call("getUpdates", params)
    if not isinstance(updates, list):
        raise TelegramError("getUpdates 응답이 배열이 아님. 복구: 잠시 후 다시 실행하십시오.")

    observations: list[dict[str, Any]] = []
    valid_replies: list[dict[str, Any]] = []
    invalid_replies: list[dict[str, Any]] = []
    for update in updates:
        if not isinstance(update, dict) or isinstance(update.get("update_id"), bool) or not isinstance(
            update.get("update_id"), int
        ):
            raise TelegramError("update_id가 없는 Telegram 업데이트가 있음. 복구: 잠시 후 다시 실행하십시오.")
        update_id = update["update_id"]
        message = update.get("message")
        reason: str | None = None
        message_id: int | None = None
        user_id: int | None = None
        message_date: int | None = None
        text = ""
        if not isinstance(message, dict):
            reason = "지원하지 않는 업데이트"
        else:
            raw_message_id = message.get("message_id")
            message_id = (
                raw_message_id
                if isinstance(raw_message_id, int)
                and not isinstance(raw_message_id, bool)
                and raw_message_id > 0
                else None
            )
            raw_message_date = message.get("date")
            message_date = (
                raw_message_date
                if isinstance(raw_message_date, int)
                and not isinstance(raw_message_date, bool)
                and raw_message_date >= 0
                else None
            )
            chat = message.get("chat")
            if not isinstance(chat, dict) or chat.get("id") != config.chat_id:
                reason = "대상 그룹이 아님"
            else:
                sender = message.get("from")
                raw_user_id = sender.get("id") if isinstance(sender, dict) else None
                user_id = (
                    raw_user_id
                    if isinstance(raw_user_id, int)
                    and not isinstance(raw_user_id, bool)
                    and raw_user_id > 0
                    else None
                )
                raw_text = message.get("text", message.get("caption", ""))
                text = raw_text.strip() if isinstance(raw_text, str) else ""
                reply = message.get("reply_to_message")
                if not isinstance(reply, dict) or reply.get("message_id") != report_message_id:
                    reason = "현재 보고 메시지에 대한 직접 답장이 아님"
                else:
                    if user_id not in config.approver_ids:
                        reason = "등록되지 않은 담당자"
                    elif not text:
                        reason = "답장 텍스트 없음"
        observation = {
            "update_id": update_id,
            "message_id": message_id,
            "user_id": user_id,
            "text": text,
            "date": message_date,
            "valid": reason is None,
            "reason": reason,
        }
        observations.append(observation)
        public_item = {
            "update_id": update_id,
            "message_id": message_id,
            "user_id": user_id,
            "text": text,
            "date": message_date,
        }
        if reason is None:
            valid_replies.append(public_item)
        else:
            public_item["reason"] = reason
            invalid_replies.append(public_item)
    job_store.record_reply_observations(
        project_root,
        job_id,
        report_message_id,
        observations,
        observed_at=now,
    )
    return {
        "job_id": job_id,
        "revision": job["current_revision"],
        "report_message_id": report_message_id,
        "valid_replies": valid_replies,
        "invalid_replies": invalid_replies,
        "last_update_id": max((item["update_id"] for item in observations), default=last_update_id),
    }


def send_images(
    project_root: Path | str,
    job_id: str,
    config: TelegramConfig,
    *,
    api: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    errors = job_store.validate_job(project_root, job_id)
    if errors:
        raise TelegramError("작업 검증 실패: " + "; ".join(errors))
    job = job_store.load_job(project_root, job_id)
    if job["status"] not in {"GENERATING", "NEEDS_REVIEW"}:
        raise TelegramError(
            "GENERATING 또는 NEEDS_REVIEW 상태에서만 이미지를 전송할 수 있음. "
            "복구: 승인·생성 상태를 확인하십시오."
        )
    client = _api_for(config, api)
    preflight(config, api=client)
    revision = job_store.current_revision(job)
    total = len(revision["scenes"])
    sent: list[dict[str, int]] = []
    skipped: list[int] = []
    failed: list[dict[str, Any]] = []
    for scene in revision["scenes"]:
        number = scene["scene_number"]
        if scene["status"] == "DELIVERED" and scene.get("telegram_message_id") is not None:
            skipped.append(number)
            continue
        if scene["status"] != "GENERATED" or not scene.get("local_path"):
            continue
        image_path = Path(project_root).expanduser().resolve() / scene["local_path"]
        if not image_path.is_file():
            failed.append({"scene_number": number, "error": "로컬 이미지 파일 없음"})
            break
        if image_path.stat().st_size > MAX_PHOTO_BYTES:
            failed.append({"scene_number": number, "error": "이미지 파일이 10MB를 초과함"})
            break
        caption = (
            f"{job_id} · r{job['current_revision']:03d} · 장면 {number}/{total}\n"
            + scene["source_text"][:800]
        )
        try:
            message_id = _message_id(
                client.call(
                    "sendPhoto",
                    {"chat_id": config.chat_id, "caption": caption},
                    {"photo": image_path},
                ),
                f"장면 {number} 전송",
            )
            job_store.record_scene_delivery(
                project_root,
                job_id,
                number,
                message_id,
                idempotency_key=f"{job_id}:{job['current_revision']}:{number}:telegram",
                delivered_at=now,
            )
            sent.append({"scene_number": number, "telegram_message_id": message_id})
        except (TelegramError, job_store.JobStoreError) as exc:
            failed.append({"scene_number": number, "error": _redact(exc, config.token)})
            break
    return {
        "job_id": job_id,
        "revision": job["current_revision"],
        "sent": sent,
        "skipped": skipped,
        "failed": failed,
    }


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="묵상 쇼츠 Telegram 보고·답장·이미지 전송")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("preflight", help="환경·봇·그룹 접근과 전송 권한 사전 점검")

    report = commands.add_parser("send-report", help="요약 메시지와 전체 보고서 전송")
    report.add_argument("--project-root", default=".")
    report.add_argument("--job-id", required=True)
    report.add_argument("--resend", action="store_true")

    replies = commands.add_parser("check-replies", help="현재 보고 메시지 직접 답장 조회")
    replies.add_argument("--project-root", default=".")
    replies.add_argument("--job-id", required=True)

    images = commands.add_parser("send-images", help="생성 완료 이미지를 번호순으로 전송")
    images.add_argument("--project-root", default=".")
    images.add_argument("--job-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = load_config()
        if args.command == "preflight":
            result = preflight(config)
        elif args.command == "send-report":
            result = send_report(args.project_root, args.job_id, config, resend=args.resend)
        elif args.command == "check-replies":
            result = check_replies(args.project_root, args.job_id, config)
        else:
            result = send_images(args.project_root, args.job_id, config)
        _print_json(result)
        return 1 if args.command == "send-images" and result["failed"] else 0
    except (TelegramError, job_store.JobStoreError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"Telegram 오류: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
