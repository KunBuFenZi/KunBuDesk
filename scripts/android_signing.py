"""Sign every Android output with the existing, pinned release certificate.

This module never generates keys. Private material exists only in RUNNER_TEMP
during a signing operation and passwords are passed through environment names.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from common import ROOT

SECRET_NAMES = (
    "ANDROID_SIGNING_KEY", "ANDROID_ALIAS",
    "ANDROID_KEY_STORE_PASSWORD", "ANDROID_KEY_PASSWORD",
)


def expected_fingerprint() -> str:
    config = json.loads((ROOT / "config/android-signing.json").read_text(encoding="utf-8"))
    fingerprint = config["certificate_sha256"].replace(":", "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise ValueError("Invalid pinned Android certificate SHA256")
    return fingerprint


def require_secrets():
    missing = [name for name in SECRET_NAMES if not os.environ.get("CB_SECRET_" + name)]
    if missing:
        raise ValueError("Fixed Android signing requires repository Secrets: " + ", ".join(missing))
    expected_fingerprint()


def run_tool(command: list[str]) -> bytes:
    result = subprocess.run(command, capture_output=True, timeout=120)
    if result.returncode:
        # Tool error output can contain private keystore details; do not log it.
        raise ValueError(f"{Path(command[0]).name} failed; check signing configuration and Android SDK")
    return result.stdout


@contextmanager
def keystore():
    require_secrets()
    try:
        data = base64.b64decode(os.environ["CB_SECRET_ANDROID_SIGNING_KEY"], validate=True)
    except ValueError:
        raise ValueError("ANDROID_SIGNING_KEY must be a Base64 keystore") from None
    with tempfile.TemporaryDirectory(prefix="client-signing-", dir=os.environ.get("RUNNER_TEMP")) as tmp:
        path = Path(tmp) / "release.keystore"
        path.write_bytes(data)
        path.chmod(0o600)
        certificate = run_tool([
            "keytool", "-exportcert", "-keystore", str(path),
            "-alias", os.environ["CB_SECRET_ANDROID_ALIAS"],
            "-storepass:env", "CB_SECRET_ANDROID_KEY_STORE_PASSWORD",
        ])
        if hashlib.sha256(certificate).hexdigest() != expected_fingerprint():
            raise ValueError("Android signing certificate differs from config/android-signing.json; refusing key rotation")
        yield path


def check_keystore():
    with keystore():
        pass
    print("Fixed Android keystore matches the pinned certificate")


def sdk_tool(name: str) -> str:
    sdk = Path(os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT") or "/usr/local/lib/android/sdk")
    candidates = [path for path in (sdk / "build-tools").glob("*/" + name) if path.is_file()]
    if not candidates:
        raise ValueError(f"Android SDK {name} is unavailable")
    # Android build-tools versions are numeric with optional preview suffixes.
    return str(max(candidates, key=lambda path: tuple(int(value) for value in re.findall(r"\d+", path.parent.name))))


def verify_apk(path: Path) -> str:
    output = run_tool([sdk_tool("apksigner"), "verify", "--verbose", "--print-certs", str(path)]).decode("utf-8")
    # New SDK versions add certificate numbers / SDK ranges to signer labels.
    # Require one signer and reject every certificate differing from the pin,
    # including a rotated certificate reported for another Android SDK range.
    counts = re.findall(r"^Number of signers: (\d+)[ \t\r]*$", output, re.M)
    fingerprints = re.findall(r"^Signer[^\r\n]* certificate SHA-256 digest: ([0-9a-fA-F]{64})[ \t\r]*$", output, re.M)
    if counts != ["1"] or not fingerprints or {value.lower() for value in fingerprints} != {expected_fingerprint()}:
        # These certificate fingerprints are public; never include private tool output.
        raise ValueError(f"APK {path.name} does not have the single pinned Android signer; public signer count={counts}, SHA256={fingerprints}")
    return fingerprints[0].lower()


def sign(directory: Path):
    inputs = sorted(path for path in directory.glob("*.apk") if not path.name.endswith("-signed.apk"))
    if not inputs:
        raise ValueError("No Android APKs available to sign")
    apksigner, zipalign = sdk_tool("apksigner"), sdk_tool("zipalign")
    with keystore() as key:
        for apk in inputs:
            aligned = key.parent / "aligned.apk"
            # Preserve the Android 16 KB native-library page alignment.
            run_tool([zipalign, "-f", "-P", "16", "4", str(apk), str(aligned)])
            signed = apk.with_name(apk.stem + "-signed.apk")
            run_tool([
                apksigner, "sign", "--ks", str(key), "--ks-key-alias", os.environ["CB_SECRET_ANDROID_ALIAS"],
                "--ks-pass", "env:CB_SECRET_ANDROID_KEY_STORE_PASSWORD",
                "--key-pass", "env:CB_SECRET_ANDROID_KEY_PASSWORD", "--out", str(signed), str(aligned),
            ])
            verify_apk(signed)
    print(f"Signed and verified {len(inputs)} APK(s) with the fixed Android certificate")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("check", "sign"))
    parser.add_argument("--directory", type=Path, default=Path("signed-apk"))
    args = parser.parse_args()
    check_keystore() if args.operation == "check" else sign(args.directory)
