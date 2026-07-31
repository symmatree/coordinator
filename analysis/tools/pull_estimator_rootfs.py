#!/usr/bin/env python3
"""pull_estimator_rootfs -- daemonless pull of a coordinator-vio-estimator image
into a flat rootfs, for running vins_fusion off-vehicle on a host with no container runtime.

No Docker/containerd needed: fetch an anonymous GHCR token, read the OCI image index,
pick the manifest for the requested arch, download each layer blob, and extract all layers
into one directory. Then rehome absolute symlinks into the prefix (see --fix-symlinks) so
the loader (native or qemu-user) resolves them against the rootfs, not the host '/'.

The estimator image is multi-arch (CI builds amd64 + arm64 into one manifest), so:
  * on an x86_64 analysis box, `--arch amd64` gives a **native** rootfs -- run the binary
    directly, no qemu (the fast default, coordinator #85);
  * `--arch arm64` gives the Pi's production binary, run under qemu-aarch64-static as a
    fallback / for a cross-arch fidelity check.
--arch defaults to the host architecture. See docs/vio-offline-replay.md for the run recipe.

    python3 pull_estimator_rootfs.py --out rootfs/               # host arch: pull+extract+fix
    python3 pull_estimator_rootfs.py --arch arm64 --out rootfs/  # arm64 (for qemu on x86)
    python3 pull_estimator_rootfs.py --fix-symlinks rootfs/      # only rehome symlinks (idempotent)
"""

import argparse
import io
import json
import os
import platform
import sys
import tarfile
import urllib.request

IMAGE = "symmatree/coordinator-vio-estimator"
DEFAULT_TAG = "main"
REGISTRY = "https://ghcr.io"

# The arch names we accept on the CLI (== OCI manifest 'architecture' values). _HOST_ARCH
# maps the host's uname machine (platform.machine()) to one of these, for the default.
_ARCHES = ("amd64", "arm64")
_HOST_ARCH = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}


def host_arch():
    """The OCI arch name matching this host, or None if we don't recognize it."""
    return _HOST_ARCH.get(platform.machine())


def _token():
    url = f"{REGISTRY}/token?scope=repository:{IMAGE}:pull&service=ghcr.io"
    return json.load(urllib.request.urlopen(url))["token"]


def _get(url, tok, accept):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}", "Accept": accept})
    return urllib.request.urlopen(req)


def _pick_manifest(tok, tag, arch):
    idx = json.load(_get(
        f"{REGISTRY}/v2/{IMAGE}/manifests/{tag}", tok,
        "application/vnd.oci.image.index.v1+json,"
        "application/vnd.docker.distribution.manifest.list.v2+json",
    ))
    for m in idx.get("manifests", []):
        p = m.get("platform", {})
        if p.get("os") == "linux" and p.get("architecture") == arch:
            return m["digest"]
    have = sorted({m.get("platform", {}).get("architecture") for m in idx.get("manifests", [])} - {None})
    sys.exit(f"pull_estimator_rootfs: no linux/{arch} manifest in the image index (have: {', '.join(have) or 'none'})")


def pull(out, tag, arch):
    tok = _token()
    dig = _pick_manifest(tok, tag, arch)
    man = json.load(_get(
        f"{REGISTRY}/v2/{IMAGE}/manifests/{dig}", tok,
        "application/vnd.oci.image.manifest.v1+json,"
        "application/vnd.docker.distribution.manifest.v2+json",
    ))
    layers = man["layers"]
    total = sum(l["size"] for l in layers)
    print(f"pulling linux/{arch} {len(layers)} layers ({total/1e6:.0f} MB) -> {out}")
    os.makedirs(out, exist_ok=True)
    for i, l in enumerate(layers):
        print(f"  layer {i + 1}/{len(layers)} {l['digest'][:19]} {l['size']/1e6:.1f} MB", flush=True)
        blob = _get(f"{REGISTRY}/v2/{IMAGE}/blobs/{l['digest']}", tok, "application/octet-stream").read()
        tf = tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz")
        for m in tf.getmembers():
            if os.path.basename(m.name).startswith(".wh."):  # OCI whiteout; flat merge is fine here
                continue
            try:
                # "fully_trusted": the OCI image is ours + trusted, and its /etc/alternatives
                # symlinks point outside the member dir, which py3.12's "tar"/"data" filters
                # reject with OutsideDestinationError. Rehomed afterward by _fix_symlinks.
                tf.extract(m, out, numeric_owner=True, filter="fully_trusted")
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
    ap.add_argument("--arch", choices=sorted(_ARCHES), default=host_arch(),
                    help="image arch to pull (default: host arch, %(default)s). "
                         "amd64 runs natively on x86; arm64 needs qemu-aarch64-static.")
    ap.add_argument("--fix-symlinks", metavar="ROOTFS",
                    help="only rehome absolute symlinks in an existing rootfs, then exit")
    args = ap.parse_args()

    if args.fix_symlinks:
        fix_symlinks(args.fix_symlinks)
        return
    if not args.out:
        ap.error("need --out DIR (or --fix-symlinks ROOTFS)")
    if not args.arch:
        ap.error(f"unknown host arch {platform.machine()!r}; pass --arch {{{','.join(sorted(_ARCHES))}}}")
    pull(args.out, args.tag, args.arch)
    fix_symlinks(args.out)
    binp = os.path.join(args.out, "opt/coordinator/bin/vins_fusion")
    print(f"{'OK' if os.path.exists(binp) else 'MISSING'}: {binp}")


if __name__ == "__main__":
    main()
