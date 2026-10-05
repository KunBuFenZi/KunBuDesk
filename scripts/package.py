"""Collect complete outputs, use the App name in filenames, and record provenance."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil

from common import ROOT, decode_config


PATTERNS = {
    "windows": ["SignOutput/*.exe", "SignOutput/*.msi"],
    "windows_legacy": ["SignOutput/*.exe"],
    "macos": ["rustdesk-*.dmg"],
    "linux": ["rustdesk-*.deb", "rustdesk-*.rpm"],
    "linux_wayland": ["rustdesk-unattended-wayland-*-*.deb"],
    "linux_legacy": ["rustdesk-*-sciter.deb"],
    "appimage": ["appimage/rustdesk-*.AppImage"],
    "android": ["signed-apk/*-signed.apk", "signed-apk/rustdesk-*.apk"],
    "android_universal": ["signed-apk/*-signed.apk", "signed-apk/rustdesk-*.apk"],
}


def package(kind: str, source: Path):
    config = decode_config(os.environ["CLIENT_CONFIG_B64"])
    destination = source / ".custom-builder/dist"  # outside official package globs
    destination.mkdir(parents=True, exist_ok=True)
    files = set()
    for pattern in PATTERNS[kind]:
        files.update(path for path in source.glob(pattern) if path.is_file())
    if kind.startswith("android"):
        # When custom signing is enabled, do not distribute the debug-signed input APK.
        signed = {path for path in files if path.name.endswith("-signed.apk")}
        if os.environ.get("CB_SECRET_ANDROID_SIGNING_KEY"):
            if not signed:
                raise ValueError("Android signing configured but no signed APK produced")
            files = signed
    if not files:
        raise ValueError(f"No final output files produced for {kind}")
    if kind == "windows" and {path.suffix for path in files} != {".exe", ".msi"}:
        raise ValueError("Windows Flutter build must produce both EXE and MSI")
    checksums = []
    for path in sorted(files):
        filename = config["app_name"] + path.name[len("rustdesk"):] if path.name.startswith("rustdesk") else path.name
        target = destination / filename
        shutil.copy2(path, target)
        checksums.append(hashlib.sha256(target.read_bytes()).hexdigest() + "  " + filename)
    (destination / "SHA256SUMS.txt").write_text("\n".join(checksums) + "\n", encoding="utf-8")
    lock = json.loads((ROOT / "upstream/lock.json").read_text(encoding="utf-8"))
    info = {
        "app_name": config["app_name"], "rustdesk_version": lock["tag"],
        "upstream_commit": lock["commit"], "hbb_common_commit": lock["hbb_common_commit"],
        "builder_commit": os.environ.get("GITHUB_SHA", ""), "platform_job": kind,
        "architecture": os.environ.get("CB_MATRIX_ARCH", ""),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "run_url": f"https://github.com/{os.environ.get('GITHUB_REPOSITORY', '')}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}",
        "android_signing": ("custom-keystore" if os.environ.get("CB_SECRET_ANDROID_SIGNING_KEY") else "debug-key") if kind.startswith("android") else None,
        "files": [path.name for path in destination.iterdir() if path.suffix != ".json"],
    }
    if kind == "linux_wayland":
        info.update(experimental=True, build_variant="unattended-wayland", capture_backend="drm")
    (destination / "build-info.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    print(f"Packaged {len(files)} files for {kind}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=PATTERNS)
    parser.add_argument("--source", type=Path, default=Path.cwd())
    args = parser.parse_args()
    package(args.kind, args.source)
