"""Read the chrome Firefox ships — stylesheets, scripts, markup — without
running Firefox.

Everything the browser's own UI is built from lives in two `omni.ja`
archives next to the binary. They are technically ZIPs, but "optimized"
ones: the central directory is relocated, and Python's zipfile refuses the
whole archive ("Bad magic number for central directory"). The local file
headers are laid out conventionally though, so walking those sequentially
reads every entry without ever consulting the central directory.

What this buys the audit is *consumption* evidence. A live browser can say
whether a custom property resolves to a value — whether something declares
it — but not whether any rule still reads it, and a declared-but-unread
property is exactly what an override silently dies against (the window-modal
dialog stayed white under WhiteSur's dark palettes for that reason: the
theme set `--in-content-page-background`, which current Firefox neither
declares nor reads). Scanning the shipped sources answers both questions,
covers documents the live audit cannot reach (dialogs, in-content pages), and
needs no second Firefox launch.

DevTools is kept apart. Its stylesheets style only DevTools' own documents,
and it lags the browser's renames: Firefox 158's toolbar reads
`--toolbarbutton-background-color-hover` while DevTools still declares and
reads `--toolbarbutton-hover-background`. Pooled together, DevTools' reader
vouched for an override no toolbar rule had read for releases.
"""
import difflib
import re
import struct
import zlib
from collections import Counter
from pathlib import Path

# var(--x) in CSS is direct consumption. In scripts, any literal token is
# taken as consumption evidence (getPropertyValue, setProperty, template
# strings) — deliberately loose, because a false "consumed" merely hides a
# dead property while a false "dead" accuses a working override.
CSS_VAR_USE = re.compile(r"var\(\s*(--[\w-]+)")
CSS_VAR_DEF = re.compile(r"(--[\w-]+)\s*:")
JS_VAR = re.compile(r"(--[A-Za-z][\w-]+)")
ID_ATTR = re.compile(r'\bid="([A-Za-z_][\w-]*)"')
CLASS_ATTR = re.compile(r'\bclass="([^"]+)"')

SCRIPT_SUFFIXES = (".js", ".mjs", ".jsm")
MARKUP_SUFFIXES = (".xhtml", ".html", ".xml", ".svg", ".inc")

# Where DevTools packs its documents: chrome://devtools/ and
# resource://devtools/ both map here. Not chrome/devtools-startup/, which is
# the browser-side shim that opens DevTools and is loaded into every window.
DEVTOOLS_ENTRY = "chrome/devtools/"


def pack_paths(firefox):
    """The omni.ja archives for this Firefox binary, wherever they live."""
    binary = Path(firefox).resolve()
    # macOS keeps them under Contents/Resources, a sibling of Contents/MacOS;
    # Linux and Windows keep them next to the binary itself.
    roots = (binary.parent.parent / "Resources", binary.parent)
    found = []
    for root in roots:
        for rel in ("omni.ja", "browser/omni.ja"):
            path = root / rel
            if path.is_file() and path not in found:
                found.append(path)
    return found


def read_entries(path):
    """Yield (name, content) for each archive entry, walking local headers.

    Tolerant by construction: an entry that fails to decompress yields empty
    bytes rather than aborting the walk, because one bad entry should not
    cost the corpus the other few thousand.
    """
    data = Path(path).read_bytes()
    pos = 0
    while True:
        pos = data.find(b"PK\x03\x04", pos)
        if pos < 0:
            return
        try:
            (_, _, _, method, _, _, _, csize, _, nlen, elen) = struct.unpack_from(
                "<IHHHHHIIIHH", data, pos)
        except struct.error:
            return
        name = data[pos + 30:pos + 30 + nlen].decode("utf-8", "replace")
        start = pos + 30 + nlen + elen
        raw = data[start:start + csize]
        if method == 0:
            content = raw
        elif method == 8:
            try:
                content = zlib.decompressobj(-15).decompress(raw)
            except zlib.error:
                content = b""
        else:
            content = b""
        yield name, content
        pos = start + max(csize, 1)


def scan(firefox):
    """Everything the audit wants to know about this build's shipped chrome.

    Returns None when no omni.ja can be found (an unpackaged local build,
    say), in which case callers fall back to live probing. Otherwise a dict:

      consumed           {property name: first file that reads it}
      declared           {property name} declared in any shipped stylesheet
      devtools_consumed  the same two, for DevTools' own files, which
      devtools_declared  `consumed` and `declared` leave out
      ids                {id: first markup file carrying it}
      classes            {class: first markup file carrying it}

    Markup ids and classes are not split: DevTools' markup is still where a
    selector scoped to DevTools documents finds its element.
    """
    paths = pack_paths(firefox)
    if not paths:
        return None
    ids, classes = {}, {}
    browser, devtools = ({}, set()), ({}, set())
    for pack in paths:
        label = Path(pack).name
        for name, content in read_entries(pack):
            where = f"{label}!{name}"
            consumed, declared = (devtools if name.startswith(DEVTOOLS_ENTRY)
                                  else browser)
            if name.endswith(".css"):
                text = content.decode("utf-8", "replace")
                for prop in CSS_VAR_USE.findall(text):
                    consumed.setdefault(prop, where)
                declared.update(CSS_VAR_DEF.findall(text))
            elif name.endswith(SCRIPT_SUFFIXES):
                text = content.decode("utf-8", "replace")
                for prop in JS_VAR.findall(text):
                    consumed.setdefault(prop, where)
            elif name.endswith(MARKUP_SUFFIXES):
                text = content.decode("utf-8", "replace")
                for prop in CSS_VAR_USE.findall(text):
                    consumed.setdefault(prop, where)
                for value in ID_ATTR.findall(text):
                    ids.setdefault(value, where)
                for value in CLASS_ATTR.findall(text):
                    for cls in value.split():
                        classes.setdefault(cls, where)
    return {"paths": paths, "consumed": browser[0], "declared": browser[1],
            "devtools_consumed": devtools[0], "devtools_declared": devtools[1],
            "ids": ids, "classes": classes}


# Names the shipped chrome can give an element, by kind, read from every
# place one is written: markup attributes, stylesheet selectors, and the
# scripts that build elements or look them up. Loose on purpose, like
# CSS_VAR_USE above: a name wrongly counted as present only hides a warning,
# while one wrongly counted as missing would accuse a working rule.
SELECTOR_NAME = re.compile(r"(?<![\w-])([#.])([A-Za-z_][\w-]*)")
CSS_NOISE = re.compile(r"/\*.*?\*/|url\([^)]*\)|(['\"])(?:\\.|(?!\1).)*\1", re.S)
JS_STRING = re.compile(r"""(["'`])((?:\\.|(?!\1)[^\\\n])*)\1""")
JS_ID = re.compile(r"""getElementById\(\s*["'`]([A-Za-z_][\w-]*)""")
JS_CLASS_CALL = re.compile(r"""classList\.(?:add|remove|toggle|contains|replace)\(([^)]*)\)""")
JS_SET_ATTR = re.compile(r"""setAttribute\(\s*["'](id|class)["']\s*,\s*["'`]([^"'`]*)""")
JS_ASSIGN = re.compile(r"""\.(id|className)\s*=\s*["'`]([^"'`]*)""")
PROPERTY_NAME = re.compile(r"(?<![\w-])(--[A-Za-z_][\w-]*)")
NAME = re.compile(r"[A-Za-z_][\w-]*")
TEXT_SUFFIXES = (".css",) + SCRIPT_SUFFIXES + MARKUP_SUFFIXES


def chrome_names(paths):
    """{"ids", "classes", "properties"} any shipped chrome file mentions.

    Unlike scan(), this keeps ids and classes apart wherever they appear,
    DevTools included. That is what tells two builds apart when Firefox moves
    a name from an id to a class: in the older build the name is still there,
    just as the other kind.
    """
    ids, classes, properties = set(), set(), set()
    for pack in paths:
        for name, content in read_entries(pack):
            if not name.endswith(TEXT_SUFFIXES):
                continue
            text = content.decode("utf-8", "replace")
            properties.update(PROPERTY_NAME.findall(text))
            ids.update(ID_ATTR.findall(text))
            for value in CLASS_ATTR.findall(text):
                classes.update(NAME.findall(value))
            if name.endswith(".css"):
                selectors = [CSS_NOISE.sub(" ", text)]
            elif name.endswith(SCRIPT_SUFFIXES):
                selectors = [m.group(2) for m in JS_STRING.finditer(text)]
                ids.update(JS_ID.findall(text))
                for args in JS_CLASS_CALL.findall(text):
                    for _, value in JS_STRING.findall(args):
                        classes.update(NAME.findall(value))
                for kind, value in JS_SET_ATTR.findall(text) + JS_ASSIGN.findall(text):
                    (ids if kind == "id" else classes).update(NAME.findall(value))
            else:
                selectors = []
            for chunk in selectors:
                for kind, value in SELECTOR_NAME.findall(chunk):
                    (ids if kind == "#" else classes).add(value)
    return {"ids": ids, "classes": classes, "properties": properties}


def _is_near_miss_property(name, candidate):
    """Property names version differently from element names: whole words get
    appended (`--panel-background` became `--panel-background-color`), so the
    suffix allowance is wider than the two characters element renames get."""
    shorter, longer = sorted((name, candidate), key=len)
    if longer.startswith(shorter) and len(longer) - len(shorter) <= 8:
        return True
    matcher = difflib.SequenceMatcher(None, name, candidate, autojunk=False)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    return (len(name) - matched) + (len(candidate) - matched) <= 3


def _words(name):
    return name.lstrip("-").split("-")


def _is_reworded_property(name, candidate):
    """The same words, reordered and at most one added, under the same lead.

    Firefox's design tokens renamed by moving and appending words:
    `--toolbarbutton-inner-padding` became `--toolbarbutton-padding-inner`,
    `--toolbarbutton-hover-background` became
    `--toolbarbutton-background-color-hover`. Character distance calls both
    pairs strangers. The leading word names the component and survives such
    renames, which keeps a theme's `--gnome-toolbar-color` off Firefox's
    `--toolbar-color`; a dropped word is refused because it changes meaning
    (`--toolbarbutton-icon-fill-opacity` is not `--toolbarbutton-icon-fill`).
    """
    old, new = _words(name), _words(candidate)
    if name == candidate or old[0] != new[0]:
        return False
    old, new = Counter(old), Counter(new)
    return not old - new and sum((new - old).values()) <= 1


def _reworded(name, pool):
    matches = [c for c in pool if _is_reworded_property(name, c)]
    # A pure reorder before an added word, then the closest spelling.
    matches.sort(key=lambda c: (
        len(_words(c)),
        -difflib.SequenceMatcher(None, name, c, autojunk=False).ratio(), c))
    return matches[0] if matches else None


def _pool(pack, key, devtools):
    pool = set(pack[key])
    if devtools:
        pool |= set(pack.get(f"devtools_{key}", ()))
    return sorted(pool)


def suggest_property(name, pack, devtools=False):
    """The consumed property this dead one was probably renamed to, if any.

    `devtools` adds DevTools' readers to the pool, for a name the theme sets
    where DevTools documents can see it.
    """
    pool = _pool(pack, "consumed", devtools)
    close = [c for c in difflib.get_close_matches(name, pool, n=4, cutoff=0.80)
             if _is_near_miss_property(name, c)]
    return close[0] if close else _reworded(name, pool)


def suggest_provided(name, pack, devtools=False):
    """The declared property this undefined one was probably renamed to.

    Rewordings only. A theme reading a name Firefox stopped providing has
    almost always met a rename, and character distance there mostly finds
    strangers: `--urlbar-width` is three letters from `--bar-width`.
    """
    return _reworded(name, _pool(pack, "declared", devtools))
