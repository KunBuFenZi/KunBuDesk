"""Executed after checkout, before the runtime-generated composite action."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from common import ROOT, decode_config
from configure import apply
from pipeline import create_action, resolve_env


def stage(kind: str, source: Path):
    config = apply(source, decode_config(os.environ["CLIENT_CONFIG_B64"]))
    matrix = json.loads(os.environ["BUILD_MATRIX_JSON"])
    env = create_action(kind, ROOT / ".generated/action")
    env = resolve_env(env, matrix)
    env["CB_APP_NAME"] = config["app_name"]
    env["CB_KIND"] = kind
    # Use GitHub's environment file. User input is never placed in generated shell code.
    with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as output:
        for key, value in env.items():
            output.write(f"{key}={value}\n")
    print(f"Prepared official {kind} build steps and client defaults")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("kind")
    parser.add_argument("--source", type=Path, default=Path.cwd())
    args = parser.parse_args()
    stage(args.kind, args.source)
