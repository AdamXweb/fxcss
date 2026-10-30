#!/usr/bin/env python3
"""What a theme needs beyond its files, stated as a checklist.

Copying ``chrome/`` and enabling one pref is a complete installation for
many themes and a fraction of one for many others: a root ``user.js`` with
twenty prefs the README calls required, a Sidebery settings file to import,
a userChrome.js loader, a particular fork, fonts, a compositor. The 132-theme
evaluation found manifests recording hundreds of copied files while every
one of those stayed undone, and nothing in the output said so.

This module reads the theme's own files and README and reports items in
three states, each with its evidence:

  required   the theme's files or wording say it will not work without this
  optional   offered by the theme; the person decides
  unknown    mentioned, but whether it is needed cannot be read off the files

Nothing here is applied. Preference files outside ``configuration/user.js``
are listed with their pref names so a person can choose them with
``--prefs``; README prose and CSS ``-moz-pref`` toggles are only reported.
That is deliberate: a crawler that applies every pref-like string it finds
would set things nobody asked for, and a copy operation that calls itself
"configured" would be a lie a person acts on.
"""

import re
from pathlib import Path

from .install import PREF_NAME_RE, _strip_js_comments

MAX_README_BYTES = 512 * 1024
MAX_PREF_FILE_BYTES = 1024 * 1024

# Preference files themes ship outside the one location fxcss applies.
# Matched against repository-relative paths, case-insensitively.
PREF_FILE_RE = re.compile(
    r"(^|/)((example[-_.]?)?user(\.js|\.js\.example|\.example\.js)"
    r"|prefs?(\.js)?|[\w-]*\.user\.js|user[-_][\w-]+\.js)$", re.I)
PREF_DIRS_RE = re.compile(r"(^|/)(prefs?|config|configuration|preferences)/[^/]+\.js$", re.I)
REQUIRED_WORD_RE = re.compile(r"(required|mandatory|needed|must)", re.I)
INSTALL_VERB_RE = re.compile(r"\b(copy|place|put|move|drop|paste|add|save)\b", re.I)
OPTIONAL_WORD_RE = re.compile(r"(optional|recommended|example|suggested)", re.I)

# Signals read from shipped files, not prose.
LOADER_FILE_RE = re.compile(r"\.(uc\.js|uc\.mjs|sys\.mjs|as\.css)$|(^|/)(config\.js|config-prefs\.js|userChrome\.js)$", re.I)
EXTENSION_CONFIG_RE = re.compile(
    r"(^|/)[\w .-]*(sidebery|tst|tree[-_ ]?style[-_ ]?tab|tab[-_ ]?center|"
    r"firefox[-_ ]?color|ublock|stylus)[\w .-]*\.(json|css|txt)$", re.I)
FONT_RE = re.compile(r"\.(ttf|otf|woff2?)$", re.I)

# Things a README can say that files cannot show. Each is (pattern, item,
# state, note). Kept to names whose presence in a theme README is almost
# always about a requirement, and reported as "unknown" unless the sentence
# around it uses a required-word.
README_SIGNALS = (
    (r"\bfx-?autoconfig\b|\buserChrome\.js\b|\bautoconfig\b|\buserChromeJS\b",
     "a userChrome.js loader (fx-autoconfig or similar)",
     "fxcss does not install loaders; .uc.js scripts stay inert without one"),
    (r"\bsidebery\b", "the Sidebery extension (and its settings)",
     "fxcss does not install extensions or import their settings"),
    (r"\btree ?style ?tabs?\b|\bTST\b", "the Tree Style Tab extension",
     "fxcss does not install extensions"),
    (r"\btab ?center ?reborn\b", "the Tab Center Reborn extension",
     "fxcss does not install extensions"),
    (r"\bfirefox ?color\b", "a Firefox Color theme",
     "fxcss does not apply Firefox Color themes"),
    (r"\bfloorp\b", "Floorp (a Firefox fork)", "verified only on the browser you run"),
    (r"\bzen browser\b|\bzen-browser\b", "Zen Browser (a Firefox fork)",
     "verified only on the browser you run"),
    (r"\blibrewolf\b", "LibreWolf (a Firefox fork)", "verified only on the browser you run"),
    (r"\bwaterfox\b", "Waterfox (a Firefox fork)", "verified only on the browser you run"),
    (r"\bmica\b|\bwindows 11\b", "Windows 11 (Mica/compositor effects)",
     "platform-specific appearance cannot be reproduced elsewhere"),
    (r"\bgnome\b|\bkde\b|\bpicom\b|\bhyprland\b|\bcompositor\b",
     "a Linux desktop/compositor setup", "transparency and blur depend on it"),
    (r"\bvertical tabs?\b|\bsidebar\.verticalTabs\b", "Firefox's native vertical tabs",
     "a setting in Firefox's own sidebar options"),
    (r"\bfont\b|\bfonts\b|\bnerd font\b", "fonts the theme expects",
     "fonts are not installed by fxcss"),
    (r"\bdevPixelsPerPx\b|\bui\.scale\b", "a display scaling preference",
     "listed because changing it affects every window, not only the theme"),
)
# Pref names in prose: "browser.tabs.allow_transparent_browser" and the like.
README_PREF_RE = re.compile(
    r"\b((?:toolkit|browser|layout|svg|widget|ui|sidebar|gfx|devtools|privacy|"
    r"network|dom|security|general|extensions|uc|userChrome|userchrome|full-screen-api|"
    r"app|font|media|pdfjs|print|signon|places|services|nglayout|mousewheel|"
    r"zoom|view_source|image|html5|intl|keyword|middlemouse|permissions|pref|"
    r"reader|startup|accessibility|alerts|apz|clipboard|config|content|"
    r"cookiebanners|datareporting|identity|javascript|lightweightThemes|"
    r"mail|mailnews|findbar|urlclassifier|webgl|xpinstall)\.[A-Za-z0-9_.-]*[A-Za-z0-9_])\b")

CSS_PREF_RE = re.compile(r"""-moz-(?:bool-)?pref\(\s*["']([^"']+)["']""")


def _read(path, limit):
    try:
        if path.stat().st_size > limit:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def pref_names_in(path):
    """Pref names a JavaScript preference file sets, in order, deduplicated."""
    code = _strip_js_comments(_read(path, MAX_PREF_FILE_BYTES))
    names = []
    for match in PREF_NAME_RE.finditer(code):
        name = match.group(2)
        if name not in names:
            names.append(name)
    return names


def _walk(root, limit=20000):
    root = Path(root)
    count = 0
    for path in sorted(root.rglob("*")):
        parts = path.relative_to(root).parts
        if any(p in (".git", "node_modules", "__pycache__") for p in parts):
            continue
        count += 1
        if count > limit:
            return
        if path.is_file() and not path.is_symlink():
            yield path, "/".join(parts)


def pref_files(repo_root, theme_root=None):
    """Shipped preference files, each with its names and a state guess.

    ``configuration/user.js`` under the theme root is what fxcss applies and
    is reported as "applied". Anything else is "unknown" unless its name or
    folder says required/recommended/example. The guess is a *label* for the
    person; nothing is applied from it.
    """
    repo_root = Path(repo_root)
    theme_root = Path(theme_root) if theme_root else repo_root
    applied = (theme_root / "configuration" / "user.js")
    found = []
    for path, relative in _walk(repo_root):
        lowered = relative.lower()
        if not (PREF_FILE_RE.search(relative) or PREF_DIRS_RE.search(relative)):
            continue
        if path.suffix.lower() not in (".js", ".example") and not lowered.endswith(".js.example"):
            continue
        names = pref_names_in(path)
        if not names:
            continue
        if applied.is_file() and path.resolve() == applied.resolve():
            state = "applied"
        elif REQUIRED_WORD_RE.search(relative):
            state = "required"
        elif OPTIONAL_WORD_RE.search(relative):
            state = "optional"
        else:
            state = "unknown"
        found.append({"path": relative, "names": names, "state": state,
                      "count": len(names)})
    return found


def readme_text(repo_root):
    for name in ("README.md", "readme.md", "README", "README.rst", "README.txt"):
        path = Path(repo_root) / name
        if path.is_file():
            return _read(path, MAX_README_BYTES)
    return ""


def _sentences(text):
    return re.split(r"(?<=[.!?\n])\s+", text)


def build(repo_root, theme_root=None, plan=None, applied_pref_files=()):
    """The checklist: [{item, state, evidence, note}], required first.

    `applied_pref_files` are repository-relative preference files the
    person chose with --prefs; those are reported as applied.
    """
    repo_root = Path(repo_root)
    theme_root = Path(theme_root) if theme_root else repo_root
    items = []
    applied = {str(p).replace("\\", "/") for p in applied_pref_files or ()}

    readme = readme_text(repo_root)
    sentences = _sentences(readme) if readme else []
    for entry in pref_files(repo_root, theme_root):
        state = entry["state"]
        if entry["path"] in applied or state == "applied":
            state = "applied"
        elif state == "unknown" and sentences:
            # A README that tells the reader to copy the file into the
            # profile is an installation step, which makes the file required.
            base = re.escape(Path(entry["path"]).name)
            hits = [s for s in sentences if re.search(rf"(?<![\w.]){base}(?![\w])", s)]
            if any(REQUIRED_WORD_RE.search(s) or INSTALL_VERB_RE.search(s) for s in hits):
                state = "required"
            elif any(OPTIONAL_WORD_RE.search(s) for s in hits):
                state = "optional"
        preview = ", ".join(entry["names"][:5]) + (" …" if entry["count"] > 5 else "")
        items.append({
            "item": f"preferences in {entry['path']} ({entry['count']} pref(s): {preview})",
            "state": state,
            "evidence": entry["path"],
            "note": ("applied inside the fxcss block of user.js" if state == "applied" else
                     f"not applied; `--prefs {entry['path']}` adds them to the managed block"),
            "pref_file": entry["path"],
            "names": entry["names"],
        })

    loaders, ext_configs, fonts = [], [], []
    for path, relative in _walk(repo_root):
        if LOADER_FILE_RE.search(relative):
            loaders.append(relative)
        elif EXTENSION_CONFIG_RE.search(relative):
            ext_configs.append(relative)
        elif FONT_RE.search(relative):
            fonts.append(relative)
    if loaders:
        shown = ", ".join(loaders[:3]) + (" …" if len(loaders) > 3 else "")
        items.append({"item": f"a userChrome.js loader for {len(loaders)} script file(s)",
                      "state": "required", "evidence": shown,
                      "note": "fxcss copies the files but installs no loader; without "
                              "fx-autoconfig or similar they do nothing"})
    if ext_configs:
        shown = ", ".join(ext_configs[:3]) + (" …" if len(ext_configs) > 3 else "")
        items.append({"item": "extension settings the theme ships (import them in the extension)",
                      "state": "unknown", "evidence": shown,
                      "note": "fxcss does not install extensions or import their settings"})
    if fonts:
        items.append({"item": f"{len(fonts)} font file(s) shipped with the theme",
                      "state": "unknown", "evidence": fonts[0],
                      "note": "fonts referenced from the stylesheets load from chrome/; "
                              "ones meant to be installed system-wide are not"})

    for pattern, item, note in README_SIGNALS:
        hits = [s for s in sentences if re.search(pattern, s, re.I)]
        if not hits:
            continue
        state = "required" if any(REQUIRED_WORD_RE.search(s) for s in hits) else "unknown"
        if state != "required" and any(OPTIONAL_WORD_RE.search(s) for s in hits):
            state = "optional"
        evidence = re.sub(r"\s+", " ", hits[0]).strip()[:120]
        items.append({"item": item, "state": state, "evidence": f"README: {evidence}",
                      "note": note})

    if readme:
        mentioned = []
        known = {n for i in items for n in i.get("names", [])}
        for name in README_PREF_RE.findall(readme):
            if name not in mentioned and name not in known and name.count(".") >= 1 \
                    and not name.lower().endswith((".css", ".js", ".json", ".md", ".com", ".org")):
                mentioned.append(name)
        if mentioned:
            shown = ", ".join(mentioned[:6]) + (" …" if len(mentioned) > 6 else "")
            items.append({"item": f"{len(mentioned)} preference name(s) mentioned in the README",
                          "state": "unknown", "evidence": shown,
                          "note": "read the README; fxcss never applies prefs from prose"})

    toggles = set()
    chrome = theme_root / "chrome"
    if chrome.is_dir():
        for path, relative in _walk(chrome, limit=5000):
            if path.suffix.lower() == ".css":
                toggles.update(CSS_PREF_RE.findall(_read(path, MAX_PREF_FILE_BYTES)))
    if toggles:
        shown = ", ".join(sorted(toggles)[:6]) + (" …" if len(toggles) > 6 else "")
        items.append({"item": f"{len(toggles)} -moz-pref() toggle(s) the stylesheets read",
                      "state": "optional", "evidence": shown,
                      "note": "set in about:config to switch features; nothing is set for you"})

    if plan and plan.get("kind") == "content-only":
        items.append({"item": "nothing styles the browser window",
                      "state": "unknown", "evidence": "userContent.css only",
                      "note": "this theme styles web and extension pages"})

    order = {"required": 0, "applied": 1, "optional": 2, "unknown": 3}
    items.sort(key=lambda i: order.get(i["state"], 4))
    return items


def render(items, indent="  "):
    """Lines for a terminal. Empty list when there is nothing to say."""
    if not items:
        return []
    counts = {}
    for item in items:
        counts[item["state"]] = counts.get(item["state"], 0) + 1
    summary = ", ".join(f"{n} {state}" for state, n in sorted(
        counts.items(), key=lambda kv: {"required": 0, "applied": 1, "optional": 2,
                                        "unknown": 3}.get(kv[0], 4)))
    lines = [f"{indent}setup beyond the files ({summary}):"]
    for item in items:
        tag = {"required": "REQUIRED", "applied": "applied ", "optional": "optional",
               "unknown": "unknown "}[item["state"]]
        lines.append(f"{indent}  {tag}  {item['item']}")
        if item.get("note"):
            lines.append(f"{indent}            {item['note']}")
    return lines


def complete(items):
    """Can the installation honestly be called complete? Only with no
    required item left unapplied."""
    return not any(i["state"] == "required" for i in items)
