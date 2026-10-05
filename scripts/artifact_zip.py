"""Seal downloadable files in AES-256 ZIPs without putting passwords in argv."""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile

import pyzipper


PASSWORD_ENV = "CB_ARCHIVE_PASSWORD"
SECRET_NAME = "ARTIFACT_ZIP_PASSWORD"


def require_password() -> bytes:
    value = os.environ.get(PASSWORD_ENV, "")
    if not value:
        raise ValueError(f"Missing repository Secret: {SECRET_NAME}; refusing unencrypted upload")
    return value.encode("utf-8")


def digest(stream) -> bytes:
    checksum = hashlib.sha256()
    while block := stream.read(1024 * 1024):
        checksum.update(block)
    return checksum.digest()


def encrypted_members(archive):
    members = archive.infolist()
    if not members or any(not item.flag_bits & 1 or item.wz_aes_strength != 3 for item in members):
        raise ValueError("Every ZIP member must use AES-256 encryption")
    return members


def seal(source: Path, destination: Path) -> Path:
    password = require_password()
    source, destination = source.absolute(), destination.absolute()
    if source.is_symlink():
        raise ValueError("Artifact input must not be a symbolic link")
    if source.is_dir():
        if destination.is_relative_to(source):
            raise ValueError("Encrypted output must be outside the input directory")
        files = sorted(path for path in source.rglob("*") if path.is_file())
        names = [path.relative_to(source).as_posix() for path in files]
    elif source.is_file():
        files, names = [source], [source.name]
        if source == destination:
            raise ValueError("Encrypted output must differ from input")
    else:
        raise ValueError("Artifact input does not exist")
    if not files or any(path.is_symlink() for path in files):
        raise ValueError("Artifact input must contain regular files")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".tmp", delete=False) as tmp:
        temporary = Path(tmp.name)
    try:
        # Installers and source tarballs are already compressed. Keep memory bounded.
        with pyzipper.AESZipFile(temporary, "w", compression=pyzipper.ZIP_STORED,
                                encryption=pyzipper.WZ_AES, allowZip64=True) as archive:
            archive.setpassword(password)
            archive.setencryption(pyzipper.WZ_AES, nbits=256)
            for path, name in zip(files, names):
                archive.write(path, name)
        with pyzipper.AESZipFile(temporary) as archive:
            archive.setpassword(password)
            encrypted_members(archive)
            if archive.namelist() != names:
                raise ValueError("Encrypted archive is incomplete")
            for path, name in zip(files, names):
                with path.open("rb") as original, archive.open(name) as restored:
                    if digest(original) != digest(restored):
                        raise ValueError("Encrypted archive failed content verification")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Sealed and verified {len(files)} files in an AES-256 ZIP")
    return destination


def unseal(source: Path, destination: Path):
    password = require_password()
    destination = destination.absolute()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with pyzipper.AESZipFile(source) as archive, \
            tempfile.TemporaryDirectory(dir=destination.parent) as tmp:
        archive.setpassword(password)
        members = encrypted_members(archive)
        staging = Path(tmp)
        for item in members:
            name = PurePosixPath(item.filename)
            if name.is_absolute() or ".." in name.parts or "\\" in item.filename or ":" in item.filename \
                    or item.is_dir() or (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("Unsafe encrypted ZIP member")
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(item) as incoming, target.open("wb") as outgoing:
                shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
        # Authentication succeeds for all members before any build input is replaced.
        for item in members:
            target = destination / item.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(staging / item.filename, target)
    print("Decrypted and authenticated build dependency")


def smoke():
    require_password()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        package = root / "package"
        package.mkdir()
        (package / "client.apk").write_bytes(b"temporary encryption probe")
        archive = seal(package, root / "client.zip")
        with pyzipper.AESZipFile(archive) as encrypted:
            try:
                encrypted.read("client.apk")
            except RuntimeError:
                pass
            else:
                raise ValueError("ZIP content was readable without a password")
        unseal(archive, root / "restored")
        if (root / "restored/client.apk").read_bytes() != (package / "client.apk").read_bytes():
            raise ValueError("ZIP roundtrip failed")
    print("Repository Secret verified; encrypted test artifact removed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("seal", "open"):
        subparser = commands.add_parser(command)
        subparser.add_argument("--input", type=Path, required=True)
        subparser.add_argument("--output", type=Path, required=True)
        if command == "open":
            subparser.add_argument("--remove-archive", action="store_true")
    commands.add_parser("check")
    commands.add_parser("smoke")
    args = parser.parse_args()
    if args.command == "seal":
        seal(args.input, args.output)
    elif args.command == "open":
        unseal(args.input, args.output)
        if args.remove_archive:
            args.input.unlink()
    elif args.command == "smoke":
        smoke()
    else:
        require_password()
        print("Artifact encryption Secret is available")
