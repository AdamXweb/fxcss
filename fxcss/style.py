"""How fxcss looks in a terminal: one palette and a few shapes, used everywhere.

Colour is decoration on top of text that already reads plainly. Every helper
returns the same words with colour off (piped output, NO_COLOR, --no-colour),
so nothing depends on a colour to carry its meaning. Only the 16 ANSI colours
plus bold and dim are used: the user's terminal theme picks the shades, which
is what keeps one palette readable on light and dark backgrounds alike. The
one exception is `swatch`, which previews a theme's own colours: those are
the point of showing it, and the hex code beside it says the same in text.

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
    POINTER, UPDOWN, ARROWS, BLOCK = "›", "↑↓", "↑↓←→", "██"
else:  # pragma: no cover - depends on the console
    TICK, BANG, CROSS, ARROW, DOT, BULLET, ELLIPSIS, SEP = (
        "ok", "!", "x", "->", "-", "*", "...", "-")
    POINTER, UPDOWN, ARROWS, BLOCK = ">", "up/down", "arrows", "##"

# Beside an operating system's name, never instead of it. Each is the symbol
# for that system's special key: Command, the Windows key and Meta.
OS_MARKS = {"macOS": "⌘", "Windows": "⊞", "Linux": "◆"} if _utf8() else {}


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


def colour_depth():
    """What a colour preview can use here: 24 (any colour), 8 (the 256-colour
    palette, which nearly every terminal has) or 0 (none: no colour at all)."""
    if not colour_on():
        return 0
    if os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit"):
        return 24
    return 8 if "256" in os.environ.get("TERM", "") else 0


def rgb(hex_colour):
    """(r, g, b) for #rgb, #rgba, #rrggbb or #rrggbbaa; alpha is dropped."""
    digits = hex_colour.lstrip("#")
    if len(digits) in (3, 4):
        digits = "".join(c * 2 for c in digits[:3])
    return tuple(int(digits[i:i + 2], 16) for i in (0, 2, 4))


def _nearest_256(rgb):
    """The closest of xterm's 6x6x6 colour cube and its 24 greys."""
    levels = (0, 95, 135, 175, 215, 255)
    cube = [min(range(6), key=lambda i: abs(levels[i] - c)) for c in rgb]
    cube_rgb = tuple(levels[i] for i in cube)
    grey = min(range(24), key=lambda i: abs(8 + 10 * i - sum(rgb) / 3))
    grey_rgb = (8 + 10 * grey,) * 3

    def distance(other):
        return sum((a - b) ** 2 for a, b in zip(rgb, other))
    if distance(grey_rgb) < distance(cube_rgb):
        return 232 + grey
    return 16 + 36 * cube[0] + 6 * cube[1] + cube[2]


def swatch(hex_colour):
    """A block of `hex_colour` (#rgb or #rrggbb, alpha ignored), or "" where
    the terminal cannot show one. Always two columns wide when shown."""
    depth = colour_depth()
    if not depth:
        return ""
    red, green, blue = rgb(hex_colour)
    code = (f"38;2;{red};{green};{blue}" if depth == 24
            else f"38;5;{_nearest_256((red, green, blue))}")
    return f"\033[{code}m{BLOCK}\033[0m"


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


def clip(text, width, path=False):
    """Cut to `width` columns, keeping the end that carries the meaning.

    Prose keeps its start and breaks between words. A path keeps its tail
    (the .app, the profile folder) and drops whole folders from the front.
    """
    if len(text) <= width:
        return text
    if path:
        parts = text.split("/")
        while len(parts) > 1 and len(ELLIPSIS) + 1 + len("/".join(parts)) > width:
            parts.pop(0)
        out = f"{ELLIPSIS}/{'/'.join(parts)}"
        return out if len(out) <= width else ELLIPSIS + text[-(width - len(ELLIPSIS)):]
    cut = text[:width - len(ELLIPSIS)]
    if " " in cut[width // 2:]:
        cut = cut[:cut.rindex(" ")]
    return cut.rstrip(" ·—,;:") + ELLIPSIS


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
        # A note that would run past the edge goes under the title instead.
        fits = indent + len(text) + 2 + len(note) < term_width()
        line += f"  {muted(note, on=on)}" if fits else f"\n{' ' * indent}{muted(note, on=on)}"
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


def _column_layout(labels, indent, gap, width):
    for ncols in range(min(len(labels), 8), 0, -1):
        nrows = -(-len(labels) // ncols)
        cols = [labels[c * nrows:(c + 1) * nrows] for c in range(ncols)]
        cols = [c for c in cols if c]
        widths = [max(len(s) for s in col) for col in cols]
        if indent + sum(widths) + gap * (len(cols) - 1) <= width - 1 or ncols == 1:
            return cols, widths, nrows
    return [labels], [max(len(s) for s in labels)], len(labels)


def _numbered(items, first=1, digits=None):
    digits = digits or len(str(first + len(items) - 1))
    return [f"{i:>{digits}}. {name}" for i, name in enumerate(items, first)]


def column_rows(items, indent=4, gap=3, width=None, numbered=False, first=1, digits=None):
    """How many rows `columns` would use: the step for moving across a column."""
    labels = _numbered(list(items), first, digits) if numbered else list(items)
    return _column_layout(labels, indent, gap, width or term_width())[2] if labels else 0


def columns(items, indent=4, gap=3, width=None, numbered=False, pointer=None, ticked=None,
            first=1, digits=None):
    """Names packed into as many columns as fit, filled top to bottom.

    Each column is as wide as its own longest entry, and the gutter between
    columns is the same everywhere, so no pair of columns runs together.

    For a menu answered with the arrow keys, `pointer` is the index the `›`
    stands at (it sits in the gutter, so the layout never shifts) and
    `ticked` the indices whose `.` has become a `✓`. Both count from 0
    within `items`; `first` is the number shown for the first of them, and
    `digits` the width the numbers are padded to, so that groups numbered
    straight through (see `column_groups`) line up with each other.
    """
    width = width or term_width()
    names = list(items)
    digits = digits or len(str(first + len(names) - 1))
    labels = _numbered(names, first, digits) if numbered else list(names)
    if not labels:
        return []
    cols, widths, nrows = _column_layout(labels, indent, gap, width)
    marked = numbered and (pointer is not None or ticked)
    ticked = set(ticked or ())
    out = []
    for r in range(nrows):
        line = ""
        for c, col in enumerate(cols):
            if r >= len(col):
                continue
            gutter = " " * (indent if c == 0 else gap)
            cell = col[r].ljust(widths[c])
            if marked:
                index = c * nrows + r
                name = names[index]
                if index == pointer:
                    gutter = gutter[:-2] + brand(POINTER) + " "
                    name = brand(name)
                elif index in ticked:
                    name = strong(name)
                mark = good(TICK) if index in ticked else "."
                cell = (f"{first + index:>{digits}}{mark} {name}"
                        + " " * (widths[c] - len(col[r])))
            line += gutter + cell
        out.append(line.rstrip())
    return out


def column_groups(names, groups=None, indent=4, gap=3, width=None, pointer=None, ticked=None,
                  numbered=True):
    """`columns` in titled groups, numbered straight through.

    `groups` is [(title, count)] in the order of `names`; None, or a single
    untitled group, is the plain grid. A blank line and the title, muted and
    in line with the names, start every group. `pointer` and `ticked` count
    across all of `names`, as the numbers do.
    """
    names = list(names)
    groups = groups or [(None, len(names))]
    digits = len(str(len(names)))
    ticked = set(ticked or ())
    out, start = [], 0
    for title, count in groups:
        if title:
            out += ([""] if out else []) + [" " * indent + muted(title)]
        end = start + count
        out += columns(names[start:end], indent, gap, width, numbered=numbered,
                       pointer=pointer - start if pointer is not None and start <= pointer < end
                       else None,
                       ticked={i - start for i in ticked if start <= i < end},
                       first=start + 1, digits=digits)
        start = end
    return out


def group_spans(names, groups=None, indent=4, gap=3, width=None):
    """[(start, count, rows)] for each of `column_groups`' groups: where the
    group begins in `names`, how many it holds, and its column height (the
    step ←→ take inside it)."""
    names = list(names)
    groups = groups or [(None, len(names))]
    digits = len(str(len(names)))
    spans, start = [], 0
    for _, count in groups:
        spans.append((start, count, column_rows(names[start:start + count], indent, gap, width,
                                                numbered=True, first=start + 1, digits=digits)))
        start += count
    return spans


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


def dotted(items, indent=2, width=None, on=None):
    """Styled items joined by a dim dot, breaking between items at the edge."""
    width = width or term_width()
    separator = f" {muted(DOT, on=on)} "
    lines = [[]]
    used = indent
    for item in items:
        cells = len(_visible(item))
        if lines[-1] and used + 3 + cells >= width:
            lines.append([])
            used = indent
        used += cells + (3 if lines[-1] else 0)
        lines[-1].append(item)
    return [" " * indent + separator.join(line) for line in lines if line]


def title(command_name, context=None, on=None):
    """The first line of a long-running command: the mark, the verb, where."""
    line = f"  {brand('fxcss', on=on)} {heading(command_name, on=on)}"
    if context:
        line += f"  {muted(context, on=on)}"
    return line


def platform_name(os_name):
    """A reader's name for an OS, from what Firefox or sys.platform calls it."""
    return {"Darwin": "macOS", "Linux": "Linux", "WINNT": "Windows",
            "Windows_NT": "Windows", "darwin": "macOS", "linux": "Linux",
            "win32": "Windows"}.get(os_name, os_name)


def platform_label(os_name, on=None):
    """`⌘ macOS`: the name, after its symbol in the brand colour when it has one."""
    name = platform_name(os_name)
    mark = OS_MARKS.get(name)
    return f"{brand(mark, on=on)} {name}" if mark else name


def agree(count, many, one):
    """The verb that goes with a count: agree(n, "are", "is")."""
    return one if count == 1 else many
