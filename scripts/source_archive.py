from __future__ import annotations

import json
import os
from pathlib import Path
import tarfile

from common import ROOT, decode_config
from configure import apply


def include_source_member(member):
    path = Path(member.name)
    if any(part in {".git", ".venv", "__pycache__", "dist", ".generated", ".signing"} for part in path.parts):
        return None
    if path.suffix.lower() in {".jks", ".keystore", ".p12", ".pfx"}:
        return None
    return member


def create(source: Path):
    config = apply(source, decode_config(os.environ["CLIENT_CONFIG_B64"]))
    (source / "custom-build-config.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    archive = output / f"{config['app_name']}-corresponding-source.tar.gz"

    with tarfile.open(archive, "w:gz") as tar:
        tar.add(source, arcname="source", filter=include_source_member)
    print("Archived corresponding patched source, submodule, build recipes and scripts")


if __name__ == "__main__":
    create(Path.cwd())
