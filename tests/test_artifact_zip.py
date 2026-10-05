from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import pyzipper

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from artifact_zip import PASSWORD_ENV, seal, smoke, unseal
from common import ROOT, load_yaml
from pipeline import adapted_steps
from prepare import prepare


class EncryptionTests(unittest.TestCase):
    def test_all_download_contents_require_the_password_and_roundtrip_exactly(self):
        files = {
            "client.exe": b"compiled Windows executable",
            "client.msi": b"compiled Windows installer",
            "client.dmg": b"compiled macOS installer",
            "client.apk": b"APK signed with a fixed certificate",
            "client.deb": b"compiled Linux installer",
            "client.rpm": b"compiled Linux installer",
            "client.AppImage": b"compiled AppImage",
            "build-info.json": b'{"id_server":"private.example.invalid"}',
            "SHA256SUMS.txt": b"checksums",
            "source/corresponding-source.tar.gz": b"patched source including private defaults",
        }
        password = "test-archive-password"
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {PASSWORD_ENV: password}):
            root = Path(tmp)
            source = root / "dist"
            for name, content in files.items():
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                archive = seal(source, root / "encrypted/client.zip")
            self.assertNotIn(password, stdout.getvalue())
            with pyzipper.AESZipFile(archive) as encrypted:
                self.assertEqual(set(encrypted.namelist()), set(files))
                for item in encrypted.infolist():
                    self.assertTrue(item.flag_bits & 1)
                    self.assertEqual(item.wz_aes_strength, 3)
                    with self.assertRaises(RuntimeError):
                        encrypted.read(item)
                encrypted.setpassword(b"wrong-password")
                for name in files:
                    with self.assertRaises(RuntimeError):
                        encrypted.read(name)
            with patch.dict(os.environ, {PASSWORD_ENV: "wrong-password"}):
                with self.assertRaises(RuntimeError):
                    unseal(archive, root / "wrong")
                self.assertFalse((root / "wrong").exists())
            unseal(archive, root / "restored")
            for name, content in files.items():
                self.assertEqual((root / "restored" / name).read_bytes(), content)

    def test_missing_password_stops_before_build_and_never_creates_an_archive(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            root = Path(tmp)
            source = root / "client.apk"
            source.write_bytes(b"signed APK")
            with self.assertRaisesRegex(ValueError, "ARTIFACT_ZIP_PASSWORD"):
                seal(source, root / "client.zip")
            self.assertFalse((root / "client.zip").exists())
            with patch("prepare.load_client_config") as config:
                with self.assertRaisesRegex(ValueError, "ARTIFACT_ZIP_PASSWORD"):
                    prepare()
                config.assert_not_called()

    def test_intermediate_files_keep_their_original_names_for_consumers(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {PASSWORD_ENV: "dependency-password"}):
            root = Path(tmp)
            for name in ("liblibrustdesk.so", "rustdesk-1.5.0-aarch64.deb"):
                with self.subTest(name=name):
                    original = root / "target/release" / name
                    original.parent.mkdir(parents=True, exist_ok=True)
                    original.write_bytes(b"build dependency")
                    archive = seal(original, root / "encrypted-intermediate/client-build-dependency.zip")
                    with pyzipper.AESZipFile(archive) as encrypted:
                        self.assertEqual(encrypted.namelist(), [name])
                    destination = root / "consumer" / name
                    unseal(archive, destination)
                    self.assertEqual((destination / name).read_bytes(), original.read_bytes())

    def test_unsafe_members_and_unencrypted_dependencies_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {PASSWORD_ENV: "dependency-password"}):
            root = Path(tmp)
            archive = root / "unsafe.zip"
            with pyzipper.AESZipFile(archive, "w", encryption=pyzipper.WZ_AES) as encrypted:
                encrypted.setpassword(b"dependency-password")
                encrypted.setencryption(pyzipper.WZ_AES, nbits=256)
                encrypted.writestr("../escape.deb", b"unsafe")
            with self.assertRaisesRegex(ValueError, "Unsafe"):
                unseal(archive, root / "restored")
            self.assertFalse((root / "escape.deb").exists())
            with pyzipper.AESZipFile(archive, "w") as plain:
                plain.writestr("client.deb", b"not encrypted")
            with self.assertRaisesRegex(ValueError, "AES-256"):
                unseal(archive, root / "restored")

    def test_real_secret_smoke_does_not_print_password_or_keep_test_artifacts(self):
        stdout = io.StringIO()
        password = "probe-password"
        with patch.dict(os.environ, {PASSWORD_ENV: password}), redirect_stdout(stdout):
            smoke()
        self.assertNotIn(password, stdout.getvalue())
        self.assertIn("encrypted test artifact removed", stdout.getvalue())


class EncryptedWorkflowTests(unittest.TestCase):
    def test_final_downloads_and_source_upload_only_encrypted_zips(self):
        jobs = load_yaml(ROOT / ".github/workflows/custom-client.yml")["jobs"]
        prepare_step = next(step for step in jobs["prepare"]["steps"] if step.get("id") == "prepare")
        secret = "${{ secrets.ARTIFACT_ZIP_PASSWORD }}"
        self.assertEqual(prepare_step["env"][PASSWORD_ENV], secret)
        uploads = 0
        for kind, job in jobs.items():
            for i, step in enumerate(job["steps"]):
                if step.get("uses", "").startswith("actions/upload-artifact@"):
                    uploads += 1
                    self.assertEqual(step["with"]["path"], ".custom-builder/encrypted/*.zip")
                    self.assertEqual(step["with"]["if-no-files-found"], "error")
                    encryption = job["steps"][i - 1]
                    self.assertIn("artifact_zip.py seal", encryption["run"])
                    self.assertEqual(encryption["env"][PASSWORD_ENV], secret)
                    self.assertNotIn("secrets.", encryption["run"])
                    self.assertNotIn("continue-on-error", encryption)
                    self.assertNotIn("if", step)
        self.assertEqual(uploads, 10)
        for kind in ("linux", "android", "android_universal", "appimage"):
            composite = next(step for step in jobs[kind]["steps"]
                             if step.get("uses") == "./.custom-builder/.generated/action")
            self.assertEqual(composite["env"][PASSWORD_ENV], secret)

    def test_intermediate_installers_and_libraries_are_encrypted_before_upload(self):
        for kind in ("linux", "android"):
            steps, _ = adapted_steps(kind)
            uploads = [(i, step) for i, step in enumerate(steps)
                       if step.get("uses", "").startswith("actions/upload-artifact@")]
            self.assertEqual(len(uploads), 1)
            i, upload = uploads[0]
            self.assertEqual(upload["with"]["path"], ".custom-builder/encrypted-intermediate/client-build-dependency.zip")
            self.assertEqual(upload["with"]["if-no-files-found"], "error")
            encryption = steps[i - 1]
            self.assertIn("artifact_zip.py seal", encryption["run"])
            self.assertEqual(encryption.get("if"), upload.get("if"))
            self.assertNotIn("matrix.", encryption["env"]["CB_ARTIFACT_INPUT"])

    def test_dependency_downloads_are_decrypted_before_the_next_build_step(self):
        for kind, expected in (("appimage", 1), ("android_universal", 4)):
            steps, _ = adapted_steps(kind)
            downloads = [(i, step) for i, step in enumerate(steps)
                         if step.get("uses", "").startswith("actions/download-artifact@")
                         and step["with"]["name"] != "bridge-artifact"]
            self.assertEqual(len(downloads), expected)
            for i, download in downloads:
                decryption = steps[i + 1]
                self.assertIn("artifact_zip.py open", decryption["run"])
                self.assertIn("--remove-archive", decryption["run"])
                self.assertEqual(decryption.get("if"), download.get("if"))
                self.assertEqual(decryption["env"]["CB_ARTIFACT_DESTINATION"], download["with"]["path"])


if __name__ == "__main__":
    unittest.main()
