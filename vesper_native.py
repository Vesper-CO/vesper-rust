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
import re
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


# Injected into the registry's native_compat.py. Horizon now declares `MIXINS_REV` as a pointer
# (`pub const MIXINS_REV: &str = horizon_platform::mod_package::mixins_tool::REV;`), so the 40-hex commit has to be
# looked up where `REV` itself is defined, in the Horizon source tree (path in $VESPER_HORIZON_SRC).
HELPER = r'''

def _vesper_mixins_rev(text):
    import os as _os, re as _re, subprocess as _sp
    hexrev = r'"([0-9a-fA-F]{40})"'
    pats = [
        r'(?:pub(?:\([^)]*\))?\s+)?(?:const|static)\s+MIXINS_REV\b[^=]*=\s*' + hexrev,
        r'horizon-mixins[^\n]*?\brev\s*=\s*' + hexrev,
    ]
    for p in pats:
        m = _re.search(p, text)
        if m:
            return m.group(1).lower()
    # Pointer: look for `const REV: &str = "<40 hex>"` in the source tree, preferring the mixins_tool module.
    root = _os.environ.get("VESPER_HORIZON_SRC", "")
    rev_pat = _re.compile(r'(?:pub(?:\([^)]*\))?\s+)?(?:const|static)\s+REV\s*:\s*&(?:\'static\s+)?str\s*=\s*' + hexrev)
    best = None
    if root:
        for dp, dn, fn in _os.walk(root):
            dn[:] = [d for d in dn if d not in (".git", "target", "node_modules")]
            for f in fn:
                if not f.endswith(".rs"):
                    continue
                path = _os.path.join(dp, f)
                try:
                    src = open(path, encoding="utf-8", errors="ignore").read()
                except OSError:
                    continue
                m = rev_pat.search(src)
                if m:
                    score = ("mixins_tool" in path) * 2 + ("mixins" in path.lower())
                    if best is None or score > best[0]:
                        best = (score, m.group(1).lower(), path)
    if best:
        print("MIXINS_REV " + best[1] + " from " + best[2])
        return best[1]
    # Last resort: the tool's current head (bundles are normally tested against a pinned commit; this may differ).
    try:
        out = _sp.run(["git", "ls-remote", "https://github.com/horizon-rust/horizon-mixins", "HEAD"],
                      capture_output=True, text=True, check=True, timeout=60).stdout.split()
        if out and len(out[0]) == 40:
            print("WARNING: MIXINS_REV not found in source; using horizon-mixins HEAD " + out[0])
            return out[0].lower()
    except Exception as e:
        print("ls-remote failed:", e)
    ctx = [l for l in text.splitlines() if "MIXINS" in l.upper()][:10]
    raise SystemExit("Could not find MIXINS_REV. Lines mentioning it: " + repr(ctx))
'''


def load_registry(where):
    """Clone the registry scripts and make its MIXINS_REV lookup work with Horizon's current source."""
    if not (where / "scripts" / "native_compat.py").is_file():
        shutil.rmtree(where, ignore_errors=True)
        subprocess.run(
            ["git", "clone", "--depth", "1", "-q", REGISTRY_GIT, str(where)],
            check=True
        )

    compat_file = where / "scripts" / "native_compat.py"
    source = compat_file.read_text(encoding="utf-8")

    if "_vesper_mixins_rev" not in source:
        source, n = re.subn(
            r"re\.search\([^\n]*?MIXINS_REV[^\n]*?,\s*text\)\.group\(1\)",
            "_vesper_mixins_rev(text)",
            source,
        )
        if n:
            compat_file.write_text(source + HELPER, encoding="utf-8")
            print(f"Patched MIXINS_REV lookup ({n} place(s)).")
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


def strip_foreign_arch_sources(root):
    """The voice-chat mod's build script compiles every .c file under libopus, including the ARM and MIPS ones,
    which fail on x86 Windows (celt/arm/celt_fft_ne10.c needs NE10_dsp.h) and take the whole native build with them.
    Those files are only used by other CPUs, so they are removed from the copy that gets built."""
    n = 0
    for p in list((root / "native-data").glob("**/*.c")) if (root / "native-data").is_dir() else []:
        parts = {x.lower() for x in p.relative_to(root).parts[:-1]}
        if "opus" in parts and parts & {"arm", "mips", "aarch64"}:
            p.unlink()
            n += 1
    if n:
        log(f"Removed {n} non-x86 libopus source file(s).")


def fix_opus_for_msvc(root):
    """libopus declares its scratch buffers as C99 variable-length arrays (VAR_ARRAYS), which Microsoft's C compiler
    rejects (bands.c: error C2057 "expected constant expression"). Opus has an alloca() mode for this; select it
    when compiling with MSVC. Guarded by _MSC_VER, so the Linux/macOS builds are untouched."""
    patch = (
        "\n#if defined(_MSC_VER)\n"
        "/* Vesper: MSVC has no variable-length arrays; use alloca instead */\n"
        "# undef VAR_ARRAYS\n"
        "# ifndef USE_ALLOCA\n"
        "#  define USE_ALLOCA\n"
        "# endif\n"
        "# include <malloc.h>\n"
        "# ifndef alloca\n"
        "#  define alloca _alloca\n"
        "# endif\n"
        "#endif\n"
    )
    n = 0
    base = root / "native-data"
    for p in (base.glob("**/stack_alloc.h") if base.is_dir() else []):
        if "opus" not in {x.lower() for x in p.relative_to(root).parts[:-1]}:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        if "Vesper: MSVC" in text:
            continue
        m = re.search(r"^[ \t]*#[ \t]*define[ \t]+STACK_ALLOC_H\b[^\n]*\n", text, re.M)
        if not m:
            log(f"WARNING: no include guard in {p.relative_to(root)}; MSVC fix not applied")
            continue
        p.write_text(text[: m.end()] + patch + text[m.end():], encoding="utf-8")
        n += 1
    if n:
        log(f"Patched {n} libopus stack_alloc.h for MSVC.")


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
        os.environ["VESPER_HORIZON_SRC"] = str(tree)
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
    strip_foreign_arch_sources(args.out)
    fix_opus_for_msvc(args.out)

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
