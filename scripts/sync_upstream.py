"""Synchronize the latest official stable release with validated recipes and source."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

from common import OFFICIAL_REPO, RECIPE_NAMES, ROOT, api, download_source, github_output
from configure import apply
from pipeline import JOBS, all_matrices, create_action, resolve_env, select_matrices


SMOKE_CONFIG = {
    "app_name": "BuildSmoke", "id_server": "example.invalid:21116",
    "relay_server": "example.invalid:21117", "api_server": "https://example.invalid",
    "key": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
}


def verify_recipes(directory: Path):
    matrices = all_matrices(directory)
    select_matrices("all", "all", directory)
    with tempfile.TemporaryDirectory() as tmp:
        for kind in JOBS:
            env = create_action(kind, Path(tmp) / kind, directory)
            for matrix in matrices[kind]:
                resolve_env(env, matrix)


def sync(force: bool = False):
    current = json.loads((ROOT / "upstream/lock.json").read_text(encoding="utf-8"))
    release = api(f"repos/{OFFICIAL_REPO}/releases/latest")
    if release["draft"] or release["prerelease"]:
        raise ValueError("GitHub returned a non-stable release")
    tag = release["tag_name"]
    if current["tag"] == tag and not force:
        github_output({"changed": "false", "tag": tag})
        print(f"Already on official stable release {tag}")
        return
    # GitHub's commit endpoint resolves annotated and lightweight tags to a commit.
    from urllib.parse import quote
    commit = api(f"repos/{OFFICIAL_REPO}/commits/{quote(tag, safe='')}")["sha"]
    submodule = api(f"repos/{OFFICIAL_REPO}/contents/libs/hbb_common?ref={commit}")
    if submodule.get("submodule_git_url", "").rstrip(".git") != "https://github.com/rustdesk/hbb_common":
        raise ValueError("Official submodule repository changed; review needed")
    candidate = {
        "repository": OFFICIAL_REPO, "channel": "stable", "tag": tag,
        "commit": commit, "hbb_common_commit": submodule["sha"],
        "release_url": release["html_url"],
        "synced_at": datetime.now(timezone.utc).isoformat(),
    }
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "source"
        download_source(candidate, source)
        recipes = Path(tmp) / "recipes"
        recipes.mkdir()
        for name in RECIPE_NAMES:
            shutil.copy2(source / ".github/workflows" / name, recipes / name)
        verify_recipes(recipes)
        # Validate exact patch anchors on the real new source, including the submodule.
        apply(source, SMOKE_CONFIG)
        candidate["recipe_sha256"] = {
            name: hashlib.sha256((recipes / name).read_bytes()).hexdigest() for name in RECIPE_NAMES
        }
        for name in RECIPE_NAMES:
            shutil.copy2(recipes / name, ROOT / "upstream" / name)
        (ROOT / "upstream/lock.json").write_text(json.dumps(candidate, indent=2) + "\n", encoding="utf-8")
    github_output({"changed": "true", "tag": tag})
    print(f"Synchronized official stable release {tag}, source pins and build recipes")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    sync(args.force)
