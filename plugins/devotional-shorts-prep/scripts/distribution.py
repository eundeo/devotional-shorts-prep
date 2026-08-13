#!/usr/bin/env python3
"""Audit, build, verify, and safely extract a plugin release archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_MANIFEST = "DISTRIBUTION-MANIFEST.json"
FORMAT_VERSION = 1
MARKETPLACE_NAME = "devotional-shorts"
MAX_MEMBER_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
RELEASE_VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
PLUGIN_VERSION_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:\+codex\.[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
BOT_TOKEN_RE = re.compile(rb"(?<![A-Za-z0-9_-])\d{6,12}:[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])")
ENV_KEYS = {"TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_APPROVER_IDS"}
FORBIDDEN_PARTS = {".git", ".venv", "jobs", "source-materials"}
FORBIDDEN_SUFFIXES = {".log", ".zip"}
IGNORED_PARTS = {"__pycache__"}
IGNORED_NAMES = {".DS_Store"}
IGNORED_SUFFIXES = {".pyc", ".pyo"}


class DistributionError(ValueError):
    """A release input is unsafe or does not satisfy the distribution contract."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DistributionError(f"플러그인 매니페스트를 읽을 수 없음: {exc}") from exc
    if not isinstance(payload, dict):
        raise DistributionError("플러그인 매니페스트는 JSON 객체여야 함")
    name = payload.get("name")
    version = payload.get("version")
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
        raise DistributionError("플러그인 매니페스트 name이 kebab-case 형식이 아님")
    if not isinstance(version, str) or not PLUGIN_VERSION_RE.fullmatch(version):
        raise DistributionError("플러그인 버전은 X.Y.Z 또는 Codex 캐시버스터 형식이어야 함")
    return payload


def _forbidden_reason(relative: PurePosixPath) -> str | None:
    if any(part in FORBIDDEN_PARTS for part in relative.parts):
        return "작업·개인·캐시 디렉터리"
    name = relative.name
    if relative.suffix.lower() in FORBIDDEN_SUFFIXES:
        return "로컬 또는 생성 파일"
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return "실제 환경설정 파일"
    return None


def _secret_findings(relative: PurePosixPath, data: bytes) -> list[str]:
    findings: list[str] = []
    if BOT_TOKEN_RE.search(data):
        findings.append("Telegram 봇 토큰 형식")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return findings

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        if separator and key.strip() in ENV_KEYS and value.strip().strip("\"'"):
            findings.append(f"값이 채워진 {key.strip()}")

    # Split the literals so this scanner does not flag its own source code.
    personal_paths = (
        re.compile("/" + "Users/" + r"[^/\s]+/"),
        re.compile("/" + "home/" + r"[^/\s]+/"),
        re.compile(r"[A-Za-z]:\\" + "Users\\" + r"[^\\\s]+\\"),
    )
    if any(pattern.search(text) for pattern in personal_paths):
        findings.append("사용자 홈 절대경로")
    return findings


def audit_source(plugin_root: Path | str = PLUGIN_ROOT) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return manifest and ordered deployable file records, or raise on unsafe content."""
    root = Path(plugin_root).expanduser().resolve()
    manifest = _manifest(root / ".codex-plugin" / "plugin.json")
    files: list[dict[str, Any]] = []
    errors: list[str] = []

    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = PurePosixPath(path.relative_to(root).as_posix())
        if relative.as_posix() == ARCHIVE_MANIFEST:
            continue
        if (
            any(part in IGNORED_PARTS for part in relative.parts)
            or relative.name in IGNORED_NAMES
            or relative.suffix.lower() in IGNORED_SUFFIXES
        ):
            continue
        if path.is_symlink():
            errors.append(f"심볼릭 링크 금지: {relative}")
            continue
        reason = _forbidden_reason(relative)
        if reason:
            errors.append(f"배포 금지 항목({reason}): {relative}")
            continue
        if path.is_dir():
            continue
        if not path.is_file():
            errors.append(f"일반 파일이 아닌 항목: {relative}")
            continue
        try:
            data = path.read_bytes()
        except OSError as exc:
            errors.append(f"파일 읽기 실패: {relative}: {exc}")
            continue
        for finding in _secret_findings(relative, data):
            errors.append(f"배포 보안 검사 실패({finding}): {relative}")
        files.append({"path": relative.as_posix(), "bytes": len(data), "sha256": _sha256(data)})

    if errors:
        raise DistributionError("\n".join(errors))
    if not files:
        raise DistributionError("배포할 파일이 없음")
    return manifest, files


def _release_manifest(manifest: dict[str, Any], files: list[dict[str, Any]]) -> bytes:
    payload = {
        "format_version": FORMAT_VERSION,
        "plugin": {"name": manifest["name"], "version": manifest["version"]},
        "files": files,
    }
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def marketplace_manifest(plugin_name: str) -> dict[str, Any]:
    return {
        "name": MARKETPLACE_NAME,
        "interface": {"displayName": "Devotional Shorts"},
        "plugins": [
            {
                "name": plugin_name,
                "source": {"source": "local", "path": f"./plugins/{plugin_name}"},
                "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                "category": "Productivity",
            }
        ],
    }


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    return info


def build_archive(plugin_root: Path | str, output_dir: Path | str) -> dict[str, Any]:
    root = Path(plugin_root).expanduser().resolve()
    output = Path(output_dir).expanduser().resolve()
    if output == root or root in output.parents:
        raise DistributionError("배포 출력 폴더는 플러그인 루트 밖이어야 함")
    manifest, files = audit_source(root)
    if not RELEASE_VERSION_RE.fullmatch(manifest["version"]):
        raise DistributionError("배포 ZIP은 캐시버스터가 없는 X.Y.Z 출시 버전으로만 생성 가능")
    output.mkdir(parents=True, exist_ok=True)
    archive_name = f"{manifest['name']}-{manifest['version']}.zip"
    archive_path = output / archive_name
    release_manifest = _release_manifest(manifest, files)

    handle, temp_name = tempfile.mkstemp(prefix=f".{archive_name}.", dir=output)
    os.close(handle)
    temp_path = Path(temp_name)
    try:
        with zipfile.ZipFile(temp_path, "w") as bundle:
            top = manifest["name"]
            bundle.writestr(_zip_info(f"{top}/{ARCHIVE_MANIFEST}"), release_manifest)
            for record in files:
                data = (root / record["path"]).read_bytes()
                bundle.writestr(_zip_info(f"{top}/{record['path']}"), data)
        verified = verify_archive(temp_path)
        os.replace(temp_path, archive_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    archive_hash = _sha256(archive_path.read_bytes())
    checksum_path = archive_path.with_suffix(archive_path.suffix + ".sha256")
    checksum_path.write_text(f"{archive_hash}  {archive_path.name}\n", encoding="utf-8")
    marketplace_path = output / "marketplace.json"
    marketplace_path.write_text(
        json.dumps(marketplace_manifest(manifest["name"]), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        **verified,
        "archive": str(archive_path),
        "archive_sha256": archive_hash,
        "checksum": str(checksum_path),
        "marketplace": str(marketplace_path),
    }


def _safe_member(info: zipfile.ZipInfo) -> PurePosixPath:
    name = info.filename
    if not name or "\\" in name:
        raise DistributionError(f"안전하지 않은 ZIP 경로: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise DistributionError(f"안전하지 않은 ZIP 경로: {name}")
    file_type = (info.external_attr >> 16) & 0o170000
    if file_type == stat.S_IFLNK:
        raise DistributionError(f"ZIP 심볼릭 링크 금지: {name}")
    if info.is_dir():
        raise DistributionError(f"명시적 ZIP 디렉터리 항목 금지: {name}")
    if info.file_size > MAX_MEMBER_BYTES:
        raise DistributionError(f"ZIP 파일 크기 한도 초과: {name}")
    return path


def verify_archive(archive: Path | str) -> dict[str, Any]:
    archive_path = Path(archive).expanduser().resolve()
    try:
        bundle = zipfile.ZipFile(archive_path, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise DistributionError(f"배포 ZIP을 열 수 없음: {exc}") from exc

    with bundle:
        infos = bundle.infolist()
        if not infos:
            raise DistributionError("배포 ZIP이 비어 있음")
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            raise DistributionError("ZIP에 중복 경로가 있음")
        paths = [_safe_member(info) for info in infos]
        if sum(info.file_size for info in infos) > MAX_ARCHIVE_BYTES:
            raise DistributionError("ZIP 전체 압축 해제 크기 한도 초과")
        top_levels = {path.parts[0] for path in paths if path.parts}
        if len(top_levels) != 1:
            raise DistributionError("ZIP 최상위 플러그인 폴더는 하나여야 함")
        plugin_name = next(iter(top_levels))
        manifest_name = f"{plugin_name}/{ARCHIVE_MANIFEST}"
        if manifest_name not in names:
            raise DistributionError("배포 무결성 매니페스트가 없음")
        try:
            release = json.loads(bundle.read(manifest_name).decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError, KeyError) as exc:
            raise DistributionError(f"배포 무결성 매니페스트를 읽을 수 없음: {exc}") from exc
        if not isinstance(release, dict) or release.get("format_version") != FORMAT_VERSION:
            raise DistributionError("지원하지 않는 배포 매니페스트 버전")
        plugin = release.get("plugin")
        records = release.get("files")
        if not isinstance(plugin, dict) or set(plugin) != {"name", "version"}:
            raise DistributionError("배포 플러그인 메타데이터 형식 오류")
        if plugin.get("name") != plugin_name or not RELEASE_VERSION_RE.fullmatch(
            str(plugin.get("version", ""))
        ):
            raise DistributionError("배포 플러그인 이름 또는 버전 오류")
        if not isinstance(records, list):
            raise DistributionError("배포 파일 목록 형식 오류")

        expected: dict[str, dict[str, Any]] = {}
        for record in records:
            if not isinstance(record, dict) or set(record) != {"path", "bytes", "sha256"}:
                raise DistributionError("배포 파일 기록 형식 오류")
            relative = PurePosixPath(str(record["path"]))
            if relative.is_absolute() or ".." in relative.parts or len(relative.parts) < 1:
                raise DistributionError(f"잘못된 배포 파일 경로: {relative}")
            if _forbidden_reason(relative):
                raise DistributionError(f"배포 금지 항목이 ZIP에 포함됨: {relative}")
            if relative.as_posix() in expected:
                raise DistributionError(f"배포 매니페스트 중복 경로: {relative}")
            if not isinstance(record["bytes"], int) or record["bytes"] < 0:
                raise DistributionError(f"배포 파일 크기 오류: {relative}")
            if not isinstance(record["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", record["sha256"]):
                raise DistributionError(f"배포 파일 해시 오류: {relative}")
            expected[relative.as_posix()] = record

        actual_names = set(names) - {manifest_name}
        expected_names = {f"{plugin_name}/{relative}" for relative in expected}
        if actual_names != expected_names:
            raise DistributionError("ZIP 파일 목록과 배포 매니페스트가 다름")
        for relative, record in expected.items():
            data = bundle.read(f"{plugin_name}/{relative}")
            if len(data) != record["bytes"] or _sha256(data) != record["sha256"]:
                raise DistributionError(f"배포 파일 무결성 불일치: {relative}")
            findings = _secret_findings(PurePosixPath(relative), data)
            if findings:
                raise DistributionError(f"배포 보안 검사 실패({', '.join(findings)}): {relative}")

        plugin_json_name = f"{plugin_name}/.codex-plugin/plugin.json"
        if plugin_json_name not in actual_names:
            raise DistributionError("ZIP에 플러그인 매니페스트가 없음")
        try:
            plugin_json = json.loads(bundle.read(plugin_json_name).decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise DistributionError(f"ZIP 플러그인 매니페스트 오류: {exc}") from exc
        if plugin_json.get("name") != plugin_name or plugin_json.get("version") != plugin["version"]:
            raise DistributionError("ZIP 매니페스트의 이름 또는 버전이 배포 기록과 다름")

    return {"name": plugin_name, "version": plugin["version"], "files": len(expected)}


def _version_tuple(version: str) -> tuple[int, int, int]:
    match = PLUGIN_VERSION_RE.fullmatch(version)
    if not match:
        raise DistributionError("설치본 버전이 X.Y.Z 형식이 아님")
    return tuple(int(part) for part in match.groups())


def extract_archive(archive: Path | str, destination: Path | str, update: bool = False) -> dict[str, Any]:
    """Extract to a marketplace plugins directory without partial or silent overwrite."""
    verified = verify_archive(archive)
    archive_path = Path(archive).expanduser().resolve()
    plugins_dir = Path(destination).expanduser().resolve()
    plugins_dir.mkdir(parents=True, exist_ok=True)
    target = plugins_dir / verified["name"]

    if target.is_symlink():
        raise DistributionError("설치 대상 심볼릭 링크에는 설치하거나 업데이트하지 않음")
    if target.exists() and not update:
        raise DistributionError("설치 대상이 이미 존재함; 업데이트하려면 --update를 사용")
    if target.exists():
        current = _manifest(target / ".codex-plugin" / "plugin.json")
        if current["name"] != verified["name"]:
            raise DistributionError("기존 설치본 이름이 배포물과 다름")
        if _version_tuple(verified["version"]) < _version_tuple(current["version"]):
            raise DistributionError("이전 버전으로의 덮어쓰기는 지원하지 않음")
        audit_source(target)

    stage = plugins_dir / f".{verified['name']}.stage-{uuid.uuid4().hex}"
    backup = plugins_dir / f".{verified['name']}.backup-{uuid.uuid4().hex}"
    with tempfile.TemporaryDirectory(prefix=".devotional-shorts-extract-", dir=plugins_dir) as temp:
        temp_root = Path(temp)
        with zipfile.ZipFile(archive_path, "r") as bundle:
            for info in bundle.infolist():
                relative = _safe_member(info)
                output = temp_root.joinpath(*relative.parts)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(bundle.read(info.filename))
        extracted = temp_root / verified["name"]
        audit_source(extracted)
        shutil.move(str(extracted), stage)

    try:
        if target.exists():
            os.replace(target, backup)
        try:
            os.replace(stage, target)
        except OSError:
            if backup.exists() and not target.exists():
                os.replace(backup, target)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return {**verified, "installed": str(target), "updated": update}


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="묵상 쇼츠 플러그인 배포 검사와 ZIP 생성")
    commands = parser.add_subparsers(dest="command", required=True)

    audit = commands.add_parser("audit", help="플러그인 소스의 배포 경계와 비밀정보 검사")
    audit.add_argument("--plugin-root", default=str(PLUGIN_ROOT))

    build = commands.add_parser("build", help="결정적 ZIP과 SHA-256 체크섬 생성")
    build.add_argument("--plugin-root", default=str(PLUGIN_ROOT))
    build.add_argument("--output-dir", required=True)

    verify = commands.add_parser("verify", help="ZIP 경로·파일·해시·비밀정보 검사")
    verify.add_argument("--archive", required=True)

    extract = commands.add_parser("extract", help="검증된 ZIP을 마켓플레이스 plugins 폴더에 설치")
    extract.add_argument("--archive", required=True)
    extract.add_argument("--destination", required=True, help="마켓플레이스의 plugins 폴더")
    extract.add_argument("--update", action="store_true")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "audit":
            manifest, files = audit_source(args.plugin_root)
            result = {"name": manifest["name"], "version": manifest["version"], "files": len(files)}
        elif args.command == "build":
            result = build_archive(args.plugin_root, args.output_dir)
        elif args.command == "verify":
            result = verify_archive(args.archive)
        else:
            result = extract_archive(args.archive, args.destination, update=args.update)
    except (DistributionError, OSError, zipfile.BadZipFile) as exc:
        print(f"배포 작업 실패: {exc}", file=sys.stderr)
        return 1
    _print_json(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
