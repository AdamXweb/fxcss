#!/usr/bin/env python3
"""fxcss - a testing toolkit for Firefox userChrome.css themes.

    fxcss try          download a theme from GitHub and test-drive it
    fxcss new          start a theme from a small, working scaffold
    fxcss init         add PR previews and CI checks to your theme repo
    fxcss tweaks       screenshot every install option, into a committable doc
    fxcss watch        edit CSS and see it live, no restart
    fxcss pick         click any part of the UI to get its CSS selector
    fxcss inspect      look up a selector you already have
    fxcss audit        find selectors that no longer match, and suggest fixes
    fxcss changelog    diff two Firefox builds to see what chrome changed
    fxcss snapshot     record this Firefox's chrome names, to diff against later
    fxcss catalogue    build a directory of themeable UI parts
    fxcss shot         capture a set of screenshots
    fxcss compare      diff two sets into before/after/diff images
    fxcss doctor       report what this Firefox supports

Run `fxcss <command> --help` for the options of each.
"""

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from . import core
from . import __version__

LANDING = """fxcss - a testing toolkit for Firefox userChrome.css themes

  Building a theme?
    fxcss new my-theme             start from a small, working scaffold
    fxcss watch                    edit CSS, see it live, no restart
    fxcss pick                     click any part of the UI to get its selector

  Trying someone else's theme?
    fxcss try owner/repo           test-drive it in a throwaway profile
                                   (find themes: firefoxcss-store.github.io,
                                    r/FirefoxCSS)

  Maintaining a theme repository?
    fxcss init                     add before/after PR previews and CI checks
    fxcss tweaks                   screenshot every install option for your README
    fxcss audit                    find selectors Firefox has renamed

Run `fxcss --help` for the full command list, or `fxcss <command> --help`
for one command. Nothing here ever touches your real Firefox profile."""

TOOLBOX_KEY = {
    "darwin": "Cmd+Opt+Shift+I",
}.get(sys.platform, "Ctrl+Alt+Shift+I")


def _watched_files(theme: Path):
    for root in (theme / "chrome", theme / "custom"):
        if root.exists():
            for p in root.rglob("*"):
                if p.is_file() and p.suffix in (".css", ".svg"):
                    yield p


def _fingerprint(theme: Path):
    return {p: p.stat().st_mtime_ns for p in _watched_files(theme)}


def _install_signal_handlers():
    """Make a terminate signal exit like Ctrl-C, so the browser is shut down."""
    def _bail(signum, frame):
        raise KeyboardInterrupt
    for sig in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGHUP", None)):
        if sig is None:
            continue
        try:
            signal.signal(sig, _bail)
        except (ValueError, OSError):
            pass


PILLOW_HINT = (
    "error: `fxcss {cmd}` needs Pillow, which the base install leaves out.\n"
    "  pipx:  pipx inject fxcss pillow      (or reinstall: pipx install \"fxcss[images]\")\n"
    "  pip:   pip install pillow")


def _needs_pillow(command, exc):
    """A missing PIL becomes instructions; any other import error stays loud."""
    if getattr(exc, "name", None) not in ("PIL", "PIL.Image"):
        raise exc
    print(PILLOW_HINT.format(cmd=command), file=sys.stderr)
    return 2


def _parse_choice(raw, count, default):
    """Menu input -> zero-based index. Anything unusable means the default."""
    raw = (raw or "").strip()
    if not raw:
        return default
    try:
        index = int(raw) - 1
    except ValueError:
        return default
    return index if 0 <= index < count else default


def choose_firefox(explicit):
    """Resolve which Firefox to drive, asking when that is a real question.

    An explicit --firefox (path or channel name) or FIREFOX_BIN always wins,
    and non-interactive runs never prompt -- CI must not block on stdin. The
    menu appears only when several builds are installed and a human is at the
    terminal, because that is the one case where silently picking stable
    hides a capability people ask for.
    """
    if explicit or os.environ.get("FIREFOX_BIN"):
        return core.find_firefox(explicit)
    builds = core.discover_firefoxes()
    interactive = (sys.stdin.isatty() and sys.stdout.isatty()
                   and not os.environ.get("CI"))
    if len(builds) < 2 or not interactive:
        return core.find_firefox(None)

    default = 0
    print("Several Gecko builds are installed:")
    for i, build in enumerate(builds, 1):
        version = core.firefox_version(build["path"]) or "?"
        marker = "  (Enter)" if i - 1 == default else ""
        print(f"  {i}. {build['label']:<10} {version:<14} {build['path']}{marker}")
    try:
        raw = input(f"Test against [1-{len(builds)}]: ")
    except EOFError:
        raw = ""
    picked = builds[_parse_choice(raw, len(builds), default)]
    print(f"  using {picked['label']} — skip this menu with "
          f"--firefox {picked['label']}\n")
    return picked["path"]


def _references(theme, selector):
    from .audit import css_references
    return css_references(theme, selector)


def cmd_try(args):
    import shutil
    import tempfile
    from . import fetch

    _install_signal_handlers()
    owner, name = fetch.parse_repo(args.repo)

    if args.ref:
        ref, why = args.ref, f"ref {args.ref}"
        info = None
    else:
        info = fetch.resolve(owner, name)
        print()
        for line in fetch.humanise(info):
            print("  " + line)
        prefer = "commit" if args.commit else "release"
        ref, why = fetch.choose_ref(info, prefer)

    workdir = Path(tempfile.mkdtemp(prefix="fxcss-try-"))
    keep = args.keep.resolve() if args.keep else None
    try:
        print(f"\n  fetching {why} …")
        repo_root = fetch.download(owner, name, ref, workdir / "src")
        theme_root = fetch.find_theme_root(repo_root)
        if theme_root is None:
            print("\n  No chrome/userChrome.css anywhere in this repository, so "
                  "there is\n  nothing for Firefox to load. Is it a userChrome theme?")
            return 2

        facts = fetch.describe(repo_root, theme_root)
        where = theme_root.relative_to(repo_root)
        print(f"  theme found at {where if str(where) != '.' else 'the repository root'}"
              f"  ({facts['stylesheets']} stylesheets, {facts['bytes'] // 1024} KB)")

        if facts["scripts"]:
            print(f"\n  This theme ships {', '.join(facts['scripts'])}. fxcss does not run "
                  f"it —\n  it installs the files itself, which is all those scripts do.")
        if facts["flags"]:
            print("\n  Options its README documents:")
            for entry in facts["flags"]:
                print(f"    {entry['flag']:<6} {entry['text']}")
        if facts["variants"]:
            names = [v.stem for v in facts["variants"]]
            print(f"\n  Optional stylesheets you can layer on with --with:")
            print("    " + ", ".join(names))

        chosen = []
        if args.with_sheets:
            wanted = {w.strip().lower() for w in args.with_sheets.split(",") if w.strip()}
            by_name = {v.stem.lower(): v for v in facts["variants"]}
            missing = wanted - set(by_name)
            if missing:
                print(f"\n  error: no optional sheet named {', '.join(sorted(missing))}",
                      file=sys.stderr)
                return 2
            chosen = [by_name[w] for w in sorted(wanted)]

        if args.info:
            return 0

        firefox = choose_firefox(args.firefox)
        session = core.Session(theme_root, firefox, dark=args.dark,
                               native_menus=args.native_menus,
                               devtools=not args.no_devtools)
        if chosen:
            fetch.apply_variants(session.profile, chosen)
            print(f"  layering on: {', '.join(c.stem for c in chosen)}")

        with session:
            session.setup_window()
            version = session.info()["version"]
            print(f"\n  Firefox {version} — this is a throwaway profile; your own "
                  f"Firefox\n  profile has not been touched.")
            if args.shot:
                core.capture_views(session, args.shot.resolve())
                print(f"\n  wrote screenshots to {args.shot}")
                return 0
            print("  Ctrl-C here when you are done.\n")
            try:
                while True:
                    time.sleep(0.5)
            except KeyboardInterrupt:
                print("\n  closing.")
        return 0
    finally:
        if keep and not (workdir / "src").exists():
            print("  nothing was downloaded, so nothing was kept", file=sys.stderr)
            keep = None
        if keep:
            keep.parent.mkdir(parents=True, exist_ok=True)
            if keep.exists():
                shutil.rmtree(keep, ignore_errors=True)
            shutil.copytree(workdir / "src", keep)
            print(f"  kept the download at {keep}")
        shutil.rmtree(workdir, ignore_errors=True)


def cmd_new(args):
    from . import scaffold
    target = args.directory.resolve()
    try:
        created = scaffold.new_theme(target)
    except FileExistsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"Started a theme in {target}:")
    for rel in created:
        print(f"  {rel}")
    print()
    print("Next:")
    print(f"  cd {args.directory}")
    print("  fxcss watch        # open it live; edit chrome/userChrome.css and save")
    print("  fxcss pick         # click any part of the UI to get its selector")
    print("  fxcss init         # when it lives on GitHub, add PR previews")
    return 0


def cmd_tweaks(args):
    try:
        from . import tweaks
    except ImportError as exc:
        return _needs_pillow("tweaks", exc)
    theme = args.theme.resolve()
    if not (theme / "chrome" / "userChrome.css").exists():
        print(f"error: no chrome/userChrome.css under {theme}", file=sys.stderr)
        return 2
    available = core.find_variant_sheets(theme)
    if not available:
        print("This theme ships no optional stylesheets (looked in custom/, "
              "optional/, options/, extras/, variants/), so there is nothing "
              "to document.", file=sys.stderr)
        return 2
    variants = {slug: [path] for slug, path in available.items()}
    for combo in args.combo or []:
        try:
            variants.update(core.parse_variant_spec(combo, available))
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    firefox = choose_firefox(args.firefox)
    out = args.out.resolve()
    print(f"fxcss tweaks\n  theme: {theme}\n  out:   {out}\n")
    with core.Session(theme, firefox, dark=args.dark) as session:
        entries = tweaks.build(session, theme, out, variants, tweaks.readme_flags(theme))
    stale = [e["slug"] for e in entries if not e["image"]]
    print(f"\n  wrote {out / 'TWEAKS.md'} with {len(entries)} option(s)")
    if stale:
        print(f"  note: {', '.join(stale)} changed nothing — possibly stale, "
              f"worth a look")
    print("  Commit the folder and link TWEAKS.md from your README.")
    return 0


def cmd_init(args):
    from . import scaffold
    theme = args.theme.resolve()
    if not (theme / "chrome" / "userChrome.css").exists():
        print(f"error: no chrome/userChrome.css under {theme} - run this from "
              f"your theme's root, or pass --theme", file=sys.stderr)
        return 2
    slugs = sorted(core.find_variant_sheets(theme))
    written, skipped = scaffold.write_workflows(
        theme, slugs, watch=args.watch, showcase=args.showcase, force=args.force)
    print(scaffold.next_steps(written, skipped, slugs, args.watch, args.showcase))
    return 0


def cmd_watch(args):
    theme = args.theme.resolve()
    firefox = choose_firefox(args.firefox)
    _install_signal_handlers()

    print(f"fxcss watch\n  theme:   {theme}\n  firefox: {firefox}")
    if args.native_menus is False:
        print("  menus:   XUL (themeable) — right-click to inspect them live")

    # An empty userChrome.css hands the stylesheet entirely to us, so replacing
    # it reflects deletions as well as additions: a rule you remove really
    # disappears instead of lingering from the copy loaded at startup.
    session = core.Session(theme, firefox, dark=args.dark,
                           native_menus=args.native_menus,
                           empty_user_chrome=True, devtools=not args.no_devtools)
    with session:
        session.setup_window()
        session.reload_theme()
        print("\n  theme loaded. Saving any file under chrome/ reloads it.")
        if not args.no_devtools:
            print(f"  Browser Toolbox: press {TOOLBOX_KEY} in the browser window")
            print("  to inspect the UI itself (not page content).")
        print("  Ctrl-C to stop.\n")

        state = _fingerprint(theme)
        reloads = 0
        try:
            while True:
                time.sleep(args.interval)
                current = _fingerprint(theme)
                if current == state:
                    continue
                changed = sorted({p for p in set(current) | set(state)
                                  if current.get(p) != state.get(p)})
                state = current
                t0 = time.perf_counter()
                try:
                    session.reload_theme()
                except Exception as exc:  # a half-saved file must not kill the loop
                    print(f"  ! reload failed: {exc}")
                    continue
                reloads += 1
                names = ", ".join(p.name for p in changed[:3])
                if len(changed) > 3:
                    names += f" +{len(changed) - 3} more"
                print(f"  [{reloads:>3}] {names} -> reloaded in "
                      f"{(time.perf_counter() - t0) * 1000:.0f} ms")
                if args.shot:
                    args.shot.parent.mkdir(parents=True, exist_ok=True)
                    args.shot.write_bytes(session.m.screenshot())
                    print(f"        wrote {args.shot}")
        except KeyboardInterrupt:
            print("\n  stopping.")
    return 0


def cmd_pick(args):
    from . import probe
    theme = args.theme.resolve()
    firefox = choose_firefox(args.firefox)
    _install_signal_handlers()
    print(f"fxcss pick\n  theme:   {theme}\n  firefox: {firefox}")
    with core.Session(theme, firefox, dark=args.dark,
                      native_menus=args.native_menus, devtools=True) as session:
        session.setup_window()
        return probe.pick(session, theme, _references)


def cmd_inspect(args):
    from . import probe
    theme = args.theme.resolve()
    firefox = choose_firefox(args.firefox)
    with core.Session(theme, firefox, dark=args.dark, devtools=True) as session:
        session.setup_window()
        return probe.inspect_selector(session, args.selector, theme, _references)


def cmd_audit(args):
    from . import audit
    theme = args.theme.resolve()
    firefox = choose_firefox(args.firefox)
    print(f"fxcss audit\n  theme:   {theme}\n  firefox: {firefox}\n")
    static = None if args.no_unused else audit.collect_unused(theme)
    with core.Session(theme, firefox) as session:
        result = audit.audit(session, theme)

    unused = None
    if static:
        # Ask an *unthemed* Firefox which custom properties it knows about.
        # Against the themed browser every name resolves, because the theme
        # itself set them, and nothing could be told apart.
        candidates = (set(static["used"]) - set(static["defined"])) | (
            set(static["defined"]) - set(static["used"]))
        known = set()
        if candidates:
            with core.Session(theme, firefox, empty_user_chrome=True) as vanilla:
                # Sweep the same states first: panel-scoped properties like
                # --arrowpanel-background do not exist until the panel has been
                # built, and would otherwise look like names Firefox never had.
                audit.collect_dom(vanilla, verbose=False)
                known = audit.probe_properties(vanilla, candidates)
        unused = audit.classify_unused(static, known)
    audit.report(result, show_all=args.all, colour=not args.no_colour)
    if unused:
        audit.report_unused(unused, colour=not args.no_colour, show_all=args.all)
    if args.patch:
        files = audit.write_patch(result, theme, args.patch.resolve())
        if files:
            print(f"  wrote {args.patch} ({files} file(s)); review it, then:")
            print(f"    git apply {args.patch}\n")
        else:
            print("  nothing confident enough to patch\n")
    actionable = [f for f in result["findings"] if f["confidence"] != "unresolved"]
    if args.strict and actionable:
        print(f"  --strict: failing because {len(actionable)} selector(s) need attention\n")
        return 1
    return 0


def cmd_snapshot(args):
    import json
    from . import audit
    theme = args.theme.resolve()
    firefox = choose_firefox(args.firefox)
    with core.Session(theme, firefox) as session:
        snap = audit.make_snapshot(session, verbose=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(snap, indent=1) + "\n", encoding="utf-8")
    print(f"\n  Firefox {snap['version']}: {len(snap['ids'])} ids, "
          f"{len(snap['classes'])} classes")
    print(f"  wrote {args.out}")
    return 0


def cmd_changelog(args):
    from . import audit
    theme = args.theme.resolve()
    if not args.baseline and not args.against:
        print("error: pass --against <firefox> or --baseline <snapshot.json>",
              file=sys.stderr)
        return 2
    after_bin = choose_firefox(args.firefox)

    snapshots = {}
    if args.baseline:
        data, dom = audit.load_snapshot(args.baseline)
        snapshots["before"], snapshots["before_version"] = dom, data["version"]
        print(f"fxcss changelog\n  before: {args.baseline} (Firefox {data['version']})")
        print(f"  after:  {after_bin}\n")
        with core.Session(theme, after_bin) as session:
            snapshots["after_version"] = session.info()["version"]
            print(f"  after — Firefox {snapshots['after_version']}")
            snapshots["after"] = audit.collect_dom(session)
    else:
        before_bin = core.find_firefox(args.against)
        print(f"fxcss changelog\n  before: {before_bin}\n  after:  {after_bin}\n")
        for label, binary in (("before", before_bin), ("after", after_bin)):
            with core.Session(theme, binary) as session:
                version = session.info()["version"]
                print(f"  {label} — Firefox {version}")
                snapshots[label] = audit.collect_dom(session)
                snapshots[label + "_version"] = version

    tokens = audit.extract_tokens(theme)
    delta = audit.changelog(snapshots["before"], snapshots["after"], tokens)

    print(f"\n  Firefox {snapshots['before_version']} → {snapshots['after_version']}")
    print(f"    {len(delta['removed'])} chrome names gone, {len(delta['added'])} new")

    affected = delta.get("affects_theme") or []
    if affected:
        print(f"\n  {len(affected)} of them are used by this theme:")
        for token in affected:
            first = tokens[token][0]
            print(f"    {token:<44} {first['file']}:{first['line']}")
        print("\n  Run `fxcss audit` for suggested replacements.")
    else:
        print("\n  None of them are used by this theme.")

    if args.show_all:
        print("\n  gone:")
        for token in delta["removed"]:
            print(f"    - {token}")
        print("\n  new:")
        for token in delta["added"]:
            print(f"    + {token}")
    print()
    return 0


def cmd_shot(args):
    theme = args.theme.resolve()
    if not (theme / "chrome" / "userChrome.css").exists():
        print(f"error: no chrome/userChrome.css under {theme}", file=sys.stderr)
        return 2
    urls = getattr(args, "url", [])
    if getattr(args, "only_live", False) and not urls:
        print("error: --only-live needs at least one --url", file=sys.stderr)
        return 2
    variants = {}
    spec = getattr(args, "variants", None)
    if spec:
        try:
            variants = core.parse_variant_spec(spec, core.find_variant_sheets(theme))
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if not variants:
            print("  note: --variants all found no optional stylesheets", flush=True)

    firefox = choose_firefox(args.firefox)
    with core.Session(theme, firefox) as session:
        if not getattr(args, "only_live", False):
            core.capture_views(session, args.out.resolve(), variants=variants)
        if urls:
            print("\n  live sites (captured, never compared):", flush=True)
            core.capture_live(session, args.out.resolve(), urls)
    print(f"\nwrote screenshots to {args.out}")
    return 0


def cmd_compare(args):
    try:
        from . import compare
    except ImportError as exc:
        return _needs_pillow("compare", exc)
    return compare.run(args.base.resolve(), args.head.resolve(),
                       args.out.resolve(), args.platform)


def cmd_catalogue(args):
    try:
        from . import catalogue
    except ImportError as exc:
        return _needs_pillow("catalogue", exc)
    theme = args.theme.resolve()
    firefox = choose_firefox(args.firefox)
    out = args.out.resolve()
    with core.Session(theme, firefox, native_menus=args.native_menus) as session:
        result = catalogue.build(session, theme, out,
                                 self_contained=args.self_contained)
    found = sum(1 for e in result["entries"]
                if any(m["found"] for m in e["modes"].values()))
    print(f"\n{found}/{len(result['entries'])} landmarks resolved")
    print(f"open {out / 'index.html'}")
    if args.open:
        opener = {"darwin": "open", "win32": "start"}.get(sys.platform, "xdg-open")
        subprocess.run([opener, str(out / "index.html")], check=False)
    return 0


def cmd_doctor(args):
    theme = args.theme.resolve()
    firefox = choose_firefox(args.firefox)
    print(f"fxcss:   {__version__}")
    print(f"theme:   {theme}")
    print(f"firefox: {firefox}")
    with core.Session(theme, firefox, devtools=True) as session:
        info = session.info()
    print(f"\nversion: {info['version']} (build {info['buildID']}) on {info['os']}")
    print(f"window:  {info['outer']} at dpr {info['dpr']}")
    print(f"userChrome.css enabled: {info['legacyStylesheets']}")
    print(f"Browser Toolbox: enabled in the temp profile ({TOOLBOX_KEY})")

    native = info["nativeContextMenus"]
    print("\ncontext menus:")
    for platform, value in native.items():
        if value is None:
            continue
        state = "native (CSS cannot style them)" if value else "XUL (themeable)"
        print(f"  {platform:<8} native-context-menus={value} -> {state}")
    if native.get("macos"):
        print("  note: on macOS this defaults to true, so menupopup rules have no\n"
              "        effect on right-click menus there. Use --native-menus=false\n"
              "        to work on them.")

    sheets = sorted((theme / "chrome").rglob("*.css")) if (theme / "chrome").exists() else []
    total = sum(p.stat().st_size for p in sheets)
    print(f"\ntheme: {len(sheets)} stylesheets, {total:,} bytes")
    missing = [p for p in ("chrome/userChrome.css",) if not (theme / p).exists()]
    print("missing expected files: " + (", ".join(missing) if missing else "none"))
    builds = core.discover_firefoxes()
    if builds:
        print("\ninstalled Gecko builds:")
        for build in builds:
            version = core.firefox_version(build["path"]) or "?"
            in_use = "  <- in use" if build["path"] == firefox else ""
            print(f"  {build['label']:<10} {version:<14} {build['path']}{in_use}")
        if len(builds) > 1:
            print("  pick one per run with --firefox <name>, e.g. --firefox "
                  + builds[-1]["label"])

    print(f"\nnew here? `fxcss try owner/repo` test-drives a theme; `fxcss init`")
    print("adds PR previews to yours.")
    return 0


def _bool(v):
    return v.lower() not in ("false", "0", "no")


def _common(p, theme=True):
    if theme:
        p.add_argument("--theme", type=Path, default=Path.cwd(),
                       help="theme root, the folder containing chrome/ (default: cwd)")
    p.add_argument("--firefox", default=None,
                   help="a channel or fork name (stable, beta, dev, nightly, esr, librewolf, floorp, waterfox, zen) or a path to a binary "
                        "(default: ask if several are installed)")


def _menus(p):
    p.add_argument("--native-menus", dest="native_menus", default=None, type=_bool,
                   metavar="BOOL",
                   help="false makes right-click menus XUL, so a theme can style them")


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="fxcss", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"fxcss {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=False)

    for verb, helptext in (("try", "download a theme from GitHub and test-drive it"),
                           ("install", "alias for try")):
        tr = sub.add_parser(
            verb, help=helptext,
            epilog="Looking for themes? Browse firefoxcss-store.github.io or "
                   "r/FirefoxCSS — anything with a userChrome.css on GitHub works.")
        tr.add_argument("repo", help="owner/name, or a github.com URL")
        tr.add_argument("--firefox", default=None,
                        help="a channel/fork name (e.g. nightly, dev, esr) or a binary path")
        tr.add_argument("--ref", default=None, help="tag, branch or commit to fetch")
        tr.add_argument("--commit", action="store_true",
                        help="use the latest commit rather than the latest release")
        tr.add_argument("--with", dest="with_sheets", default=None, metavar="NAME[,NAME]",
                        help="also load named optional stylesheets")
        tr.add_argument("--dark", action="store_true", help="start in dark mode")
        tr.add_argument("--shot", type=Path, default=None,
                        help="capture screenshots instead of opening interactively")
        tr.add_argument("--keep", type=Path, default=None,
                        help="keep the downloaded theme at this path")
        tr.add_argument("--info", action="store_true",
                        help="report what was found and stop, without launching")
        tr.add_argument("--no-devtools", action="store_true")
        _menus(tr)
        tr.set_defaults(func=cmd_try)

    nw = sub.add_parser("new", help="start a theme from a small, working scaffold")
    nw.add_argument("directory", type=Path, help="directory to create")
    nw.set_defaults(func=cmd_new)

    tw = sub.add_parser("tweaks",
                        help="screenshot every install option into a committable doc")
    tw.add_argument("--theme", type=Path, default=Path.cwd(),
                    help="theme root, the folder containing chrome/ (default: cwd)")
    tw.add_argument("--firefox", default=None,
                    help="a channel/fork name (e.g. nightly, dev, esr) or a binary path")
    tw.add_argument("--out", type=Path, default=Path("docs/tweaks"),
                    help="output folder (default: docs/tweaks)")
    tw.add_argument("--combo", action="append", metavar="NAME+NAME",
                    help="also render options together, e.g. --combo "
                         "compact-tabs+tabs-swapclose; repeatable")
    tw.add_argument("--dark", action="store_true", help="render in dark mode")
    tw.set_defaults(func=cmd_tweaks)

    ini = sub.add_parser("init", help="add PR previews and CI checks to your theme repo")
    ini.add_argument("--theme", type=Path, default=Path.cwd(),
                     help="theme root, the folder containing chrome/ (default: cwd)")
    ini.add_argument("--watch", action="store_true",
                     help="also add the weekly Firefox release/beta/nightly audit")
    ini.add_argument("--showcase", action="store_true",
                     help="also add the on-release showcase screenshot workflow")
    ini.add_argument("--force", action="store_true",
                     help="replace workflow files that already exist")
    ini.set_defaults(func=cmd_init)

    w = sub.add_parser("watch", help="live-reload the theme as you edit")
    _common(w); _menus(w)
    w.add_argument("--dark", action="store_true", help="start in dark mode")
    w.add_argument("--interval", type=float, default=0.4, help="poll seconds")
    w.add_argument("--shot", type=Path, default=None,
                   help="also write a screenshot here after every reload")
    w.add_argument("--no-devtools", action="store_true",
                   help="do not enable the Browser Toolbox in the temp profile")
    w.set_defaults(func=cmd_watch)

    k = sub.add_parser("pick", help="click any part of the UI to get its selector")
    _common(k); _menus(k)
    k.add_argument("--dark", action="store_true", help="start in dark mode")
    k.set_defaults(func=cmd_pick)

    i = sub.add_parser("inspect", help="look up a selector you already have")
    _common(i)
    i.add_argument("selector", help="a CSS selector, e.g. '#urlbar' or '.tab-close-button'")
    i.add_argument("--dark", action="store_true", help="report dark-mode styles")
    i.set_defaults(func=cmd_inspect)

    a = sub.add_parser("audit", help="find selectors that no longer match anything")
    _common(a)
    a.add_argument("--patch", type=Path, default=None,
                   help="write the confident replacements as a unified diff")
    a.add_argument("--all", action="store_true",
                   help="also list tokens with no suggestion")
    a.add_argument("--no-colour", action="store_true", help="plain output")
    a.add_argument("--strict", action="store_true",
                   help="exit non-zero if any selector needs attention (for CI)")
    a.add_argument("--no-unused", action="store_true",
                   help="skip the unused/unreachable section")
    a.set_defaults(func=cmd_audit)

    cl = sub.add_parser("changelog",
                        help="diff two Firefox builds to see what chrome changed")
    _common(cl)
    cl.add_argument("--against", default=None,
                    help="path to the other firefox binary to compare with")
    cl.add_argument("--baseline", type=Path, default=None,
                    help="compare against a saved snapshot instead of a second browser")
    cl.add_argument("--show-all", action="store_true",
                    help="list every name that changed, not just ones the theme uses")
    cl.set_defaults(func=cmd_changelog)

    s = sub.add_parser("shot", help="capture the standard screenshot set")
    _common(s)
    s.add_argument("--out", type=Path, required=True)
    s.add_argument("--url", action="append", default=[], metavar="URL",
                   help="also capture the theme against a live site, light and "
                        "dark; repeatable. Written to <out>/live/ and never "
                        "included in a comparison")
    s.add_argument("--variants", default=None, metavar="all|NAME[,NAME]",
                   help="also capture one view per optional stylesheet from "
                        "the theme's custom/ (or optional/, variants/) folder")
    s.add_argument("--only-live", action="store_true",
                   help="capture just the --url views, skipping the standard set")
    s.set_defaults(func=cmd_shot)

    c = sub.add_parser("compare", help="diff two screenshot sets")
    _common(c, theme=False)
    c.add_argument("--base", type=Path, required=True)
    c.add_argument("--head", type=Path, required=True)
    c.add_argument("--out", type=Path, required=True)
    c.add_argument("--platform", default="local")
    c.set_defaults(func=cmd_compare)

    g = sub.add_parser("catalogue", help="build the themeable UI directory")
    _common(g); _menus(g)
    g.add_argument("--out", type=Path, default=Path("fxcss-catalogue"))
    g.add_argument("--open", action="store_true", help="open the result when done")
    g.add_argument("--self-contained", dest="self_contained", action="store_true",
                   help="also write catalogue.html with images inlined, as one file")
    g.set_defaults(func=cmd_catalogue)

    sn = sub.add_parser("snapshot", help="record this Firefox's chrome names")
    _common(sn)
    sn.add_argument("--out", type=Path, required=True)
    sn.set_defaults(func=cmd_snapshot)

    d = sub.add_parser("doctor", help="report what this Firefox supports")
    _common(d)
    d.set_defaults(func=cmd_doctor)

    args = ap.parse_args(argv)
    if args.cmd is None:
        # Bare `fxcss` used to be an argparse error. Greet by task instead:
        # the three reasons someone installs this are not obvious from a
        # subcommand list.
        print(LANDING)
        return 0
    # Long-running commands are a live log; keep them readable when piped.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
