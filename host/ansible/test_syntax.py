#!/usr/bin/env python3
"""`ansible-playbook --syntax-check` over every playbook here.

WHY IT LIVES IN `tests` AND NOT IN A DOCKERFILE. It used to run at fleet-control's image
build time, because that image carried the playbook. Two consequences of that placement,
both bad and neither about syntax:

  * It gated the REBUILD rather than the merge. A broken playbook merged, then failed the
    image build -- so the check fired after the thing it was protecting had already
    happened. `test_notifies.py` was already in `tests` for exactly this reason and its
    comment in that Dockerfile says so.
  * It tied the check to fleet-control carrying the playbook at all. coordinator#504 moves
    the play out of that image, which would have taken the check with it.

So it is here, beside the handler check, in the suite that runs in the notebook image --
which has ansible-core (2.16.3 at the time of writing) because the playbooks are driven
from that environment by hand as well as from the cluster.

GLOBBED, NOT LISTED. The version in the Dockerfile named four playbooks. A guard that
covers a file only because somebody remembered to add it is one new playbook away from
covering nothing, silently -- and `site.yaml` imports the others, so a list also hides
which of them is really being exercised. Every *.yaml at the top of host/ansible is a
playbook; roles and vars live in subdirectories.

NO SKIP IF ANSIBLE IS ABSENT. A check that quietly does not run is the failure mode this
file exists to remove, so a missing ansible-playbook is a failure with a message saying
what is missing.

What this does NOT catch, so nobody reads more into a pass than is there: a notify with no
matching handler (that is test_notifies.py), a task that is wrong rather than malformed,
and anything that only appears against a real device.

    python3 host/ansible/test_syntax.py
"""
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

failures = []


def check(name, ok, detail=""):
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(name)


exe = shutil.which("ansible-playbook")
if exe is None:
    print("FAIL ansible-playbook is not on PATH, so nothing was checked.\n"
          "     This suite runs in the JupyterHub notebook image, which has ansible-core.\n"
          "     Not skipped on purpose: a syntax check that silently does not run is worse\n"
          "     than no syntax check, because it reports success.")
    sys.exit(1)

playbooks = sorted(p.name for p in HERE.glob("*.yaml"))
check("there are playbooks to check at all", bool(playbooks), ", ".join(playbooks))

for name in playbooks:
    # --connection=local with a bare `localhost,` inventory: --syntax-check parses and
    # resolves imports without connecting, and these two keep it from even trying.
    r = subprocess.run(
        [exe, "--syntax-check", "-i", "localhost,", "--connection=local", name],
        cwd=HERE, capture_output=True, text=True,
    )
    detail = ""
    if r.returncode != 0:
        detail = (r.stderr.strip() or r.stdout.strip()).splitlines()
        detail = detail[-1] if detail else f"exit {r.returncode}"
    check(f"{name} parses, and its imports resolve", r.returncode == 0, detail)

if failures:
    print(f"\n{len(failures)} check(s) failed: {', '.join(failures)}")
    sys.exit(1)
print(f"\ntest_syntax: {len(playbooks)} playbook(s) parse")
