#!/usr/bin/env python3
"""Check that every handler a role notifies is defined in the same playbook.

Handlers are resolved PER PLAY. A notify whose handler is defined in a role that runs
in the other playbook is a fatal "requested handler was not found" -- but only on the
run that actually notifies it, and these notifies all fire on `changed`. So an
already-converged device reports ok and sails past, and the failure appears on a
first-time converge, which is the run nobody does twice.

That is how six of them shipped in #453: the split moved tasks/main.yml and left
handlers/ behind.

Static check -- no device, no ansible, no network.

    python3 host/ansible/test_notifies.py
"""
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROLES = HERE / "roles"
PLAYBOOKS = ["provision.yaml", "deploy.yaml"]

failures = []


def walk(tasks):
    """Every task, descending into block/rescue/always."""
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for key in ("block", "rescue", "always"):
            yield from walk(t.get(key))


def load(path):
    return yaml.safe_load(path.read_text()) if path.exists() else None


def role_names(playbook):
    """Roles a playbook runs, following include_role one level."""
    names = set()
    for play in load(HERE / playbook) or []:
        for entry in play.get("roles") or []:
            names.add(entry["role"] if isinstance(entry, dict) else entry)
    for role in list(names):
        for task in walk(load(ROLES / role / "tasks" / "main.yml")):
            inc = task.get("ansible.builtin.include_role") or task.get("include_role")
            if isinstance(inc, dict) and inc.get("name"):
                names.add(inc["name"])
    return names


def notified_by(role):
    out = set()
    for task in walk(load(ROLES / role / "tasks" / "main.yml")):
        n = task.get("notify")
        if isinstance(n, str):
            out.add(n)
        elif isinstance(n, list):
            out |= {x for x in n if isinstance(x, str)}
    return out


def defined_by(role):
    return {
        t["name"]
        for t in walk(load(ROLES / role / "handlers" / "main.yml"))
        if t.get("name")
    }


for playbook in PLAYBOOKS:
    roles = role_names(playbook)
    available = set()
    for r in roles:
        available |= defined_by(r)
    for role in sorted(roles):
        for handler in sorted(notified_by(role)):
            if handler in available:
                print(f"ok   {playbook}: {role} -> {handler!r}")
            else:
                owners = [r.name for r in ROLES.iterdir()
                          if r.is_dir() and handler in defined_by(r.name)]
                failures.append(f"{playbook}: {role} notifies {handler!r}")
                print(f"FAIL {playbook}: {role} notifies {handler!r}; "
                      f"defined in {owners or 'NOWHERE'}, not in this play")

# A handler nothing notifies is dead weight, and after a split it usually means the
# notify ended up in the other playbook. One handler notifying another counts.
all_roles = {r for p in PLAYBOOKS for r in role_names(p)}
notified_anywhere = set()
for role in all_roles:
    notified_anywhere |= notified_by(role)
    for task in walk(load(ROLES / role / "handlers" / "main.yml")):
        n = task.get("notify")
        if isinstance(n, str):
            notified_anywhere.add(n)
        elif isinstance(n, list):
            notified_anywhere |= {x for x in n if isinstance(x, str)}

for role in sorted(all_roles):
    for handler in sorted(defined_by(role) - notified_anywhere):
        print(f"warn {role} defines {handler!r} and nothing notifies it")

if failures:
    print(f"\n{len(failures)} unresolvable notify(s):")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)
print("\ntest_notifies: every notify resolves within its playbook")
