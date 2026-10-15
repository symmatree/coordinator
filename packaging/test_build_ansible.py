#!/usr/bin/env python3
"""`packaging/build-ansible` produces an archive that can say what it is.

The point of the artifact is not packing, it is PROVENANCE. Baking the playbooks into
fleet-control's image clamps two revisions together, so `GET /build` answers "which
playbook ran" with one lookup. Publishing them separately unclamps that on purpose, and
the VERSION file plus a run recording what it unpacked are what carry the property
across. An archive that packs correctly but cannot say which commit it came from has
lost the only thing the versioning was for -- so that is what this checks hardest.

Standalone and exiting non-zero, like the rest of the suite; test.sh runs it.
"""
import os
import pathlib
import subprocess
import sys
import tarfile
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
BUILD = HERE / "build-ansible"

# The keys coord-version emits and fleet-control's manifest.ts already parses. A second
# spelling here would mean a second parser in the one place that aggregates versions, so
# these names are the contract rather than an implementation detail.
REQUIRED_KEYS = {
    "ORG_OPENCONTAINERS_IMAGE_SOURCE",
    "ORG_OPENCONTAINERS_IMAGE_REVISION",
    "FLEET_SOURCE_REF",
}

failures = []


def check(label, ok, detail=""):
    if ok:
        print(f"  ok: {label}")
    else:
        print(f"  FAIL: {label}{(' -- ' + detail) if detail else ''}")
        failures.append(label)


def build(out_dir):
    out = subprocess.run(
        [str(BUILD), str(out_dir)], capture_output=True, text=True, cwd=str(REPO)
    )
    if out.returncode != 0:
        print(out.stdout)
        print(out.stderr, file=sys.stderr)
        raise SystemExit(f"build-ansible failed with {out.returncode}")
    return pathlib.Path(out.stdout.strip())


def parse_version(text):
    found = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        key, _, value = line.partition("=")
        found[key] = value.strip('"')
    return found


def main():
    if not BUILD.exists():
        raise SystemExit(f"missing {BUILD}")

    with tempfile.TemporaryDirectory() as tmp:
        tarball = build(pathlib.Path(tmp) / "a")
        check("the script prints a path that exists", tarball.is_file(), str(tarball))

        with tarfile.open(tarball) as tf:
            names = tf.getnames()
            version_text = tf.extractfile("ansible/VERSION").read().decode()

        # The playbooks themselves. site.yaml imports the others, so its presence alone
        # would not tell us the imported ones came along.
        for play in ("site.yaml", "provision.yaml", "deploy.yaml", "reimage.yaml"):
            check(f"carries {play}", f"ansible/{play}" in names)
        check("carries roles/", any(n.startswith("ansible/roles/") for n in names))
        check(
            "carries group_vars/", any(n.startswith("ansible/group_vars/") for n in names)
        )

        # Not the tests beside them, and nothing a local test run generated. The second
        # of these is also what keeps the build reproducible.
        check(
            "excludes the tests",
            not any("test_" in n and n.endswith(".py") for n in names),
            str([n for n in names if n.endswith(".py")]),
        )
        check(
            "excludes __pycache__",
            not any("__pycache__" in n for n in names),
        )

        # The whole reason it is versioned.
        found = parse_version(version_text)
        check(
            "VERSION carries coord-version's keys",
            REQUIRED_KEYS <= set(found),
            f"missing {sorted(REQUIRED_KEYS - set(found))}",
        )
        revision = found.get("ORG_OPENCONTAINERS_IMAGE_REVISION", "")
        check(
            "the revision is a full sha, not an abbreviation",
            len(revision) == 40 and all(c in "0123456789abcdef" for c in revision),
            revision,
        )
        # Fully qualified: refs/heads/main, not main. docs/build-and-provenance.md records
        # why the abbreviated spelling cannot be used.
        ref = found.get("FLEET_SOURCE_REF", "")
        check("the ref is fully qualified", ref.startswith("refs/"), ref)
        check(
            "the filename names the same revision it carries",
            tarball.name == f"ansible-{revision}.tar.gz",
            tarball.name,
        )

        # Same commit, same bytes. Without this the artifact's identity is a claim rather
        # than a property, and two builds of one commit could differ.
        again = build(pathlib.Path(tmp) / "b")
        check(
            "the same commit packs to the same bytes",
            tarball.read_bytes() == again.read_bytes(),
        )

        # Overriding the identity is how CI stamps it; a hand run falls back to git.
        env = dict(os.environ, PAYLOAD_COMMIT="0" * 40, PAYLOAD_REF="refs/heads/probe")
        out = subprocess.run(
            [str(BUILD), str(pathlib.Path(tmp) / "c")],
            capture_output=True, text=True, cwd=str(REPO), env=env,
        )
        stamped = pathlib.Path(out.stdout.strip())
        with tarfile.open(stamped) as tf:
            stamped_version = parse_version(
                tf.extractfile("ansible/VERSION").read().decode()
            )
        check(
            "PAYLOAD_COMMIT and PAYLOAD_REF reach the archive",
            stamped_version.get("ORG_OPENCONTAINERS_IMAGE_REVISION") == "0" * 40
            and stamped_version.get("FLEET_SOURCE_REF") == "refs/heads/probe",
            str(stamped_version),
        )

    if failures:
        print(f"\n{len(failures)} failed: {failures}")
        return 1
    print("\nall passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
