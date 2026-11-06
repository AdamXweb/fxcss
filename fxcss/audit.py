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

from .style import plural as _pl
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

DEVTOOLS_DOCUMENTS = "chrome://devtools/"
DEVTOOLS_SAMPLE = DEVTOOLS_DOCUMENTS + "content/framework/toolbox.xhtml"


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


def _reaches_devtools(functions):
    """Can rules under this @-moz-document condition apply to DevTools?

    The rule for counting a DevTools reader of a theme's property: the
    declaration has to sit in an @-moz-document block that can match a
    chrome://devtools/ document. userChrome.css does load into the toolbox
    docked in a browser window, but not into the Browser Toolbox, which runs
    in its own process and profile; and an unscoped rule is written for the
    browser window -- WhiteSur set --toolbarbutton-hover-background on
    #nav-bar, which no DevTools document has. A theme that does restyle
    DevTools scopes it, as WhiteSur's own devtools.css does, and anything
    else that means to can say so with fxcss-keep.

    Unknown conditions count as not reaching, the opposite of _scope_covers
    and for the same reason: here that is the direction that reports.
    """
    for kind, value in functions:
        if kind == "url" and value.startswith(DEVTOOLS_DOCUMENTS):
            return True
        if kind == "url-prefix" and (value.startswith(DEVTOOLS_DOCUMENTS)
                                     or DEVTOOLS_DOCUMENTS.startswith(value)):
            return True
        if kind == "domain" and value == "devtools":
            return True
        if kind == "regexp":
            try:
                if "devtools" in value or re.fullmatch(value, DEVTOOLS_SAMPLE):
                    return True
            except re.error:
                pass
    return False


def _scoped_lines(blanked):
    """Yield (index, line, cleaned, scopes) for each line of a stylesheet.

    `blanked` has its comments blanked already. `cleaned` is the line with
    strings and url() bodies neutralised, safe for counting braces and
    finding selectors; `scopes` holds the function lists of the
    @-moz-document blocks open on that line, outermost first.
    """
    # Brace depth and the @-moz-document blocks open at each depth. Counted
    # on `cleaned`, so a brace inside a string or url() cannot shift it.
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
                scope_stack.append((depth, _document_functions(prelude)))
                prelude = None
            depth += 1

        yield index, scanned, cleaned, [functions for _, functions in scope_stack]

        # Closing braces after the caller has seen the line, so a `}` sharing
        # a line with a declaration still counts that declaration as inside.
        for _ in range(cleaned.count("}")):
            depth = max(0, depth - 1)
            if scope_stack and scope_stack[-1][0] == depth:
                scope_stack.pop()


def _looks_like_colour(kind, name):
    return kind == "#" and len(name) in (3, 4, 6, 8) and HEXCOLOR.match(name)


def extract_tokens(theme: Path):
    """Map every id/class token in the theme to where it is written.

    Deliberately token-level rather than whole-selector: Firefox breaks themes
    by renaming individual ids and classes, and a whole selector containing
    `&` nesting or `::part()` cannot be handed to querySelectorAll anyway.
    """
    from . import inventory
    found = {}
    chrome = theme / "chrome"
    # Sheets reached only from userContent.css style web and extension
    # pages; their selectors are never about the browser window, so they
    # are scoped out the way an @-moz-document block for another document
    # is, with the reason recorded so the report can say which it was.
    scanned = inventory.scan(chrome) if chrome.is_dir() else None
    for path in sorted(chrome.rglob("*.css")):
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        relative = path.relative_to(chrome).as_posix()
        content_only = bool(scanned) and relative in scanned["sheets"] \
            and scanned["sheets"][relative]["scope"] == ["content"]

        # Blank comments across the whole file, not per line: a block comment
        # spans lines, and a selector named in prose is not a selector. Newlines
        # are preserved so reported line numbers still line up with the source.
        blanked = COMMENT.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), raw)
        source_lines = raw.splitlines()

        # The @-moz-document blocks open on each line, so a rule scoped to a
        # document the live audit never opens can be told apart from a
        # global one.
        for index, _, cleaned, scopes in _scoped_lines(blanked):
            # One enclosing block that excludes the audited window is enough:
            # the rule can never apply there, whatever the outer blocks say.
            scoped_out = any(not _scope_covers(functions) for functions in scopes)

            # Skip declaration-only lines so a property value cannot be
            # mistaken for a selector -- but only for token extraction:
            # _scoped_lines still counts their braces, or a `}` sharing a
            # line with a declaration would leave the depth permanently wrong.
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
                        "scoped_out": scoped_out or content_only,
                        "document": "content" if content_only else "chrome",
                    })
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


def collect_dom(session, verbose=True, progress=None):
    """Union of every id and class present across the states we can produce.

    `progress(label, ids, classes)`, when given, hears about each state as it
    is swept instead of the verbose log line.
    """
    from . import core

    ids, classes = set(), set()

    def sweep(label):
        result = session.m.script(COLLECT_DOM)
        ids.update(result["ids"])
        classes.update(result["classes"])
        if progress:
            progress(label, len(ids), len(classes))
        elif verbose:
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


def suggest(token, dom, pack=None, scoped_out=False, document=None):
    """Infer a replacement for a token that matches nothing.

    `document` says why a use is scoped out: "content" for a rule that
    loads only through userContent.css, which styles web and extension
    pages and is never evidence about the browser window.
    """
    kind, name = token[0], token[1:]
    ids, classes = dom["ids"], dom["classes"]

    if scoped_out and document == "content":
        return {"replacement": None, "confidence": "other-document",
                "reason": "loaded only through userContent.css, which styles "
                          "web and extension pages, not the browser window"}

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


def audit(session, theme: Path, verbose=True, pack=None, progress=None):
    tokens = extract_tokens(theme)
    if verbose and not progress:
        print(f"  {len(tokens)} distinct id/class tokens in the theme", flush=True)
        print("  collecting live elements:", flush=True)
    dom = collect_dom(session, verbose=verbose, progress=progress)

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
        keys = sorted({(bool(use.get("scoped_out")), use.get("document") or "chrome")
                       for use in uses})
        for scoped_out, document in keys:
            group = [use for use in uses
                     if bool(use.get("scoped_out")) is scoped_out
                     and (use.get("document") or "chrome") == document]
            info = suggest(token, dom, pack=pack, scoped_out=scoped_out,
                           document=document)
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


# The status column every audit finding starts with: one width, and colour
# by what the finding costs the theme -- red breaks it, yellow wastes or
# guesses, dim is housekeeping. The word always carries the meaning alone.
STATUS_WIDTH = 16
BODY = 2 + STATUS_WIDTH


def _status(word, role, on):
    from . import style
    return "  " + style.status(word, role, width=STATUS_WIDTH, on=on)


def report(result, show_all=False, colour=True, headline=True, context=None):
    from . import style
    on = colour and style.colour_on()
    pad = " " * BODY

    problems = actionable(result["findings"])
    offscreen = [f for f in result["findings"] if f["confidence"] == "offscreen"]
    elsewhere = [f for f in result["findings"]
                 if f["confidence"] == "other-document"]
    unresolved = [f for f in result["findings"] if f["confidence"] == "unresolved"]

    if headline:
        print()
    if not headline:
        pass
    elif not problems:
        print(f"  {style.good(style.TICK, on=on)} "
              f"{style.strong('No selectors need attention', on=on)}  "
              f"{style.muted('every id and class the theme uses is in this Firefox', on=on)}")
    else:
        one = len(problems) == 1
        print(f"  {style.heading(_pl(len(problems), 'selector') + (' needs' if one else ' need') + ' attention', on=on)}")

    # RENAMED and SIMILAR used to render identically -- same before/after diff,
    # separated only by a colour that --no-colour strips. In a CI pull request
    # body that made a guess look exactly like the change being proposed, and
    # WhiteSur's `#placesToolbar` suggestion (the Library window's toolbar,
    # "corrected" to the browser window's) got advertised that way three times
    # over. Only the exact matches are written as a diff now.
    for finding in problems:
        exact = finding["confidence"] == "renamed"
        print()
        arrow = f" {style.ARROW} "
        if exact:
            print(_status("RENAMED", "bad", on)
                  + style.strong(finding["token"], on=on) + arrow
                  + style.strong(finding["replacement"], on=on))
            print(pad + style.muted(finding["reason"], on=on))
        else:
            print(_status("SIMILAR", "warn", on)
                  + style.strong(finding["token"], on=on) + arrow
                  + style.strong(finding["replacement"], on=on)
                  + "  " + style.warn("a guess, not applied", on=on))
            print(pad + style.muted(finding["reason"], on=on))
            home = finding.get("replacement_home")
            if home:
                print(pad + style.muted(finding["replacement"] + " belongs to " + home, on=on))
            print(pad + style.muted("check what reads that name before using it: a close", on=on))
            print(pad + style.muted("spelling can belong to an unrelated element", on=on))

        for i, use in enumerate(finding["uses"][:3]):
            if i:
                print()
            print(pad + style.muted(use["file"] + ":" + str(use["line"]), on=on))
            if exact and use.get("patch_note"):
                print(pad + use["text"].strip())
                print(pad + style.warn(use["patch_note"], on=on))
            elif exact:
                after = replace_in_line(use["text"], finding["token"],
                                        finding["replacement"])
                print(pad + style.bad("- " + use["text"].strip(), on=on))
                print(pad + style.good("+ " + after.strip(), on=on))
            else:
                # No +/- : a suggestion should not arrive pre-formatted as a
                # patch someone can paste without reading it.
                print(pad + "  " + use["text"].strip())
        extra = len(finding["uses"]) - 3
        if extra > 0:
            print(pad + style.muted(f"{style.ELLIPSIS} and {_pl(extra, 'more occurrence')}", on=on))

    groups = [
        (offscreen, "in states this audit could not open: dialogs, other platforms",
         "in states not opened", lambda f: f["reason"]),
        (elsewhere, "scoped by @-moz-document to pages this audit does not open",
         "in other documents", lambda f: f["uses"][0]["file"] + ":" + str(f["uses"][0]["line"])),
        (unresolved, "never seen in any state; usually platform- or state-specific",
         "never seen", lambda f: f["uses"][0]["file"] + ":" + str(f["uses"][0]["line"])),
    ]
    rows = [{"count": len(items), "text": text, "short": short, "kind": "token",
             "details": [(f["token"], where(f)) for f in items] if show_all else []}
            for items, text, short, where in groups if items]
    if context is not None:
        context.extend(rows)
        context.append({"hint": not show_all and bool(rows)})
    elif rows:
        print()
        print_context(rows, show_all, on)
    print()


_KINDS = (("token", "tokens out of reach"),
          ("file", "files left out as intended"),
          ("property", "properties skipped on purpose"))


def print_context(rows, show_all, on, hint=None):
    """What the audit left out, as one quiet sentence -- or, with --all, one
    right-aligned count and reason per part, each with its names."""
    from . import style
    rows = [r for r in rows if "count" in r]
    if not rows:
        return
    if not show_all:
        short = {"token": "out of reach", "file": "left out", "property": "skipped"}
        parts = []
        for kind, label in _KINDS:
            group = [r for r in rows if r.get("kind") == kind]
            if group:
                noun = label.split()[0]
                parts.append(f"{sum(r['count'] for r in group):,} {noun} {short[kind]}")
        parts += [f"{r['count']:,} {r['text']}" for r in rows
                  if r.get("kind") not in dict(_KINDS)]
        for line in style.rows([("not counted", f" {style.DOT} ".join(parts) + "  (--all explains)")],
                               indent=2, label_width=11, on=on):
            print(style.muted(line, on=on))
        return
    print(style.heading_line("Not counted", "healthy, out of reach or deliberate", on=on))
    width = max(len(f"{r['count']:,}") for r in rows)
    for row in rows:
        print(f"    {row['count']:>{width},}  {style.muted(row['text'], on=on)}")
        for token, where in row.get("details", []):
            print(f"    {' ' * width}    {token:<44} {style.muted(where, on=on)}")


def write_patch(result, theme: Path, out: Path, source_paths=None):
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
        target = source_paths[rel] if source_paths is not None else rel
        def header(prefix):
            name = f"{prefix}/{target}"
            # Git's quoted-path syntax is needed for control characters,
            # backslashes and literal double quotes in a repository filename.
            if any(c in name for c in '\t\n\r\\"'):
                import json
                return json.dumps(name, ensure_ascii=False)
            return name
        chunks.extend(difflib.unified_diff(
            original, updated, fromfile=header("a"), tofile=header("b"), lineterm=""))

    # Git patch syntax uses LF even when generated on Windows. Text-mode
    # newline translation would add carriage returns to every context line.
    out.write_bytes(("\n".join(chunks) + "\n").encode("utf-8"))
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
from .inventory import OPTION_DIRS as _OPTION_DIRS  # noqa: E402
OPTIONAL_DIRS = set(_OPTION_DIRS)


def import_graph(theme: Path):
    """Every stylesheet reachable by following @import from the entry sheets.

    Read through the shared inventory, so quoted and unquoted imports,
    escaped paths and conditional imports all count -- the same reading
    install and upgrade use. Returns resolved absolute paths.
    """
    from . import inventory
    chrome = theme / "chrome"
    if not chrome.is_dir():
        return set()
    found = inventory.scan(chrome)
    return {inventory._locate(chrome, key).resolve() for key in found["sheets"]}


def custom_properties(paths):
    """Where each custom property is defined and where it is used.

    A declaration whose raw line carries an `fxcss-keep` comment is recorded
    in the third return value: the theme author is saying "I know this looks
    dead here, keep it" — the usual reason being an older Firefox (an ESR)
    that still reads the name even though the audited build does not.

    The fourth holds every name set or read inside a rule scoped to DevTools
    documents (_reaches_devtools): the only names DevTools' own stylesheets
    can count as reading or providing.
    """
    defined, used, kept, devtools = {}, {}, set(), set()
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        blanked = COMMENT.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)
        raw_lines = text.splitlines()
        for index, line, _, scopes in _scoped_lines(blanked):
            number = index + 1
            names = PROP_DEF.findall(line)
            for name in names:
                defined.setdefault(name, []).append((path, number))
            if names and "fxcss-keep" in raw_lines[index]:
                kept.update(names)
            uses = PROP_USE.findall(line)
            for name in uses:
                used.setdefault(name, []).append((path, number))
            if scopes and all(_reaches_devtools(functions) for functions in scopes):
                devtools.update(names, uses)
    return defined, used, kept, devtools


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
    from . import inventory
    chrome = theme / "chrome"
    if not chrome.is_dir():
        return None

    found = inventory.scan(chrome)
    reachable = {(chrome / relative).resolve() for relative in found["sheets"]}
    orphans, optional = [], []
    for sheet in sorted(chrome.rglob("*.css")):
        if sheet.resolve() in reachable:
            continue
        if OPTIONAL_DIRS & {p.name for p in sheet.relative_to(chrome).parents}:
            optional.append(sheet)
        else:
            orphans.append(sheet)

    defined, used, kept, devtools = custom_properties(sorted(reachable))
    return {
        "theme": theme,
        "reachable": len(reachable),
        "orphans": [p.relative_to(theme) for p in orphans],
        "optional": [p.relative_to(theme) for p in optional],
        "defined": defined,
        "used": used,
        "kept": kept,
        "devtools": devtools,
        # What the inventory could and could not settle: an unresolved
        # required import or a missing entry means the picture is partial,
        # and the report says so instead of reading as clean.
        "analysis": {
            "complete": found["complete"],
            "reasons": list(found["reasons"]),
            "entries": list(found["entries"]),
            "unresolved": [f"{e['source']} → {e['target']}" for e in found["unresolved"]],
            "conditional": [f"{e['source']} → {e['target']}" for e in found["conditional"]],
            "hooks": sorted({Path(e["resolved"]).name for e in found["hooks"]}),
            "content_only": sorted(rel for rel, s in found["sheets"].items()
                                   if s["scope"] == ["content"]),
        },
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

    The pack's DevTools half counts only for names the theme sets or reads
    in a rule that can reach DevTools documents (_reaches_devtools). A name
    only DevTools still claims is reported on its own, even when the theme
    reads it too: the browser window provides no value for it, so the
    theme's reads only ever see its own, and its overrides override nothing.
    WhiteSur set --toolbarbutton-hover-background on #nav-bar and read it on
    #back-button for months after the toolbar moved to
    --toolbarbutton-background-color-hover, and only DevTools' reader of the
    old name stood between that and a finding.
    """
    if static is None:
        return None
    # import_graph resolves each source, including imports and chrome/ links
    # outside the theme. Resolve both sides to handle filesystem aliases (on
    # macOS, /var is /private/var), and keep external sources addressable.
    theme = static["theme"].resolve()
    defined, used = static["defined"], static["used"]
    kept = static.get("kept", set())
    scoped = static.get("devtools", set())
    defined_only = set(defined) - set(used)

    known = set(firefox_knows)
    devtools_only = set()
    if pack:
        consumed = set(pack["consumed"])
        known |= pack["declared"] | consumed
        devtools_reads = set(pack.get("devtools_consumed", ()))
        devtools_knows = pack.get("devtools_declared", set()) | devtools_reads
        consumed |= devtools_reads & scoped
        known |= devtools_knows & scoped
        devtools_only = devtools_knows - known
        overrides = sorted(defined_only & consumed)
        stale = sorted(name for name in (defined_only & known) - consumed
                       if name not in kept)
    else:
        overrides = sorted(defined_only & known)
        stale = []

    missing = [name for name in sorted(set(used) - set(defined))
               if name not in known]
    dead = [name for name in sorted(defined_only)
            if name not in known and name not in kept
            and name not in devtools_only]
    abandoned = [name for name in sorted(set(defined) & devtools_only)
                 if name not in kept]

    def _where(name, places=defined):
        path = places[name][0][0].resolve()
        try:
            location = path.relative_to(theme)
        except ValueError:
            location = path
        return {"name": name,
                "file": str(location),
                "line": places[name][0][1]}

    def _suggest(item, provided=False):
        if pack:
            from . import omni
            suggest = omni.suggest_provided if provided else omni.suggest_property
            item["suggestion"] = suggest(item["name"], pack,
                                         devtools=item["name"] in scoped)
        return item

    devtools_properties = []
    for name in abandoned:
        item = _suggest(_where(name))
        if name in used:
            item["read_here"] = _where(name, used)
        devtools_properties.append(item)

    missing_properties = []
    for name in missing:
        item = _suggest(_where(name, used), provided=True)
        item["uses"] = len(used[name])
        item["devtools"] = name in devtools_only
        missing_properties.append(item)

    return {
        "reachable": static["reachable"],
        "orphans": static["orphans"],
        "optional": static["optional"],
        "analysis": static.get("analysis") or {"complete": True, "reasons": []},
        "overrides": len(overrides),
        "kept": sorted(kept & (defined_only | devtools_only)),
        "packed": pack is not None,
        "stale_overrides": [_where(name) for name in stale],
        "devtools_only": devtools_properties,
        "unused_properties": [_suggest(_where(name)) for name in dead],
        "missing_properties": missing_properties,
    }


def _names_block(items, on, show_all, suggest_word, limit=10, width=None, quiet=False):
    """Property rows: the name, then where, in one column for the report."""
    from . import style
    pad = " " * BODY
    shown = items if show_all or limit is None else items[:limit]
    width = width or max(len(i["name"]) for i in items) + 2
    for item in shown:
        where = f"{item['file']}:{item['line']}" if item.get("file") else ""
        uses = f" {style.muted('×' + str(item['uses']), on=on)}" if item.get("uses") else ""
        name = style.muted(item["name"].ljust(width), on=on) if quiet else item["name"].ljust(width)
        print(f"{pad}{name}{style.muted(where, on=on)}{uses}".rstrip())
        if item.get("devtools"):
            print(pad + "  " + style.muted("only DevTools declares it, and these rules do not reach DevTools", on=on))
        if item.get("read_here"):
            read = item["read_here"]
            print(pad + "  " + style.muted(f"also read at {read['file']}:{read['line']}, "
                                            "where only this theme's value reaches", on=on))
        if item.get("suggestion"):
            print(f"{pad}  {style.good(style.ARROW + ' ' + item['suggestion'], on=on)}"
                  f" {style.muted(suggest_word, on=on)}")
    if len(shown) < len(items):
        print(pad + style.muted(f"{style.ELLIPSIS} and {len(items) - len(shown)} more; "
                                "--all lists them", on=on))


def _explain(lines, on):
    """A dim note under a finding: a fragment, no closing full stop."""
    from . import style
    text = " ".join(lines).rstrip(".")
    if not text.startswith(("Firefox", "DevTools")):
        text = text[0].lower() + text[1:]
    for line in style.wrap(text, indent=BODY):
        print(style.muted(line, on=on))


def report_unused(unused, colour=True, show_all=False, context=None):
    from . import style
    on = colour and style.colour_on()
    pad = " " * BODY

    if not unused:
        return
    # One file:line column for every property block in the report, so the
    # references line up all the way down rather than block by block.
    named = [i for key in ("missing_properties", "stale_overrides", "devtools_only",
                           "unused_properties") for i in unused.get(key) or []]
    name_width = max((len(i["name"]) for i in named), default=0) + 2
    total = (len(unused["orphans"]) + len(unused["unused_properties"])
             + len(unused.get("stale_overrides", []))
             + len(unused.get("devtools_only", []))
             + len(unused["missing_properties"]))
    if unused["missing_properties"]:
        count = len(unused["missing_properties"])
        print(_status("UNDEFINED", "bad", on)
              + f"{_pl(count, 'custom property', 'custom properties')} used but never set")
        _explain(["Firefox does not provide these either, so the var() falls back or fails."], on)
        _names_block(unused["missing_properties"], on, show_all,
                     "is what this Firefox provides", width=name_width)
        print()

    print(style.heading_line("Unused and unreachable",
                             "housekeeping, not breakage: nothing here stops the theme working",
                             on=on))
    notes = [] if context is None else context

    analysis = unused.get("analysis") or {}
    if analysis and not analysis.get("complete", True):
        print(_status("INCOMPLETE", "warn", on)
              + "the import graph could not be read in full, so the findings")
        print(pad + "below describe only the part that resolved:")
        for reason in analysis.get("reasons") or []:
            print(pad + "  " + reason)
        print()
    if analysis.get("conditional"):
        n = len(analysis["conditional"])
        notes.append({"count": n, "kind": "file",
                      "short": "conditional import" if n == 1 else "conditional imports",
                      "text": ("conditional import" if n == 1 else "conditional imports")
                              + " of files not shipped, off unless their pref or media applies"})
    if analysis.get("hooks"):
        n = len(analysis["hooks"])
        notes.append({"count": n, "kind": "file",
                      "short": ("user hook" if n == 1 else "user hooks")
                               + f" ({', '.join(analysis['hooks'])})",
                      "text": ("user hook" if n == 1 else "user hooks")
                              + " imported but not shipped, as intended: "
                              + ", ".join(analysis["hooks"])})
    if analysis.get("content_only"):
        n = len(analysis["content_only"])
        notes.append({"count": n, "kind": "file",
                      "short": "content-only stylesheet" if n == 1 else "content-only stylesheets",
                      "text": ("stylesheet that loads" if n == 1 else "stylesheets that load")
                              + " only through userContent.css, for web and extension pages"})
    print()

    if unused["orphans"]:
        print(_status("NOT IMPORTED", "warn", on)
              + f"{_pl(len(unused['orphans']), 'stylesheet')} nothing reaches")
        limit = None if show_all else 5
        for path in unused["orphans"][:limit]:
            print(f"{pad}{path}")
        extra = len(unused["orphans"]) - (limit or len(unused["orphans"]))
        if extra > 0:
            print(pad + style.muted(f"{style.ELLIPSIS} and {extra} more; --all lists them", on=on))
        print()

    if unused.get("stale_overrides"):
        count = len(unused["stale_overrides"])
        print(_status("NEVER READ", "warn", on)
              + f"{_pl(count, 'custom property', 'custom properties')} this Firefox "
              "declares but no longer reads")
        _explain(["Overriding these changes nothing: the shipped chrome carries the",
                  "declaration but not one var() or script that consumes it. Mark a",
                  "line `/* fxcss-keep */` if an older Firefox you support still reads it."], on)
        _names_block(unused["stale_overrides"], on, show_all, "is what this Firefox reads",
                     width=name_width)
        print()

    if unused.get("devtools_only"):
        count = len(unused["devtools_only"])
        print(_status("DEVTOOLS ONLY", "warn", on)
              + f"{_pl(count, 'custom property', 'custom properties')} set here that only "
              "DevTools still uses")
        _explain(["This Firefox's browser chrome neither declares nor reads these names;",
                  "DevTools' stylesheets do, but only inside DevTools documents, which a",
                  "rule reaches from @-moz-document url-prefix(\"chrome://devtools/\").",
                  "Set anywhere else they override nothing: usually a rename the browser",
                  "made and DevTools did not."], on)
        _names_block(unused["devtools_only"], on, show_all, "is what this Firefox reads",
                     width=name_width)
        print()

    if unused["unused_properties"]:
        count = len(unused["unused_properties"])
        print(_status("DEFINED ONLY", "muted", on)
              + f"{_pl(count, 'custom property', 'custom properties')} set here "
              "and read nowhere in the theme")
        if unused.get("packed"):
            _explain(["This Firefox's shipped chrome neither declares nor reads these",
                      "names, so overriding them does nothing on this build."], on)
        else:
            _explain(["An unthemed Firefox does not resolve these names either, so they are",
                      "likely renamed or dropped. Worth checking rather than deleting: a",
                      "name Firefox references without setting would look the same here."], on)
        _names_block(unused["unused_properties"], on, show_all, "is what this Firefox reads",
                     width=name_width, quiet=True)
        print()

    if unused.get("overrides"):
        n = unused["overrides"]
        notes.append({"count": n, "kind": "property",
                      "short": "Firefox-only overrides" if n != 1 else "Firefox-only override",
                      "text": ("property" if n == 1 else "properties")
                              + " set here but read only by Firefox: deliberate overrides, not dead code"})
    if unused.get("kept"):
        n = len(unused["kept"])
        notes.append({"count": n, "kind": "property", "short": "marked fxcss-keep",
                      "text": ("property" if n == 1 else "properties")
                              + " marked fxcss-keep, left out of the findings"})
    if context is None and notes:
        print_context(notes, show_all, on, hint=False)
        print()

    if total == 0:
        print(f"  {style.good(style.TICK, on=on)} {style.muted('Nothing unused found.', on=on)}\n")


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
