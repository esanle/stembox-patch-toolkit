#!/usr/bin/env python3
"""Stembox patch toolkit — single-file CLI.

Subcommands
-----------
  patch      apply/verify the 5 binary patches (default target: Stembox 2.0.0)
  keys       generate a fresh Ed25519 keypair for our own token scheme
  mint       mint an offline license token with our keypair
  install    install a token into the app's license store (and refresh lastCheck)
  install-app  patch + re-sign + deploy to /Applications, then install token
  status     show current patch state / key material / installed app state

All artifacts live under ``build/`` (git-ignored):
  build/app-pristine/   official copy (input for `patch`)
  build/app-patched/    patched + ad-hoc signed copy (deployment source)
  build/keys/           our Ed25519 keypair (private key never committed)

Only ``cryptography`` is required for key operations; patching itself is
pure stdlib.

Examples
--------
  python3 apply_patches.py patch
  python3 apply_patches.py keys --force
  python3 apply_patches.py mint --tier lifetime
  python3 apply_patches.py install --tier lifetime
  python3 apply_patches.py install-app --tier lifetime
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(ROOT, "build")
APP_PRISTINE = os.path.join(BUILD, "app-pristine")
APP_PATCHED = os.path.join(BUILD, "app-patched")
KEYS_DIR = os.path.join(BUILD, "keys")
KEY_FILE = os.path.join(KEYS_DIR, "keys.json")
INSTALL_DIR = "/Applications/Stembox.app"
STORE_PATH = os.path.expanduser(
    "~/Library/Application Support/stembox-substrat/stembox_license.json")

# ── patch definitions (Stembox 2.0.0, arm64) ───────────────────────────────
# (name, file offset, original bytes, patched bytes)
PATCHES = [
    ("P1 isTrial-entry", 0x2AF930, bytes.fromhex("081d0037"), bytes.fromhex("1f2003d5")),
    ("P2 isTrial-ret", 0x2AFCD0, bytes.fromhex("800e7939"), bytes.fromhex("00008052")),
    ("P3 skip-ed25519", 0x2EC4A8, bytes.fromhex("c0040034"), bytes.fromhex("26000014")),
    ("P4 skip-dialog", 0x1A0080, bytes.fromhex("e8020034"), bytes.fromhex("17000014")),
]
#: vendor Ed25519 SPKI public key embedded in the official binary
OLD_PUB = b"MCowBQYDK2VwAyEAeHG7SXjIcM5yomnrnQg39a1q6kiZAR6+GgdT5DOA2QM="

#: patched binary path inside a bundle
BIN_REL = os.path.join("Contents", "MacOS", "Stembox")


# ─────────────────────────────────────────────────────────────────────────────
# keys
# ─────────────────────────────────────────────────────────────────────────────
def _ed25519():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    return Ed25519PrivateKey


def load_keys() -> dict:
    if not os.path.isfile(KEY_FILE):
        raise SystemExit(
            f"no keypair at {KEY_FILE}\n"
            "  run:  python3 apply_patches.py keys")
    with open(KEY_FILE) as fh:
        d = json.load(fh)
    for k in ("priv_raw_hex", "pub_raw_hex", "pub_spki_b64"):
        if k not in d:
            raise SystemExit(f"malformed key file: missing {k}")
    return d


def save_keys(keys: dict) -> None:
    os.makedirs(KEYS_DIR, exist_ok=True)
    with open(KEY_FILE, "w") as fh:
        json.dump(keys, fh, indent=2)
    os.chmod(KEY_FILE, 0o600)


def cmd_keys(args) -> int:
    if os.path.isfile(KEY_FILE) and not args.force:
        print(f"keypair already exists: {KEY_FILE} (use --force to regenerate)")
        return 0
    from cryptography.hazmat.primitives import serialization
    priv = _ed25519().generate()
    pub = priv.public_key()
    priv_raw = priv.private_bytes(serialization.Encoding.Raw,
                                  serialization.PrivateFormat.Raw,
                                  serialization.NoEncryption())
    pub_raw = pub.public_bytes(serialization.Encoding.Raw,
                               serialization.PublicFormat.Raw)
    der = pub.public_bytes(serialization.Encoding.DER,
                            serialization.PublicFormat.SubjectPublicKeyInfo)
    keys = {
        "priv_raw_hex": priv_raw.hex(),
        "pub_raw_hex": pub_raw.hex(),
        "pub_spki_b64": base64.b64encode(der).decode(),
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    save_keys(keys)
    print(f"wrote keypair -> {KEY_FILE}")
    print(f"  public SPKI b64: {keys['pub_spki_b64']}")
    print("  (private key stays local; build/ is git-ignored)")
    if args.patch_now:
        print("note: run `patch` to embed the new public key into the binary")
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# token minting
# ─────────────────────────────────────────────────────────────────────────────
def device_id() -> str:
    """Local device id, matching the app's computeDeviceId() (macOS UUID)."""
    if sys.platform == "darwin":
        try:
            out = subprocess.run(
                ["/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                capture_output=True, text=True, timeout=15).stdout
            import re
            m = re.search(r'IOPlatformUUID"?\s*=\s*"([\w-]+)"', out)
            if m:
                return m.group(1)
        except Exception:
            pass
    return "UNKNOWN-DEVICE"


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode()


def mint_token(keys: dict, device: str, tier: str, exp_days: int) -> str:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    priv = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(keys["priv_raw_hex"]))
    now = int(time.time())
    payload = {"d": device, "tier": tier, "exp": now + exp_days * 86400,
               "iat": now, "kind": tier}
    left = b64u(json.dumps(payload, separators=(",", ":")).encode())
    sig = priv.sign(left.encode())
    # self-check against our public key
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    Ed25519PublicKey.from_public_bytes(
        bytes.fromhex(keys["pub_raw_hex"])).verify(sig, left.encode())
    return left + "." + b64u(sig)


def cmd_mint(args) -> int:
    keys = load_keys()
    token = mint_token(keys, args.device or device_id(), args.tier, args.exp_days)
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(token + "\n")
        print(f"token -> {args.out}")
    else:
        print(token)
    print(f"  device={args.device or device_id()} tier={args.tier} "
          f"valid_days={args.exp_days}", file=sys.stderr)
    return 0


def cmd_install(args) -> int:
    """Install a token into the app's license store."""
    keys = load_keys()
    token = mint_token(keys, args.device or device_id(), args.tier, args.exp_days)
    try:
        with open(STORE_PATH) as fh:
            store = json.load(fh)
    except Exception:
        store = {}
    store["token"] = token
    store["lastCheck"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    store["ok"] = True
    os.makedirs(os.path.dirname(STORE_PATH), exist_ok=True)
    with open(STORE_PATH, "w") as fh:
        json.dump(store, fh)
    print(f"token installed -> {STORE_PATH}")
    print(f"  tier={args.tier} device={args.device or device_id()}")
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# patching
# ─────────────────────────────────────────────────────────────────────────────
def patch_binary(binary: str, pub_b64: str, verify_only: bool) -> int:
    if not os.path.isfile(binary):
        print(f"missing binary: {binary}", file=sys.stderr)
        return 2
    new_pub = pub_b64.encode()
    if len(new_pub) != 60:
        print(f"bad public key length: {len(new_pub)}", file=sys.stderr)
        return 2

    d = bytearray(open(binary, "rb").read())
    changed = 0
    for name, off, orig, new in PATCHES:
        cur = bytes(d[off:off + 4])
        if cur == new:
            print(f"{name}: already patched, skip")
        elif cur == orig:
            if not verify_only:
                d[off:off + 4] = new
            changed += 1
            print(f"{name}: {'WOULD PATCH' if verify_only else 'PATCHED'}")
        else:
            print(f"{name}: UNEXPECTED bytes {cur.hex()} (want {orig.hex()}) -- abort!",
                  file=sys.stderr)
            return 1

    n_old, n_new = d.count(OLD_PUB), d.count(new_pub)
    if n_new == 1 and n_old == 0:
        print("P5 pubkey: already swapped, skip")
    elif n_old == 1 and n_new == 0:
        if not verify_only:
            d = d.replace(OLD_PUB, new_pub)
        changed += 1
        print(f"P5 pubkey: {'WOULD SWAP' if verify_only else 'SWAPPED'}")
    else:
        print(f"P5 pubkey: unexpected counts old={n_old} new={n_new} -- abort!",
              file=sys.stderr)
        return 1

    if changed and not verify_only:
        open(binary, "wb").write(d)
        print(f"wrote {changed} change(s)")
    elif changed:
        print(f"would write {changed} change(s) (verify-only)")
    else:
        print("nothing to do")
    if verify_only:
        print("VERIFY (dry-run): plan computed, nothing written")
        return 0

    d = open(binary, "rb").read()
    ok = True
    for name, off, orig, new in PATCHES:
        if bytes(d[off:off + 4]) != new:
            print(f"VERIFY FAIL {name}", file=sys.stderr)
            ok = False
    if d.count(new_pub) != 1 or d.count(OLD_PUB) != 0:
        print("VERIFY FAIL pubkey", file=sys.stderr)
        ok = False
    print("ALL VERIFIED" if ok else "VERIFY FAILED")
    return 0 if ok else 1


def cmd_patch(args) -> int:
    keys = load_keys()
    binary = args.binary or os.path.join(APP_PATCHED, BIN_REL)
    return patch_binary(binary, keys["pub_spki_b64"], args.verify_only)


def cmd_install_app(args) -> int:
    """patch + ad-hoc sign + deploy to /Applications + install token."""
    keys = load_keys()
    patched_bin = os.path.join(APP_PATCHED, BIN_REL)
    rc = patch_binary(patched_bin, keys["pub_spki_b64"], False)
    if rc != 0:
        return rc
    print("\n-- re-signing (no --options runtime: disables library validation)")
    subprocess.run(["codesign", "--force", "--sign", "-", patched_bin], check=True)
    print("\n-- deploying to", INSTALL_DIR)
    if os.path.isdir(INSTALL_DIR):
        shutil.rmtree(INSTALL_DIR)
    shutil.copytree(APP_PATCHED, INSTALL_DIR, symlinks=True)
    subprocess.run(["xattr", "-dr", "com.apple.quarantine", INSTALL_DIR],
                   check=False)
    print("   deployed")
    if not args.no_token:
        print("\n-- installing token")
        cmd_install(args)
    print("\nlaunch:  %s &" % os.path.join(INSTALL_DIR, BIN_REL))
    return 0


def cmd_bootstrap(args) -> int:
    """One-shot setup on a fresh machine: fetch the official app, generate
    keys, patch, sign, deploy, install a token for THIS machine.

    Works from a bare clone (no build/ contents). Steps:
      1. download the vendor DMG for the requested version
      2. mount it and copy Stembox.app -> build/app-pristine
      3. generate our keypair (build/keys/keys.json)
      4. copy pristine -> build/app-patched and apply the patches
      5. ad-hoc sign, deploy to /Applications, install a local token
    """
    ver = args.version
    url = (f"https://stemboxdj.com/downloads/Stembox-{ver}-arm64.dmg")
    dmg = os.path.join(BUILD, "downloads", f"Stembox-{ver}-arm64.dmg")
    os.makedirs(os.path.dirname(dmg), exist_ok=True)

    if not os.path.isfile(dmg):
        print(f"-- downloading {url}")
        subprocess.run(["curl", "-fL", "-o", dmg, url], check=True)
    print(f"   dmg: {os.path.basename(dmg)} ({os.path.getsize(dmg)} bytes)")

    print("-- mounting dmg")
    mount = subprocess.run(
        ["hdiutil", "attach", dmg, "-nobrowse", "-readonly"],
        capture_output=True, text=True)
    vol = None
    for line in mount.stdout.splitlines():
        if line.strip().startswith("/Volumes/"):
            vol = line.strip()
    if not vol:
        # license agreement may block a non-tty attach
        print("   (license prompt?) retrying with `yes |`")
        subprocess.run("yes | hdiutil attach '%s' -nobrowse -readonly" % dmg,
                       shell=True, check=False, capture_output=True)
        for cand in os.listdir("/Volumes"):
            if cand.startswith("Stembox"):
                vol = os.path.join("/Volumes", cand)
    if not vol or not os.path.isdir(vol):
        print("could not mount dmg", file=sys.stderr)
        return 1
    src_app = os.path.join(vol, "Stembox.app")
    if not os.path.isdir(src_app):
        print(f"Stembox.app not found in {vol}", file=sys.stderr)
        subprocess.run(["hdiutil", "detach", vol], check=False)
        return 1

    try:
        print("-- copying vendor app -> build/app-pristine")
        if os.path.isdir(APP_PRISTINE):
            shutil.rmtree(APP_PRISTINE)
        shutil.copytree(src_app, APP_PRISTINE, symlinks=True)
    finally:
        subprocess.run(["hdiutil", "detach", vol], check=False)

    if not os.path.isfile(KEY_FILE) or args.force_keys:
        print("-- generating keypair")
        cmd_keys(argparse.Namespace(force=args.force_keys, patch_now=False))
    else:
        print(f"-- reusing keypair {KEY_FILE}")

    print("-- copying pristine -> app-patched")
    if os.path.isdir(APP_PATCHED):
        shutil.rmtree(APP_PATCHED)
    shutil.copytree(APP_PRISTINE, APP_PATCHED, symlinks=True)

    keys = load_keys()
    rc = patch_binary(os.path.join(APP_PATCHED, BIN_REL), keys["pub_spki_b64"], False)
    if rc != 0:
        return rc
    subprocess.run(["codesign", "--force", "--sign", "-",
                    os.path.join(APP_PATCHED, BIN_REL)], check=True)
    print("-- ad-hoc signed (no --options runtime)")
    return cmd_install_app(args)


def cmd_status(args) -> int:
    keys = load_keys() if os.path.isfile(KEY_FILE) else None
    if keys:
        print(f"keypair: {KEY_FILE}")
        print(f"  pub SPKI b64: {keys['pub_spki_b64']}")
        print(f"  created:      {keys.get('created','?')}")
    else:
        print("keypair: MISSING (run `keys`)")
    for label, root in (("pristine", APP_PRISTINE), ("patched", APP_PATCHED)):
        b = os.path.join(root, BIN_REL)
        if not os.path.isfile(b):
            print(f"{label}: absent ({root})")
            continue
        d = open(b, "rb").read()
        state = []
        for name, off, orig, new in PATCHES:
            cur = bytes(d[off:off + 4])
            state.append(f"{name.split()[0]}={'OK' if cur == new else ('orig' if cur == orig else '??')}")
        pub = "ours" if d.count(keys["pub_spki_b64"].encode()) == 1 else "vendor"
        print(f"{label}: {root}")
        print("  " + " ".join(state) + f"  pubkey={pub}")
    inst = os.path.join(INSTALL_DIR, BIN_REL)
    if os.path.isfile(inst):
        d = open(inst, "rb").read()
        st = "OK" if all(bytes(d[o:o + 4]) == n for _, o, _, n in PATCHES) else "MISSING"
        print(f"installed: {INSTALL_DIR} -> patches {st}")
    else:
        print(f"installed: {INSTALL_DIR} -> absent")
    if os.path.isfile(STORE_PATH):
        with open(STORE_PATH) as fh:
            st = json.load(fh)
        print(f"license store: kind={st.get('kind')} ok={st.get('ok')} "
              f"token={'present' if st.get('token') else 'empty'} "
              f"lastCheck={st.get('lastCheck')}")
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="apply_patches.py",
        description="Stembox patch / key / token toolkit (single file)")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("patch", help="apply the 5 binary patches")
    p.add_argument("--binary", default=None, help="binary to patch")
    p.add_argument("--verify-only", action="store_true", help="dry run")
    p.set_defaults(fn=cmd_patch)

    k = sub.add_parser("keys", help="generate our Ed25519 keypair")
    k.add_argument("--force", action="store_true", help="overwrite existing")
    k.add_argument("--patch-now", action="store_true",
                   help="(informational) embed the new public key")
    k.set_defaults(fn=cmd_keys)

    m = sub.add_parser("mint", help="mint a license token")
    m.add_argument("--tier", default="lifetime")
    m.add_argument("--device", default=None, help="override device id")
    m.add_argument("--exp-days", type=int, default=36500)
    m.add_argument("--out", default=None, help="write token to file")
    m.set_defaults(fn=cmd_mint)

    i = sub.add_parser("install", help="install a token into the license store")
    i.add_argument("--tier", default="lifetime")
    i.add_argument("--device", default=None)
    i.add_argument("--exp-days", type=int, default=36500)
    i.set_defaults(fn=cmd_install)

    a = sub.add_parser("install-app",
                       help="patch + sign + deploy + install token")
    a.add_argument("--tier", default="lifetime")
    a.add_argument("--device", default=None)
    a.add_argument("--exp-days", type=int, default=36500)
    a.add_argument("--no-token", action="store_true",
                   help="skip token installation")
    a.set_defaults(fn=cmd_install_app)

    b = sub.add_parser("bootstrap",
                       help="fresh-machine setup: download official app, "
                            "keys, patch, sign, deploy, token")
    b.add_argument("--version", default="2.0.0", help="vendor version to fetch")
    b.add_argument("--force-keys", action="store_true",
                   help="regenerate the keypair even if one exists")
    b.add_argument("--tier", default="lifetime")
    b.add_argument("--device", default=None)
    b.add_argument("--exp-days", type=int, default=36500)
    b.add_argument("--no-token", action="store_true")
    b.set_defaults(fn=cmd_bootstrap)

    s = sub.add_parser("status", help="show patch/key/app state")
    s.set_defaults(fn=cmd_status)

    args = ap.parse_args(argv)
    if not getattr(args, "fn", None):
        ap.print_help()
        return 0
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
