#!/usr/bin/env python3
"""Read host/ansible/vars/payload.yml -- the one list of what a device installs.

Shared so bin/build-payload and bin/test_payload.py cannot read it differently.
Called as a script by the shell one:

    bin/payload_manifest.py host_tools
"""

import os
import sys

import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(REPO, "host", "ansible", "vars", "payload.yml")


def manifest():
    with open(PATH) as f:
        return yaml.safe_load(f)


def names(key):
    got = manifest()[key]
    if not got:
        raise SystemExit(f"{PATH}: {key} is empty")
    return got


if __name__ == "__main__":
    print("\n".join(names(sys.argv[1])))
