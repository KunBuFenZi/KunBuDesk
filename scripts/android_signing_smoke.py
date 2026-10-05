"""Exercise real SDK signing and packaging using the repository's fixed key.

The tiny test APK is built in temporary storage and is never uploaded.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile

from android_signing import check_keystore, expected_fingerprint, run_tool, sdk_tool, sign
from common import encode_config
from package import package
from sync_upstream import SMOKE_CONFIG


def smoke():
    check_keystore()
    aapt = Path(sdk_tool("aapt"))
    platforms = [path for path in (aapt.parent.parent.parent / "platforms").glob("android-*/android.jar")
                 if re.fullmatch(r"android-\d+(?:\.\d+)*", path.parent.name)]
    if not platforms:
        raise ValueError("Android SDK platform jar unavailable")
    platform = max(platforms, key=lambda path: tuple(int(value) for value in re.findall(r"\d+", path.parent.name)))
    with tempfile.TemporaryDirectory(dir=os.environ.get("RUNNER_TEMP")) as tmp:
        source = Path(tmp)
        manifest = source / "AndroidManifest.xml"
        manifest.write_text('''<manifest xmlns:android="http://schemas.android.com/apk/res/android"
package="cn.kjxtec.kunbudesk.signingtest" android:versionCode="1" android:versionName="1.0">
<uses-sdk android:minSdkVersion="23" android:targetSdkVersion="35"/>
<application android:hasCode="false" android:label="Signing smoke"/>
</manifest>''', encoding="utf-8")
        directory = source / "signed-apk"
        directory.mkdir()
        run_tool([str(aapt), "package", "-f", "-M", str(manifest), "-I", str(platform), "-F", str(directory / "rustdesk-signing-smoke.apk")])
        sign(directory)
        os.environ["CLIENT_CONFIG_B64"] = encode_config(SMOKE_CONFIG)
        package("android", source)
        info = json.loads((source / ".custom-builder/dist/build-info.json").read_text())
        assert info["android_certificate_sha256"] == expected_fingerprint()
    print("Real APK signing, certificate pinning and packaging passed; test APK removed")


if __name__ == "__main__":
    smoke()
