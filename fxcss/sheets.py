#!/usr/bin/env python3
"""Which of a theme's optional stylesheets can be used together.

Themes ship their options as separate sheets in ``custom/``, and installing
two is just two ``@import``s. Nothing warns anyone that some pairs are
alternatives rather than additions: install two colour themes and the later
import silently wins outright, leaving a browser that looks like neither the
one that was picked nor the one before it.

The check here is a measurement, not a guess. Two sheets are alternatives when
they set **the same properties on the same selectors** -- which is provable
from their text and true regardless of what they are called. WhiteSur's colour
themes each redefine the same 118 custom properties on ``:root``; its
``compact-tabs`` shares none of them with any of them.

Deliberately *not* done by name. `theme-blue` and `theme-red` look like a
family, but a prefix is a convention rather than a fact: a theme shipping
`theme-blue` and `theme-compact` would be told those clash when they compose
perfectly well, and a pair named `dark.css` and `nord.css` would be missed.
The same reasoning keeps a hardcoded table of known themes out of `audit`.

**Known limit.** This sees declarations, so it catches sheets that fight over
the same property. Two sheets that rearrange the same area through different
selectors -- WhiteSur's `tabs-swapclose` and `windows-swapclose` both move a
close button, sharing almost no declarations -- are invisible to it, and
`fxcss tweaks --combo a+b` is what proves those: if a+b renders identically to
b alone, a was overridden. This never reports such a pair, and never claims a
pair it passes is safe; it reports what it can prove.
"""

import re
from pathlib import Path

# One sheet being ~entirely overridden by another is what "alternatives"
# means. Below this they are reported as overlapping, which is ordinary --
# two options touching one shared variable is not a reason to refuse.
EXCLUSIVE_SHARE = 0.8

WHITESPACE = re.compile(r"\s+")


def _strip_comments(text):
    out, i, n = [], 0, len(text)
    while i < n:
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
            out.append(" ")
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def _tidy(text):
    return WHITESPACE.sub(" ", text).strip()


def _tidy_selector(text):
    """A selector in one spelling, so two sheets can be compared at all.

    `.a>.b` and `.a > .b` address the same element, and themes are written by
    different hands; comparing them as text would miss the overlap. Only
    combinators at the top level are spaced out -- the `~=` of an attribute
    selector and the `+` of `:nth-child(2n+1)` are not combinators, and
    pulling those apart would invent selectors that match nothing.
    """
    out, depth = [], 0
    quote = None
    for i, char in enumerate(text):
        if quote:
            out.append(char)
            if char == quote:
                quote = None
            continue
        if char in "\"'":
            quote = char
        elif char in "([":
            depth += 1
        elif char in ")]":
            depth = max(depth - 1, 0)
        elif char in ">+~" and depth == 0:
            # `~=` never reaches here: it only occurs inside [ ], where depth
            # is above zero.
            out.append(f" {char} ")
            continue
        out.append(char)
    return _tidy("".join(out))


def _record(out, stack, buffer):
    """File one declaration under every selector the open rule names.

    Only inside a style rule: a `;` directly inside an @media block is a
    stray, and one at the top level is an @import or @namespace.
    """
    text = _tidy("".join(buffer))
    if not text or ":" not in text or not stack:
        return
    rule = stack[-1]
    if not rule or rule.startswith("@"):
        return
    prop, _, value = text.partition(":")
    prop, value = _tidy(prop), _tidy(value)
    if not prop or not value:
        return
    # Everything an @media/@supports wrapper adds is part of the address: the
    # same declaration under two different conditions is two declarations.
    context = tuple(_tidy(entry) for entry in stack[:-1] if entry.startswith("@"))
    for selector in rule.split(","):
        selector = _tidy_selector(selector)
        if selector:
            out[(context, selector, prop)] = value


def declarations(text):
    """Every declaration in a stylesheet, keyed by where it lands.

    Returns {(at-rule context, selector, property): value}. Hand-rolled rather
    than a CSS parser: the answer needed is "does this sheet set that, there",
    which needs brace and quote tracking and nothing else. Anything it cannot
    make sense of is skipped rather than guessed at -- a missed declaration
    weakens a warning, an invented one produces a false refusal.
    """
    text = _strip_comments(text)
    out, stack, buffer = {}, [], []
    quote = None
    i, n = 0, len(text)
    while i < n:
        char = text[i]
        if quote:
            buffer.append(char)
            if char == "\\" and i + 1 < n:
                buffer.append(text[i + 1])
                i += 2
                continue
            if char == quote:
                quote = None
            i += 1
            continue
        if char in "\"'":
            quote = char
            buffer.append(char)
        elif char == "{":
            stack.append(_tidy("".join(buffer)))
            buffer = []
        elif char == "}":
            _record(out, stack, buffer)      # a last declaration may have no ;
            buffer = []
            if stack:
                stack.pop()
        elif char == ";":
            _record(out, stack, buffer)
            buffer = []
        else:
            buffer.append(char)
        i += 1
    return out


def read_declarations(path):
    """`declarations` for a file, empty when it cannot be read."""
    try:
        return declarations(Path(path).read_text(encoding="utf-8",
                                                 errors="replace"))
    except OSError:
        return {}


def overlap(first, second):
    """How much two sheets fight over the same ground.

    `first` and `second` are (name, declarations) pairs. Returns a dict with
    the shared and conflicting counts, what share of each sheet that is, and
    a few examples worth printing.

    Declarations both sheets set *identically* are shared but not conflicting:
    two options that happen to set the same border radius the same way are
    not alternatives, they simply agree.
    """
    (name_a, decls_a), (name_b, decls_b) = first, second
    shared = set(decls_a) & set(decls_b)
    conflicting = sorted(key for key in shared if decls_a[key] != decls_b[key])
    return {
        "a": name_a,
        "b": name_b,
        "shared": len(shared),
        "conflicting": len(conflicting),
        "declarations_a": len(decls_a),
        "declarations_b": len(decls_b),
        # How much of each sheet the other one covers. Shared ground, not
        # differing values: two palettes that both set all 122 of the same
        # properties are alternatives even where a few colours coincide, and
        # dividing by the conflicting count would rank the pair as mild
        # precisely when the two designs happen to agree.
        "share_a": len(shared) / (len(decls_a) or 1),
        "share_b": len(shared) / (len(decls_b) or 1),
        "examples": [
            {"selector": selector, "property": prop,
             "a": decls_a[key], "b": decls_b[key]}
            for key in conflicting[:3]
            for (_, selector, prop) in [key]
        ],
    }


def verdict(report):
    """What this pair is: "alternatives", "subsumed", "overlap" or "".

    alternatives  each sheet covers what the other does, so installing both
                  means the later one replaces the earlier one wholesale
    subsumed      one sheet is entirely inside the other, which keeps doing
                  its other work -- the smaller one is what stops mattering
    overlap       they disagree somewhere, but each still does its own job
    ""            nothing measurable between them

    Identical values are not a conflict: two options that set the same border
    radius the same way agree rather than compete, and combining them changes
    nothing.
    """
    if not report["conflicting"]:
        return ""
    big = max(report["share_a"], report["share_b"])
    small = min(report["share_a"], report["share_b"])
    if small >= EXCLUSIVE_SHARE:
        return "alternatives"
    if big >= EXCLUSIVE_SHARE:
        return "subsumed"
    return "overlap"


def exclusive(report):
    """Is this pair a choice rather than a combination?"""
    return verdict(report) == "alternatives"


def overridden(report):
    """For a `subsumed` pair, the sheet that stops mattering."""
    return report["a"] if report["share_a"] >= report["share_b"] else report["b"]


def conflicts(sheets):
    """Every overlapping pair among these sheets, worst first.

    `sheets` are paths. Pairs with nothing in common are left out entirely,
    so an empty result means "nothing to say", which is the common case.
    """
    parsed = [(Path(sheet).stem, read_declarations(sheet)) for sheet in sheets]
    found = []
    for i, first in enumerate(parsed):
        for second in parsed[i + 1:]:
            report = overlap(first, second)
            if report["conflicting"]:
                found.append(report)
    return sorted(found, key=lambda r: (-max(r["share_a"], r["share_b"]),
                                        r["a"], r["b"]))


def describe(report):
    """One line saying what this pair does to each other."""
    kind = verdict(report)
    if kind == "alternatives":
        return (f"{report['a']} and {report['b']} are alternatives, not "
                f"additions: both set the same {report['shared']} "
                "declaration(s), so whichever loads last replaces the other "
                "entirely")
    if kind == "subsumed":
        loser = overridden(report)
        other = report["b"] if loser == report["a"] else report["a"]
        return (f"{loser} does nothing alongside {other}: every declaration "
                f"it makes is also made by {other}, which sets them "
                "differently")
    return (f"{report['a']} and {report['b']} disagree on "
            f"{report['conflicting']} declaration(s); the later one wins those")
