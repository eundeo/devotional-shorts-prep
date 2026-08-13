from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path

import distribution


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[3]


class DistributionTests(unittest.TestCase):
    def _copy_plugin(self, root: Path, version: str | None = None) -> Path:
        target = root / PLUGIN_ROOT.name
        shutil.copytree(
            PLUGIN_ROOT,
            target,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )
        if version:
            manifest_path = target / ".codex-plugin" / "plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["version"] = version
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        return target

    def _release_copy(self, root: Path) -> Path:
        manifest = json.loads(
            (PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        return self._copy_plugin(root, manifest["version"].split("+", 1)[0])

    def test_source_audit_and_repo_marketplace(self) -> None:
        manifest, files = distribution.audit_source(PLUGIN_ROOT)
        self.assertEqual("devotional-shorts-prep", manifest["name"])
        self.assertGreater(len(files), 20)

        marketplace_path = PROJECT_ROOT / ".agents" / "plugins" / "marketplace.json"
        if not marketplace_path.is_file():
            self.assertEqual(
                "devotional-shorts",
                distribution.marketplace_manifest("devotional-shorts-prep")["name"],
            )
            return
        marketplace = json.loads(marketplace_path.read_text(encoding="utf-8"))
        self.assertEqual("devotional-shorts", marketplace["name"])
        self.assertEqual(distribution.marketplace_manifest("devotional-shorts-prep"), marketplace)
        self.assertEqual(
            {
                "name": "devotional-shorts-prep",
                "source": {"source": "local", "path": "./plugins/devotional-shorts-prep"},
                "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                "category": "Productivity",
            },
            marketplace["plugins"][0],
        )

    def test_build_is_deterministic_and_archive_is_clean(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            release_plugin = self._release_copy(root / "source")
            first = distribution.build_archive(release_plugin, root / "one")
            second = distribution.build_archive(release_plugin, root / "two")
            self.assertEqual(first["archive_sha256"], second["archive_sha256"])
            verified = distribution.verify_archive(first["archive"])
            self.assertEqual(first["version"], verified["version"])

            checksum = Path(first["checksum"]).read_text(encoding="utf-8").split()[0]
            self.assertEqual(hashlib.sha256(Path(first["archive"]).read_bytes()).hexdigest(), checksum)
            self.assertEqual(
                distribution.marketplace_manifest("devotional-shorts-prep"),
                json.loads(Path(first["marketplace"]).read_text(encoding="utf-8")),
            )
            with zipfile.ZipFile(first["archive"]) as bundle:
                names = bundle.namelist()
            self.assertTrue(all(name.startswith("devotional-shorts-prep/") for name in names))
            self.assertFalse(any("/jobs/" in name or "/source-materials/" in name for name in names))
            self.assertFalse(any("__pycache__" in name or name.endswith(".pyc") for name in names))
            self.assertFalse(any(name.endswith("/.env") for name in names))
            self.assertFalse(any(name.startswith(".agents/") for name in names))

    def test_audit_rejects_private_runtime_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            plugin = self._copy_plugin(Path(temp))
            (plugin / ".env").write_text("TELEGRAM_BOT_TOKEN" + "=secret\n", encoding="utf-8")
            with self.assertRaisesRegex(distribution.DistributionError, "실제 환경설정"):
                distribution.audit_source(plugin)

        with tempfile.TemporaryDirectory() as temp:
            plugin = self._copy_plugin(Path(temp))
            (plugin / "private.txt").write_text(
                "123456:" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcd\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(distribution.DistributionError, "Telegram 봇 토큰"):
                distribution.audit_source(plugin)

        with tempfile.TemporaryDirectory() as temp:
            plugin = self._copy_plugin(Path(temp))
            (plugin / "notes.txt").write_text("local: /" + "Users/example/private.txt\n", encoding="utf-8")
            with self.assertRaisesRegex(distribution.DistributionError, "사용자 홈 절대경로"):
                distribution.audit_source(plugin)

    def test_cachebuster_is_valid_for_development_but_not_release_zip(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plugin = self._copy_plugin(root / "source", "0.8.0+codex.local-20260812-120000")
            manifest, _ = distribution.audit_source(plugin)
            self.assertEqual("0.8.0+codex.local-20260812-120000", manifest["version"])
            with self.assertRaisesRegex(distribution.DistributionError, "출시 버전"):
                distribution.build_archive(plugin, root / "release")

    def test_codex_version_cache_folder_is_a_valid_runtime_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            cached = Path(temp) / "cache" / "devotional-shorts-prep" / "0.8.0"
            shutil.copytree(
                PLUGIN_ROOT,
                cached,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
            )
            manifest, files = distribution.audit_source(cached)
            self.assertEqual("devotional-shorts-prep", manifest["name"])
            self.assertGreater(len(files), 20)
            completed = subprocess.run(
                [sys.executable, str(cached / "scripts" / "validate_architecture.py")],
                cwd=cached,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)

    def test_verify_rejects_tampered_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            built = distribution.build_archive(
                self._release_copy(root / "source"), root / "release"
            )
            source = Path(built["archive"])
            tampered = root / "tampered.zip"
            with zipfile.ZipFile(source) as original, zipfile.ZipFile(tampered, "w") as changed:
                for info in original.infolist():
                    data = original.read(info.filename)
                    if info.filename.endswith("/README.md"):
                        data += b"tampered"
                    changed.writestr(info, data)
            with self.assertRaisesRegex(distribution.DistributionError, "무결성 불일치"):
                distribution.verify_archive(tampered)

    def test_verify_rejects_duplicate_and_traversal_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            duplicate = root / "duplicate.zip"
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                with zipfile.ZipFile(duplicate, "w") as bundle:
                    bundle.writestr("devotional-shorts-prep/file.txt", b"one")
                    bundle.writestr("devotional-shorts-prep/file.txt", b"two")
            with self.assertRaisesRegex(distribution.DistributionError, "중복 경로"):
                distribution.verify_archive(duplicate)

            traversal = root / "traversal.zip"
            with zipfile.ZipFile(traversal, "w") as bundle:
                bundle.writestr("devotional-shorts-prep/../escape.txt", b"bad")
            with self.assertRaisesRegex(distribution.DistributionError, "안전하지 않은 ZIP 경로"):
                distribution.verify_archive(traversal)

    def test_clean_extract_update_downgrade_guard_and_reinstall(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            current = distribution.build_archive(
                self._release_copy(root / "source"), root / "current"
            )
            plugins_dir = root / "marketplace" / "plugins"
            installed = Path(distribution.extract_archive(current["archive"], plugins_dir)["installed"])
            completed = subprocess.run(
                [sys.executable, str(installed / "scripts" / "validate_architecture.py")],
                cwd=installed,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertNotIn(str(PROJECT_ROOT), completed.stdout)

            current_version = current["version"]
            major, minor, patch = map(int, current_version.split("."))
            newer_version = f"{major}.{minor}.{patch + 1}"
            newer_source = self._copy_plugin(root / "newer-source", newer_version)
            newer = distribution.build_archive(newer_source, root / "newer")
            updated = distribution.extract_archive(newer["archive"], plugins_dir, update=True)
            self.assertTrue(updated["updated"])
            installed_manifest = json.loads(
                (installed / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
            )
            self.assertEqual(newer_version, installed_manifest["version"])

            with self.assertRaisesRegex(distribution.DistributionError, "이전 버전"):
                distribution.extract_archive(current["archive"], plugins_dir, update=True)
            self.assertEqual(
                newer_version,
                json.loads((installed / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))[
                    "version"
                ],
            )

            shutil.rmtree(installed)
            reinstalled = Path(distribution.extract_archive(current["archive"], plugins_dir)["installed"])
            self.assertTrue((reinstalled / "skills" / "devotional-shorts" / "SKILL.md").is_file())

    def test_extract_refuses_existing_install_without_update(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            built = distribution.build_archive(
                self._release_copy(root / "source"), root / "release"
            )
            plugins_dir = root / "plugins"
            distribution.extract_archive(built["archive"], plugins_dir)
            with self.assertRaisesRegex(distribution.DistributionError, "--update"):
                distribution.extract_archive(built["archive"], plugins_dir)

    def test_extract_refuses_symlink_install_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            built = distribution.build_archive(
                self._release_copy(root / "source"), root / "release"
            )
            plugins_dir = root / "plugins"
            plugins_dir.mkdir()
            outside = root / "outside"
            outside.mkdir()
            (plugins_dir / "devotional-shorts-prep").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(distribution.DistributionError, "심볼릭 링크"):
                distribution.extract_archive(built["archive"], plugins_dir, update=True)


if __name__ == "__main__":
    unittest.main()
