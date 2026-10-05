import json
import os

results = json.loads(os.environ["JOB_RESULTS_JSON"])
with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as out:
    out.write("## Build results\n\n| Job | Result |\n|---|---|\n")
    for name, value in results.items():
        out.write(f"| {name} | {value['result']} |\n")
    out.write("\nDownload **client-*** artifacts at the bottom of this run page. "
              "Extract the GitHub wrapper ZIP, then unlock the inner AES-256 ZIP with the "
              "password configured in ARTIFACT_ZIP_PASSWORD (use 7-Zip or Keka). "
              "Installers, build-info.json, SHA256SUMS.txt and corresponding source are encrypted. "
              "Distribute the corresponding source archive with your clients.\n")
failed = [name for name, value in results.items() if value["result"] in {"failure", "cancelled"}]
if failed:
    raise SystemExit("Some selected jobs did not complete: " + ", ".join(failed))
