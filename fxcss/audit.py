#!/usr/bin/env python3
"""Find selectors in a theme that no longer match anything, and suggest fixes.

Firefox renames and removes chrome elements between releases, and a rule that
targets a name which no longer exists fails silently -- the theme just stops
styling that part, with no error anywhere. This walks every id and class a theme
mentions, resolves each against a running Firefox, and reports the ones that
resolve to nothing, with a suggested replacement where one can be inferred.

Suggestions are derived from the live browser rather than a hardcoded list, so
they stay correct as Firefox changes:

  renamed     the same name exists, but as a class instead of an id (or the
              reverse). This is the common case and the suggestion is exact.
  similar     no exact counterpart, but a close name exists -- usually a typo
              in the theme, or a name that gained or lost a suffix.
  unresolved  nothing close. Reported separately and not counted as a problem,
              because it is usually an element that only appears in a state
              this tool cannot reach rather than one that has been removed.

`changelog` runs the same collection against two Firefox builds and diffs them,
which is how you find what a new release changed before it reaches users.
"""

import difflib
import re
import time
from pathlib import Path

COMMENT = re.compile(r"/\*.*?\*/", re.S)
URLS = re.compile(r"url\([^)]*\)")
STRINGS = re.compile(r"(['\"])(?:\\.|(?!\1).)*\1", re.S)
TOKEN = re.compile(r"(?<![\w-])([#.])([A-Za-z_][\w-]*)")
HEXCOLOR = re.compile(r"^[0-9a-fA-F]+$")

# Selectors that intentionally match nothing. `:not(#hack)` is a well-known
# trick for raising specificity without changing what a rule matches, so
# flagging it as a dead selector would be noise.
SPECIFICITY_HACKS = {"#hack", "#nope", "#never", "#no", "#none", "#fake"}


def _looks_like_colour(kind, name):
    return kind == "#" and len(name) in (3, 4, 6, 8) and HEXCOLOR.match(name)


def extract_tokens(theme: Path):
    """Map every id/class token in the theme to where it is written.

    Deliberately token-level rather than whole-selector: Firefox breaks themes
    by renaming individual ids and classes, and a whole selector containing
    `&` nesting or `::part()` cannot be handed to querySelectorAll anyway.
    """
    found = {}
    for path in sorted((theme / "chrome").rglob("*.css")):
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        # Blank comments across the whole file, not per line: a block comment
        # spans lines, and a selector named in prose is not a selector. Newlines
        # are preserved so reported line numbers still line up with the source.
        blanked = COMMENT.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), raw)
        source_lines = raw.splitlines()

        for index, scanned in enumerate(blanked.splitlines()):
            cleaned = STRINGS.sub("''", URLS.sub("url()", scanned))
            # Skip declaration-only lines so a property value cannot be mistaken
            # for a selector.
            if "{" not in cleaned and ";" in cleaned and ":" in cleaned:
                continue
            for kind, name in TOKEN.findall(cleaned):
                if _looks_like_colour(kind, name):
                    continue
                token = kind + name
                if token in SPECIFICITY_HACKS:
                    continue
                found.setdefault(token, []).append({
                    "file": str(path.relative_to(theme)),
                    "line": index + 1,
                    "text": source_lines[index].rstrip(),
                })
    return found


COLLECT_DOM = """
const win = Services.wm.getMostRecentWindow("navigator:browser");
const doc = win.document;
const ids = new Set(), classes = new Set();
for (const el of doc.querySelectorAll("*")) {
  if (el.id) ids.add(el.id);
  const c = el.getAttribute("class");
  if (c) { for (const x of c.trim().split(/\\s+/)) classes.add(x); }
}
return {ids: [...ids], classes: [...classes]};
"""

# Large parts of browser.xhtml are built lazily -- the app menu's contents do
# not exist as elements until the menu has been opened once. Collecting from a
# single resting state would report hundreds of live elements as missing.
OPEN_APPMENU = """
const win = Services.wm.getMostRecentWindow("navigator:browser");
try { win.PanelUI.show(); } catch (e) {}
return true;
"""

CLOSE_APPMENU = """
const win = Services.wm.getMostRecentWindow("navigator:browser");
try { win.PanelUI.hide(); } catch (e) {}
return true;
"""

OPEN_CONTEXT_MENUS = """
const win = Services.wm.getMostRecentWindow("navigator:browser");
const doc = win.document;
let opened = 0;
for (const id of ["tabContextMenu", "contentAreaContextMenu", "toolbar-context-menu"]) {
  const popup = doc.getElementById(id);
  if (!popup) { continue; }
  try {
    popup.openPopupAtScreen(win.screenX + 60, win.screenY + 120, false);
    popup.hidePopup();
    opened++;
  } catch (e) {}
}
return opened;
"""

URLBAR_RESULTS = """
const win = Services.wm.getMostRecentWindow("navigator:browser");
win.gURLBar.focus();
win.gURLBar.value = "a";
try { win.gURLBar.startQuery({searchString: "a", allowAutofill: false}); } catch (e) {}
return true;
"""


def collect_dom(session, verbose=True):
    """Union of every id and class present across the states we can produce."""
    from . import core

    ids, classes = set(), set()

    def sweep(label):
        result = session.m.script(COLLECT_DOM)
        ids.update(result["ids"])
        classes.update(result["classes"])
        if verbose:
            print(f"    {label:<22} {len(ids)} ids, {len(classes)} classes", flush=True)

    session.setup_window()
    time.sleep(2.0)
    sweep("resting")

    session.m.script(core.OPEN_FINDBAR)
    time.sleep(1.2)
    sweep("find bar open")

    session.m.script(URLBAR_RESULTS)
    time.sleep(1.5)
    sweep("address bar results")
    session.m.script(core.BLUR_URLBAR)

    session.m.script(OPEN_APPMENU)
    time.sleep(2.0)
    sweep("app menu opened")
    session.m.script(CLOSE_APPMENU)
    time.sleep(0.8)

    session.m.script(OPEN_CONTEXT_MENUS)
    time.sleep(1.5)
    sweep("context menus built")

    session.set_dark(True)
    time.sleep(1.5)
    sweep("dark mode")

    return {"ids": ids, "classes": classes}


def _differing_chars(a, b):
    """Characters that differ between two names, ignoring shared runs."""
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    return (len(a) - matched) + (len(b) - matched)


def _is_near_miss(token_name, candidate):
    """Is this close enough to be the same element under a new name?

    A plain similarity ratio is not enough. Chrome ids share long scaffolding
    (`appMenu-…-button`), so `appMenu-paste-button` and
    `appMenu-translate-button` score highly while being unrelated controls --
    suggesting one for the other would be worse than saying nothing.

    Two shapes are trustworthy: a short suffix appearing or disappearing, which
    is how Firefox versions its chrome (`…-button` became `…-button2`), and a
    difference of a character or two, which is a typo.
    """
    shorter, longer = sorted((token_name, candidate), key=len)
    if longer.startswith(shorter) and len(longer) - len(shorter) <= 2:
        return True
    return _differing_chars(token_name, candidate) <= 2


def suggest(token, dom):
    """Infer a replacement for a token that matches nothing."""
    kind, name = token[0], token[1:]
    ids, classes = dom["ids"], dom["classes"]

    # An id that is now a class, or the reverse. Exact and by far the commonest.
    if kind == "#" and name in classes:
        return {"replacement": "." + name, "confidence": "renamed",
                "reason": "same name, now a class rather than an id"}
    if kind == "." and name in ids:
        return {"replacement": "#" + name, "confidence": "renamed",
                "reason": "same name, now an id rather than a class"}

    # A near-miss in the same namespace: usually a typo, or a suffix change.
    pool = sorted(ids if kind == "#" else classes)
    close = [c for c in difflib.get_close_matches(name, pool, n=4, cutoff=0.80)
             if _is_near_miss(name, c)][:1]
    if close:
        return {"replacement": kind + close[0], "confidence": "similar",
                "reason": f"no exact match; closest live name is {kind}{close[0]}"}

    # Same name in the other namespace but only as a near-miss.
    other = sorted(classes if kind == "#" else ids)
    close = [c for c in difflib.get_close_matches(name, other, n=4, cutoff=0.80)
             if _is_near_miss(name, c)][:1]
    if close:
        flip = "." if kind == "#" else "#"
        return {"replacement": flip + close[0], "confidence": "similar",
                "reason": f"closest live name is {flip}{close[0]}"}

    return {"replacement": None, "confidence": "unresolved",
            "reason": "no similar element found in any state fxcss could produce"}


def audit(session, theme: Path, verbose=True):
    tokens = extract_tokens(theme)
    if verbose:
        print(f"  {len(tokens)} distinct id/class tokens in the theme", flush=True)
        print("  collecting live elements:", flush=True)
    dom = collect_dom(session, verbose=verbose)

    live = {"#" + i for i in dom["ids"]} | {"." + c for c in dom["classes"]}
    findings = []
    for token, uses in sorted(tokens.items()):
        if token in live:
            continue
        info = suggest(token, dom)
        info.update({"token": token, "uses": uses})
        findings.append(info)

    order = {"renamed": 0, "similar": 1, "unresolved": 2}
    findings.sort(key=lambda f: (order[f["confidence"]], f["token"]))
    return {"tokens": len(tokens), "live": len(live), "findings": findings}


def replace_in_line(text, token, replacement):
    """Swap one id/class token in a line, leaving the rest of the rule alone."""
    pattern = re.compile(r"(?<![\w-])" + re.escape(token) + r"(?![\w-])")
    return pattern.sub(replacement, text)


BOLD, DIM, RED, GREEN, YELLOW, RESET = (
    "\033[1m", "\033[2m", "\033[31m", "\033[32m", "\033[33m", "\033[0m")


def report(result, show_all=False, colour=True):
    def c(code, s):
        return f"{code}{s}{RESET}" if colour else s

    actionable = [f for f in result["findings"] if f["confidence"] != "unresolved"]
    unresolved = [f for f in result["findings"] if f["confidence"] == "unresolved"]

    print()
    if not actionable:
        print("  No selectors need attention: every id and class the theme uses "
              "was found\n  in the running Firefox.")
    else:
        print(c(BOLD, f"  {len(actionable)} selector"
                     f"{'s' if len(actionable) != 1 else ''} need attention"))

    for finding in actionable:
        label = "RENAMED" if finding["confidence"] == "renamed" else "SIMILAR"
        tint = GREEN if finding["confidence"] == "renamed" else YELLOW
        print()
        print(f"  {c(tint, label)}  {c(BOLD, finding['token'])}"
              f"  →  {c(BOLD, finding['replacement'])}")
        print(f"           {c(DIM, finding['reason'])}")
        for use in finding["uses"][:3]:
            after = replace_in_line(use["text"], finding["token"], finding["replacement"])
            print()
            print(f"    {c(DIM, use['file'] + ':' + str(use['line']))}")
            print(f"    {c(RED, '- ' + use['text'].strip())}")
            print(f"    {c(GREEN, '+ ' + after.strip())}")
        extra = len(finding["uses"]) - 3
        if extra > 0:
            plural = "s" if extra != 1 else ""
            print()
            print(f"    {c(DIM, f'… and {extra} more occurrence{plural}')}")

    if unresolved:
        print()
        print(f"  {c(DIM, f'{len(unresolved)} other token(s) were not seen in any state fxcss could')}")
        print(f"  {c(DIM, 'produce. That usually means a platform-specific or state-specific')}")
        print(f"  {c(DIM, 'element rather than a removed one, so they are not counted above.')}")
        if show_all:
            for finding in unresolved:
                first = finding["uses"][0]
                print(f"    {finding['token']:<44} {c(DIM, first['file'] + ':' + str(first['line']))}")
        else:
            print(f"  {c(DIM, 'Pass --all to list them.')}")
    print()


def write_patch(result, theme: Path, out: Path):
    """Emit a unified diff of the confident replacements, for review."""
    edits = {}
    for finding in result["findings"]:
        if finding["confidence"] != "renamed":
            continue
        for use in finding["uses"]:
            edits.setdefault(use["file"], []).append(
                (use["line"], finding["token"], finding["replacement"]))

    if not edits:
        return 0

    chunks = []
    for rel, changes in sorted(edits.items()):
        path = theme / rel
        original = path.read_text(encoding="utf-8", errors="replace").splitlines()
        updated = list(original)
        for line_no, token, replacement in changes:
            index = line_no - 1
            if 0 <= index < len(updated):
                updated[index] = replace_in_line(updated[index], token, replacement)
        chunks.extend(difflib.unified_diff(
            original, updated, fromfile=f"a/{rel}", tofile=f"b/{rel}", lineterm=""))

    out.write_text("\n".join(chunks) + "\n", encoding="utf-8")
    return len(edits)


def changelog(before, after, theme_tokens=None):
    """Diff two collected DOM snapshots."""
    gone_ids = sorted(before["ids"] - after["ids"])
    new_ids = sorted(after["ids"] - before["ids"])
    gone_classes = sorted(before["classes"] - after["classes"])
    new_classes = sorted(after["classes"] - before["classes"])

    result = {
        "removed": [f"#{i}" for i in gone_ids] + [f".{c}" for c in gone_classes],
        "added": [f"#{i}" for i in new_ids] + [f".{c}" for c in new_classes],
    }
    if theme_tokens is not None:
        result["affects_theme"] = sorted(set(result["removed"]) & set(theme_tokens))
    return result
