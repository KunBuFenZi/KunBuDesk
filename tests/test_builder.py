from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from common import ROOT, decode_config, encode_config, load_yaml
from configure import validate
from android_signing import SECRET_NAMES, keystore, require_secrets, verify_apk
from package import package
from pipeline import JOBS, adapted_steps, all_matrices, select_matrices
from sync_upstream import SMOKE_CONFIG, verify_recipes
from source_archive import include_source_member


class ConfigTests(unittest.TestCase):
    def test_config_fields_are_strings(self):
        with self.assertRaises(ValueError):
            validate({**SMOKE_CONFIG, "relay_server": 123})

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
        self.assertEqual([entry["arch"] for entry in matrix["linux_wayland"]], ["x86_64"])
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
        self.assertFalse(matrix["linux_wayland"])

    def test_linux_includes_wayland_only_for_supported_architecture(self):
        for arch in ("all", "x86_64"):
            with self.subTest(arch=arch):
                self.assertEqual(len(select_matrices("linux", arch)["linux_wayland"]), 1)
        for arch in ("arm64", "armv7"):
            with self.subTest(arch=arch):
                self.assertFalse(select_matrices("linux", arch)["linux_wayland"])

    def test_standalone_wayland_build_has_its_bridge_dependency(self):
        selected = select_matrices("linux-wayland", "x86_64")
        self.assertEqual(len(selected["linux_wayland"]), 1)
        self.assertEqual([item["artifact-name"] for item in selected["bridge"]], ["bridge-artifact"])
        for kind in ("windows", "macos", "android", "linux", "linux_legacy", "appimage"):
            self.assertFalse(selected[kind])
        with self.assertRaises(ValueError):
            select_matrices("linux-wayland", "arm64")

    def test_wayland_keeps_drm_build_and_verification(self):
        steps, _ = adapted_steps("linux_wayland")
        build = next(step for step in steps if step.get("name") == "Build rustdesk")
        self.assertIn('drm', build["with"]["run"])
        self.assertTrue(any(step.get("name") == "Build libdrmtap" for step in steps))
        check = next(step for step in steps if step.get("name") == "Check the deb is a drm build")
        self.assertIn("libdrmtap.so.0", check["run"])
        # Upload the final package only after verification, not on a failed DRM check.
        self.assertFalse(any(step.get("uses", "").startswith("actions/upload-artifact@") for step in steps))

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

    def test_android_signing_is_mandatory_for_every_recipe(self):
        for kind in ("android", "android_universal"):
            steps, env = adapted_steps(kind)
            signing = [step for step in steps if "android_signing.py sign" in step.get("run", "")]
            self.assertEqual(len(signing), 1)
            self.assertNotIn("if", signing[0])
            self.assertFalse(any("sign-android-release@" in step.get("uses", "") for step in steps))
            # Private keystore must never be exported into GITHUB_ENV by stage.py.
            self.assertNotIn("CB_SECRET_ANDROID_SIGNING_KEY", json.dumps(env))

    def test_static_workflows_do_not_embed_user_input_in_shell(self):
        for path in (ROOT / ".github/workflows").glob("*.yml"):
            text = path.read_text(encoding="utf-8")
            workflow = load_yaml(path)
            for job in workflow["jobs"].values():
                for step in job.get("steps", []):
                    self.assertNotIn("inputs.", step.get("run", ""))


class ArtifactTests(unittest.TestCase):
    def test_wayland_package_is_distinct_and_marked_experimental(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            (source / "rustdesk-unattended-wayland-1.5.0-x86_64.deb").write_bytes(b"experimental")
            (source / "rustdesk-1.5.0-x86_64.deb").write_bytes(b"standard")
            with patch.dict(os.environ, {"CLIENT_CONFIG_B64": encode_config(SMOKE_CONFIG), "CB_MATRIX_ARCH": "x86_64"}):
                package("linux_wayland", source)
            output = source / ".custom-builder/dist"
            self.assertTrue((output / "BuildSmoke-unattended-wayland-1.5.0-x86_64.deb").exists())
            self.assertFalse((output / "BuildSmoke-1.5.0-x86_64.deb").exists())
            info = json.loads((output / "build-info.json").read_text())
            self.assertTrue(info["experimental"])
            self.assertEqual(info["capture_backend"], "drm")

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
            with patch.dict(os.environ, {"CLIENT_CONFIG_B64": encode_config(SMOKE_CONFIG)}), \
                    patch("package.require_secrets"), patch("package.verify_apk", return_value="a" * 64) as verify:
                package("android", source)
                verify.assert_called_once_with(source / "signed-apk/rustdesk-test-signed.apk")
            output = source / ".custom-builder/dist"
            self.assertFalse((output / "BuildSmoke-test.apk").exists())
            self.assertTrue((output / "BuildSmoke-test-signed.apk").exists())
            info = json.loads((output / "build-info.json").read_text())
            self.assertEqual(info["android_certificate_sha256"], "a" * 64)

    def test_android_debug_only_build_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            (source / "signed-apk").mkdir()
            (source / "signed-apk/rustdesk-test.apk").write_bytes(b"debug")
            with patch.dict(os.environ, {"CLIENT_CONFIG_B64": encode_config(SMOKE_CONFIG)}), patch("package.require_secrets"):
                with self.assertRaisesRegex(ValueError, "No APK signed"):
                    package("android", source)

    def test_android_wrong_signature_is_rejected_before_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            (source / "signed-apk").mkdir()
            (source / "signed-apk/rustdesk-test-signed.apk").write_bytes(b"wrong-signer")
            with patch.dict(os.environ, {"CLIENT_CONFIG_B64": encode_config(SMOKE_CONFIG)}), \
                    patch("package.require_secrets"), patch("package.verify_apk", side_effect=ValueError("wrong signer")):
                with self.assertRaisesRegex(ValueError, "wrong signer"):
                    package("android", source)
            self.assertFalse(list((source / ".custom-builder/dist").glob("*.apk")))


class SigningTests(unittest.TestCase):
    def test_missing_secrets_never_falls_back_to_debug_signing(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "ANDROID_SIGNING_KEY"):
                require_secrets()

    def test_apk_must_have_exactly_one_pinned_signer(self):
        fingerprint = "a" * 64
        for digests in ([fingerprint], ["b" * 64], [fingerprint, "b" * 64], []):
            output = "\n".join(f"Signer #{i + 1} certificate SHA-256 digest: {value}" for i, value in enumerate(digests)).encode()
            with self.subTest(digests=digests), patch("android_signing.sdk_tool", return_value="apksigner"), \
                    patch("android_signing.run_tool", return_value=output), \
                    patch("android_signing.expected_fingerprint", return_value=fingerprint):
                if digests == [fingerprint]:
                    self.assertEqual(verify_apk(Path("test.apk")), fingerprint)
                else:
                    with self.assertRaises(ValueError):
                        verify_apk(Path("test.apk"))

    def test_replaced_keystore_is_rejected_and_temporary_key_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"CB_SECRET_" + name: "value" for name in SECRET_NAMES}
            env.update(CB_SECRET_ANDROID_SIGNING_KEY="YWJj", RUNNER_TEMP=tmp)
            with patch.dict(os.environ, env), patch("android_signing.run_tool", return_value=b"wrong certificate"), \
                    patch("android_signing.expected_fingerprint", return_value="a" * 64):
                with self.assertRaisesRegex(ValueError, "refusing key rotation"):
                    with keystore():
                        self.fail("Wrong signing key was accepted")
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_signing_backups_and_keys_are_excluded_from_source_artifact(self):
        for name in ("source/.custom-builder/.signing/passwords.json", "source/key.jks", "source/key.p12", "source/key.keystore", "source/key.pfx"):
            with self.subTest(name=name):
                self.assertIsNone(include_source_member(tarfile.TarInfo(name)))
        self.assertIsNotNone(include_source_member(tarfile.TarInfo("source/config/android-signing.json")))


if __name__ == "__main__":
    unittest.main()
