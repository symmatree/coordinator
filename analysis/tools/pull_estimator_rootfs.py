#!/usr/bin/env python3
"""pull_estimator_rootfs -- daemonless pull of the arm64 coordinator-vio-estimator
image into a flat rootfs, for running vins_fusion under qemu-user on an x86_64 host.

No Docker/containerd needed: fetch an anonymous GHCR token, read the OCI image index,
pick the linux/arm64 manifest, download each layer blob, and extract all layers into one
directory. Then rehome absolute symlinks into the prefix (see --fix-symlinks) so
qemu-user can resolve them.

See docs/vio-offline-replay.md for the full run recipe.

    python3 pull_estimator_rootfs.py --out rootfs/          # pull + extract + fix symlinks
    python3 pull_estimator_rootfs.py --fix-symlinks rootfs/ # only rehome symlinks (idempotent)
"""

import argparse
import io
import json
import os
import sys
import tarfile
import urllib.request

IMAGE = "symmatree/coordinator-vio-estimator"
DEFAULT_TAG = "main"
REGISTRY = "https://ghcr.io"


def _token():
    url = f"{REGISTRY}/token?scope=repository:{IMAGE}:pull&service=ghcr.io"
    return json.load(urllib.request.urlopen(url))["token"]


def _get(url, tok, accept):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}", "Accept": accept})
    return urllib.request.urlopen(req)


def _arm64_manifest(tok, tag):
    idx = json.load(_get(
        f"{REGISTRY}/v2/{IMAGE}/manifests/{tag}", tok,
        "application/vnd.oci.image.index.v1+json,"
        "application/vnd.docker.distribution.manifest.list.v2+json",
    ))
    for m in idx.get("manifests", []):
        p = m.get("platform", {})
        if p.get("os") == "linux" and p.get("architecture") == "arm64":
            return m["digest"]
    sys.exit("pull_estimator_rootfs: no linux/arm64 manifest in the image index")


def pull(out, tag):
    tok = _token()
    dig = _arm64_manifest(tok, tag)
    man = json.load(_get(
        f"{REGISTRY}/v2/{IMAGE}/manifests/{dig}", tok,
        "application/vnd.oci.image.manifest.v1+json,"
        "application/vnd.docker.distribution.manifest.v2+json",
    ))
    layers = man["layers"]
    total = sum(l["size"] for l in layers)
    print(f"pulling {len(layers)} layers ({total/1e6:.0f} MB) -> {out}")
    os.makedirs(out, exist_ok=True)
    for i, l in enumerate(layers):
        print(f"  layer {i + 1}/{len(layers)} {l['digest'][:19]} {l['size']/1e6:.1f} MB", flush=True)
        blob = _get(f"{REGISTRY}/v2/{IMAGE}/blobs/{l['digest']}", tok, "application/octet-stream").read()
        tf = tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz")
        for m in tf.getmembers():
            if os.path.basename(m.name).startswith(".wh."):  # OCI whiteout; flat merge is fine here
                continue
            try:
                # trusted first-party image; filter='tar' keeps device/setuid handling
                # sane while still allowing the paths/links a rootfs needs (py>=3.12).
                tf.extract(m, out, numeric_owner=True, filter="tar")
            except (PermissionError, OSError):
                pass


def fix_symlinks(out):
    """Rehome absolute symlink targets into the prefix.

    qemu-user resolves an absolute symlink target (e.g. /etc/alternatives/liblapack...)
    against the host '/', not the rootfs, so library alternatives chains break. Repoint
    every absolute symlink to <root><target>. Idempotent.
    """
    root = os.path.abspath(out)
    fixed = 0
    for dirpath, dirs, files in os.walk(root):
        for name in dirs + files:
            p = os.path.join(dirpath, name)
            if os.path.islink(p):
                tgt = os.readlink(p)
                if tgt.startswith("/") and not tgt.startswith(root):
                    try:
                        os.remove(p)
                        os.symlink(root + tgt, p)
                        fixed += 1
                    except OSError:
                        pass
    print(f"rehomed {fixed} absolute symlink(s) into {root}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", help="rootfs output dir (pull + extract + fix symlinks)")
    ap.add_argument("--tag", default=DEFAULT_TAG, help="image tag (default: %(default)s)")
    ap.add_argument("--fix-symlinks", metavar="ROOTFS",
                    help="only rehome absolute symlinks in an existing rootfs, then exit")
    args = ap.parse_args()

    if args.fix_symlinks:
        fix_symlinks(args.fix_symlinks)
        return
    if not args.out:
        ap.error("need --out DIR (or --fix-symlinks ROOTFS)")
    pull(args.out, args.tag)
    fix_symlinks(args.out)
    binp = os.path.join(args.out, "opt/coordinator/bin/vins_fusion")
    print(f"{'OK' if os.path.exists(binp) else 'MISSING'}: {binp}")


if __name__ == "__main__":
    main()
