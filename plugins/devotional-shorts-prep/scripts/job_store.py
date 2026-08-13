#!/usr/bin/env python3
"""File-backed devotional Shorts jobs, revisions, and state transitions."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import struct
import sys
import tempfile
import uuid
import zlib
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 1
JOB_ID_RE = re.compile(r"^ds-\d{8}-\d{6}-[0-9a-f]{8}$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

STATES = {
    "DRAFT",
    "SENT_FOR_APPROVAL",
    "APPROVED",
    "REVISION_REQUESTED",
    "HOLD",
    "GENERATING",
    "DELIVERED",
    "NEEDS_REVIEW",
}

GENERATION_TRANSITIONS = {
    ("APPROVED", "GENERATING"),
    ("GENERATING", "DELIVERED"),
    ("GENERATING", "NEEDS_REVIEW"),
    ("GENERATING", "APPROVED"),
    ("NEEDS_REVIEW", "GENERATING"),
}

DECISION_TO_STATE = {
    "approved": "APPROVED",
    "revision_requested": "REVISION_REQUESTED",
    "hold": "HOLD",
    "unclear": "SENT_FOR_APPROVAL",
}

SCENE_SECTIONS = {"biblical", "interpretation", "modern_application", "prayer", "cta"}
SCENE_SETTINGS = {"biblical_era", "modern"}
SCENE_STATES = {"PENDING", "GENERATED", "FAILED", "DELIVERED"}
IMAGE_EXTENSIONS = {"png", "jpg", "webp"}
QUALITY_AXES = {
    "passage_fidelity",
    "interpretive_clarity",
    "evangelical_consistency",
    "spoken_naturalness",
    "application_specificity",
}

FIXED_CTA = (
    "이 영상이 도움이 되셨다면 좋아요와 구독으로 응원해주시고, "
    "생각난 분들에게 이 영상을 공유해주시기 바랍니다."
)


class JobStoreError(ValueError):
    """A user-correctable job data or state error."""


def _aware_now(now: datetime | None = None) -> datetime:
    current = now or datetime.now().astimezone()
    return current.astimezone() if current.tzinfo is None else current


def _iso(now: datetime | None = None) -> str:
    return _aware_now(now).isoformat(timespec="seconds")


def _validate_timestamp(value: Any, field: str) -> None:
    if not isinstance(value, str):
        raise JobStoreError(f"{field}은 타임존이 포함된 ISO 8601 문자열이어야 함")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise JobStoreError(f"{field}의 ISO 8601 형식이 잘못됨") from exc
    if parsed.utcoffset() is None:
        raise JobStoreError(f"{field}에 타임존이 없음")


def generate_job_id(now: datetime | None = None) -> str:
    current = _aware_now(now)
    return f"ds-{current:%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}"


def _check_job_id(job_id: str) -> None:
    if not JOB_ID_RE.fullmatch(job_id):
        raise JobStoreError("작업 ID 형식이 ds-YYYYMMDD-HHmmss-xxxxxxxx가 아님")


def _jobs_root(project_root: Path | str) -> Path:
    return Path(project_root).expanduser().resolve() / "jobs"


def _job_root(project_root: Path | str, job_id: str) -> Path:
    _check_job_id(job_id)
    return _jobs_root(project_root) / job_id


def job_root(project_root: Path | str, job_id: str) -> Path:
    """Return the validated absolute root for one job."""
    return _job_root(project_root, job_id)


def _revision_name(number: int) -> str:
    if number < 1 or number > 999:
        raise JobStoreError("리비전 번호는 1~999여야 함")
    return f"r{number:03d}"


def report_idempotency_key(job_id: str, revision: int) -> str:
    _check_job_id(job_id)
    _revision_name(revision)
    return f"{job_id}:{revision}:report"


def image_idempotency_key(job_id: str, revision: int, scene_number: int, prompt_hash: str) -> str:
    _check_job_id(job_id)
    _revision_name(revision)
    if scene_number < 1:
        raise JobStoreError("장면 번호는 1 이상이어야 함")
    if not SHA256_RE.fullmatch(prompt_hash):
        raise JobStoreError("프롬프트 해시가 SHA-256 형식이 아님")
    return f"{job_id}:{revision}:{scene_number}:{prompt_hash}"


def scene_filename(scene_number: int, extension: str) -> str:
    if scene_number < 1 or scene_number > 999:
        raise JobStoreError("장면 번호는 1~999여야 함")
    normalized = extension.lower().removeprefix(".")
    if normalized not in IMAGE_EXTENSIONS:
        raise JobStoreError("이미지 확장자는 png, jpg, webp 중 하나여야 함")
    return f"scene-{scene_number:03d}.{normalized}"


def inspect_image(path: Path | str) -> dict[str, Any]:
    """Validate a PNG, JPEG, or WebP container and return stable file metadata."""
    image_path = Path(path)
    try:
        data = image_path.read_bytes()
    except OSError as exc:
        raise JobStoreError(f"이미지 파일을 읽을 수 없음: {exc}") from exc
    if not data:
        raise JobStoreError("이미지 파일이 비어 있음")

    extension: str
    width: int
    height: int
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        extension, width, height = _inspect_png(data)
    elif data.startswith(b"\xff\xd8"):
        extension, width, height = _inspect_jpeg(data)
    elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        extension, width, height = _inspect_webp(data)
    else:
        raise JobStoreError("지원하지 않거나 형식을 확인할 수 없는 이미지")
    if width < 1 or height < 1:
        raise JobStoreError("이미지 크기가 잘못됨")
    return {
        "extension": extension,
        "width": width,
        "height": height,
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
    }


def _inspect_png(data: bytes) -> tuple[str, int, int]:
    position = 8
    width = height = 0
    saw_iend = False
    chunk_index = 0
    while position + 12 <= len(data):
        length = struct.unpack(">I", data[position : position + 4])[0]
        chunk_type = data[position + 4 : position + 8]
        end = position + 12 + length
        if end > len(data):
            raise JobStoreError("PNG 청크 길이가 파일 범위를 벗어남")
        payload = data[position + 8 : position + 8 + length]
        expected_crc = struct.unpack(">I", data[position + 8 + length : end])[0]
        if zlib.crc32(chunk_type + payload) & 0xFFFFFFFF != expected_crc:
            raise JobStoreError("PNG 청크 CRC가 잘못됨")
        if chunk_index == 0:
            if chunk_type != b"IHDR" or length != 13:
                raise JobStoreError("PNG 첫 청크가 올바른 IHDR이 아님")
            width, height = struct.unpack(">II", payload[:8])
        if chunk_type == b"IEND":
            if length != 0 or end != len(data):
                raise JobStoreError("PNG IEND 뒤에 불필요한 데이터가 있음")
            saw_iend = True
            break
        position = end
        chunk_index += 1
    if not saw_iend:
        raise JobStoreError("PNG IEND 청크가 없음")
    return "png", width, height


def _inspect_jpeg(data: bytes) -> tuple[str, int, int]:
    if len(data) < 4 or not data.endswith(b"\xff\xd9"):
        raise JobStoreError("JPEG 시작 또는 종료 마커가 잘못됨")
    position = 2
    width = height = 0
    saw_scan = False
    sof_markers = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    while position < len(data) - 2:
        if data[position] != 0xFF:
            raise JobStoreError("JPEG 세그먼트 마커가 잘못됨")
        while position < len(data) and data[position] == 0xFF:
            position += 1
        if position >= len(data):
            raise JobStoreError("JPEG 마커가 중간에서 끝남")
        marker = data[position]
        position += 1
        if marker == 0xDA:
            if position + 2 > len(data):
                raise JobStoreError("JPEG 스캔 헤더 길이가 없음")
            length = struct.unpack(">H", data[position : position + 2])[0]
            if length < 2 or position + length >= len(data) - 2:
                raise JobStoreError("JPEG 스캔 데이터가 비어 있거나 잘못됨")
            saw_scan = True
            break
        if marker in {0x01, *range(0xD0, 0xD9)}:
            continue
        if position + 2 > len(data):
            raise JobStoreError("JPEG 세그먼트 길이가 없음")
        length = struct.unpack(">H", data[position : position + 2])[0]
        if length < 2 or position + length > len(data):
            raise JobStoreError("JPEG 세그먼트 길이가 잘못됨")
        if marker in sof_markers:
            if length < 7:
                raise JobStoreError("JPEG SOF 세그먼트가 너무 짧음")
            height, width = struct.unpack(">HH", data[position + 3 : position + 7])
        position += length
    if not width or not height or not saw_scan:
        raise JobStoreError("JPEG 크기 또는 스캔 정보를 찾을 수 없음")
    return "jpg", width, height


def _inspect_webp(data: bytes) -> tuple[str, int, int]:
    if len(data) < 20 or struct.unpack("<I", data[4:8])[0] + 8 != len(data):
        raise JobStoreError("WebP RIFF 길이가 잘못됨")
    position = 12
    width = height = 0
    saw_bitstream = False
    while position + 8 <= len(data):
        chunk_type = data[position : position + 4]
        length = struct.unpack("<I", data[position + 4 : position + 8])[0]
        start = position + 8
        end = start + length
        if end > len(data):
            raise JobStoreError("WebP 청크 길이가 파일 범위를 벗어남")
        payload = data[start:end]
        if chunk_type == b"VP8X" and len(payload) >= 10:
            width = 1 + int.from_bytes(payload[4:7], "little")
            height = 1 + int.from_bytes(payload[7:10], "little")
        elif chunk_type == b"VP8L" and len(payload) >= 5 and payload[0] == 0x2F:
            width = 1 + payload[1] + ((payload[2] & 0x3F) << 8)
            height = 1 + (payload[2] >> 6) + (payload[3] << 2) + ((payload[4] & 0x0F) << 10)
            saw_bitstream = True
        elif chunk_type == b"VP8 " and len(payload) >= 10 and payload[3:6] == b"\x9d\x01\x2a":
            width = struct.unpack("<H", payload[6:8])[0] & 0x3FFF
            height = struct.unpack("<H", payload[8:10])[0] & 0x3FFF
            saw_bitstream = True
        position = end + (length % 2)
    if position != len(data) or not width or not height or not saw_bitstream:
        raise JobStoreError("WebP 구조 또는 크기 정보가 잘못됨")
    return "webp", width, height


def _required_string(value: Any, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise JobStoreError(f"{field}은 비어 있지 않은 문자열이어야 함")
    return value


def normalize_input(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise JobStoreError("input은 객체여야 함")
    allowed = {
        "passage_text",
        "passage_reference",
        "title",
        "date",
        "special_instructions",
        "visual_overrides",
    }
    unknown = set(data) - allowed
    if unknown:
        raise JobStoreError(f"알 수 없는 input 필드: {', '.join(sorted(unknown))}")

    result: dict[str, Any] = {
        "passage_text": _required_string(data.get("passage_text"), "input.passage_text"),
        "passage_reference": data.get("passage_reference"),
        "title": data.get("title"),
        "date": data.get("date"),
        "special_instructions": data.get("special_instructions", []),
        "visual_overrides": data.get("visual_overrides"),
    }
    for field in ("passage_reference", "title"):
        if result[field] is not None:
            _required_string(result[field], f"input.{field}")
    if result["date"] is not None:
        if not isinstance(result["date"], str):
            raise JobStoreError("input.date는 ISO 날짜 문자열 또는 null이어야 함")
        try:
            date.fromisoformat(result["date"])
        except ValueError as exc:
            raise JobStoreError("input.date의 ISO 날짜 형식이 잘못됨") from exc
    if not isinstance(result["special_instructions"], list) or not all(
        isinstance(item, str) and item.strip() for item in result["special_instructions"]
    ):
        raise JobStoreError("input.special_instructions는 비어 있지 않은 문자열 배열이어야 함")
    if result["visual_overrides"] is not None:
        visual = result["visual_overrides"]
        if not isinstance(visual, dict) or set(visual) - {"style", "person", "scene_instructions"}:
            raise JobStoreError(
                "input.visual_overrides는 style, person, scene_instructions만 가진 객체 또는 null이어야 함"
            )
        for field in ("style", "person"):
            if field in visual and visual[field] is not None:
                _required_string(visual[field], f"input.visual_overrides.{field}")
        instructions = visual.get("scene_instructions", {})
        if not isinstance(instructions, dict) or not all(
            isinstance(key, str)
            and key.isdigit()
            and int(key) > 0
            and isinstance(value, str)
            and value.strip()
            for key, value in instructions.items()
        ):
            raise JobStoreError("input.visual_overrides.scene_instructions 형식이 잘못됨")
    return result


def _validate_selection(data: Any, field: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise JobStoreError(f"script.{field}는 객체여야 함")
    if set(data) != {"selected", "alternatives", "candidates"}:
        raise JobStoreError(f"script.{field} 필드는 selected, alternatives, candidates여야 함")
    selected = _required_string(data["selected"], f"script.{field}.selected")
    alternatives = data["alternatives"]
    candidates = data["candidates"]
    if not isinstance(alternatives, list) or len(alternatives) != 3:
        raise JobStoreError(f"script.{field}.alternatives는 정확히 3개여야 함")
    if not all(isinstance(item, str) and item.strip() for item in alternatives):
        raise JobStoreError(f"script.{field}.alternatives 항목이 잘못됨")
    if len({selected, *alternatives}) != 4:
        raise JobStoreError(f"script.{field} 선택안과 대안이 중복됨")
    if not isinstance(candidates, list) or len(candidates) != 10:
        raise JobStoreError(f"script.{field}.candidates는 정확히 10개여야 함")
    texts: list[str] = []
    normalized_candidates: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates, 1):
        if not isinstance(candidate, dict) or set(candidate) != {"text", "score"}:
            raise JobStoreError(f"script.{field}.candidates[{index}] 형식이 잘못됨")
        text = _required_string(candidate["text"], f"script.{field}.candidates[{index}].text")
        score = candidate["score"]
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 100:
            raise JobStoreError(f"script.{field}.candidates[{index}].score는 0~100 숫자여야 함")
        texts.append(text)
        normalized_candidates.append({"text": text, "score": score})
    if len(set(texts)) != 10:
        raise JobStoreError(f"script.{field}.candidates 텍스트가 중복됨")
    if selected not in texts or not set(alternatives).issubset(texts):
        raise JobStoreError(f"script.{field} 선택안과 대안이 후보에 없음")
    return {"selected": selected, "alternatives": list(alternatives), "candidates": normalized_candidates}


def normalize_content(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or set(data) != {"devotional_points", "script", "scenes"}:
        raise JobStoreError("content 필드는 devotional_points, script, scenes여야 함")

    points = data["devotional_points"]
    point_fields = {
        "background",
        "interpretation",
        "core_sentence",
        "application",
        "application_questions",
    }
    if not isinstance(points, dict) or set(points) != point_fields:
        raise JobStoreError("devotional_points 필드가 계약과 다름")
    normalized_points = {
        field: _required_string(points[field], f"devotional_points.{field}")
        for field in ("background", "interpretation", "core_sentence", "application")
    }
    questions = points["application_questions"]
    if not isinstance(questions, list) or not 1 <= len(questions) <= 2:
        raise JobStoreError("application_questions는 1~2개여야 함")
    if not all(isinstance(item, str) and item.strip() for item in questions):
        raise JobStoreError("application_questions 항목이 잘못됨")
    normalized_points["application_questions"] = list(questions)

    script = data["script"]
    script_fields = {
        "core_sentence",
        "thumbnail",
        "opening",
        "bridge",
        "context",
        "passage_summary",
        "interpretation_application",
        "conclusion",
        "prayer",
        "cta",
        "full_text",
        "estimated_seconds",
        "duration_exception",
        "duration_exception_reason",
    }
    if not isinstance(script, dict) or set(script) != script_fields:
        raise JobStoreError("script 필드가 계약과 다름")
    normalized_script: dict[str, Any] = {
        "core_sentence": _required_string(script["core_sentence"], "script.core_sentence"),
        "thumbnail": _validate_selection(script["thumbnail"], "thumbnail"),
        "opening": _validate_selection(script["opening"], "opening"),
    }
    for field in (
        "bridge",
        "passage_summary",
        "interpretation_application",
        "conclusion",
        "prayer",
        "cta",
        "full_text",
    ):
        normalized_script[field] = _required_string(script[field], f"script.{field}")
    normalized_script["context"] = _required_string(script["context"], "script.context", allow_empty=True)
    if normalized_script["cta"] != FIXED_CTA:
        raise JobStoreError("script.cta가 고정 CTA와 다름")
    seconds = script["estimated_seconds"]
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not 0 < seconds <= 150:
        raise JobStoreError("script.estimated_seconds는 0초 초과 150초 이하 숫자여야 함")
    normalized_script["estimated_seconds"] = seconds
    if not isinstance(script["duration_exception"], bool):
        raise JobStoreError("script.duration_exception은 boolean이어야 함")
    normalized_script["duration_exception"] = script["duration_exception"]
    reason = script["duration_exception_reason"]
    if reason is not None and not isinstance(reason, str):
        raise JobStoreError("script.duration_exception_reason은 문자열 또는 null이어야 함")
    if script["duration_exception"] and (not isinstance(reason, str) or not reason.strip()):
        raise JobStoreError("분량 예외에는 사유가 필요함")
    if not script["duration_exception"] and reason not in (None, ""):
        raise JobStoreError("분량 예외가 아니면 사유는 null 또는 빈 문자열이어야 함")
    normalized_script["duration_exception_reason"] = reason

    scenes = data["scenes"]
    if not isinstance(scenes, list) or not scenes:
        raise JobStoreError("scenes는 하나 이상의 장면 배열이어야 함")
    normalized_scenes: list[dict[str, Any]] = []
    required_scene_fields = {"source_text", "section", "setting", "description_ko", "prompt_en"}
    for number, scene in enumerate(scenes, 1):
        if not isinstance(scene, dict) or set(scene) != required_scene_fields:
            raise JobStoreError(f"scenes[{number}] 필드가 계약과 다름")
        section = scene["section"]
        setting = scene["setting"]
        if section not in SCENE_SECTIONS:
            raise JobStoreError(f"scenes[{number}].section이 허용값이 아님")
        if setting not in SCENE_SETTINGS:
            raise JobStoreError(f"scenes[{number}].setting이 허용값이 아님")
        prompt = _required_string(scene["prompt_en"], f"scenes[{number}].prompt_en")
        normalized_scenes.append(
            {
                "scene_number": number,
                "source_text": _required_string(scene["source_text"], f"scenes[{number}].source_text"),
                "section": section,
                "setting": setting,
                "description_ko": _required_string(
                    scene["description_ko"], f"scenes[{number}].description_ko"
                ),
                "prompt_en": prompt,
                "prompt_hash": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "status": "PENDING",
                "attempts": 0,
                "local_path": None,
                "image_hash": None,
                "telegram_message_id": None,
                "error": None,
            }
        )

    return {"devotional_points": normalized_points, "script": normalized_script, "scenes": normalized_scenes}


def normalize_quality_evaluation(data: Any) -> dict[str, Any]:
    """Normalize detailed semantic review data kept outside the human-facing report."""
    if not isinstance(data, dict) or set(data) != {
        "scores",
        "average",
        "reasons",
        "immediate_failures",
    }:
        raise JobStoreError("quality_evaluation 필드가 계약과 다름")
    scores = data["scores"]
    reasons = data["reasons"]
    failures = data["immediate_failures"]
    if not isinstance(scores, dict) or set(scores) != QUALITY_AXES:
        raise JobStoreError("quality_evaluation.scores 축이 계약과 다름")
    if not isinstance(reasons, dict) or set(reasons) != QUALITY_AXES:
        raise JobStoreError("quality_evaluation.reasons 축이 계약과 다름")
    if any(type(scores[axis]) is not int or not 1 <= scores[axis] <= 5 for axis in QUALITY_AXES):
        raise JobStoreError("quality_evaluation 점수는 1~5점 정수여야 함")
    if any(not isinstance(reasons[axis], str) or not reasons[axis].strip() for axis in QUALITY_AXES):
        raise JobStoreError("quality_evaluation 근거가 비어 있음")
    if not isinstance(failures, list) or not all(
        isinstance(item, str) and item.strip() for item in failures
    ):
        raise JobStoreError("quality_evaluation.immediate_failures가 문자열 배열이 아님")
    average = round(sum(scores.values()) / len(QUALITY_AXES), 1)
    if isinstance(data["average"], bool) or not isinstance(data["average"], (int, float)):
        raise JobStoreError("quality_evaluation.average가 숫자가 아님")
    if round(float(data["average"]), 1) != average:
        raise JobStoreError("quality_evaluation.average가 점수 평균과 다름")
    return {
        "scores": {axis: scores[axis] for axis in sorted(QUALITY_AXES)},
        "average": average,
        "reasons": {axis: reasons[axis].strip() for axis in sorted(QUALITY_AXES)},
        "immediate_failures": list(failures),
    }


def _method_metadata(method_file: Path | str) -> tuple[dict[str, str], bytes]:
    path = Path(method_file).expanduser().resolve()
    try:
        content = path.read_bytes()
        text = content.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise JobStoreError(f"방법론 파일을 읽을 수 없음: {exc}") from exc
    match = re.search(r"방법론 버전: `([^`]+)`", text)
    if not match or not SEMVER_RE.fullmatch(match.group(1)):
        raise JobStoreError("방법론 파일에 유효한 의미적 버전이 없음")
    return {"version": match.group(1), "sha256": hashlib.sha256(content).hexdigest()}, content


def method_metadata(method_file: Path | str) -> tuple[dict[str, str], bytes]:
    """Return the declared method version, byte hash, and original bytes."""
    return _method_metadata(method_file)


def _read_json(path: Path | str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise JobStoreError(f"JSON을 읽을 수 없음: {path}: {exc}") from exc


def _read_nonempty_text(path: Path | str, label: str) -> str:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise JobStoreError(f"{label}을 읽을 수 없음: {exc}") from exc
    if not text.strip():
        raise JobStoreError(f"{label}이 비어 있음")
    return text


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _revision_payload(
    number: int,
    created_at: str,
    method: dict[str, str],
    content: dict[str, Any],
    quality_evaluation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    revision_name = _revision_name(number)
    payload = {
        "number": number,
        "created_at": created_at,
        "status": "DRAFT",
        "report_path": f"revisions/{revision_name}/report.md",
        "method_snapshot_path": f"revisions/{revision_name}/method-snapshot.md",
        "method": copy.deepcopy(method),
        "telegram": {
            "report_message_id": None,
            "report_sent_at": None,
            "last_update_id": None,
            "authorized_reply_ids": [],
        },
        "approval": {
            "decision": None,
            "decided_at": None,
            "approver_ids": [],
            "feedback": [],
        },
        "devotional_points": content["devotional_points"],
        "script": content["script"],
        "scenes": content["scenes"],
    }
    if quality_evaluation is not None:
        payload["quality_evaluation"] = copy.deepcopy(quality_evaluation)
    return payload


def revision_content_signature(revision: dict[str, Any]) -> str:
    """Hash approval-relevant content while excluding generation and delivery state."""
    scenes = [
        {
            field: scene.get(field)
            for field in (
                "scene_number",
                "source_text",
                "section",
                "setting",
                "description_ko",
                "prompt_en",
                "prompt_hash",
            )
        }
        for scene in revision.get("scenes", [])
    ]
    payload = {
        "devotional_points": revision.get("devotional_points"),
        "script": revision.get("script"),
        "scenes": scenes,
    }
    if "quality_evaluation" in revision:
        payload["quality_evaluation"] = revision["quality_evaluation"]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _write_revision_files(
    revision_root: Path, report_text: str, method_bytes: bytes
) -> None:
    revision_root.mkdir(parents=True, exist_ok=False)
    (revision_root / "images").mkdir()
    (revision_root / "report.md").write_text(report_text, encoding="utf-8")
    (revision_root / "method-snapshot.md").write_bytes(method_bytes)


def create_draft(
    project_root: Path | str,
    input_data: Any,
    content_data: Any,
    report_text: str,
    method_file: Path | str,
    *,
    job_id: str | None = None,
    now: datetime | None = None,
    quality_evaluation: Any = None,
) -> dict[str, Any]:
    normalized_input = normalize_input(input_data)
    normalized_content = normalize_content(content_data)
    normalized_quality = (
        normalize_quality_evaluation(quality_evaluation)
        if quality_evaluation is not None
        else None
    )
    if not isinstance(report_text, str) or not report_text.strip():
        raise JobStoreError("보고서가 비어 있음")
    method, method_bytes = _method_metadata(method_file)
    created_at = _iso(now)
    job_id = job_id or generate_job_id(now)
    _check_job_id(job_id)

    jobs_root = _jobs_root(project_root)
    jobs_root.mkdir(parents=True, exist_ok=True)
    destination = jobs_root / job_id
    if destination.exists():
        raise JobStoreError(f"이미 존재하는 작업 ID: {job_id}")

    temporary = Path(tempfile.mkdtemp(prefix=f".{job_id}.", dir=jobs_root))
    try:
        _write_revision_files(temporary / "revisions" / "r001", report_text, method_bytes)
        revision = _revision_payload(
            1, created_at, method, normalized_content, normalized_quality
        )
        job = {
            "schema_version": SCHEMA_VERSION,
            "job_id": job_id,
            "created_at": created_at,
            "updated_at": created_at,
            "current_revision": 1,
            "status": "DRAFT",
            "input": normalized_input,
            "method": method,
            "revisions": [revision],
            "events": [
                {
                    "at": created_at,
                    "type": "job_created",
                    "revision": 1,
                    "from": None,
                    "to": "DRAFT",
                    "idempotency_key": f"{job_id}:1:create",
                    "details": {},
                }
            ],
        }
        _atomic_write_json(temporary / "job.json", job)
        os.replace(temporary, destination)
        return job
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


def load_job(project_root: Path | str, job_id: str) -> dict[str, Any]:
    path = _job_root(project_root, job_id) / "job.json"
    data = _read_json(path)
    if not isinstance(data, dict):
        raise JobStoreError("job.json 최상위 값이 객체가 아님")
    return data


def _current_revision(job: dict[str, Any]) -> dict[str, Any]:
    number = job.get("current_revision")
    revisions = job.get("revisions")
    if not isinstance(number, int) or not isinstance(revisions, list):
        raise JobStoreError("현재 리비전 데이터가 잘못됨")
    for revision in revisions:
        if isinstance(revision, dict) and revision.get("number") == number:
            return revision
    raise JobStoreError("현재 리비전을 찾을 수 없음")


def current_revision(job: dict[str, Any]) -> dict[str, Any]:
    """Return the current revision from a loaded job."""
    return _current_revision(job)


def _event_with_key(job: dict[str, Any], idempotency_key: str | None) -> dict[str, Any] | None:
    if not idempotency_key:
        return None
    for event in job.get("events", []):
        if event.get("idempotency_key") == idempotency_key:
            return event
    return None


def _save_job(project_root: Path | str, job: dict[str, Any]) -> None:
    _atomic_write_json(_job_root(project_root, job["job_id"]) / "job.json", job)


def new_revision(
    project_root: Path | str,
    job_id: str,
    content_data: Any,
    report_text: str,
    method_file: Path | str,
    *,
    feedback: Iterable[str] = (),
    idempotency_key: str,
    now: datetime | None = None,
    quality_evaluation: Any = None,
) -> dict[str, Any]:
    job = load_job(project_root, job_id)
    if not idempotency_key:
        raise JobStoreError("새 리비전에는 멱등 키가 필요함")
    if job.get("status") == "GENERATING":
        raise JobStoreError("이미지 생성 중에는 새 리비전을 만들 수 없음")
    normalized_content = normalize_content(content_data)
    normalized_quality = (
        normalize_quality_evaluation(quality_evaluation)
        if quality_evaluation is not None
        else None
    )
    if not isinstance(report_text, str) or not report_text.strip():
        raise JobStoreError("보고서가 비어 있음")
    method, method_bytes = _method_metadata(method_file)
    feedback_items = list(feedback)
    if not all(isinstance(item, str) and item.strip() for item in feedback_items):
        raise JobStoreError("수정 피드백은 비어 있지 않은 문자열이어야 함")
    signature = {
        "content_sha256": hashlib.sha256(
            json.dumps(normalized_content, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest(),
        "report_sha256": hashlib.sha256(report_text.encode("utf-8")).hexdigest(),
        "method_sha256": method["sha256"],
        "feedback": feedback_items,
    }
    if normalized_quality is not None:
        signature["quality_sha256"] = hashlib.sha256(
            json.dumps(normalized_quality, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
    existing = _event_with_key(job, idempotency_key)
    if existing:
        if existing.get("type") != "revision_created" or existing.get("details") != signature:
            raise JobStoreError("같은 멱등 키가 다른 리비전 요청에 이미 사용됨")
        return job

    number = job["current_revision"] + 1
    revision_name = _revision_name(number)
    revisions_root = _job_root(project_root, job_id) / "revisions"
    destination = revisions_root / revision_name
    if destination.exists():
        raise JobStoreError(f"이미 존재하는 리비전 폴더: {revision_name}")
    temporary = Path(tempfile.mkdtemp(prefix=f".{revision_name}.", dir=revisions_root))
    created_at = _iso(now)
    previous_status = job["status"]
    updated = copy.deepcopy(job)
    updated["current_revision"] = number
    updated["status"] = "DRAFT"
    updated["updated_at"] = created_at
    updated["method"] = method
    new_payload = _revision_payload(
        number, created_at, method, normalized_content, normalized_quality
    )
    new_payload["telegram"]["last_update_id"] = _current_revision(job)["telegram"][
        "last_update_id"
    ]
    updated["revisions"].append(new_payload)
    updated["events"].append(
        {
            "at": created_at,
            "type": "revision_created",
            "revision": number,
            "from": previous_status,
            "to": "DRAFT",
            "idempotency_key": idempotency_key,
            "details": signature,
        }
    )

    try:
        (temporary / "images").mkdir()
        (temporary / "report.md").write_text(report_text, encoding="utf-8")
        (temporary / "method-snapshot.md").write_bytes(method_bytes)
        os.replace(temporary, destination)
        try:
            _save_job(project_root, updated)
        except Exception:
            shutil.rmtree(destination)
            raise
        return updated
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


def record_report_summary_sent(
    project_root: Path | str,
    job_id: str,
    summary_message_id: int,
    *,
    idempotency_key: str,
    sent_at: datetime | None = None,
) -> dict[str, Any]:
    """Checkpoint a sent summary before the report document is uploaded."""
    if (
        isinstance(summary_message_id, bool)
        or not isinstance(summary_message_id, int)
        or summary_message_id <= 0
    ):
        raise JobStoreError("요약 메시지 ID는 양의 정수여야 함")
    if not idempotency_key:
        raise JobStoreError("요약 전송에는 멱등 키가 필요함")
    job = load_job(project_root, job_id)
    if job["status"] not in {"DRAFT", "SENT_FOR_APPROVAL", "HOLD"}:
        raise JobStoreError(f"{job['status']} 상태에서는 보고 요약을 기록할 수 없음")
    details = {"summary_message_id": summary_message_id}
    existing = _event_with_key(job, idempotency_key)
    if existing:
        if existing.get("type") != "report_summary_sent" or existing.get("details") != details:
            raise JobStoreError("같은 멱등 키가 다른 요약 전송에 이미 사용됨")
        return job
    timestamp = _iso(sent_at)
    job["updated_at"] = timestamp
    job["events"].append(
        {
            "at": timestamp,
            "type": "report_summary_sent",
            "revision": job["current_revision"],
            "from": job["status"],
            "to": job["status"],
            "idempotency_key": idempotency_key,
            "details": details,
        }
    )
    _save_job(project_root, job)
    return job


def record_report_document_sent(
    project_root: Path | str,
    job_id: str,
    summary_message_id: int,
    document_message_id: int,
    *,
    idempotency_key: str,
    sent_at: datetime | None = None,
) -> dict[str, Any]:
    """Checkpoint the document upload before activating the approval message."""
    for value, label in (
        (summary_message_id, "요약 메시지 ID"),
        (document_message_id, "보고서 파일 메시지 ID"),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise JobStoreError(f"{label}는 양의 정수여야 함")
    if not idempotency_key:
        raise JobStoreError("보고서 파일 전송에는 멱등 키가 필요함")
    job = load_job(project_root, job_id)
    if job["status"] not in {"DRAFT", "SENT_FOR_APPROVAL", "HOLD"}:
        raise JobStoreError(f"{job['status']} 상태에서는 보고서 파일 전송을 기록할 수 없음")
    details = {
        "summary_message_id": summary_message_id,
        "document_message_id": document_message_id,
    }
    existing = _event_with_key(job, idempotency_key)
    if existing:
        if existing.get("type") != "report_document_sent" or existing.get("details") != details:
            raise JobStoreError("같은 멱등 키가 다른 보고서 파일 전송에 이미 사용됨")
        return job
    timestamp = _iso(sent_at)
    job["updated_at"] = timestamp
    job["events"].append(
        {
            "at": timestamp,
            "type": "report_document_sent",
            "revision": job["current_revision"],
            "from": job["status"],
            "to": job["status"],
            "idempotency_key": idempotency_key,
            "details": details,
        }
    )
    _save_job(project_root, job)
    return job


def record_report_delivery_failed(
    project_root: Path | str,
    job_id: str,
    summary_message_id: int,
    error: str,
    *,
    idempotency_key: str,
    failed_at: datetime | None = None,
) -> dict[str, Any]:
    """Record a failed report document upload without activating the summary."""
    if (
        isinstance(summary_message_id, bool)
        or not isinstance(summary_message_id, int)
        or summary_message_id <= 0
    ):
        raise JobStoreError("요약 메시지 ID는 양의 정수여야 함")
    if not isinstance(error, str) or not error.strip() or not idempotency_key:
        raise JobStoreError("보고 실패에는 오류와 멱등 키가 필요함")
    job = load_job(project_root, job_id)
    if job["status"] not in {"DRAFT", "SENT_FOR_APPROVAL", "HOLD"}:
        raise JobStoreError(f"{job['status']} 상태에서는 보고 실패를 기록할 수 없음")
    details = {"summary_message_id": summary_message_id, "error": error.strip()}
    existing = _event_with_key(job, idempotency_key)
    if existing:
        if existing.get("type") != "report_delivery_failed" or existing.get("details") != details:
            raise JobStoreError("같은 멱등 키가 다른 보고 실패에 이미 사용됨")
        return job
    timestamp = _iso(failed_at)
    job["updated_at"] = timestamp
    job["events"].append(
        {
            "at": timestamp,
            "type": "report_delivery_failed",
            "revision": job["current_revision"],
            "from": job["status"],
            "to": job["status"],
            "idempotency_key": idempotency_key,
            "details": details,
        }
    )
    _save_job(project_root, job)
    return job


def mark_report_sent(
    project_root: Path | str,
    job_id: str,
    report_message_id: int,
    *,
    report_sent_at: datetime | None = None,
    resend: bool = False,
    idempotency_key: str | None = None,
    report_document_message_id: int | None = None,
) -> dict[str, Any]:
    if (
        isinstance(report_message_id, bool)
        or not isinstance(report_message_id, int)
        or report_message_id <= 0
    ):
        raise JobStoreError("보고 메시지 ID는 양의 정수여야 함")
    if report_document_message_id is not None and (
        isinstance(report_document_message_id, bool)
        or not isinstance(report_document_message_id, int)
        or report_document_message_id <= 0
    ):
        raise JobStoreError("보고서 파일 메시지 ID는 정수 또는 null이어야 함")
    job = load_job(project_root, job_id)
    key = idempotency_key or (
        f"{job_id}:{job['current_revision']}:report:{report_message_id}"
        if resend
        else report_idempotency_key(job_id, job["current_revision"])
    )
    existing = _event_with_key(job, key)
    if existing:
        existing_details = existing.get("details", {})
        if existing_details.get("report_message_id") != report_message_id or (
            report_document_message_id is not None
            and existing_details.get("report_document_message_id") != report_document_message_id
        ):
            raise JobStoreError("같은 멱등 키가 다른 보고 메시지에 이미 사용됨")
        return job
    if job["status"] not in {"DRAFT", "SENT_FOR_APPROVAL", "HOLD"}:
        raise JobStoreError(f"{job['status']} 상태에서는 보고 성공을 기록할 수 없음")
    revision = _current_revision(job)
    if revision["telegram"]["report_message_id"] is not None and not resend:
        if revision["telegram"]["report_message_id"] == report_message_id:
            return job
        raise JobStoreError("기존 보고 메시지를 바꾸려면 명시적 재전송이 필요함")

    timestamp = _iso(report_sent_at)
    previous_state = job["status"]
    previous_message_id = revision["telegram"]["report_message_id"]
    revision["telegram"]["report_message_id"] = report_message_id
    revision["telegram"]["report_sent_at"] = timestamp
    revision["telegram"]["authorized_reply_ids"] = []
    revision["approval"] = {
        "decision": None,
        "decided_at": None,
        "approver_ids": [],
        "feedback": [],
    }
    revision["status"] = "SENT_FOR_APPROVAL"
    job["status"] = "SENT_FOR_APPROVAL"
    job["updated_at"] = timestamp
    job["events"].append(
        {
            "at": timestamp,
            "type": "report_resent" if previous_message_id is not None else "report_sent",
            "revision": job["current_revision"],
            "from": previous_state,
            "to": "SENT_FOR_APPROVAL",
            "idempotency_key": key,
            "details": {
                "report_message_id": report_message_id,
                "report_document_message_id": report_document_message_id,
                "previous_report_message_id": previous_message_id,
            },
        }
    )
    _save_job(project_root, job)
    return job


def record_reply_observations(
    project_root: Path | str,
    job_id: str,
    report_message_id: int,
    observations: Iterable[dict[str, Any]],
    *,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Persist validated and rejected Telegram updates without deciding meaning."""
    if (
        isinstance(report_message_id, bool)
        or not isinstance(report_message_id, int)
        or report_message_id <= 0
    ):
        raise JobStoreError("보고 메시지 ID는 양의 정수여야 함")
    items = list(observations)
    job = load_job(project_root, job_id)
    if job["status"] != "SENT_FOR_APPROVAL":
        raise JobStoreError("승인 대기 상태에서만 답장을 기록할 수 있음")
    revision = _current_revision(job)
    if revision["telegram"]["report_message_id"] != report_message_id:
        raise JobStoreError("현재 보고 메시지 ID와 답장 관찰 대상이 다름")

    required = {"update_id", "message_id", "user_id", "text", "date", "valid", "reason"}
    timestamp = _iso(observed_at)
    updated = False
    newest = revision["telegram"]["last_update_id"]
    authorized = set(revision["telegram"]["authorized_reply_ids"])
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict) or set(item) != required:
            raise JobStoreError(f"답장 관찰 {index}의 필드가 계약과 다름")
        update_id = item["update_id"]
        if isinstance(update_id, bool) or not isinstance(update_id, int) or update_id < 0:
            raise JobStoreError(f"답장 관찰 {index}의 update_id가 잘못됨")
        for field in ("message_id", "user_id", "date"):
            value = item[field]
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
                raise JobStoreError(f"답장 관찰 {index}의 {field}가 잘못됨")
            if value is not None and value < 0:
                raise JobStoreError(f"답장 관찰 {index}의 {field}는 음수일 수 없음")
        if not isinstance(item["text"], str):
            raise JobStoreError(f"답장 관찰 {index}의 text가 문자열이 아님")
        if not isinstance(item["valid"], bool):
            raise JobStoreError(f"답장 관찰 {index}의 valid가 boolean이 아님")
        if item["valid"]:
            if item["reason"] is not None:
                raise JobStoreError(f"유효 답장 관찰 {index}에 무효 사유가 있음")
            if (
                not isinstance(item["message_id"], int)
                or item["message_id"] <= 0
                or not isinstance(item["user_id"], int)
                or item["user_id"] <= 0
                or not item["text"].strip()
            ):
                raise JobStoreError(f"유효 답장 관찰 {index}의 필수 값이 없음")
        elif not isinstance(item["reason"], str) or not item["reason"].strip():
            raise JobStoreError(f"무효 답장 관찰 {index}에 사유가 없음")

        details = copy.deepcopy(item)
        details["report_message_id"] = report_message_id
        key = f"telegram:update:{update_id}"
        existing = _event_with_key(job, key)
        if existing:
            if existing.get("type") != "telegram_reply_observed" or existing.get("details") != details:
                raise JobStoreError("같은 Telegram update_id가 다른 답장으로 이미 기록됨")
        else:
            job["events"].append(
                {
                    "at": timestamp,
                    "type": "telegram_reply_observed",
                    "revision": job["current_revision"],
                    "from": "SENT_FOR_APPROVAL",
                    "to": "SENT_FOR_APPROVAL",
                    "idempotency_key": key,
                    "details": details,
                }
            )
            updated = True
        newest = update_id if newest is None else max(newest, update_id)
        if item["valid"]:
            authorized.add(item["message_id"])

    if newest != revision["telegram"]["last_update_id"]:
        revision["telegram"]["last_update_id"] = newest
        updated = True
    normalized_authorized = sorted(authorized)
    if normalized_authorized != revision["telegram"]["authorized_reply_ids"]:
        revision["telegram"]["authorized_reply_ids"] = normalized_authorized
        updated = True
    if updated:
        job["updated_at"] = timestamp
        _save_job(project_root, job)
    return job


def record_approval_expired(
    project_root: Path | str,
    job_id: str,
    report_message_id: int,
    *,
    expired_at: datetime | None = None,
) -> dict[str, Any]:
    """Record a 24-hour approval-window expiry without changing approval state."""
    if (
        isinstance(report_message_id, bool)
        or not isinstance(report_message_id, int)
        or report_message_id <= 0
    ):
        raise JobStoreError("보고 메시지 ID는 양의 정수여야 함")
    job = load_job(project_root, job_id)
    if job["status"] != "SENT_FOR_APPROVAL":
        raise JobStoreError("승인 대기 상태에서만 만료를 기록할 수 있음")
    revision = _current_revision(job)
    if revision["telegram"]["report_message_id"] != report_message_id:
        raise JobStoreError("현재 보고 메시지 ID와 만료 대상이 다름")
    key = f"{job_id}:{job['current_revision']}:approval-window-expired:{report_message_id}"
    if _event_with_key(job, key):
        return job
    timestamp = _iso(expired_at)
    job["updated_at"] = timestamp
    job["events"].append(
        {
            "at": timestamp,
            "type": "approval_window_expired",
            "revision": job["current_revision"],
            "from": "SENT_FOR_APPROVAL",
            "to": "SENT_FOR_APPROVAL",
            "idempotency_key": key,
            "details": {"report_message_id": report_message_id, "hours": 24},
        }
    )
    _save_job(project_root, job)
    return job


def record_decision(
    project_root: Path | str,
    job_id: str,
    decision: str,
    report_message_id: int,
    *,
    approver_ids: Iterable[int] = (),
    feedback: Iterable[str] = (),
    idempotency_key: str,
    decided_at: datetime | None = None,
) -> dict[str, Any]:
    if decision not in DECISION_TO_STATE:
        raise JobStoreError("승인 결정이 허용값이 아님")
    if (
        isinstance(report_message_id, bool)
        or not isinstance(report_message_id, int)
        or report_message_id <= 0
    ):
        raise JobStoreError("보고 메시지 ID는 양의 정수여야 함")
    if not idempotency_key:
        raise JobStoreError("승인 결정에는 멱등 키가 필요함")
    approvers = list(approver_ids)
    feedback_items = list(feedback)
    job = load_job(project_root, job_id)
    revision = _current_revision(job)
    content_signature = revision_content_signature(revision)
    existing = _event_with_key(job, idempotency_key)
    if existing:
        expected_details = {
            "decision": decision,
            "report_message_id": report_message_id,
            "approver_ids": sorted(set(approvers)),
            "feedback": feedback_items,
            "content_signature": content_signature,
        }
        if existing.get("type") != "approval_decision" or existing.get("details") != expected_details:
            raise JobStoreError("같은 멱등 키가 다른 승인 결정에 이미 사용됨")
        return job
    if job["status"] != "SENT_FOR_APPROVAL":
        raise JobStoreError("승인 대기 상태에서만 결정을 기록할 수 있음")
    if revision["telegram"]["report_message_id"] != report_message_id:
        raise JobStoreError("현재 보고 메시지 ID와 승인 대상이 다름")

    if not approvers or not all(
        isinstance(item, int) and not isinstance(item, bool) and item > 0 for item in approvers
    ):
        raise JobStoreError("하나 이상의 양의 정수 승인 담당자 ID가 필요함")
    if not all(isinstance(item, str) and item.strip() for item in feedback_items):
        raise JobStoreError("승인 피드백 항목이 잘못됨")

    timestamp = _iso(decided_at)
    target = DECISION_TO_STATE[decision]
    revision["approval"] = {
        "decision": decision,
        "decided_at": timestamp,
        "approver_ids": sorted(set(approvers)),
        "feedback": feedback_items,
    }
    revision["status"] = target
    job["status"] = target
    job["updated_at"] = timestamp
    job["events"].append(
        {
            "at": timestamp,
            "type": "approval_decision",
            "revision": job["current_revision"],
            "from": "SENT_FOR_APPROVAL",
            "to": target,
            "idempotency_key": idempotency_key,
            "details": {
                "decision": decision,
                "report_message_id": report_message_id,
                "approver_ids": sorted(set(approvers)),
                "feedback": feedback_items,
                "content_signature": content_signature,
            },
        }
    )
    _save_job(project_root, job)
    return job


def _assert_generation_approval(job: dict[str, Any]) -> None:
    revision = _current_revision(job)
    if revision.get("approval", {}).get("decision") != "approved":
        raise JobStoreError("현재 리비전의 명확한 승인 없이는 이미지를 생성할 수 없음")
    event = next(
        (
            item
            for item in reversed(job.get("events", []))
            if item.get("type") == "approval_decision"
            and item.get("revision") == job.get("current_revision")
            and item.get("details", {}).get("decision") == "approved"
        ),
        None,
    )
    if event is None:
        raise JobStoreError("현재 리비전의 승인 근거 이벤트가 없음")
    details = event.get("details", {})
    if details.get("report_message_id") != revision.get("telegram", {}).get("report_message_id"):
        raise JobStoreError("승인 보고 메시지와 현재 보고 메시지가 다름")
    if details.get("content_signature") != revision_content_signature(revision):
        raise JobStoreError("승인 이후 콘텐츠 서명이 변경됨")


def transition_generation(
    project_root: Path | str,
    job_id: str,
    target: str,
    *,
    reason: str,
    idempotency_key: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    if target not in STATES:
        raise JobStoreError("알 수 없는 대상 상태")
    if not reason.strip() or not idempotency_key:
        raise JobStoreError("상태 전이에는 사유와 멱등 키가 필요함")
    job = load_job(project_root, job_id)
    existing = _event_with_key(job, idempotency_key)
    if existing:
        if existing.get("to") != target:
            raise JobStoreError("같은 멱등 키가 다른 상태 전이에 이미 사용됨")
        return job
    current = job["status"]
    if (current, target) not in GENERATION_TRANSITIONS:
        raise JobStoreError(f"허용되지 않은 상태 전이: {current} -> {target}")
    if target == "GENERATING":
        _assert_generation_approval(job)
        errors = validate_job(project_root, job_id)
        if errors:
            raise JobStoreError("작업 무결성 검증 실패: " + "; ".join(errors))
    if target == "DELIVERED" and any(
        scene["status"] != "DELIVERED" for scene in _current_revision(job)["scenes"]
    ):
        raise JobStoreError("모든 장면이 전달되기 전에는 DELIVERED로 전이할 수 없음")

    timestamp = _iso(now)
    job["status"] = target
    job["updated_at"] = timestamp
    revision = _current_revision(job)
    revision["status"] = target
    job["events"].append(
        {
            "at": timestamp,
            "type": "state_transition",
            "revision": job["current_revision"],
            "from": current,
            "to": target,
            "idempotency_key": idempotency_key,
            "details": {"reason": reason},
        }
    )
    _save_job(project_root, job)
    return job


def record_scene_attempt(
    project_root: Path | str,
    job_id: str,
    scene_number: int,
    *,
    idempotency_key: str,
    attempted_at: datetime | None = None,
) -> dict[str, Any]:
    """Reserve one of the two allowed ImageGen attempts before calling the tool."""
    if isinstance(scene_number, bool) or not isinstance(scene_number, int) or scene_number < 1:
        raise JobStoreError("장면 번호는 1 이상 정수여야 함")
    if not idempotency_key:
        raise JobStoreError("이미지 생성 시도에는 멱등 키가 필요함")
    job = load_job(project_root, job_id)
    if job["status"] != "GENERATING":
        raise JobStoreError("GENERATING 상태에서만 이미지 생성을 시작할 수 있음")
    _assert_generation_approval(job)
    revision = _current_revision(job)
    if scene_number > len(revision["scenes"]):
        raise JobStoreError("존재하지 않는 장면 번호")
    scene = revision["scenes"][scene_number - 1]
    next_attempt = scene["attempts"] + 1
    details = {
        "scene_number": scene_number,
        "attempt": next_attempt,
        "prompt_hash": scene["prompt_hash"],
    }
    existing = _event_with_key(job, idempotency_key)
    if existing:
        if existing.get("type") != "scene_generation_started" or existing.get("details") != details:
            raise JobStoreError("같은 멱등 키가 다른 생성 시도에 이미 사용됨")
        return job
    if scene["status"] != "PENDING" or scene.get("local_path") is not None:
        raise JobStoreError("PENDING 장면만 이미지 생성을 시작할 수 있음")
    if next_attempt > 2:
        raise JobStoreError("장면당 이미지 생성은 두 번까지만 시도할 수 있음")
    timestamp = _iso(attempted_at)
    scene["attempts"] = next_attempt
    scene["error"] = None
    job["updated_at"] = timestamp
    job["events"].append(
        {
            "at": timestamp,
            "type": "scene_generation_started",
            "revision": job["current_revision"],
            "from": "PENDING",
            "to": "PENDING",
            "idempotency_key": idempotency_key,
            "details": details,
        }
    )
    _save_job(project_root, job)
    return job


def record_scene_generated(
    project_root: Path | str,
    job_id: str,
    scene_number: int,
    local_path: str,
    image_hash: str,
    *,
    idempotency_key: str,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Persist one validated local image without overwriting another scene result."""
    if isinstance(scene_number, bool) or not isinstance(scene_number, int) or scene_number < 1:
        raise JobStoreError("장면 번호는 1 이상 정수여야 함")
    _safe_relative_path(local_path, "이미지 경로")
    if not SHA256_RE.fullmatch(image_hash):
        raise JobStoreError("이미지 해시가 SHA-256 형식이 아님")
    if not idempotency_key:
        raise JobStoreError("이미지 생성 성공에는 멱등 키가 필요함")
    job = load_job(project_root, job_id)
    revision = _current_revision(job)
    if scene_number > len(revision["scenes"]):
        raise JobStoreError("존재하지 않는 장면 번호")
    scene = revision["scenes"][scene_number - 1]
    details = {
        "scene_number": scene_number,
        "prompt_hash": scene["prompt_hash"],
        "local_path": local_path,
        "image_hash": image_hash,
    }
    existing = _event_with_key(job, idempotency_key)
    if existing:
        if existing.get("type") != "scene_generated" or existing.get("details") != details:
            raise JobStoreError("같은 멱등 키가 다른 이미지 생성에 이미 사용됨")
        return job
    if job["status"] != "GENERATING":
        raise JobStoreError("GENERATING 상태에서만 이미지 생성을 기록할 수 있음")
    _assert_generation_approval(job)
    if scene["status"] == "GENERATED" and scene.get("local_path") == local_path and scene.get(
        "image_hash"
    ) == image_hash:
        return job
    if scene["status"] != "PENDING" or not 1 <= scene["attempts"] <= 2:
        raise JobStoreError("시작 기록이 있는 PENDING 장면만 생성 완료로 기록할 수 있음")
    image_path = Path(project_root).expanduser().resolve() / local_path
    metadata = inspect_image(image_path)
    if metadata["sha256"] != image_hash:
        raise JobStoreError("저장 이미지 해시가 기록값과 다름")
    expected_name = scene_filename(scene_number, metadata["extension"])
    if image_path.name != expected_name:
        raise JobStoreError("이미지 파일명이 장면 번호와 실제 형식에 맞지 않음")
    timestamp = _iso(generated_at)
    scene["status"] = "GENERATED"
    scene["local_path"] = local_path
    scene["image_hash"] = image_hash
    scene["telegram_message_id"] = None
    scene["error"] = None
    job["updated_at"] = timestamp
    job["events"].append(
        {
            "at": timestamp,
            "type": "scene_generated",
            "revision": job["current_revision"],
            "from": "PENDING",
            "to": "GENERATED",
            "idempotency_key": idempotency_key,
            "details": details,
        }
    )
    _save_job(project_root, job)
    return job


def record_scene_generation_failed(
    project_root: Path | str,
    job_id: str,
    scene_number: int,
    error: str,
    *,
    idempotency_key: str,
    failed_at: datetime | None = None,
) -> dict[str, Any]:
    """Record one failed attempt and stop the job after the second failure."""
    if isinstance(scene_number, bool) or not isinstance(scene_number, int) or scene_number < 1:
        raise JobStoreError("장면 번호는 1 이상 정수여야 함")
    if not isinstance(error, str) or not error.strip() or not idempotency_key:
        raise JobStoreError("이미지 생성 실패에는 오류와 멱등 키가 필요함")
    job = load_job(project_root, job_id)
    if job["status"] != "GENERATING":
        raise JobStoreError("GENERATING 상태에서만 이미지 생성 실패를 기록할 수 있음")
    _assert_generation_approval(job)
    revision = _current_revision(job)
    if scene_number > len(revision["scenes"]):
        raise JobStoreError("존재하지 않는 장면 번호")
    scene = revision["scenes"][scene_number - 1]
    if scene["status"] != "PENDING" or not 1 <= scene["attempts"] <= 2:
        raise JobStoreError("시작 기록이 있는 PENDING 장면만 실패로 기록할 수 있음")
    normalized_error = " ".join(error.split())[:500]
    final_failure = scene["attempts"] == 2
    details = {
        "scene_number": scene_number,
        "attempt": scene["attempts"],
        "prompt_hash": scene["prompt_hash"],
        "error": normalized_error,
        "final": final_failure,
    }
    existing = _event_with_key(job, idempotency_key)
    if existing:
        if existing.get("type") != "scene_generation_failed" or existing.get("details") != details:
            raise JobStoreError("같은 멱등 키가 다른 생성 실패에 이미 사용됨")
        return job
    timestamp = _iso(failed_at)
    scene["error"] = normalized_error
    scene["status"] = "FAILED" if final_failure else "PENDING"
    if final_failure:
        job["status"] = "NEEDS_REVIEW"
        revision["status"] = "NEEDS_REVIEW"
    job["updated_at"] = timestamp
    job["events"].append(
        {
            "at": timestamp,
            "type": "scene_generation_failed",
            "revision": job["current_revision"],
            "from": "PENDING",
            "to": "NEEDS_REVIEW" if final_failure else "PENDING",
            "idempotency_key": idempotency_key,
            "details": details,
        }
    )
    _save_job(project_root, job)
    return job


def resume_failed_generation(
    project_root: Path | str,
    job_id: str,
    *,
    idempotency_key: str,
    reason: str,
    resumed_at: datetime | None = None,
) -> dict[str, Any]:
    """Start a manual recovery cycle for FAILED scenes while preserving successes."""
    if not idempotency_key or not isinstance(reason, str) or not reason.strip():
        raise JobStoreError("실패 장면 재개에는 사유와 멱등 키가 필요함")
    job = load_job(project_root, job_id)
    existing = _event_with_key(job, idempotency_key)
    if existing:
        if existing.get("type") != "generation_resumed":
            raise JobStoreError("같은 멱등 키가 다른 작업에 이미 사용됨")
        return job
    if job["status"] != "NEEDS_REVIEW":
        raise JobStoreError("NEEDS_REVIEW 상태에서만 실패 장면을 재개할 수 있음")
    _assert_generation_approval(job)
    errors = validate_job(project_root, job_id)
    if errors:
        raise JobStoreError("작업 무결성 검증 실패: " + "; ".join(errors))
    revision = _current_revision(job)
    failed = [scene for scene in revision["scenes"] if scene["status"] == "FAILED"]
    if not failed:
        raise JobStoreError("재개할 FAILED 장면이 없음")
    numbers = [scene["scene_number"] for scene in failed]
    for scene in failed:
        scene["status"] = "PENDING"
        scene["attempts"] = 0
        scene["error"] = None
    timestamp = _iso(resumed_at)
    job["status"] = "GENERATING"
    revision["status"] = "GENERATING"
    job["updated_at"] = timestamp
    job["events"].append(
        {
            "at": timestamp,
            "type": "generation_resumed",
            "revision": job["current_revision"],
            "from": "NEEDS_REVIEW",
            "to": "GENERATING",
            "idempotency_key": idempotency_key,
            "details": {"scene_numbers": numbers, "reason": reason.strip()},
        }
    )
    _save_job(project_root, job)
    return job


def record_delivery_summary(
    project_root: Path | str,
    job_id: str,
    telegram_message_id: int,
    *,
    idempotency_key: str,
    delivered_count: int,
    failed_scene_numbers: Iterable[int],
    sent_at: datetime | None = None,
) -> dict[str, Any]:
    if (
        isinstance(telegram_message_id, bool)
        or not isinstance(telegram_message_id, int)
        or telegram_message_id <= 0
    ):
        raise JobStoreError("전달 요약 메시지 ID는 양의 정수여야 함")
    failed = list(failed_scene_numbers)
    if not idempotency_key or isinstance(delivered_count, bool) or delivered_count < 0:
        raise JobStoreError("전달 요약 입력이 잘못됨")
    job = load_job(project_root, job_id)
    if job["status"] not in {"GENERATING", "NEEDS_REVIEW"}:
        raise JobStoreError("생성 또는 검토 상태에서만 전달 요약을 기록할 수 있음")
    details = {
        "telegram_message_id": telegram_message_id,
        "delivered_count": delivered_count,
        "failed_scene_numbers": failed,
    }
    existing = _event_with_key(job, idempotency_key)
    if existing:
        if existing.get("type") != "delivery_summary_sent" or existing.get("details") != details:
            raise JobStoreError("같은 멱등 키가 다른 전달 요약에 이미 사용됨")
        return job
    timestamp = _iso(sent_at)
    job["updated_at"] = timestamp
    job["events"].append(
        {
            "at": timestamp,
            "type": "delivery_summary_sent",
            "revision": job["current_revision"],
            "from": job["status"],
            "to": job["status"],
            "idempotency_key": idempotency_key,
            "details": details,
        }
    )
    _save_job(project_root, job)
    return job


def record_scene_delivery(
    project_root: Path | str,
    job_id: str,
    scene_number: int,
    telegram_message_id: int,
    *,
    idempotency_key: str,
    delivered_at: datetime | None = None,
) -> dict[str, Any]:
    """Persist one successful Telegram image delivery without finalizing the job."""
    if isinstance(scene_number, bool) or not isinstance(scene_number, int) or scene_number < 1:
        raise JobStoreError("장면 번호는 1 이상 정수여야 함")
    if (
        isinstance(telegram_message_id, bool)
        or not isinstance(telegram_message_id, int)
        or telegram_message_id <= 0
    ):
        raise JobStoreError("이미지 메시지 ID는 양의 정수여야 함")
    if not idempotency_key:
        raise JobStoreError("이미지 전달에는 멱등 키가 필요함")
    job = load_job(project_root, job_id)
    if job["status"] not in {"GENERATING", "NEEDS_REVIEW"}:
        raise JobStoreError("GENERATING 또는 NEEDS_REVIEW 상태에서만 이미지 전달을 기록할 수 있음")
    revision = _current_revision(job)
    if scene_number > len(revision["scenes"]):
        raise JobStoreError("존재하지 않는 장면 번호")
    scene = revision["scenes"][scene_number - 1]
    details = {"scene_number": scene_number, "telegram_message_id": telegram_message_id}
    existing = _event_with_key(job, idempotency_key)
    if existing:
        if existing.get("type") != "scene_delivered" or existing.get("details") != details:
            raise JobStoreError("같은 멱등 키가 다른 이미지 전달에 이미 사용됨")
        return job
    if scene["status"] == "DELIVERED":
        if scene["telegram_message_id"] == telegram_message_id:
            return job
        raise JobStoreError("이미 전달된 장면의 메시지 ID를 바꿀 수 없음")
    if scene["status"] != "GENERATED" or not scene.get("local_path"):
        raise JobStoreError("로컬 생성이 완료된 장면만 전달할 수 있음")
    timestamp = _iso(delivered_at)
    scene["status"] = "DELIVERED"
    scene["telegram_message_id"] = telegram_message_id
    scene["error"] = None
    job["updated_at"] = timestamp
    job["events"].append(
        {
            "at": timestamp,
            "type": "scene_delivered",
            "revision": job["current_revision"],
            "from": "GENERATED",
            "to": "DELIVERED",
            "idempotency_key": idempotency_key,
            "details": details,
        }
    )
    _save_job(project_root, job)
    return job


def _safe_relative_path(value: Any, field: str) -> None:
    if not isinstance(value, str) or not value:
        raise JobStoreError(f"{field}은 상대경로 문자열이어야 함")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise JobStoreError(f"{field}은 작업 루트 내부 상대경로여야 함")


def validate_job(project_root: Path | str, job_id: str) -> list[str]:
    errors: list[str] = []
    try:
        job = load_job(project_root, job_id)
        root = _job_root(project_root, job_id)
        if job.get("schema_version") != SCHEMA_VERSION:
            errors.append("지원하지 않는 schema_version")
        if job.get("job_id") != job_id:
            errors.append("폴더명과 job_id 불일치")
        for field in ("created_at", "updated_at"):
            try:
                _validate_timestamp(job.get(field), field)
            except JobStoreError as exc:
                errors.append(str(exc))
        if job.get("status") not in STATES:
            errors.append("알 수 없는 현재 상태")
        try:
            if normalize_input(job.get("input")) != job.get("input"):
                errors.append("저장된 input이 정규화 계약과 다름")
        except JobStoreError as exc:
            errors.append(str(exc))
        method = job.get("method")
        if not isinstance(method, dict) or set(method) != {"version", "sha256"}:
            errors.append("작업 방법론 메타데이터가 잘못됨")
        elif not SEMVER_RE.fullmatch(str(method["version"])) or not SHA256_RE.fullmatch(
            str(method["sha256"])
        ):
            errors.append("작업 방법론 버전 또는 해시 형식이 잘못됨")
        revisions = job.get("revisions")
        if not isinstance(revisions, list) or not revisions:
            errors.append("리비전 배열이 비어 있음")
            return errors
        numbers = [revision.get("number") for revision in revisions if isinstance(revision, dict)]
        if numbers != list(range(1, len(revisions) + 1)):
            errors.append("리비전 번호가 1부터 연속되지 않음")
        if job.get("current_revision") != len(revisions):
            errors.append("현재 리비전이 마지막 리비전이 아님")
        current = _current_revision(job)
        if current.get("status") != job.get("status"):
            errors.append("작업 상태와 현재 리비전 상태 불일치")
        if current.get("method") != job.get("method"):
            errors.append("작업 방법론과 현재 리비전 방법론 불일치")

        revision_directories = sorted(
            path.name
            for path in (root / "revisions").iterdir()
            if path.is_dir() and re.fullmatch(r"r\d{3}", path.name)
        )
        expected_directories = [_revision_name(number) for number in range(1, len(revisions) + 1)]
        if revision_directories != expected_directories:
            errors.append("리비전 목록과 실제 폴더가 일치하지 않음")

        event_keys: set[str] = set()
        events = job.get("events")
        if not isinstance(events, list) or not events:
            errors.append("이벤트 배열이 비어 있음")
        else:
            for index, event in enumerate(events, 1):
                if not isinstance(event, dict):
                    errors.append(f"events[{index}]가 객체가 아님")
                    continue
                try:
                    _validate_timestamp(event.get("at"), f"events[{index}].at")
                except JobStoreError as exc:
                    errors.append(str(exc))
                key = event.get("idempotency_key")
                if not isinstance(key, str) or not key:
                    errors.append(f"events[{index}]에 멱등 키가 없음")
                elif key in event_keys:
                    errors.append(f"중복 이벤트 멱등 키: {key}")
                else:
                    event_keys.add(key)

        for revision in revisions:
            number = revision["number"]
            try:
                _validate_timestamp(revision.get("created_at"), f"revisions[{number}].created_at")
                _safe_relative_path(revision.get("report_path"), f"revisions[{number}].report_path")
                _safe_relative_path(
                    revision.get("method_snapshot_path"), f"revisions[{number}].method_snapshot_path"
                )
                report_path = root / revision["report_path"]
                method_path = root / revision["method_snapshot_path"]
                if not report_path.is_file() or not report_path.read_text(encoding="utf-8").strip():
                    errors.append(f"r{number:03d} 보고서 누락 또는 비어 있음")
                if not method_path.is_file():
                    errors.append(f"r{number:03d} 방법론 스냅샷 누락")
                else:
                    digest = hashlib.sha256(method_path.read_bytes()).hexdigest()
                    if digest != revision.get("method", {}).get("sha256"):
                        errors.append(f"r{number:03d} 방법론 해시 불일치")
                if revision.get("status") not in STATES:
                    errors.append(f"r{number:03d} 상태가 허용값이 아님")
                revision_method = revision.get("method")
                if not isinstance(revision_method, dict) or set(revision_method) != {"version", "sha256"}:
                    errors.append(f"r{number:03d} 방법론 메타데이터가 잘못됨")
                elif not SEMVER_RE.fullmatch(str(revision_method["version"])) or not SHA256_RE.fullmatch(
                    str(revision_method["sha256"])
                ):
                    errors.append(f"r{number:03d} 방법론 버전 또는 해시 형식이 잘못됨")

                if "quality_evaluation" in revision:
                    try:
                        normalized_quality = normalize_quality_evaluation(
                            revision["quality_evaluation"]
                        )
                        if normalized_quality != revision["quality_evaluation"]:
                            errors.append(f"r{number:03d} 의미 품질 평가가 정규화 계약과 다름")
                    except JobStoreError as exc:
                        errors.append(f"r{number:03d} 의미 품질 평가 오류: {exc}")

                telegram = revision.get("telegram")
                telegram_fields = {
                    "report_message_id",
                    "report_sent_at",
                    "last_update_id",
                    "authorized_reply_ids",
                }
                if not isinstance(telegram, dict) or set(telegram) != telegram_fields:
                    errors.append(f"r{number:03d} 텔레그램 메타데이터가 잘못됨")
                else:
                    for field in ("report_message_id", "last_update_id"):
                        value = telegram[field]
                        if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
                            errors.append(f"r{number:03d} telegram.{field}가 정수 또는 null이 아님")
                    if telegram["report_sent_at"] is not None:
                        try:
                            _validate_timestamp(
                                telegram["report_sent_at"], f"r{number:03d}.telegram.report_sent_at"
                            )
                        except JobStoreError as exc:
                            errors.append(str(exc))
                    if not isinstance(telegram["authorized_reply_ids"], list) or not all(
                        isinstance(item, int) and not isinstance(item, bool)
                        for item in telegram["authorized_reply_ids"]
                    ):
                        errors.append(f"r{number:03d} authorized_reply_ids가 정수 배열이 아님")

                approval = revision.get("approval")
                approval_fields = {"decision", "decided_at", "approver_ids", "feedback"}
                if not isinstance(approval, dict) or set(approval) != approval_fields:
                    errors.append(f"r{number:03d} 승인 메타데이터가 잘못됨")
                else:
                    if approval["decision"] is not None and approval["decision"] not in DECISION_TO_STATE:
                        errors.append(f"r{number:03d} 승인 결정이 허용값이 아님")
                    if approval["decided_at"] is not None:
                        try:
                            _validate_timestamp(
                                approval["decided_at"], f"r{number:03d}.approval.decided_at"
                            )
                        except JobStoreError as exc:
                            errors.append(str(exc))
                    if not isinstance(approval["approver_ids"], list) or not all(
                        isinstance(item, int) and not isinstance(item, bool) for item in approval["approver_ids"]
                    ):
                        errors.append(f"r{number:03d} approver_ids가 정수 배열이 아님")
                    if not isinstance(approval["feedback"], list) or not all(
                        isinstance(item, str) and item.strip() for item in approval["feedback"]
                    ):
                        errors.append(f"r{number:03d} feedback이 문자열 배열이 아님")
                    if approval["decision"] == "approved":
                        approval_event = next(
                            (
                                event
                                for event in reversed(events if isinstance(events, list) else [])
                                if isinstance(event, dict)
                                and event.get("type") == "approval_decision"
                                and event.get("revision") == number
                                and event.get("details", {}).get("decision") == "approved"
                            ),
                            None,
                        )
                        if approval_event is None:
                            errors.append(f"r{number:03d} 승인 근거 이벤트가 없음")
                        else:
                            details = approval_event.get("details", {})
                            current_report_id = (
                                telegram.get("report_message_id") if isinstance(telegram, dict) else None
                            )
                            if details.get("report_message_id") != current_report_id:
                                errors.append(f"r{number:03d} 승인 보고 메시지 ID 불일치")
                            if details.get("content_signature") != revision_content_signature(revision):
                                errors.append(f"r{number:03d} 승인 후 콘텐츠 서명 불일치")
                scenes = revision.get("scenes")
                if not isinstance(scenes, list) or not scenes:
                    errors.append(f"r{number:03d} 장면 배열이 비어 있음")
                    continue
                try:
                    stored_content = {
                        "devotional_points": revision.get("devotional_points"),
                        "script": revision.get("script"),
                        "scenes": [
                            {
                                field: scene.get(field)
                                for field in ("source_text", "section", "setting", "description_ko", "prompt_en")
                            }
                            for scene in scenes
                        ],
                    }
                    normalized_content = normalize_content(stored_content)
                    if normalized_content["devotional_points"] != revision.get("devotional_points"):
                        errors.append(f"r{number:03d} 묵상 포인트가 정규화 계약과 다름")
                    if normalized_content["script"] != revision.get("script"):
                        errors.append(f"r{number:03d} 스크립트가 정규화 계약과 다름")
                except JobStoreError as exc:
                    errors.append(f"r{number:03d} 콘텐츠 오류: {exc}")
                for expected, scene in enumerate(scenes, 1):
                    if scene.get("scene_number") != expected:
                        errors.append(f"r{number:03d} 장면 번호가 연속되지 않음")
                    prompt = scene.get("prompt_en")
                    expected_hash = (
                        hashlib.sha256(prompt.encode("utf-8")).hexdigest()
                        if isinstance(prompt, str)
                        else None
                    )
                    if scene.get("prompt_hash") != expected_hash:
                        errors.append(f"r{number:03d} scene-{expected:03d} 프롬프트 해시 불일치")
                    if scene.get("status") not in SCENE_STATES:
                        errors.append(f"r{number:03d} scene-{expected:03d} 상태가 허용값이 아님")
                    attempts = scene.get("attempts")
                    if isinstance(attempts, bool) or not isinstance(attempts, int) or not 0 <= attempts <= 2:
                        errors.append(f"r{number:03d} scene-{expected:03d} 시도 횟수가 잘못됨")
                    image_hash = scene.get("image_hash")
                    if image_hash is not None and not SHA256_RE.fullmatch(str(image_hash)):
                        errors.append(f"r{number:03d} scene-{expected:03d} 이미지 해시가 잘못됨")
                    telegram_message_id = scene.get("telegram_message_id")
                    if telegram_message_id is not None and (
                        isinstance(telegram_message_id, bool)
                        or not isinstance(telegram_message_id, int)
                        or telegram_message_id <= 0
                    ):
                        errors.append(f"r{number:03d} scene-{expected:03d} 텔레그램 메시지 ID가 잘못됨")
                    error = scene.get("error")
                    if error is not None and (not isinstance(error, str) or not error.strip()):
                        errors.append(f"r{number:03d} scene-{expected:03d} 오류가 잘못됨")
                    if scene.get("local_path") is not None:
                        try:
                            _safe_relative_path(
                                scene["local_path"], f"r{number:03d} scene-{expected:03d}.local_path"
                            )
                            image_path = Path(project_root).expanduser().resolve() / scene["local_path"]
                            metadata = inspect_image(image_path)
                            if metadata["sha256"] != image_hash:
                                errors.append(
                                    f"r{number:03d} scene-{expected:03d} 저장 이미지 해시 불일치"
                                )
                            if image_path.name != scene_filename(expected, metadata["extension"]):
                                errors.append(
                                    f"r{number:03d} scene-{expected:03d} 파일명과 실제 형식 불일치"
                                )
                        except JobStoreError as exc:
                            errors.append(str(exc))
                    status = scene.get("status")
                    if status in {"GENERATED", "DELIVERED"} and (
                        scene.get("local_path") is None or image_hash is None
                    ):
                        errors.append(f"r{number:03d} scene-{expected:03d} 생성 파일 정보가 없음")
                    if status == "DELIVERED" and telegram_message_id is None:
                        errors.append(f"r{number:03d} scene-{expected:03d} 전달 메시지 ID가 없음")
                    if status == "GENERATED" and telegram_message_id is not None:
                        errors.append(f"r{number:03d} scene-{expected:03d} 미전달 상태에 메시지 ID가 있음")
                    if status in {"PENDING", "FAILED"} and (
                        scene.get("local_path") is not None
                        or image_hash is not None
                        or telegram_message_id is not None
                    ):
                        errors.append(f"r{number:03d} scene-{expected:03d} 미생성 상태에 결과가 있음")
                    if status == "FAILED" and (attempts != 2 or error is None):
                        errors.append(f"r{number:03d} scene-{expected:03d} 실패 근거가 불완전함")
                if revision.get("status") == "DELIVERED" and any(
                    scene.get("status") != "DELIVERED" for scene in scenes
                ):
                    errors.append(f"r{number:03d} DELIVERED 상태에 미전달 장면이 있음")
                if revision.get("status") == "SENT_FOR_APPROVAL" and (
                    not isinstance(telegram, dict) or telegram.get("report_message_id") is None
                ):
                    errors.append(f"r{number:03d} 승인 대기 상태에 보고 메시지 ID가 없음")
                if revision.get("status") == "APPROVED" and (
                    not isinstance(approval, dict) or approval.get("decision") != "approved"
                ):
                    errors.append(f"r{number:03d} APPROVED 상태에 명확한 승인 결정이 없음")
            except (JobStoreError, OSError, UnicodeError) as exc:
                errors.append(str(exc))
    except (JobStoreError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        errors.append(str(exc))
    return errors


def _validation_marker(project_root: Path | str, job_id: str) -> Path:
    return _job_root(project_root, job_id) / "validation-error.json"


def mark_validation_failure(
    project_root: Path | str,
    job_id: str,
    errors: Iterable[str],
    *,
    now: datetime | None = None,
) -> None:
    job_root = _job_root(project_root, job_id)
    if not job_root.is_dir():
        raise JobStoreError(f"작업 폴더가 없음: {job_id}")
    error_list = [str(error) for error in errors if str(error).strip()]
    if not error_list:
        raise JobStoreError("기록할 검증 오류가 없음")
    timestamp = _iso(now)
    digest = hashlib.sha256(
        json.dumps(error_list, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    try:
        job = load_job(project_root, job_id)
        revision = _current_revision(job)
        event_key = f"validation:{digest}"
        if _event_with_key(job, event_key):
            return
        previous = job.get("status")
        job["status"] = "NEEDS_REVIEW"
        job["updated_at"] = timestamp
        revision["status"] = "NEEDS_REVIEW"
        job.setdefault("events", []).append(
            {
                "at": timestamp,
                "type": "validation_failed",
                "revision": job.get("current_revision"),
                "from": previous,
                "to": "NEEDS_REVIEW",
                "idempotency_key": event_key,
                "details": {"errors": error_list},
            }
        )
        _save_job(project_root, job)
    except (JobStoreError, OSError, UnicodeError, json.JSONDecodeError):
        marker = {
            "status": "NEEDS_REVIEW",
            "detected_at": timestamp,
            "errors": error_list,
            "job_json_preserved": True,
        }
        _atomic_write_json(_validation_marker(project_root, job_id), marker)


def validate_and_mark(project_root: Path | str, job_id: str) -> list[str]:
    errors = validate_job(project_root, job_id)
    marker = _validation_marker(project_root, job_id)
    if errors:
        mark_validation_failure(project_root, job_id, errors)
    elif marker.exists():
        marker.unlink()
    return errors


def status_summary(project_root: Path | str, job_id: str) -> dict[str, Any]:
    try:
        job = load_job(project_root, job_id)
    except JobStoreError:
        marker = _validation_marker(project_root, job_id)
        if marker.is_file():
            data = _read_json(marker)
            return {
                "job_id": job_id,
                "current_revision": None,
                "status": "NEEDS_REVIEW",
                "method": None,
                "report_sent_at": None,
                "report_message_id": None,
                "approval_decision": None,
                "scene_counts": {},
                "updated_at": data.get("detected_at"),
                "validation_errors": data.get("errors", []),
            }
        raise
    revision = _current_revision(job)
    counts = {state: 0 for state in SCENE_STATES}
    for scene in revision["scenes"]:
        counts[scene["status"]] = counts.get(scene["status"], 0) + 1
    return {
        "job_id": job_id,
        "current_revision": job["current_revision"],
        "status": job["status"],
        "method": job["method"],
        "report_sent_at": revision["telegram"]["report_sent_at"],
        "report_message_id": revision["telegram"]["report_message_id"],
        "approval_decision": revision["approval"]["decision"],
        "scene_counts": counts,
        "updated_at": job["updated_at"],
    }


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="묵상 쇼츠 작업·리비전·상태 저장 도구")
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create-draft", help="새 DRAFT 작업을 원자적으로 생성")
    create.add_argument("--project-root", required=True, type=Path)
    create.add_argument("--input-json", required=True, type=Path)
    create.add_argument("--content-json", required=True, type=Path)
    create.add_argument("--report", required=True, type=Path)
    create.add_argument("--method-file", required=True, type=Path)
    create.add_argument("--job-id")

    revise = commands.add_parser("new-revision", help="이전 결과를 보존한 새 DRAFT 생성")
    revise.add_argument("--project-root", required=True, type=Path)
    revise.add_argument("--job-id", required=True)
    revise.add_argument("--content-json", required=True, type=Path)
    revise.add_argument("--report", required=True, type=Path)
    revise.add_argument("--method-file", required=True, type=Path)
    revise.add_argument("--feedback", action="append", default=[])
    revise.add_argument("--idempotency-key", required=True)

    sent = commands.add_parser("mark-report-sent", help="실제 보고 성공 결과를 기록")
    sent.add_argument("--project-root", required=True, type=Path)
    sent.add_argument("--job-id", required=True)
    sent.add_argument("--report-message-id", required=True, type=int)
    sent.add_argument("--resend", action="store_true")
    sent.add_argument("--idempotency-key")

    decision = commands.add_parser("record-decision", help="현재 보고서의 집계된 승인 판단 기록")
    decision.add_argument("--project-root", required=True, type=Path)
    decision.add_argument("--job-id", required=True)
    decision.add_argument("--decision", required=True, choices=sorted(DECISION_TO_STATE))
    decision.add_argument("--report-message-id", required=True, type=int)
    decision.add_argument("--approver-id", required=True, action="append", type=int)
    decision.add_argument("--feedback", action="append", default=[])
    decision.add_argument("--idempotency-key", required=True)

    transition = commands.add_parser("transition", help="승인 후 이미지 상태 전이")
    transition.add_argument("--project-root", required=True, type=Path)
    transition.add_argument("--job-id", required=True)
    transition.add_argument("--to", required=True, choices=sorted(STATES))
    transition.add_argument("--reason", required=True)
    transition.add_argument("--idempotency-key", required=True)

    status = commands.add_parser("status", help="현재 작업 상태 요약")
    status.add_argument("--project-root", required=True, type=Path)
    status.add_argument("--job-id", required=True)

    validate = commands.add_parser("validate", help="작업 파일·스키마·해시 검증")
    validate.add_argument("--project-root", required=True, type=Path)
    validate.add_argument("--job-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "create-draft":
            job = create_draft(
                args.project_root,
                _read_json(args.input_json),
                _read_json(args.content_json),
                _read_nonempty_text(args.report, "보고서"),
                args.method_file,
                job_id=args.job_id,
            )
            _print_json(status_summary(args.project_root, job["job_id"]))
        elif args.command == "new-revision":
            job = new_revision(
                args.project_root,
                args.job_id,
                _read_json(args.content_json),
                _read_nonempty_text(args.report, "보고서"),
                args.method_file,
                feedback=args.feedback,
                idempotency_key=args.idempotency_key,
            )
            _print_json(status_summary(args.project_root, job["job_id"]))
        elif args.command == "mark-report-sent":
            mark_report_sent(
                args.project_root,
                args.job_id,
                args.report_message_id,
                resend=args.resend,
                idempotency_key=args.idempotency_key,
            )
            _print_json(status_summary(args.project_root, args.job_id))
        elif args.command == "record-decision":
            record_decision(
                args.project_root,
                args.job_id,
                args.decision,
                args.report_message_id,
                approver_ids=args.approver_id,
                feedback=args.feedback,
                idempotency_key=args.idempotency_key,
            )
            _print_json(status_summary(args.project_root, args.job_id))
        elif args.command == "transition":
            transition_generation(
                args.project_root,
                args.job_id,
                args.to,
                reason=args.reason,
                idempotency_key=args.idempotency_key,
            )
            _print_json(status_summary(args.project_root, args.job_id))
        elif args.command == "status":
            _print_json(status_summary(args.project_root, args.job_id))
        else:
            errors = validate_and_mark(args.project_root, args.job_id)
            if errors:
                for error in errors:
                    print(f"작업 검증 실패: {error}", file=sys.stderr)
                return 1
            print("작업 검증 통과")
        return 0
    except (JobStoreError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"작업 저장 오류: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
