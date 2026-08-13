#!/usr/bin/env python3
"""Gate, persist, resume, and deliver approved devotional scene images."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import job_store
import telegram_bot


MAX_IMAGE_BYTES = 10 * 1024 * 1024
PERSONAS = (
    "a Korean man in his 20s",
    "a Korean woman in her 20s",
    "a Korean man in his 40s",
    "a Korean woman in her 40s",
    "a Korean man in his 60s",
    "a Korean woman in her 60s",
)
GROUP_RE = re.compile(
    r"\b(family|couple|community|congregation|group|crowd|people|neighbor|neighbors|parents|children)\b|"
    r"가족|부부|공동체|회중|여러 사람|이웃들|부모|아이들",
    re.IGNORECASE,
)
SPECIFIC_PERSON_RE = re.compile(
    r"\b(man|woman|male|female|mother|father|widow|husband|wife)\b|"
    r"\b(?:20|40|60)s\b|twenties|forties|sixties|20대|40대|60대|남성|여성|어머니|아버지|과부",
    re.IGNORECASE,
)
BIBLICAL_STYLE = (
    "photorealistic, hyper-detailed, historically accurate ancient Near Eastern setting, "
    "{era}, medium brightness natural lighting, balanced warm color grading, film-like texture, "
    "shallow depth of field"
)
MODERN_STYLE = (
    "photorealistic, hyper-detailed, contemporary Korean setting, medium brightness natural "
    "lighting, balanced warm color grading, film-like texture, shallow depth of field"
)
SHARED_FINISH = (
    "medium brightness natural lighting, balanced warm color grading, film-like texture, "
    "shallow depth of field"
)
REQUIRED_COMPOSITION = "9:16 vertical composition with safe top and bottom caption space, no text, no watermark"
NEW_TESTAMENT_BOOKS = {
    "마태복음", "마가복음", "누가복음", "요한복음", "사도행전", "로마서", "고린도전서",
    "고린도후서", "갈라디아서", "에베소서", "빌립보서", "골로새서", "데살로니가전서",
    "데살로니가후서", "디모데전서", "디모데후서", "디도서", "빌레몬서", "히브리서",
    "야고보서", "베드로전서", "베드로후서", "요한일서", "요한이서", "요한삼서", "유다서",
    "요한계시록", "matthew", "mark", "luke", "john", "acts", "romans", "corinthians",
    "galatians", "ephesians", "philippians", "colossians", "thessalonians", "timothy", "titus",
    "philemon", "hebrews", "james", "peter", "jude", "revelation",
}


class ImageWorkflowError(ValueError):
    """A safe, user-correctable image workflow failure."""


def _append(prompt: str, fragment: str) -> str:
    return prompt if fragment.lower() in prompt.lower() else f"{prompt.rstrip(' ,.')}, {fragment}"


def _biblical_era(reference: str | None) -> str:
    normalized = (reference or "").strip().lower()
    if not normalized:
        return "a general ancient Near Eastern era without unsupported dynasty or city details"
    if any(normalized.startswith(book) for book in NEW_TESTAMENT_BOOKS):
        return "a first-century eastern Mediterranean setting under Roman rule"
    if normalized.startswith(("창세기", "genesis")):
        return "a broadly plausible patriarchal ancient Near Eastern setting without unsupported dynasty details"
    if normalized.startswith(("출애굽기", "레위기", "민수기", "신명기", "exodus", "leviticus", "numbers", "deuteronomy")):
        return "an ancient Near Eastern wilderness setting without unsupported dynasty details"
    if normalized.startswith(("에스라", "느헤미야", "에스더", "ezra", "nehemiah", "esther")):
        return "a Persian-period ancient Near Eastern setting"
    if normalized.startswith(("다니엘", "에스겔", "daniel", "ezekiel")):
        return "an exilic Mesopotamian setting without unsupported city details"
    return "an Iron Age Israelite era"


def resolve_visual_prompts(
    input_data: dict[str, Any], scenes: list[dict[str, Any]], job_id: str
) -> list[dict[str, Any]]:
    """Resolve common styles and deterministic modern-person defaults before approval."""
    if not job_store.JOB_ID_RE.fullmatch(job_id):
        raise ImageWorkflowError("시각 프롬프트를 확정할 유효한 작업 ID가 필요함")
    visual_overrides = input_data.get("visual_overrides") or {}
    style_override = visual_overrides.get("style")
    person_override = visual_overrides.get("person")
    scene_instructions = visual_overrides.get("scene_instructions", {})
    invalid_scene_numbers = [
        key for key in scene_instructions if int(key) > len(scenes)
    ]
    if invalid_scene_numbers:
        raise ImageWorkflowError(
            "존재하지 않는 장면 시각 지시: " + ", ".join(sorted(invalid_scene_numbers))
        )
    start = hashlib.sha256(job_id.encode("utf-8")).digest()[0] % len(PERSONAS)
    biblical_style = BIBLICAL_STYLE.format(era=_biblical_era(input_data.get("passage_reference")))
    modern_index = 0
    resolved: list[dict[str, Any]] = []
    for number, scene in enumerate(copy.deepcopy(scenes), 1):
        prompt = scene["prompt_en"].strip()
        if style_override:
            prompt = _append(prompt, style_override)
            prompt = _append(prompt, SHARED_FINISH)
        if scene["setting"] == "biblical_era":
            if not style_override:
                prompt = _append(prompt, biblical_style)
        else:
            combined = f"{scene.get('description_ko', '')} {prompt}"
            if not style_override:
                prompt = _append(prompt, MODERN_STYLE)
            if person_override:
                prompt = _append(prompt, person_override)
            if GROUP_RE.search(combined):
                prompt = _append(prompt, "a natural mix of adult ages and genders")
            elif not person_override and not SPECIFIC_PERSON_RE.search(combined):
                prompt = _append(prompt, PERSONAS[(start + modern_index) % len(PERSONAS)])
            modern_index += 1
        instruction = scene_instructions.get(str(number))
        if instruction:
            prompt = _append(prompt, instruction)
        scene["prompt_en"] = _append(prompt, REQUIRED_COMPOSITION)
        resolved.append(scene)
    return resolved


def _approval_event(job: dict[str, Any]) -> dict[str, Any]:
    revision = job_store.current_revision(job)
    for event in reversed(job.get("events", [])):
        if (
            event.get("type") == "approval_decision"
            and event.get("revision") == job["current_revision"]
            and event.get("details", {}).get("decision") == "approved"
        ):
            details = event["details"]
            if details.get("report_message_id") != revision["telegram"]["report_message_id"]:
                raise ImageWorkflowError("승인 보고 메시지와 현재 보고 메시지가 다름")
            if details.get("content_signature") != job_store.revision_content_signature(revision):
                raise ImageWorkflowError("승인 이후 원고 또는 장면 프롬프트가 변경됨")
            return event
    raise ImageWorkflowError("현재 리비전의 명확한 승인 근거 이벤트가 없음")


def _validated_job(project_root: Path | str, job_id: str) -> dict[str, Any]:
    errors = job_store.validate_job(project_root, job_id)
    if errors:
        job_store.mark_validation_failure(project_root, job_id, errors)
        raise ImageWorkflowError("작업 무결성 검증 실패: " + "; ".join(errors))
    job = job_store.load_job(project_root, job_id)
    revision = job_store.current_revision(job)
    if revision["approval"]["decision"] != "approved":
        raise ImageWorkflowError("현재 리비전이 명확히 승인되지 않음")
    approval = _approval_event(job)
    approval_index = job["events"].index(approval)
    if any(
        event.get("revision") == job["current_revision"]
        and event.get("type") == "approval_decision"
        and event.get("details", {}).get("decision") in {"revision_requested", "hold"}
        for event in job["events"][approval_index + 1 :]
    ):
        raise ImageWorkflowError("승인 뒤 수정 요청 또는 보류가 기록됨")
    return job


def start_generation(
    project_root: Path | str, job_id: str, *, now: datetime | None = None
) -> dict[str, Any]:
    job = _validated_job(project_root, job_id)
    if job["status"] == "GENERATING":
        return generation_status(job, skipped=True)
    if job["status"] != "APPROVED":
        raise ImageWorkflowError("APPROVED 상태에서만 이미지 생성을 시작할 수 있음")
    signature = job_store.revision_content_signature(job_store.current_revision(job))
    job = job_store.transition_generation(
        project_root,
        job_id,
        "GENERATING",
        reason="현재 리비전 승인 게이트 통과",
        idempotency_key=f"{job_id}:{job['current_revision']}:generation:start:{signature[:16]}",
        now=now,
    )
    return generation_status(job, skipped=False)


def generation_status(job: dict[str, Any], *, skipped: bool = False) -> dict[str, Any]:
    revision = job_store.current_revision(job)
    counts = {state: 0 for state in job_store.SCENE_STATES}
    for scene in revision["scenes"]:
        counts[scene["status"]] += 1
    return {
        "job_id": job["job_id"],
        "revision": job["current_revision"],
        "status": job["status"],
        "scene_counts": counts,
        "skipped": skipped,
    }


def next_scene(
    project_root: Path | str, job_id: str, *, now: datetime | None = None
) -> dict[str, Any]:
    job = _validated_job(project_root, job_id)
    if job["status"] != "GENERATING":
        raise ImageWorkflowError("GENERATING 상태에서만 다음 장면을 시작할 수 있음")
    revision = job_store.current_revision(job)
    scene = next((item for item in revision["scenes"] if item["status"] == "PENDING"), None)
    if scene is None:
        return {
            "job_id": job_id,
            "revision": job["current_revision"],
            "status": job["status"],
            "ready": False,
            "message_ko": "생성할 PENDING 장면이 없습니다. 이미지 전달 단계를 실행하십시오.",
        }
    attempt = scene["attempts"] + 1
    key = job_store.image_idempotency_key(
        job_id, job["current_revision"], scene["scene_number"], scene["prompt_hash"]
    )
    active_key = f"{key}:attempt:{scene['attempts']}:start"
    active_attempt = scene["attempts"] > 0 and any(
        event.get("idempotency_key") == active_key
        for event in job.get("events", [])
    ) and not any(
        event.get("idempotency_key") == f"{key}:attempt:{scene['attempts']}:failed"
        for event in job.get("events", [])
    )
    if active_attempt:
        attempt = scene["attempts"]
    else:
        job_store.record_scene_attempt(
            project_root,
            job_id,
            scene["scene_number"],
            idempotency_key=f"{key}:attempt:{attempt}:start",
            attempted_at=now,
        )
    return {
        "job_id": job_id,
        "revision": job["current_revision"],
        "status": "GENERATING",
        "ready": True,
        "scene_number": scene["scene_number"],
        "attempt": attempt,
        "attempt_already_reserved": active_attempt,
        "source_text": scene["source_text"],
        "description_ko": scene["description_ko"],
        "prompt_en": scene["prompt_en"],
        "prompt_hash": scene["prompt_hash"],
        "tool_mode": "built-in image_gen",
    }


def _destination(project_root: Path | str, job: dict[str, Any], scene_number: int, extension: str) -> Path:
    root = job_store.job_root(project_root, job["job_id"])
    return (
        root
        / "revisions"
        / f"r{job['current_revision']:03d}"
        / "images"
        / job_store.scene_filename(scene_number, extension)
    )


def record_success(
    project_root: Path | str,
    job_id: str,
    scene_number: int,
    source_file: Path | str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    job = _validated_job(project_root, job_id)
    if job["status"] != "GENERATING":
        raise ImageWorkflowError("GENERATING 상태에서만 이미지 결과를 저장할 수 있음")
    revision = job_store.current_revision(job)
    if scene_number < 1 or scene_number > len(revision["scenes"]):
        raise ImageWorkflowError("존재하지 않는 장면 번호")
    scene = revision["scenes"][scene_number - 1]
    source = Path(source_file).expanduser().resolve()
    try:
        metadata = job_store.inspect_image(source)
    except job_store.JobStoreError as exc:
        raise ImageWorkflowError(str(exc)) from exc
    if metadata["height"] <= metadata["width"]:
        raise ImageWorkflowError("9:16 구성에 사용할 세로형 이미지가 아님")
    if metadata["bytes"] > MAX_IMAGE_BYTES:
        raise ImageWorkflowError("이미지 파일이 Telegram 사진 제한인 10MB를 초과함")
    destination = _destination(project_root, job, scene_number, metadata["extension"])
    siblings = list(destination.parent.glob(f"scene-{scene_number:03d}.*"))
    if siblings:
        if len(siblings) != 1:
            raise ImageWorkflowError("같은 장면 번호의 이미지 파일이 둘 이상 존재함")
        existing = job_store.inspect_image(siblings[0])
        if siblings[0] != destination or existing["sha256"] != metadata["sha256"]:
            raise ImageWorkflowError("기존 정상 이미지를 다른 결과로 덮어쓸 수 없음")
    elif source != destination:
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "wb", dir=destination.parent, prefix=f".{destination.name}.", delete=False
            ) as handle:
                with source.open("rb") as input_handle:
                    shutil.copyfileobj(input_handle, handle)
                handle.flush()
                os.fsync(handle.fileno())
                temporary = Path(handle.name)
            copied = job_store.inspect_image(temporary)
            if copied != metadata:
                raise ImageWorkflowError("복사 후 이미지 메타데이터가 원본과 다름")
            os.link(temporary, destination)
        except FileExistsError as exc:
            raise ImageWorkflowError("이미지 저장 중 같은 파일명이 먼저 생성됨") from exc
        except OSError as exc:
            raise ImageWorkflowError(f"이미지를 원자적으로 저장할 수 없음: {exc}") from exc
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
    local_path = str(destination.relative_to(Path(project_root).expanduser().resolve()))
    key = job_store.image_idempotency_key(
        job_id, job["current_revision"], scene_number, scene["prompt_hash"]
    )
    try:
        updated = job_store.record_scene_generated(
            project_root,
            job_id,
            scene_number,
            local_path,
            metadata["sha256"],
            idempotency_key=key,
            generated_at=now,
        )
    except job_store.JobStoreError as exc:
        raise ImageWorkflowError(str(exc)) from exc
    stored = job_store.current_revision(updated)["scenes"][scene_number - 1]
    return {
        "job_id": job_id,
        "revision": updated["current_revision"],
        "status": updated["status"],
        "scene_number": scene_number,
        "scene_status": stored["status"],
        "attempts": stored["attempts"],
        "local_path": stored["local_path"],
        "image_hash": stored["image_hash"],
        "width": metadata["width"],
        "height": metadata["height"],
    }


def record_failure(
    project_root: Path | str,
    job_id: str,
    scene_number: int,
    error: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    job = _validated_job(project_root, job_id)
    revision = job_store.current_revision(job)
    if job["status"] != "GENERATING" or not 1 <= scene_number <= len(revision["scenes"]):
        raise ImageWorkflowError("현재 생성 중인 올바른 장면 번호가 아님")
    scene = revision["scenes"][scene_number - 1]
    key = job_store.image_idempotency_key(
        job_id, job["current_revision"], scene_number, scene["prompt_hash"]
    )
    try:
        updated = job_store.record_scene_generation_failed(
            project_root,
            job_id,
            scene_number,
            error,
            idempotency_key=f"{key}:attempt:{scene['attempts']}:failed",
            failed_at=now,
        )
    except job_store.JobStoreError as exc:
        raise ImageWorkflowError(str(exc)) from exc
    stored = job_store.current_revision(updated)["scenes"][scene_number - 1]
    return {
        "job_id": job_id,
        "revision": updated["current_revision"],
        "status": updated["status"],
        "scene_number": scene_number,
        "scene_status": stored["status"],
        "attempts": stored["attempts"],
        "retry_allowed": stored["status"] == "PENDING",
        "error": stored["error"],
    }


def abort_unavailable(
    project_root: Path | str, job_id: str, reason: str, *, now: datetime | None = None
) -> dict[str, Any]:
    job = _validated_job(project_root, job_id)
    if job["status"] == "APPROVED":
        return generation_status(job, skipped=True)
    if job["status"] != "GENERATING":
        raise ImageWorkflowError("GENERATING 상태에서만 ImageGen 사용 불가 복구를 실행할 수 있음")
    job = job_store.transition_generation(
        project_root,
        job_id,
        "APPROVED",
        reason=reason,
        idempotency_key=f"{job_id}:{job['current_revision']}:generation:unavailable",
        now=now,
    )
    return generation_status(job)


def resume_failed(
    project_root: Path | str,
    job_id: str,
    reason: str,
    idempotency_key: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    job = _validated_job(project_root, job_id)
    if job["status"] != "NEEDS_REVIEW":
        raise ImageWorkflowError("NEEDS_REVIEW 상태에서만 실패 장면을 재개할 수 있음")
    try:
        updated = job_store.resume_failed_generation(
            project_root,
            job_id,
            idempotency_key=idempotency_key,
            reason=reason,
            resumed_at=now,
        )
    except job_store.JobStoreError as exc:
        raise ImageWorkflowError(str(exc)) from exc
    return generation_status(updated)


def _summary_event(job: dict[str, Any], key: str) -> dict[str, Any] | None:
    return next((event for event in job.get("events", []) if event.get("idempotency_key") == key), None)


def deliver(
    project_root: Path | str,
    job_id: str,
    config: telegram_bot.TelegramConfig,
    *,
    api: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    job = _validated_job(project_root, job_id)
    if job["status"] == "DELIVERED":
        return {**generation_status(job, skipped=True), "transport_failures": [], "summary_skipped": True}
    if job["status"] not in {"GENERATING", "NEEDS_REVIEW"}:
        raise ImageWorkflowError("GENERATING 또는 NEEDS_REVIEW 상태에서만 이미지를 전달할 수 있음")
    try:
        transport = telegram_bot.send_images(project_root, job_id, config, api=api, now=now)
    except (telegram_bot.TelegramError, job_store.JobStoreError) as exc:
        raise ImageWorkflowError(str(exc)) from exc
    if transport["failed"]:
        current = job_store.load_job(project_root, job_id)
        return {
            **generation_status(current),
            "transport_failures": transport["failed"],
            "summary_skipped": True,
        }

    current = job_store.load_job(project_root, job_id)
    revision = job_store.current_revision(current)
    delivered = [scene["scene_number"] for scene in revision["scenes"] if scene["status"] == "DELIVERED"]
    failed = [scene["scene_number"] for scene in revision["scenes"] if scene["status"] == "FAILED"]
    pending = [scene["scene_number"] for scene in revision["scenes"] if scene["status"] == "PENDING"]
    last_image_event = next(
        (
            event.get("idempotency_key")
            for event in reversed(current.get("events", []))
            if event.get("type")
            in {
                "scene_generated",
                "scene_generation_failed",
                "generation_resumed",
                "scene_delivered",
            }
        ),
        None,
    )
    signature = hashlib.sha256(
        json.dumps(
            {
                "delivered": delivered,
                "failed": failed,
                "pending": pending,
                "last_image_event": last_image_event,
            }
        ).encode("utf-8")
    ).hexdigest()[:16]
    summary_key = f"{job_id}:{current['current_revision']}:delivery-summary:{signature}"
    summary_event = _summary_event(current, summary_key)
    if summary_event is None:
        folder = f"jobs/{job_id}/revisions/r{current['current_revision']:03d}/images"
        text = (
            f"[묵상 쇼츠 이미지 전달 결과]\n작업 ID: {job_id}\n"
            f"리비전: r{current['current_revision']:03d}\n성공: {len(delivered)}개\n"
            f"실패: {len(failed)}개 ({failed or '없음'})\n미생성: {len(pending)}개\n"
            f"로컬 폴더: {folder}"
        )
        client = api or telegram_bot.TelegramAPI(config.token)
        result = client.call("sendMessage", {"chat_id": config.chat_id, "text": text})
        try:
            summary_message_id = telegram_bot._message_id(result, "이미지 전달 요약")
            job_store.record_delivery_summary(
                project_root,
                job_id,
                summary_message_id,
                idempotency_key=summary_key,
                delivered_count=len(delivered),
                failed_scene_numbers=failed,
                sent_at=now,
            )
        except (telegram_bot.TelegramError, job_store.JobStoreError) as exc:
            raise ImageWorkflowError(str(exc)) from exc
    else:
        summary_message_id = summary_event["details"]["telegram_message_id"]

    current = job_store.load_job(project_root, job_id)
    if not failed and not pending and all(
        scene["status"] == "DELIVERED" for scene in job_store.current_revision(current)["scenes"]
    ):
        current = job_store.transition_generation(
            project_root,
            job_id,
            "DELIVERED",
            reason="모든 이미지의 로컬 저장과 Telegram 전달 완료",
            idempotency_key=f"{job_id}:{current['current_revision']}:delivery:complete",
            now=now,
        )
    return {
        **generation_status(current),
        "transport_failures": [],
        "summary_message_id": summary_message_id,
        "summary_skipped": summary_event is not None,
    }


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="승인된 묵상 쇼츠 이미지 생성·저장·전달 워크플로")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("start", "현재 승인 게이트를 검증하고 생성 상태 시작"),
        ("next-scene", "다음 PENDING 장면의 생성 시도를 예약"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--project-root", default=".")
        command.add_argument("--job-id", required=True)

    success = commands.add_parser("record-success", help="ImageGen 결과를 검증해 원자적으로 저장")
    success.add_argument("--project-root", default=".")
    success.add_argument("--job-id", required=True)
    success.add_argument("--scene-number", required=True, type=int)
    success.add_argument("--source-file", required=True)

    failure = commands.add_parser("record-failure", help="한 장면의 ImageGen 실패 기록")
    failure.add_argument("--project-root", default=".")
    failure.add_argument("--job-id", required=True)
    failure.add_argument("--scene-number", required=True, type=int)
    failure.add_argument("--error", required=True)

    abort = commands.add_parser("abort-unavailable", help="ImageGen 사용 불가 시 APPROVED로 복구")
    abort.add_argument("--project-root", default=".")
    abort.add_argument("--job-id", required=True)
    abort.add_argument("--reason", required=True)

    resume = commands.add_parser("resume-failed", help="수동 검토 후 FAILED 장면만 재개")
    resume.add_argument("--project-root", default=".")
    resume.add_argument("--job-id", required=True)
    resume.add_argument("--reason", required=True)
    resume.add_argument("--idempotency-key", required=True)

    delivery = commands.add_parser("deliver", help="정상 이미지를 Telegram으로 전달하고 완료 확정")
    delivery.add_argument("--project-root", default=".")
    delivery.add_argument("--job-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "start":
            result = start_generation(args.project_root, args.job_id)
        elif args.command == "next-scene":
            result = next_scene(args.project_root, args.job_id)
        elif args.command == "record-success":
            result = record_success(
                args.project_root, args.job_id, args.scene_number, args.source_file
            )
        elif args.command == "record-failure":
            result = record_failure(
                args.project_root, args.job_id, args.scene_number, args.error
            )
        elif args.command == "abort-unavailable":
            result = abort_unavailable(args.project_root, args.job_id, args.reason)
        elif args.command == "resume-failed":
            result = resume_failed(
                args.project_root,
                args.job_id,
                args.reason,
                args.idempotency_key,
            )
        else:
            result = deliver(
                args.project_root,
                args.job_id,
                telegram_bot.load_config(),
            )
        _print(result)
        if args.command == "deliver" and result.get("transport_failures"):
            return 1
        return 0
    except (
        ImageWorkflowError,
        job_store.JobStoreError,
        telegram_bot.TelegramError,
        OSError,
        UnicodeError,
    ) as exc:
        print(f"이미지 워크플로 실패: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
