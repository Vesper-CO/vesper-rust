#!/usr/bin/env python3
"""Compiles Horizon's native mods into the Vesper client.

Takes the mods listed in native-mods.txt from Horizon's own mod library (the index behind
playhorizon.dev/mods), checks every file, and applies them to the (already rebranded) Horizon source with
`horizon-mixins`, the tool Horizon's launcher uses. The result is a source tree to compile.

    native_mods.py --source src --revision <40-hex commit> --list native-mods.txt \
                   --tool horizon-mixins --stage stage --report mods-report.json [--index FILE|URL]

Only mixin mods are built in (the library's trait mods need a different build step). A mod that cannot be
used, or that horizon-mixins refuses, is left out with the reason in the report; the rest still build.
Native mods are not sandboxed: they run with the player's rights, so the list is yours to review. Every
file is checked against the size and SHA-256 the index lists, and nothing comes from outside GitHub.
"""
import argparse, hashlib, json, re, shutil, subprocess, sys, urllib.request, zipfile
from pathlib import Path

INDEX = "https://horizon-rust.github.io/horizon-mods/index.json"
HOOKS_FILE = "crates/horizon-client/src/game/native_hooks.rs"
MAX_FILE = 8 << 20


def log(msg):
    print(msg, flush=True)


def read_list(path):
    ids = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line and line not in ids:
            ids.append(line)
    return ids


def fetch(url_or_path):
    if re.match(r"https?://", url_or_path):
        with urllib.request.urlopen(url_or_path, timeout=120) as r:
            return r.read()
    return Path(url_or_path).read_bytes()


def check_url(url):
    ok = url.startswith("https://github.com/") and "/releases/download/" in url and ".." not in url and "?" not in url and "#" not in url
    if not ok:
        raise ValueError("download is not on GitHub's release servers")


def hooks_api(source):
    text = (Path(source) / HOOKS_FILE).read_text(encoding="utf-8")
    m = re.search(r"NATIVE_HOOKS_API: u32 = (\d+);", text)
    if not m:
        raise SystemExit("::error::cannot read NATIVE_HOOKS_API from the Horizon source")
    return int(m.group(1))


def pick(project, revoked, game):
    for v in project.get("versions", []):
        if v.get("game") != game:
            continue
        sha = v["file"]["sha256"].lower()
        if any(r.get("id") == project["id"] and (not r.get("sha256") or r["sha256"].lower() == sha) for r in revoked):
            continue
        return v
    return None


def unpack(data, dest, mod_id):
    """An .hnmod is a zip: horizon-native.json and mixins/<id>/... Nothing else is read, nothing is run."""
    tmp = dest.with_suffix(".zip")
    tmp.write_bytes(data)
    with zipfile.ZipFile(tmp) as z:
        for info in z.infolist():
            name = info.filename
            if name.endswith("/"):
                continue
            parts = Path(name).parts
            if name.startswith("/") or ".." in parts or "\\" in name:
                raise ValueError(f"unsafe path in the file: {name}")
            if not (name == "horizon-native.json" or name.startswith(f"mixins/{mod_id}/")):
                raise ValueError(f"unexpected file in the bundle: {name}")
            target = dest / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(info))
    tmp.unlink()


def repin(manifest, revision):
    """A mod built on Horizon's native hook points runs on any revision that has them: its pin becomes this build's."""
    text = manifest.read_text(encoding="utf-8")
    new, n = re.subn(r'(horizon_revision\s*=\s*)"[0-9a-fA-F]{40}"', rf'\1"{revision}"', text)
    if n:
        manifest.write_text(new, encoding="utf-8")


def prepare(ids, index, args, work, mods_in, report):
    projects = {p["id"]: p for p in index.get("projects", [])}
    revoked = index.get("revoked", [])
    api = hooks_api(args.source)
    kept = []
    for mod_id in ids:
        def skip(why):
            log(f"  skipped {mod_id}: {why}")
            report["skipped"].append({"id": mod_id, "reason": why})

        p = projects.get(mod_id)
        if not p:
            skip("not in Horizon's library")
            continue
        if p.get("kind") != "native-mod":
            skip(f"is a {p.get('kind')}, not a native mod")
            continue
        v = pick(p, revoked, args.game)
        if not v:
            skip(f"no version for Minecraft {args.game}, or taken down")
            continue
        f = v["file"]
        try:
            check_url(f["url"])
            if not f["name"].endswith(".hnmod") or "/" in f["name"] or "\\" in f["name"]:
                raise ValueError("unexpected file name")
            if not (0 < f["size"] <= MAX_FILE) or not re.fullmatch(r"[0-9a-fA-F]{64}", f["sha256"]):
                raise ValueError("the index gives no usable size or checksum")
            data = fetch(f["url"])
            if len(data) != f["size"] or hashlib.sha256(data).hexdigest() != f["sha256"].lower():
                raise ValueError("size or checksum does not match the library")
            dest = work / mod_id
            shutil.rmtree(dest, ignore_errors=True)
            dest.mkdir(parents=True)
            unpack(data, dest, mod_id)
            meta = json.loads((dest / "horizon-native.json").read_text(encoding="utf-8"))
        except Exception as e:  # a bad download or bundle leaves only that mod out
            skip(str(e))
            continue
        bundle = dest / "mixins" / mod_id
        manifest = bundle / "horizon-mixin.toml"
        if not manifest.is_file():
            skip("has no mixin manifest (a trait mod, which needs a different build step)")
            continue
        text = manifest.read_text(encoding="utf-8")
        if re.search(r"^\s*\[api\]", text, re.M) or re.search(r"^\s*\[\[post_build\]\]", text, re.M):
            skip("is a trait mod or has a post-build program, which this build does not do")
            continue
        hooks = meta.get("hooks")
        if hooks is not None:
            if hooks > api:
                skip(f"needs hook points v{hooks}; this Horizon has v{api}")
                continue
            repin(manifest, args.revision)
        elif meta.get("horizon") != args.revision:
            skip(f"is pinned to Horizon {str(meta.get('horizon'))[:7]}, this build is {args.revision[:7]}")
            continue
        shutil.copytree(bundle, mods_in / mod_id)
        kept.append({"id": mod_id, "name": p.get("name", mod_id), "version": v["version"], "summary": p.get("summary", ""),
                     "authors": p.get("authors", []), "icon": p.get("icon")})
        log(f"  ready   {mod_id} {v['version']}")
    return kept


def apply(args, mods_in, stage):
    cmd = [args.tool, "apply", "--workspace", args.source, "--mods", str(mods_in), "--out", str(stage)]
    return subprocess.run(cmd, capture_output=True, text=True)


def culprit(stderr, mods_in):
    """The mod a horizon-mixins diagnostic names (its file lies under mods_in/<id>/)."""
    m = re.search(re.escape(str(mods_in)) + r"[/\\]([^/\\\"]+)", stderr)
    return m.group(1) if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--revision", required=True)
    ap.add_argument("--list", required=True)
    ap.add_argument("--tool", required=True)
    ap.add_argument("--stage", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--index", default=INDEX)
    ap.add_argument("--game", default="26.3")
    ap.add_argument("--work", default="native-work")
    args = ap.parse_args()

    if not re.fullmatch(r"[0-9a-f]{40}", args.revision):
        raise SystemExit("::error::--revision must be the full 40-character commit id of the Horizon source")
    source, stage, work = Path(args.source).resolve(), Path(args.stage).resolve(), Path(args.work).resolve()
    args.source = str(source)
    (source / ".horizon-revision").write_text(args.revision + "\n", encoding="utf-8")
    # horizon-mixins refuses symbolic links in the source (Horizon's launcher leaves them out too); none is needed to build.
    for path in list(source.rglob("*")):
        if path.is_symlink():
            path.unlink()
    shutil.rmtree(work, ignore_errors=True)
    shutil.rmtree(stage, ignore_errors=True)
    mods_in = work / "mods"
    mods_in.mkdir(parents=True)

    index = json.loads(fetch(args.index))
    if index.get("schema") != 1:
        raise SystemExit(f"::error::the Horizon mod library uses schema {index.get('schema')}, this script knows 1")
    report = {"revision": args.revision, "game": args.game, "mods": [], "skipped": []}
    ids = read_list(args.list)
    log(f"Preparing {len(ids)} native mods")
    kept = prepare(ids, index, args, work, mods_in, report)

    # horizon-mixins refuses the whole set when one mod does not fit; leave that mod out and try again.
    while kept:
        log(f"Applying {len(kept)} mods to the source")
        r = apply(args, mods_in, stage)
        if r.returncode == 0:
            break
        bad = culprit(r.stderr, mods_in)
        log(r.stderr.strip()[-1500:])
        if not bad or not any(k["id"] == bad for k in kept):
            report["mods"] = []
            report["failed"] = r.stderr.strip()[-1500:]
            kept = []
            break
        reason = "horizon-mixins refused it (see the build log)"
        report["skipped"].append({"id": bad, "reason": reason})
        kept = [k for k in kept if k["id"] != bad]
        shutil.rmtree(mods_in / bad, ignore_errors=True)
        shutil.rmtree(stage, ignore_errors=True)
    report["mods"] = kept
    Path(args.report).write_text(json.dumps(report, indent=2), encoding="utf-8")
    if not kept:
        log("::warning::No native mod could be applied; the build continues without them.")
        sys.exit(3)
    log(f"Built into the game: {', '.join(k['id'] for k in kept)}")


if __name__ == "__main__":
    main()
