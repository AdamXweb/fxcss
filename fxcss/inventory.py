#!/usr/bin/env python3
"""One inventory of a theme's stylesheets: what loads, from where, and why.

Install, upgrade, audit and the generated workflows used to each read a
theme's ``@import`` lines with their own regular expression, and they
disagreed: one missed unquoted ``url()`` imports and reported twenty live
sheets as orphans, another missed ``@import "x.css" screen and
-moz-pref("…")`` and called the conditional sheet dead. This module is the
single reading they now share.

It is syntax-aware in the ways that matter for userChrome themes -- comments
and strings are consumed whole, quoted and unquoted targets are both read,
CSS escapes in a target are decoded, and the condition after an import is
kept -- and it states what it could not settle rather than guessing: an
import that resolves to nothing is *missing*, *conditional* (behind a
``-moz-pref`` or media query), a *hook* (a conventional user-override file
the theme does not ship) or *external* (``chrome://``, ``data:``, a URL),
and those are reported as different things.

Scope is tracked from the entry sheet: ``userChrome.css`` reaches the
browser window, ``userContent.css`` reaches web content and extension
pages, and a sheet reached only from the latter is never evidence of
browser-chrome breakage. ``complete`` is False whenever an entry sheet is
missing or a required import is unresolved, so a half-shipped package reads
as an incomplete analysis rather than a clean bill of health.
"""

import os
import re
from pathlib import Path
from urllib.parse import unquote

# The stylesheets Firefox loads from chrome/ by name, with the documents
# each one reaches.
ENTRY_SCOPES = {"userChrome.css": "chrome", "userContent.css": "content"}
ENTRY_SHEETS = tuple(ENTRY_SCOPES)

# Conventional names for a user's own override sheet, which themes @import
# without shipping: the import of a missing file fails silently and that is
# the whole on/off mechanism. Lower-case, compared against the file name.
HOOK_NAMES = frozenset({
    "customchrome.css", "customcontent.css", "custom.css", "user.css",
    "local.css", "overrides.css", "override.css", "my.css", "mine.css",
    "userchrome.local.css", "usercontent.local.css", "usercustom.css",
})

# Beyond the exact names, a missing import whose stem says it is the
# person's own file (FlexFox's components/uc-user-settings.css, "create it
# to modify the interface") is a hook too. Deliberately narrow: "user-chrome"
# is Material Fox's *build output*, and calling that a hook would hide the
# fact that the source tree needs compiling.
HOOK_STEM_RE = re.compile(r"(custom|settings|local|overrides?|mine|my[-_])", re.I)

# Directories of deliberately opt-in sheets. Searched both beside chrome/
# and inside it; a sheet an entry already reaches is active, not optional.
# A missing import that points into one of these (Safari Golden Gate's
# userContent.css imports ThemeCore/custom/<option>.css for every option it
# offers) is an option switched off, not a broken reference.
OPTION_DIRS = ("custom", "optional", "options", "extras", "variants",
               "tweaks", "presets")

# A bound on how much of a theme is read: the biggest real theme in the
# 132-entry catalogue has a few hundred stylesheets.
MAX_SHEETS = 4000
MAX_SHEET_BYTES = 4 * 1024 * 1024

# One pass over a stylesheet: comments and strings are consumed whole, so an
# @import or url() inside either is never mistaken for a live reference, and
# a "/*" inside a string never starts a comment. Imports keep the text up to
# the terminating semicolon, which is where the condition lives.
_UNQUOTED_URL = r'''(?:\\(?:[0-9a-fA-F]{1,6}[ \t\r\n\f]?|[^\r\n\f])|[^\\"'()\s])+'''
_TOKEN_RE = re.compile(r"""
    /\*.*?(?:\*/|\Z)
  | @import\s+(?:url\(\s*)?(?P<q1>["'])(?P<imp>(?:\\.|(?!(?P=q1))[^\\\n])*)(?P=q1)\s*\)?(?P<cond>[^;]*)
  | @import\s+url\(\s*(?P<bimp>__URL__)\s*\)(?P<bcond>[^;]*)
  | (?<![\w-])url\(\s*(?P<q2>["'])(?P<url>(?:\\.|(?!(?P=q2))[^\\\n])*)(?P=q2)\s*\)
  | (?<![\w-])url\(\s*(?P<burl>__URL__)\s*\)
  | (?P<q3>["'])(?:\\.|(?!(?P=q3))[^\\\n])*(?:(?P=q3)|(?=\n)|\Z)
""".replace("__URL__", _UNQUOTED_URL), re.S | re.I | re.X)

_ESCAPE_RE = re.compile(r"\\(?:([0-9a-fA-F]{1,6})[ \t\n]?|(.))", re.S)
_PREF_RE = re.compile(r"""-moz-(?:bool-)?pref\(\s*(["'])(.*?)\1""", re.S)
_SCHEME_RE = re.compile(r"^[A-Za-z][\w+.-]*:")


def css_unescape(text):
    """Decode CSS escapes: ``\\20`` is a space, ``\\"`` a quote, ``\\é`` é."""
    def one(match):
        if match.group(1):
            try:
                value = int(match.group(1), 16)
                return chr(value) if value and not 0xD800 <= value <= 0xDFFF else "\ufffd"
            except (ValueError, OverflowError):
                return "\ufffd"
        return match.group(2)
    return _ESCAPE_RE.sub(one, text)


def references(text):
    """Every live reference in a stylesheet, in source order.

    Yields {kind: "import"|"url", target, condition, condition_kind, prefs}
    with `target` already unescaped and stripped. `condition_kind` is "" for
    an unconditional import, "pref" when a ``-moz-pref`` / ``-moz-bool-pref``
    guards it (`prefs` names them), "supports" or "media" otherwise. url()
    references never carry a condition.
    """
    for match in _TOKEN_RE.finditer(text):
        kind = target = condition = None
        if match.group("imp") is not None:
            kind, target, condition = "import", match.group("imp"), match.group("cond")
        elif match.group("bimp") is not None:
            kind, target, condition = "import", match.group("bimp"), match.group("bcond")
        elif match.group("url") is not None:
            kind, target = "url", match.group("url")
        elif match.group("burl") is not None:
            kind, target = "url", match.group("burl")
        else:
            continue
        condition = (condition or "").strip()
        # ``layer(name)`` alone is not a condition on loading.
        condition = re.sub(r"^layer(?:\([^)]*\))?\s*", "", condition).strip()
        prefs = [name for _, name in _PREF_RE.findall(condition)]
        if prefs:
            condition_kind = "pref"
        elif condition.lower().startswith("supports("):
            condition_kind = "supports"
        elif condition:
            condition_kind = "media"
        else:
            condition_kind = ""
        yield {"kind": kind, "target": css_unescape(target).strip(),
               "condition": condition, "condition_kind": condition_kind,
               "prefs": prefs}


def local_target(target):
    """The relative file path a reference names, or None when it is not one.

    Anything absolute -- a scheme (chrome://, data:, file://, https:), a
    leading slash, a drive letter -- or a bare #fragment resolves the same
    wherever the sheet sits, so only relative paths are a theme's own.
    """
    target = (target or "").strip()
    if not target or target.startswith(("#", "/", "\\")) or _SCHEME_RE.match(target):
        return None
    return unquote(re.split(r"[?#]", target, maxsplit=1)[0]) or None


def _read(path):
    try:
        if path.stat().st_size > MAX_SHEET_BYTES:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _locate(root, key):
    """The file a sheet key names: chrome-relative, or absolute for outside."""
    return Path(key) if Path(key).is_absolute() else root / key


def is_inside(key):
    """Is an inventory key a chrome/-relative path (as opposed to outside)?"""
    return not Path(key).is_absolute()


def _relative(path, chrome):
    try:
        return Path(os.path.normpath(path)).relative_to(chrome).as_posix()
    except ValueError:
        return None


def _is_hook(resolved):
    name = Path(resolved).name.lower()
    return name in HOOK_NAMES or bool(HOOK_STEM_RE.search(Path(resolved).stem))


def _is_option_slot(resolved, option_names):
    parts = [part.lower() for part in Path(resolved).parts[:-1]]
    if any(part in OPTION_DIRS for part in parts):
        return True
    name = Path(resolved).name.lower()
    return bool(option_names) and (name in option_names or Path(name).stem in option_names)


def scan(chrome_dir, entries=None, option_names=None):
    """Read a chrome/ directory into an inventory.

    `entries` overrides which sheets are the roots (defaults to whichever of
    userChrome.css / userContent.css exist). `option_names` are the file
    names (or stems) of optional sheets the theme offers; a missing import
    of one of those is an option that is off, not a broken reference.
    Returns a dict:

      chrome        the directory scanned
      entries       entry sheets present, in ENTRY_SHEETS order
      missing_entries  entry sheets absent (informational: a content-only
                    theme legitimately has no userChrome.css)
      sheets        {relative: {scope: [..], conditional: bool, via: [..]}}
                    for every stylesheet reachable from an entry
      assets        {relative, ...} non-stylesheet files reached by url()
      edges         [{source, kind, target, resolved, status, condition,
                    condition_kind, prefs, scope}] for every reference
      unresolved    required imports that resolve to nothing (status missing)
      hooks         missing imports of conventional user-override names
      conditional   imports behind a pref/media/supports condition whose
                    target is absent (not required, not an error)
      options_off   imports of optional sheets not currently installed
      entry_unresolved  the subset of `unresolved` imported directly by an
                    entry sheet: when those are missing, the theme's top
                    level is missing, which usually means it needs building
      external      references to chrome://, data:, http(s) and the like
      outside       references that resolve outside chrome/ (not followed)
      complete      True when every entry exists that the theme ships and
                    no required import is unresolved
      reasons       why `complete` is False, in words
    """
    chrome = Path(chrome_dir)
    root = Path(os.path.normpath(chrome))
    present = [name for name in ENTRY_SHEETS if (chrome / name).is_file()]
    if entries is not None:
        present = [name for name in entries if (chrome / name).is_file()]
    inventory = {
        "chrome": chrome,
        "entries": present,
        "missing_entries": [name for name in ENTRY_SHEETS if name not in present],
        "sheets": {}, "assets": set(), "edges": [],
        "unresolved": [], "hooks": [], "conditional": [], "external": [],
        "outside": [], "options_off": [], "entry_unresolved": [],
        "complete": True, "reasons": [],
    }
    option_names = {str(n).lower() for n in (option_names or ())}
    if not present:
        inventory["complete"] = False
        inventory["reasons"].append(
            "no userChrome.css or userContent.css: nothing Firefox loads by name")
        return inventory

    queue = []
    for name in present:
        scope = ENTRY_SCOPES.get(name, "chrome")
        inventory["sheets"][name] = {"scope": {scope}, "conditional": False,
                                     "via": [], "entry": True}
        queue.append(name)

    seen_edges = set()
    while queue:
        relative = queue.pop(0)
        sheet = inventory["sheets"][relative]
        if len(inventory["sheets"]) > MAX_SHEETS:
            inventory["complete"] = False
            inventory["reasons"].append(f"more than {MAX_SHEETS} stylesheets; stopped reading")
            break
        text = _read(_locate(root, relative))
        if text is None:
            inventory["complete"] = False
            inventory["reasons"].append(f"{relative} could not be read")
            continue
        for ref in references(text):
            edge = dict(ref, source=relative, resolved=None, status=None,
                        scope=sorted(sheet["scope"]))
            local = local_target(ref["target"])
            if local is None:
                edge["status"] = "external"
                inventory["external"].append(edge)
                inventory["edges"].append(edge)
                continue
            target = Path(os.path.normpath(_locate(root, relative).parent / local))
            resolved = _relative(target, root)
            if resolved is None:
                # Outside chrome/: still read for analysis (a theme may keep
                # its sources beside chrome/ and import them), but keyed by
                # its absolute path so nothing mistakes it for an installable
                # file. `outside` lists these for callers that copy.
                resolved = target.as_posix()
                edge["outside"] = True
                inventory["outside"].append(edge)
            edge["resolved"] = resolved
            key = (relative, resolved, ref["kind"])
            if target.is_file():
                edge["status"] = "resolved"
                if ref["kind"] == "import" or resolved.lower().endswith(".css"):
                    conditional = sheet["conditional"] or bool(ref["condition_kind"])
                    known = inventory["sheets"].get(resolved)
                    if known is None:
                        inventory["sheets"][resolved] = {
                            "scope": set(sheet["scope"]),
                            "conditional": conditional, "via": [relative],
                            "entry": False}
                        queue.append(resolved)
                    else:
                        grew = not sheet["scope"] <= known["scope"]
                        known["scope"] |= sheet["scope"]
                        if relative not in known["via"]:
                            known["via"].append(relative)
                        if known["conditional"] and not conditional:
                            known["conditional"] = False
                            grew = True
                        if grew and resolved not in queue:
                            queue.append(resolved)
                else:
                    inventory["assets"].add(resolved)
            elif ref["kind"] == "import" and _is_hook(resolved):
                edge["status"] = "hook"
                inventory["hooks"].append(edge)
            elif ref["kind"] == "import" and _is_option_slot(resolved, option_names):
                edge["status"] = "option"
                inventory["options_off"].append(edge)
            elif ref["kind"] == "import" and (ref["condition_kind"] or sheet["conditional"]):
                edge["status"] = "conditional"
                inventory["conditional"].append(edge)
            elif ref["kind"] == "import":
                edge["status"] = "missing"
                if key not in seen_edges:
                    inventory["unresolved"].append(edge)
            else:
                edge["status"] = "missing"
            seen_edges.add(key)
            inventory["edges"].append(edge)

    for relative, sheet in inventory["sheets"].items():
        sheet["scope"] = sorted(sheet["scope"])
    inventory["entry_unresolved"] = [e for e in inventory["unresolved"]
                                     if e["source"] in inventory["entries"]]
    if inventory["unresolved"]:
        inventory["complete"] = False
        shown = ", ".join(f"{e['source']} → {e['target']}" for e in inventory["unresolved"][:4])
        more = len(inventory["unresolved"]) - 4
        inventory["reasons"].append(
            f"{len(inventory['unresolved'])} required import(s) resolve to nothing: "
            f"{shown}{f', … and {more} more' if more > 0 else ''}")
    return inventory


def reachable_paths(chrome_dir, entries=None):
    """Chrome-relative paths (sheets and assets) an entry sheet reaches.

    Files outside chrome/ are left out: they can be analysed but not
    installed from here.
    """
    found = scan(chrome_dir, entries)
    return {key for key in set(found["sheets"]) | set(found["assets"]) if is_inside(key)}


def chrome_scoped(inventory, relative):
    """Can a sheet's rules reach the browser window?

    True for sheets reachable from userChrome.css, and -- deliberately --
    for sheets the inventory does not know about at all: an unknown sheet
    may well be chrome, and hiding findings is the wrong default.
    """
    sheet = inventory["sheets"].get(relative)
    return sheet is None or "chrome" in sheet["scope"]


def options(repo_root, chrome_dir=None, inventory=None):
    """Optional stylesheets a theme ships, under one stable identity.

    Searches OPTION_DIRS beside the theme (``custom/``, ``optional/`` …)
    and inside chrome/ (``chrome/extras/``, ``chrome/tweaks/`` …). A sheet
    an entry already reaches is part of the theme, not an option, and is
    left out. Returns [{"id", "path", "stem", "folder", "inside_chrome"}]
    where `id` is the path relative to `repo_root` with forward slashes --
    the identity install, upgrade continuity and workflows agree on.
    """
    repo_root = Path(repo_root)
    chrome_dir = Path(chrome_dir) if chrome_dir else repo_root / "chrome"
    if inventory is None and chrome_dir.is_dir():
        inventory = scan(chrome_dir)
    reachable = set((inventory or {}).get("sheets") or ())
    found, seen = [], set()
    for base, inside in ((repo_root, False), (chrome_dir, True)):
        if not base.is_dir():
            continue
        for folder in OPTION_DIRS:
            directory = base / folder
            if not directory.is_dir():
                continue
            for css in sorted(directory.glob("*.css")):
                if not css.is_file():
                    continue
                relative_chrome = _relative(css, chrome_dir) if inside else None
                if inside and relative_chrome in reachable:
                    continue
                try:
                    ident = css.relative_to(repo_root).as_posix()
                except ValueError:
                    ident = css.as_posix()
                if ident in seen:
                    continue
                seen.add(ident)
                found.append({"id": ident, "path": css, "stem": css.stem,
                              "folder": folder, "inside_chrome": inside})
    return found


def match_option(wanted, available):
    """Find a recorded option among the options a version offers.

    `wanted` is what a manifest recorded -- preferably the relative-path id,
    but older manifests hold only the stem. The same relative path wins
    outright; failing that, a unique stem match; otherwise None. Returned as
    the option dict.
    """
    text = str(wanted or "")
    for option in available:
        if option["id"] == text:
            return option
    lowered = text.lower()
    by_stem = [o for o in available if o["stem"].lower() == lowered
               or o["id"].lower() == lowered]
    if len(by_stem) == 1:
        return by_stem[0]
    # Several sheets share the stem: prefer one beside the theme, the place
    # older manifests could only have meant.
    outside = [o for o in by_stem if not o["inside_chrome"]]
    if len(outside) == 1:
        return outside[0]
    return None


def summary(inventory):
    """Short human lines describing analysis completeness and scope."""
    lines = []
    sheets = inventory["sheets"]
    chrome_only = sum(1 for s in sheets.values() if s["scope"] == ["chrome"])
    content_only = sum(1 for s in sheets.values() if s["scope"] == ["content"])
    both = len(sheets) - chrome_only - content_only
    parts = []
    if chrome_only:
        parts.append(f"{chrome_only} browser-window")
    if content_only:
        parts.append(f"{content_only} web-content")
    if both:
        parts.append(f"{both} shared")
    lines.append(f"{len(sheets)} stylesheet(s) load" + (f" ({', '.join(parts)})" if parts else ""))
    if inventory["conditional"]:
        lines.append(f"{len(inventory['conditional'])} conditional import(s) "
                     "point at files not shipped (off unless their pref/media applies)")
    if inventory["hooks"]:
        names = ", ".join(sorted({Path(e["resolved"]).name for e in inventory["hooks"]}))
        lines.append(f"user hook(s) not shipped, as intended: {names}")
    if not inventory["complete"]:
        for reason in inventory["reasons"]:
            lines.append(f"incomplete: {reason}")
    return lines
