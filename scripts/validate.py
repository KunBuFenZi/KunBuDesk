from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile

import yaml
from common import RECIPE_NAMES, ROOT, download_source
from configure import FIELDS, apply
from pipeline import JOBS, all_matrices, create_action, matrix_env_key, resolve_env
from sync_upstream import SMOKE_CONFIG, verify_recipes


def validate(source: Path | None = None, lint_directory: Path | None = None):
    lock = json.loads((ROOT / "upstream/lock.json").read_text(encoding="utf-8"))
    for name in RECIPE_NAMES:
        if hashlib.sha256((ROOT / "upstream" / name).read_bytes()).hexdigest() != lock["recipe_sha256"][name]:
            raise ValueError(f"Recipe checksum mismatch: {name}")
    verify_recipes(ROOT / "upstream")
    with tempfile.TemporaryDirectory() as tmp:
        if source is None:
            source = Path(tmp) / "source"
            download_source(lock, source)
        # Do not mutate a caller-supplied checkout during verification.
        import shutil
        copy = Path(tmp) / "patched"
        shutil.copytree(source, copy)
        config = apply(copy, SMOKE_CONFIG)
        text = (copy / "libs/hbb_common/src/config.rs").read_text(encoding="utf-8")
        for field in FIELDS:
            value = config[field]
            if value and value not in text:
                raise AssertionError("Expected client defaults are missing from patched source")
        linux = (copy / "src/platform/linux.rs").read_text(encoding="utf-8")
        if "let app_name_lower = crate::get_app_name().to_lowercase();" not in linux:
            raise AssertionError("Linux config-copy directory must still follow the custom App name")
        if 'let app_name = "rustdesk".to_owned();' not in linux:
            raise AssertionError("Linux service management must use the installed upstream service name")
        manifest = (copy / "flutter/android/app/src/main/AndroidManifest.xml").read_text(encoding="utf-8")
        if 'android:scheme="buildsmoke"' not in manifest:
            raise AssertionError("Android URI scheme must match the runtime App name")
        if lint_directory:
            lint_directory.mkdir(parents=True, exist_ok=True)
            for kind, matrices in all_matrices().items():
                env = create_action(kind, Path(tmp) / kind)
                action = yaml.safe_load((Path(tmp) / kind / "action.yml").read_text(encoding="utf-8"))
                env = resolve_env(env, matrices[0])
                env.update(CB_APP_NAME="BuildSmoke", CB_KIND=kind)
                for key in ("ANDROID_SIGNING_KEY", "ANDROID_ALIAS", "ANDROID_KEY_STORE_PASSWORD", "ANDROID_KEY_PASSWORD"):
                    env["CB_SECRET_" + key] = ""
                fixture = {
                    "name": kind, "on": "workflow_dispatch",
                    "env": env,
                    "jobs": {"build": {"runs-on": "windows-2022" if kind.startswith("windows") or kind == "topmost" else "ubuntu-22.04", "steps": action["runs"]["steps"]}},
                }
                (lint_directory / f"{kind}.yml").write_text(yaml.safe_dump(fixture, sort_keys=False, width=120), encoding="utf-8")
    print("Verified all official recipes, target matrices, checksums and source patches")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path)
    parser.add_argument("--lint-directory", type=Path)
    args = parser.parse_args()
    validate(args.source, args.lint_directory)
