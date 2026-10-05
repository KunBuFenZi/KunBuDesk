"""Apply explicit source-level defaults; no binary patching or shell interpolation."""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import re
from urllib.parse import urlsplit


FIELDS = ("app_name", "id_server", "relay_server", "api_server", "key")


def validate(config: dict) -> dict:
    config = {field: config.get(field, "").strip() for field in FIELDS}
    name = config["app_name"]
    # Safe across Windows services, WiX, macOS paths, XML and shell packaging.
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,31}", name):
        raise ValueError("App name: use 1–32 ASCII letters, digits, _ or -, starting with a letter")
    if name.upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        raise ValueError("App name is a reserved Windows filename")
    for field in ("id_server", "relay_server"):
        value = config[field]
        if field == "id_server" and not value:
            raise ValueError("ID server is required (hostname/IP, optionally :port)")
        if not value:
            continue
        if not re.fullmatch(r"(?:[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?|\[[0-9A-Fa-f:]+\])(?::[0-9]{1,5})?", value):
            raise ValueError(f"{field}: use hostname/IP[:port], without http(s):// or a path")
        parsed = urlsplit("//" + value)
        try:
            if parsed.port is not None and not 1 <= parsed.port <= 65535:
                raise ValueError("Port out of range")
        except ValueError as error:
            raise ValueError(f"{field}: invalid port") from error
    if value := config["api_server"]:
        url = urlsplit(value)
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password:
            raise ValueError("API server must be an http(s) URL without embedded credentials")
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
            raise ValueError("API server contains control characters")
        try:
            if url.port is not None and not 1 <= url.port <= 65535:
                raise ValueError("Port out of range")
        except ValueError as error:
            raise ValueError("API server: invalid port") from error
    try:
        key = base64.b64decode(config["key"], validate=True)
    except (ValueError, base64.binascii.Error) as error:
        raise ValueError("Key must be the Base64 public key from id_ed25519.pub") from error
    if len(key) != 32:
        raise ValueError("Key must decode to a 32-byte Ed25519 public key")
    return config


def rust_string(value: str) -> str:
    # UTF-8 text is preserved; escaping is shared by JSON and Rust for these inputs.
    return json.dumps(value, ensure_ascii=False)


def replace_one(text: str, old: str, new: str, path: Path) -> str:
    if text.count(old) != 1:
        raise ValueError(f"Upstream changed: expected exactly one patch anchor in {path}: {old[:90]}")
    return text.replace(old, new, 1)


def apply(source: Path, raw_config: dict):
    config = validate(raw_config)
    name = config["app_name"]
    config_path = source / "libs/hbb_common/src/config.rs"
    text = config_path.read_text(encoding="utf-8")
    old = 'pub static ref APP_NAME: RwLock<String> = RwLock::new("RustDesk".to_owned());'
    text = replace_one(text, old, old.replace('"RustDesk"', rust_string(name)), config_path)
    old = 'pub static ref PROD_RENDEZVOUS_SERVER: RwLock<String> = RwLock::new("".to_owned());'
    text = replace_one(text, old, old.replace('""', rust_string(config["id_server"])), config_path)
    text, count = re.subn(r'pub const RS_PUB_KEY: &str = "[^"\n]*";',
                          lambda _: f'pub const RS_PUB_KEY: &str = {rust_string(config["key"])};', text)
    if count != 1:
        raise ValueError("Upstream changed: RS_PUB_KEY anchor")
    options = {"custom-rendezvous-server": config["id_server"], "key": config["key"]}
    if config["relay_server"]:
        options["relay-server"] = config["relay_server"]
    if config["api_server"]:
        options["api-server"] = config["api_server"]
    entries = ",\n".join(f"        ({rust_string(k)}.to_owned(), {rust_string(v)}.to_owned())" for k, v in options.items())
    old = "pub static ref DEFAULT_SETTINGS: RwLock<HashMap<String, String>> = Default::default();"
    new = "pub static ref DEFAULT_SETTINGS: RwLock<HashMap<String, String>> = RwLock::new(HashMap::from([\n" + entries + "\n    ]));"
    text = replace_one(text, old, new, config_path)
    config_path.write_text(text, encoding="utf-8")

    # Native display metadata. Keep executable/library/bundle identifiers and official
    # packaging paths stable so upstream packaging continues to work.
    replacements = {
        "flutter/android/app/src/main/AndroidManifest.xml": [
            ('android:label="RustDesk"', f'android:label="{name}"'),
            ('android:label="RustDesk Input"', f'android:label="{name} Input"')],
        "flutter/windows/runner/Runner.rc": [
            ('VALUE "ProductName", "RustDesk"', f'VALUE "ProductName", "{name}"'),
            ('VALUE "FileDescription", "RustDesk Remote Desktop"', f'VALUE "FileDescription", "{name} Remote Desktop"')],
        "flutter/linux/my_application.cc": [
            ('gtk_header_bar_set_title(header_bar, "rustdesk");', f'gtk_header_bar_set_title(header_bar, "{name}");'),
            ('gtk_window_set_title(window, "rustdesk");', f'gtk_window_set_title(window, "{name}");')],
        "flutter/macos/Runner/Info.plist": [],
    }
    for relative, pairs in replacements.items():
        path = source / relative
        text = path.read_text(encoding="utf-8")
        for old, new in pairs:
            text = replace_one(text, old, new, path)
        if relative.endswith("Info.plist"):
            # Xcode keeps PRODUCT_NAME=RustDesk so the official DMG commands work;
            # Finder's display name comes from CFBundleDisplayName.
            if "<key>CFBundleDisplayName</key>" in text:
                text, n = re.subn(r'(<key>CFBundleDisplayName</key>\s*<string>)[^<]*(</string>)',
                                  lambda m: m[1] + name + m[2], text)
                if n != 1:
                    raise ValueError("Upstream changed: CFBundleDisplayName")
            else:
                text = replace_one(text, '<plist version="1.0">\n<dict>',
                                   f'<plist version="1.0">\n<dict>\n\t<key>CFBundleDisplayName</key>\n\t<string>{name}</string>', path)
        path.write_text(text, encoding="utf-8")

    for relative in ("libs/portable/Cargo.toml", "Cargo.toml"):
        path = source / relative
        text = path.read_text(encoding="utf-8")
        text = text.replace('ProductName = "RustDesk"', f'ProductName = "{name}"')
        text = text.replace('FileDescription = "RustDesk Remote Desktop"', f'FileDescription = "{name} Remote Desktop"')
        path.write_text(text, encoding="utf-8")
    path = source / "res/rustdesk.desktop"
    text = path.read_text(encoding="utf-8")
    text = replace_one(text, "Name=RustDesk\n", f"Name={name}\n", path)
    path.write_text(text, encoding="utf-8")
    # Linux retains upstream executable/service/package identifiers. The display name
    # and config-directory name can change independently without breaking systemctl.
    path = source / "src/platform/linux.rs"
    text = path.read_text(encoding="utf-8")
    if "crate::get_app_name().to_lowercase()" not in text:
        raise ValueError("Upstream Linux service naming changed")
    text = text.replace("crate::get_app_name().to_lowercase()", '"rustdesk".to_owned()')
    path.write_text(text, encoding="utf-8")
    path = source / "src/core_main.rs"
    text = path.read_text(encoding="utf-8")
    text = replace_one(text, '.arg(&format!("{} --tray", crate::get_app_name().to_lowercase()))',
                       '.arg("rustdesk --tray")', path)
    path.write_text(text, encoding="utf-8")
    path = source / "res/msi/preprocess.py"
    text = path.read_text(encoding="utf-8")
    text = replace_one(text, "    update_license_file(app_name)\n",
                       "    # Preserve upstream copyright and license notices.\n", path)
    path.write_text(text, encoding="utf-8")
    # Keep AGPL copyright and license notices intact.
    return config


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    apply(args.source, json.loads(args.config.read_text(encoding="utf-8")))
    print("Client defaults and display name applied")
