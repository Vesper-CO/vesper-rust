#!/usr/bin/env python3
"""Puts every native mod of Horizon's mod library into the Vesper client, so a mod the library gains is in the next build.

    python vesper_native.py SRC OUT [--mode all|listed|none] [--no-compile]

SRC is the unpacked and rebranded Horizon source. OUT is written: the same source with the mods' code applied (what the
build compiles), `vesper-mods.json` (the mods in the build, for the launcher) and `native-report.md` (what happened).

It uses the library's own tooling (the registry repository's scripts/native_compat.py, native.py and native_trace.py,
which the library runs every day to find the mods that do not build) so the rules are the launcher's:

  1. take the newest, not taken down, version of every native mod in the published index (`all`), or only those
     named in native-mods.txt (`listed`); leave out the ones named in native-mods-exclude.txt;
  2. horizon-mixins applies them together on this Horizon; the ones it refuses (and the ones needing those) are left out;
  3. `cargo check` of the game with them; the mods whose own code has the errors are left out, up to six rounds,
     then the game is checked once more with what is left.

If that last check still fails the next smaller set is tried (`all`, then `listed`, then none), so a week's build is
never lost to one mod. Mods that need an API package or a post-build step run by Horizon's own launcher are left out: this does not
build those.

Needs: git, Python 3.11, cargo, rsync (a Linux runner), and the network. Set GITHUB_TOKEN to avoid GitHub's API limits.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

REGISTRY_GIT = "https://github.com/horizon-rust/horizon-mods"
HERE = Path(__file__).resolve().parent


def log(*args):
    print(*args, file=sys.stderr, flush=True)


def ids_in(path):
    """The ids in a text file, one per line (`#` starts a comment); none when the file is missing."""
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.append(line)
    return out


def load_registry(where):
    """Clone the registry scripts and make MIXINS_REV parsing compatible."""
    if not (where / "scripts" / "native_compat.py").is_file():
        shutil.rmtree(where, ignore_errors=True)
        subprocess.run(
            ["git", "clone", "--depth", "1", "-q", REGISTRY_GIT, str(where)],
            check=True
        )

    compat_file = where / "scripts" / "native_compat.py"
    source = compat_file.read_text(encoding="utf-8")

    # The registry looks for `const MIXINS_REV: &str = "<40 hex>"` in Horizon's source. When Horizon changes how it
    # declares that (different type, `static`, `[u8]`, a Cargo.toml `rev = ...`), the lookup returns None and crashes.
    # Replace every `re.search(<MIXINS_REV pattern>, text).group(1)` with a tolerant helper.
    helper = (
        "\n\ndef _vesper_mixins_rev(text):\n"
        "    import re as _re\n"
        "    pats = [\n"
        "        r'(?:pub(?:\\([^)]*\\))?\\s+)?(?:const|static)\\s+MIXINS_REV\\b[^=]*=\\s*\"([0-9a-fA-F]{40})\"',\n"
        "        r'MIXINS_REV[^\\n]{0,80}?([0-9a-fA-F]{40})',\n"
        "        r'horizon-mixins[^\\n]*?\\brev\\s*=\\s*\"([0-9a-fA-F]{40})\"',\n"
        "    ]\n"
        "    for p in pats:\n"
        "        m = _re.search(p, text)\n"
        "        if m:\n"
        "            return m.group(1).lower()\n"
        "    ctx = [l for l in text.splitlines() if 'MIXINS' in l.upper()][:10]\n"
        "    raise SystemExit('Could not find MIXINS_REV in Horizon source. Lines mentioning it: ' + repr(ctx))\n"
    )
    import re as _re
    if "_vesper_mixins_rev" not in source:
        source, n = _re.subn(
            r"re\.search\([^\n]*?MIXINS_REV[^\n]*?,\s*text\)\.group\(1\)",
            "_vesper_mixins_rev(text)",
            source,
        )
        if n:
            source = source + helper
            compat_file.write_text(source, encoding="utf-8")
            print(f"Patched Horizon MIXINS_REV compatibility ({n} place(s)).")
        else:
            print("WARNING: could not find the MIXINS_REV lookup to patch; dumping it:")
            for i, line in enumerate(source.splitlines(), 1):
                if "MIXINS_REV" in line:
                    print(f"  native_compat.py:{i}: {line}")

    sys.path.insert(0, str(where / "scripts"))
    sys.path.insert(0, str(where / "native" / "tools"))

    import native_compat
    return native_compat

def prepare_tree(tree, native):
    """What horizon-mixins needs of a source folder: no links, and the commit it is (the newest Nightly)."""
    for path in tree.rglob("*"):
        if path.is_symlink():
            path.unlink()
    revision = native.latest_nightly()
    (tree / ".horizon-revision").write_text(revision)
    return revision


def select(nc, mode, listed, excluded):
    """The bundles to try, most downloaded first, and why each left-out one was left out: ([bundle], {id: why})."""
    left_out = {}
    if mode == "none":
        return [], left_out
    bundles = nc.published()
    keep = []
    for b in bundles:
        if b.id in excluded:
            left_out[b.id] = "excluded in native-mods-exclude.txt"
        elif mode == "listed" and b.id not in listed:
            left_out[b.id] = "not in native-mods.txt"
        elif b.api:
            left_out[b.id] = "needs an API package that only Horizon's launcher prepares"
        elif "post_build" in tomllib.loads(b.files["horizon-mixin.toml"].decode()):
            left_out[b.id] = "has a post-build step, which only Horizon's launcher runs"
        else:
            keep.append(b)
    # What needs a mod that was left out cannot be built either.
    names = {b.id for b in keep}
    while True:
        gone = [b for b in keep if any(n not in names and n in left_out for n in b.needs)]
        if not gone:
            break
        for b in gone:
            left_out[b.id] = "needs " + ", ".join(n for n in b.needs if n not in names)
            names.discard(b.id)
        keep = [b for b in keep if b.id in names]
    return keep, left_out


def build_set(nc, tool, tree, revision, bundles, compile_check):
    """(stage folder, the bundles in it, {id: why left out}) for `bundles`, or (None, [], why) when nothing builds."""
    why = {}
    if not bundles:
        return None, [], why
    stage, refused = nc.apply(tool, tree, bundles, revision, "vesper")
    why.update({i: f"horizon-mixins refused it: {d.get('code')}: {d.get('message')}" for i, d in refused.items()})
    keep = [b for b in bundles if b.id not in refused]
    if stage is None or not keep:
        return None, [], why
    if not compile_check:
        return stage, keep, why

    by_id = {b.id: b for b in keep}

    def needed(bundle, seen):
        for n in bundle.needs:
            if n in by_id and n not in seen:
                seen.add(n)
                needed(by_id[n], seen)
        return seen

    failed = nc.broken_compiling(tool, tree, keep, revision, needed)
    why.update({i: f"does not compile on this Horizon: {d.get('code')}: {d.get('message')}" for i, d in failed.items()})
    keep = [b for b in keep if b.id not in failed]
    if not keep:
        return None, [], why
    stage, _ = nc.apply(tool, tree, keep, revision, "vesper-final")
    if stage is None or not nc.compiles(stage):
        log("The game does not compile with the mods that are left.")
        return None, [], why
    return stage, keep, why


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--mode", choices=("all", "listed", "none"), default="all")
    ap.add_argument("--no-compile", dest="compile", action="store_false", help="trust horizon-mixins; skip cargo check")
    ap.add_argument("--registry", type=Path, default=Path("horizon-mods"), help="where the registry's scripts are cloned")
    args = ap.parse_args()

    listed = set(ids_in(HERE / "native-mods.txt"))
    excluded = set(ids_in(HERE / "native-mods-exclude.txt"))
    tree = args.src.resolve()
    ladder = {"all": ["all", "listed", "none"], "listed": ["listed", "none"], "none": ["none"]}[args.mode]

    nc = None
    stage, applied, left_out, used = None, [], {}, "none"
    revision = ""
    tool = None
    if args.mode != "none":
        nc = load_registry(args.registry.resolve())
        revision = prepare_tree(tree, nc.native)
        log(f"Horizon {revision[:7]}")
    for mode in ladder:
        if mode == "none":
            break
        bundles, left_out = select(nc, mode, listed, excluded)
        if not bundles:
            log(f"Nothing to build in mode `{mode}`.")
            continue
        tool = tool or nc.mixins_tool(bundles)
        stage, applied, why = build_set(nc, tool, tree, revision, bundles, args.compile)
        left_out.update(why)
        if stage is not None:
            used = mode
            break
        log(f"Mode `{mode}` did not build: trying the next smaller set.")

    shutil.rmtree(args.out, ignore_errors=True)
    shutil.copytree(stage if stage is not None else tree, args.out, symlinks=False)

    meta = {}
    if applied:
        index = json.loads(nc.native.get(nc.INDEX))
        meta = {p["id"]: p for p in index.get("projects", [])}
    mods = []
    for b in applied:
        p = meta.get(b.id, {})
        mods.append({"id": b.id, "name": p.get("name", b.id), "version": b.version, "summary": p.get("summary", ""), "icon": p.get("icon") or ""})
    mods.sort(key=lambda m: m["name"].lower())
    discord = os.environ.get("VESPER_DISCORD_APP_ID", "").strip().isdigit()
    manifest = {"mods": mods, "discord": discord, "horizon": revision, "mode": used}
    (args.out / "vesper-mods.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    report = [f"## Native mods in this build: {len(mods)}", "",
              f"Mode: `{used}`" + (f" (asked for `{args.mode}`)" if used != args.mode else "") + f", Horizon `{revision[:7] or 'n/a'}`.", ""]
    if mods:
        report += ["Built in: " + ", ".join(f"`{m['id']}`" for m in mods), ""]
    if left_out:
        report += ["Left out:", ""] + [f"- `{i}`: {w}" for i, w in sorted(left_out.items())]
    text = "\n".join(report) + "\n"
    (args.out / "native-report.md").write_text(text, encoding="utf-8")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(text)
    log(text)


if __name__ == "__main__":
    main()
