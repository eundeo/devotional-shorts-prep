#!/usr/bin/env python3
"""Validate, segment, render, and persist devotional Shorts drafts."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

import image_workflow
import job_store


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_METHOD = PLUGIN_ROOT / "skills/devotional-shorts/references/method.md"
DEFAULT_TEMPLATE = PLUGIN_ROOT / "skills/devotional-shorts/templates/report.md"

GENERATION_FIELDS = {"content", "quality_evaluation", "verified_sources", "review_warnings"}
SOURCE_FIELDS = {"quote", "author", "source", "locator", "translated"}
QUALITY_EVALUATION_FIELDS = {"scores", "reasons", "immediate_failures"}
QUALITY_AXES = (
    "passage_fidelity",
    "interpretive_clarity",
    "evangelical_consistency",
    "spoken_naturalness",
    "application_specificity",
)
SCENE_INPUT_FIELDS = ("source_text", "section", "setting", "description_ko", "prompt_en")
SENTENCE_END_RE = re.compile(r"(?<=[.!?。！？])\s+")
COUNTED_DURATION_RE = re.compile(r"[A-Za-z0-9\u3131-\u318e\uac00-\ud7a3]")
YO_ENDING_RE = re.compile(r"요[.!?。！？]?(?:\s|$)")
VERSE_REFERENCE_RE = re.compile(r"(?:\d+\s*[:：]\s*\d+|\d+\s*절)")
PLACEHOLDER_RE = re.compile(r"{{[a-z0-9_]+}}")

SPOKEN_SECTIONS = (
    ("opening", "modern_application"),
    ("bridge", "interpretation"),
    ("context", "biblical"),
    ("passage_summary", "biblical"),
    ("interpretation_application", "interpretation"),
    ("conclusion", "modern_application"),
    ("prayer", "prayer"),
    ("cta", "cta"),
)

PRESERVABLE_SECTIONS = {
    "devotional_points.background",
    "devotional_points.interpretation",
    "devotional_points.core_sentence",
    "devotional_points.application",
    "devotional_points.application_questions",
    "script.core_sentence",
    "script.thumbnail",
    "script.opening",
    "script.bridge",
    "script.context",
    "script.passage_summary",
    "script.interpretation_application",
    "script.conclusion",
    "script.prayer",
    "script.cta",
}


class WorkflowError(ValueError):
    """A content-generation result that cannot be finalized safely."""


def read_json(path: Path | str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WorkflowError(f"JSON을 읽을 수 없음: {path}: {exc}") from exc


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkflowError(f"{field}은 비어 있지 않은 문자열이어야 함")
    return value


def _normalized_space(text: str) -> str:
    return " ".join(text.split())


def visible_length(text: str) -> int:
    """Count every non-whitespace character for the 20-character scene rule."""
    return sum(not character.isspace() for character in text)


def estimate_seconds(text: str) -> int:
    """Estimate speech time from Korean, English, and number characters."""
    counted = len(COUNTED_DURATION_RE.findall(text))
    return max(1, (counted + 2) // 5)


def build_full_text(script: dict[str, Any]) -> str:
    parts: list[str] = []
    for field, _section in SPOKEN_SECTIONS:
        value = script.get(field)
        if field == "opening":
            if not isinstance(value, dict):
                raise WorkflowError("script.opening은 선택 정보를 가진 객체여야 함")
            value = value.get("selected")
        if field == "context" and value == "":
            continue
        parts.append(_required_text(value, f"script.{field}").strip())
    return "\n\n".join(parts)


def _sentence_units(script: dict[str, Any]) -> list[tuple[str, str]]:
    units: list[tuple[str, str]] = []
    for field, section in SPOKEN_SECTIONS:
        value = script[field]["selected"] if field == "opening" else script[field]
        if field == "context" and not value:
            continue
        for sentence in SENTENCE_END_RE.split(_normalized_space(value)):
            if sentence:
                units.append((sentence, section))
    return units


def accumulate_sentence_units(units: Iterable[tuple[str, str]]) -> list[dict[str, str]]:
    """Accumulate complete sentences until each scene reaches 20 characters."""
    scenes: list[dict[str, str]] = []
    texts: list[str] = []
    weights: dict[str, int] = {}
    section_order: list[str] = []

    def flush() -> None:
        if not texts:
            return
        section = max(section_order, key=lambda item: weights[item])
        scenes.append({"source_text": " ".join(texts), "section": section})
        texts.clear()
        weights.clear()
        section_order.clear()

    for text, section in units:
        cleaned = _required_text(text, "장면 원문").strip()
        if section not in job_store.SCENE_SECTIONS:
            raise WorkflowError(f"허용되지 않은 장면 구분: {section}")
        texts.append(cleaned)
        if section not in weights:
            weights[section] = 0
            section_order.append(section)
        weights[section] += visible_length(cleaned)
        if visible_length(" ".join(texts)) >= 20:
            flush()
    flush()
    if not scenes:
        raise WorkflowError("낭독문에서 장면을 만들 수 없음")
    return scenes


def segment_script(script: dict[str, Any]) -> list[dict[str, str]]:
    return accumulate_sentence_units(_sentence_units(script))


def _ranked_texts(selection: Any, field: str) -> list[str]:
    if not isinstance(selection, dict):
        raise WorkflowError(f"script.{field}은 객체여야 함")
    candidates = selection.get("candidates")
    if not isinstance(candidates, list):
        raise WorkflowError(f"script.{field}.candidates는 배열이어야 함")
    try:
        ranked = sorted(enumerate(candidates), key=lambda item: (-item[1]["score"], item[0]))
        texts = [item["text"] for _index, item in ranked]
    except (KeyError, TypeError) as exc:
        raise WorkflowError(f"script.{field} 후보 형식이 잘못됨") from exc
    if selection.get("selected") != texts[0] or selection.get("alternatives") != texts[1:4]:
        raise WorkflowError(f"script.{field} 최종안과 대안이 후보 점수 순위와 다름")
    return texts


def _validate_line_limit(text: str, maximum: int, field: str) -> None:
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) > maximum:
        raise WorkflowError(f"{field}은 {maximum}줄 이하여야 함")


def _validate_sources(data: Any, full_text: str) -> list[dict[str, Any]]:
    if not isinstance(data, list):
        raise WorkflowError("verified_sources는 배열이어야 함")
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(data, 1):
        if not isinstance(item, dict) or set(item) != SOURCE_FIELDS:
            raise WorkflowError(f"verified_sources[{index}] 필드가 계약과 다름")
        source = {
            field: _required_text(item[field], f"verified_sources[{index}].{field}")
            for field in ("quote", "author", "source", "locator")
        }
        if not isinstance(item["translated"], bool):
            raise WorkflowError(f"verified_sources[{index}].translated는 boolean이어야 함")
        if _normalized_space(source["quote"]) not in _normalized_space(full_text):
            raise WorkflowError(f"verified_sources[{index}] 인용문이 실제 낭독문에 없음")
        source["translated"] = item["translated"]
        normalized.append(source)
    return normalized


def _validate_quality_evaluation(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or set(data) != QUALITY_EVALUATION_FIELDS:
        raise WorkflowError(
            "quality_evaluation 필드는 scores, reasons, immediate_failures여야 함"
        )
    scores = data["scores"]
    reasons = data["reasons"]
    failures = data["immediate_failures"]
    if not isinstance(scores, dict) or set(scores) != set(QUALITY_AXES):
        raise WorkflowError("의미 품질 점수 축이 계약과 다름")
    if not isinstance(reasons, dict) or set(reasons) != set(QUALITY_AXES):
        raise WorkflowError("의미 품질 근거 축이 계약과 다름")
    if any(type(scores[axis]) is not int or not 1 <= scores[axis] <= 5 for axis in QUALITY_AXES):
        raise WorkflowError("의미 품질 점수는 각 축의 1~5점 정수여야 함")
    if any(not isinstance(reasons[axis], str) or not reasons[axis].strip() for axis in QUALITY_AXES):
        raise WorkflowError("의미 품질 평가는 각 축의 근거를 포함해야 함")
    if not isinstance(failures, list) or not all(
        isinstance(item, str) and item.strip() for item in failures
    ):
        raise WorkflowError("immediate_failures는 문자열 배열이어야 함")
    if failures:
        raise WorkflowError("의미 품질 즉시 실패 조건이 남아 있음")
    average = sum(scores.values()) / len(QUALITY_AXES)
    if any(scores[axis] < 4 for axis in QUALITY_AXES) or average < 4.2:
        raise WorkflowError(
            f"의미 품질 기준 미달: 각 축 4점 이상, 평균 4.2점 이상 필요(현재 {average:.1f}점)"
        )
    return {
        "scores": {axis: scores[axis] for axis in QUALITY_AXES},
        "average": round(average, 1),
        "reasons": {axis: reasons[axis].strip() for axis in QUALITY_AXES},
        "immediate_failures": [],
    }


def _validate_scenes(scenes: Any, expected: list[dict[str, str]]) -> list[dict[str, str]]:
    if not isinstance(scenes, list) or len(scenes) != len(expected):
        raise WorkflowError(f"장면 수가 20자 분할 결과와 다름: 예상 {len(expected)}개")
    normalized: list[dict[str, str]] = []
    for number, (scene, boundary) in enumerate(zip(scenes, expected), 1):
        if not isinstance(scene, dict) or set(scene) != set(SCENE_INPUT_FIELDS):
            raise WorkflowError(f"scenes[{number}] 필드가 계약과 다름")
        if _normalized_space(str(scene["source_text"])) != boundary["source_text"]:
            raise WorkflowError(f"scenes[{number}].source_text가 20자 분할 결과와 다름")
        if scene["section"] != boundary["section"]:
            raise WorkflowError(f"scenes[{number}].section이 분할 결과와 다름")
        setting = scene["setting"]
        if setting not in job_store.SCENE_SETTINGS:
            raise WorkflowError(f"scenes[{number}].setting이 허용값이 아님")
        if boundary["section"] == "biblical" and setting != "biblical_era":
            raise WorkflowError(f"scenes[{number}] 성경 본문 장면은 biblical_era여야 함")
        if boundary["section"] in {"modern_application", "prayer", "cta"} and setting != "modern":
            raise WorkflowError(f"scenes[{number}] 현대 적용 장면은 modern이어야 함")
        prompt = _required_text(scene["prompt_en"], f"scenes[{number}].prompt_en")
        if not re.search(r"[A-Za-z]", prompt):
            raise WorkflowError(f"scenes[{number}].prompt_en에 영어 설명이 없음")
        normalized.append(
            {
                "source_text": boundary["source_text"],
                "section": boundary["section"],
                "setting": setting,
                "description_ko": _required_text(
                    scene["description_ko"], f"scenes[{number}].description_ko"
                ),
                "prompt_en": prompt,
            }
        )
    return normalized


def prepare_generation(input_data: Any, generation_data: Any) -> dict[str, Any]:
    """Return a normalized persistence package or raise before any job is created."""
    try:
        normalized_input = job_store.normalize_input(input_data)
    except job_store.JobStoreError as exc:
        raise WorkflowError(str(exc)) from exc
    if not isinstance(generation_data, dict) or set(generation_data) != GENERATION_FIELDS:
        raise WorkflowError(
            "생성 결과 필드는 content, quality_evaluation, verified_sources, review_warnings여야 함"
        )

    content = copy.deepcopy(generation_data["content"])
    if not isinstance(content, dict) or set(content) != {"devotional_points", "script", "scenes"}:
        raise WorkflowError("content 필드는 devotional_points, script, scenes여야 함")
    points = content.get("devotional_points")
    script = content.get("script")
    if not isinstance(points, dict) or not isinstance(script, dict):
        raise WorkflowError("묵상 포인트와 스크립트는 객체여야 함")
    if script.get("core_sentence") != points.get("core_sentence"):
        raise WorkflowError("묵상 포인트와 스크립트의 핵심 문장이 다름")

    _ranked_texts(script.get("opening"), "opening")
    _ranked_texts(script.get("thumbnail"), "thumbnail")
    script["full_text"] = build_full_text(script)
    script["estimated_seconds"] = estimate_seconds(script["full_text"])
    seconds = script["estimated_seconds"]
    if seconds > 150:
        raise WorkflowError(f"예상 낭독 시간이 {seconds}초로 150초를 초과함")
    if seconds <= 120:
        if script.get("duration_exception"):
            raise WorkflowError("120초 이하 원고에는 분량 예외를 사용할 수 없음")
        script["duration_exception"] = False
        script["duration_exception_reason"] = None
    elif not script.get("duration_exception") or not str(
        script.get("duration_exception_reason") or ""
    ).strip():
        raise WorkflowError("121~150초 원고에는 분량 예외와 사유가 필요함")

    if YO_ENDING_RE.search(script["full_text"]):
        raise WorkflowError("낭독문에 금지된 요체 종결이 있음")
    if normalized_input["passage_reference"] is None and VERSE_REFERENCE_RE.search(
        script["full_text"]
    ):
        raise WorkflowError("본문 위치 미확인 상태에서 장절을 만들어 사용할 수 없음")
    if script.get("cta") != job_store.FIXED_CTA or not script["full_text"].endswith(
        job_store.FIXED_CTA
    ):
        raise WorkflowError("고정 CTA가 다르거나 낭독문 마지막에 없음")
    _validate_line_limit(script["opening"]["selected"], 2, "선택 오프닝")
    if script.get("context"):
        _validate_line_limit(script["context"], 2, "본문 배경")
    _validate_line_limit(script.get("prayer", ""), 3, "기도")

    expected_scenes = segment_script(script)
    content["scenes"] = _validate_scenes(content.get("scenes"), expected_scenes)
    try:
        normalized_content = job_store.normalize_content(content)
    except job_store.JobStoreError as exc:
        raise WorkflowError(str(exc)) from exc
    raw_content = {
        "devotional_points": normalized_content["devotional_points"],
        "script": normalized_content["script"],
        "scenes": [
            {field: scene[field] for field in SCENE_INPUT_FIELDS}
            for scene in normalized_content["scenes"]
        ],
    }

    sources = _validate_sources(generation_data["verified_sources"], script["full_text"])
    quality_evaluation = _validate_quality_evaluation(generation_data["quality_evaluation"])
    warnings = generation_data["review_warnings"]
    if not isinstance(warnings, list) or not all(
        isinstance(item, str) and item.strip() for item in warnings
    ):
        raise WorkflowError("review_warnings는 비어 있지 않은 문자열 배열이어야 함")
    normalized_warnings = list(dict.fromkeys(item.strip() for item in warnings))
    if normalized_input["passage_reference"] is None:
        normalized_warnings.append("본문 위치 미확인: 장절을 추측하지 않고 작성함")
    if script["duration_exception"]:
        normalized_warnings.append(
            f"분량 예외: {script['duration_exception_reason']} ({seconds}초)"
        )
    return {
        "input": normalized_input,
        "content": raw_content,
        "verified_sources": sources,
        "quality_evaluation": quality_evaluation,
        "review_warnings": list(dict.fromkeys(normalized_warnings)),
        "self_check_result": "통과",
    }


def _bullet_lines(items: Iterable[str]) -> str:
    values = list(items)
    return "\n".join(f"- {item}" for item in values) if values else "없음"


def _scene_markdown(scenes: list[dict[str, str]]) -> str:
    blocks: list[str] = []
    for number, scene in enumerate(scenes, 1):
        scene_title = _normalized_space(scene["description_ko"]).strip(" #")
        setting = "성경 시대" if scene["setting"] == "biblical_era" else "현대 적용"
        blocks.append(
            "\n".join(
                (
                    f"### SCENE {number:02d} — {scene_title}",
                    "",
                    f"- 장면 구분: {setting}",
                    f"- 사용 문장: “{scene['source_text']}”",
                    f"- 한국어 시각 설명: {scene['description_ko']}",
                    f"- English prompt: {scene['prompt_en']}",
                )
            )
        )
    return "\n\n".join(blocks)


def _source_markdown(sources: list[dict[str, Any]]) -> str:
    if not sources:
        return "없음"
    lines: list[str] = []
    for item in sources:
        translated = " · 번역 인용" if item["translated"] else ""
        lines.append(
            f"- “{item['quote']}” — {item['author']}, {item['source']}, "
            f"{item['locator']}{translated}"
        )
    return "\n".join(lines)


def _script_passage_summary(script: dict[str, Any]) -> str:
    fields = ("bridge", "context", "passage_summary")
    return "\n\n".join(script[field].strip() for field in fields if script.get(field))


def _visual_style_markdown(input_data: dict[str, Any]) -> str:
    overrides = input_data.get("visual_overrides") or {}
    lines = [
        "- 공통: 중간 밝기, 따뜻하고 균형 잡힌 색감, 영화적 질감, 세로 9:16, 상하 자막 안전 여백, 이미지 내 글자·워터마크 금지",
        "- 성경 시대: 본문 시대에 맞는 사실적인 고대 근동 배경과 고증",
        "- 현대 적용: 현대 한국 배경과 자연스러운 연령대·성별 순환",
    ]
    if overrides.get("style"):
        lines.append(f"- 지정 화풍: {overrides['style']}")
    if overrides.get("person"):
        lines.append(f"- 지정 인물: {overrides['person']}")
    if overrides.get("scene_instructions"):
        numbers = ", ".join(sorted(overrides["scene_instructions"], key=int))
        lines.append(f"- 장면별 특별 지시 적용: SCENE {numbers}")
    return "\n".join(lines)


def render_report(
    package: dict[str, Any],
    job_id: str,
    revision: int,
    method: dict[str, str],
    template_file: Path | str = DEFAULT_TEMPLATE,
) -> str:
    try:
        template = Path(template_file).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise WorkflowError(f"보고서 템플릿을 읽을 수 없음: {exc}") from exc
    content = package["content"]
    points = content["devotional_points"]
    script = content["script"]
    input_data = package["input"]
    report_title = (
        input_data["title"]
        or input_data["passage_reference"]
        or script["thumbnail"]["selected"]
    )
    quality = package["quality_evaluation"]
    replacements = {
        "job_id": job_id,
        "revision": f"r{revision:03d}",
        "report_title": report_title,
        "passage_reference_or_unconfirmed": input_data["passage_reference"] or "본문 위치 미확인",
        "core_sentence": points["core_sentence"],
        "selected_opening": script["opening"]["selected"],
        "selected_thumbnail": script["thumbnail"]["selected"],
        "estimated_seconds": str(script["estimated_seconds"]),
        "duration_summary": (
            f"2분 30초 예외 · {script['duration_exception_reason']}"
            if script["duration_exception"]
            else "기본 2분 범위"
        ),
        "scene_count": str(len(content["scenes"])),
        "self_check_result": package["self_check_result"],
        "quality_summary": (
            f"통과 · 평균 {quality['average']:.1f}/5 · 각 축 4점 이상 · 즉시 실패 없음"
        ),
        "script_opening": script["opening"]["selected"],
        "script_passage_summary": _script_passage_summary(script),
        "script_interpretation_application": script["interpretation_application"],
        "script_conclusion": script["conclusion"],
        "script_prayer": script["prayer"],
        "script_cta": script["cta"],
        "visual_style_summary": _visual_style_markdown(input_data),
        "opening_alternatives": _bullet_lines(script["opening"]["alternatives"]),
        "thumbnail_alternatives": _bullet_lines(script["thumbnail"]["alternatives"]),
        "image_scenes": _scene_markdown(content["scenes"]),
        "verified_sources_or_none": _source_markdown(package["verified_sources"]),
        "review_warnings_or_none": _bullet_lines(package["review_warnings"]),
    }
    report = template
    for field, value in replacements.items():
        report = report.replace("{{" + field + "}}", value)
    unresolved = PLACEHOLDER_RE.findall(report)
    if unresolved:
        raise WorkflowError(f"보고서 템플릿 미해결 필드: {', '.join(sorted(set(unresolved)))}")
    return report.rstrip() + "\n"


def _method_metadata(method_file: Path | str) -> dict[str, str]:
    try:
        metadata, _content = job_store.method_metadata(method_file)
    except job_store.JobStoreError as exc:
        raise WorkflowError(str(exc)) from exc
    return metadata


def _resolve_visuals(package: dict[str, Any], job_id: str) -> None:
    try:
        package["content"]["scenes"] = image_workflow.resolve_visual_prompts(
            package["input"], package["content"]["scenes"], job_id
        )
        normalized = job_store.normalize_content(package["content"])
    except (image_workflow.ImageWorkflowError, job_store.JobStoreError) as exc:
        raise WorkflowError(str(exc)) from exc
    package["content"] = {
        "devotional_points": normalized["devotional_points"],
        "script": normalized["script"],
        "scenes": [
            {field: scene[field] for field in SCENE_INPUT_FIELDS}
            for scene in normalized["scenes"]
        ],
    }


def create_draft(
    project_root: Path | str,
    input_data: Any,
    generation_data: Any,
    *,
    method_file: Path | str = DEFAULT_METHOD,
    template_file: Path | str = DEFAULT_TEMPLATE,
    job_id: str | None = None,
) -> dict[str, Any]:
    package = prepare_generation(input_data, generation_data)
    resolved_job_id = job_id or job_store.generate_job_id()
    _resolve_visuals(package, resolved_job_id)
    method = _method_metadata(method_file)
    report = render_report(package, resolved_job_id, 1, method, template_file)
    try:
        job = job_store.create_draft(
            project_root,
            package["input"],
            package["content"],
            report,
            method_file,
            job_id=resolved_job_id,
            quality_evaluation=package["quality_evaluation"],
        )
        errors = job_store.validate_job(project_root, resolved_job_id)
    except job_store.JobStoreError as exc:
        raise WorkflowError(str(exc)) from exc
    if errors:
        job_store.validate_and_mark(project_root, resolved_job_id)
        raise WorkflowError("저장 후 검증 실패: " + "; ".join(errors))
    return job


def _raw_revision_content(revision: dict[str, Any]) -> dict[str, Any]:
    return {
        "devotional_points": revision["devotional_points"],
        "script": revision["script"],
        "scenes": [
            {field: scene[field] for field in SCENE_INPUT_FIELDS}
            for scene in revision["scenes"]
        ],
    }


def _path_value(content: dict[str, Any], path: str) -> Any:
    parent, child = path.split(".", 1)
    return content[parent][child]


def create_revision(
    project_root: Path | str,
    job_id: str,
    generation_data: Any,
    *,
    feedback: Iterable[str],
    idempotency_key: str,
    preserve_sections: Iterable[str] = (),
    full_regeneration: bool = False,
    method_file: Path | str = DEFAULT_METHOD,
    template_file: Path | str = DEFAULT_TEMPLATE,
) -> dict[str, Any]:
    try:
        previous_job = job_store.load_job(project_root, job_id)
    except job_store.JobStoreError as exc:
        raise WorkflowError(str(exc)) from exc
    feedback_items = list(feedback)
    if not feedback_items or not all(isinstance(item, str) and item.strip() for item in feedback_items):
        raise WorkflowError("하나 이상의 비어 있지 않은 수정 의견이 필요함")
    preserved = list(dict.fromkeys(preserve_sections))
    if full_regeneration == bool(preserved):
        raise WorkflowError("보존 섹션 또는 전체 재생성 중 하나를 명시해야 함")
    unknown = set(preserved) - PRESERVABLE_SECTIONS
    if unknown:
        raise WorkflowError("알 수 없는 보존 섹션: " + ", ".join(sorted(unknown)))

    package = prepare_generation(previous_job["input"], generation_data)
    _resolve_visuals(package, job_id)
    existing_event = next(
        (
            event
            for event in previous_job.get("events", [])
            if event.get("idempotency_key") == idempotency_key
        ),
        None,
    )
    if existing_event is not None and existing_event.get("type") != "revision_created":
        raise WorkflowError("같은 멱등 키가 다른 작업에 이미 사용됨")
    if existing_event is not None:
        revision_number = existing_event.get("revision")
        if not isinstance(revision_number, int) or revision_number < 2:
            raise WorkflowError("기존 리비전 이벤트의 번호가 잘못됨")
        previous_revision = previous_job["revisions"][revision_number - 2]
    else:
        revision_number = previous_job["current_revision"] + 1
        previous_revision = previous_job["revisions"][previous_job["current_revision"] - 1]
    previous_content = _raw_revision_content(previous_revision)
    for path in preserved:
        if _path_value(package["content"], path) != _path_value(previous_content, path):
            raise WorkflowError(f"보존 대상으로 지정한 섹션이 변경됨: {path}")

    changed = sorted(
        path
        for path in PRESERVABLE_SECTIONS
        if _path_value(package["content"], path) != _path_value(previous_content, path)
    )
    package["review_warnings"].append(
        "수정 리비전 변경 범위: " + (", ".join(changed) if changed else "계산 필드와 장면만 변경")
    )
    method = _method_metadata(method_file)
    report = render_report(package, job_id, revision_number, method, template_file)
    if existing_event is not None:
        normalized = job_store.normalize_content(package["content"])
        signature = {
            "content_sha256": hashlib.sha256(
                json.dumps(normalized, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest(),
            "report_sha256": hashlib.sha256(report.encode("utf-8")).hexdigest(),
            "method_sha256": method["sha256"],
            "feedback": feedback_items,
            "quality_sha256": hashlib.sha256(
                json.dumps(
                    job_store.normalize_quality_evaluation(package["quality_evaluation"]),
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest(),
        }
        if existing_event.get("details") != signature:
            raise WorkflowError("같은 멱등 키가 다른 수정 요청에 이미 사용됨")
        return previous_job
    try:
        job = job_store.new_revision(
            project_root,
            job_id,
            package["content"],
            report,
            method_file,
            feedback=feedback_items,
            idempotency_key=idempotency_key,
            quality_evaluation=package["quality_evaluation"],
        )
        errors = job_store.validate_job(project_root, job_id)
    except job_store.JobStoreError as exc:
        raise WorkflowError(str(exc)) from exc
    if errors:
        job_store.validate_and_mark(project_root, job_id)
        raise WorkflowError("저장 후 검증 실패: " + "; ".join(errors))
    return job


def result_summary(job: dict[str, Any]) -> dict[str, Any]:
    revision = job["revisions"][job["current_revision"] - 1]
    return {
        "job_id": job["job_id"],
        "revision": revision["number"],
        "status": job["status"],
        "estimated_seconds": revision["script"]["estimated_seconds"],
        "scene_count": len(revision["scenes"]),
        "report_path": revision["report_path"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="묵상 쇼츠 콘텐츠 검수·장면 분할·보고서 저장")
    commands = parser.add_subparsers(dest="command", required=True)

    segment = commands.add_parser("segment", help="스크립트를 20자 누적 장면으로 분할")
    segment.add_argument("--script-json", required=True)

    check = commands.add_parser("check", help="저장 없이 생성 결과 자체 검수")
    check.add_argument("--input-json", required=True)
    check.add_argument("--generation-json", required=True)

    draft = commands.add_parser("create-draft", help="검수된 DRAFT와 보고서 생성")
    draft.add_argument("--project-root", default=".")
    draft.add_argument("--input-json", required=True)
    draft.add_argument("--generation-json", required=True)
    draft.add_argument("--job-id")
    draft.add_argument("--method-file", default=str(DEFAULT_METHOD))
    draft.add_argument("--template-file", default=str(DEFAULT_TEMPLATE))

    revise = commands.add_parser("create-revision", help="검수된 새 리비전과 보고서 생성")
    revise.add_argument("--project-root", default=".")
    revise.add_argument("--job-id", required=True)
    revise.add_argument("--generation-json", required=True)
    revise.add_argument("--feedback", action="append", required=True)
    revise.add_argument("--idempotency-key", required=True)
    choice = revise.add_mutually_exclusive_group(required=True)
    choice.add_argument("--preserve-section", action="append", choices=sorted(PRESERVABLE_SECTIONS))
    choice.add_argument("--full-regeneration", action="store_true")
    revise.add_argument("--method-file", default=str(DEFAULT_METHOD))
    revise.add_argument("--template-file", default=str(DEFAULT_TEMPLATE))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "segment":
            output = segment_script(read_json(args.script_json))
        elif args.command == "check":
            package = prepare_generation(read_json(args.input_json), read_json(args.generation_json))
            output = {
                "self_check_result": package["self_check_result"],
                "quality_evaluation": package["quality_evaluation"],
                "estimated_seconds": package["content"]["script"]["estimated_seconds"],
                "scene_count": len(package["content"]["scenes"]),
                "review_warnings": package["review_warnings"],
            }
        elif args.command == "create-draft":
            output = result_summary(
                create_draft(
                    args.project_root,
                    read_json(args.input_json),
                    read_json(args.generation_json),
                    method_file=args.method_file,
                    template_file=args.template_file,
                    job_id=args.job_id,
                )
            )
        else:
            output = result_summary(
                create_revision(
                    args.project_root,
                    args.job_id,
                    read_json(args.generation_json),
                    feedback=args.feedback,
                    idempotency_key=args.idempotency_key,
                    preserve_sections=args.preserve_section or (),
                    full_regeneration=args.full_regeneration,
                    method_file=args.method_file,
                    template_file=args.template_file,
                )
            )
    except (WorkflowError, OSError) as exc:
        print(f"콘텐츠 워크플로 실패: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
