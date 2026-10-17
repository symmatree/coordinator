#!/usr/bin/env python3
"""`run-playbook.sh` end to end, with a fake artifact server and a stub ansible.

Runs the real script: a real HTTP server serving a real zip of a real tarball, a real
ssh-keygen against a real generated key, and a stub `ansible-playbook` that records what it
was handed. No cluster, no device, no network.

What that buys, and why it is worth the setup: every bug this script can have is in the
seams -- the shape of the artifact, the version it claims, the extra vars the play receives,
whether host-key checking survived the move out of the server. Reading the script shows none
of those. The things NOT covered are named at the bottom.
"""
import http.server
import json
import os
import pathlib
import subprocess
import sys
import tarfile
import tempfile
import threading
import zipfile

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

HERE = pathlib.Path(__file__).resolve().parent
SCRIPT = HERE / "run-playbook.sh"
REVISION = "a" * 40

failures = []


def check(label, ok, detail=""):
    print(f"  {'ok' if ok else 'FAIL'}: {label}" + (f" -- {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(label)


def make_artifact(tmp, revision=REVISION, ref="refs/heads/main"):
    """A zip holding a tarball holding `ansible/`, the way the workflow publishes it."""
    stage = tmp / "stage" / "ansible"
    (stage / "roles").mkdir(parents=True)
    (stage / "site.yaml").write_text("- hosts: all\n  tasks: []\n")
    (stage / "VERSION").write_text(
        f'ORG_OPENCONTAINERS_IMAGE_SOURCE="https://example.invalid/r.git"\n'
        f'ORG_OPENCONTAINERS_IMAGE_REVISION="{revision}"\n'
        f'FLEET_SOURCE_REF="{ref}"\n'
    )
    tarball = tmp / f"ansible-{revision}.tar.gz"
    with tarfile.open(tarball, "w:gz") as tf:
        tf.add(stage, arcname="ansible")
    zip_path = tmp / "artifact.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.write(tarball, arcname=tarball.name)
    return zip_path


def serve(zip_path):
    """Serve the zip, and record whether the request carried the token."""
    seen = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            seen["authorization"] = self.headers.get("authorization")
            body = zip_path.read_bytes()
            self.send_response(200)
            self.send_header("content-type", "application/zip")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, seen


STUB = """#!/usr/bin/env python3
import json, os, sys
KEYS = ("ANSIBLE_HOST_KEY_CHECKING", "ANSIBLE_SSH_ARGS", "ANSIBLE_TIMEOUT")
with open(RECORD, "w") as fh:
    json.dump({"argv": sys.argv[1:], "env": {k: os.environ.get(k) for k in KEYS}}, fh)
"""


def stub_ansible(bin_dir, record):
    """An `ansible-playbook` that writes down its argv and the env that matters."""
    stub = bin_dir / "ansible-playbook"
    stub.write_text(STUB.replace("RECORD", repr(str(record))))
    stub.chmod(0o755)


def run(script_env, tmp, args=("site.yaml", "10.0.0.9", "campod")):
    env = dict(os.environ, **script_env)
    env["PATH"] = f"{tmp / 'bin'}:{env['PATH']}"
    return subprocess.run(
        [str(SCRIPT), *args], capture_output=True, text=True, env=env, cwd=str(tmp)
    )


def main():
    if not SCRIPT.exists():
        raise SystemExit(f"missing {SCRIPT}")

    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        (tmp / "bin").mkdir()
        record = tmp / "ansible-invocation.json"
        stub_ansible(tmp / "bin", record)

        # Written with `cryptography` rather than `ssh-keygen -t`: generating a key makes
        # ssh-keygen look up the current uid to build the default comment, and CI runs as an
        # unmapped uid ("No user exists for uid 1001"). Deriving the public half -- which is
        # what the script under test actually does -- needs no such lookup, so that stays
        # real ssh-keygen.
        key = tmp / "id_ed25519"
        key.write_bytes(
            ed25519.Ed25519PrivateKey.generate().private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.OpenSSH,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        key.chmod(0o600)
        # Declared, not derived -- the value terraform wrote into the same 1Password item
        # as the private half. Trailing whitespace on purpose: it arrives through a Secret
        # and a workflow parameter, and a stray newline in authorized_keys is its own bug.
        declared_pub = (
            serialization.load_ssh_private_key(key.read_bytes(), password=None)
            .public_key()
            .public_bytes(
                encoding=serialization.Encoding.OpenSSH,
                format=serialization.PublicFormat.OpenSSH,
            )
            .decode()
        )
        srv, seen = serve(make_artifact(tmp))
        url = f"http://127.0.0.1:{srv.server_address[1]}/artifact.zip"
        base = {
            "PLAYBOOK_ARTIFACT_URL": url,
            "PLAYBOOK_REVISION": REVISION,
            "FLEET_GITHUB_TOKEN": "t0ken",
            "FLEET_SSH_KEY": str(key),
            "FLEET_SSH_PUBLIC_KEY": declared_pub + "\n",
        }

        # --- the happy path -------------------------------------------------------------
        out = run(base, tmp)
        check("a well-formed artifact runs the play", out.returncode == 0, out.stderr[-300:])
        check(
            "the version it actually unpacked is printed, with its ref",
            f"[step] playbook {REVISION} (refs/heads/main)" in out.stdout,
            out.stdout[-300:],
        )
        # Which driver ran, the same shape as which playbook ran. On a developer box there
        # is no /etc/container-image, and saying so is the point -- a run that cannot name
        # its driver must not print a blank and look like it did.
        check(
            "the driver names itself, or says it cannot",
            "[step] driver " in out.stdout
            and ("<no /etc/container-image>" in out.stdout or "<unknown>" not in out.stdout),
            [ln for ln in out.stdout.splitlines() if "driver" in ln],
        )
        check(
            "the download is authenticated",
            seen.get("authorization") == "Bearer t0ken",
            str(seen),
        )

        got = json.loads(record.read_text())
        argv, env = got["argv"], got["env"]
        check("the named playbook is what runs", argv[0] == "site.yaml", str(argv))
        check(
            "the host becomes a one-entry inventory",
            "-i" in argv and argv[argv.index("-i") + 1] == "10.0.0.9,",
            str(argv),
        )
        # The LAST -e: the caller's come first and the step's reserved four come after, so
        # that ansible's later-wins merge puts ours on top.
        extra = json.loads([argv[i + 1] for i, a in enumerate(argv) if a == "-e"][-1])
        check("device_role reaches the play", extra.get("device_role") == "campod", str(extra))
        check("the login account reaches the play", extra.get("ansible_user") == "pi")
        check(
            "the key is passed as the inventory variable, not --private-key",
            extra.get("ansible_ssh_private_key_file") == str(key) and "--private-key" not in argv,
            str(argv),
        )
        # Passed in, from the same 1Password item that holds the private half. Stripped,
        # because it arrives through a Secret and a workflow parameter and roles/ssh-access
        # writes it straight into authorized_keys.
        check(
            "fleet_ssh_public_key reaches the play, whitespace-stripped",
            extra.get("fleet_ssh_public_key") == declared_pub,
            repr(extra.get("fleet_ssh_public_key")),
        )

        # Deliberately not checked: physical devices on a private network that get
        # reflashed routinely, where the check only ever refused a freshly written card.
        check("host-key checking is off", env.get("ANSIBLE_HOST_KEY_CHECKING") == "False")
        args = env.get("ANSIBLE_SSH_ARGS") or ""
        check(
            "nothing is recorded and nothing is consulted",
            "StrictHostKeyChecking=no" in args
            and "UserKnownHostsFile=/dev/null" in args
            and "GlobalKnownHostsFile=/dev/null" in args,
            args,
        )
        check("the connect timeout is not ansible's 10s default", env.get("ANSIBLE_TIMEOUT") == "90")

        # --- extra vars -------------------------------------------------------------------
        record.unlink()
        out = run({**base, "PLAYBOOK_EXTRA_VARS": json.dumps(
            {"reimage_image_url": "https://x/i.img", "reimage_image_sha256": "ab" * 32})}, tmp)
        check("a run with extra vars succeeds", out.returncode == 0, out.stderr[-200:])
        argv1 = json.loads(record.read_text())["argv"]
        given = json.loads([argv1[i + 1] for i, a in enumerate(argv1) if a == "-e"][0])
        ours = json.loads([argv1[i + 1] for i, a in enumerate(argv1) if a == "-e"][-1])
        check(
            "they reach the play alongside the ones the step sets",
            given.get("reimage_image_url") == "https://x/i.img"
            and ours.get("device_role") == "campod"
            and ours.get("ansible_user") == "pi",
            f"{given} / {ours}",
        )

        record.unlink()
        # The reserved four are passed in a SECOND -e, after the caller's, and ansible
        # merges repeated extra-var arguments with the later winning. So this asserts the
        # ordering that makes that true, rather than a refusal the step no longer performs.
        out = run({**base, "PLAYBOOK_EXTRA_VARS": json.dumps(
            {"ansible_user": "attacker", "fleet_ssh_public_key": "ssh-ed25519 NOPE"})}, tmp)
        check("a run that tries to override the reserved vars still succeeds",
              out.returncode == 0, out.stderr[-200:])
        argv2 = json.loads(record.read_text())["argv"]
        es = [argv2[i + 1] for i, a in enumerate(argv2) if a == "-e"]
        check("the caller's extra vars come first and ours last", len(es) == 2, str(es))
        check(
            "so the device's user and trusted key are ours, not the caller's",
            json.loads(es[-1])["ansible_user"] == "pi"
            and json.loads(es[-1])["fleet_ssh_public_key"] == declared_pub,
            es[-1],
        )

        # The one thing ansible does NOT do: given a string that is not JSON it falls back
        # to key=value parsing and runs the play anyway, so a typo would converge a device
        # with the variable absent. Checked here because nothing downstream will.
        record.unlink()
        for bad, why in ((" not json ", "not JSON"), ('["a"]', "a JSON array")):
            res = run({**base, "PLAYBOOK_EXTRA_VARS": bad}, tmp)
            check(
                f"extra vars that are {why} fail before the play runs",
                res.returncode != 0 and not record.exists(),
                f"rc={res.returncode} {res.stderr.strip()[-120:]}",
            )

        # Absent is the normal case -- a converge sets none.
        res = run(base, tmp)
        check("no extra vars at all is fine", res.returncode == 0, res.stderr[-200:])

        # --- the refusals ---------------------------------------------------------------
        record.unlink()
        srv2, _ = serve(make_artifact(tmp / "other", revision="b" * 40))
        (tmp / "other").mkdir(exist_ok=True)
        wrong = run(
            {**base, "PLAYBOOK_ARTIFACT_URL": f"http://127.0.0.1:{srv2.server_address[1]}/a.zip"},
            tmp,
        )
        check(
            "an artifact carrying another revision is refused, not run",
            wrong.returncode != 0 and not record.exists(),
            f"rc={wrong.returncode} stderr={wrong.stderr.strip()[-200:]}",
        )
        check(
            "and it says both revisions",
            "b" * 40 in wrong.stderr and REVISION in wrong.stderr,
            wrong.stderr.strip()[-200:],
        )

        for missing in ("PLAYBOOK_REVISION", "FLEET_GITHUB_TOKEN", "FLEET_SSH_KEY",
                        "FLEET_SSH_PUBLIC_KEY"):
            env_missing = {k: v for k, v in base.items() if k != missing}
            res = run(env_missing, tmp)
            check(
                f"missing {missing} fails before anything runs",
                res.returncode != 0 and missing in res.stderr,
                res.stderr.strip()[-160:],
            )

    # NOT COVERED, so nobody reads more into a pass than is there: that ansible-core in the
    # step image can actually run these playbooks (host/ansible/test_syntax.py), that the
    # WorkflowTemplate passes these parameters correctly, and anything about a real device.
    if failures:
        print(f"\n{len(failures)} failed: {failures}")
        return 1
    print("\nall passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
