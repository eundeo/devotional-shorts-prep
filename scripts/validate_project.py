#!/usr/bin/env python3
"""Validate the project documentation, reports, and documented runtime contract."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = PROJECT_ROOT / "docs"
REPORTS_ROOT = PROJECT_ROOT / "reports"
PLUGIN_ROOT = PROJECT_ROOT / "plugins" / "devotional-shorts-prep"

EXPECTED_DOCS = (
    "README.md",
    "01-product-requirements.md",
    "02-content-methodology.md",
    "03-system-architecture.md",
    "04-data-and-state-design.md",
    "05-content-generation-workflow.md",
    "06-telegram-approval-workflow.md",
    "07-image-generation-workflow.md",
    "08-implementation-roadmap.md",
    "09-test-and-acceptance-plan.md",
    "10-operations-and-distribution.md",
)
REQUIRED_HEADINGS = ("목적", "선행 조건", "확정 요구사항", "예외 처리", "완료 기준")
JOB_STATES = (
    "DRAFT",
    "SENT_FOR_APPROVAL",
    "APPROVED",
    "REVISION_REQUESTED",
    "HOLD",
    "GENERATING",
    "DELIVERED",
    "NEEDS_REVIEW",
)
TELEGRAM_COMMANDS = ("preflight", "send-report", "check-replies", "send-images")
APPROVAL_COMMANDS = ("check-approval", "resend-report", "status")
LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
TOKEN_RE = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{25,}\b")
FILLED_ENV_RE = re.compile(
    r"^(?:TELEGRAM_BOT_TOKEN|TELEGRAM_CHAT_ID|TELEGRAM_APPROVER_IDS)=(?!\s*$).+",
    re.MULTILINE,
)


def markdown_files() -> list[Path]:
    return sorted((*DOCS_ROOT.glob("*.md"), *REPORTS_ROOT.glob("*.md")))


def validate() -> list[str]:
    errors: list[str] = []
    actual_docs = tuple(path.name for path in sorted(DOCS_ROOT.glob("*.md")))
    if set(actual_docs) != set(EXPECTED_DOCS) or len(actual_docs) != len(EXPECTED_DOCS):
        errors.append(
            "docs Markdown 목록이 계약과 다름: " + ", ".join(actual_docs)
        )

    for name in EXPECTED_DOCS[1:]:
        path = DOCS_ROOT / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for heading in REQUIRED_HEADINGS:
            if f"## {heading}" not in text:
                errors.append(f"{path.relative_to(PROJECT_ROOT)}: 필수 섹션 없음: {heading}")

    for path in markdown_files():
        text = path.read_text(encoding="utf-8")
        if TOKEN_RE.search(text) or FILLED_ENV_RE.search(text):
            errors.append(f"{path.relative_to(PROJECT_ROOT)}: 실제 Telegram 비밀값 후보 발견")
        if re.search(r"/(?:Users|home)/[^/\s]+", text):
            errors.append(f"{path.relative_to(PROJECT_ROOT)}: 사용자 홈 절대경로 발견")
        for raw_target in LINK_RE.findall(text):
            target = raw_target.strip().strip("<>")
            if target.startswith(("http://", "https://", "mailto:", "codex://", "#")):
                continue
            relative = unquote(target.split("#", 1)[0])
            if not relative:
                continue
            resolved = (path.parent / relative).resolve()
            if not resolved.exists():
                errors.append(
                    f"{path.relative_to(PROJECT_ROOT)}: 깨진 상대 링크: {raw_target}"
                )

    docs_index = (DOCS_ROOT / "README.md").read_text(encoding="utf-8")
    for name in EXPECTED_DOCS[1:]:
        if f"]({name})" not in docs_index:
            errors.append(f"docs/README.md: 문서 링크 없음: {name}")

    reports_index = (REPORTS_ROOT / "README.md").read_text(encoding="utf-8")
    for number in range(1, 10):
        candidates = sorted(REPORTS_ROOT.glob(f"session-{number:02d}-*.md"))
        if len(candidates) != 1:
            errors.append(f"reports: session-{number:02d} 보고서가 정확히 1개가 아님")
            continue
        if candidates[0].name not in reports_index:
            errors.append(f"reports/README.md: 보고서 링크 없음: {candidates[0].name}")
        report_text = candidates[0].read_text(encoding="utf-8")
        if "미해결 누락" not in report_text:
            errors.append(f"{candidates[0].relative_to(PROJECT_ROOT)}: 미해결 누락 섹션 없음")

    state_doc = (DOCS_ROOT / "04-data-and-state-design.md").read_text(encoding="utf-8")
    job_store = (PLUGIN_ROOT / "scripts" / "job_store.py").read_text(encoding="utf-8")
    for state in JOB_STATES:
        if f"`{state}`" not in state_doc:
            errors.append(f"docs/04-data-and-state-design.md: 상태 정의 없음: {state}")
        if f'"{state}"' not in job_store:
            errors.append(f"job_store.py: 문서화된 상태 없음: {state}")

    telegram_doc = (DOCS_ROOT / "06-telegram-approval-workflow.md").read_text(encoding="utf-8")
    telegram_code = (PLUGIN_ROOT / "scripts" / "telegram_bot.py").read_text(encoding="utf-8")
    approval_code = (PLUGIN_ROOT / "scripts" / "approval_workflow.py").read_text(encoding="utf-8")
    for command in TELEGRAM_COMMANDS:
        if f"`{command}`" not in telegram_doc or f'add_parser("{command}"' not in telegram_code:
            errors.append(f"Telegram 문서·구현 명령 불일치: {command}")
    for command in APPROVAL_COMMANDS:
        if f'add_parser("{command}"' not in approval_code:
            errors.append(f"승인 워크플로 명령 없음: {command}")

    method_doc = (DOCS_ROOT / "02-content-methodology.md").read_text(encoding="utf-8")
    image_doc = (DOCS_ROOT / "07-image-generation-workflow.md").read_text(encoding="utf-8")
    for required in ("120초", "150초", "평균 4.2점"):
        if required not in method_doc:
            errors.append(f"docs/02-content-methodology.md: 확정값 없음: {required}")
    if "20자" not in image_doc or "9:16" not in image_doc:
        errors.append("docs/07-image-generation-workflow.md: 20자 또는 9:16 확정값 없음")
    return errors


def main() -> int:
    try:
        errors = validate()
    except (OSError, UnicodeError) as exc:
        print(f"프로젝트 검증 실패: {exc}", file=sys.stderr)
        return 2
    if errors:
        for error in errors:
            print(f"프로젝트 검증 실패: {error}", file=sys.stderr)
        return 1
    print("프로젝트 문서·보고서·계약 검증 통과")
    print(f"docs={len(EXPECTED_DOCS)} reports=9 states={len(JOB_STATES)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
