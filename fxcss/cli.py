#!/usr/bin/env python3
"""fxcss - a testing toolkit for Firefox userChrome.css themes.

    fxcss watch        edit CSS and see it live, no restart
    fxcss pick         click any part of the UI to get its CSS selector
    fxcss inspect      look up a selector you already have
    fxcss catalogue    build a directory of themeable UI parts
    fxcss shot         capture a set of screenshots
    fxcss compare      diff two sets into before/after/diff images
    fxcss doctor       report what this Firefox supports

Run `fxcss <command> --help` for the options of each.
"""

import argparse
import signal
import subprocess
import sys
import time
from pathlib import Path

from . import core

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


def _references(theme, selector):
    from .catalogue import css_references
    return css_references(theme, selector)


def cmd_watch(args):
    theme = args.theme.resolve()
    firefox = core.find_firefox(args.firefox)
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
    firefox = core.find_firefox(args.firefox)
    _install_signal_handlers()
    print(f"fxcss pick\n  theme:   {theme}\n  firefox: {firefox}")
    with core.Session(theme, firefox, dark=args.dark,
                      native_menus=args.native_menus, devtools=True) as session:
        session.setup_window()
        return probe.pick(session, theme, _references)


def cmd_inspect(args):
    from . import probe
    theme = args.theme.resolve()
    firefox = core.find_firefox(args.firefox)
    with core.Session(theme, firefox, dark=args.dark, devtools=True) as session:
        session.setup_window()
        return probe.inspect_selector(session, args.selector, theme, _references)


def cmd_shot(args):
    theme = args.theme.resolve()
    if not (theme / "chrome" / "userChrome.css").exists():
        print(f"error: no chrome/userChrome.css under {theme}", file=sys.stderr)
        return 2
    firefox = core.find_firefox(args.firefox)
    with core.Session(theme, firefox) as session:
        core.capture_views(session, args.out.resolve())
    print(f"\nwrote screenshots to {args.out}")
    return 0


def cmd_compare(args):
    from . import compare
    return compare.run(args.base.resolve(), args.head.resolve(),
                       args.out.resolve(), args.platform)


def cmd_catalogue(args):
    from . import catalogue
    theme = args.theme.resolve()
    firefox = core.find_firefox(args.firefox)
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
    firefox = core.find_firefox(args.firefox)
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
    return 0


def _bool(v):
    return v.lower() not in ("false", "0", "no")


def _common(p, theme=True):
    if theme:
        p.add_argument("--theme", type=Path, default=Path.cwd(),
                       help="theme root, the folder containing chrome/ (default: cwd)")
    p.add_argument("--firefox", default=None,
                   help="path to the firefox binary (default: autodetect, or $FIREFOX_BIN)")


def _menus(p):
    p.add_argument("--native-menus", dest="native_menus", default=None, type=_bool,
                   metavar="BOOL",
                   help="false makes right-click menus XUL, so a theme can style them")


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="fxcss", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

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

    s = sub.add_parser("shot", help="capture the standard screenshot set")
    _common(s)
    s.add_argument("--out", type=Path, required=True)
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

    d = sub.add_parser("doctor", help="report what this Firefox supports")
    _common(d)
    d.set_defaults(func=cmd_doctor)

    args = ap.parse_args(argv)
    # Long-running commands are a live log; keep them readable when piped.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
