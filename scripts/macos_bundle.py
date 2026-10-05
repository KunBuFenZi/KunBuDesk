import os
from pathlib import Path
import plistlib
import subprocess
import sys


app = Path(sys.argv[1])
name = os.environ["CB_APP_NAME"]
info = app / "Contents/Info.plist"
with info.open("rb") as file:
    data = plistlib.load(file)
old_executable = app / "Contents/MacOS" / data["CFBundleExecutable"]
new_executable = old_executable.with_name(name)
if old_executable != new_executable:
    old_executable.rename(new_executable)
data["CFBundleExecutable"] = name
data["CFBundleName"] = name
data["CFBundleDisplayName"] = name
with info.open("wb") as file:
    plistlib.dump(data, file)
renamed = app.with_name(name + ".app")
if app != renamed:
    app.rename(renamed)
# Preserve Flutter's entitlements while replacing the signature affected by branding.
subprocess.run(["codesign", "--force", "--deep", "--sign", "-", "--entitlements",
                "flutter/macos/Runner/Release.entitlements", str(renamed)], check=True)
print("Prepared custom macOS application bundle (ad-hoc signed)")
