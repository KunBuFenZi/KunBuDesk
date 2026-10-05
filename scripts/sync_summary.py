import os

with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as out:
    tag = os.environ.get("SYNC_TAG", "")
    if not tag:
        out.write("Upstream synchronization failed. Current source pins and client configuration were preserved. See the failed step.\n")
    elif os.environ.get("SYNC_CHANGED") == "true":
        out.write(f"Synchronized official stable release **{tag}** with validated source patches and official build recipes.\n")
    else:
        out.write(f"Already on official stable release **{tag}**.\n")
    out.write("\nClient configuration and custom scripts are preserved. "
              "Synchronization does not automatically start costly platform builds.\n")
