#!/usr/bin/env python3
"""fxcss - a testing toolkit for Firefox userChrome.css themes.

    fxcss try          download a theme from GitHub and test-drive it
    fxcss install      install a theme into your real Firefox profile
    fxcss uninstall    remove it again, restoring what was there before
    fxcss adopt        take over a theme you installed some other way
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
    fxcss check        audit, capture and compare using saved project settings
    fxcss support      which Firefox releases a theme change still works on
    fxcss completions  print a shell completion script
    fxcss doctor       report what this Firefox supports

Run `fxcss <command> --help` for the options of each.
"""

import argparse
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

from . import core
from . import __version__
from .style import plural as _pl, agree as _agree

# Every command once: its group and the one line that describes it. Both
# `fxcss --help` and bare `fxcss` read this, so the two entry screens group,
# order and describe commands identically; the welcome screen only picks a
# subset and adds example arguments.
COMMANDS = {
    "try": "test-drive a theme from GitHub or a folder, in a throwaway profile",
    "install": "install a theme into your real Firefox profile, with a backup",
    "profiles": "list every Firefox profile and what is themed in it",
    "upgrade": "fetch a newer version of the theme you installed",
    "rollback": "put the previous version back",
    "adopt": "take over a theme you installed some other way",
    "uninstall": "remove the theme, restoring what was there before",
    "new": "start a theme from a small, working scaffold",
    "watch": "edit CSS and see it live, no restart",
    "pick": "click any part of the UI to get its CSS selector",
    "inspect": "look up a selector you already have",
    "init": "add PR previews and CI checks to your theme repo",
    "tweaks": "screenshot every install option into a committable doc",
    "audit": "find selectors that no longer match, and suggest fixes",
    "check": "audit, capture and compare using saved project settings",
    "shot": "capture a set of screenshots",
    "compare": "diff two sets into before/after/diff images",
    "support": "which Firefox releases a theme change still works on",
    "snapshot": "record this Firefox's chrome names, to diff against later",
    "changelog": "diff two Firefox builds to see what chrome changed",
    "catalogue": "build a directory of themeable UI parts",
    "doctor": "report what this Firefox supports",
    "completions": "print a shell completion script",
}

# (help heading, welcome question, commands, the welcome subset with
# example arguments). One order for both screens.
GROUPS = (
    ("Try or install a theme", "Trying someone else's theme?",
     ("try", "install", "profiles", "upgrade", "rollback", "adopt", "uninstall"),
     (("try", "owner/repo"), ("install", "owner/repo"), ("profiles", ""),
      ("upgrade", ""), ("rollback", ""), ("adopt", "owner/repo"), ("uninstall", ""))),
    ("Build a theme", "Building a theme?",
     ("new", "watch", "pick", "inspect"),
     (("new", "my-theme"), ("watch", ""), ("pick", ""))),
    ("Maintain a theme repository", "Maintaining a theme repository?",
     ("init", "tweaks", "audit", "check", "shot", "compare", "support",
      "snapshot", "changelog", "catalogue"),
     (("init", ""), ("tweaks", ""), ("audit", ""), ("check", ""))),
    ("Set up", None, ("doctor", "completions"), ()),
)

TAGLINE = "live-reload, inspect and screenshot-test Firefox userChrome.css themes"


def _command_row(name, summary, width):
    """`    name  summary`, the summary wrapped under itself on a narrow terminal.

    Only the command word is bold, so the eye runs down the names first.
    """
    from . import style
    lines = style.wrap(summary, indent=4 + width)
    words = name.split(" ")
    verb = 1 if words[0] != "fxcss" else 2
    shown = " ".join(words[:verb - 1] + [style.strong(words[verb - 1])] + words[verb:])
    return [f"    {shown}{' ' * (width - len(name))}{lines[0].lstrip()}"] + lines[1:]


def landing_text():
    """What bare `fxcss` prints: where to start, by what someone came to do."""
    from . import style
    examples = [(name, args) for _, q, _, subset in GROUPS if q for name, args in subset]
    width = max(len(f"fxcss {n} {a}".rstrip()) for n, a in examples) + 2
    out = [f"  {style.brand('fxcss')}  {TAGLINE}"]
    for title, question, _, subset in GROUPS:
        if not question:
            continue
        out.append("")
        out.append(style.heading_line(title))
        for name, args in subset:
            out.extend(_command_row(f"fxcss {name} {args}".rstrip(), COMMANDS[name], width))
    out.append("")
    for line in style.wrap("Find themes at firefoxcss-store.github.io and r/FirefoxCSS.", indent=2):
        out.append(style.muted(line))
    for line in style.wrap("Previews and tests use throwaway profiles; install, adopt, "
                           "upgrade, rollback and uninstall change the real profile "
                           "you choose.", indent=2):
        out.append(style.muted(line))
    out += ["",
            f"  {style.muted('Run')} {style.cmd('fxcss --help')} "
            f"{style.muted('for every command and its options.')}",
            ""]
    return "\n".join(out)


def help_text():
    """`fxcss --help`: every command once, grouped, with nothing generated."""
    from . import style
    width = max(len(n) for n in COMMANDS) + 2
    lead = f"fxcss {__version__}  "
    tagline = style.wrap(TAGLINE, indent=2 + len(lead))
    out = [f"  {style.brand('fxcss')} {style.muted(__version__)}  {tagline[0].lstrip()}",
           *tagline[1:],
           "",
           f"  {style.muted('Usage')}  fxcss <command> [options]"]
    for title, _, names, _ in GROUPS:
        out.append("")
        out.append(style.heading_line(title))
        for name in names:
            out.extend(_command_row(name, COMMANDS[name], width))
    out += ["",
            style.heading_line("Options"),
            f"    {'-h, --help'.ljust(width)}show this help",
            f"    {'--version'.ljust(width)}print the version",
            "",
            style.heading_line("Examples"),
            f"    {style.muted('$')} fxcss try AdamXweb/WhiteSurFirefoxThemeMacOS",
            f"    {style.muted('$')} fxcss watch --dark",
            f"    {style.muted('$')} fxcss audit --patch fix.diff",
            "",
            f"  {style.muted('Run')} {style.cmd('fxcss <command> --help')} "
            f"{style.muted('for its options.')}",
            "", ""]
    return "\n".join(out)


class _TopParser(argparse.ArgumentParser):
    """The `fxcss` parser, whose help is written rather than generated.

    argparse would list the subcommands twice (a `{try,install,...}` brace
    that wraps mid-word, then the positional block) and could not group them.
    Subparsers are plain ArgumentParsers, so `fxcss <command> --help` is
    unchanged.
    """

    def format_help(self):
        return help_text()

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


def _menu(question, note, count, row, default):
    """A numbered question, asked the same way by every command.

    A bold heading with a dim note, one line per choice, then `Which one?
    1-N [d]`. Enter takes the default, and so does anything unusable.
    `row(i, current, pointer)` draws choice i, `current` being the one Enter
    takes. At a terminal that can redraw, the arrow keys move a pointer over
    the rows as well; the typed answer works either way. Returns an index.
    """
    from . import prompt, style
    print()
    print(style.heading_line(question, note))
    height = sum(row(i, False, False).count("\n") + 1 for i in range(count)) + 2
    if prompt.arrows_ok(height):
        return prompt.pick_one(count, default, row)
    for i in range(count):
        print(row(i, current=i == default, pointer=False))
    print()
    try:
        raw = input(style.ask("Which one?", f"1-{count}", str(default + 1)))
    except EOFError:
        raw = ""
    return _parse_choice(raw, count, default)


def _menu_row(number, name, cells=(), default=False, path=False, pointer=False):
    """One choice: its number, the name in bold, then dim detail.

    The last cell is cut to the terminal rather than wrapped back under the
    number, so every choice stays on one line and the numbers stay a column;
    `path` says it is a path, which keeps its tail. A last cell with too
    little room left to say anything is left out rather than cut to a stub.
    Room for `(Enter)` is kept on every row, because with the arrow keys the
    marker moves, and a row must not change length as it passes.

    `pointer` puts the `›` in the margin and the name in the brand colour:
    the choice the arrow keys are on.
    """
    from . import style
    lead = f"{style.brand(style.POINTER) if pointer else ' '} {number:>2}. "
    width = len(f"  {number:>2}. ")
    cells = [c for c in cells if c]
    if cells:
        used = width + len(name) + sum(len(c) + 2 for c in cells[:-1]) + 2 + len("  (Enter)")
        room = style.term_width() - 1 - used
        if room < 16:
            cells.pop()
        else:
            cells[-1] = style.clip(cells[-1], room, path=path)
    title = style.brand(name) if pointer else style.strong(name)
    line = lead + title + "".join(f"  {style.muted(c)}" for c in cells)
    return line + (f"  {style.muted('(Enter)')}" if default else "")


def _skip_hint(value, option, verb="using"):
    """`using nightly  --firefox nightly skips this question`, after a menu.

    Every menu ends this way: what was picked, and the option that picks it
    next time, so a question answered once need never be asked again.
    """
    from . import style
    used = f"  {style.muted(verb)} {style.strong(value)}"
    hint = f"{style.cmd(option)}{style.muted(' skips this question')}"
    if len(f"  {verb} {value}  {option} skips this question") < style.term_width():
        return f"{used}  {hint}"
    return f"{used}\n  {hint}"


def _shared_parent(paths):
    """The folder every path sits in, and each path's name inside it.

    A menu of paths that all start the same way names that folder once in its
    heading, so the rows show only what differs. (None, paths) when they do
    not share one, or there is only one.
    """
    parents = {str(Path(p).parent) for p in paths}
    if len(paths) < 2 or len(parents) != 1:
        return None, list(paths)
    return parents.pop(), [Path(p).name for p in paths]


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

    from . import style
    default = 0
    versions = [core.firefox_version(build["path"]) or "?" for build in builds]
    label_w = max(len(build["label"]) for build in builds)
    version_w = max(len(v) for v in versions)
    folder, places = _shared_parent([style.short_path(_app_bundle(b["path"])) for b in builds])
    def row(i, current, pointer):
        return _menu_row(i + 1, builds[i]["label"].ljust(label_w),
                         [versions[i].ljust(version_w), places[i]],
                         default=current, pointer=pointer, path=True)

    note = f"{len(builds)} installed" + (f" in {folder}" if folder else "")
    picked = builds[_menu("Which Gecko build?", note, len(builds), row, default)]
    print(_skip_hint(picked["label"], f"--firefox {picked['label']}"))
    print()
    return picked["path"]


def _references(theme, selector):
    from .audit import css_references
    return css_references(theme, selector)



def _acquire(args, interactive, workdir, verb):
    """Bring what `args.repo` names to disk: a local folder as it is, or a
    repository from a supported host at the ref the person meant.

    Returns {repo_root, theme_id, source, info, why, remote} or None after
    printing why not. A branch in a /tree/<branch> URL is honoured and
    tracked as a branch; its folder part becomes the default --subpath.
    """
    from . import fetch, style

    command_name = {"trying": "try", "installing from": "install"}.get(verb, verb)
    local = Path(args.repo).expanduser()
    if local.is_dir():
        repo_root = local.resolve()
        print(style.title(command_name, style.short_path(repo_root)))
        if getattr(args, "ref", None) or getattr(args, "commit", False):
            print(style.muted("  --ref and --commit choose a hosted version; "
                              "using the folder as it is."))
        return {"repo_root": repo_root, "theme_id": str(repo_root), "info": None,
                "why": None, "remote": False,
                "source": {"kind": "local", "owner": None, "name": None, "ref": None,
                           "ref_kind": "unknown", "resolved": None, "path": str(repo_root)}}
    if args.repo.startswith(("/", "~", ".")) or "\\" in args.repo:
        print(f"error: no folder at {local}", file=sys.stderr)
        return None
    try:
        parsed = fetch.parse_repo_spec(args.repo)
    except fetch.RepoSpecError as exc:
        print(f"error: {exc} Or pass the path of a theme folder.", file=sys.stderr)
        return None
    owner, name, host = parsed["owner"], parsed["name"], parsed["host"]
    branch = parsed["ref"]
    if parsed["subpath"] and not getattr(args, "subpath", None):
        args.subpath = parsed["subpath"]
        print(f"  folder from the URL: {parsed['subpath']}")

    info = None
    if args.ref:
        ref, why = args.ref, f"ref {args.ref}"
    else:
        print(style.title(command_name, args.repo))
        info = fetch.resolve(owner, name, host=host, branch=branch)
        owner, name = info["owner"], info["name"]
        print()
        for line in fetch.humanise(info):
            print("  " + line)
        if branch:
            tracked = info.get("tracked") or {}
            if tracked.get("missing"):
                print(f"\n  error: {owner}/{name} has no branch named {branch!r}; "
                      "the URL may be stale. --ref chooses another.", file=sys.stderr)
                return None
            ref, why = branch, f"branch {branch}"
        else:
            prefer = "commit" if getattr(args, "commit", False) else "release"
            ref, why = _choose_ref(info, prefer, interactive)

    print(f"\n  fetching {why} …")
    repo_root = fetch.download(owner, name, fetch.snapshot_ref(ref, info), workdir / "src", host=host)
    label = f"{owner}/{name}@{ref}" if host == "github.com" else f"{host}/{owner}/{name}@{ref}"
    return {"repo_root": repo_root, "theme_id": label, "info": info, "why": why,
            "remote": True,
            "source": _install_source(owner, name, ref, info, explicit=bool(args.ref),
                                      host=host, branch=branch)}


def _plan_source(repo_root, args, interactive, workdir, remote=False, quiet=False):
    """Decide what to install from a source tree, asking only when it is a
    real choice. Returns (plan, theme_root) or None after printing."""
    from . import layout

    subpath = getattr(args, "subpath", None)
    entry = getattr(args, "entry", None)
    plan = layout.discover(repo_root, subpath=subpath, entry=entry, workdir=workdir / "layout")
    if plan["status"] == "ambiguous" and interactive and plan["editions"]:
        candidates = ([e for e in plan["editions"] if not e["demoted"] and not e["misnamed"]]
                      or plan["editions"])
        from . import style
        # The label is `key  (kind; stylesheets in where)  [warning]`: the
        # key is what to pick by, the rest is detail.
        parts = []
        for edition in candidates:
            key, _, detail = edition["label"].partition("  ")
            about, bracket, warning = detail.strip().partition("  [")
            if about.startswith("(") and about.endswith(")"):
                about = about[1:-1]
            parts.append((key, about.replace("; ", f" {style.DOT} ")
                          + (f"  [{warning}" if bracket else "")))
        key_w = max(len(key) for key, _ in parts)
        def row(i, current, pointer):
            key, about = parts[i]
            return _menu_row(i + 1, key.ljust(key_w), [about], default=current, pointer=pointer)

        chosen = candidates[_menu("Which theme?", f"this repository holds {len(candidates)}",
                                  len(candidates), row, 0)]
        print(_skip_hint(chosen["key"], f"--subpath {shlex.quote(chosen['key'])}"))
        plan = layout.discover(repo_root, subpath=chosen["key"], entry=entry,
                               workdir=workdir / "layout")
    if plan["status"] == "ambiguous":
        print(f"\n  {plan['reason']}", file=sys.stderr)
        return None
    if plan["status"] != "ok":
        if not plan["editions"] and not plan["archives"]:
            where = "repository" if remote else "folder"
            print(f"\n  No userChrome.css anywhere in this {where}, so there is\n"
                  f"  nothing for Firefox to load. Is it a userChrome theme?\n"
                  f"  ({plan['reason']})", file=sys.stderr)
        else:
            print(f"\n  Nothing fxcss can install here: {plan['reason']}", file=sys.stderr)
        return None
    theme_root = layout.materialize(plan, workdir / "layout")
    if not quiet:
        for line in layout.describe_plan(plan):
            print(f"  {line}")
    return plan, theme_root


def _inventory_report(theme_root, facts, strict, force, quiet=False):
    """Say what loads and what does not; False when `strict` forbids going on."""
    from . import inventory

    found = inventory.scan(theme_root / "chrome",
                           option_names={o["path"].name for o in facts.get("options", [])})
    if not quiet:
        for line in inventory.summary(found):
            print(f"  {line}")
    if found["entry_unresolved"]:
        names = ", ".join(f"{e['source']} → {e['target']}" for e in found["entry_unresolved"][:4])
        print("\n  The entry stylesheet imports file(s) this source does not contain:"
              f"\n    {names}\n  That usually means the theme needs building — a release "
              "package, or its\n  own installer's output — rather than a copy of its sources.",
              file=sys.stderr)
        if strict and not force:
            print("  Nothing was installed. Pass --force to install it as it is.",
                  file=sys.stderr)
            return None
    return found


def _match_sheets(raw, facts):
    """--with names (stems or relative paths) to option paths, or an error."""
    wanted = [w.strip() for w in raw.split(",") if w.strip()]
    options = facts.get("options", [])
    chosen = []
    for name in wanted:
        exact = [o for o in options if o["id"] == name]
        matches = exact or [o for o in options if o["stem"].lower() == name.lower()]
        if len(matches) != 1:
            label = "ambiguous" if matches else "unknown"
            available = ", ".join(o["id"] for o in (matches or options)) or "none"
            print(f"error: {label} optional sheet {name!r}; choose a relative path: "
                  f"{available}", file=sys.stderr)
            return None
        if matches[0]["path"] not in chosen:
            chosen.append(matches[0]["path"])
    return chosen


def _sheet_paths(chosen, facts):
    """Legacy labels mapped to IDs; clashing stems use the full path as label."""
    from collections import Counter
    ids = {o["path"].resolve(): o["id"] for o in facts.get("options", [])}
    counts = Counter(Path(sheet).stem for sheet in chosen)
    return {(Path(sheet).stem if counts[Path(sheet).stem] == 1 else ids[Path(sheet).resolve()]):
            ids.get(Path(sheet).resolve(), Path(sheet).name) for sheet in chosen}


def _sheet_ids(chosen, facts):
    return list(_sheet_paths(chosen, facts).values())


def _choose_prefs(raw, repo_root, theme_root):
    """--prefs values to repository-relative preference files, or an error."""
    from . import checklist

    wanted = [w.strip().replace("\\", "/") for w in (raw or "").split(",") if w.strip()]
    if not wanted:
        return []
    available = {entry["path"]: entry for entry in checklist.pref_files(repo_root, theme_root)}
    missing = [w for w in wanted if w not in available]
    if missing:
        print(f"error: no preference file at {', '.join(missing)}; the theme ships: "
              f"{', '.join(sorted(available)) or 'none'}", file=sys.stderr)
        return None
    for path in wanted:
        names = available[path]["names"]
        shown = ", ".join(names[:8]) + (f", … and {len(names) - 8} more" if len(names) > 8 else "")
        print(f"  --prefs {path}: {_pl(len(names), 'pref')} will join the fxcss block: {shown}")
    return wanted


def _source_rows(plan, facts, found, remote):
    """What was found, as one aligned block shared by try and install."""
    from . import layout, style
    pairs = []
    if plan.get("packaged"):
        pairs.append(("packaged", f"read from {plan['packaged']}"))
    selected = plan.get("selected") or {}
    kind = {layout.STANDARD: "chrome/", layout.FLAT: "flat layout",
            layout.CONTENT_ONLY: "userContent.css only",
            layout.NAMED: "named edition"}.get(selected.get("kind"), "theme")
    key = str(selected.get("key") or ".")
    where = (("the repository root" if remote else "the top of the folder")
             if key in (".", "") else key)
    size = style.muted(f"{style.plural(facts['stylesheets'], 'stylesheet')}, "
                       f"{facts['bytes'] // 1024:,} KB")
    named = (f" {selected['named']['name']!r}" if selected.get("kind") == layout.NAMED
             else "")
    flags = []
    if selected.get("demoted"):
        flags.append(style.warn(f"looks like {selected['demoted']}"))
    if selected.get("misnamed"):
        flags.append(style.warn("entry file name case differs: Linux needs userChrome.css exactly"))
    pairs.append(("layout", f"{kind}{named} at {where}  {size}"
                  + "".join(f"  {f}" for f in flags)))
    if found is not None:
        sheets = found["sheets"]
        chrome_only = sum(1 for v in sheets.values() if v["scope"] == ["chrome"])
        content_only = sum(1 for v in sheets.values() if v["scope"] == ["content"])
        both = len(sheets) - chrome_only - content_only
        parts = [f"{n} {label}" for n, label in ((chrome_only, "browser-window"),
                 (content_only, "web-content"), (both, "shared")) if n]
        pairs.append(("loads", style.plural(len(sheets), "stylesheet")
                      + (f"  {style.muted(', '.join(parts))}" if parts else "")))
        if found["conditional"]:
            pairs.append(("optional", f"{style.plural(len(found['conditional']), 'conditional import')}"
                          f" {style.muted('to files not shipped, off unless their pref or media applies')}"))
        if found["hooks"]:
            names = ", ".join(sorted({Path(e["resolved"]).name for e in found["hooks"]}))
            pairs.append(("user hook", f"{names}  {style.muted('not shipped, as intended')}"))
        if not found["complete"]:
            pairs.append(("incomplete", [style.warn(r) for r in found["reasons"]]))
    if plan.get("scripts"):
        pairs.append(("installer", f"{', '.join(plan['scripts'])}  "
                      + style.muted("fxcss copies the files itself and never runs it")))
    for note in plan.get("notes") or []:
        if note.startswith("ships ") and plan.get("scripts"):
            continue
        pairs.append(("note", note))
    return style.rows(pairs, indent=2, label_width=10)


def _checklist_items(repo_root, theme_root, plan, pref_files):
    from . import checklist
    return checklist.build(repo_root, theme_root, plan, applied_pref_files=pref_files)


def _report_checklist(repo_root, theme_root, plan, pref_files):
    from . import checklist

    items = checklist.build(repo_root, theme_root, plan, applied_pref_files=pref_files)
    for line in checklist.render(items):
        print(line)
    return items


def _layout_record(plan):
    return {"kind": plan["kind"], "subpath": plan["subpath"], "entry": plan["entry"]}


def cmd_try(args):
    import shutil
    import tempfile
    from . import fetch

    _install_signal_handlers()
    interactive = (sys.stdin.isatty() and sys.stdout.isatty()
                   and not os.environ.get("CI"))
    workdir = Path(tempfile.mkdtemp(prefix="fxcss-try-"))
    keep = args.keep.resolve() if args.keep else None
    try:
        got = _acquire(args, interactive, workdir, verb="trying")
        if got is None:
            return 2
        repo_root = got["repo_root"]
        planned = _plan_source(repo_root, args, interactive, workdir,
                               remote=got["remote"], quiet=True)
        if planned is None:
            return 2
        plan, theme_root = planned

        from . import style
        facts = fetch.describe(plan["repo_root"], theme_root, plan)
        found = _inventory_report(theme_root, facts, strict=False, force=True, quiet=True)
        print()
        for line in _source_rows(plan, facts, found, got["remote"]):
            print(line)

        if facts["variants"]:
            shown, groups = _sheet_groups(facts["variants"])
            names = [v.stem for v in shown]
            print()
            print(style.heading_line("Optional stylesheets",
                                     f"{len(names)} {style.DOT} layer any on with --with name,name"))
            for line in style.column_groups(names, groups, indent=4, numbered=False):
                print(line)
        if checklist_items := _checklist_items(plan["repo_root"], theme_root, plan, []):
            print()
            from . import checklist
            for line in checklist.render(checklist_items):
                print(line)
        if facts["flags"]:
            # Reference, not a choice: fxcss never runs the install script
            # these belong to, so they come last, descriptions quiet.
            print()
            print(style.heading_line("README install flags",
                                     "as the README words them, for its own script; "
                                     "fxcss never runs it"))
            width = max(len(entry["flag"]) for entry in facts["flags"]) + 2
            for entry in facts["flags"]:
                wrapped = style.wrap(entry["text"], indent=4 + width)
                print(f"    {entry['flag'].ljust(width)}{style.muted(wrapped[0].lstrip())}")
                for more in wrapped[1:]:
                    print(style.muted(more))

        chosen = []
        if args.with_sheets:
            chosen = _match_sheets(args.with_sheets, facts)
            if chosen is None:
                return 2
            # A test drive is throwaway, so this says what will happen and
            # goes ahead: seeing two colour themes cancel out is a perfectly
            # good way to learn that they do.
            _sheet_conflicts(chosen)

        if args.info:
            target = args.repo
            print()
            print(f"  {style.brand(style.ARROW)} {style.cmd(f'fxcss try {target}')} "
                  f"{style.muted('opens it in a throwaway profile;')} "
                  f"{style.cmd(f'fxcss install {target}')} {style.muted('keeps it')}")
            print()
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

def _short_path(path):
    """Home-relative, because the interesting part is the tail."""
    try:
        return "~/" + str(Path(path).relative_to(Path.home()))
    except ValueError:
        return str(path)


def _profile_notes(profile):
    """The facts that actually settle "which one is mine?".

    The kind alone was not enough. Someone with three profiles picked the
    wrong one because the (Enter) default was dev-edition-default while the
    Firefox they use every day was running on default-release. Which profile
    holds a lock right now answers that directly, and whether a theme is
    already installed says which one they have themed before.
    """
    from . import install
    notes = []
    try:
        if install.firefox_running(profile["path"]):
            notes.append("open in Firefox now")
    except OSError:
        pass
    state = profile_state(profile)
    if state == "managed":
        notes.append("fxcss theme installed")
    elif state == "unmanaged":
        notes.append("has its own chrome/")
    return notes


def _profile_line(profile, number=None, marker="", notes=None, colour=None,
                  name_width=20):
    """One profile as a line: which Firefox it belongs to, then where it is.

    The kind is the point of this: `default-release` and `dev-edition-default`
    are a letter apart in a list of hashed directory names, and installing a
    theme into the wrong one looks exactly like the theme not working.
    """
    from . import style
    lead = f"  {number:>2}. " if number is not None else "    "
    kind = profile.get("kind") or "unrecognised"
    line = (f"{lead}{style.strong(profile['name'].ljust(name_width))}  "
            f"{style.muted(kind.ljust(24))}  "
            f"{style.muted(_short_path(profile['path']))}{marker}")
    if notes:
        line += "\n" + " " * len(lead) + style.brand(f"{style.ARROW} " + ", ".join(notes))
    return line


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
    from . import style
    print(style.heading_line("Firefox profiles", f"{len(profiles)} found"))
    width = max(len(p["name"]) for p in profiles)
    for profile in profiles:
        print(_profile_line(profile, name_width=width,
                            marker=f"  {style.muted('opens by default')}" if profile["default"] else "",
                            notes=_profile_notes(profile)))
    if any(not p.get("kind") for p in profiles):
        print()
        for line in style.wrap("unrecognised: the folder name has none of Firefox's "
                               "channel suffixes, usually a profile someone named "
                               "themselves.", indent=2):
            print(style.muted(line))
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
    from .fetch import REMOTE_KINDS
    source = manifest.get("source") or {}
    if source.get("kind") in REMOTE_KINDS and source.get("owner"):
        host = source.get("host") or "github.com"
        prefix = "" if host == "github.com" else f"{host}/"
        return f"{prefix}{source['owner']}/{source['name']} @ {source.get('ref')}"
    if source.get("kind") == "local":
        return source.get("path") or "a local directory"
    return manifest.get("theme") or "unknown"


def _drift_note(row):
    """One phrase for how far chrome/ has moved since it was installed."""
    drift, manifest = row["drift"], row["manifest"]
    total = len(manifest.get("files") or [])
    if drift is None:
        return f"{_pl(total, 'file')}"
    bits = [f"{_pl(total, 'file')}"]
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
            if source.get("kind") not in fetch.REMOTE_KINDS or key in updates:
                continue
            try:
                updates[key] = fetch.update_state(
                    source, fetch.resolve(
                        source["owner"], source["name"],
                        host=source.get("host") or "github.com",
                        branch=source.get("ref") if source.get("ref_kind") == "branch" else None))
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

    from . import style
    default_note = any(r["default"] for r in rows)
    print(style.title("profiles"))
    parents = {Path(r["path"]).parent for r in rows}
    shared = parents.pop() if len(parents) == 1 else None
    print()
    print(style.heading_line("Firefox profiles",
                             f"{len(rows)} in {style.short_path(shared)}" if shared else f"{len(rows)} found"))
    name_w = max(len(r["name"]) for r in rows)
    kind_w = max(len(r.get("kind") or "unrecognised") for r in rows)
    # The profile Firefox opens first, then the rest by name: the order of
    # profiles.ini is an accident of history, not something to read.
    rows = sorted(rows, key=lambda r: (not r["default"], r["name"].lower()))
    for row in rows:
        mark = style.brand(style.BULLET) if row["default"] else " "
        kind = row.get("kind") or "unrecognised"
        print()
        folder = Path(row["path"]).name if shared else style.short_path(row["path"])
        empty = row["state"] == "empty" and not row["running"]
        name = row["name"].ljust(name_w)
        print(f"  {mark} {name if empty else style.strong(name)}  "
              f"{style.muted(kind.ljust(kind_w))}  {style.muted(folder)}"
              + (f"  {style.muted(style.DOT + ' no theme')}" if empty else ""))
        if empty:
            continue
        pairs = []
        if row["state"] == "managed":
            manifest = row["manifest"]
            source = manifest.get("source") or {}
            # ref_kind only means something for a repo: "unknown" against a
            # local directory would read as a gap rather than as N/A.
            tracking = (f", tracking the {source['ref_kind']}"
                        if source.get("kind") in fetch.REMOTE_KINDS
                        and source.get("ref_kind") in ("release", "branch")
                        else "")
            label = _theme_label(manifest)
            if source.get("kind") == "local":
                # Named the way install names it, with where it came from after.
                where = style.short_path(label)
                label = f"{style.strong(Path(label).name)}  {style.muted(where)}"
            else:
                label = style.strong(label)
            installed = str(manifest.get("installed", "?"))[:16]
            pairs.append(("theme", label))
            pairs.append(("installed", installed + (f"  {style.muted(tracking.lstrip(', '))}"
                                                     if tracking else "")))
            if manifest.get("sheets"):
                pairs.append(("sheets", ", ".join(manifest["sheets"])))
            note = _drift_note(row)
            first, _, rest = note.partition(", ")
            pairs.append(("files", first.split(" ")[0]
                          + (f"  {style.caution(rest)}" if rest else "")))
            adopted = manifest.get("adopted")
            if isinstance(adopted, dict):
                # Adopted rather than installed: say how sure the
                # identification was, since it is a match rather than a
                # record of something fxcss did itself.
                text = (f"identified by its files: "
                        f"{adopted.get('matched', 0)}/"
                        f"{adopted.get('files', 0)} matched")
                if adopted.get("differing"):
                    text += (f", {len(adopted['differing'])} already differed "
                             "from that version")
                pairs.append(("adopted", text))
            update = updates.get((source.get("owner"), source.get("name")))
            if update and update["state"] == "available":
                detail = f"  {style.muted(update['detail'])}" if update["detail"] else ""
                pairs.append(("update", style.good(f"{update['label']} available") + detail))
            elif update and update["state"] == "current":
                pairs.append(("update", "up to date"))
            elif update:
                pairs.append(("update", style.warn(update["label"])))
            if update and update.get("also"):
                pairs.append(("", style.muted(update["also"])))
            if manifest.get("user_files"):
                pairs.append(("yours", f"{style.plural(len(manifest['user_files']), 'file')} "
                                       "you added, kept active"))
        elif row["state"] == "unmanaged":
            pairs.append(("theme", [style.caution("installed by hand") + "  "
                                    + style.muted(f"chrome/ holds {style.plural(row['files'], 'file')}"),
                                    style.cmd("fxcss adopt") + style.muted(" takes it over; ")
                                    + style.cmd("fxcss install") + style.muted(" would replace it, "
                                                                               "backing it up first")]))
        else:
            pairs.append(("theme", style.muted("none")))
        if row["running"]:
            pairs.append(("note", style.warn("Firefox is using this profile right now")))
        for line in style.rows(pairs, indent=6, label_width=9):
            print(line)

    notes = []
    if default_note:
        notes.append((style.brand(style.BULLET), "opens by default: the profile Firefox opens on its own"))
    if any(not r.get("kind") for r in rows):
        notes.append((" ", "unrecognised: no Firefox channel suffix in the folder name"))
    checkable = any(((r["manifest"] or {}).get("source") or {}).get("kind")
                    in fetch.REMOTE_KINDS for r in rows)
    if not args.check and checkable:
        notes.append("Pass --check to ask GitHub whether anything newer exists.")
    if notes:
        print()
        for mark, note in notes:
            lines = style.wrap(note, indent=4)
            print(f"  {mark} {style.muted(lines[0].lstrip())}")
            for line in lines[1:]:
                print(style.muted(line))
    print()
    return 0



def _install_source(owner, name, ref, info, explicit, host="github.com", branch=None):
    """Record where a theme came from, precisely enough to fetch it again.

    The distinction that matters later is `ref_kind`: re-fetching a release
    means looking for a newer tag, while re-fetching a branch means looking at
    what that branch points at now. The string "main" cannot tell those apart,
    so it is settled here, where the answer is actually known, and written
    down. Nothing infers it afterwards. A branch records the full commit it
    pointed at, so an exact match later reads as up to date.
    """
    from . import fetch

    source = {"kind": fetch.HOSTS.get(host, "github"), "host": host,
              "owner": owner, "name": name, "ref": ref,
              "ref_kind": "explicit" if explicit else "unknown",
              "resolved": ref, "path": None}
    if not info:
        return source
    if explicit:
        source["resolved"] = fetch.snapshot_ref(ref, info)
        return source
    if info.get("renamed_from"):
        source["renamed_from"] = info["renamed_from"]
    release = (info.get("release") or {}).get("tag")
    tracked = info.get("tracked") or {}
    if ref == release:
        source["ref_kind"] = "release"
    elif ref == info.get("default_branch"):
        source["ref_kind"] = "branch"
        # A branch has no version, so the commit it pointed at is the only
        # thing a later run can compare against to see it has moved.
        source["resolved"] = fetch.snapshot_ref(ref, info)
    elif tracked.get("ref") == ref and not tracked.get("missing"):
        source["ref_kind"] = "branch"
        source["resolved"] = fetch.snapshot_ref(ref, info)
    return source

def profile_state(profile):
    """"managed" (fxcss installed a theme here), "unmanaged" or "empty"."""
    from . import install
    if install.read_manifest(profile["path"]):
        return "managed"
    chrome = Path(profile["path"]) / "chrome"
    if chrome.is_dir() and any(chrome.rglob("*.css")):
        return "unmanaged"
    return "empty"


def _choose_profile(explicit, interactive, prefer=None):
    """Which real profile to touch, asking only when that is a real question.

    Mirrors choose_firefox: an explicit --profile always wins, a clear default
    is used silently, and the menu appears only for a human at a terminal --
    CI must not block on stdin. Ambiguity without a terminal is an error
    rather than a guess, because this command edits a profile someone lives in.

    `prefer` narrows the field to profiles the command can actually act on --
    "managed" for uninstall, upgrade and rollback, "unmanaged" for adopt.
    Without it, a theme deliberately installed into a profile that is not the
    one Firefox opens could not be found again: every command would go to
    Firefox's default and report nothing installed there, which is true and
    useless. Firefox's own default still decides, but only among profiles that
    have what the command needs.
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
    if prefer:
        # None matching is left to the command to report: it knows what it
        # wanted and can say so far better than a generic message here.
        narrowed = [p for p in profiles if profile_state(p) == prefer]
        if len(narrowed) == 1:
            return narrowed[0]
        if narrowed:
            profiles = narrowed
    defaults = [p for p in profiles if p["default"]]
    if len(defaults) == 1:
        return defaults[0]
    if not interactive:
        raise SystemExit(
            "error: several Firefox profiles and no clear default; pass "
            "--profile <name-or-path> (--list-profiles shows them)")
    default = profiles.index(defaults[0]) if defaults else 0
    from . import style
    name_w = max(len(p["name"]) for p in profiles)
    kind_w = max(len(p.get("kind") or "unrecognised") for p in profiles)
    folder, places = _shared_parent([_short_path(p["path"]) for p in profiles])
    notes = [_profile_notes(profile) for profile in profiles]

    def row(i, current, pointer):
        kind = (profiles[i].get("kind") or "unrecognised").ljust(kind_w)
        line = _menu_row(i + 1, profiles[i]["name"].ljust(name_w), [kind, places[i]],
                         default=current, pointer=pointer, path=True)
        if notes[i]:
            line += "\n      " + style.brand(f"{style.ARROW} " + ", ".join(notes[i]))
        return line

    note = f"{len(profiles)} found" + (f" in {folder}" if folder else "")
    picked = profiles[_menu("Which profile?", note, len(profiles), row, default)]
    print(_skip_hint(picked["name"], f"--profile {shlex.quote(picked['name'])}"))
    return picked


def paint(enabled=None):
    """A colouriser for prompts, off unless a human is watching.

    NO_COLOR is honoured because these are interactive prompts, not report
    output with its own --no-colour flag; someone who has set it globally
    should not have to learn a per-command switch.
    """
    if enabled is None:
        enabled = sys.stdout.isatty()
    enabled = enabled and not os.environ.get("NO_COLOR")

    def colour(code, text):
        if not enabled:
            return text
        from .audit import RESET
        return f"{code}{text}{RESET}"
    return colour


def scan_selection(raw, count):
    """Menu input -> (zero-based indices, tokens that were not usable).

    Accepts "1,3", "1 3", "all", and empty for none. Anything out of range or
    unreadable is dropped rather than guessed at: this picks stylesheets that
    get written into someone's profile, so a typo should under-select, never
    over-select. Order is the list's, not the order typed, and repeats collapse.

    Returning the rejects alongside is the point. Under-selecting is the right
    instinct but it must not be silent: someone typed "20, 4" into the install
    menu, their terminal had leaked a Page Up escape into the line, and the
    first token arrived as "\\x1b[5~20". int() refused it, the token vanished,
    and they got sheet 4 alone with nothing said. The caller can now show what
    it threw away and ask again.
    """
    text = (raw or "").strip().lower()
    if not text:
        return [], []
    if text in ("all", "*", "a"):
        return list(range(count)), []
    picked, ignored = set(), []
    for token in text.replace(",", " ").split():
        try:
            index = int(token) - 1
        except ValueError:
            ignored.append((token, "not a number"))
            continue
        if 0 <= index < count:
            picked.add(index)
        else:
            ignored.append((token, f"outside 1-{count}"))
    return sorted(picked), ignored


def parse_selection(raw, count):
    """Just the indices, for callers that have nothing to say about rejects."""
    return scan_selection(raw, count)[0]


def _sheet_conflicts(chosen, indent="  "):
    """Report optional sheets that cannot all take effect. Returns the pairs.

    Printed the same way wherever sheets are picked, so `try`, `install` and
    `tweaks` describe the same situation in the same words -- only what they
    do about it differs. An empty list means nothing measurable was found,
    which is not the same as proof that a combination works: see fxcss.sheets.
    """
    from . import sheets as sheets_mod, style

    found = sheets_mod.conflicts(chosen)
    for report in found:
        print()
        for line in style.wrap(sheets_mod.describe(report), indent=len(indent)):
            print(line)
        for example in report["examples"][:2]:
            print(f"{indent}  {example['selector']} {{ "
                  f"{example['property']}: {example['a']} }}  vs  "
                  f"{{ …: {example['b']} }}")
    return found


def _sheet_groups(variants):
    """The stylesheet menu's order and groups: (sheets, [(title, count)] or None).

    Tweaks first, then colour schemes, when a theme ships both; any other
    theme gets the single list it always had, in the order it was found.

    The colour schemes' title says "pick one" when every pair of them clashes
    -- measured, as install measures before refusing a pair, not assumed from
    the names -- so nobody has to pick two to find out.
    """
    from . import inventory, sheets, style
    schemes = [v for v in variants if inventory.is_colour_scheme(v.stem)]
    tweaks = [v for v in variants if not inventory.is_colour_scheme(v.stem)]
    if not schemes or not tweaks:
        return list(variants), None
    title = "Colour schemes"
    pairs = len(schemes) * (len(schemes) - 1) // 2
    if pairs and len(sheets.conflicts(schemes)) == pairs:
        title += f" {style.DOT} pick one"
    return tweaks + schemes, [("Tweaks", len(tweaks)), (title, len(schemes))]


def _keep_one(chosen):
    """Ask which of each set of clashing sheets to keep; returns the rest.

    A set is the worst-clashing pair plus any other chosen sheet that clashes
    with all of them, so three colour schemes are one question, not three.
    Each sheet is shown with the colours it would set (a swatch and its hex
    code), and Enter keeps the one that loads last -- the one that would have
    won anyway. Asked again until nothing chosen clashes.
    """
    from . import inventory, sheets as sheets_mod, style
    chosen = list(chosen)
    while True:
        reports = sheets_mod.conflicts(chosen)
        if not reports:
            return chosen
        pairs = {frozenset((r["a"], r["b"])) for r in reports}
        first = reports[0]
        group = [first["a"], first["b"]]
        for sheet in chosen:
            if sheet.stem not in group and all(frozenset((sheet.stem, g)) in pairs
                                               for g in group):
                group.append(sheet.stem)
        members = [sheet for sheet in chosen if sheet.stem in group]
        if len(members) < 2:            # two sheets share a stem: nothing to ask
            return chosen

        values = [v for decls in sheets_mod.contested(members).values() for v in decls.values()]
        what = "colour" if sum(map(sheets_mod.is_colour, values)) * 2 > len(values) else "property"
        kind = sheets_mod.verdict(first)
        if kind == "subsumed":
            loser = sheets_mod.overridden(first)
            other = first["b"] if loser == first["a"] else first["a"]
            note = f"{loser} changes nothing alongside {other}"
        elif kind == "alternatives":
            note = (f"{'both' if len(members) == 2 else 'all'} set the same "
                    f"{style.plural(first['shared'], what)}; only one can take effect")
        else:
            note = (f"they set {style.plural(first['conflicting'], what)} differently; "
                    "the later one wins those")
        noun = ("colour scheme" if all(inventory.is_colour_scheme(m.stem) for m in members)
                else "stylesheet")

        colours = sheets_mod.palettes(members)
        name_width = max(len(m.stem) for m in members)
        hex_width = max((len(c) for cs in colours.values() for c in cs), default=0)
        # Each colour is "  ██ #1e1e2e" (or just the hex where a terminal
        # cannot show the block), as many as fit beside the longest name.
        cell = 2 + (len(style.BLOCK) + 1 if style.swatch("#000") else 0) + hex_width
        room = style.term_width() - 1 - len("  00. ") - name_width - len("  (Enter)")
        fits = min(4, max(0, room // cell)) if hex_width else 0
        shown = max(min(fits, len(c)) for c in colours.values())
        width = len("  00. ") + name_width + shown * cell

        def row(i, current, pointer):
            sheet = members[i]
            lead = f"{style.brand(style.POINTER) if pointer else ' '} {i + 1:>2}. "
            title = style.brand(sheet.stem) if pointer else style.strong(sheet.stem)
            line = lead + title + " " * (name_width - len(sheet.stem))
            for colour in colours[sheet][:fits]:
                mark = style.swatch(colour)
                line += "  " + (mark + " " if mark else "") + style.muted(colour.ljust(hex_width))
            if not current:
                return line.rstrip()
            line += " " * (width - len(style._visible(line)))
            return f"{line}  {style.muted('(Enter)')}"

        pick = _menu(f"Keep which {noun}?", note, len(members), row, len(members) - 1)
        dropped = {m for i, m in enumerate(members) if i != pick}
        chosen = [sheet for sheet in chosen if sheet not in dropped]


def _choose_sheets(variants, interactive, force=False):
    """Offer a theme's optional stylesheets, when there is a human to ask.

    Only reachable when --with was not given: an explicit flag is an answer
    and asking again would be rude. Silence (no terminal, or CI) means none,
    which is what installing without --with has always done.

    The menu may group the sheets (see `_sheet_groups`) and numbers them in
    the order shown; what is returned keeps the theme's own order, which is
    the order they are imported in. Sheets that cancel each other out are
    settled by asking which to keep, unless `force` (install's --force) says
    to install them anyway.
    """
    if not interactive or not variants:
        return []
    from . import prompt, style
    shown, groups = _sheet_groups(variants)
    names = [sheet.stem for sheet in shown]
    grid = style.column_groups(names, groups, indent=4)
    # The arrow keys and Space tick sheets at a terminal that can redraw the
    # grid; typing the numbers works there too, and is all there is elsewhere.
    arrows = prompt.arrows_ok(len(grid) + 2)
    print()
    print(style.heading_line("Optional stylesheets", f"{len(variants)} {style.DOT} "
                             + ("pick any" if arrows else "pick any by number")))
    if not arrows:
        for line in grid:
            print(line)
        print()
    # Rejected tokens get the question asked again. Dropping them quietly is
    # how a stray escape sequence in the line turned "20, 4" into sheet 4
    # alone.
    chosen = []
    for attempt in range(3):
        # A retry is typed: the question it asks ("retype the whole list") is
        # about the text, and the grid has scrolled up behind the warnings.
        if arrows and attempt == 0:
            raw = prompt.pick_many(names, "Include which?", "e.g. 1,3 or all",
                                   lambda text: scan_selection(text, len(names))[0],
                                   groups=groups)
        else:
            try:
                raw = input(style.ask("Include which?", "e.g. 1,3 or all", "none"))
            except EOFError:
                raw = ""
        # Empty means "none" the first time and "keep what you read" on a
        # retry, so accepting a partial answer never costs the whole list.
        if attempt and not raw.strip():
            break
        indices, ignored = scan_selection(raw, len(variants))
        picked = {shown[i] for i in indices}
        chosen = [sheet for sheet in variants if sheet in picked]
        for token, why in ignored:
            # repr() so an escape sequence is shown rather than replayed at
            # the terminal -- seeing the \x1b is the whole diagnosis.
            print(style.warn(f"    {style.BANG} ignored {token!r}: {why}"))
        if attempt == 2 or not ignored:
            break
        print(f"    That is {len(chosen)} of the "
              f"{len(indices) + len(ignored)} you asked for. Retype the "
              "whole list, or Enter to keep it.")
    # `all` is offered above and is right for most themes, but wrong for one
    # with a family of alternatives in it. Only one of such a family can take
    # effect, so which one is the question, rather than retyping the list.
    # --force has already answered it: install them all, but say what clashes.
    clashing = force and [r for r in _sheet_conflicts(chosen, indent="    ")
                          if r["conflicting"]]
    if not force:
        chosen = _keep_one(chosen)
    if chosen:
        stems = [s.stem for s in chosen]
        option = f"--with {shlex.quote(','.join(stems))}" + (" --force" if clashing else "")
        print(_skip_hint(", ".join(stems), option, verb="including"))
    return chosen


def _choose_ref(info, prefer, interactive):
    """Which ref to install: the blessed release, or what the branch has now.

    Only asks when the answer is genuinely open -- both exist and the branch
    has moved on since the release. A release with nothing newer behind it is
    not a question, and neither is an explicit --commit or --ref.
    """
    from . import fetch, style
    options = fetch.ref_options(info)
    newer = style.muted("  The default branch has newer commits than this release; "
                        "--commit installs those instead.")
    if prefer == "commit" or len(options) < 2 or not fetch.commit_is_newer(info):
        ref, why = fetch.choose_ref(info, prefer)
        if not interactive and len(options) > 1 and fetch.commit_is_newer(info):
            print(newer)
        return ref, why
    if not interactive:
        ref, why = fetch.choose_ref(info, prefer)
        print(newer)
        return ref, why

    label_w = max(len(option["label"]) for option in options)
    date_w = max(len(option["date"]) for option in options)
    def row(i, current, pointer):
        option = options[i]
        return _menu_row(i + 1, option["label"].ljust(label_w),
                         [option["date"].ljust(date_w), option["note"]],
                         default=current, pointer=pointer)

    picked = options[_menu("Which version?", "the default branch has moved on since the "
                           "latest release", len(options), row, 0)]
    branch = picked["ref"] == info.get("default_branch")
    print(_skip_hint(picked["label"], "--commit" if branch else f"--ref {shlex.quote(picked['ref'])}"))
    return picked["ref"], picked["why"]


def _confirm(question, interactive, assume_yes):
    """Enter means yes; only an explicit n declines. Never asks in CI."""
    if assume_yes or not interactive:
        return True
    from . import style
    lead = len(question) - len(question.lstrip())
    try:
        raw = input(style.ask(question.strip(), default="Y/n", indent=lead or 2))
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
    workdir = Path(tempfile.mkdtemp(prefix="fxcss-install-"))
    try:
        got = _acquire(args, interactive, workdir, verb="installing from")
        if got is None:
            return 2
        planned = _plan_source(got["repo_root"], args, interactive, workdir,
                               remote=got["remote"], quiet=True)
        if planned is None:
            return 2
        plan, theme_root = planned
        facts = fetch.describe(plan["repo_root"], theme_root, plan)
        found = _inventory_report(theme_root, facts, strict=True, force=args.force,
                                  quiet=True)
        if found is None:
            return 2
        from . import style, checklist
        # The profile is part of what is being described, so it is settled
        # before the questions about what to put in it.
        picked = _choose_profile(args.profile, interactive)
        print()
        for line in _source_rows(plan, facts, found, got["remote"]):
            print(line)
        for line in style.rows([("profile", [style.strong(picked["name"]),
                                             style.muted(_short_path(picked["path"]))])],
                               indent=2, label_width=10):
            print(line)

        chosen = []
        if args.with_sheets:
            chosen = _match_sheets(args.with_sheets, facts)
            if chosen is None:
                return 2
            blocking = [r for r in _sheet_conflicts(chosen) if r["conflicting"]]
        else:
            chosen = _choose_sheets(facts["variants"], interactive, force=args.force)
            # The menu asks which of a clashing set to keep, so it should
            # never hand one back without --force. Validate its answer like
            # an explicit --with list all the same; any clash it found it has
            # already shown, so this measures without a reprint.
            from . import sheets as sheets_mod
            blocking = [r for r in sheets_mod.conflicts(chosen) if r["conflicting"]]
        if blocking and not args.force:
            print("\n  Refusing to install stylesheets that cancel each "
                  "other out — only one\n  of them would have any effect, "
                  "and which one is decided by import\n  order rather "
                  "than by you. Pick one, or pass --force.",
                  file=sys.stderr)
            return 2

        pref_files = _choose_prefs(args.prefs, plan["repo_root"], theme_root)
        if pref_files is None:
            return 2

        paint_c = paint()
        from .audit import BOLD as _BOLD
        theme_name = (got["source"].get("name") or Path(got["repo_root"]).name)
        items = checklist.build(plan["repo_root"], theme_root, plan,
                                applied_pref_files=pref_files)
        pairs = []
        if install.firefox_running(picked["path"]):
            pairs.append(("note", style.warn("Firefox is running; the theme shows "
                                             "after a restart")))
        prefs = install.theme_pref_names(theme_root)
        # The checklist already lists a shipped user.js it will apply, names
        # and all; saying it twice was the noisiest line on this screen.
        if prefs and not any("user.js" in i["item"] and i["state"] == "applied"
                             for i in items):
            pairs.append(("user.js", f"the theme also sets {_pl(len(prefs), 'pref')}: "
                                     f"{', '.join(prefs)}"))
        if pairs:
            print()
            for line in style.rows(pairs, indent=2, label_width=10):
                print(line)
        if items:
            print()
            for line in checklist.render(items):
                print(line)
        previous = install.read_manifest(picked["path"])
        if previous:
            _report_additions(install.user_additions(picked["path"], previous))
        print()
        # Its prefs are on the automatic row just above; this line is only
        # what happens to the profile, kept to one line.
        writes = f"Copies the theme into {picked['name']}"
        if (Path(picked["path"]) / "chrome").exists():
            writes += ", backing up chrome/ first"
        for line in style.wrap(f"{writes}; fxcss uninstall undoes it.", indent=2):
            print(style.muted(line).replace("fxcss uninstall",
                                            style.cmd("fxcss uninstall") + ("\033[2m" if style.colour_on() else "")))
        if not _confirm(f"  Install {theme_name} into {picked['name']}?",
                        interactive, args.yes):
            print(style.muted("  Nothing installed."))
            return 1

        extra = {"layout": _layout_record(plan), "pref_files": pref_files,
                 "sheet_paths": _sheet_paths(chosen, facts),
                 "sheet_ids": _sheet_ids(chosen, facts)}
        try:
            result = install.install_theme(
                theme_root, picked["path"], got["theme_id"], sheets=chosen,
                source=got["source"], on_collision="theme" if args.force else "stop",
                extra=extra, repo_root=plan["repo_root"])
        except install.CollisionError as exc:
            _report_collision(exc)
            return 2
        if result["backup"]:
            print(f"  {style.muted('backup')}  existing chrome/ saved as {result['backup']}")
        _report_preserved(result)
        if result["sheets"]:
            print(f"  {style.muted('sheets')}  {style.strong(', '.join(result['sheets']))}")
        for note in result.get("sheet_notes") or []:
            print(f"  note: {note}")
        print(f"\n  {style.good(style.TICK)} {style.strong('Installed')} "
              f"{_pl(len(result['files']), 'file')} into "
              f"{_short_path(picked['path'] / 'chrome')}")
        if not checklist.complete(items):
            print(style.warn(f"  {style.BANG} the files are in place; the required "
                             "items above are not something fxcss can do for you"))
        print(f"  {style.muted('Restart Firefox to see it;')} {style.cmd('fxcss uninstall')} "
              f"{style.muted('puts everything back.')}")
        return 0
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

def cmd_uninstall(args):
    from . import install

    if args.list_profiles:
        return _print_profiles()
    interactive = (sys.stdin.isatty() and sys.stdout.isatty()
                   and not os.environ.get("CI"))
    picked = _choose_profile(args.profile, interactive, prefer="managed")
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
    if result.get("noop"):
        print("  no fxcss-installed theme is active here, so nothing was "
              "removed or restored.")
        if result.get("user_js_cleaned"):
            print("  user.js: removed a leftover fxcss block")
        if result.get("backups"):
            print(f"  backups kept: {', '.join(result['backups'])}  "
                  "(`fxcss rollback --to <name>` restores one)")
        return 0
    if result["removed"]:
        print(f"  removed {_pl(result['removed'], 'file')}")
    if result["restored"]:
        print(f"  restored your previous chrome/ from {result['restored']}")
    if result["moved_aside"]:
        print(f"  moved the current chrome/ to {result['moved_aside']} "
              "(fxcss could not prove it wrote it, so nothing is deleted)")
    if result["kept"]:
        print(f"  left {len(result['kept'])} added, modified or unverifiable "
              "file(s) in chrome/; any backup stays put alongside them")
    print("\n  Restart Firefox to see the change.")
    return 0


def _report_additions(additions, indent="  "):
    """Say which of the person's own files the upgrade will keep active."""
    files, skipped = additions["files"], additions["skipped"]
    if files:
        print(f"{indent}{_pl(len(files), 'file')} you added will stay active:")
        for name in files[:6]:
            print(f"{indent}  {name}")
        if len(files) > 6:
            print(f"{indent}  … and {len(files) - 6} more")
    if skipped:
        print(f"{indent}{_pl(len(skipped), 'added item')} {_agree(len(skipped), 'are not regular files', 'is not a regular file')} "
              "(links, git metadata) and stay only in the backup")


def _report_preserved(result, indent="  "):
    """After an install: what of the person's own is active, what is not."""
    if result.get("user_files"):
        print(f"{indent}kept {_pl(len(result['user_files']), 'file')} you added "
              "active in the new chrome/")
    if result.get("displaced"):
        print(f"{indent}{_pl(len(result['displaced']), 'file')} you added {_agree(len(result['displaced']), 'were', 'was')} "
              f"replaced by the theme's own (your copies are in "
              f"{result['backup']}):")
        for name in result["displaced"][:6]:
            print(f"{indent}  {name}")


def _report_collision(exc, indent="  "):
    print(f"\n{indent}The new version ships files at paths you added "
          f"yourself, with different\n{indent}contents. Nothing was changed. "
          "Move or rename yours, or pass --force to\n"
          f"{indent}install the theme's copies (yours stay in the backup):",
          file=sys.stderr)
    for name in exc.collisions[:12]:
        print(f"{indent}  {name}", file=sys.stderr)
    if len(exc.collisions) > 12:
        print(f"{indent}  … and {len(exc.collisions) - 12} more", file=sys.stderr)


def _report_drift(moved, indent="  "):
    """Say what has changed under chrome/ since the install, if anything."""
    if moved["modified"]:
        print(f"{indent}{_pl(len(moved['modified']), 'file')} edited since install:")
        for name in moved["modified"][:6]:
            print(f"{indent}  {name}")
        if len(moved["modified"]) > 6:
            print(f"{indent}  … and {len(moved['modified']) - 6} more")
    if moved["missing"]:
        print(f"{indent}{_pl(len(moved['missing']), 'file')} deleted since install")
    if moved["extra"]:
        print(f"{indent}{_pl(len(moved['extra']), 'file')} added by hand "
              "(kept active across upgrades)")



def cmd_upgrade(args):
    import shutil
    import tempfile
    from . import fetch, install

    interactive = (sys.stdin.isatty() and sys.stdout.isatty()
                   and not os.environ.get("CI"))
    picked = _choose_profile(args.profile, interactive, prefer="managed")
    manifest = install.read_manifest(picked["path"])
    print(f"\n  profile: {picked['name']}  ({picked['path']})")
    if not manifest:
        print("\n  Nothing fxcss installed in this profile, so there is "
              "nothing to\n  upgrade. `fxcss install <theme>` puts one here; "
              "`fxcss profiles`\n  shows what is where.", file=sys.stderr)
        return 2

    source = manifest.get("source") or {}
    print(f"  installed: {_theme_label(manifest)}")
    recorded_layout = manifest.get("layout") if isinstance(manifest.get("layout"), dict) else {}
    if args.subpath is None and recorded_layout.get("subpath"):
        args.subpath = recorded_layout["subpath"]
    if args.entry is None and recorded_layout.get("entry"):
        args.entry = recorded_layout["entry"]

    # What is there to move to?
    info = None
    local_root = None
    if source.get("kind") == "local":
        local_root = Path(source.get("path") or "")
        if not local_root.is_dir():
            print(f"\n  error: the directory this was installed from is gone "
                  f"({local_root})", file=sys.stderr)
            return 2
        target_ref, why = None, f"the current contents of {local_root}"
        state = {"state": "unsupported", "also": ""}
    elif source.get("kind") in fetch.REMOTE_KINDS:
        host = source.get("host") or "github.com"
        branch = source.get("ref") if source.get("ref_kind") == "branch" else None
        try:
            info = fetch.resolve(source["owner"], source["name"], host=host, branch=branch)
        except RuntimeError as exc:
            print(f"\n  error: {exc}", file=sys.stderr)
            return 2
        if info.get("renamed_from"):
            print(f"  upstream moved: {info['renamed_from']} is now "
                  f"{info['owner']}/{info['name']}; recorded under the new name")
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
        if state.get("also"):
            print(f"  also:      {state['also']}")
    else:
        print("\n  error: this install has no record of where it came from, "
              "so fxcss\n  cannot fetch a newer copy. Reinstall it with "
              "`fxcss install <theme>`.", file=sys.stderr)
        return 2

    # Has the user edited what is installed? For an adopted theme this also
    # covers files that already differed from the release when fxcss found
    # it -- an upgrade would overwrite those just the same.
    moved = install.drift(picked["path"], manifest)
    moved["modified"] = install.local_changes(picked["path"], manifest)
    if moved["modified"] or moved["missing"]:
        print()
        _report_drift(moved, indent="  ")
    elif not moved["checked"] and manifest.get("files"):
        print("\n  note: this was installed before fxcss recorded file "
              "hashes, so\n  whether it has been edited since cannot be "
              "checked.")

    remote = source.get("kind") in fetch.REMOTE_KINDS
    setup_requested = args.prefs is not None or args.with_sheets is not None
    nothing_to_do = (state["state"] == "current" and not args.ref
                     and not args.commit and remote and not setup_requested)
    if args.check and remote and not setup_requested:
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

    workdir = Path(tempfile.mkdtemp(prefix="fxcss-upgrade-"))
    keep_pin = (source.get("ref_kind") == "explicit" and not args.ref and not args.commit
                and bool(re.fullmatch(r"[0-9a-fA-F]{40}", str(source.get("resolved") or ""))))
    try:
        if local_root is not None:
            repo_root = local_root
        else:
            print(f"\n  fetching {why} …")
            revision = source["resolved"] if keep_pin else fetch.snapshot_ref(target_ref, info)
            repo_root = fetch.download(info["owner"], info["name"], revision,
                                       workdir / "src", host=info["host"])

        planned = _plan_source(repo_root, args, interactive, workdir)
        if planned is None:
            print("  (the recorded layout no longer matches this version; choose "
                  "with --subpath)", file=sys.stderr)
            return 2
        plan, theme_root = planned
        facts = fetch.describe(plan["repo_root"], theme_root, plan)
        if _inventory_report(theme_root, facts, strict=True, force=args.force) is None:
            return 2

        if args.with_sheets is not None:
            selected = _match_sheets(args.with_sheets, facts)
            if selected is None:
                return 2
            continuity = {"kept": selected, "missing": []}
        else:
            recorded = manifest.get("sheet_paths") if isinstance(manifest.get("sheet_paths"), dict) else {}
            wanted = manifest.get("sheet_ids")
            if not isinstance(wanted, list) or not all(isinstance(p, str) for p in wanted):
                wanted = [recorded.get(stem, stem) for stem in (manifest.get("sheets") or [])]
            continuity = fetch.sheet_continuity(wanted, facts["variants"], facts.get("options"))
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

        if args.prefs is not None:
            pref_files = _choose_prefs(args.prefs, plan["repo_root"], theme_root)
            if pref_files is None:
                return 2
        else:
            pref_files = [str(p) for p in (manifest.get("pref_files") or []) if isinstance(p, str)]
            from . import checklist
            shipped = {entry["path"] for entry in checklist.pref_files(plan["repo_root"], theme_root)}
            gone = [p for p in pref_files if p not in shipped]
            if gone:
                print(f"\n  error: the new version has no preference file at "
                      f"{', '.join(gone)}.\n  Its prefs would silently stop being "
                      "applied. Re-run with --prefs to choose\n  from: "
                      f"{', '.join(sorted(shipped)) or 'none'}, or --prefs '' for none.",
                      file=sys.stderr)
                return 2
            if pref_files:
                print(f"  keeping preference file(s): {', '.join(pref_files)}")

        if local_root is not None or setup_requested:
            # A local source has no upstream to ask; the files themselves are
            # the answer. Compare what an install would write with what the
            # last one wrote, and say "up to date" only when they agree.
            preview = install.preview_digests(theme_root, continuity["kept"],
                                               repo_root=plan["repo_root"])
            recorded = {k: v for k, v in (manifest.get("digests") or {}).items()
                        if k in set(manifest.get("files") or [])}
            changed = sorted(k for k in set(preview) | set(recorded)
                             if preview.get(k) != recorded.get(k))
            block = install.user_js_body(theme_root, pref_files,
                                         repo_root=plan["repo_root"])
            if block != manifest.get("user_js_block"):
                changed.append("user.js (fxcss preference block)")
            if pref_files != (manifest.get("pref_files") or []) or (
                    isinstance(manifest.get("sheet_ids"), list) and
                    _sheet_ids(continuity["kept"], facts) != manifest["sheet_ids"]):
                changed.append("setup selection")
            same_source = local_root is not None or state["state"] in ("current", "pinned")
            if not changed and not args.ref and same_source:
                if args.check:
                    print("\n  Up to date: the source and setup match what is installed.")
                    return 0
                print("\n  Already up to date: the source and setup match what is "
                      "installed.")
                return 0
            print(f"\n  {_pl(len(changed), 'file')} {_agree(len(changed), 'differ', 'differs')} from what is installed"
                  + (": " + ", ".join(changed[:5]) if changed else ""))
            if args.check:
                print("  Run `fxcss upgrade` to take it.")
                return 1

        if (moved["modified"] or moved["missing"]) and not args.force:
            print("\n  Refusing to upgrade over edits fxcss did not make — the "
                  "new version\n  would overwrite them. Save what you want, then "
                  "pass --force.\n  (Files you *added* are carried over; only "
                  "the ones above.)", file=sys.stderr)
            return 2

        if args.audit:
            from . import audit as audit_mod
            firefox = choose_firefox(args.firefox)
            print(f"\n  auditing the new version against {firefox} …")
            with core.Session(theme_root, firefox) as session:
                result = audit_mod.audit(session, theme_root)
            audit_mod.report(result, colour=not args.no_colour)
            actionable = audit_mod.actionable(result["findings"])
            if actionable and not args.force:
                print(f"  {_pl(len(actionable), 'selector')} in the new version "
                      f"{_agree(len(actionable), 'match', 'matches')} nothing in this Firefox.\n  Pass --force to "
                      "upgrade anyway.", file=sys.stderr)
                return 1

        if args.compare:
            # Seeing beats being told: render what is installed and what the
            # upgrade would install, and diff them, before the question is
            # asked. The profile itself is the theme root for the installed
            # side -- its chrome/ IS the theme as the user actually has it,
            # local edits and carried-over sheets included, which no re-fetch
            # of the recorded ref could reproduce.
            try:
                from . import compare as compare_mod
            except ImportError as exc:
                return _needs_pillow("upgrade --compare", exc)
            firefox = choose_firefox(args.firefox)
            outdir = Path(tempfile.mkdtemp(prefix="fxcss-upgrade-compare-"))
            print("\n  rendering what is installed now …")
            with core.Session(Path(picked["path"]), firefox) as session:
                core.capture_views(session, outdir / "installed")
            print(f"\n  rendering {why} …")
            with core.Session(theme_root, firefox) as session:
                core.capture_views(session, outdir / "new")
            print()
            status = compare_mod.run(outdir / "installed", outdir / "new",
                                     outdir / "diff", "upgrade")
            if status:
                print("error: upgrade preview failed; nothing was installed", file=sys.stderr)
                return status
            print(f"  before/after images: {outdir / 'diff'}")

        print()
        _report_checklist(plan["repo_root"], theme_root, plan, pref_files)
        if install.firefox_running(picked["path"]):
            print("\n  note: Firefox appears to be running — the change will "
                  "only show after a restart.")
        _report_additions(install.user_additions(picked["path"], manifest))
        if not _confirm(f"\n  Upgrade to {why}?", interactive, args.yes):
            print("  nothing changed.")
            return 1

        if local_root is not None:
            theme_id, new_source = str(repo_root), dict(source)
        else:
            theme_id = (f"{info['owner']}/{info['name']}@{target_ref}" if info["host"] == "github.com"
                        else f"{info['host']}/{info['owner']}/{info['name']}@{target_ref}")
            new_source = _install_source(info["owner"], info["name"], target_ref, info,
                                         explicit=bool(args.ref) or (source.get("ref_kind") == "explicit"
                                                                    and not args.commit), host=info["host"],
                                         branch=source.get("ref") if source.get("ref_kind") == "branch" else None)
            if keep_pin:
                new_source["resolved"] = source["resolved"]
        extra = {"layout": _layout_record(plan), "pref_files": pref_files,
                 "sheet_paths": _sheet_paths(continuity["kept"], facts),
                 "sheet_ids": _sheet_ids(continuity["kept"], facts)}
        try:
            result = install.install_theme(
                theme_root, picked["path"], theme_id,
                sheets=continuity["kept"], source=new_source,
                # The chrome/ from before fxcss ever ran here, carried forward so
                # `uninstall` still reaches it however many upgrades happen.
                origin_backup=manifest.get("origin_backup"),
                on_collision="theme" if args.force else "stop",
                extra=extra, repo_root=plan["repo_root"])
        except install.CollisionError as exc:
            _report_collision(exc)
            return 2
        print(f"\n  upgraded to {why}")
        print(f"  the previous version is kept as {result['backup']}")
        _report_preserved(result)
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
            print(f"  pruned {_pl(len(removed), 'older backup')}, keeping the "
                  f"newest {keep}")
        print("\n  Restart Firefox to see it. `fxcss rollback` puts the "
              "previous version back.")
        return 0
    except RuntimeError as exc:
        print(f"\n  error: {exc}", file=sys.stderr)
        return 2
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

def cmd_adopt(args):
    from . import adopt, fetch, install

    if args.list_profiles:
        return _print_profiles()
    interactive = (sys.stdin.isatty() and sys.stdout.isatty()
                   and not os.environ.get("CI"))
    picked = _choose_profile(args.profile, interactive, prefer="unmanaged")
    chrome = Path(picked["path"]) / "chrome"
    print(f"\n  profile: {picked['name']}  ({picked['path']})")

    if not (chrome / "userChrome.css").is_file():
        print("\n  No chrome/userChrome.css here, so there is no theme to "
              "adopt.\n  `fxcss install <theme>` puts one in.",
              file=sys.stderr)
        return 2
    if install.read_manifest(picked["path"]):
        print("\n  fxcss already manages this profile — `fxcss upgrade` "
              "updates it,\n  `fxcss profiles` shows what is there.",
              file=sys.stderr)
        return 2

    # Which repository is this?
    if args.repo:
        try:
            owner, name = fetch.parse_repo(args.repo)
        except ValueError as exc:
            print(f"\n  error: {exc}", file=sys.stderr)
            return 2
    else:
        found = adopt.candidates(chrome)
        if not found:
            print("\n  Nothing in this chrome/ says where it came from — no "
                  "git remote, and\n  no GitHub URL in its files. Plenty of "
                  "themes leave no trace once\n  installed, so this is "
                  "normal rather than a problem.\n\n  Name the repository "
                  "and fxcss will check the files against it:\n"
                  "    fxcss adopt owner/theme", file=sys.stderr)
            return 2
        owner, name = fetch.parse_repo(found[0]["repo"])
        print(f"  looks like {found[0]['repo']} — {found[0]['why']}")
        if not found[0]["certain"] and not _confirm(
                "  Check the files against it?", interactive, args.yes):
            print("  nothing changed. Pass the repository instead: "
                  "fxcss adopt owner/theme")
            return 1

    # Which version?
    try:
        if args.ref:
            refs = [{"ref": args.ref, "kind": "explicit"}]
        else:
            refs = fetch.known_refs(owner, name, limit=args.depth)
    except RuntimeError as exc:
        print(f"\n  error: {exc}", file=sys.stderr)
        return 2
    if not refs:
        print(f"\n  error: {owner}/{name} has no releases, tags or branches "
              "to compare against", file=sys.stderr)
        return 2

    print(f"\n  comparing {_pl(sum(1 for _ in chrome.rglob('*')), 'file')} against "
          f"{owner}/{name} …")
    best, checked = None, 0
    for entry in refs:
        try:
            revision = entry["ref"]
            if entry["kind"] == "branch":
                revision = fetch.require_commit(entry.get("sha") or
                    fetch.branch_head(owner, name, entry["ref"]), entry["ref"])
                entry["sha"] = revision
            listing = fetch.tree(owner, name, revision)
        except RuntimeError as exc:
            print(f"  {exc}", file=sys.stderr)
            break
        if not listing:
            continue
        result = adopt.compare(chrome, listing)
        result["ref"], result["kind"] = entry["ref"], entry["kind"]
        result["sha"] = entry.get("sha")
        checked += 1
        print(f"    {adopt.describe(result)}")
        if adopt.better(result, best):
            best = result
        if adopt.exact(result):
            break

    if best is None or not best["files"]:
        print(f"\n  Could not read a chrome/ folder out of {owner}/{name} at "
              "any of the\n  versions checked, so there is nothing to compare "
              "with. Is that the\n  right repository?", file=sys.stderr)
        return 2
    if not best["matched"]:
        print(f"\n  None of {_pl(checked, 'version')} of {owner}/{name} share a "
              "single file with\n  this profile. That is a different theme.",
              file=sys.stderr)
        return 2

    print(f"\n  best match: {adopt.describe(best)}")
    if not args.repo and best["score"] < adopt.GUESS_MIN_SCORE:
        # The repository was only *mentioned* in the theme's files -- a
        # credit, a palette, an upstream -- and the files mostly disagree
        # with it. Recording that would hand every later upgrade to the
        # wrong project.
        print(f"\n  Only {best['matched']}/{best['files']} of that version's "
              "files match, and the repository was\n  guessed from a mention "
              "in the theme's own text, not from anything definite.\n  Not "
              "recording it. If it really is this theme, say so:\n"
              f"    fxcss adopt {owner}/{name}", file=sys.stderr)
        return 2
    if not adopt.exact(best):
        print("  Recorded as that version plus local differences, so an "
              "upgrade knows\n  not to overwrite them without being told.")
    if install.firefox_running(picked["path"]):
        print("  note: Firefox appears to be running — nothing here needs a "
              "restart.")
    if not _confirm(f"\n  Record this profile as {owner}/{name}@{best['ref']}?",
                    interactive, args.yes):
        print("  nothing changed.")
        return 1

    kind = {"release": "release", "tag": "release",
            "branch": "branch"}.get(best["kind"], "explicit")
    resolved = best["ref"]
    if kind == "branch":
        # The commit the branch points at, never its name: an exact match
        # recorded as "main" announced an upgrade on the very next check.
        resolved = fetch.require_commit(best.get("sha"), best["ref"])
    source = {"kind": "github", "host": "github.com", "owner": owner, "name": name,
              "ref": best["ref"], "ref_kind": kind, "resolved": resolved,
              "path": None}
    try:
        manifest = install.adopt_theme(
            picked["path"], f"{owner}/{name}@{best['ref']}", source,
            {key: best[key] for key in ("ref", "matched", "files", "score",
                                        "differing", "missing", "extra",
                                        "eol")})
    except RuntimeError as exc:
        print(f"  error: {exc}", file=sys.stderr)
        return 2
    print(f"\n  recorded {_pl(len(manifest['files']), 'file')} as "
          f"{manifest['theme']}")
    print(f"  the theme as it is now is kept as {manifest['backup']}")
    print("\n  `fxcss upgrade` can now update it, and `fxcss uninstall` puts "
          "this back.")
    return 0


def cmd_rollback(args):
    from . import install

    interactive = (sys.stdin.isatty() and sys.stdout.isatty()
                   and not os.environ.get("CI"))
    picked = _choose_profile(args.profile, interactive, prefer="managed")
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
    try:
        available = core.find_variant_sheets(theme)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
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
    print(f"\n  wrote {out / 'TWEAKS.md'} with {_pl(len(entries), 'option')}")
    if stale:
        print(f"  note: {', '.join(stale)} changed nothing — possibly stale, "
              f"worth a look")
    print("  Commit the folder and link TWEAKS.md from your README.")
    return 0



def cmd_init(args):
    from . import layout, scaffold
    theme = args.theme.resolve()
    plan = layout.discover(theme, subpath=args.subpath)
    if plan["status"] == "ambiguous":
        print(f"error: {plan['reason']}", file=sys.stderr)
        return 2
    if plan["status"] != "ok":
        print(f"error: no chrome/userChrome.css under {theme} - run this from "
              f"your theme's root, or pass --theme. {plan['reason']}", file=sys.stderr)
        return 2
    if plan["kind"] in ("named", "fragment"):
        print(f"error: this layout ({plan['kind']}) needs an explicit edition or "
              "entry every time it is read, which the generated workflows cannot "
              "pass yet; lay the theme out as chrome/userChrome.css to use them",
              file=sys.stderr)
        return 2
    selected = plan["selected"]
    # The real folder the workflows should name: where chrome/ lives for a
    # standard layout, the folder with the flat files otherwise.
    theme_dir = theme / selected["subpath"] if selected["subpath"] else theme
    repo_root = scaffold.repository_root(theme_dir)
    if repo_root.resolve() == theme_dir.resolve() and theme_dir != theme:
        # No git checkout to ask: the folder passed as --theme is the
        # repository, and the edition sits inside it.
        repo_root = theme
    try:
        standard = layout.standard_root(theme, subpath=args.subpath) \
            if plan["kind"] != "standard" or args.subpath else theme_dir
        slugs = sorted(core.find_variant_sheets(standard))
        preview = scaffold.preview_settings(standard, args.variants, args.views,
                                            args.min_changed_percent, args.oldest_firefox)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for line in layout.describe_plan(plan):
        print(f"  {line}")
    if repo_root.resolve() != theme_dir.resolve():
        try:
            rel = theme_dir.resolve().relative_to(repo_root.resolve()).as_posix()
        except ValueError:
            rel = str(theme_dir)
        print(f"  workflows go to {repo_root / '.github' / 'workflows'} and point at {rel}/")
    toolkit_ref = getattr(args, "toolkit_ref", None)
    if toolkit_ref:
        print(f"  workflows install fxcss from commit {toolkit_ref}")
    else:
        print(f"  workflows pin fxcss {__version__}; that version must be published on PyPI "
              "before they run. Use --toolkit-ref <full SHA> to test an unreleased commit.")
    try:
        written, skipped = scaffold.write_workflows(
            theme_dir, slugs, watch=args.watch, showcase=args.showcase,
            previews=args.previews, force=args.force, preview=preview,
            repo_root=repo_root, toolkit_ref=toolkit_ref)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(scaffold.next_steps(written, skipped, slugs, args.watch, args.showcase,
                              args.previews))
    return 0

def _fit_names(names, width):
    """As many names as fit in `width`, then `+N more`, so columns stay put."""
    shown = []
    for i, name in enumerate(names):
        rest = len(names) - i - 1
        candidate = ", ".join(shown + [name]) + (f" +{rest} more" if rest else "")
        if len(candidate) > width and shown:
            break
        shown.append(name)
    rest = len(names) - len(shown)
    return ", ".join(shown) + (f" +{rest} more" if rest else "")


def cmd_watch(args):
    theme = args.theme.resolve()
    firefox = choose_firefox(args.firefox)
    _install_signal_handlers()

    from . import style
    print(style.title("watch", f"{style.short_path(theme)} {style.DOT} "
                               f"{style.short_path(_app_bundle(firefox))}"))
    if args.native_menus is False:
        print(style.muted("  right-click menus are XUL and themeable: right-click to "
                          "inspect them live"))

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
        print()
        print(f"  {style.good(style.TICK)} {style.strong('Theme loaded')}  "
              f"{style.muted('saving any file under chrome/ reloads it')}")
        if not args.no_devtools:
            print(style.muted(f"    {TOOLBOX_KEY} in the browser opens the Browser "
                              "Toolbox, for the UI itself"))
        print(style.muted("    Ctrl+C stops watching"))
        print()

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
                    print(f"  {style.bad(style.CROSS)} "
                          f"{style.muted(time.strftime('%H:%M:%S'))}  "
                          f"{style.bad('reload failed')}  {exc}")
                    continue
                reloads += 1
                names = _fit_names([p.name for p in changed], 30)
                ms = f"{(time.perf_counter() - t0) * 1000:.0f}"
                print(f"  {style.good(style.TICK)} "
                      f"{style.muted(time.strftime('%H:%M:%S'))}  "
                      f"{names:<30}  {style.muted('reloaded in')} {ms:>5} {style.muted('ms')}")
                if args.shot:
                    args.shot.parent.mkdir(parents=True, exist_ok=True)
                    args.shot.write_bytes(session.m.screenshot())
                    print(style.muted(f"             wrote {style.short_path(args.shot)}"))
        except KeyboardInterrupt:
            print(f"\n  Stopped after {style.plural(reloads, 'reload')}.")
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
    try:
        return _run_audit(args)
    except (OSError, ValueError, RuntimeError) as exc:
        # 1 means a completed audit found something actionable. A broken
        # browser/session is a different result, not evidence of dead CSS.
        print(f"error: audit could not complete: {exc}", file=sys.stderr)
        return 2


def _app_bundle(binary):
    """`/Applications/Firefox.app` for the binary inside it; anything else as is."""
    path = Path(binary)
    for parent in path.parents:
        if parent.suffix == ".app":
            return parent
    return path


def _run_audit(args):
    """The audit, with --no-colour holding only for its own output.

    `fxcss check` runs this in-process with no_colour=True, so the switch
    must not outlive the call and strip colour from what check prints next.
    """
    from . import style
    previous = style._override
    if args.no_colour:
        style.set_colour(False)
    try:
        return _audit_report(args)
    finally:
        style.set_colour(previous)


def _audit_report(args):
    from . import audit, omni
    from . import style
    theme = args.theme.resolve()
    firefox = choose_firefox(args.firefox)
    print(style.title("audit", style.short_path(theme)))
    pack = omni.scan(firefox)
    static = (audit.collect_unused(theme)
              if args.strict_vars or not args.no_unused else None)
    states = []
    live = sys.stdout.isatty()

    def progress(label, ids, classes):
        # A transient line on a terminal; nothing at all in a log.
        states.append((label, ids, classes))
        if live:
            print(f"\r\033[2K  {style.muted('checking ' + label + style.ELLIPSIS)}",
                  end="", flush=True)

    with core.Session(theme, firefox) as session:
        result = audit.audit(session, theme, pack=pack, progress=progress)
        try:
            version = (session.info() or {}).get("version")
        except Exception:  # a version label is not worth failing the audit for
            version = None
    if live:
        print("\r\033[2K", end="", flush=True)

    unused = None
    if static:
        # Without omni.ja to read, ask an *unthemed* Firefox which custom
        # properties it knows about. Against the themed browser every name
        # resolves, because the theme itself set them, and nothing could be
        # told apart. With the pack, the shipped sources answer better (they
        # separate declared from consumed) and no second launch is needed.
        candidates = (set(static["used"]) - set(static["defined"])) | (
            set(static["defined"]) - set(static["used"]))
        known = set()
        if candidates and pack is None:
            with core.Session(theme, firefox, empty_user_chrome=True) as vanilla:
                # Sweep the same states first: panel-scoped properties like
                # --arrowpanel-background do not exist until the panel has been
                # built, and would otherwise look like names Firefox never had.
                audit.collect_dom(vanilla, verbose=False)
                known = audit.probe_properties(vanilla, candidates)
        unused = audit.classify_unused(static, known, pack=pack)
    problems = audit.actionable(result["findings"])
    parts = []
    if problems:
        parts.append(f"{_pl(len(problems), 'selector')} "
                     f"{_agree(len(problems), 'need', 'needs')} attention")
    shown_unused = unused if (unused and not args.no_unused) else None
    if shown_unused:
        if shown_unused["missing_properties"]:
            parts.append(f"{_pl(len(shown_unused['missing_properties']), 'undefined property', 'undefined properties')}")
        if shown_unused["orphans"]:
            parts.append(f"{_pl(len(shown_unused['orphans']), 'stylesheet')} not imported")
        dead = (len(shown_unused.get("stale_overrides", []))
                + len(shown_unused.get("devtools_only", [])))
        if dead:
            parts.append(f"{_pl(dead, 'property', 'properties')} never read")
    breaking = bool(problems) or bool(shown_unused and shown_unused["missing_properties"])
    print()
    if not parts:
        print(f"  {style.good(style.TICK)} {style.strong('Nothing needs attention')}  "
              f"{style.muted('every id and class the theme uses is in this Firefox')}")
    else:
        mark = style.bad(style.CROSS) if breaking else style.warn(style.BANG)
        print(f"  {mark} {style.strong(f' {style.DOT} '.join(parts))}")
    context = []
    audit.report(result, show_all=args.all, colour=not args.no_colour, headline=False,
                 context=context)
    if shown_unused:
        audit.report_unused(unused, colour=not args.no_colour, show_all=args.all,
                            context=context)
    hint = any(r.get("hint") for r in context)
    audit.print_context(context, args.all, style.colour_on() and not args.no_colour,
                        hint=hint)

    final_ids, final_classes = (states[-1][1], states[-1][2]) if states else (0, 0)
    shipped = None
    if pack:
        paths = [Path(p) for p in pack["paths"]]
        try:
            import os.path as _osp
            common = Path(_osp.commonpath([str(p) for p in paths]))
            shipped = ", ".join(str(p.relative_to(common)) for p in paths)
        except ValueError:
            shipped = ", ".join(str(p) for p in paths)
    short_states = {"find bar open": "find bar", "address bar results": "address bar",
                    "app menu opened": "app menu", "context menus built": "context menus"}
    names = ", ".join(short_states.get(label, label) for label, _, _ in states)
    checked = [_pl(result.get("tokens", 0), "id and class", "ids and classes"),
               f"Firefox {version}" if version else "Firefox"]
    if states:
        checked.append(_pl(len(states), "state"))
    if shipped:
        checked.append("names from omni.ja")
    for line in style.rows([("checked", f" {style.DOT} ".join(checked))],
                           indent=2, label_width=11):
        print(style.muted(line))
    print()
    if args.patch:
        source_paths = None
        if getattr(args, "_layout_plan", None):
            from . import layout
            uses = {use["file"] for finding in result["findings"]
                    if finding["confidence"] == "renamed" for use in finding["uses"]}
            source_paths = layout.patch_source_paths(args._layout_plan, theme, uses)
        files = audit.write_patch(result, theme, args.patch.resolve(), source_paths=source_paths)
        if files:
            print(f"  wrote {args.patch} ({_pl(files, 'file')}); review it, then:")
            print(f"    git apply {args.patch}\n")
        else:
            print("  nothing confident enough to patch\n")
    failing = 0
    actionable = [f for f in result["findings"]
                  if f["confidence"] in ("renamed", "similar")]
    if args.strict and actionable:
        print(f"  --strict: failing because {_pl(len(actionable), 'selector')} {_agree(len(actionable), 'need', 'needs')} attention\n")
        failing = 1
    if args.strict_vars and unused:
        dead_vars = (unused["unused_properties"]
                     + unused.get("stale_overrides", [])
                     + unused.get("devtools_only", []))
        if dead_vars:
            print(f"  --strict-vars: failing because {len(dead_vars)} custom "
                  f"propert{'ies' if len(dead_vars) != 1 else 'y'} no longer "
                  f"do{'es' if len(dead_vars) == 1 else ''} anything "
                  f"(mark deliberate ones /* fxcss-keep */)\n")
            if args.no_unused:
                # The report that names them was hidden, and a failure that
                # does not say which properties is not actionable.
                for item in dead_vars:
                    where = f"{item['file']}:{item['line']}" if item.get("file") else ""
                    print(f"    {item['name']:<44} {where}".rstrip())
                print()
            failing = 1
    return failing


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
    try:
        with core.Session(theme, firefox) as session:
            if not getattr(args, "only_live", False):
                core.capture_views(session, args.out.resolve(), variants=variants,
                                   toolbar=toolbar)
            if urls:
                print("\n  live sites (captured, never compared):", flush=True)
                core.capture_live(session, args.out.resolve(), urls)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"\nwrote screenshots to {args.out}")
    return 0


def cmd_compare(args):
    try:
        from . import compare
    except ImportError as exc:
        return _needs_pillow("compare", exc)
    return compare.run(args.base.resolve(), args.head.resolve(),
                       args.out.resolve(), args.platform, args.views,
                       args.min_changed_percent)


def cmd_check(args):
    try:
        from . import check
        return check.run(args)
    except ImportError as exc:
        return _needs_pillow("check", exc)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def cmd_support(args):
    from . import support
    args.firefox = choose_firefox(args.firefox)
    return support.run(args)


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
        if sys.platform == "win32":
            os.startfile(str(out / "index.html"))
        else:
            opener = "open" if sys.platform == "darwin" else "xdg-open"
            subprocess.run([opener, str(out / "index.html")], check=False)
    return 0


def cmd_doctor(args):
    from . import style
    theme = args.theme.resolve()
    firefox = choose_firefox(args.firefox)
    print(style.title("doctor", style.short_path(theme)))
    with core.Session(theme, firefox, devtools=True) as session:
        info = session.info()

    sheets = sorted((theme / "chrome").rglob("*.css")) if (theme / "chrome").exists() else []
    total = sum(p.stat().st_size for p in sheets)
    width, height = (list(info["outer"]) + [None, None])[:2]
    dpr = info["dpr"]
    scale = f" at {dpr:g}×" if isinstance(dpr, (int, float)) else f" at dpr {dpr}"
    print()
    builds = core.discover_firefoxes()
    listed = any(b["path"] == firefox for b in builds)
    for line in style.rows([
            ("fxcss", __version__),
            ("firefox", f"{info['version']} on {style.platform_name(info['os'])}"
                        f"  {style.muted('build ' + str(info['buildID']))}"),
            # The builds table below names the binary in use; say it here
            # only when that table will not.
            ("binary", None if listed else style.short_path(firefox)),
            ("theme", f"{style.plural(len(sheets), 'stylesheet')}, {total / 1024:,.0f} KB"),
            ("window", f"{width} × {height}{scale}" if height else str(info["outer"])),
    ]):
        print(line)

    findings = {"warn": 0, "bad": 0}
    named = []

    def report(state, text, detail=None, short=None):
        if state in findings:
            findings[state] += 1
            named.append(short or text)
        for line in style.check(state, text, detail, indent=4):
            print(line)

    print()
    print(style.heading_line("Checks"))
    if info["legacyStylesheets"]:
        report("ok", "userChrome.css loading is enabled")
    else:
        report("bad", "userChrome.css loading is off",
               "set toolkit.legacyUserProfileCustomizations.stylesheets to true")
    missing = [p for p in ("chrome/userChrome.css",) if not (theme / p).exists()]
    if missing:
        report("bad", f"{', '.join(missing)} is missing",
               "run this from a theme's root folder, the one holding chrome/, or pass --theme")
    else:
        report("ok", "chrome/userChrome.css is in place")
    report("ok", f"Browser Toolbox is enabled in the throwaway profile ({TOOLBOX_KEY})")
    native = info["nativeContextMenus"]
    themeable = [p for p, v in native.items() if v is False]
    if native.get("macos"):
        report("warn", "Right-click menus are native on macOS, so CSS cannot style them",
               short="native right-click menus",
               detail=
               ("pass --native-menus=false to work on them"
                + (f"; {', '.join(t.upper() if t == 'gtk' else t for t in themeable)} "
                   "menus are XUL and themeable" if themeable else "")))
    elif native:
        states = ", ".join(f"{p}: {'native' if v else 'XUL, themeable'}"
                           for p, v in native.items() if v is not None)
        report("ok", f"Right-click menus can be styled ({states})")

    if builds:
        print()
        print(style.heading_line("Installed Gecko builds"))
        label_w = max(len(b["label"]) for b in builds)
        versions = {b["path"]: core.firefox_version(b["path"]) or "?" for b in builds}
        ver_w = max(len(v) for v in versions.values())
        for build in builds:
            using = build["path"] == firefox
            tail = f"  {style.brand('in use')}" if using else ""
            print(f"    {build['label'].ljust(label_w)}  {versions[build['path']].ljust(ver_w)}  "
                  f"{style.muted(style.short_path(build['path']))}{tail}")
        if len(builds) > 1:
            print(style.muted(f"      choose one per run with --firefox <name>, e.g. "
                              f"--firefox {builds[-1]['label']}"))

    print()
    if findings["bad"]:
        print(f"  {style.bad(style.CROSS)} {style.strong(style.plural(findings['bad'], 'problem') + ' to fix')}"
              f"  {style.muted(', '.join(named))}")
    elif findings["warn"]:
        print(f"  {style.good(style.TICK)} {style.strong('Ready to theme')}  "
              f"{style.muted(style.plural(findings['warn'], 'thing') + ' to know: ' + ', '.join(named))}")
    else:
        print(f"  {style.good(style.TICK)} {style.strong('Ready to theme')}")
    if missing:
        print(f"  {style.muted('New here?')} {style.cmd('fxcss try owner/repo')} "
              f"{style.muted('test-drives a theme;')} {style.cmd('fxcss new')} "
              f"{style.muted('starts one.')}")
    print()
    return 0


def _bool(v):
    return v.lower() not in ("false", "0", "no")


def _layout_options(p):
    p.add_argument("--subpath", default=None, metavar="DIR-OR-FILE",
                   help="which theme to take from a repository that holds several: "
                        "a folder (1.Normal), a named entry file "
                        "(src/userChrome-WhiteSur.css) or an archive (chrome.zip)")
    p.add_argument("--entry", default=None, metavar="FILE",
                   help="for a theme with no userChrome.css: the stylesheet to "
                        "load through a generated one, e.g. Brave-Fox/Import.css")


def _common(p, theme=True, firefox_help=None):
    if theme:
        p.add_argument("--theme", type=Path, default=Path.cwd(),
                       help="theme root, the folder containing chrome/ (default: cwd)")
    p.add_argument("--firefox", default=None,
                   help=firefox_help or "a channel or fork name (stable, beta, dev, nightly, esr, librewolf, floorp, waterfox, zen) or a path to a binary "
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
    ap = _TopParser(
        prog="fxcss", usage="fxcss <command> [options]", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"fxcss {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=False, metavar="<command>",
                            parser_class=argparse.ArgumentParser)

    tr = sub.add_parser(
        "try", help="test-drive a theme from GitHub or a folder in a throwaway profile",
        epilog="Looking for themes? Browse firefoxcss-store.github.io or "
               "r/FirefoxCSS — anything with a userChrome.css on GitHub works.")
    tr.add_argument("repo", help="owner/name, a github.com URL, or a local theme folder")
    tr.add_argument("--firefox", default=None,
                    help="a channel/fork name (e.g. nightly, dev, esr) or a binary path")
    tr.add_argument("--ref", default=None, help="tag, branch or commit to fetch")
    tr.add_argument("--commit", action="store_true",
                    help="use the latest commit rather than the latest release")
    tr.add_argument("--with", dest="with_sheets", default=None, metavar="NAME[,NAME]",
                    help="also load named optional stylesheets")
    _layout_options(tr)
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
    _layout_options(ins)
    ins.add_argument("--prefs", default=None, metavar="FILE[,FILE]",
                     help="also apply these shipped preference files (paths relative "
                          "to the repository, e.g. user.js or config/user.js) inside "
                          "the removable fxcss block; names are previewed first")
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
    up.add_argument("--compare", action="store_true",
                    help="render the installed theme and the new version, "
                         "and show what changes, before asking (needs Pillow "
                         "and a local Firefox)")
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
    _layout_options(up)
    up.add_argument("--prefs", default=None, metavar="FILE[,FILE]",
                    help="shipped preference files to apply (default: the ones "
                         "already applied; '' for none)")
    up.add_argument("--keep", type=int, default=3, metavar="N",
                    help="how many backups to keep (default 3; the original "
                         "chrome/ is never pruned)")
    up.add_argument("--force", action="store_true",
                    help="upgrade despite local edits or audit findings")
    up.add_argument("--no-colour", action="store_true", help="plain output")
    up.add_argument("--yes", action="store_true",
                    help="skip the confirmation prompt")
    up.set_defaults(func=cmd_upgrade)

    ad = sub.add_parser(
        "adopt", help="record a theme someone else installed, so fxcss can "
                      "manage it",
        epilog="Identifies the theme by comparing every file against the "
               "repository's own tree, so the answer is the same bytes "
               "rather than a guess. Nothing is installed or replaced: the "
               "chrome/ already there is copied to a backup and described.")
    ad.add_argument("repo", nargs="?", default=None,
                    help="owner/name or a github.com URL (default: work it "
                         "out from chrome/, when it says)")
    ad.add_argument("--profile", default=None, metavar="NAME-OR-PATH",
                    help="which Firefox profile (default: the one Firefox itself opens)")
    ad.add_argument("--ref", default=None,
                    help="check against this tag or branch only")
    ad.add_argument("--depth", type=int, default=10, metavar="N",
                    help="how many versions to check, newest first (default 10)")
    ad.add_argument("--list-profiles", action="store_true",
                    help="list the Firefox profiles found and stop")
    ad.add_argument("--yes", action="store_true",
                    help="skip the confirmation prompt")
    ad.set_defaults(func=cmd_adopt)

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
    ini.add_argument("--toolkit-ref", metavar="SHA",
                     help="pin workflows to a full fxcss Git commit instead of a PyPI release")
    ini.add_argument("--watch", action="store_true",
                     help="also add the weekly Firefox release/beta/nightly audit "
                          "and capture canary")
    ini.add_argument("--showcase", action="store_true",
                     help="also add the on-release showcase screenshot workflow")
    ini.add_argument("--previews", action="store_true",
                     help="also add the workflow that keeps README screenshots "
                          "of every view and variant up to date")
    ini.add_argument("--variants", default="all", metavar="all|NAME[,NAME]",
                     help="PR previews: optional stylesheets to capture; '' for "
                          "none (default: all)")
    ini.add_argument("--views", default="", metavar="PATTERN[,PATTERN]",
                     help="PR previews: compare and show only matching views, "
                          "e.g. 'light-*,extra-07-many-tabs' (default: every view)")
    ini.add_argument("--min-changed-percent", type=float, default=0.0, metavar="N",
                     help="PR previews: leave out views where less than N%% of "
                          "pixels changed; the comment still counts them (default: 0)")
    ini.add_argument("--oldest-firefox", default="esr", metavar="esr|VERSION",
                     help="PR previews and the watch: say whether a change drops styling "
                          "for any Firefox back to this one; 'esr' for the current ESR "
                          "(default), a major version such as 140, or '' to skip")
    ini.add_argument("--subpath", default=None, metavar="DIR",
                     help="which theme in a repository that holds several "
                          "(a folder relative to --theme)")
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
    a.add_argument("--strict-vars", action="store_true",
                   help="exit non-zero if a custom property override is dead — "
                        "this Firefox no longer reads the name (for CI)")
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
    c.add_argument("--views", default=None, metavar="PATTERN[,PATTERN]",
                   help="compare only captures whose names match, e.g. "
                        "'light-*,extra-07-many-tabs'; * and ? are wildcards. "
                        "A pattern that matches nothing is an error")
    c.add_argument("--min-changed-percent", type=float, default=0.0, metavar="N",
                   help="report a view where less than N%% of pixels changed "
                        "without drawing it or counting it as changed (default: 0)")
    c.set_defaults(func=cmd_compare)

    ck = sub.add_parser("check", help="audit, capture and compare with saved project settings")
    _common(ck, firefox_help="override the saved browser list with one channel or binary path "
                            "(default: saved settings, otherwise stable)")
    ck.add_argument("--config", type=Path, help="JSON settings file (default: <theme>/.fxcss.json)")
    ck.add_argument("--out", type=Path, help="report directory, relative to the theme")
    ck.add_argument("--baseline", type=Path, help="baseline directory, relative to the theme")
    ck.add_argument("--update-baseline", action="store_true",
                    help="accept captures as a baseline only if all checks pass")
    ck.add_argument("--variants", help="optional stylesheet captures: all or comma-separated names")
    ck.add_argument("--toolbar", help="toolbar arrangement for the captures")
    ck.add_argument("--strict", action=argparse.BooleanOptionalAction, default=None,
                    help="fail on actionable selector findings (default: true)")
    ck.add_argument("--strict-vars", action=argparse.BooleanOptionalAction, default=None,
                    help="fail on dead custom properties (default: false)")
    ck.add_argument("--max-changed-percent", type=float,
                    help="fail if any view exceeds this percent of changed pixels (0–100)")
    ck.add_argument("--views", metavar="PATTERN[,PATTERN]",
                    help="compare only matching views, e.g. 'light-*,variant-*'")
    ck.add_argument("--min-changed-percent", type=float,
                    help="report smaller changes without picturing them (0–100)")
    ck.set_defaults(func=cmd_check)

    sp = sub.add_parser("support", help="which Firefox releases a theme change still works on")
    _common(sp, theme=False, firefox_help="the newest Firefox to compare against: a channel "
                                         "name or a path to a binary")
    sp.add_argument("--base", type=Path, required=True,
                    help="the theme before the change (a folder containing chrome/)")
    sp.add_argument("--head", type=Path, default=Path.cwd(),
                    help="the theme after the change (default: cwd)")
    sp.add_argument("--oldest", default="esr", metavar="esr|VERSION",
                    help="the oldest Firefox to check: 'esr' for the current ESR "
                         "(default), or a major version such as 140")
    sp.add_argument("--channel", default=None,
                    help="label for the newest build in the report, e.g. beta")
    sp.add_argument("--cache", type=Path, default=None,
                    help="where downloaded build indexes are kept "
                         "(default: ~/.cache/fxcss/firefox-builds)")
    sp.add_argument("--max-downloads", type=int, default=8, metavar="N",
                    help="most builds to download in one run (default: 8)")
    sp.add_argument("--json", type=Path, default=None, help="also write the report as JSON")
    sp.add_argument("--markdown", type=Path, default=None,
                    help="also write the Markdown summary to this file")
    sp.set_defaults(func=cmd_support)

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


# Commands that only *read* a theme. For these a flat or otherwise
# non-standard layout is materialised into a temporary chrome/ tree, so
# `fxcss shot --theme .` works in a repository that keeps userChrome.css at
# its root. Commands that write beside the theme (init, check) or watch its
# files (watch) resolve layouts themselves or need the standard shape.
LAYOUT_AWARE = {"shot", "audit", "doctor", "snapshot", "changelog", "tweaks",
                "pick", "inspect", "catalogue"}


def _resolve_layouts(args):
    from . import layout
    if getattr(args, "cmd", None) in LAYOUT_AWARE and getattr(args, "theme", None):
        if args.cmd == "audit" and args.patch:
            args.theme, args._layout_plan = layout.standard_root(args.theme, with_plan=True)
            layout.patch_source_paths(args._layout_plan, args.theme, [])
        else:
            args.theme = layout.standard_root(args.theme)
    if getattr(args, "cmd", None) == "support":
        for key in ("base", "head"):
            value = getattr(args, key, None)
            if value is not None and Path(value).is_dir():
                setattr(args, key, layout.standard_root(value))


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
        print(landing_text(), end="")
        print()
        return 0
    # Long-running commands are a live log; keep them readable when piped.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    from .fetch import FetchError
    from .layout import LayoutError
    try:
        _resolve_layouts(args)
        return args.func(args)
    except KeyboardInterrupt:
        # Ctrl+C, or Esc at a menu: stop where we are, without a traceback.
        # Commands that need tidying up (a browser to close) do it in their
        # own finally blocks on the way here.
        from . import style
        print(f"\n  {style.muted('Stopped.')}", file=sys.stderr)
        return 130
    except LayoutError as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return 2
    except (FetchError, core.MarionetteError) as exc:
        # Expected fetch and browser failures get a concise diagnostic rather
        # than a traceback; programming errors still propagate.
        print(f"\nerror: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
