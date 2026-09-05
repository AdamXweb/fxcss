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

MOZ_DOCUMENT = re.compile(r"@-moz-document\b", re.IGNORECASE)
DOC_FUNCTION = re.compile(r"\b(url|url-prefix|domain|regexp)\(([^)]*)\)",
                          re.IGNORECASE)

# The only document the live audit ever opens. Every state in collect_dom is a
# browser window, so this is the whole of what the collected DOM describes.
AUDITED_DOCUMENT = "chrome://browser/content/browser.xhtml"


def _document_functions(line):
    """The (kind, value) pairs of an @-moz-document condition on this line."""
    return [(m.group(1).lower(), m.group(2).strip().strip("\"'"))
            for m in DOC_FUNCTION.finditer(line)]


def _scope_covers(functions, document=AUDITED_DOCUMENT):
    """Can rules under this @-moz-document condition apply to the audited window?

    Unknown condition types count as covering. Being wrong in that direction
    reports a finding that needs a human; being wrong in the other hides a real
    one, which is the failure this whole tool exists to prevent.
    """
    for kind, value in functions:
        if kind == "url" and value == document:
            return True
        if kind == "url-prefix" and document.startswith(value):
            return True
        if kind in ("domain", "regexp"):
            return True
    return False


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

        # Brace depth and the @-moz-document blocks open at each depth, so a
        # rule scoped to a document the live audit never opens can be told
        # apart from a global one. Counted on `cleaned`, where strings and
        # url() bodies are already neutralised, so a brace inside either cannot
        # shift the depth.
        depth = 0
        scope_stack = []
        prelude = None

        for index, scanned in enumerate(blanked.splitlines()):
            cleaned = STRINGS.sub("''", URLS.sub("url()", scanned))

            # Read the condition off `scanned`, where url() bodies survive.
            # It may run past the end of the line -- a long list of url()s is
            # usually wrapped -- so accumulate until the `{` that opens the
            # block. Evaluating a half-read condition finds no functions at
            # all, which would score as "excludes the audited window" and
            # silently hide every finding inside.
            if prelude is not None:
                prelude += " " + scanned
            elif MOZ_DOCUMENT.search(scanned):
                prelude = scanned

            for _ in range(cleaned.count("{")):
                if prelude is not None:
                    scope_stack.append(
                        (depth, _scope_covers(_document_functions(prelude))))
                    prelude = None
                depth += 1

            # One enclosing block that excludes the audited window is enough:
            # the rule can never apply there, whatever the outer blocks say.
            scoped_out = any(not covers for _, covers in scope_stack)

            # Skip declaration-only lines so a property value cannot be
            # mistaken for a selector -- but only for token extraction. The
            # brace accounting still has to run below, or a `}` sharing a line
            # with a declaration would leave the depth permanently wrong.
            declaration_only = ("{" not in cleaned
                                and ";" in cleaned and ":" in cleaned)

            if not declaration_only:
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
                        "scoped_out": scoped_out,
                    })

            for _ in range(cleaned.count("}")):
                depth = max(0, depth - 1)
                if scope_stack and scope_stack[-1][0] == depth:
                    scope_stack.pop()
    return found


def css_references(repo: Path, selector: str):
    """Find where the theme styles this selector.

    Matches on the id or class token rather than the literal selector string,
    since a rule is far more likely to read `#urlbar[focused]` or
    `#nav-bar > .foo` than to repeat the selector verbatim.
    """
    token = selector.lstrip(".#[").split("[")[0].split(">")[0].strip()
    if not token:
        return []
    pattern = re.compile(r"(?<![\w-])[.#]?" + re.escape(token) + r"(?![\w-])")
    hits = []
    for path in sorted((repo / "chrome").rglob("*.css")):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for n, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("/*"):
                continue
            if pattern.search(line):
                hits.append({
                    "file": str(path.relative_to(repo)),
                    "line": n,
                    "text": stripped[:160],
                })
    return hits



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


def _markup_home(pack, kind, name):
    """Which shipped document carries this id or class, if the pack knows.

    A near-miss suggestion compares spellings, so it can land on a name
    belonging to an entirely different document. The pack already records
    where every name lives; surfacing it is what lets a reader see that in one
    line rather than going to searchfox to find out.
    """
    if not pack:
        return None
    where = (pack["ids"] if kind == "#" else pack["classes"]).get(name)
    return where.rsplit("/", 1)[-1] if where else None


def suggest(token, dom, pack=None, scoped_out=False):
    """Infer a replacement for a token that matches nothing."""
    kind, name = token[0], token[1:]
    ids, classes = dom["ids"], dom["classes"]

    # Every rule using this token is scoped by @-moz-document to a document the
    # live audit never opens, so the running window's DOM is not evidence about
    # it either way -- and comparing against it invents renames between
    # unrelated documents. WhiteSur's `#placesToolbar` is the case that found
    # this: the Library window's toolbar in places.xhtml, which browser.xhtml's
    # `#PlacesToolbar` merely resembles by a single letter of case.
    #
    # The shipped chrome still is evidence: omni.ja carries those documents.
    if scoped_out:
        if pack:
            source = (pack["ids"] if kind == "#" else pack["classes"]).get(name)
            if source:
                return {"replacement": None, "confidence": "offscreen",
                        "reason": f"not in any live state, but shipped in {source}"}
        return {"replacement": None, "confidence": "other-document",
                "reason": "@-moz-document scopes this to a document the audit "
                          "does not open"}

    # An id that is now a class, or the reverse. Exact and by far the commonest.
    if kind == "#" and name in classes:
        return {"replacement": "." + name, "confidence": "renamed",
                "reason": "same name, now a class rather than an id"}
    if kind == "." and name in ids:
        return {"replacement": "#" + name, "confidence": "renamed",
                "reason": "same name, now an id rather than a class"}

    # Not in any state the live audit produced -- but the shipped chrome may
    # still carry it, in a document this tool cannot open (the window-modal
    # dialog, other platforms' markup, DevTools). That is a healthy selector,
    # not a suspicious one, and saying where it lives proves it.
    #
    # This has to outrank the fuzzy matchers below. They compare names, not
    # meanings, so a token that Firefox ships under its own spelling can still
    # look like a typo for some unrelated live element. WhiteSur's
    # `#placesToolbar` is the case that found this: it is the Library window's
    # toolbar in places.xhtml, but the live audit never opens that window, so
    # the near-miss branch offered `#PlacesToolbar` -- the main window's
    # bookmarks toolbar, a different element -- and --patch called it
    # confident. Firefox shipping the exact name is the stronger evidence.
    if pack:
        source = (pack["ids"] if kind == "#" else pack["classes"]).get(name)
        if source:
            return {"replacement": None, "confidence": "offscreen",
                    "reason": f"not in any live state, but shipped in {source}"}

    # A near-miss in the same namespace: usually a typo, or a suffix change.
    pool = sorted(ids if kind == "#" else classes)
    close = [c for c in difflib.get_close_matches(name, pool, n=4, cutoff=0.80)
             if _is_near_miss(name, c)][:1]
    if close:
        return {"replacement": kind + close[0], "confidence": "similar",
                "reason": f"no exact match; closest live name is {kind}{close[0]}",
                "replacement_home": _markup_home(pack, kind, close[0])}

    # Same name in the other namespace but only as a near-miss.
    other = sorted(classes if kind == "#" else ids)
    close = [c for c in difflib.get_close_matches(name, other, n=4, cutoff=0.80)
             if _is_near_miss(name, c)][:1]
    if close:
        flip = "." if kind == "#" else "#"
        return {"replacement": flip + close[0], "confidence": "similar",
                "reason": f"closest live name is {flip}{close[0]}",
                "replacement_home": _markup_home(pack, flip, close[0])}

    return {"replacement": None, "confidence": "unresolved",
            "reason": "no similar element found in any state fxcss could produce"}


def audit(session, theme: Path, verbose=True, pack=None):
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
        # Grouped by scope, not merged into one verdict per token. A token
        # can be written both globally and inside an @-moz-document block, and
        # the two uses are not the same claim: --patch rewrites the exact
        # file:line sites a finding carries, so one shared verdict would let a
        # confident rename reach the sites this audit just admitted it cannot
        # judge -- the very failure the scope check exists to prevent.
        for scoped_out in sorted({bool(use.get("scoped_out")) for use in uses}):
            group = [use for use in uses
                     if bool(use.get("scoped_out")) is scoped_out]
            info = suggest(token, dom, pack=pack, scoped_out=scoped_out)
            info.update({"token": token, "uses": group})
            findings.append(info)

    order = {"renamed": 0, "similar": 1, "offscreen": 2, "other-document": 3,
             "unresolved": 4}
    # First use as a tiebreak: two findings can now share a token, and the
    # report and the patch must not depend on dict ordering.
    findings.sort(key=lambda f: (order[f["confidence"]], f["token"],
                                 f["uses"][0]["file"] if f["uses"] else "",
                                 f["uses"][0]["line"] if f["uses"] else 0))
    result = {"tokens": len(tokens), "live": len(live), "findings": findings}
    _mark_duplicate_patches(result, theme)
    return result


def replace_in_line(text, token, replacement):
    """Swap one id/class token in a line, leaving the rest of the rule alone."""
    pattern = re.compile(r"(?<![\w-])" + re.escape(token) + r"(?![\w-])")
    return pattern.sub(replacement, text)


# Strings, comments and escapes must be consumed before structural punctuation:
# commas inside :is(), attribute values or comments do not separate selectors.
CSS_PARTS = re.compile(r'''/\*[\s\S]*?\*/|"(?:\\[\s\S]|[^"\\])*"|'(?:\\[\s\S]|[^'\\])*'|\\[\s\S]|[{};,\[\]()]''')
CSS_SPACE = re.compile(r'''"(?:\\[\s\S]|[^"\\])*"|'(?:\\[\s\S]|[^'\\])*'|\s+''')


def _selector_key(selector):
    # Preserve whitespace inside attribute strings. Only formatting outside
    # strings can be collapsed when comparing two selectors.
    selector = CSS_PARTS.sub(
        lambda m: "" if m[0].startswith("/*") else m[0], selector)
    return CSS_SPACE.sub(lambda m: " " if m[0].isspace() else m[0], selector).strip()


def _selector_lists(source):
    """Yield source spans for the comma-separated selectors before each rule."""
    start, commas, depth = 0, [], 0
    for match in CSS_PARTS.finditer(source):
        part = match[0]
        if part in ("(", "["):
            depth += 1
        elif part in (")", "]"):
            depth = max(0, depth - 1)
        elif depth == 0 and part == ",":
            commas.append(match.start())
        elif depth == 0 and part in ("{", "}", ";"):
            if part == "{" and commas:
                prelude = _selector_key(source[start:match.start()])
                if prelude and not prelude.startswith("@"):
                    starts = [start] + [comma + 1 for comma in commas]
                    ends = commas + [match.start()]
                    yield list(zip(starts, ends))
            start, commas = match.end(), []


def _mark_duplicate_patches(result, theme):
    """Keep exact diagnoses but leave duplicate-producing edits for review.

    Work per source occurrence: a duplicate in one rule must not prevent the
    same rename being safely patched in a different rule or file.
    """
    sources = {}
    for finding in result["findings"]:
        if finding["confidence"] != "renamed":
            continue
        by_file = {}
        for use in finding["uses"]:
            use.pop("patch_note", None)
            by_file.setdefault(use["file"], []).append(use)
        for rel, uses in by_file.items():
            if rel not in sources:
                source = (theme / rel).read_text(encoding="utf-8", errors="replace")
                sources[rel] = (source, list(_selector_lists(source)))
            source, groups = sources[rel]
            for spans in groups:
                keys = [_selector_key(source[start:end]) for start, end in spans]
                for index, (start, end) in enumerate(spans):
                    first_line = source.count("\n", 0, start) + 1
                    last_line = source.count("\n", 0, end) + 1
                    affected = [use for use in uses if first_line <= use["line"] <= last_line]
                    if not affected:
                        continue
                    lines = source[start:end].splitlines(keepends=True)
                    for use in affected:
                        offset = use["line"] - first_line
                        if offset < len(lines):
                            lines[offset] = replace_in_line(
                                lines[offset], finding["token"], finding["replacement"])
                    candidate = _selector_key("".join(lines))
                    if candidate != keys[index] and candidate in keys[:index] + keys[index + 1:]:
                        for use in affected:
                            use["patch_note"] = (
                                "not patched: the replacement would duplicate another "
                                "selector in this rule; remove the redundant selector manually")


BOLD, DIM, RED, GREEN, YELLOW, RESET = (
    "\033[1m", "\033[2m", "\033[31m", "\033[32m", "\033[33m", "\033[0m")


def actionable(findings):
    """The findings that are a theme's problem to fix.

    `offscreen` and `unresolved` are explicitly not: the first means Firefox
    still ships the name in a document this tool cannot open, and the second
    means we could not tell. Naming the two that count in one place keeps the
    callers from drifting -- `fxcss upgrade --audit` had grown the inverse
    test (`!= "unresolved"`), so a healthy offscreen name blocked an upgrade.
    That was latent until the pack started resolving more names into the
    offscreen tier.
    """
    return [f for f in findings if f["confidence"] in ("renamed", "similar")]


def report(result, show_all=False, colour=True):
    def c(code, s):
        return f"{code}{s}{RESET}" if colour else s

    problems = actionable(result["findings"])
    offscreen = [f for f in result["findings"] if f["confidence"] == "offscreen"]
    elsewhere = [f for f in result["findings"]
                 if f["confidence"] == "other-document"]
    unresolved = [f for f in result["findings"] if f["confidence"] == "unresolved"]

    print()
    if not problems:
        print("  No selectors need attention: every id and class the theme uses "
              "was found\n  in the running Firefox.")
    else:
        one = len(problems) == 1
        print(c(BOLD, f"  {len(problems)} selector{'' if one else 's'}"
                     f" {'needs' if one else 'need'} attention"))

    # RENAMED and SIMILAR used to render identically -- same before/after diff,
    # separated only by a colour that --no-colour strips. In a CI pull request
    # body that made a guess look exactly like the change being proposed, and
    # WhiteSur's `#placesToolbar` suggestion (the Library window's toolbar,
    # "corrected" to the browser window's) got advertised that way three times
    # over. Only the exact matches are written as a diff now.
    for finding in problems:
        exact = finding["confidence"] == "renamed"
        print()
        if exact:
            print(f"  {c(GREEN, 'RENAMED')}  {c(BOLD, finding['token'])}"
                  f"  →  {c(BOLD, finding['replacement'])}")
            print(f"           {c(DIM, finding['reason'])}")
        else:
            print(f"  {c(YELLOW, 'SIMILAR')}  {c(BOLD, finding['token'])}"
                  f"  →  {c(BOLD, finding['replacement'])}"
                  f"  {c(YELLOW, '(a guess, not applied)')}")
            print(f"           {c(DIM, finding['reason'])}")
            home = finding.get("replacement_home")
            if home:
                print(f"           {c(DIM, finding['replacement'] + ' belongs to ' + home)}")
            print(f"           {c(DIM, 'check what reads that name before using it -- a close')}")
            print(f"           {c(DIM, 'spelling can belong to an unrelated element')}")

        for use in finding["uses"][:3]:
            print()
            print(f"    {c(DIM, use['file'] + ':' + str(use['line']))}")
            if exact and use.get("patch_note"):
                print(f"      {use['text'].strip()}")
                print(f"      {c(YELLOW, use['patch_note'])}")
            elif exact:
                after = replace_in_line(use["text"], finding["token"],
                                        finding["replacement"])
                print(f"    {c(RED, '- ' + use['text'].strip())}")
                print(f"    {c(GREEN, '+ ' + after.strip())}")
            else:
                # No +/- : a suggestion should not arrive pre-formatted as a
                # patch someone can paste without reading it.
                print(f"      {use['text'].strip()}")
        extra = len(finding["uses"]) - 3
        if extra > 0:
            plural = "s" if extra != 1 else ""
            print()
            print(f"    {c(DIM, f'… and {extra} more occurrence{plural}')}")

    if offscreen:
        print()
        print(f"  {c(DIM, f'{len(offscreen)} token(s) live in states this audit could not open — the')}")
        print(f"  {c(DIM, 'shipped chrome still carries them (dialogs, other platforms), so they')}")
        print(f"  {c(DIM, 'are healthy and not counted above.')}")
        if show_all:
            for finding in offscreen:
                first = finding["uses"][0]
                print(f"    {finding['token']:<44} {c(DIM, finding['reason'])}")

    if elsewhere:
        print()
        print(f"  {c(DIM, f'{len(elsewhere)} token(s) belong to another document — an @-moz-document')}")
        print(f"  {c(DIM, 'block scopes them to a page this audit does not open, so the window')}")
        print(f"  {c(DIM, 'it does open cannot judge them and they are not counted above.')}")
        if show_all:
            for finding in elsewhere:
                first = finding["uses"][0]
                print(f"    {finding['token']:<44} {c(DIM, first['file'] + ':' + str(first['line']))}")

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
    _mark_duplicate_patches(result, theme)
    edits = {}
    for finding in result["findings"]:
        if finding["confidence"] != "renamed":
            continue
        for use in finding["uses"]:
            if use.get("patch_note"):
                continue
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


# --- unused code -----------------------------------------------------------

IMPORT_RE = re.compile(r"""@import\s+(?:url\(\s*)?["']?([^"')]+)["']?\s*\)?\s*;""")
PROP_DEF = re.compile(r"(--[\w-]+)\s*:")
PROP_USE = re.compile(r"var\(\s*(--[\w-]+)")

# Entry points Firefox loads by name. Anything not reachable from one of these
# is only reachable if something imports it.
ENTRY_SHEETS = ("userChrome.css", "userContent.css")

# Directories of deliberately opt-in sheets. A theme ships these expecting its
# installer (or the user) to enable them, so "nothing imports it" is the
# intended state rather than a finding.
OPTIONAL_DIRS = {"custom", "optional", "options", "extras", "variants"}


def import_graph(theme: Path):
    """Every stylesheet reachable by following @import from the entry sheets."""
    chrome = theme / "chrome"
    reachable, queue = set(), []
    for entry in ENTRY_SHEETS:
        path = chrome / entry
        if path.exists():
            queue.append(path.resolve())

    while queue:
        current = queue.pop()
        if current in reachable or not current.exists():
            continue
        reachable.add(current)
        text = COMMENT.sub("", current.read_text(encoding="utf-8", errors="replace"))
        for target in IMPORT_RE.findall(text):
            target = target.strip()
            if re.match(r"^(chrome:|resource:|https?:|data:)", target):
                continue
            queue.append((current.parent / target).resolve())
    return reachable


def custom_properties(paths):
    """Where each custom property is defined and where it is used.

    A declaration whose raw line carries an `fxcss-keep` comment is recorded
    in the third return value: the theme author is saying "I know this looks
    dead here, keep it" — the usual reason being an older Firefox (an ESR)
    that still reads the name even though the audited build does not.
    """
    defined, used, kept = {}, {}, set()
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        blanked = COMMENT.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)
        raw_lines = text.splitlines()
        for number, line in enumerate(blanked.splitlines(), 1):
            names = PROP_DEF.findall(line)
            for name in names:
                defined.setdefault(name, []).append((path, number))
            if names and "fxcss-keep" in raw_lines[number - 1]:
                kept.update(names)
            for name in PROP_USE.findall(line):
                used.setdefault(name, []).append((path, number))
    return defined, used, kept


PROBE_PROPERTIES = """
const [names] = arguments;
const win = Services.wm.getMostRecentWindow("navigator:browser");
const doc = win.document;
const remaining = new Set(names);
const found = [];
// Custom properties can be declared on any element, not only :root, so probe
// the whole document. The remaining set shrinks as names are resolved, so this
// stays cheap in practice.
for (const el of [doc.documentElement, ...doc.querySelectorAll("*")]) {
  if (!remaining.size) { break; }
  const cs = win.getComputedStyle(el);
  for (const name of [...remaining]) {
    if (cs.getPropertyValue(name).trim() !== "") {
      found.push(name);
      remaining.delete(name);
    }
  }
}
return found;
"""


def probe_properties(session, names):
    """Which of these custom properties does this browser resolve to a value?"""
    if not names:
        return set()
    return set(session.m.script(PROBE_PROPERTIES, [sorted(names)]))


def collect_unused(theme: Path):
    """The parts that need no browser: reachability and property bookkeeping."""
    chrome = theme / "chrome"
    if not chrome.is_dir():
        return None

    reachable = import_graph(theme)
    orphans, optional = [], []
    for sheet in sorted(chrome.rglob("*.css")):
        if sheet.resolve() in reachable:
            continue
        if OPTIONAL_DIRS & {p.name for p in sheet.relative_to(chrome).parents}:
            optional.append(sheet)
        else:
            orphans.append(sheet)

    defined, used, kept = custom_properties(sorted(reachable))
    return {
        "theme": theme,
        "reachable": len(reachable),
        "orphans": [p.relative_to(theme) for p in orphans],
        "optional": [p.relative_to(theme) for p in optional],
        "defined": defined,
        "used": used,
        "kept": kept,
    }


def classify_unused(static, firefox_knows, pack=None):
    """Separate real dead code from names Firefox itself reads or provides.

    Both directions need Firefox's opinion. Without `pack` that opinion comes
    from probing an *unthemed* browser — whether each name resolves to a value:

    * A property used but not defined here may still be one Firefox provides --
      `--toolbarbutton-inner-padding` is Firefox's, not the theme's.
    * A property defined here but never read here is usually the whole point:
      setting `--arrowpanel-background` exists precisely so Firefox's own rules
      pick it up. Only a name Firefox has never heard of is dead.

    With `pack` (an omni.scan of the shipped chrome) the second direction
    sharpens from *declared* to *consumed*: a name Firefox still declares but
    no longer reads is a stale override -- setting it changes nothing, which a
    resolution probe can never tell apart from a working one. That is exactly
    how `--in-content-page-background` died: it kept resolving on ESR-era
    guides while current Firefox had dropped every consumer.
    """
    if static is None:
        return None
    theme = static["theme"]
    defined, used = static["defined"], static["used"]
    kept = static.get("kept", set())
    defined_only = set(defined) - set(used)

    known = set(firefox_knows)
    if pack:
        known |= pack["declared"] | set(pack["consumed"])
        consumed = set(pack["consumed"])
        overrides = sorted(defined_only & consumed)
        stale = sorted(name for name in (defined_only & known) - consumed
                       if name not in kept)
    else:
        overrides = sorted(defined_only & known)
        stale = []

    missing = [name for name in sorted(set(used) - set(defined))
               if name not in known]
    dead = [name for name in sorted(defined_only)
            if name not in known and name not in kept]

    def _where(name):
        return {"name": name,
                "file": str(defined[name][0][0].relative_to(theme)),
                "line": defined[name][0][1]}

    unused_properties = []
    for name in dead:
        item = _where(name)
        if pack:
            from . import omni
            item["suggestion"] = omni.suggest_property(name, pack)
        unused_properties.append(item)

    return {
        "reachable": static["reachable"],
        "orphans": static["orphans"],
        "optional": static["optional"],
        "overrides": len(overrides),
        "kept": sorted(kept & defined_only),
        "packed": pack is not None,
        "stale_overrides": [_where(name) for name in stale],
        "unused_properties": unused_properties,
        "missing_properties": [
            {"name": name,
             "file": str(used[name][0][0].relative_to(theme)),
             "line": used[name][0][1],
             "uses": len(used[name])}
            for name in missing],
    }


def report_unused(unused, colour=True, show_all=False):
    def c(code, s):
        return f"{code}{s}{RESET}" if colour else s

    if not unused:
        return
    total = (len(unused["orphans"]) + len(unused["unused_properties"])
             + len(unused.get("stale_overrides", []))
             + len(unused["missing_properties"]))
    print(c(BOLD, "  Unused and unreachable"))
    print(f"  {c(DIM, 'Housekeeping, not breakage — nothing here stops the theme working.')}")
    print()

    if unused["orphans"]:
        print(f"  {c(YELLOW, 'NOT IMPORTED')}  {len(unused['orphans'])} stylesheet"
              f"{'s' if len(unused['orphans']) != 1 else ''} nothing reaches")
        for path in unused["orphans"][:12]:
            print(f"    {path}")
        extra = len(unused["orphans"]) - 12
        if extra > 0:
            print("    " + c(DIM, f"… and {extra} more"))
        print()

    if unused["missing_properties"]:
        print(f"  {c(RED, 'UNDEFINED')}     {len(unused['missing_properties'])} custom "
              f"propert{'ies' if len(unused['missing_properties']) != 1 else 'y'} used "
              f"but never set")
        print(f"  {c(DIM, 'Firefox does not provide these either, so the var() falls back or fails.')}")
        for item in unused["missing_properties"][:10]:
            where = f"{item['file']}:{item['line']}"
            print(f"    {item['name']:<44} {c(DIM, where)} {c(DIM, '×' + str(item['uses']))}")
        print()

    if unused.get("stale_overrides"):
        count = len(unused["stale_overrides"])
        print(f"  {c(YELLOW, 'SET, NEVER READ')} {count} custom "
              f"propert{'ies' if count != 1 else 'y'} this Firefox declares "
              f"but no longer reads")
        print("  " + c(DIM, "Overriding these changes nothing: the shipped chrome carries the"))
        print("  " + c(DIM, "declaration but not one var() or script that consumes it. Mark a"))
        print("  " + c(DIM, "line `/* fxcss-keep */` if an older Firefox you support still reads it."))
        limit = None if show_all else 10
        for item in unused["stale_overrides"][:limit]:
            print(f"    {item['name']:<44} {c(DIM, item['file'] + ':' + str(item['line']))}")
        if limit and len(unused["stale_overrides"]) > limit:
            print(f"    {c(DIM, 'Pass --all to list them.')}")
        print()

    if unused.get("overrides"):
        count = unused["overrides"]
        noun = "property is" if count == 1 else "properties are"
        print("  " + c(DIM, f"{count} more {noun} set here but read only by Firefox —"))
        print("  " + c(DIM, "deliberate overrides, not dead code."))
        print()

    if unused.get("kept"):
        count = len(unused["kept"])
        noun = "property" if count == 1 else "properties"
        print("  " + c(DIM, f"{count} {noun} marked fxcss-keep — "
                           "excluded from the findings above."))
        print()

    if unused["unused_properties"]:
        print(f"  {c(DIM, 'DEFINED ONLY')}  {len(unused['unused_properties'])} custom "
              f"propert{'ies' if len(unused['unused_properties']) != 1 else 'y'} set here "
              f"and read nowhere in the theme")
        if unused.get("packed"):
            print("  " + c(DIM, "This Firefox's shipped chrome neither declares nor reads these"))
            print("  " + c(DIM, "names, so overriding them does nothing on this build."))
        else:
            print("  " + c(DIM, "An unthemed Firefox does not resolve these names either, so they are"))
            print("  " + c(DIM, "likely renamed or dropped. Worth checking rather than deleting: a"))
            print("  " + c(DIM, "name Firefox references without setting would look the same here."))
        limit = None if show_all else 10
        for item in unused["unused_properties"][:limit]:
            print(f"    {item['name']:<44} {c(DIM, item['file'] + ':' + str(item['line']))}")
            if item.get("suggestion"):
                print(f"      {c(GREEN, '→ ' + item['suggestion'])}"
                      f" {c(DIM, 'is what this Firefox reads')}")
        if limit and len(unused["unused_properties"]) > limit:
            print(f"    {c(DIM, 'Pass --all to list them.')}")
        print()

    if total == 0:
        print(f"  {c(DIM, 'Nothing unused found.')}\n")


# --- snapshots -------------------------------------------------------------

def make_snapshot(session, verbose=False):
    """A record of every chrome name this Firefox has, plus its version.

    Committing one of these lets a scheduled job answer "what did the new
    Firefox change" without keeping an old browser around to compare against.
    """
    info = session.info()
    dom = collect_dom(session, verbose=verbose)
    return {
        "version": info["version"],
        "buildID": info["buildID"],
        "os": info["os"],
        "ids": sorted(dom["ids"]),
        "classes": sorted(dom["classes"]),
    }


def load_snapshot(path: Path):
    import json
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data, {"ids": set(data["ids"]), "classes": set(data["classes"])}
