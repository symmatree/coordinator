#!/usr/bin/env python3
"""Check a payload tarball against what the ansible roles install.

Run by .github/workflows/host-payload.yaml after bin/build-payload, and standalone:

    bin/build-payload /tmp/p && bin/test_payload.py /tmp/p/rekon-host-*.tar.gz

With no argument it builds one itself into a temp dir.
"""

import glob
import os
import subprocess
import sys
import tarfile
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from payload_manifest import REPO, names  # noqa: E402

fails = []


def check(ok, msg):
    if not ok:
        fails.append(msg)


def main(argv):
    tarball = argv[1] if len(argv) > 1 else None
    tmp = None
    if tarball is None:
        tmp = tempfile.mkdtemp()
        built = subprocess.run(
            [os.path.join(REPO, "bin", "build-payload"), tmp],
            capture_output=True, text=True,
        )
        if built.returncode != 0:
            print(f"FAIL: bin/build-payload exited {built.returncode}")
            print(built.stderr.strip())
            return 1
        tarball = built.stdout.strip()

    tools = names("host_tools")
    roles = names("stack_roles")

    # The manifest is the only list, so these catch it naming something that is gone
    # and a stack role that was added without being packed.
    for t in tools:
        check(os.path.isfile(f"{REPO}/bin/{t}"), f"manifest names bin/{t}, which does not exist")
    on_disk = {d for d in os.listdir(f"{REPO}/stacks") if os.path.isdir(f"{REPO}/stacks/{d}")}
    check(set(roles) == on_disk, f"stack_roles {sorted(roles)} != stacks/ dirs {sorted(on_disk)}")

    with tarfile.open(tarball) as tf:
        members = {m.name: m for m in tf.getmembers()}
    entries = set(members)
    check(all(n == "rekon-host" or n.startswith("rekon-host/") for n in entries),
          "tarball has entries outside rekon-host/")

    got_bin = {n.split("/")[-1] for n in entries if n.startswith("rekon-host/bin/")}
    check(got_bin == set(tools), f"tarball bin/ {sorted(got_bin)} != manifest {sorted(tools)}")
    for t in tools:
        m = members.get(f"rekon-host/bin/{t}")
        check(m is not None and m.mode & 0o111, f"bin/{t} is not executable in the tarball")

    # roles/coord-stack installs every *.container and <role>-stack.target, and fails
    # if there is no *.container -- so a payload without them converges to nothing.
    for role in roles:
        want = set(os.listdir(f"{REPO}/stacks/{role}"))
        got = {n.split("/")[-1] for n in entries if n.startswith(f"rekon-host/stacks/{role}/")}
        check(got == want, f"stacks/{role}: tarball {sorted(got)} != repo {sorted(want)}")
        check(any(n.endswith(".container") for n in got), f"stacks/{role}: no *.container")
        check(f"{role}-stack.target" in got, f"stacks/{role}: no {role}-stack.target")

    check("rekon-host/VERSION" in entries, "no VERSION in the tarball")

    # bin/ also holds tests, a README and bench-only scripts. None are installed.
    for n in sorted(entries):
        leaf = n.split("/")[-1]
        check(not (leaf.startswith("test_") or leaf.endswith((".py", ".md"))
                   or "__pycache__" in n or leaf == ".git"),
              f"{n} is in the payload and should not be")

    if tmp:
        for f in glob.glob(f"{tmp}/*"):
            os.unlink(f)
        os.rmdir(tmp)

    for f in fails:
        print(f"FAIL: {f}")
    print(f"{'FAIL' if fails else 'PASS'}: payload, {len(fails)} problem(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
