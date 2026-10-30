#!/usr/bin/env python3
"""Install a theme into a real Firefox profile, and take it out again.

Everything else in fxcss works in throwaway profiles; this module is the one
deliberate exception. Because it touches a profile someone lives in, it is
built around three rules:

* **Nothing is destroyed.** The replacement is prepared before the existing
  chrome/ moves to a timestamped backup. Failed swaps restore the original,
  and user.js is only edited inside a clearly marked block.
* **Everything written is recorded.** A small manifest
  (``chrome/fxcss-install.json``) lists every file the install created, so
  uninstalling removes only unchanged files, preserving anything edited or
  added since and any file whose original contents cannot be verified.
* **No prompts here.** This module is pure filesystem logic; questions are the
  CLI's job, and the CLI never asks them in CI or without a terminal.

The install itself is what a theme's own install.sh does: copy chrome/ into
the profile, copy the optional sheets that were asked for, and make sure
``toolkit.legacyUserProfileCustomizations.stylesheets`` is true in **user.js**
(prefs.js is Firefox's own file and gets rewritten on exit; user.js is the
supported place for a value that must survive).
"""

import contextlib
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

MANIFEST_NAME = "fxcss-install.json"

# Bumped when the manifest's shape changes. Old manifests are still read --
# `read_manifest` normalises them -- but they are never silently rewritten:
# whatever wrote a manifest is the thing that gets to own its contents.
#
# 1: {theme, installed, backup, user_js_created, sheets, files}
# 2: adds `schema`, `fxcss`, `source` (the theme_id string taken apart so an
#    upgrade can act on it), `origin_backup`, `digests` and `user_js_block`.
#    Later additions are optional keys older readers ignore: `sheet_notes`
#    (optional-sheet references that could not be installed).
MANIFEST_SCHEMA = 2

BLOCK_BEGIN = "/* >>> fxcss install >>> */"
BLOCK_END = "/* <<< fxcss install <<< */"

STYLESHEET_PREF = (
    'user_pref("toolkit.legacyUserProfileCustomizations.stylesheets", true);')

# One pass over a stylesheet: comments and strings are consumed whole, so an
# @import or url() inside either is never mistaken for a live reference, and
# a "/*" inside a string never starts a comment.
CSS_TOKEN_RE = re.compile(r"""
    /\*.*?(?:\*/|\Z)
  | @import\s+(?:url\(\s*)?(?P<q1>["'])(?P<imp>(?:\\.|(?!(?P=q1))[^\\\n])*)(?P=q1)
  | @import\s+url\(\s*(?P<bimp>[^"'()\s]+)\s*\)
  | (?<![\w-])url\(\s*(?P<q2>["'])(?P<url>(?:\\.|(?!(?P=q2))[^\\\n])*)(?P=q2)\s*\)
  | (?<![\w-])url\(\s*(?P<burl>[^"'()\s]+)\s*\)
  | (?P<q3>["'])(?:\\.|(?!(?P=q3))[^\\\n])*(?:(?P=q3)|(?=\n)|\Z)
""", re.S | re.I | re.X)

# How much a selected optional sheet may pull in alongside itself. Generous
# for icon sets, small enough that a theme cannot fill a profile with files.
MAX_SHEET_DEPENDENCIES = 256
MAX_SHEET_DEPENDENCY_BYTES = 32 * 1024 * 1024


# --- finding profiles -------------------------------------------------------

def profile_roots():
    """Directories whose profiles.ini describes real Firefox profiles.

    Only roots that actually have a profiles.ini are returned, so on Linux the
    snap and flatpak locations cost nothing when they do not apply.
    FXCSS_PROFILE_ROOTS (os.pathsep-separated directories) extends the search,
    for a Firefox that keeps its profiles somewhere unusual -- the same idiom
    FXCSS_FIREFOX_ROOTS provides for binaries.
    """
    home = Path.home()
    if sys.platform == "darwin":
        candidates = [home / "Library" / "Application Support" / "Firefox"]
    elif os.name == "nt":
        appdata = os.environ.get("APPDATA")
        candidates = [Path(appdata) / "Mozilla" / "Firefox"] if appdata else []
    else:
        candidates = [
            home / ".mozilla" / "firefox",
            home / "snap" / "firefox" / "common" / ".mozilla" / "firefox",
            home / ".var" / "app" / "org.mozilla.firefox" / ".mozilla" / "firefox",
        ]
    env_roots = os.environ.get("FXCSS_PROFILE_ROOTS", "")
    candidates += [Path(r) for r in env_roots.split(os.pathsep) if r]
    return [root for root in candidates if (root / "profiles.ini").is_file()]


def parse_profiles_ini(text):
    """profiles.ini -> ([profile dict, ...], [install-section default, ...]).

    Hand-rolled rather than configparser: profiles.ini in the wild carries
    oddities (duplicate keys after a crashed migration, stray whitespace) that
    strict parsers turn into a crash on someone's real machine.

    Each profile dict keeps the raw Path= value; resolving it against the root
    is the caller's job, because only the caller knows the root.
    """
    sections = []
    current = None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith((";", "#")):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = (line[1:-1].strip(), {})
            sections.append(current)
            continue
        if current is None or "=" not in line:
            continue
        key, _, value = line.partition("=")
        current[1][key.strip().lower()] = value.strip()

    profiles, install_defaults = [], []
    for name, kv in sections:
        lowered = name.lower()
        if lowered.startswith("profile") and kv.get("path"):
            profiles.append({
                "section": name,
                "name": kv.get("name", ""),
                "path": kv["path"],
                "is_relative": kv.get("isrelative", "1") != "0",
                "old_default": kv.get("default") == "1",
            })
        elif lowered.startswith("install") and kv.get("default"):
            # Locked=1 marks the entry as claimed by one Firefox install;
            # for choosing where a theme goes it changes nothing.
            install_defaults.append(kv["default"])
    return profiles, install_defaults


def pick_default(profiles, install_defaults):
    """The profile Firefox itself would open, or None when that is a guess.

    [Install*] sections are how Firefox 67+ records each installation's
    profile, so they outrank the old-style Default=1 flag. Several install
    sections agreeing is still one answer; disagreeing ones mean the machine
    runs several Firefoxes into different profiles, and picking between them
    silently would install the theme somewhere the user did not mean.
    """
    matched = []
    for wanted in install_defaults:
        for profile in profiles:
            if profile["path"] == wanted and profile not in matched:
                matched.append(profile)
    if len(matched) == 1:
        return matched[0]
    if len(matched) > 1:
        return None
    flagged = [p for p in profiles if p["old_default"]]
    if len(flagged) == 1:
        return flagged[0]
    if len(profiles) == 1:
        return profiles[0]
    return None


# Suffixes Firefox itself gives the profile directories it creates. Only ones
# whose meaning is unambiguous are listed: a label here is shown to someone
# about to overwrite that profile's chrome/, so a confident-looking guess is
# worse than saying nothing. Longest match wins ("default-esr" before
# "default"), which is why this is an ordered tuple and not a dict.
PROFILE_SUFFIXES = (
    ("dev-edition-default", "Developer Edition"),
    ("default-esr", "ESR"),
    ("default-release", "Release"),
    ("default-nightly", "Nightly"),
    ("default", "Release (pre-67 layout)"),
)

# Where the profile root lives says which packaging of Firefox owns it. Only
# meaningful on Linux, where the same machine routinely has two or three.
PROFILE_ROOT_KINDS = (
    ("/snap/", "snap"),
    ("/.var/app/", "flatpak"),
)


def profile_kind(name, root=None):
    """Best-effort label for which Firefox a profile belongs to.

    Firefox names the directories it creates after the channel that made them
    (`<salt>.dev-edition-default`), so the suffix is the only honest signal
    available without launching anything. Returns "" when the name says
    nothing recognisable -- a profile someone named themselves is common, and
    inventing a channel for it would be a lie in the one place it matters.
    """
    label = ""
    text = str(name or "")
    for suffix, pretty in PROFILE_SUFFIXES:
        if text.endswith("." + suffix) or text == suffix:
            label = pretty
            break
    if root:
        rooted = str(root).replace(os.sep, "/")
        for fragment, packaging in PROFILE_ROOT_KINDS:
            if fragment in rooted:
                label = f"{label}, {packaging}" if label else packaging
                break
    return label


def discover_profiles(roots=None):
    """Real profiles on this machine, each with an absolute path.

    Returns [{name, path, root, default, kind}, ...]. At most one entry per
    root is marked default; a machine with several roots (Linux with both a
    distro and a snap Firefox) can legitimately have several, which the CLI
    treats as ambiguous rather than picking one. `kind` is a best-effort
    "Developer Edition" / "ESR" / "snap" style label, or "".
    """
    found = []
    for root in (profile_roots() if roots is None else roots):
        root = Path(root)
        ini = root / "profiles.ini"
        if not ini.is_file():
            continue
        profiles, install_defaults = parse_profiles_ini(
            ini.read_text(encoding="utf-8", errors="replace"))
        chosen = pick_default(profiles, install_defaults)
        for profile in profiles:
            path = Path(profile["path"])
            if profile["is_relative"]:
                path = root / profile["path"]
            found.append({
                "name": profile["name"] or profile["section"],
                "path": path,
                "root": root,
                "default": profile is chosen,
                # From the directory name, not the display name: Firefox lets
                # someone rename a profile to anything, but the directory it
                # created keeps the channel suffix.
                "kind": profile_kind(Path(profile["path"]).name, root),
            })
    return found


def match_profile(profiles, spec):
    """Resolve a --profile value: a directory path, or a name from the list."""
    as_path = Path(spec).expanduser()
    if as_path.is_dir():
        looks_real = any((as_path / marker).exists()
                         for marker in ("prefs.js", "compatibility.ini",
                                        "times.json"))
        if not looks_real:
            raise ValueError(
                f"{as_path} exists but does not look like a Firefox profile "
                "(no prefs.js, compatibility.ini or times.json in it)")
        return {"name": as_path.name, "path": as_path, "root": as_path.parent,
                "default": False}
    lowered = spec.lower()
    hits = [p for p in profiles
            if p["name"].lower() == lowered or p["path"].name.lower() == lowered]
    if len(hits) == 1:
        return hits[0]
    names = ", ".join(sorted(p["name"] for p in profiles)) or "none found"
    if not hits:
        raise ValueError(f"no profile named {spec!r}; profiles here: {names}")
    raise ValueError(f"{spec!r} matches more than one profile; pass its full "
                     "path instead (fxcss install --list-profiles shows them)")


def firefox_running(profile):
    """Best effort: does some Firefox hold this profile's lock right now?

    Lock files outlive a clean exit on every platform, so existence proves
    nothing; what matters is whether the lock is *held*. Worth only a warning
    either way -- Firefox reads userChrome.css at startup, so installing under
    a running instance simply does not show until a restart.
    """
    profile = Path(profile)
    if os.name == "nt":
        lock = profile / "parent.lock"
        if not lock.exists():
            return False
        try:  # Firefox holds it open with no sharing; touching it then fails.
            with open(lock, "ab"):
                pass
            return False
        except OSError:
            return True
    lock = profile / ".parentlock"
    if lock.exists():
        try:
            import fcntl
            with open(lock, "rb") as handle:
                fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
                fcntl.flock(handle, fcntl.LOCK_UN)
        except OSError:  # includes BlockingIOError: the exclusive lock is held
            return True
        except ImportError:
            pass
    symlink = profile / "lock"  # Linux: a dangling symlink to "ip:+pid"
    if symlink.is_symlink():
        pid = os.readlink(symlink).rsplit("+", 1)[-1]
        if pid.isdigit():
            try:
                os.kill(int(pid), 0)
                return True
            except ProcessLookupError:
                return False
            except (PermissionError, OSError):
                return True
    return False


# --- user.js ----------------------------------------------------------------

# user.js belongs to the person, not to fxcss, and is not guaranteed to be
# UTF-8 or to use \n line endings. It is read and written as bytes, decoded
# with surrogateescape so every byte outside the fxcss block survives exactly.

def read_prefs(path):
    """user.js as text that encodes back to the very same bytes."""
    return Path(path).read_bytes().decode("utf-8", "surrogateescape")


def write_prefs(path, text):
    Path(path).write_bytes(text.encode("utf-8", "surrogateescape"))


def _lines(text):
    """Lines split on \n alone, keeping it: the only terminator fxcss writes."""
    return re.findall(r"[^\n]*\n|[^\n]+\Z", text)


def strip_user_js_block(text):
    """Remove every well-formed fxcss block, touching nothing else.

    `user_js_with_block` joins the block on with exactly one "\n": a blank
    line after text that already ended in one, otherwise the terminator the
    last line lacked. Taking that one character back too is what makes
    install-then-uninstall leave user.js byte-for-byte as it was. (The lacked
    terminator stays when the person has since added lines after the block.)

    A BEGIN marker without its END is left completely alone: deleting to the
    end of the file could take a line the user wrote, and a duplicated pref is
    harmless where a lost one is not.
    """
    out = []
    lines = _lines(text)
    i = 0
    while i < len(lines):
        if lines[i].strip() == BLOCK_BEGIN:
            for j in range(i + 1, len(lines)):
                if lines[j].strip() == BLOCK_END:
                    # Older Windows installs wrote the separator and markers
                    # with CRLF. Match the marker's ending so a new LF block
                    # after a user's lone CR still preserves that CR.
                    separator = "\r\n" if lines[i].endswith("\r\n") else "\n"
                    if out and out[-1] == separator:
                        out.pop()
                    elif (separator == "\n" and out and out[-1].endswith("\n")
                          and j + 1 == len(lines)):
                        # Only at the end: with lines after the block, joining
                        # two lines could let a // comment swallow a pref.
                        out[-1] = out[-1][:-1]
                    i = j + 1
                    break
            else:
                out.append(lines[i])
                i += 1
            continue
        out.append(lines[i])
        i += 1
    return "".join(out)


_MARKER_LINE = "/* (an fxcss install marker from the theme was removed here) */"


def sanitize_block_body(body):
    """`body` with no line that `strip_user_js_block` would read as a marker.

    A theme's configuration/user.js is untrusted. A marker line in it would
    end the block early -- leaving the theme's later prefs behind on
    uninstall, and stacking another copy on every reinstall -- or open a
    block that never closes. Only whole marker lines are replaced; a marker
    line holds no pref, so every pref the theme meant to set is kept.
    """
    return "".join(
        line.replace(line.strip(), _MARKER_LINE)
        if line.strip() in (BLOCK_BEGIN, BLOCK_END) else line
        for line in _lines(body))


def user_js_with_block(existing, body):
    """user.js text with exactly one fxcss block holding `body` at the end."""
    base = strip_user_js_block(existing)
    if base:
        base += "\n"  # undone exactly by strip_user_js_block
    body = sanitize_block_body(body).rstrip("\n")
    return base + BLOCK_BEGIN + "\n" + body + "\n" + BLOCK_END + "\n"


def user_js_body(theme_root):
    """What goes inside the block: the pref that turns userChrome.css on,
    plus whatever configuration/user.js the theme ships."""
    body = STYLESHEET_PREF
    shipped = Path(theme_root) / "configuration" / "user.js"
    if shipped.is_file():
        body += "\n\n" + shipped.read_text(encoding="utf-8",
                                           errors="replace").rstrip("\n")
    # Sanitised here too, so the manifest records the block as written.
    return sanitize_block_body(body)


PREF_NAME_RE = re.compile(
    r"""\b(?:user_pref|pref|sticky_pref|lockPref)\(\s*(["'])(.+?)\1""")


def theme_pref_names(theme_root):
    """Names of the prefs a theme's configuration/user.js sets, in order.

    Shown before install so nobody has their preferences changed by a theme
    without being told which ones. Best effort -- the block is installed as
    shipped either way.
    """
    shipped = Path(theme_root) / "configuration" / "user.js"
    if not shipped.is_file():
        return []
    code = _strip_js_comments(shipped.read_text(encoding="utf-8",
                                                errors="replace"))
    names = []
    for match in PREF_NAME_RE.finditer(code):
        name = _printable(match.group(2))
        if name not in names:
            names.append(name)
    return names


def _printable(text):
    """Theme-supplied text safe to print: escapes shown, never replayed."""
    return text if text.isprintable() else ascii(text)[1:-1]


def _strip_js_comments(text):
    """JavaScript text with comments removed and strings left as they were."""
    return re.sub(r"""(/\*.*?(?:\*/|\Z)|//[^\n]*)|("(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*')""",
                  lambda m: m.group(2) or " ", text, flags=re.S)


# --- optional sheets --------------------------------------------------------

def css_references(text):
    """(kind, target) for each live @import ("import") and url() ("url").

    Commented-out references are skipped and strings are respected, but CSS
    escapes inside a target are not decoded.
    """
    for match in CSS_TOKEN_RE.finditer(text):
        for kind, group in (("import", "imp"), ("import", "bimp"),
                            ("url", "url"), ("url", "burl")):
            if match.group(group) is not None:
                yield kind, match.group(group)
                break


def _local_reference(target):
    """The relative file path a url()/@import names, or None.

    Anything absolute -- a scheme (chrome://, data:, file://, https:), a
    leading slash, a drive letter -- or a bare #fragment resolves the same
    wherever the sheet sits, so only relative paths need carrying along.
    """
    from urllib.parse import unquote

    target = target.strip()
    if not target or target.startswith(("#", "/", "\\")) \
            or re.match(r"^[A-Za-z][\w+.-]*:", target):
        return None
    return unquote(re.split(r"[?#]", target, maxsplit=1)[0]) or None


def _inside(path, root):
    try:
        Path(path).relative_to(root)
        return True
    except ValueError:
        return False


def _copy_sheet_dependencies(sheet, dest, chrome, source_root, budget):
    """Bring the local files `sheet` refers to along to `dest`.

    `fxcss try` loads an optional sheet where it was unpacked, so its
    relative url()s and nested @imports resolve against the theme. Installed,
    the sheet sits at `dest` instead; each file it reaches is copied to the
    same relative place from there, recursively through nested sheets, so the
    installed sheet sees what the preview saw.

    Bounded and confined: only regular files inside `source_root` are read,
    only paths inside the staged chrome/ are written, a file the theme itself
    installed there is never overwritten, and `budget` caps how much one
    install can copy. A reference to a missing file is skipped silently --
    Firefox ignores it in the preview too. Everything else that cannot be
    carried along is returned as a note for the caller to show.
    """
    notes = []
    queue = [(Path(sheet), Path(dest))]
    seen = {(Path(sheet).resolve(), Path(dest))}
    while queue:
        origin_sheet, placed_sheet = queue.pop(0)
        text = origin_sheet.read_text(encoding="utf-8", errors="replace")
        for kind, target in css_references(text):
            relative = _local_reference(target)
            if relative is None:
                continue
            origin = Path(os.path.normpath(origin_sheet.parent / relative))
            placed = Path(os.path.normpath(placed_sheet.parent / relative))
            if not origin.exists():
                continue
            resolved = origin.resolve()
            label = _printable(f"{origin_sheet.name}: {target.strip()}")
            if not _inside(resolved, source_root):
                notes.append(f"{label} (outside the theme, not copied)")
                continue
            if not resolved.is_file() or (resolved, placed) in seen:
                continue
            if not _inside(placed, chrome) or placed == chrome / MANIFEST_NAME:
                notes.append(f"{label} (would land outside chrome/, not copied)")
                continue
            seen.add((resolved, placed))
            if placed.exists():
                if not placed.is_file() or \
                        placed.read_bytes() != resolved.read_bytes():
                    notes.append(
                        f"{label} (the theme's own chrome/"
                        f"{placed.relative_to(chrome).as_posix()} kept)")
                elif kind == "import" or placed.suffix.lower() == ".css":
                    queue.append((origin, placed))
                continue
            budget["files"] -= 1
            budget["bytes"] -= resolved.stat().st_size
            if budget["files"] < 0 or budget["bytes"] < 0:
                raise RuntimeError(
                    f"optional sheet {Path(sheet).name} refers to more local "
                    f"files than fxcss will copy ({MAX_SHEET_DEPENDENCIES} "
                    f"files, {MAX_SHEET_DEPENDENCY_BYTES // (1024 * 1024)} MB)")
            try:
                placed.parent.mkdir(parents=True, exist_ok=True)
            except (FileExistsError, NotADirectoryError):
                notes.append(f"{label} (a file of the theme's is in the way, "
                             "not copied)")
                continue
            shutil.copy2(resolved, placed)
            if kind == "import" or placed.suffix.lower() == ".css":
                queue.append((origin, placed))
    return notes


def variant_destination(chrome_dir, filename):
    """Where an optional sheet belongs inside an installed chrome/ tree.

    Themes like WhiteSur ship a stylesheet that already @imports
    ``custom/<name>.css`` and expect their installer to copy the chosen sheet
    to that spot -- the import of a missing file fails silently, which is the
    whole on/off mechanism. Honour it: find the import that names this sheet
    and return the path it resolves to. None when no stylesheet asks for it,
    or when the import points outside chrome/ (an @import cannot be allowed
    to direct a write elsewhere in the profile).
    """
    chrome_dir = Path(chrome_dir)
    root = chrome_dir.resolve()
    needle = filename.lower()
    for css in sorted(chrome_dir.rglob("*.css")):
        text = css.read_text(encoding="utf-8", errors="replace")
        for kind, target in css_references(text):
            target = target.strip()
            if kind != "import" or not target:
                continue
            if not (target.lower().endswith("/" + needle)
                    or target.lower() == needle):
                continue
            dest = (css.parent / target).resolve()
            try:
                dest.relative_to(root)
            except ValueError:
                continue
            return chrome_dir / dest.relative_to(root)
    return None


# --- describing what got installed ------------------------------------------

THEME_ID_RE = re.compile(r"^([\w.-]+)/([\w.-]+)@(.+)$")


def parse_theme_id(theme_id):
    """Take a `theme` string apart into a source dict.

    Schema-1 manifests recorded only ``owner/name@ref`` (or a local directory
    path), which is enough to *show* someone what they installed but not
    enough to act on: re-fetching needs to know whether ``main`` was a branch
    being tracked or a tag that happens to be named that. Everything that
    cannot be recovered from the string is reported as "unknown" rather than
    assumed -- an upgrade that guesses wrong here re-points someone's install
    at a different ref.

    Returns {kind, owner, name, ref, ref_kind, resolved, path}.
    """
    blank = {"kind": "unknown", "owner": None, "name": None, "ref": None,
             "ref_kind": "unknown", "resolved": None, "path": None}
    text = str(theme_id or "").strip()
    if not text:
        return blank
    match = THEME_ID_RE.match(text)
    if match:
        owner, name, ref = match.groups()
        return {"kind": "github", "owner": owner, "name": name, "ref": ref,
                "ref_kind": "unknown", "resolved": ref, "path": None}
    if os.path.isabs(text) or text.startswith((".", "~")):
        return dict(blank, kind="local", path=text)
    return blank


def _digest(path):
    """sha256 of one file, or None when it cannot be read.

    Unreadable is not an error worth failing an install over: the digest is
    there to answer "has this changed since?", and a file that cannot be read
    now simply has no answer.
    """
    try:
        hasher = hashlib.sha256()
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(131072), b""):
                hasher.update(block)
        return hasher.hexdigest()
    except OSError:
        return None


def local_changes(profile, manifest=None):
    """Every file that differs from the version the manifest names.

    Two ways a file can get there. It was edited after fxcss installed it --
    which `drift` sees, by comparing against the digests recorded then. Or it
    already differed when `fxcss adopt` found the theme, which no digest can
    show, because adoption records the files as they were rather than as the
    release has them; that comparison is recorded at adopt time instead.

    Both mean the same thing to an upgrade -- work here that overwriting
    would destroy -- so they are answered together.
    """
    profile = Path(profile)
    manifest = read_manifest(profile) if manifest is None else manifest
    moved = drift(profile, manifest)
    changed = set(moved["modified"])
    adopted = (manifest or {}).get("adopted")
    if isinstance(adopted, dict):
        for relative in adopted.get("differing") or []:
            path = Path("chrome") / str(relative)
            if (profile / path).is_file():
                changed.add(path.as_posix())
    return sorted(changed)


def drift(profile, manifest=None):
    """What has changed in chrome/ since the install recorded in the manifest.

    Returns {modified, missing, extra, checked}. `extra` is every file in
    chrome/ the manifest does not list -- which is how a hand-added sheet
    shows up, and why `uninstall` never deletes by wildcard.

    A schema-1 manifest carries no digests, so nothing can be said about
    modification; `checked` is 0 in that case and `modified` is empty. That is
    a real "don't know", and callers must not render it as "unchanged".
    """
    profile = Path(profile)
    manifest = read_manifest(profile) if manifest is None else manifest
    result = {"modified": [], "missing": [], "extra": [], "checked": 0}
    if not manifest:
        return result

    digests = manifest.get("digests")
    digests = digests if isinstance(digests, dict) else {}
    recorded = set()
    for path in _manifest_paths(profile, manifest):
        relative = path.relative_to(profile).as_posix()
        recorded.add(relative)
        if not path.is_file():
            result["missing"].append(relative)
            continue
        expected = digests.get(relative)
        if not isinstance(expected, str):
            continue
        result["checked"] += 1
        if _digest(path) != expected:
            result["modified"].append(relative)

    chrome = profile / "chrome"
    if chrome.is_dir():
        for path in chrome.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(profile).as_posix()
            if relative not in recorded and path.name != MANIFEST_NAME:
                result["extra"].append(relative)
    for key in ("modified", "missing", "extra"):
        result[key].sort()
    return result


# --- install ----------------------------------------------------------------

def _timestamp():
    return time.strftime("%Y%m%d%H%M%S")


# `None` is a real answer for origin_backup -- a profile with no chrome/ at
# all when fxcss first arrived -- so "not passed" needs its own value.
_UNSET = object()


def _spare_name(profile, base):
    """`base`, or `base-2`, `-3`… when two runs land in the same second."""
    candidate = profile / base
    counter = 2
    while candidate.exists():
        candidate = profile / f"{base}-{counter}"
        counter += 1
    return candidate


def adopt_theme(profile, theme_id, source, match, stamp=None):
    """Record a theme someone else installed, so fxcss can act on it.

    Nothing is installed and nothing is replaced: the chrome/ already there
    is *copied* to a backup and then described in a manifest, so that
    `uninstall` and `rollback` have somewhere to put things back to. The copy
    is why this is safe to run on a profile someone cares about.

    user.js is deliberately left alone. The pref that turns userChrome.css on
    is evidently already set -- the theme is working -- and writing an fxcss
    block to say so again would edit a file for no gain. The next upgrade
    writes one properly, from the theme it fetches.
    """
    from . import __version__

    profile = Path(profile)
    chrome = profile / "chrome"
    if not (chrome / "userChrome.css").is_file():
        raise RuntimeError(f"no chrome/userChrome.css in {profile}")
    if (chrome / MANIFEST_NAME).exists():
        raise RuntimeError("fxcss already manages this profile")

    backup = _spare_name(profile, f"chrome.backup-{stamp or _timestamp()}")
    shutil.copytree(chrome, backup)

    files = sorted(p.relative_to(profile).as_posix()
                   for p in chrome.rglob("*")
                   if p.is_file() and ".git" not in p.relative_to(chrome).parts)
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "fxcss": __version__,
        "theme": str(theme_id),
        "source": dict(source),
        "installed": time.strftime("%Y-%m-%d %H:%M:%S"),
        "adopted": dict(match),
        "backup": backup.name,
        "origin_backup": backup.name,
        "user_js_created": False,
        "sheets": [],
        "files": files,
        # The files as they are now, so anything changed from here shows as
        # drift. How far they already were from the release is in `adopted`.
        "digests": {name: digest for name, digest in
                    ((name, _digest(profile / name)) for name in files)
                    if digest},
    }
    (chrome / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    return manifest


def _sheet_source_root(theme_root, sheet):
    """The directory a sheet's dependencies may be read from.

    Optional sheets live beside the theme's chrome/, in the repository it was
    unpacked from, so that is their common ancestor. A sheet with nothing in
    common with the theme but the filesystem root gets only its own folder.
    """
    sheet_dir = Path(sheet).resolve().parent
    common = Path(os.path.commonpath([Path(theme_root).resolve(), sheet_dir]))
    return sheet_dir if common == Path(common.anchor) else common


def _prepare_chrome(theme_root, chrome, sheets, notes=None):
    """Build a complete replacement in staging, without touching live files.

    Notes about optional-sheet references that could not be carried along
    are appended to `notes`.
    """
    from urllib.parse import quote
    from .core import layer_stylesheets

    shutil.copytree(theme_root / "chrome", chrome)
    # Some themes @import customChrome.css without shipping it; an empty file
    # keeps the import resolving (same courtesy core.build_profile extends).
    placeholder = chrome / "customChrome.css"
    if not placeholder.exists() and "customChrome.css" in \
            (chrome / "userChrome.css").read_text(encoding="utf-8",
                                                  errors="replace"):
        placeholder.write_text("/* placeholder created by fxcss */\n",
                               encoding="utf-8")
    installed_sheets, extra_imports = [], []
    budget = {"files": MAX_SHEET_DEPENDENCIES,
              "bytes": MAX_SHEET_DEPENDENCY_BYTES}
    for sheet in sheets:
        sheet = Path(sheet)
        dest = variant_destination(chrome, sheet.name)
        if dest is None:
            # No import site exists: load it after the base through a wrapper.
            dest = chrome / "custom" / sheet.name
            extra_imports.append(quote(dest.relative_to(chrome).as_posix()))
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sheet, dest)
        installed_sheets.append(sheet.stem)
        found = _copy_sheet_dependencies(
            sheet, dest, chrome, _sheet_source_root(theme_root, sheet), budget)
        if notes is not None:
            notes.extend(found)
    layer_stylesheets(chrome / "userChrome.css", extra_imports)
    return installed_sheets


def install_theme(theme_root, profile, theme_id, sheets=(), stamp=None,
                  source=None, origin_backup=_UNSET):
    """Prepare a theme, then swap it into the profile with a way back.

    All copies, optional imports, prefs and the manifest are prepared on the
    profile's filesystem before its active chrome/ moves. If a swap fails,
    restore the previous chrome/. user.js is replaced atomically last, so a
    failure before that point leaves the original prefs unchanged.

    `origin_backup` is carried through upgrades, including None for a profile
    that originally had no chrome/. `source` records how to fetch the theme
    again; older callers can still supply that information in `theme_id`.
    """
    from . import __version__

    theme_root, profile = Path(theme_root), Path(profile)
    if not (theme_root / "chrome" / "userChrome.css").is_file():
        raise RuntimeError(f"no chrome/userChrome.css under {theme_root}")
    if not profile.is_dir():
        raise RuntimeError(f"profile directory does not exist: {profile}")

    active = profile / "chrome"
    user_js = profile / "user.js"
    previous = read_manifest(profile)
    backup = (_spare_name(profile, f"chrome.backup-{stamp or _timestamp()}").name
              if active.exists() or active.is_symlink() else None)
    try:
        with tempfile.TemporaryDirectory(prefix=".fxcss-install-", dir=profile) as td:
            staging = Path(td)
            chrome = staging / "chrome"
            sheet_notes = []
            installed_sheets = _prepare_chrome(theme_root, chrome, sheets,
                                               sheet_notes)
            created = not user_js.exists()
            existing = "" if created else read_prefs(user_js)
            block = user_js_body(theme_root)
            staged_prefs = staging / "user.js"
            if not created:
                shutil.copy2(user_js, staged_prefs)
            write_prefs(staged_prefs, user_js_with_block(existing, block))
            # Whether fxcss created user.js is part of the original state,
            # not something an upgrade should reset just because it exists.
            if origin_backup is not _UNSET and previous:
                created = previous.get("user_js_created", created)
            files = sorted(p.relative_to(staging).as_posix()
                           for p in chrome.rglob("*")
                           if p.is_file() and p != chrome / MANIFEST_NAME)
            manifest = {
                "schema": MANIFEST_SCHEMA,
                "fxcss": __version__,
                "theme": str(theme_id),
                "source": dict(source) if source else parse_theme_id(theme_id),
                "installed": time.strftime("%Y-%m-%d %H:%M:%S"),
                "backup": backup,
                "origin_backup": backup if origin_backup is _UNSET else origin_backup,
                "user_js_created": created,
                "user_js_block": block,
                "sheets": installed_sheets,
                # References in those sheets that could not be installed.
                "sheet_notes": sheet_notes,
                "files": files,
                "digests": {name: digest for name, digest in
                            ((name, _digest(staging / name)) for name in files)
                            if digest},
            }
            (chrome / MANIFEST_NAME).write_text(
                json.dumps(manifest, indent=1) + "\n", encoding="utf-8")

            moved_original = activated = False
            try:
                if backup:
                    active.rename(profile / backup)
                    moved_original = True
                chrome.rename(active)
                activated = True
                staged_prefs.replace(user_js)
            except BaseException:
                # Also restore the profile if Ctrl-C interrupts the swap.
                try:
                    if activated:
                        active.rename(chrome)
                    if moved_original:
                        (profile / backup).rename(active)
                except OSError as exc:
                    recovery = (f"the previous theme is kept at {profile / backup}"
                                if moved_original else
                                f"the incomplete replacement is at {active}")
                    raise RuntimeError(
                        f"install failed and could not restore chrome/; {recovery}"
                    ) from exc
                raise
            return manifest
    except OSError as exc:
        raise RuntimeError(f"could not install theme into {profile}: {exc}") from exc


# --- uninstall --------------------------------------------------------------

def read_manifest(profile):
    """The manifest a previous install left, normalised to the current shape.

    Returns None when there is none to read. A manifest written by an older
    fxcss is filled out here rather than on disk, so every caller sees one
    shape and nothing rewrites a file it did not author.

    Untrusted input throughout -- it sits in a directory anything can edit --
    so each field is only accepted when it has the right type. A manifest
    someone has mangled degrades to "fxcss cannot say", never to a confident
    wrong answer.
    """
    return _read_manifest(Path(profile) / "chrome" / MANIFEST_NAME)


def _read_manifest(path):
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    if not isinstance(data, dict):
        return None

    schema = data.get("schema")
    data["schema"] = schema if isinstance(schema, int) else 1
    source = data.get("source")
    if not isinstance(source, dict) or not source.get("kind"):
        data["source"] = parse_theme_id(data.get("theme"))
    if "origin_backup" not in data:
        # Nothing before schema 2 upgraded in place, so the only backup there
        # has ever been is the one this install made.
        data["origin_backup"] = data.get("backup")
    for key, kind in (("sheets", list), ("files", list), ("digests", dict)):
        if not isinstance(data.get(key), kind):
            data[key] = kind()
    return data


def _manifest_paths(profile, data):
    """The manifest's file list, refusing anything that strays.

    The manifest sits in a directory a theme (or anything else) could have
    edited, so it is untrusted input: only plain relative paths that stay
    under chrome/ are honoured.
    """
    safe = []
    for entry in data.get("files", []):
        if not isinstance(entry, str):
            continue
        parts = Path(entry).parts
        if not parts or parts[0] != "chrome" or ".." in parts \
                or Path(entry).is_absolute():
            continue
        candidate = Path(profile).joinpath(*parts)
        # A symlink in a managed path must never lead removal outside chrome/.
        if any(parent.is_symlink() for parent in (candidate, *candidate.parents)
               if parent != Path(profile) and Path(profile) in parent.parents):
            continue
        safe.append(candidate)
    return safe


def safe_backup_name(recorded):
    """A manifest's backup name, or None if it is not one fxcss may restore.

    Untrusted like the rest of the manifest: a backup name is turned into a
    path that gets moved over chrome/, so only a direct ``chrome.backup-*``
    child of the profile is ever honoured -- never a traversal, never an
    absolute path, never a sibling directory with an interesting name.
    """
    if not recorded or not isinstance(recorded, str):
        return None
    if Path(recorded).name != recorded or not recorded.startswith(
            "chrome.backup-"):
        return None
    return recorded


def _backup_key(name):
    """Sort key for a chrome.backup-* name: (stamp, spare-copy counter).

    `_spare_name` appends -2, -3 … when two runs land in the same second, and
    those sort wrongly as text once there are ten of them. Ordering decides
    which backup "the last one" means, so it is worth getting right.
    """
    rest = name[len("chrome.backup-"):]
    stamp, _, counter = rest.partition("-")
    return (stamp, int(counter) if counter.isdigit() else 1)


def list_backups(profile):
    """Every chrome.backup-* in this profile, newest first.

    Each entry carries the manifest found inside it, when there is one: the
    manifest lives in chrome/, so a backup taken by an upgrade holds the
    manifest of the version inside it and can say what it is. The one that
    never has a manifest is the oldest -- the user's own chrome/ from before
    fxcss ever ran here -- and naming that one correctly is the point.

    Returns [{name, path, key, manifest, theme}], newest first.
    """
    profile = Path(profile)
    found = []
    for path in profile.glob("chrome.backup-*"):
        if not path.is_dir() or not safe_backup_name(path.name):
            continue
        manifest = None
        candidate = path / MANIFEST_NAME
        if candidate.is_file():
            try:
                loaded = json.loads(candidate.read_text(encoding="utf-8"))
                manifest = loaded if isinstance(loaded, dict) else None
            except (ValueError, OSError):
                manifest = None
        found.append({
            "name": path.name,
            "path": path,
            "key": _backup_key(path.name),
            "manifest": manifest,
            "theme": (manifest or {}).get("theme"),
        })
    return sorted(found, key=lambda b: b["key"], reverse=True)


class _RecoveryError(RuntimeError):
    """The transaction's working directory must survive for manual recovery."""


_REMOVE_PREFS = object()


@contextlib.contextmanager
def _profile_work(profile, operation):
    work = Path(tempfile.mkdtemp(prefix=f".fxcss-{operation}-", dir=profile))
    preserve = False
    try:
        yield work
    except _RecoveryError:
        preserve = True
        raise
    except OSError as exc:
        raise RuntimeError(f"could not {operation} theme in {profile}: {exc}") from exc
    finally:
        if not preserve:
            shutil.rmtree(work, ignore_errors=True)


def _stage_prefs(work, user_js, text):
    staged = work / "user.js"
    if user_js.is_file():
        shutil.copy2(user_js, staged)
    write_prefs(staged, text)
    return staged


def _clean_prefs(work, profile, manifest):
    user_js = profile / "user.js"
    if not user_js.is_file():
        return None
    text = read_prefs(user_js)
    stripped = strip_user_js_block(text)
    if stripped == text:
        return None
    if not stripped.strip() and (manifest or {}).get("user_js_created"):
        return _REMOVE_PREFS
    return _stage_prefs(work, user_js, stripped)


def _swap_profile(profile, replacement, outgoing, prefs, work):
    """Activate prepared files; preferences are the final atomic operation.

    Every moved directory retains its original name until the entire operation
    succeeds. If restoring either directory fails, retain staging as well.
    """
    active = profile / "chrome"
    moved = activated = False
    try:
        if active.exists() or active.is_symlink():
            active.rename(outgoing)
            moved = True
        if replacement is not None:
            replacement.rename(active)
            activated = True
        if prefs is _REMOVE_PREFS:
            (profile / "user.js").unlink()
        elif prefs is not None:
            prefs.replace(profile / "user.js")
    except BaseException:
        try:
            if activated:
                active.rename(replacement)
            if moved:
                outgoing.rename(active)
        except BaseException as exc:
            raise _RecoveryError(
                f"could not restore profile after failure; recovery files are "
                f"preserved at {work} and {outgoing}") from exc
        raise


def rollback_to(profile, backup, stamp=None):
    """Restore a backup and its preferences, undoing failed swaps or Ctrl-C."""
    profile = Path(profile)
    name = safe_backup_name(backup)
    if not name:
        raise RuntimeError(f"{backup!r} is not a backup name fxcss will "
                           "restore; it must be a chrome.backup-* directory "
                           "in the profile itself")
    target = profile / name
    if not target.is_dir() or target.is_symlink():
        raise RuntimeError(f"no such backup directory in {profile}: {name}")
    outgoing = read_manifest(profile)
    restored = _read_manifest(target / MANIFEST_NAME)
    moved = _spare_name(profile, f"chrome.backup-{stamp or _timestamp()}")
    summary = {"restored": name, "moved_aside": None,
               "theme": (restored or {}).get("theme"), "user_js": "untouched",
               "was": (outgoing or {}).get("theme")}
    with _profile_work(profile, "rollback") as work:
        prefs = None
        block = (restored or {}).get("user_js_block")
        if restored is None:
            prefs = _clean_prefs(work, profile, outgoing)
            if prefs is not None:
                summary["user_js"] = "removed"
        elif isinstance(block, str):
            user_js = profile / "user.js"
            existing = read_prefs(user_js) if user_js.is_file() else ""
            prefs = _stage_prefs(work, user_js, user_js_with_block(existing, block))
            summary["user_js"] = "restored"
        else:
            summary["user_js"] = "unknown"
        if (profile / "chrome").exists() or (profile / "chrome").is_symlink():
            summary["moved_aside"] = moved.name
        _swap_profile(profile, target, moved, prefs, work)
    return summary


def prune_backups(profile, keep, protect=()):
    """Delete all but the newest `keep` backups. Returns the names removed.

    Backups are whole copies of a theme, so a profile upgraded weekly grows
    without this. Names in `protect` are neither pruned nor counted towards
    `keep` -- that is how the origin backup survives any number of upgrades
    without using up the allowance the caller asked for. `keep=None` prunes
    nothing at all.
    """
    if keep is None:
        return []
    protected = {name for name in protect if name}
    candidates = [b for b in list_backups(profile)
                  if b["name"] not in protected]
    removed = []
    for backup in candidates[max(keep, 0):]:
        shutil.rmtree(backup["path"], ignore_errors=True)
        if not backup["path"].exists():
            removed.append(backup["name"])
    return removed


def _prune_empty_dirs(chrome):
    for directory in sorted((p for p in chrome.rglob("*") if p.is_dir()),
                            key=lambda p: len(p.parts), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            pass
    try:
        chrome.rmdir()
    except OSError:
        pass


def uninstall_theme(profile, stamp=None):
    """Prepare removal without touching active files, then swap atomically.

    Only unchanged, digest-verified files are removed. Added, edited and
    unverifiable files remain, and block restoration of the origin backup.
    A failed swap restores both directories and leaves preferences unchanged.
    """
    profile = Path(profile)
    chrome = profile / "chrome"
    manifest = read_manifest(profile)
    backups = [b for b in list_backups(profile) if not b["path"].is_symlink()]
    stamp = stamp or _timestamp()
    summary = {"removed": 0, "restored": None, "moved_aside": None,
               "kept": [], "theme": manifest.get("theme") if manifest else None}
    if not manifest and not backups:
        raise RuntimeError(
            f"nothing fxcss installed in {profile}: no "
            f"chrome/{MANIFEST_NAME} and no chrome.backup-* to restore")
    with _profile_work(profile, "uninstall") as work:
        prefs = _clean_prefs(work, profile, manifest)
        if prefs is not None:
            summary["user_js_cleaned"] = True
        outgoing = work / "previous"
        if manifest:
            # Preserve links as links; never traverse them while removing files.
            shutil.copytree(chrome, work / "chrome", symlinks=True)
            staged = work / "chrome"
            for path in _manifest_paths(work, manifest):
                if path.is_file():
                    expected = manifest["digests"].get(path.relative_to(work).as_posix())
                    if isinstance(expected, str) and _digest(path) == expected:
                        path.unlink()
                        summary["removed"] += 1
            (staged / MANIFEST_NAME).unlink()
            _prune_empty_dirs(staged)
            replacement = staged if staged.exists() else None
            if replacement:
                summary["kept"] = sorted(
                    p.relative_to(work).as_posix()
                    for p in staged.rglob("*") if p.is_file() or p.is_symlink())
            recorded = safe_backup_name(manifest.get("origin_backup"))
            if replacement is None and recorded:
                backup = profile / recorded
                if backup.is_dir() and not backup.is_symlink():
                    replacement = backup
                    summary["restored"] = recorded
        else:
            replacement = backups[0]["path"]
            summary["restored"] = replacement.name
            outgoing = _spare_name(profile, f"chrome.removed-{stamp}")
            if chrome.exists() or chrome.is_symlink():
                summary["moved_aside"] = outgoing.name
        _swap_profile(profile, replacement, outgoing, prefs, work)
    return summary
