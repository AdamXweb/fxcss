"""Menus answered with the arrow keys, and the typed prompt they fall back to."""

import io
import os
import re
import sys
import unittest
from unittest import mock

from fxcss import cli, prompt, style

ANSI = re.compile(r"\033\[[0-9;]*[A-Za-z]")


def visible(text):
    return ANSI.sub("", text)


def screen(out):
    """What the terminal shows at the end: the last frame, codes removed."""
    text = out.getvalue()
    last = text.rsplit("\r", 1)[-1]
    return [visible(line) for line in last.split("\n")]


class DecodeTests(unittest.TestCase):
    def test_arrow_and_edit_keys(self):
        seqs = {"[A": "up", "[B": "down", "[C": "right", "[D": "left", "OA": "up",
                "[H": "home", "[F": "end", "[1~": "home", "[4~": "end",
                "[1;5A": "up", "[1;2D": "left", "[7;5~": "home"}
        for seq, key in seqs.items():
            self.assertEqual(prompt.decode("\x1b", lambda s=seq: s), key, seq)

    def test_plain_keys(self):
        self.assertEqual(prompt.decode("\x1b"), "esc")
        self.assertEqual(prompt.decode("\r"), "enter")
        self.assertEqual(prompt.decode("\n"), "enter")
        self.assertEqual(prompt.decode("\x7f"), "backspace")
        self.assertEqual(prompt.decode(" "), "space")
        self.assertEqual(prompt.decode("7"), "7")
        self.assertEqual(prompt.decode(","), ",")
        self.assertEqual(prompt.decode("\x03"), "ctrl-c")
        self.assertEqual(prompt.decode("\x04"), "eof")

    def test_keys_that_mean_nothing_here(self):
        self.assertIsNone(prompt.decode("\x1b", lambda: "[3~"))   # Delete
        self.assertIsNone(prompt.decode("\x1b", lambda: "x"))     # Alt+x
        self.assertIsNone(prompt.decode("\xe9"))                   # a byte of é
        self.assertIsNone(prompt.decode("\x01"))


@unittest.skipIf(os.name == "nt", "a pseudo-terminal is POSIX only")
class TerminalTests(unittest.TestCase):
    """The real reader, against a pseudo-terminal rather than a stand-in."""

    def test_keys_arrive_and_the_terminal_is_put_back(self):
        import pty
        import termios
        master, slave = pty.openpty()
        try:
            before = termios.tcgetattr(slave)
            fake_stdin = mock.Mock(fileno=lambda: slave)
            with mock.patch.object(sys, "stdin", fake_stdin):
                with prompt.keypresses() as keys:
                    # Typed after cbreak starts: switching mode flushes input
                    # already waiting, as it should for a stale keystroke.
                    os.write(master, b"\x1b[B\x1b[B2\x7f\r")
                    got = [next(keys) for _ in range(5)]
                    during = termios.tcgetattr(slave)
            self.assertEqual(got, ["down", "down", "2", "backspace", "enter"])
            self.assertFalse(during[3] & termios.ICANON)
            # BSD kernels set PENDIN whenever a terminal returns to line
            # mode; it is theirs, not a setting left behind.
            after = termios.tcgetattr(slave)
            pendin = getattr(termios, "PENDIN", 0)
            after[3] &= ~pendin
            before[3] &= ~pendin
            self.assertEqual(after, before)
        finally:
            os.close(master)
            os.close(slave)


class PickOneTests(unittest.TestCase):
    def setUp(self):
        style.set_colour(False)
        self.addCleanup(style.set_colour, None)
        patcher = mock.patch.dict(os.environ, {"COLUMNS": "100"})
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def row(i, current, pointer):
        return cli._menu_row(i + 1, ["stable", "developer", "nightly"][i].ljust(9),
                             ["155.0.1"], default=current, pointer=pointer)

    def pick(self, keys, default=0):
        out = io.StringIO()
        return prompt.pick_one(3, default, self.row, keys=keys, out=out), out

    def test_arrows_move_and_enter_takes(self):
        self.assertEqual(self.pick(["down", "down", "enter"])[0], 2)
        self.assertEqual(self.pick(["up", "enter"])[0], 2)            # wraps
        self.assertEqual(self.pick(["end", "home", "enter"])[0], 0)
        self.assertEqual(self.pick(["enter"], default=1)[0], 1)

    def test_a_typed_number_moves_the_pointer(self):
        self.assertEqual(self.pick(["3", "enter"])[0], 2)
        self.assertEqual(self.pick(["2", "9", "enter"])[0], 1)        # 29 is no choice
        self.assertEqual(self.pick(["3", "backspace", "enter"])[0], 2)
        self.assertEqual(self.pick(["x", "enter"])[0], 0)

    def test_the_scrollback_reads_like_a_typed_answer(self):
        _, out = self.pick(["down", "enter"])
        lines = screen(out)
        self.assertIn("  Which one? 1-3 [1] 2", lines)
        self.assertIn("›  2. developer  155.0.1", lines)
        self.assertNotIn("(Enter)", "\n".join(lines))

    def test_while_open_the_prompt_names_the_keys(self):
        out = io.StringIO()
        prompt.Frame(out).draw(["x"])
        _, out = self.pick(["down", "enter"])
        frames = out.getvalue().split("\r")
        self.assertIn("Which one? ↑↓ or 1-3 [2]", visible(frames[-2]))
        self.assertIn("(Enter)", visible(frames[-2]))

    def test_esc_cancels(self):
        with self.assertRaises(KeyboardInterrupt):
            self.pick(["down", "esc"])

    def test_the_pointer_does_not_move_the_columns(self):
        plain = visible(self.row(1, False, False))
        pointed = visible(self.row(1, False, True))
        self.assertEqual(len(plain), len(pointed))
        self.assertTrue(pointed.startswith("›"))


class PickManyTests(unittest.TestCase):
    NAMES = [f"sheet-{n}" for n in range(1, 13)]

    def setUp(self):
        style.set_colour(False)
        self.addCleanup(style.set_colour, None)
        patcher = mock.patch.dict(os.environ, {"COLUMNS": "60"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def pick(self, keys):
        out = io.StringIO()
        parse = lambda text: cli.scan_selection(text, len(self.NAMES))[0]
        answer = prompt.pick_many(self.NAMES, "Include which?", "e.g. 1,3 or all",
                                  parse, keys=keys, out=out)
        return answer, out

    def test_space_ticks_and_enter_answers_as_text(self):
        self.assertEqual(self.pick(["space", "down", "down", "space", "enter"])[0], "1,3")
        self.assertEqual(self.pick(["space", "space", "enter"])[0], "")
        self.assertEqual(self.pick(["enter"])[0], "")

    def test_typing_works_as_in_the_plain_prompt(self):
        self.assertEqual(self.pick(["1", ",", "3", "enter"])[0], "1,3")
        self.assertEqual(self.pick(["2", "0", "space", "4", "enter"])[0], "20 4")
        self.assertEqual(self.pick(["space", "a", "l", "l", "enter"])[0], "all")
        self.assertEqual(self.pick(["space", "4", "enter"])[0], "1,4")

    def test_left_and_right_cross_columns(self):
        rows = style.column_rows(self.NAMES, indent=4, numbered=True)
        answer, _ = self.pick(["right", "space", "enter"])
        self.assertEqual(answer, str(rows + 1))
        answer, _ = self.pick(["left", "space", "enter"])               # no column left of it
        self.assertEqual(answer, "1")

    def test_ticks_replace_the_dot_and_the_layout_holds(self):
        plain = style.columns(self.NAMES, indent=4, numbered=True)
        marked = [visible(l) for l in style.columns(self.NAMES, indent=4, numbered=True,
                                                    pointer=4, ticked={0, 4})]
        self.assertEqual([len(l) for l in plain], [len(l) for l in marked])
        self.assertTrue(marked[0].lstrip().startswith("1✓ sheet-1"))
        self.assertIn("›  5✓ sheet-5", "\n".join(marked))      # in the gutter

    def test_scrollback_shows_ticks_and_the_typed_equivalent(self):
        _, out = self.pick(["space", "down", "down", "space", "enter"])
        lines = screen(out)
        self.assertIn("  Include which? e.g. 1,3 or all [none] 1,3", lines)
        self.assertNotIn("›", "\n".join(lines))
        self.assertTrue(any("3✓ sheet-3" in line for line in lines))

    def test_esc_cancels(self):
        with self.assertRaises(KeyboardInterrupt):
            self.pick(["space", "esc"])


class FallbackTests(unittest.TestCase):
    """Anything short of a real, wide-enough terminal gets the typed prompt."""

    def test_opt_out_dumb_terminal_and_stand_in_streams(self):
        with mock.patch.dict(os.environ, {"FXCSS_PLAIN_PROMPTS": "1"}):
            self.assertFalse(prompt.arrows_ok())
        with mock.patch.dict(os.environ, {"FXCSS_PLAIN_PROMPTS": "", "TERM": "dumb"}):
            self.assertFalse(prompt.arrows_ok())
        with mock.patch.dict(os.environ, {"FXCSS_PLAIN_PROMPTS": "", "TERM": "xterm"}), \
                mock.patch.object(sys, "stdout", io.StringIO()):
            self.assertFalse(prompt.arrows_ok())

    def test_too_small_a_terminal(self):
        size = os.terminal_size((50, 40))
        with mock.patch.dict(os.environ, {"FXCSS_PLAIN_PROMPTS": "", "TERM": "xterm"}), \
                mock.patch.object(sys.stdin, "isatty", return_value=True), \
                mock.patch.object(sys.stdout, "isatty", return_value=True), \
                mock.patch("os.isatty", return_value=True), \
                mock.patch.object(sys.stdin, "fileno", return_value=0), \
                mock.patch.object(sys.stdout, "fileno", return_value=1), \
                mock.patch("os.get_terminal_size", return_value=size):
            self.assertFalse(prompt.arrows_ok())
            size = os.terminal_size((100, 12))
            with mock.patch("os.get_terminal_size", return_value=size):
                self.assertFalse(prompt.arrows_ok(height=20))
                self.assertTrue(prompt.arrows_ok(height=5) or os.name == "nt")

    def test_the_menu_uses_the_typed_prompt_when_arrows_are_off(self):
        with mock.patch.object(prompt, "arrows_ok", return_value=False), \
                mock.patch("builtins.input", return_value="2") as typed, \
                mock.patch("builtins.print"):
            index = cli._menu("Which one?", "3 found", 3, PickOneTests.row, 0)
        self.assertEqual(index, 1)
        typed.assert_called_once()

    def test_the_menu_uses_the_keys_when_arrows_are_on(self):
        with mock.patch.object(prompt, "arrows_ok", return_value=True), \
                mock.patch.object(prompt, "pick_one", return_value=2) as picked, \
                mock.patch("builtins.input") as typed, \
                mock.patch("builtins.print"):
            index = cli._menu("Which one?", "3 found", 3, PickOneTests.row, 0)
        self.assertEqual(index, 2)
        picked.assert_called_once()
        typed.assert_not_called()


class StopTests(unittest.TestCase):
    def test_an_interrupted_command_stops_without_a_traceback(self):
        def interrupted(args):
            raise KeyboardInterrupt
        err = io.StringIO()
        with mock.patch.object(cli, "cmd_doctor", interrupted), \
                mock.patch.object(sys, "stderr", err):
            self.assertEqual(cli.main(["doctor"]), 130)
        self.assertIn("Stopped.", err.getvalue())


if __name__ == "__main__":
    unittest.main()
