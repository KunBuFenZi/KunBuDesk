from __future__ import annotations

import json
import os
from pathlib import Path
import tarfile

from common import ROOT, decode_config
from configure import apply


def create(source: Path):
    config = apply(source, decode_config(os.environ["CLIENT_CONFIG_B64"]))
    (source / "custom-build-config.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    archive = output / f"{config['app_name']}-corresponding-source.tar.gz"

    def include(member):
        parts = Path(member.name).parts
        if any(part in {".git", ".venv", "__pycache__", "dist", ".generated"} for part in parts):
            return None
        return member

    with tarfile.open(archive, "w:gz") as tar:
        tar.add(source, arcname="source", filter=include)
    print("Archived corresponding patched source, submodule, build recipes and scripts")


if __name__ == "__main__":
    create(Path.cwd())
