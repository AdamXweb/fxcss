"""How fxcss looks in a terminal: one palette and a few shapes, used everywhere.

Colour is decoration on top of text that already reads plainly. Every helper
returns the same words with colour off (piped output, NO_COLOR, --no-colour),
so nothing depends on a colour to carry its meaning. Only the 16 ANSI colours
plus bold and dim are used: the user's terminal theme picks the shades, which
is what keeps one palette readable on light and dark backgrounds alike.

Roles, not colours, are what the rest of the code asks for:

  brand    the fxcss mark, and the thing being asked about (bold magenta)
  heading  a section title (bold)
  muted    labels, explanation and secondary notes (dim)
  good     done, healthy, applied, the fix (green)
  warn     needs a look, manual, a guess (yellow)
  bad      broken, missing, required (red)

The layout rules every command follows:

  * output is indented two spaces; items under a heading four; detail six
  * a heading is bold sentence case with no colon, one blank line above
  * key/value rows put a dim label in a column sized to the block
  * status words are upper case in a fixed column, coloured by severity
  * a check is a mark (✓ ! ✗) then the sentence; the mark is never alone
  * paths start at ~ when they are under the home folder
  * nothing wraps inside a word: lists break between items, prose between words
"""

import os
import sys
import textwrap
from pathlib import Path

_override = None

_SGR = {
    "bold": "1", "dim": "2", "underline": "4",
    "red": "31", "green": "32", "yellow": "33", "blue": "34",
    "magenta": "35", "cyan": "36",
}


def _utf8():
    encoding = (getattr(sys.stdout, "encoding", None) or "").lower()
    return "utf" in encoding


# One glyph set, with ASCII stand-ins for a terminal that cannot show them.
if _utf8():
    TICK, BANG, CROSS, ARROW, DOT, BULLET, ELLIPSIS, SEP = (
        "✓", "!", "✗", "→", "·", "●", "…", "—")
else:  # pragma: no cover - depends on the console
    TICK, BANG, CROSS, ARROW, DOT, BULLET, ELLIPSIS, SEP = (
        "ok", "!", "x", "->", "-", "*", "...", "-")


def set_colour(on):
    """Force colour on or off for the rest of the run (None: detect again)."""
    global _override
    _override = on


def colour_on(stream=None):
    """Colour only for a person at a terminal, and never against NO_COLOR."""
    if _override is not None:
        return _override
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    stream = stream or sys.stdout
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False


def sgr(text, *names, on=None):
    if not text or not (colour_on() if on is None else on):
        return text
    codes = ";".join(_SGR[n] for n in names)
    return f"\033[{codes}m{text}\033[0m"


def brand(text, on=None):
    return sgr(text, "bold", "magenta", on=on)


def heading(text, on=None):
    return sgr(text, "bold", on=on)


strong = heading


def muted(text, on=None):
    return sgr(text, "dim", on=on)


def good(text, on=None):
    return sgr(text, "green", on=on)


def warn(text, on=None):
    return sgr(text, "yellow", on=on)


def bad(text, on=None):
    return sgr(text, "red", on=on)


_ROLES = {"good": good, "warn": warn, "bad": bad, "muted": muted,
          "brand": brand, "strong": strong, "plain": lambda t, on=None: t}


def paint(text, role, on=None):
    return _ROLES[role](text, on=on)


def plural(count, word, many=None):
    """`1 file`, `3 files`, `2 properties`: never `file(s)`."""
    if count == 1:
        return f"{count} {word}"
    if many is None:
        many = word[:-1] + "ies" if word.endswith("y") and word[-2:-1] not in "aeiou" else word + "s"
    return f"{count:,} {many}"


def short_path(path):
    """Home-relative, because the interesting part of a path is its tail."""
    try:
        return "~/" + str(Path(path).expanduser().relative_to(Path.home()))
    except ValueError:
        return str(path)


def term_width(default=100):
    try:
        cols = os.get_terminal_size(sys.stdout.fileno()).columns
    except (OSError, ValueError, AttributeError):
        try:
            cols = int(os.environ.get("COLUMNS") or default)
        except ValueError:
            cols = default
    return max(60, min(cols, 110))


def wrap(text, indent=2, hang=None, width=None):
    """Prose wrapped between words to the terminal, with a hanging indent."""
    width = width or term_width()
    hang = indent if hang is None else hang
    return textwrap.wrap(text, width=width - 1, initial_indent=" " * indent,
                         subsequent_indent=" " * hang, break_long_words=False,
                         break_on_hyphens=False) or [" " * indent]


def heading_line(text, note=None, indent=2, on=None):
    """A section title: bold, sentence case, an optional dim note after it."""
    line = f"{' ' * indent}{heading(text, on=on)}"
    if note:
        line += f"  {muted(note, on=on)}"
    return line


def rows(pairs, indent=2, gap=2, width=None, label_width=None, on=None):
    """Aligned `label  value` lines; labels recede, values carry the weight.

    A value longer than the line wraps under itself, never back under the
    label; a list value puts one item per line in the value column.
    """
    pairs = [(k, v) for k, v in pairs if v is not None]
    if not pairs:
        return []
    width = width or term_width()
    lw = label_width or max(len(k) for k, _ in pairs)
    lead = indent + lw + gap
    out = []
    for key, value in pairs:
        values = value if isinstance(value, (list, tuple)) else [value]
        first = True
        for item in (values or [""]):
            item = str(item)
            plain = _visible(item)
            if lead + len(plain) < width:
                pieces = [item]
            elif plain == item:
                pieces = textwrap.wrap(item, width=max(20, width - lead - 1),
                                       break_long_words=False, break_on_hyphens=False)
            else:
                # Styled text cannot be re-wrapped safely, but every styled
                # value here is `value  note`: too long, the note moves to
                # the next line under the value instead of splitting a word.
                pieces = [piece for piece in item.split("  ") if piece]
            for piece in pieces:
                label = muted(key.ljust(lw), on=on) if first else " " * lw
                out.append(f"{' ' * indent}{label}{' ' * gap}{piece}")
                first = False
    return out


MARKS = {"good": TICK, "warn": BANG, "bad": CROSS, "muted": DOT}


def status(word, role, width=12, on=None, mark=True):
    """A status word after its mark, padded outside the colour codes.

    One rule everywhere: the mark carries the colour (✗ red, ! yellow,
    ✓ green), the word carries the weight in the terminal's own ink. Bold
    yellow turns bright yellow in most terminals, unreadable on white, so
    colour never sits on the word. `muted` words stay dim: informational.
    """
    if role == "muted" or not mark or role not in MARKS:
        text = f"{MARKS[role]} {word}" if mark and role in MARKS else word
        return paint(text, role, on=on) + " " * max(1, width - len(text))
    text = f"{MARKS[role]} {word}"
    painted = f"{paint(MARKS[role], role, on=on)} {strong(word, on=on)}"
    return painted + " " * max(1, width - len(text))


def caution(text, on=None):
    """A warning inside a sentence: the yellow mark, then the words in ink."""
    return f"{warn(BANG, on=on)} {strong(text, on=on)}"


def cmd(text, on=None):
    """A command to type, inside a sentence: bold, never in backticks."""
    return strong(text, on=on)


def check(state, text, detail=None, indent=2, on=None):
    """One diagnostic line: ✓ fine, ! worth a look, ✗ broken; then the words."""
    mark, role = {"ok": (TICK, "good"), "warn": (BANG, "warn"),
                  "bad": (CROSS, "bad"), "info": (DOT, "muted")}[state]
    out = [f"{' ' * indent}{paint(mark, role, on=on)} {text}"]
    if detail:
        # A sub-note like every other: dim, with any --flag in it bold, since
        # the flag is the part to type.
        import re as _re
        for line in wrap(detail, indent=indent + 2):
            line = muted(line, on=on)
            if colour_on() if on is None else on:
                line = _re.sub(r"(--[\w-]+(?:=\S+)?)", lambda m: "\033[22;1m" + m.group(1) + "\033[22;2m", line)
            out.append(line)
    return out


def columns(items, indent=4, gap=3, width=None, numbered=False):
    """Names packed into as many columns as fit, filled top to bottom.

    Each column is as wide as its own longest entry, and the gutter between
    columns is the same everywhere, so no pair of columns runs together.
    """
    width = width or term_width()
    labels = list(items)
    if numbered:
        digits = len(str(len(labels)))
        labels = [f"{i:>{digits}}. {name}" for i, name in enumerate(labels, 1)]
    if not labels:
        return []
    for ncols in range(min(len(labels), 8), 0, -1):
        nrows = -(-len(labels) // ncols)
        cols = [labels[c * nrows:(c + 1) * nrows] for c in range(ncols)]
        cols = [c for c in cols if c]
        widths = [max(len(s) for s in col) for col in cols]
        if indent + sum(widths) + gap * (len(cols) - 1) <= width - 1 or ncols == 1:
            break
    out = []
    for r in range(nrows):
        cells = []
        for c, col in enumerate(cols):
            if r < len(col):
                cells.append(col[r].ljust(widths[c]))
        out.append(" " * indent + (" " * gap).join(cells).rstrip())
    return out


def ask(question, choices=None, default=None, indent=2, on=None):
    """A prompt: the question bold, then the choices and the default."""
    tail = []
    if choices:
        tail.append(choices)
    if default:
        tail.append(f"[{default}]")
    suffix = (" " + " ".join(tail)) if tail else ""
    return f"{' ' * indent}{strong(question, on=on)}{muted(suffix, on=on)} "


def _visible(text):
    import re
    return re.sub(r"\033\[[0-9;]*m", "", text)


def title(command_name, context=None, on=None):
    """The first line of a long-running command: the mark, the verb, where."""
    line = f"  {brand('fxcss', on=on)} {heading(command_name, on=on)}"
    if context:
        line += f"  {muted(context, on=on)}"
    return line


def platform_name(os_name):
    return {"Darwin": "macOS", "Linux": "Linux", "WINNT": "Windows",
            "Windows_NT": "Windows"}.get(os_name, os_name)


def agree(count, many, one):
    """The verb that goes with a count: agree(n, "are", "is")."""
    return one if count == 1 else many
