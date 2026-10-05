"""Adapt official job steps into local composite actions at build time.

Recipes live outside .github/workflows, so GITHUB_TOKEN can synchronize them without
requiring a personal access token with workflow-writing privileges.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re

import yaml
from common import ROOT, load_yaml

CHECKOUT = "actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5"
UPLOAD = "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"

JOBS = {
    "bridge": ("bridge.yml", "generate_bridge", "helper"),
    "topmost": ("third-party-RustDeskTempTopMostWindow.yml", "build-RustDeskTempTopMostWindow", "helper"),
    "windows": ("flutter-build.yml", "build-for-windows-flutter", "windows"),
    "windows_legacy": ("flutter-build.yml", "build-for-windows-sciter", "windows"),
    "macos": ("flutter-build.yml", "build-for-macOS", "macos"),
    "android": ("flutter-build.yml", "build-rustdesk-android", "android"),
    "android_universal": ("flutter-build.yml", "build-rustdesk-android-universal", "android"),
    "linux": ("flutter-build.yml", "build-rustdesk-linux", "linux"),
    "linux_legacy": ("flutter-build.yml", "build-rustdesk-linux-sciter", "linux"),
    "appimage": ("flutter-build.yml", "build-appimage", "linux"),
}


def matrix_env_key(key: str) -> str:
    return "CB_MATRIX_" + re.sub(r"[^A-Za-z0-9_]", "_", key).upper()


def rewrite(value):
    if isinstance(value, dict):
        return {key: rewrite(item) for key, item in value.items()}
    if isinstance(value, list):
        return [rewrite(item) for item in value]
    if not isinstance(value, str):
        return value
    value = re.sub(r"matrix\.job\.([A-Za-z0-9_-]+)",
                   lambda m: "env." + matrix_env_key(m[1]), value)
    value = value.replace("inputs.upload-artifact", "true").replace("inputs.upload-tag", "'custom'")
    for key in ("target", "configuration", "platform", "target_version"):
        value = value.replace("inputs." + key, "env." + matrix_env_key(key))
    value = re.sub(r"secrets\.([A-Z0-9_]+)", lambda m: "env.CB_SECRET_" + m[1], value)
    return value


def recipes(directory: Path = ROOT / "upstream") -> dict:
    return {name: load_yaml(directory / name) for name, _, _ in JOBS.values()}


def all_matrices(directory: Path = ROOT / "upstream") -> dict:
    data = recipes(directory)
    matrices = {}
    for kind, (filename, job_name, _) in JOBS.items():
        job = data[filename]["jobs"][job_name]
        matrix = job.get("strategy", {}).get("matrix", {}).get("job")
        if kind == "topmost":
            matrix = data["flutter-build.yml"]["jobs"]["build-RustDeskTempTopMostWindow"]["strategy"]["matrix"]["job"]
            matrix = [dict(item, configuration="Release", target_version="Windows10", arch="aarch64" if item["platform"] == "ARM64" else "x86_64") for item in matrix]
        if kind == "android_universal":
            matrix = [{"on": job["runs-on"], "arch": "universal"}]
        if not isinstance(matrix, list) or not matrix:
            raise ValueError(f"Upstream job matrix changed: {job_name}")
        for item in matrix:
            # Runner labels / flags become expressions inside trusted official recipes.
            # Require simple strings and forbid expression/shell delimiters on sync.
            if any(not isinstance(v, (str, int, bool)) or any(c in str(v) for c in "\n\r`$;&|<>") for v in item.values()):
                raise ValueError(f"Unsafe/unrecognized upstream matrix entry in {kind}")
        if kind == "linux_legacy":
            matrix = [item for item in matrix if item["arch"] == "armv7"]
        matrices[kind] = matrix
    return matrices


def select_matrices(platform: str, arch: str, directory: Path = ROOT / "upstream") -> dict:
    if platform not in {"all", "windows", "linux", "macos", "android"}:
        raise ValueError("Unknown platform")
    arch = {"arm64": "aarch64", "x64": "x86_64"}.get(arch, arch)
    if arch not in {"all", "x86_64", "aarch64", "x86", "armv7"}:
        raise ValueError("Unknown architecture")
    all_jobs = all_matrices(directory)
    selected = {}
    for kind, entries in all_jobs.items():
        job_platform = JOBS[kind][2]
        selected[kind] = [entry for entry in entries
                          if job_platform != "helper"
                          and (platform == "all" or platform == job_platform)
                          and (arch == "all" or entry["arch"] == arch)
                          and (kind != "android_universal" or arch == "all")]
    if not any(selected[kind] for kind in selected):
        raise ValueError(f"No supported targets for {platform}/{arch}")
    modern = any(selected[kind] for kind in ("windows", "linux", "macos", "android"))
    selected["bridge"] = all_jobs["bridge"] if modern else []
    if not any(item["arch"] == "aarch64" for item in selected["windows"]):
        selected["bridge"] = [item for item in selected["bridge"] if item["artifact-name"] == "bridge-artifact"]
    selected["topmost"] = [item for item in all_jobs["topmost"]
                           if item["arch"] in {item["arch"] for item in selected["windows"]}]
    return selected


def adapted_steps(kind: str, directory: Path = ROOT / "upstream") -> tuple[list, dict]:
    filename, job_name, platform = JOBS[kind]
    data = recipes(directory)[filename]
    job = data["jobs"][job_name]
    env = {**data.get("env", {}), **job.get("env", {})}
    env.update(UPLOAD_ARTIFACT="true", SIGN_BASE_URL="-2", MACOS_P12_BASE64="", TAG_NAME="custom")
    for key, value in list(env.items()):
        if isinstance(value, str) and "secrets." in value:
            if key == "ANDROID_SIGNING_KEY":
                env[key] = "${{ env.CB_SECRET_ANDROID_SIGNING_KEY }}"
            else:
                env[key] = ""
    steps = []
    for original in job["steps"]:
        step = copy.deepcopy(original)
        name = step.get("name", "")
        uses = step.get("uses", "")
        if uses.startswith("actions/checkout@") or uses.startswith("softprops/action-gh-release@"):
            continue
        # The official signing server is intentionally never called.
        if "Sign rustdesk" in name or "MSI template" in name or name == "Upload unsigned msi template":
            continue
        if name == "Upload unsigned" or name == "Upload unsigned macOS app":
            continue
        if platform == "macos" and ("codesign" in name.lower() or "sign key" in name or "notarize key" in name or "rcodesign" in name):
            continue
        if platform == "macos" and name == "Rename rustdesk":
            continue
        # Upload final outputs once, preserving the upstream intermediate libraries/debs.
        if uses.startswith("actions/upload-artifact@") and platform != "helper":
            if kind in ("android", "linux") and name in ("Upload Rustdesk library to Artifacts", "Upload deb"):
                pass
            else:
                continue
        if name == "Build pre-built MSI template":
            continue
        if kind == "linux" and name in ("Patch archlinux PKGBUILD", "Build archlinux package"):
            continue  # DEB/RPM/AppImage are the supported Linux packages.
        if platform == "macos" and name == "create unsigned dmg":
            step["run"] = '''set -euo pipefail
appdir=./flutter/build/macos/Build/Products/Release
python .custom-builder/scripts/macos_bundle.py "$appdir/RustDesk.app"
create-dmg --icon "$CB_APP_NAME.app" 200 190 --hide-extension "$CB_APP_NAME.app" --window-size 800 400 --app-drop-link 600 185 "rustdesk-$VERSION-${{ matrix.job.arch }}.dmg" "$appdir/$CB_APP_NAME.app"
'''
        if platform == "windows" and name == "Build msi":
            old = "python preprocess.py --arp -d ../../rustdesk"
            if old not in step["run"]:
                raise ValueError("Upstream MSI preprocess command changed")
            step["run"] = step["run"].replace(old, '''if ($env:CB_APP_NAME.ToLower() -ne 'rustdesk') {
  Move-Item ../../rustdesk/rustdesk.exe "../../rustdesk/$env:CB_APP_NAME.exe"
}
python preprocess.py --arp --app-name $env:CB_APP_NAME -d ../../rustdesk''')
        if name == "find Runner.res":
            step.pop("continue-on-error", None)
            step["run"] = '''python - <<'PY'
from pathlib import Path
import shutil
files = list(Path('flutter/build/windows').rglob('Runner.res'))
if files:
    shutil.copy2(files[0], 'libs/portable/Runner.res')
else:
    print('Runner.res is unavailable; portable packer uses its default resources')
PY
'''
        if "run" in step:
            step.setdefault("shell", "pwsh" if platform == "windows" or kind == "topmost" else "bash")
        if uses.startswith("actions/cache@"):
            step["uses"] = "actions/cache@0057852bfaa89a56745cba8c7296529d2fc39830"  # v4.3.0
        steps.append(rewrite(step))
    # Composite actions do not support secrets/matrix/workflow input contexts directly.
    encoded = json.dumps(steps)
    if "secrets." in encoded or "matrix." in encoded or "inputs." in encoded:
        raise ValueError(f"Unconverted expression in upstream job {kind}")
    if not steps:
        raise ValueError(f"No build steps for {kind}")
    return steps, rewrite(env)


def create_action(kind: str, destination: Path, directory: Path = ROOT / "upstream"):
    steps, env = adapted_steps(kind, directory)
    # env is set before invoking the action (composites lack top-level env).
    destination.mkdir(parents=True, exist_ok=True)
    action = {"name": f"Official RustDesk {kind}", "description": "Adapted from the pinned official RustDesk workflow", "runs": {"using": "composite", "steps": steps}}
    (destination / "action.yml").write_text(yaml.safe_dump(action, sort_keys=False, width=120), encoding="utf-8")
    return env


def resolve_env(values: dict, matrix: dict) -> dict:
    resolved = {}
    values = {**values, **{matrix_env_key(k): str(v) for k, v in matrix.items()}}
    # Upstream env values currently contain only direct env substitutions; fail on
    # future formulas rather than silently exporting unevaluated expressions.
    for key, value in values.items():
        value = str(value)
        value = re.sub(r"\$\{\{\s*env\.([A-Z0-9_]+)\s*\}\}",
                       lambda m: str(values.get(m[1], os.environ.get(m[1], ""))), value)
        if "${{" in value:
            raise ValueError(f"Unsupported upstream environment expression: {key}")
        if "\n" in value or "\r" in value:
            raise ValueError(f"Multiline upstream environment variable: {key}")
        resolved[key] = value
    return resolved
