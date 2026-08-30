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
covers documents the live audit cannot reach (dialogs, DevTools, in-content
pages), and needs no second Firefox launch.
"""
import difflib
import re
import struct
import zlib
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

      consumed  {property name: first file that reads it}
      declared  {property name} declared in any shipped stylesheet
      ids       {id: first markup file carrying it}
      classes   {class: first markup file carrying it}
    """
    paths = pack_paths(firefox)
    if not paths:
        return None
    consumed, declared, ids, classes = {}, set(), {}, {}
    for pack in paths:
        label = Path(pack).name
        for name, content in read_entries(pack):
            where = f"{label}!{name}"
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
    return {"paths": paths, "consumed": consumed, "declared": declared,
            "ids": ids, "classes": classes}


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


def suggest_property(name, pack):
    """The consumed property this dead one was probably renamed to, if any."""
    pool = sorted(pack["consumed"])
    close = [c for c in difflib.get_close_matches(name, pool, n=4, cutoff=0.80)
             if _is_near_miss_property(name, c)]
    return close[0] if close else None
