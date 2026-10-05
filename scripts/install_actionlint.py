"""Download the pinned official actionlint release and verify its published checksum."""
from pathlib import Path
import hashlib
import json
import platform
import shutil
import tarfile
import tempfile
import urllib.request

from common import api

version = "1.7.7"
system = {"Darwin": "darwin", "Linux": "linux"}[platform.system()]
arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "amd64"}[platform.machine()]
filename = f"actionlint_{version}_{system}_{arch}.tar.gz"
release = api(f"repos/rhysd/actionlint/releases/tags/v{version}")
assets = {item["name"]: item["browser_download_url"] for item in release["assets"]}
with tempfile.TemporaryDirectory() as tmp:
    def download(name):
        path = Path(tmp) / name
        with urllib.request.urlopen(assets[name], timeout=60) as response, path.open("wb") as out:
            shutil.copyfileobj(response, out)
        return path
    checksum_file = download(f"actionlint_{version}_checksums.txt")
    expected = next(line.split()[0] for line in checksum_file.read_text().splitlines() if line.split()[-1] == filename)
    archive = download(filename)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
        raise ValueError("Official actionlint release checksum mismatch")
    with tarfile.open(archive) as tar:
        tar.extractall(Path(tmp) / "unpack", filter="data")
    destination = Path(".generated/actionlint")
    destination.parent.mkdir(exist_ok=True)
    shutil.copy2(Path(tmp) / "unpack/actionlint", destination)
    destination.chmod(0o755)
print("Installed verified official actionlint")
