from __future__ import annotations

import json
import os

from common import ROOT, encode_config, github_output
from configure import FIELDS, validate
from pipeline import all_matrices, select_matrices
from android_signing import check_keystore


def prepare():
    defaults = json.loads((ROOT / "config/client.json").read_text(encoding="utf-8"))
    config = validate({field: os.environ.get("INPUT_" + field.upper(), "").strip() or defaults.get(field, "") for field in FIELDS})
    lock = json.loads((ROOT / "upstream/lock.json").read_text(encoding="utf-8"))
    selected = select_matrices(os.environ.get("INPUT_PLATFORM", "all"), os.environ.get("INPUT_ARCH", "all"))
    if selected["android"] or selected["android_universal"]:
        check_keystore()
    all_jobs = all_matrices()
    outputs = {"commit": lock["commit"], "version": lock["tag"], "config": encode_config(config)}
    for kind, entries in selected.items():
        outputs[kind + "_enabled"] = "true" if entries else "false"
        # A nonempty placeholder prevents matrix validation issues on skipped jobs.
        outputs[kind + "_matrix"] = {"job": entries or all_jobs[kind][:1]}
    github_output(outputs)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as out:
            out.write(f"## {config['app_name']} / RustDesk {lock['tag']}\n\n")
            out.write(f"Upstream commit: `{lock['commit']}`\n\n")
            out.write("| Platform | Architectures |\n|---|---|\n")
            for kind, entries in selected.items():
                if entries and kind not in ("bridge", "topmost"):
                    out.write(f"| {kind} | {', '.join(item['arch'] for item in entries)} |\n")
    print("Configuration validated; build targets selected")


if __name__ == "__main__":
    prepare()
