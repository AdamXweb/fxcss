#!/usr/bin/env python3
"""fxcss - a testing toolkit for Firefox userChrome.css themes.

    fxcss try          download a theme from GitHub and test-drive it
    fxcss install      install a theme into your real Firefox profile
    fxcss uninstall    remove it again, restoring what was there before
    fxcss upgrade      fetch a newer version of the theme you installed
    fxcss rollback     put the previous version back
    fxcss profiles     list every Firefox profile and what is themed in it
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
    fxcss completions  print a shell completion script
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
    fxcss install owner/repo       keep it: install into your real profile,
                                   with a backup; uninstall restores it
    fxcss profiles --check         what is themed in each profile, and
                                   whether anything newer has been released
    fxcss upgrade                  take that newer version, keeping a way back
    fxcss rollback                 …and that is the way back

  Maintaining a theme repository?
    fxcss init                     add before/after PR previews and CI checks
    fxcss tweaks                   screenshot every install option for your README
    fxcss audit                    find selectors Firefox has renamed

Run `fxcss --help` for the full command list, or `fxcss <command> --help`
for one command. Only `fxcss install` touches your real Firefox profile —
everything else runs in throwaway profiles."""

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


def _toolbar_ops(args):
    """Parse --toolbar, turning a bad spec into a message rather than a stack."""
    spec = getattr(args, "toolbar", None)
    if not spec:
        return None
    try:
        return core.parse_toolbar_spec(spec)
    except ValueError as exc:
        raise SystemExit(f"error: --toolbar: {exc}")


def _apply_toolbar(session, ops):
    """Rearrange a live window, reporting widget ids Firefox does not know.

    Worth reporting rather than ignoring: CustomizableUI accepts any string and
    writes it into the placements, so a typo is otherwise invisible -- the
    window simply comes up unchanged.
    """
    if not ops:
        return
    result = session.m.script(core.APPLY_TOOLBAR, [ops]) or {}
    if result.get("unknown"):
        print(f"  no such toolbar widget: {', '.join(result['unknown'])}",
              flush=True)
    if result.get("applied"):
        print(f"  toolbar: {', '.join(result['applied'])}", flush=True)
    if result.get("overflowing"):
        print("  the nav bar overflowed; some widgets are behind the chevron",
              flush=True)


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
        interactive = (sys.stdin.isatty() and sys.stdout.isatty()
                       and not os.environ.get("CI"))
        ref, why = _choose_ref(info, prefer, interactive)

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
            # A test drive is throwaway, so this says what will happen and
            # goes ahead: seeing two colour themes cancel out is a perfectly
            # good way to learn that they do.
            _sheet_conflicts(chosen)

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
            _apply_toolbar(session, _toolbar_ops(args))
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


def _profile_line(profile, number=None, marker=""):
    """One profile as a line: which Firefox it belongs to, then where it is.

    The kind is the point of this: `default-release` and `dev-edition-default`
    are a letter apart in a list of hashed directory names, and installing a
    theme into the wrong one looks exactly like the theme not working.
    """
    lead = f"  {number}. " if number is not None else "  "
    kind = f"[{profile['kind']}]" if profile.get("kind") else "[unrecognised]"
    return f"{lead}{profile['name']:<22} {kind:<26} {profile['path']}{marker}"


def complete_module():
    from . import complete
    return complete


def cmd_completions(args):
    shell = args.shell or Path(os.environ.get("SHELL", "")).name
    text = complete_module().script(shell)
    if text is None:
        supported = ", ".join(complete_module().SHELLS)
        print(f"error: no completion script for {shell or 'this shell'} "
              f"(supported: {supported})", file=sys.stderr)
        return 2
    print(text)
    return 0


def _complete_entry(rest):
    """`fxcss __complete <cword> <word>...`, called by the shell scripts.

    Handled before argparse sees anything: the words being completed are a
    half-typed command line, so feeding them to a parser would mean an error
    message printed over someone's prompt on a stray Tab.
    """
    try:
        cword = int(rest[0]) if rest else 0
    except (ValueError, IndexError):
        return 0
    for line in complete_module().complete_line(rest[1:], cword):
        print(line)
    return 0


def _print_profiles():
    from . import install
    profiles = install.discover_profiles()
    if not profiles:
        print("No Firefox profiles found (no profiles.ini in the usual "
              "places).", file=sys.stderr)
        return 2
    print("Firefox profiles:")
    for profile in profiles:
        print(_profile_line(profile,
                            marker="  (default)" if profile["default"] else ""))
    if any(not p.get("kind") for p in profiles):
        print("\n  [unrecognised] means the directory name does not carry one "
              "of Firefox's\n  channel suffixes — usually a profile someone "
              "named themselves.")
    return 0


def survey_profiles():
    """Every profile on this machine, with whatever is themed in it.

    Read-only, and deliberately says "I don't know" in three distinguishable
    ways: no chrome/ at all, a chrome/ fxcss did not write (someone installed
    a theme by hand -- worth reporting, since `install` would move it aside),
    and a chrome/ with a manifest fxcss can speak for.
    """
    from . import install

    rows = []
    for profile in install.discover_profiles():
        chrome = Path(profile["path"]) / "chrome"
        manifest = install.read_manifest(profile["path"])
        row = dict(profile, manifest=manifest, state="empty", drift=None,
                   running=install.firefox_running(profile["path"]))
        if manifest:
            row["state"] = "managed"
            row["drift"] = install.drift(profile["path"], manifest)
        elif chrome.is_dir() and any(chrome.rglob("*.css")):
            row["state"] = "unmanaged"
            row["files"] = sum(1 for p in chrome.rglob("*") if p.is_file())
        rows.append(row)
    return rows


def _theme_label(manifest):
    """`owner/name @ ref`, or the raw theme string when that is all there is."""
    source = manifest.get("source") or {}
    if source.get("kind") == "github" and source.get("owner"):
        return f"{source['owner']}/{source['name']} @ {source.get('ref')}"
    if source.get("kind") == "local":
        return source.get("path") or "a local directory"
    return manifest.get("theme") or "unknown"


def _drift_note(row):
    """One phrase for how far chrome/ has moved since it was installed."""
    drift, manifest = row["drift"], row["manifest"]
    total = len(manifest.get("files") or [])
    if drift is None:
        return f"{total} file(s)"
    bits = [f"{total} file(s)"]
    if not drift["checked"] and total:
        # Schema-1: the files are listed but were never hashed, so whether
        # they still match is genuinely unknown and must not read as "clean".
        bits.append("not checked for edits (installed by an older fxcss)")
    elif drift["modified"]:
        bits.append(f"{len(drift['modified'])} edited since install")
    if drift["missing"]:
        bits.append(f"{len(drift['missing'])} missing")
    if drift["extra"]:
        bits.append(f"{len(drift['extra'])} added by hand")
    return ", ".join(bits)


def cmd_profiles(args):
    from . import fetch, install

    rows = survey_profiles()
    if not rows:
        print("No Firefox profiles found (no profiles.ini in the usual "
              "places).", file=sys.stderr)
        return 2

    # One lookup per distinct theme, not per profile: the same theme in three
    # profiles is one question for GitHub.
    updates = {}
    if args.check:
        for row in rows:
            source = (row["manifest"] or {}).get("source") or {}
            key = (source.get("owner"), source.get("name"))
            if source.get("kind") != "github" or key in updates:
                continue
            try:
                updates[key] = fetch.update_state(
                    source, fetch.resolve(source["owner"], source["name"]))
            except RuntimeError as exc:
                updates[key] = {"state": "unknown", "ref": None,
                                "label": "could not ask GitHub",
                                "detail": str(exc)[:60]}

    if args.json:
        import json
        payload = []
        for row in rows:
            source = (row["manifest"] or {}).get("source") or {}
            payload.append({
                "name": row["name"], "path": str(row["path"]),
                "kind": row["kind"], "default": row["default"],
                "state": row["state"], "running": row["running"],
                "theme": (row["manifest"] or {}).get("theme"),
                "source": source or None,
                "sheets": (row["manifest"] or {}).get("sheets") or [],
                "drift": row["drift"],
                "update": updates.get((source.get("owner"),
                                       source.get("name"))),
            })
        print(json.dumps(payload, indent=1))
        return 0

    print("\n  Firefox profiles on this machine\n")
    for row in rows:
        mark = "●" if row["default"] else " "
        kind = f"[{row['kind']}]" if row.get("kind") else "[unrecognised]"
        print(f"  {mark} {row['name']:<24} {kind}")
        print(f"    {row['path']}")
        if row["state"] == "managed":
            manifest = row["manifest"]
            source = manifest.get("source") or {}
            print(f"    theme    {_theme_label(manifest)}")
            # ref_kind only means something for a repo: "unknown" against a
            # local directory would read as a gap rather than as N/A.
            tracking = (f"  (tracking the {source['ref_kind']})"
                        if source.get("kind") == "github"
                        and source.get("ref_kind") in ("release", "branch")
                        else "")
            print(f"             installed {manifest.get('installed', '?')}"
                  f"{tracking}")
            if manifest.get("sheets"):
                print(f"    sheets   {', '.join(manifest['sheets'])}")
            print(f"    files    {_drift_note(row)}")
            update = updates.get((source.get("owner"), source.get("name")))
            if update and update["state"] == "available":
                detail = f"  — {update['detail']}" if update["detail"] else ""
                print(f"    update   {update['label']} available{detail}")
            elif update and update["state"] == "current":
                print("    update   up to date")
            elif update:
                print(f"    update   {update['label']}")
        elif row["state"] == "unmanaged":
            print(f"    chrome/  {row['files']} file(s), not installed by "
                  "fxcss")
            print("             `fxcss install` here would back this up first")
        else:
            print("    chrome/  none — no userChrome theme in this profile")
        if row["running"]:
            print("    note     Firefox is using this profile right now")
        print()

    if any(r["default"] for r in rows):
        print("  ● the profile Firefox opens by default")
    checkable = any(((r["manifest"] or {}).get("source") or {}).get("kind")
                    == "github" for r in rows)
    if not args.check and checkable:
        print("  Pass --check to ask GitHub whether anything newer exists.")
    return 0


def _install_source(owner, name, ref, info, explicit):
    """Record where a theme came from, precisely enough to fetch it again.

    The distinction that matters later is `ref_kind`: re-fetching a release
    means looking for a newer tag, while re-fetching a branch means looking at
    what that branch points at now. The string "main" cannot tell those apart,
    so it is settled here, where the answer is actually known, and written
    down. Nothing infers it afterwards.
    """
    source = {"kind": "github", "owner": owner, "name": name, "ref": ref,
              "ref_kind": "explicit" if explicit else "unknown",
              "resolved": ref, "path": None}
    if not info:
        return source
    release = (info.get("release") or {}).get("tag")
    if ref == release:
        source["ref_kind"] = "release"
    elif ref == info.get("default_branch"):
        source["ref_kind"] = "branch"
        # A branch has no version, so the commit it pointed at is the only
        # thing a later run can compare against to see it has moved.
        source["resolved"] = (info.get("commit") or {}).get("sha") or ref
    return source


def _choose_profile(explicit, interactive):
    """Which real profile to touch, asking only when that is a real question.

    Mirrors choose_firefox: an explicit --profile always wins, a clear default
    is used silently, and the menu appears only for a human at a terminal --
    CI must not block on stdin. Ambiguity without a terminal is an error
    rather than a guess, because this command edits a profile someone lives in.
    """
    from . import install
    profiles = install.discover_profiles()
    if explicit:
        try:
            return install.match_profile(profiles, explicit)
        except ValueError as exc:
            raise SystemExit(f"error: {exc}")
    if not profiles:
        raise SystemExit(
            "error: no Firefox profiles found. Start Firefox once to create "
            "one, or pass --profile <path>.")
    if len(profiles) == 1:
        return profiles[0]
    defaults = [p for p in profiles if p["default"]]
    if len(defaults) == 1:
        return defaults[0]
    if not interactive:
        raise SystemExit(
            "error: several Firefox profiles and no clear default; pass "
            "--profile <name-or-path> (--list-profiles shows them)")
    default = profiles.index(defaults[0]) if defaults else 0
    print("Several Firefox profiles exist:")
    for i, profile in enumerate(profiles, 1):
        marker = "  (Enter)" if i - 1 == default else ""
        print(_profile_line(profile, number=i, marker=marker))
    try:
        raw = input(f"Which profile [1-{len(profiles)}]: ")
    except EOFError:
        raw = ""
    return profiles[_parse_choice(raw, len(profiles), default)]


def parse_selection(raw, count):
    """Menu input -> zero-based indices, for a multiple-choice list.

    Accepts "1,3", "1 3", "all", and empty for none. Anything out of range or
    unreadable is dropped rather than guessed at: this picks stylesheets that
    get written into someone's profile, so a typo should under-select, never
    over-select. Order is the list's, not the order typed, and repeats collapse.
    """
    text = (raw or "").strip().lower()
    if not text:
        return []
    if text in ("all", "*", "a"):
        return list(range(count))
    picked = set()
    for token in text.replace(",", " ").split():
        try:
            index = int(token) - 1
        except ValueError:
            continue
        if 0 <= index < count:
            picked.add(index)
    return sorted(picked)


def _sheet_conflicts(chosen, indent="  "):
    """Report optional sheets that cannot all take effect. Returns the pairs.

    Printed the same way wherever sheets are picked, so `try`, `install` and
    `tweaks` describe the same situation in the same words -- only what they
    do about it differs. An empty list means nothing measurable was found,
    which is not the same as proof that a combination works: see fxcss.sheets.
    """
    from . import sheets as sheets_mod

    found = sheets_mod.conflicts(chosen)
    for report in found:
        print(f"\n{indent}{sheets_mod.describe(report)}")
        for example in report["examples"][:2]:
            print(f"{indent}  {example['selector']} {{ "
                  f"{example['property']}: {example['a']} }}  vs  "
                  f"{{ …: {example['b']} }}")
    return found


def _choose_sheets(variants, interactive):
    """Offer a theme's optional stylesheets, when there is a human to ask.

    Only reachable when --with was not given: an explicit flag is an answer
    and asking again would be rude. Silence (no terminal, or CI) means none,
    which is what installing without --with has always done.
    """
    if not interactive or not variants:
        return []
    print("\n  This theme ships optional stylesheets:")
    for i, sheet in enumerate(variants, 1):
        print(f"    {i}. {sheet.stem}")
    print("    Numbers separated by commas, `all`, or Enter for none.")
    # `all` is offered above and is right for most themes, but wrong for one
    # with a family of alternatives in it -- so a picked set is checked and
    # the question asked again, rather than the answer being refused.
    for attempt in range(3):
        try:
            raw = input("  Include: ")
        except EOFError:
            raw = ""
        chosen = [variants[i] for i in parse_selection(raw, len(variants))]
        clashing = [r for r in _sheet_conflicts(chosen, indent="    ")
                    if r["conflicting"]]
        if not clashing or attempt == 2:
            break
        print("\n    Pick one of each pair — the rest would have no effect.")
    if chosen:
        print(f"  including: {', '.join(s.stem for s in chosen)}")
    return chosen


def _choose_ref(info, prefer, interactive):
    """Which ref to install: the blessed release, or what the branch has now.

    Only asks when the answer is genuinely open -- both exist and the branch
    has moved on since the release. A release with nothing newer behind it is
    not a question, and neither is an explicit --commit or --ref.
    """
    from . import fetch
    options = fetch.ref_options(info)
    if prefer == "commit" or len(options) < 2 or not fetch.commit_is_newer(info):
        ref, why = fetch.choose_ref(info, prefer)
        if not interactive and len(options) > 1 and fetch.commit_is_newer(info):
            print("  note: the default branch has newer commits; --commit "
                  "installs those instead")
        return ref, why
    if not interactive:
        ref, why = fetch.choose_ref(info, prefer)
        print("  note: the default branch has newer commits than this "
              "release; --commit installs those instead")
        return ref, why

    print("\n  The default branch has moved on since the latest release:")
    for i, option in enumerate(options, 1):
        marker = "  (Enter)" if i == 1 else ""
        note = f"  {option['note']}" if option["note"] else ""
        print(f"    {i}. {option['label']:<28} {option['date']}{note}{marker}")
    try:
        raw = input(f"  Install [1-{len(options)}]: ")
    except EOFError:
        raw = ""
    picked = options[_parse_choice(raw, len(options), 0)]
    return picked["ref"], picked["why"]


def _confirm(question, interactive, assume_yes):
    """Enter means yes; only an explicit n declines. Never asks in CI."""
    if assume_yes or not interactive:
        return True
    try:
        raw = input(question + " [Y/n] ")
    except EOFError:
        raw = ""
    return not raw.strip().lower().startswith("n")


def cmd_install(args):
    import shutil
    import tempfile
    from . import fetch, install

    if args.list_profiles:
        return _print_profiles()
    if not args.repo:
        print("error: give a theme to install — a GitHub owner/name, a URL, "
              "or a local theme directory (or --list-profiles)",
              file=sys.stderr)
        return 2

    interactive = (sys.stdin.isatty() and sys.stdout.isatty()
                   and not os.environ.get("CI"))
    workdir = None
    try:
        local = Path(args.repo).expanduser()
        if local.is_dir():
            repo_root = local.resolve()
            theme_id = str(repo_root)
            source = {"kind": "local", "owner": None, "name": None,
                      "ref": None, "ref_kind": "unknown", "resolved": None,
                      "path": str(repo_root)}
            print(f"\n  installing from {repo_root}")
        else:
            try:
                owner, name = fetch.parse_repo(args.repo)
            except ValueError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
            info = None
            if args.ref:
                ref, why = args.ref, f"ref {args.ref}"
            else:
                info = fetch.resolve(owner, name)
                print()
                for line in fetch.humanise(info):
                    print("  " + line)
                prefer = "commit" if args.commit else "release"
                ref, why = _choose_ref(info, prefer, interactive)
            workdir = Path(tempfile.mkdtemp(prefix="fxcss-install-"))
            print(f"\n  fetching {why} …")
            repo_root = fetch.download(owner, name, ref, workdir / "src")
            theme_id = f"{owner}/{name}@{ref}"
            source = _install_source(owner, name, ref, info,
                                     explicit=bool(args.ref))

        theme_root = fetch.find_theme_root(repo_root)
        if theme_root is None:
            print("\n  No chrome/userChrome.css anywhere in this theme, so "
                  "there is\n  nothing for Firefox to load. Is it a "
                  "userChrome theme?")
            return 2

        facts = fetch.describe(repo_root, theme_root)
        chosen = []
        if args.with_sheets:
            wanted = {w.strip().lower()
                      for w in args.with_sheets.split(",") if w.strip()}
            by_name = {v.stem.lower(): v for v in facts["variants"]}
            missing = wanted - set(by_name)
            if missing:
                print(f"error: no optional sheet named "
                      f"{', '.join(sorted(missing))}; available: "
                      f"{', '.join(sorted(by_name)) or 'none'}",
                      file=sys.stderr)
                return 2
            chosen = [by_name[w] for w in sorted(wanted)]
            blocking = [r for r in _sheet_conflicts(chosen)
                        if r["conflicting"]]
            if blocking and not args.force:
                print("\n  Refusing to install stylesheets that cancel each "
                      "other out — only one\n  of them would have any effect, "
                      "and which one is decided by import\n  order rather "
                      "than by you. Pick one, or pass --force.",
                      file=sys.stderr)
                return 2
        else:
            chosen = _choose_sheets(facts["variants"], interactive)

        picked = _choose_profile(args.profile, interactive)
        print(f"\n  profile: {picked['name']}  ({picked['path']})")
        if install.firefox_running(picked["path"]):
            print("  note: Firefox appears to be running — the theme will "
                  "only show after a restart.")
        if not _confirm("  Install into this profile?", interactive, args.yes):
            print("  nothing installed.")
            return 1

        result = install.install_theme(theme_root, picked["path"], theme_id,
                                       sheets=chosen, source=source)
        if result["backup"]:
            print(f"  existing chrome/ saved as {result['backup']}")
        if result["sheets"]:
            print(f"  optional sheets: {', '.join(result['sheets'])}")
        print(f"  installed {len(result['files'])} file(s) into "
              f"{picked['path'] / 'chrome'}")
        print("\n  Restart Firefox to see it. `fxcss uninstall` puts "
              "everything back.")
        return 0
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        if workdir:
            shutil.rmtree(workdir, ignore_errors=True)


def cmd_uninstall(args):
    from . import install

    if args.list_profiles:
        return _print_profiles()
    interactive = (sys.stdin.isatty() and sys.stdout.isatty()
                   and not os.environ.get("CI"))
    picked = _choose_profile(args.profile, interactive)
    manifest = install.read_manifest(picked["path"])
    print(f"\n  profile: {picked['name']}  ({picked['path']})")
    if manifest and manifest.get("theme"):
        print(f"  installed theme: {manifest['theme']}")
    if install.firefox_running(picked["path"]):
        print("  note: Firefox appears to be running — the change will only "
              "show after a restart.")
    if not _confirm("  Remove the installed theme from this profile?",
                    interactive, args.yes):
        print("  nothing removed.")
        return 1
    try:
        result = install.uninstall_theme(picked["path"])
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if result["removed"]:
        print(f"  removed {result['removed']} file(s)")
    if result["restored"]:
        print(f"  restored your previous chrome/ from {result['restored']}")
    if result["moved_aside"]:
        print(f"  moved the current chrome/ to {result['moved_aside']} "
              "(fxcss could not prove it wrote it, so nothing is deleted)")
    if result["kept"]:
        print(f"  left {len(result['kept'])} file(s) fxcss did not write in "
              "chrome/; the backup stays put alongside them")
    print("\n  Restart Firefox to see the change.")
    return 0


def _report_drift(moved, indent="  "):
    """Say what has changed under chrome/ since the install, if anything."""
    if moved["modified"]:
        print(f"{indent}{len(moved['modified'])} file(s) edited since install:")
        for name in moved["modified"][:6]:
            print(f"{indent}  {name}")
        if len(moved["modified"]) > 6:
            print(f"{indent}  … and {len(moved['modified']) - 6} more")
    if moved["missing"]:
        print(f"{indent}{len(moved['missing'])} file(s) deleted since install")
    if moved["extra"]:
        print(f"{indent}{len(moved['extra'])} file(s) added by hand "
              "(these are left alone)")


def cmd_upgrade(args):
    import shutil
    import tempfile
    from . import fetch, install

    interactive = (sys.stdin.isatty() and sys.stdout.isatty()
                   and not os.environ.get("CI"))
    picked = _choose_profile(args.profile, interactive)
    manifest = install.read_manifest(picked["path"])
    print(f"\n  profile: {picked['name']}  ({picked['path']})")
    if not manifest:
        print("\n  Nothing fxcss installed in this profile, so there is "
              "nothing to\n  upgrade. `fxcss install <theme>` puts one here; "
              "`fxcss profiles`\n  shows what is where.", file=sys.stderr)
        return 2

    source = manifest.get("source") or {}
    print(f"  installed: {_theme_label(manifest)}")

    # What is there to move to?
    info = None
    if source.get("kind") == "local":
        local_root = Path(source.get("path") or "")
        if not (local_root / "chrome").is_dir():
            print(f"\n  error: the directory this was installed from is gone "
                  f"({local_root})", file=sys.stderr)
            return 2
        target_ref, why = None, f"the current contents of {local_root}"
        state = {"state": "unsupported"}
    elif source.get("kind") == "github":
        try:
            info = fetch.resolve(source["owner"], source["name"])
        except RuntimeError as exc:
            print(f"\n  error: {exc}", file=sys.stderr)
            return 2
        state = fetch.update_state(source, info)
        if args.ref:
            target_ref, why = args.ref, f"ref {args.ref}"
        elif args.commit:
            target_ref = info["default_branch"]
            why = f"latest commit on {info['default_branch']}"
        elif state["state"] == "available":
            target_ref, why = state["ref"], state["label"]
        else:
            target_ref, why = state.get("ref") or source.get("ref"), \
                state.get("label") or "what is installed"
        print(f"  upstream:  {state['label']}"
              + (f"  — {state['detail']}" if state.get("detail") else ""))
    else:
        print("\n  error: this install has no record of where it came from, "
              "so fxcss\n  cannot fetch a newer copy. Reinstall it with "
              "`fxcss install <theme>`.", file=sys.stderr)
        return 2

    # Has the user edited what is installed?
    moved = install.drift(picked["path"], manifest)
    if moved["modified"] or moved["missing"]:
        print()
        _report_drift(moved, indent="  ")
    elif not moved["checked"] and manifest.get("files"):
        print("\n  note: this was installed before fxcss recorded file "
              "hashes, so\n  whether it has been edited since cannot be "
              "checked.")

    nothing_to_do = (state["state"] == "current" and not args.ref
                     and not args.commit and source.get("kind") == "github")
    if args.check:
        if nothing_to_do:
            print("\n  Up to date.")
            return 0
        if state["state"] in ("pinned", "unknown"):
            print(f"\n  {state['detail'] or state['label']}")
            return 2 if state["state"] == "unknown" else 0
        print(f"\n  An upgrade is available: {why}")
        print("  Run `fxcss upgrade` to take it.")
        return 1
    if nothing_to_do:
        print("\n  Already up to date. Pass --ref to move somewhere else "
              "anyway.")
        return 0

    if (moved["modified"] or moved["missing"]) and not args.force:
        print("\n  Refusing to upgrade over edits fxcss did not make — the "
              "new version\n  would overwrite them. Save what you want, then "
              "pass --force.\n  (Files you *added* are never touched; only "
              "the ones above.)", file=sys.stderr)
        return 2

    workdir = None
    try:
        if source.get("kind") == "local":
            repo_root = local_root
        else:
            workdir = Path(tempfile.mkdtemp(prefix="fxcss-upgrade-"))
            print(f"\n  fetching {why} …")
            repo_root = fetch.download(source["owner"], source["name"],
                                       target_ref, workdir / "src")

        theme_root = fetch.find_theme_root(repo_root)
        if theme_root is None:
            print("\n  error: no chrome/userChrome.css in that version",
                  file=sys.stderr)
            return 2

        facts = fetch.describe(repo_root, theme_root)
        wanted = (args.with_sheets.split(",") if args.with_sheets is not None
                  else manifest.get("sheets") or [])
        wanted = [w.strip() for w in wanted if w.strip()]
        continuity = fetch.sheet_continuity(wanted, facts["variants"])
        if continuity["missing"]:
            print(f"\n  error: the new version has no optional sheet named "
                  f"{', '.join(continuity['missing'])}.\n  It was renamed or "
                  "dropped; upgrading would turn that option off silently.\n"
                  "  Available now: "
                  f"{', '.join(sorted(v.stem for v in facts['variants'])) or 'none'}\n"
                  "  Re-run with --with to choose from those, or --with '' "
                  "for none.", file=sys.stderr)
            return 2
        if continuity["kept"]:
            print(f"  keeping optional sheets: "
                  f"{', '.join(s.stem for s in continuity['kept'])}")
            # Checked against the *new* version's sheets, not the old ones: a
            # release that splits one option into two, or merges two into one,
            # can turn a combination that used to work into a pair that
            # cancels out, without anyone changing what they asked for.
            clashing = [r for r in _sheet_conflicts(continuity["kept"])
                        if r["conflicting"]]
            if clashing and not args.force:
                print("\n  Refusing to carry these over — in this version "
                      "they cancel each\n  other out. Choose with --with, or "
                      "pass --force.", file=sys.stderr)
                return 2

        if args.audit:
            from . import audit as audit_mod
            firefox = choose_firefox(args.firefox)
            print(f"\n  auditing the new version against {firefox} …")
            with core.Session(theme_root, firefox) as session:
                result = audit_mod.audit(session, theme_root)
            audit_mod.report(result, colour=not args.no_colour)
            actionable = [f for f in result["findings"]
                          if f["confidence"] != "unresolved"]
            if actionable and not args.force:
                print(f"  {len(actionable)} selector(s) in the new version "
                      "match nothing in this Firefox.\n  Pass --force to "
                      "upgrade anyway.", file=sys.stderr)
                return 1

        if install.firefox_running(picked["path"]):
            print("\n  note: Firefox appears to be running — the change will "
                  "only show after a restart.")
        if not _confirm(f"\n  Upgrade to {why}?", interactive, args.yes):
            print("  nothing changed.")
            return 1

        theme_id = (str(repo_root) if source.get("kind") == "local"
                    else f"{source['owner']}/{source['name']}@{target_ref}")
        new_source = (dict(source) if source.get("kind") == "local"
                      else _install_source(source["owner"], source["name"],
                                           target_ref, info,
                                           explicit=bool(args.ref)))
        result = install.install_theme(
            theme_root, picked["path"], theme_id,
            sheets=continuity["kept"], source=new_source,
            # The chrome/ from before fxcss ever ran here, carried forward so
            # `uninstall` still reaches it however many upgrades happen.
            origin_backup=manifest.get("origin_backup"))
        print(f"\n  upgraded to {why}")
        print(f"  the previous version is kept as {result['backup']}")
        keep = args.keep
        if keep is not None and keep < 1:
            # This command has just promised a way back; honouring --keep 0
            # literally would delete the backup named one line above it.
            print("  note: --keep 0 would remove the backup this upgrade just "
                  "made, so\n  one is kept — the original chrome/ is never "
                  "pruned either way.")
            keep = 1
        removed = install.prune_backups(
            picked["path"], keep, protect=[manifest.get("origin_backup")])
        if removed:
            print(f"  pruned {len(removed)} older backup(s), keeping the "
                  f"newest {keep}")
        print("\n  Restart Firefox to see it. `fxcss rollback` puts the "
              "previous version back.")
        return 0
    except RuntimeError as exc:
        print(f"\n  error: {exc}", file=sys.stderr)
        return 2
    finally:
        if workdir:
            shutil.rmtree(workdir, ignore_errors=True)


def cmd_rollback(args):
    from . import install

    interactive = (sys.stdin.isatty() and sys.stdout.isatty()
                   and not os.environ.get("CI"))
    picked = _choose_profile(args.profile, interactive)
    backups = install.list_backups(picked["path"])
    manifest = install.read_manifest(picked["path"])
    print(f"\n  profile: {picked['name']}  ({picked['path']})")
    if manifest:
        print(f"  installed: {_theme_label(manifest)}")

    if not backups:
        print("\n  No backups here to roll back to. `fxcss install` and "
              "`fxcss upgrade`\n  both leave one behind; nothing else does.",
              file=sys.stderr)
        return 2

    origin = (manifest or {}).get("origin_backup")
    if args.list:
        print("\n  Backups, newest first:\n")
        for backup in backups:
            what = backup["theme"] or "your own chrome/, from before fxcss"
            tag = "  (the original)" if backup["name"] == origin else ""
            print(f"    {backup['name']}{tag}")
            print(f"      {what}")
        print("\n  `fxcss rollback --to <name>` restores one of these.")
        return 0

    target = args.to or backups[0]["name"]
    chosen = next((b for b in backups if b["name"] == target), None)
    if chosen is None:
        print(f"\n  error: no backup named {target} here; --list shows them",
              file=sys.stderr)
        return 2

    what = chosen["theme"] or "your own chrome/, from before fxcss ran here"
    print(f"\n  rolling back to {chosen['name']}\n    {what}")
    if chosen["manifest"] is None:
        print("  that has no fxcss theme in it, so the user.js block goes "
              "too")
    if install.firefox_running(picked["path"]):
        print("  note: Firefox appears to be running — the change will only "
              "show after a restart.")
    if not _confirm("  Roll back to this?", interactive, args.yes):
        print("  nothing changed.")
        return 1

    try:
        summary = install.rollback_to(picked["path"], chosen["name"])
    except RuntimeError as exc:
        print(f"  error: {exc}", file=sys.stderr)
        return 2
    print(f"\n  restored {summary['restored']}")
    if summary["moved_aside"]:
        print(f"  what was installed is kept as {summary['moved_aside']}, so "
              "this is undoable")
    if summary["user_js"] == "restored":
        print("  user.js: put that version's prefs back")
    elif summary["user_js"] == "removed":
        print("  user.js: removed the fxcss block")
    elif summary["user_js"] == "unknown":
        print("  user.js: left as it is — that version did not record its "
              "prefs")
    print("\n  Restart Firefox to see the change.")
    return 0


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

    # A --combo that cannot take effect would be screenshotted and written
    # into TWEAKS.md as though it were an option someone could choose. Say so
    # before spending a browser session on it; the author decides whether the
    # combination was the point.
    for slug, sheets_in_combo in variants.items():
        if len(sheets_in_combo) > 1 and _sheet_conflicts(sheets_in_combo):
            print(f"    …that is `{slug}`, which TWEAKS.md would show as one "
                  "option.\n")

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
        theme, slugs, watch=args.watch, showcase=args.showcase,
        previews=args.previews, force=args.force)
    print(scaffold.next_steps(written, skipped, slugs, args.watch, args.showcase,
                              args.previews))
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
        _apply_toolbar(session, _toolbar_ops(args))
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

    toolbar = _toolbar_ops(args)
    firefox = choose_firefox(args.firefox)
    with core.Session(theme, firefox) as session:
        if not getattr(args, "only_live", False):
            core.capture_views(session, args.out.resolve(), variants=variants,
                               toolbar=toolbar)
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


def build_parser():
    """The full argument parser.

    Split out from main() so shell completion can read the real
    subcommands and options straight off it. Anything added below is
    completable the moment it exists -- a hand-kept list of flags in a
    shell script would start drifting on the next commit.
    """
    ap = argparse.ArgumentParser(
        prog="fxcss", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"fxcss {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=False)

    tr = sub.add_parser(
        "try", help="download a theme from GitHub and test-drive it",
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
    tr.add_argument("--toolbar", default=None, metavar="SPEC",
                    help="rearrange the toolbar, e.g. 'new-tab-button>nav-bar'")
    tr.add_argument("--no-devtools", action="store_true")
    _menus(tr)
    tr.set_defaults(func=cmd_try)

    ins = sub.add_parser(
        "install", help="install a theme into your real Firefox profile",
        epilog="The profile's existing chrome/ is backed up first, and a "
               "manifest records every file written, so `fxcss uninstall` "
               "puts everything back. Before 0.13, `install` was an alias "
               "for `try`.")
    ins.add_argument("repo", nargs="?", default=None,
                     help="owner/name, a github.com URL, or a local theme directory")
    ins.add_argument("--ref", default=None, help="tag, branch or commit to fetch")
    ins.add_argument("--commit", action="store_true",
                     help="use the latest commit rather than the latest release")
    ins.add_argument("--with", dest="with_sheets", default=None, metavar="NAME[,NAME]",
                     help="also install named optional stylesheets")
    ins.add_argument("--profile", default=None, metavar="NAME-OR-PATH",
                     help="which Firefox profile (default: the one Firefox itself opens)")
    ins.add_argument("--list-profiles", action="store_true",
                     help="list the Firefox profiles found and stop")
    ins.add_argument("--force", action="store_true",
                     help="install optional sheets even when they cancel "
                          "each other out")
    ins.add_argument("--yes", action="store_true",
                     help="skip the confirmation prompt")
    ins.set_defaults(func=cmd_install)

    un = sub.add_parser(
        "uninstall", help="remove an installed theme, restoring what was there",
        epilog="Removes exactly the files `fxcss install` recorded in its "
               "manifest and restores the chrome/ backup. Files fxcss did "
               "not write are never deleted.")
    un.add_argument("--profile", default=None, metavar="NAME-OR-PATH",
                    help="which Firefox profile (default: the one Firefox itself opens)")
    un.add_argument("--list-profiles", action="store_true",
                    help="list the Firefox profiles found and stop")
    un.add_argument("--yes", action="store_true",
                    help="skip the confirmation prompt")
    un.set_defaults(func=cmd_uninstall)

    up = sub.add_parser(
        "upgrade", help="fetch a newer version of the installed theme",
        epilog="Re-installs the theme this profile already has, at whatever "
               "is newest of the kind it tracks. The version being replaced "
               "is kept as a chrome.backup-*, so `fxcss rollback` undoes it. "
               "Refuses to overwrite files you have edited yourself unless "
               "--force says to.")
    _common(up, theme=False)      # --firefox, for --audit
    up.add_argument("--profile", default=None, metavar="NAME-OR-PATH",
                    help="which Firefox profile (default: the one Firefox itself opens)")
    up.add_argument("--check", action="store_true",
                    help="report only, changing nothing. Exit 0 when up to "
                         "date, 1 when an upgrade is available, 2 when it "
                         "cannot be told — for cron and CI")
    up.add_argument("--audit", action="store_true",
                    help="before installing, check the new version's "
                         "selectors against a real Firefox")
    up.add_argument("--ref", default=None,
                    help="upgrade to this tag, branch or commit instead")
    up.add_argument("--commit", action="store_true",
                    help="take the latest commit rather than the latest release")
    up.add_argument("--with", dest="with_sheets", default=None,
                    metavar="NAME[,NAME]",
                    help="optional sheets to install (default: the ones "
                         "already installed)")
    up.add_argument("--keep", type=int, default=3, metavar="N",
                    help="how many backups to keep (default 3; the original "
                         "chrome/ is never pruned)")
    up.add_argument("--force", action="store_true",
                    help="upgrade despite local edits or audit findings")
    up.add_argument("--no-colour", action="store_true", help="plain output")
    up.add_argument("--yes", action="store_true",
                    help="skip the confirmation prompt")
    up.set_defaults(func=cmd_upgrade)

    rb = sub.add_parser(
        "rollback", help="put the previous version of the theme back",
        epilog="Restores a chrome.backup-* left by an install or an upgrade. "
               "What is currently installed becomes a backup in its turn, so "
               "a rollback can itself be rolled back.")
    rb.add_argument("--profile", default=None, metavar="NAME-OR-PATH",
                    help="which Firefox profile (default: the one Firefox itself opens)")
    rb.add_argument("--to", default=None, metavar="NAME",
                    help="which backup (default: the most recent)")
    rb.add_argument("--list", action="store_true",
                    help="list the backups and stop")
    rb.add_argument("--yes", action="store_true",
                    help="skip the confirmation prompt")
    rb.set_defaults(func=cmd_rollback)

    pr = sub.add_parser(
        "profiles", help="list every Firefox profile and what is themed in it",
        epilog="Read-only. Reports what `fxcss install` recorded in each "
               "profile, and says so plainly when a profile has a chrome/ "
               "folder fxcss did not write.")
    pr.add_argument("--check", action="store_true",
                    help="ask GitHub whether a newer release or commit exists")
    pr.add_argument("--json", action="store_true",
                    help="machine-readable output")
    pr.set_defaults(func=cmd_profiles)

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
    ini.add_argument("--previews", action="store_true",
                     help="also add the workflow that keeps README screenshots "
                          "of every view and variant up to date")
    ini.add_argument("--force", action="store_true",
                     help="replace workflow files that already exist")
    ini.set_defaults(func=cmd_init)

    w = sub.add_parser("watch", help="live-reload the theme as you edit")
    _common(w); _menus(w)
    w.add_argument("--dark", action="store_true", help="start in dark mode")
    w.add_argument("--interval", type=float, default=0.4, help="poll seconds")
    w.add_argument("--shot", type=Path, default=None,
                   help="also write a screenshot here after every reload")
    w.add_argument("--toolbar", default=None, metavar="SPEC",
                   help="rearrange the toolbar before watching, e.g. "
                        "'new-tab-button>nav-bar'")
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
    s.add_argument("--out", type=Path, required=True,
                   help="directory for the captures, written FLAT as "
                        "<out>/<view>.png (--url captures go to <out>/live/). "
                        "Note `fxcss compare` writes a different shape: "
                        "comparisons at its own --out, plus a normalised copy "
                        "of every head capture under <out>/full/")
    s.add_argument("--url", action="append", default=[], metavar="URL",
                   help="also capture the theme against a live site, light and "
                        "dark; repeatable. Written to <out>/live/ and never "
                        "included in a comparison")
    s.add_argument("--toolbar", default=None, metavar="SPEC",
                   help="arrangement for the toolbar view, e.g. "
                        "'new-tab-button>nav-bar, -downloads-button'")
    s.add_argument("--variants", default=None, metavar="all|NAME[,NAME]",
                   help="also capture one view per optional stylesheet from "
                        "the theme's custom/ (or optional/, variants/) folder")
    s.add_argument("--only-live", action="store_true",
                   help="capture just the --url views, skipping the standard set")
    s.set_defaults(func=cmd_shot)

    c = sub.add_parser("compare", help="diff two screenshot sets")
    _common(c, theme=False)
    c.add_argument("--base", type=Path, required=True,
                   help="a directory of captures from `fxcss shot`")
    c.add_argument("--head", type=Path, required=True,
                   help="the directory to compare against --base")
    c.add_argument("--out", type=Path, required=True,
                   help="directory for the results: one stacked "
                        "before/after/diff image per CHANGED view, a "
                        "summary.json, and <out>/full/ holding a normalised "
                        "copy of every head capture (changed or not). To "
                        "publish plain screenshots, read <out>/full/ here or "
                        "`fxcss shot`'s --out directly")
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

    cp = sub.add_parser(
        "completions", help="print a shell completion script",
        epilog="bash:  eval \"$(fxcss completions bash)\"   (add to ~/.bashrc)\n"
               "zsh:   eval \"$(fxcss completions zsh)\"    (add to ~/.zshrc)\n"
               "fish:  fxcss completions fish | source     (add to config.fish)",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    cp.add_argument("shell", nargs="?", choices=complete_module().SHELLS,
                    help="which shell (default: guess from $SHELL)")
    cp.set_defaults(func=cmd_completions)

    d = sub.add_parser("doctor", help="report what this Firefox supports")
    _common(d)
    d.set_defaults(func=cmd_doctor)

    return ap


def main(argv=None):
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] == "__complete":
        return _complete_entry(raw[1:])
    ap = build_parser()
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
