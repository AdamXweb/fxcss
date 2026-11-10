"""Answering fxcss's questions: type the number, or move to it with the arrows.

Every numbered question takes a typed answer, exactly as it always has: that
is what works through a pipe, in a dumb terminal, under a screen reader and
in a script. At a real terminal the same prompt also listens for keys. ↑ and
↓ move a pointer, a typed number moves it too, and Enter takes it; in a
multiple choice, Space ticks. Only the menu's own lines are redrawn, never
the whole screen, and what is left in the scrollback reads like the typed
answer would have.

FXCSS_PLAIN_PROMPTS=1 turns the keys off and keeps the typed prompt, for
anyone whose terminal or screen reader copes badly with lines redrawn in
place.
"""

import os
import sys
from contextlib import contextmanager, nullcontext

from . import style

# A terminal narrower than the style layer's 60-column floor wraps rows, and
# a wrapped row cannot be redrawn in place.
MIN_COLUMNS = 60


def arrows_ok(height=0):
    """Arrow keys only for a person at a terminal that can redraw `height` lines.

    Both ends must be the real terminal: a test that stands in for stdout,
    or output piped to a file, gets the typed prompt.
    """
    if os.environ.get("FXCSS_PLAIN_PROMPTS") or os.environ.get("TERM") == "dumb":
        return False
    try:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return False
        if not (os.isatty(sys.stdin.fileno()) and os.isatty(sys.stdout.fileno())):
            return False
        size = os.get_terminal_size(sys.stdout.fileno())
    except (AttributeError, ValueError, OSError):
        return False
    if size.columns < MIN_COLUMNS or size.lines <= height + 1:
        return False
    if os.name == "nt":
        return _windows_vt()
    try:
        import termios  # noqa: F401
    except ImportError:
        return False
    return True


# Keys, as tokens: "up", "down", "left", "right", "home", "end", "enter",
# "space", "backspace", "esc", "ctrl-c", "eof", or the character typed.

_CSI = {"A": "up", "B": "down", "C": "right", "D": "left", "H": "home", "F": "end",
        "1~": "home", "7~": "home", "4~": "end", "8~": "end"}

_WINDOWS = {"H": "up", "P": "down", "K": "left", "M": "right", "G": "home", "O": "end"}


def decode(first, more=lambda: ""):
    """One key from the characters a terminal sends for it.

    `first` is the first character; after an Esc, `more()` returns what
    followed it in the same burst ("" when nothing did, which is Esc itself).
    Returns a token, or None for a key that means nothing here.
    """
    if first == "\x1b":
        seq = more()
        if not seq:
            return "esc"
        if seq[0] in "[O" and len(seq) > 1:
            body = seq[1:]
            # A modifier rides in the middle: Ctrl+↑ is "[1;5A", Ctrl+Home
            # "[1;5H" or "[7;5~". The final letter, or the number, is the key.
            key = body[-1] if body[-1].isalpha() else body.split(";")[0].rstrip("~") + "~"
            return _CSI.get(key)
        return None
    if first in ("\r", "\n"):
        return "enter"
    if first in ("\x7f", "\x08"):
        return "backspace"
    if first == "\x03":
        return "ctrl-c"
    if first in ("\x04", "\x1a"):
        return "eof"
    if first == " ":
        return "space"
    if len(first) == 1 and " " < first < "\x7f":
        return first
    return None


@contextmanager
def _posix_keys():
    import select
    import termios
    import tty

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)

    def read(timeout=None):
        if timeout is not None and not select.select([fd], [], [], timeout)[0]:
            return ""
        data = os.read(fd, 1)
        return data.decode("latin-1") if data else None

    def more():
        seq = ""
        while len(seq) < 8:
            ch = read(0.05)
            if not ch:
                break
            seq += ch
            if len(seq) > 1 and (ch.isalpha() or ch == "~"):
                break
        return seq

    def keys():
        while True:
            first = read()
            if first is None:
                yield "eof"
                return
            key = decode(first, more)
            if key:
                yield key

    # cbreak, not raw: Ctrl+C still raises KeyboardInterrupt, and the
    # terminal's own settings come back however this ends.
    tty.setcbreak(fd)
    try:
        yield keys()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


@contextmanager
def _windows_keys():  # pragma: no cover - needs a Windows console
    import msvcrt

    def keys():
        while True:
            ch = msvcrt.getwch()
            if ch in ("\x00", "\xe0"):
                key = _WINDOWS.get(msvcrt.getwch())
            else:
                key = decode(ch)
            if key == "ctrl-c":
                raise KeyboardInterrupt
            if key:
                yield key

    yield keys()


def _windows_vt():  # pragma: no cover - needs a Windows console
    """Let a Windows console act on the cursor codes the redraw uses."""
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except (AttributeError, OSError):
        return False


def keypresses():
    return _windows_keys() if os.name == "nt" else _posix_keys()


class Frame:
    """The menu's own lines at the foot of the terminal, redrawn in place."""

    def __init__(self, out=None):
        self.out = out or sys.stdout
        self.height = 0

    def draw(self, lines, final=False):
        rows = "\n".join(lines).split("\n")
        up = f"\033[{self.height - 1}A" if self.height > 1 else ""
        text = ("\r" + up if self.height else "") + "\n".join(f"\033[2K{r}" for r in rows)
        self.out.write(text + "\033[J" + ("\n" if final else ""))
        self.out.flush()
        self.height = len(rows)


def _cancel(frame):
    frame.out.write("\n")
    frame.out.flush()
    raise KeyboardInterrupt


def pick_one(count, default, row, question="Which one?", keys=None, out=None):
    """One of `count` choices; returns its zero-based index.

    `row(i, current, pointer)` draws choice i: `current` marks the one Enter
    takes, `pointer` the one the arrow keys are on. A typed number moves the
    pointer as it is typed; Backspace takes digits back.
    """
    current, typed = default, ""
    frame = Frame(out)

    def lines(final=False):
        body = [row(i, current=not final and i == current, pointer=i == current)
                for i in range(count)]
        if final:
            ask = style.ask(question, f"1-{count}", str(default + 1)) + str(current + 1)
        else:
            ask = style.ask(question, f"{style.UPDOWN} or 1-{count}", str(current + 1)) + typed
        return body + ["", ask]

    with keypresses() if keys is None else nullcontext(iter(keys)) as stream:
        frame.draw(lines())
        for key in stream:
            if key in ("enter", "eof"):
                break
            if key in ("esc", "ctrl-c"):
                _cancel(frame)
            if key in ("up", "down", "home", "end"):
                typed = ""
                current = {"up": (current - 1) % count, "down": (current + 1) % count,
                           "home": 0, "end": count - 1}[key]
            elif key == "backspace":
                typed = typed[:-1]
                if typed and 1 <= int(typed) <= count:
                    current = int(typed) - 1
            elif key.isdigit():
                for candidate in (typed + key, key):
                    if 1 <= int(candidate) <= count:
                        typed, current = candidate, int(candidate) - 1
                        break
                else:
                    continue
            else:
                continue
            frame.draw(lines())
        frame.draw(lines(final=True), final=True)
    return current


def pick_many(names, question, typed_hint, parse, keys=None, out=None, indent=4, groups=None):
    """Any of `names`; returns the answer as text, as if it had been typed.

    Space ticks the item at the pointer and ←→ cross columns. Typing works as
    in the plain prompt (`1,3`, `all`), and what is typed is ticked as it is
    typed. Once something is typed, Space is part of the text (`20 4`), as
    it would be in the plain prompt. The answer comes back as the text the
    plain prompt would have read, so one parser checks both.

    `groups` ([(title, count)], as for `style.column_groups`) shows the names
    under titles, numbered straight through. ↑↓ run on from one group into
    the next; ←→ stay inside the group.
    """
    count = len(names)
    spans = style.group_spans(names, groups, indent=indent)
    current, ticks, typed = 0, set(), ""
    frame = Frame(out)

    def shown():
        return ticks | set(parse(typed))

    def answer():
        text = typed.strip()
        if text.lower() in ("all", "*", "a") or not ticks:
            return text
        numbers = ",".join(str(i + 1) for i in sorted(ticks))
        return f"{numbers},{text}" if text else numbers

    def summary():
        got = sorted(shown())
        if not got:
            return "none"
        if len(got) == count > 1:
            return "all"
        if len(got) > 6:
            return f"{len(got)} ticked"
        return ",".join(str(i + 1) for i in got)

    def lines(final=False):
        grid = style.column_groups(names, groups, indent=indent,
                                   pointer=None if final else current, ticked=shown())
        if final:
            ask = style.ask(question, typed_hint, "none") + answer()
        else:
            ask = style.ask(question, f"{style.ARROWS} and space to tick, or {typed_hint}",
                            summary()) + typed
        return grid + ["", ask]

    with keypresses() if keys is None else nullcontext(iter(keys)) as stream:
        frame.draw(lines())
        for key in stream:
            if key in ("enter", "eof"):
                break
            if key in ("esc", "ctrl-c"):
                _cancel(frame)
            if key in ("up", "down"):
                current = (current + (1 if key == "down" else -1)) % count
            elif key in ("left", "right"):
                start, size, step = next(s for s in spans if s[0] <= current < s[0] + s[1])
                here = current - start
                moved = here + (step if key == "right" else -step)
                if 0 <= moved < size:
                    current = start + moved
                elif key == "right" and here // step < (size - 1) // step:
                    current = start + size - 1
            elif key in ("home", "end"):
                current = 0 if key == "home" else count - 1
            elif key == "space" and not typed:
                ticks ^= {current}
            elif key == "backspace":
                typed = typed[:-1]
            elif key == "space" or len(key) == 1:
                typed += " " if key == "space" else key
                last = typed.replace(",", " ").split()[-1:] if typed.strip() else []
                if last and last[0].isdigit() and 1 <= int(last[0]) <= count:
                    current = int(last[0]) - 1
            else:
                continue
            frame.draw(lines())
        frame.draw(lines(final=True), final=True)
    return answer()
