from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from common import ROOT, decode_config, encode_config, load_yaml
from configure import validate
from package import package
from pipeline import JOBS, adapted_steps, all_matrices, select_matrices
from sync_upstream import SMOKE_CONFIG, verify_recipes


class ConfigTests(unittest.TestCase):
    def test_valid_servers_and_base64_roundtrip(self):
        config = validate(SMOKE_CONFIG)
        self.assertEqual(decode_config(encode_config(config)), config)

    def test_ipv6_servers(self):
        config = {**SMOKE_CONFIG, "id_server": "[2001:db8::1]:21116"}
        self.assertEqual(validate(config)["id_server"], config["id_server"])

    def test_unsafe_names(self):
        for name in ('a;touch pwned', 'a$(id)', 'a`id`', '../desk', 'a/b', 'My Desk', 'CON', '9Desk'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate({**SMOKE_CONFIG, "app_name": name})

    def test_invalid_server_formats(self):
        for server in ('', 'https://example.com', 'host/path', 'host:0', 'host:65536', 'host;id', 'host\nother'):
            with self.subTest(server=server), self.assertRaises(ValueError):
                validate({**SMOKE_CONFIG, "id_server": server})

    def test_public_key_only(self):
        for key in ('', 'not-base64', 'YWJj', 'A' * 88):
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate({**SMOKE_CONFIG, "key": key})

    def test_api_credentials_and_controls_rejected(self):
        for url in ('https://user:secret@example.com', 'ftp://example.com', 'https://example.com\n/abc'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate({**SMOKE_CONFIG, "api_server": url})


class PipelineTests(unittest.TestCase):
    def test_all_platforms_and_architectures(self):
        matrix = select_matrices("all", "all")
        self.assertEqual({entry["arch"] for entry in matrix["windows"]}, {"x86_64", "aarch64"})
        self.assertEqual([entry["arch"] for entry in matrix["windows_legacy"]], ["x86"])
        self.assertEqual({entry["arch"] for entry in matrix["linux"]}, {"x86_64", "aarch64"})
        self.assertEqual([entry["arch"] for entry in matrix["linux_legacy"]], ["armv7"])
        self.assertEqual({entry["arch"] for entry in matrix["macos"]}, {"x86_64", "aarch64"})
        self.assertEqual({entry["arch"] for entry in matrix["android"]}, {"x86_64", "aarch64", "armv7"})
        self.assertEqual(matrix["android_universal"][0]["arch"], "universal")

    def test_single_platform_selection(self):
        matrix = select_matrices("android", "arm64")
        self.assertEqual([entry["arch"] for entry in matrix["android"]], ["aarch64"])
        self.assertFalse(matrix["windows"])
        self.assertFalse(matrix["android_universal"])
        self.assertEqual(len(matrix["bridge"]), 1)
        self.assertFalse(matrix["topmost"])

    def test_legacy_targets_need_no_bridge(self):
        matrix = select_matrices("windows", "x86")
        self.assertFalse(matrix["bridge"])
        self.assertFalse(matrix["topmost"])
        self.assertEqual(len(matrix["windows_legacy"]), 1)

    def test_unsupported_combination_fails_before_build(self):
        with self.assertRaises(ValueError):
            select_matrices("macos", "armv7")

    def test_all_recipes_are_adaptable(self):
        verify_recipes(ROOT / "upstream")

    def test_no_author_signing_service_or_release(self):
        for kind in JOBS:
            steps, _ = adapted_steps(kind)
            self.assertFalse(any(step.get("uses", "").startswith("softprops/action-gh-release") for step in steps))
            self.assertFalse(any("res/job.py" in step.get("run", "") for step in steps))
            self.assertNotIn("secrets.", json.dumps(steps))
            self.assertNotIn("matrix.", json.dumps(steps))
            for step in steps:
                if "run" in step:
                    self.assertIn("shell", step)

    def test_static_workflows_do_not_embed_user_input_in_shell(self):
        for path in (ROOT / ".github/workflows").glob("*.yml"):
            text = path.read_text(encoding="utf-8")
            workflow = load_yaml(path)
            for job in workflow["jobs"].values():
                for step in job.get("steps", []):
                    self.assertNotIn("inputs.", step.get("run", ""))


class ArtifactTests(unittest.TestCase):
    def test_windows_outputs_complete_and_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            (source / "SignOutput").mkdir()
            for suffix in ("exe", "msi"):
                (source / "SignOutput" / f"rustdesk-1.5.0-x86_64.{suffix}").write_bytes(b"compiled-client")
            with patch.dict(os.environ, {"CLIENT_CONFIG_B64": encode_config(SMOKE_CONFIG), "CB_MATRIX_ARCH": "x86_64"}):
                package("windows", source)
            output = source / ".custom-builder/dist"
            self.assertTrue((output / "BuildSmoke-1.5.0-x86_64.exe").exists())
            self.assertTrue((output / "BuildSmoke-1.5.0-x86_64.msi").exists())
            self.assertIn("BuildSmoke-1.5.0-x86_64.exe", (output / "SHA256SUMS.txt").read_text())
            info = json.loads((output / "build-info.json").read_text())
            self.assertEqual(info["architecture"], "x86_64")

    def test_missing_artifact_is_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"CLIENT_CONFIG_B64": encode_config(SMOKE_CONFIG)}):
                with self.assertRaises(ValueError):
                    package("macos", Path(tmp))

    def test_windows_requires_both_formats(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            (source / "SignOutput").mkdir()
            (source / "SignOutput/rustdesk-test.exe").write_bytes(b"test")
            with patch.dict(os.environ, {"CLIENT_CONFIG_B64": encode_config(SMOKE_CONFIG)}):
                with self.assertRaises(ValueError):
                    package("windows", source)

    def test_android_signed_build_does_not_ship_debug_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            (source / "signed-apk").mkdir()
            (source / "signed-apk/rustdesk-test.apk").write_bytes(b"debug")
            (source / "signed-apk/rustdesk-test-signed.apk").write_bytes(b"release")
            with patch.dict(os.environ, {"CLIENT_CONFIG_B64": encode_config(SMOKE_CONFIG), "CB_SECRET_ANDROID_SIGNING_KEY": "configured"}):
                package("android", source)
            output = source / ".custom-builder/dist"
            self.assertFalse((output / "BuildSmoke-test.apk").exists())
            self.assertTrue((output / "BuildSmoke-test-signed.apk").exists())


if __name__ == "__main__":
    unittest.main()
