"""Use maintained Actions without letting stable upstream recipes downgrade them."""
from __future__ import annotations

from functools import lru_cache
import re

from common import ROOT


@lru_cache(maxsize=1)
def current_actions() -> dict[str, str]:
    # Maintainers move stable major tags forward for compatible minor updates.
    versions = {
        "Swatinem/rust-cache": "v2",
        "subosito/flutter-action": "v2",
        "lukka/run-vcpkg": "v11",
        "microsoft/setup-msbuild": "v3",
        "nttld/setup-ndk": "v1",
        "jlumbroso/free-disk-space": "v2",
    }
    # Read official Action versions from the real workflows so Dependabot updates
    # apply to generated recipes as well, without maintaining a second pin list.
    for path in sorted((ROOT / ".github/workflows").glob("*.yml")):
        for repo, ref in re.findall(r"uses:\s*(actions/[a-z0-9-]+)@([^\s#]+)", path.read_text(encoding="utf-8")):
            if repo in versions and versions[repo] != ref:
                raise ValueError(f"Conflicting workflow versions for {repo}")
            versions[repo] = ref
    # Download steps live in the generated recipes rather than static jobs.
    versions.setdefault("actions/download-artifact", "v8")
    return versions


def modern_action(uses: str) -> str:
    repo, separator, _ = uses.partition("@")
    ref = current_actions().get(repo)
    return f"{repo}@{ref}" if separator and ref else uses
