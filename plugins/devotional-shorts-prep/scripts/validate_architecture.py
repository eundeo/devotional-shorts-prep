#!/usr/bin/env python3
"""Validate the plugin's deployable/runtime boundaries with the standard library."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PLUGIN_ROOT / "skills" / "devotional-shorts"

REQUIRED_FILES = (
    PLUGIN_ROOT / ".codex-plugin" / "plugin.json",
    PLUGIN_ROOT / ".env.example",
    SKILL_ROOT / "SKILL.md",
    SKILL_ROOT / "references" / "method.md",
    SKILL_ROOT / "references" / "examples.md",
    SKILL_ROOT / "references" / "system-boundaries.md",
    SKILL_ROOT / "references" / "generation-contract.md",
    SKILL_ROOT / "references" / "quality-evaluation.md",
    SKILL_ROOT / "references" / "telegram-contract.md",
    SKILL_ROOT / "references" / "image-contract.md",
    SKILL_ROOT / "templates" / "report.md",
    PLUGIN_ROOT / "scripts" / "content_workflow.py",
    PLUGIN_ROOT / "scripts" / "telegram_bot.py",
    PLUGIN_ROOT / "scripts" / "approval_workflow.py",
    PLUGIN_ROOT / "scripts" / "image_workflow.py",
    PLUGIN_ROOT / "scripts" / "job_store.py",
    PLUGIN_ROOT / "scripts" / "distribution.py",
)

ENV_KEYS = (
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "TELEGRAM_APPROVER_IDS",
)

REPORT_FIELDS = (
    "report_title",
    "job_id",
    "revision",
    "passage_reference_or_unconfirmed",
    "core_sentence",
    "selected_opening",
    "selected_thumbnail",
    "script_opening",
    "script_passage_summary",
    "script_interpretation_application",
    "script_conclusion",
    "script_prayer",
    "script_cta",
    "visual_style_summary",
    "image_scenes",
    "opening_alternatives",
    "thumbnail_alternatives",
    "review_warnings_or_none",
    "self_check_result",
    "quality_summary",
    "duration_summary",
)


def validate() -> tuple[list[str], dict[str, str]]:
    errors: list[str] = []
    for path in REQUIRED_FILES:
        if not path.is_file():
            errors.append(f"필수 파일 없음: {path.relative_to(PLUGIN_ROOT)}")

    if errors:
        return errors, {}

    manifest = json.loads(REQUIRED_FILES[0].read_text(encoding="utf-8"))
    if manifest.get("name") != "devotional-shorts-prep":
        errors.append("플러그인 매니페스트 name이 devotional-shorts-prep이 아님")
    if manifest.get("skills") != "./skills/":
        errors.append("매니페스트 skills 경계가 ./skills/가 아님")
    if "apps" in manifest or "mcpServers" in manifest:
        errors.append("구현되지 않은 app 또는 MCP 경계가 매니페스트에 선언됨")
    if not re.fullmatch(
        r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
        r"(?:\+codex\.[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?",
        str(manifest.get("version", "")),
    ):
        errors.append("플러그인 버전이 X.Y.Z 또는 Codex 캐시버스터 형식이 아님")

    env_values: dict[str, str] = {}
    for line in (PLUGIN_ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            key, separator, value = line.partition("=")
            if not separator:
                errors.append(f"잘못된 환경변수 예시: {line}")
            else:
                env_values[key] = value
    if tuple(env_values) != ENV_KEYS:
        errors.append("환경변수 예시의 키 또는 순서가 계약과 다름")
    if any(env_values.values()):
        errors.append("환경변수 예시에 실제 값이 포함됨")

    skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    if "references/system-boundaries.md" not in skill_text:
        errors.append("스킬 진입점이 시스템 경계를 읽도록 지시하지 않음")
    if "templates/report.md" not in skill_text:
        errors.append("스킬 진입점이 보고서 템플릿을 사용하지 않음")
    if "references/generation-contract.md" not in skill_text or "content_workflow.py" not in skill_text:
        errors.append("스킬 진입점이 콘텐츠 생성·저장 계약을 사용하지 않음")
    if "references/quality-evaluation.md" not in skill_text:
        errors.append("스킬 진입점이 의미 품질 평가 계약을 읽도록 지시하지 않음")
    if "references/telegram-contract.md" not in skill_text or "approval_workflow.py" not in skill_text:
        errors.append("스킬 진입점이 Telegram 승인 계약을 사용하지 않음")
    if "references/image-contract.md" not in skill_text or "image_workflow.py" not in skill_text:
        errors.append("스킬 진입점이 승인 후 이미지 계약을 사용하지 않음")

    method_path = SKILL_ROOT / "references" / "method.md"
    method_bytes = method_path.read_bytes()
    method_text = method_bytes.decode("utf-8")
    match = re.search(r"방법론 버전: `([^`]+)`", method_text)
    if not match:
        errors.append("방법론 버전을 찾을 수 없음")
    for rule in ("내면 동기", "필수 필요", "평균 4.2점"):
        if rule not in method_text:
            errors.append(f"방법론 의미 품질 규칙 없음: {rule}")

    report_text = (SKILL_ROOT / "templates" / "report.md").read_text(encoding="utf-8")
    for field in REPORT_FIELDS:
        if "{{" + field + "}}" not in report_text:
            errors.append(f"보고서 필드 없음: {field}")

    for forbidden in (PLUGIN_ROOT / "jobs", PLUGIN_ROOT / "source-materials"):
        if forbidden.exists():
            errors.append(f"배포 경계 안에 작업 데이터 디렉터리가 있음: {forbidden.name}")

    try:
        from distribution import DistributionError, audit_source

        audit_source(PLUGIN_ROOT)
    except (DistributionError, OSError) as exc:
        errors.append(f"배포 경계 검사 실패: {exc}")

    metadata = {
        "plugin_version": str(manifest.get("version", "")),
        "method_version": match.group(1) if match else "",
        "method_sha256": hashlib.sha256(method_bytes).hexdigest(),
    }
    return errors, metadata


def main() -> int:
    try:
        errors, metadata = validate()
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print(f"아키텍처 검증 실패: {exc}", file=sys.stderr)
        return 1

    if errors:
        for error in errors:
            print(f"아키텍처 검증 실패: {error}", file=sys.stderr)
        return 1

    print("아키텍처 검증 통과")
    for key, value in metadata.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
