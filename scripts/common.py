"""Shared, intentionally small helpers for build and upstream synchronization."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import shutil
import tarfile
import tempfile
import urllib.request

import yaml

ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_REPO = "rustdesk/rustdesk"
RECIPE_NAMES = (
    "flutter-build.yml", "bridge.yml", "third-party-RustDeskTempTopMostWindow.yml"
)


class YamlLoader(yaml.SafeLoader):
    pass


# GitHub's `on` key is a string, not YAML 1.1's boolean True.
YamlLoader.yaml_implicit_resolvers = {
    key: [(tag, regex) for tag, regex in values if tag != "tag:yaml.org,2002:bool"]
    for key, values in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
YamlLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool", re.compile(r"^(?:true|false)$", re.I), list("tTfF")
)


def load_yaml(path: Path):
    return yaml.load(path.read_text(encoding="utf-8"), Loader=YamlLoader)


def api(path: str):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "rustdesk-custom-build"}
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request("https://api.github.com/" + path, headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def download_source(lock: dict, destination: Path):
    """Fetch the pinned source including the pinned submodule; reject unsafe tar paths."""
    if destination.exists():
        raise ValueError(f"Destination already exists: {destination}")
    sha = lock["commit"]
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Invalid upstream commit")
    _extract_repo(OFFICIAL_REPO, sha, destination)
    submodule = lock["hbb_common_commit"]
    if not re.fullmatch(r"[0-9a-f]{40}", submodule):
        raise ValueError("Invalid hbb_common commit")
    empty = destination / "libs/hbb_common"
    if empty.exists():
        empty.rmdir()
    _extract_repo("rustdesk/hbb_common", submodule, empty)


def _extract_repo(repo: str, sha: str, destination: Path):
    url = f"https://codeload.github.com/{repo}/tar.gz/{sha}"
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "source.tar.gz"
        with urllib.request.urlopen(url, timeout=120) as response, archive.open("wb") as output:
            shutil.copyfileobj(response, output)
        unpack = Path(tmp) / "unpack"
        with tarfile.open(archive) as tar:
            # Available since Python 3.12; blocks traversal, unsafe links and special files.
            tar.extractall(unpack, filter="data")
        roots = list(unpack.iterdir())
        if len(roots) != 1 or not roots[0].is_dir():
            raise ValueError("Unexpected upstream archive structure")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(roots[0]), destination)


def github_output(values: dict):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as output:
            for key, value in values.items():
                if not isinstance(value, str):
                    value = json.dumps(value, separators=(",", ":"), ensure_ascii=True)
                if "\n" in value or "\r" in value:
                    raise ValueError("GitHub output must be single-line")
                output.write(f"{key}={value}\n")


def encode_config(config: dict) -> str:
    return base64.b64encode(json.dumps(config, ensure_ascii=True).encode()).decode()


def decode_config(value: str) -> dict:
    return json.loads(base64.b64decode(value, validate=True))
