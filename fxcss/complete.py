#!/usr/bin/env python3
"""Shell completion for fxcss.

The shell scripts are deliberately thin: they hand the whole command line to
`fxcss __complete` and print whatever comes back. All the knowledge lives here,
in Python, where it can be unit-tested -- and the subcommands and flags are
read off the real parser, so a command added to the CLI is completable the
moment it exists rather than when someone remembers to update a shell script.

Two rules this module lives by, because completion runs on every Tab press:

  * never touch the network -- a Tab that pauses to talk to GitHub is worse
    than no completion at all;
  * never raise -- a traceback printed over someone's prompt is unforgivable,
    so the entry point swallows everything and offers nothing instead.
"""

from pathlib import Path

# Options whose value is a filename: better handled by the shell's own file
# completion than by anything invented here.
PATH_OPTIONS = frozenset({
    "--theme", "--out", "--base", "--head", "--keep", "--patch", "--shot",
    "--baseline", "--profile", "--config",
})

# Options taking a comma-separated list of the theme's optional stylesheets.
SHEET_OPTIONS = frozenset({"--with", "--variants", "--combo"})

SHELLS = ("bash", "zsh", "fish")


def _parser_bits():
    """(subcommand -> [option strings], subcommand -> help) from the real parser."""
    from .cli import build_parser
    parser = build_parser()
    subparsers = next(
        (a for a in parser._actions
         if isinstance(getattr(a, "choices", None), dict)), None)
    if subparsers is None:
        return {}, {}
    options, helps = {}, {}
    for name, sub in subparsers.choices.items():
        options[name] = sorted({o for action in sub._actions
                                for o in action.option_strings})
    for action in subparsers._get_subactions():
        helps[action.dest] = action.help or ""
    return options, helps


def subcommands():
    options, _ = _parser_bits()
    # Hidden plumbing: offering it would be noise, and nobody types it.
    return sorted(n for n in options if not n.startswith("__"))


def _theme_dir(words, cwd):
    """Which theme a --with/--variants value should be completed against.

    An explicit --theme wins. Otherwise `install`/`try` name the theme by
    positional argument, and when that is a local directory it is the theme --
    when it is owner/repo the sheets live on GitHub and completing would mean
    a network round trip, so nothing is offered.
    """
    for flag, following in zip(words, words[1:]):
        if flag == "--theme" and following:
            return Path(following).expanduser()
    for word in words[1:]:
        if word.startswith("-"):
            continue
        candidate = Path(word).expanduser()
        if candidate.is_dir():
            return candidate
    return cwd


def sheet_names(words, cwd):
    """Optional stylesheet slugs for the theme in play, or []."""
    from .core import find_variant_sheets
    directory = _theme_dir(words, cwd)
    try:
        if not directory or not Path(directory).is_dir():
            return []
        return sorted(find_variant_sheets(Path(directory)))
    except OSError:
        return []


def channel_names():
    from .core import CHANNEL_ALIASES, CHANNEL_ORDER
    return sorted(set(CHANNEL_ORDER) | set(CHANNEL_ALIASES))


def profile_names():
    """Names of the Firefox profiles on this machine, or []."""
    from .install import discover_profiles
    try:
        return sorted({p["name"] for p in discover_profiles() if p.get("name")})
    except (OSError, RuntimeError):
        return []


def _comma_aware(current, candidates):
    """Complete the last element of a comma-separated list.

    `--with a,b,` + Tab should offer `a,b,compact-tabs`, not `compact-tabs`:
    the shell replaces the whole word, so every candidate has to carry the
    part already typed. Values already chosen are dropped from the offer.
    """
    head, _, tail = current.rpartition(",")
    chosen = {c for c in head.split(",") if c} if head else set()
    prefix = head + "," if head or current.endswith(",") else ""
    return [prefix + c for c in candidates
            if c.startswith(tail) and c not in chosen]


def candidates(words, cword, cwd=None):
    """Completion candidates for a command line.

    `words` is the line split into words, `cword` the index the cursor is on
    (which may be one past the end, when the cursor sits after a space).
    Returning [] means "nothing to say" -- the shell falls back to filenames.
    """
    cwd = Path(cwd) if cwd is not None else Path.cwd()
    options, _ = _parser_bits()
    current = words[cword] if 0 <= cword < len(words) else ""
    previous = words[cword - 1] if cword >= 1 else ""

    # A value for the option just typed.
    if previous in PATH_OPTIONS:
        return []                                    # shell does filenames
    if previous == "--firefox":
        return [c for c in channel_names() if c.startswith(current)]
    if previous in SHEET_OPTIONS:
        return _comma_aware(current, sheet_names(words, cwd))

    command = next((w for w in words[1:cword] if not w.startswith("-")
                    and w in options), None)

    if command is None:
        if current.startswith("-"):
            return [o for o in ("--help", "--version") if o.startswith(current)]
        return [c for c in subcommands() if c.startswith(current)]

    if current.startswith("-"):
        return [o for o in options.get(command, []) if o.startswith(current)]

    if command == "completions":
        return [s for s in SHELLS if s.startswith(current)]
    if command in ("uninstall",):
        return [p for p in profile_names() if p.startswith(current)]
    return []


def complete_line(words, cword, cwd=None):
    """Entry point for `fxcss __complete`: never raises, never blocks."""
    try:
        return candidates(words, cword, cwd)
    except Exception:                                # noqa: BLE001 - see module docstring
        return []


BASH = r"""# fxcss bash completion.  eval "$(fxcss completions bash)"
_fxcss_complete() {
    local IFS=$'\n'
    COMPREPLY=($(fxcss __complete "$COMP_CWORD" "${COMP_WORDS[@]}" 2>/dev/null))
    # No candidates: fall back to filenames, which is what --theme/--out want.
    if [ ${#COMPREPLY[@]} -eq 0 ]; then
        COMPREPLY=($(compgen -f -- "${COMP_WORDS[COMP_CWORD]}"))
    fi
}
complete -o filenames -F _fxcss_complete fxcss
"""

ZSH = r"""# fxcss zsh completion.  eval "$(fxcss completions zsh)"
_fxcss_complete() {
    local -a candidates
    candidates=(${(f)"$(fxcss __complete $((CURRENT - 1)) "${words[@]}" 2>/dev/null)"})
    if (( ${#candidates} )); then
        compadd -- $candidates
    else
        _files
    fi
}
compdef _fxcss_complete fxcss
"""

FISH = r"""# fxcss fish completion.  fxcss completions fish | source
function __fxcss_complete
    set -l tokens (commandline -opc) (commandline -ct)
    fxcss __complete (math (count $tokens) - 1) $tokens 2>/dev/null
end
complete -c fxcss -f -a '(__fxcss_complete)'
"""


def script(shell):
    """The completion script for a shell, or None if it is not one we know."""
    return {"bash": BASH, "zsh": ZSH, "fish": FISH}.get(shell)
