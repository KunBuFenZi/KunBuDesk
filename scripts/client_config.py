"""Read private build defaults per job; never pass secret-derived job outputs."""
from __future__ import annotations

import json
import os

from common import ROOT, encode_config
from configure import FIELDS, validate


SECRET_ENV = {field: "CB_DEFAULT_" + field.upper() for field in FIELDS}
REQUIRED_FIELDS = ("app_name", "id_server", "key")


def load_client_config():
    policies = json.loads((ROOT / "config/client.json").read_text(encoding="utf-8"))
    if any(field in policies for field in FIELDS):
        raise ValueError("Client defaults belong in repository Secrets, not config/client.json")
    private = {field: os.environ.get(env, "").strip() for field, env in SECRET_ENV.items()}
    missing = ["CLIENT_" + field.upper() for field in REQUIRED_FIELDS if not private[field]]
    if missing:
        raise ValueError("Missing repository Secrets: " + ", ".join(missing))
    return validate({**policies, **private})


def export_client_config():
    destination = os.environ.get("GITHUB_ENV")
    if not destination:
        raise ValueError("GITHUB_ENV is required to load private build defaults")
    encoded = encode_config(load_client_config())
    # GitHub masks original Secrets automatically; the transformed value needs its
    # own mask before later steps can log their environment. It stays within a job.
    print("::add-mask::" + encoded, flush=True)
    with open(destination, "a", encoding="utf-8") as output:
        output.write("CLIENT_CONFIG_B64=" + encoded + "\n")
    print("Loaded private client defaults for this job")


if __name__ == "__main__":
    export_client_config()
