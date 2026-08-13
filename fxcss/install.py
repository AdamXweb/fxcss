#!/usr/bin/env python3
"""Install a theme into a real Firefox profile, and take it out again.

Everything else in fxcss works in throwaway profiles; this module is the one
deliberate exception. Because it touches a profile someone lives in, it is
built around three rules:

* **Nothing is destroyed.** An existing chrome/ folder is moved aside to a
  timestamped ``chrome.backup-*`` sibling before the theme is copied in, and
  user.js is only ever edited inside a clearly marked block.
* **Everything written is recorded.** A small manifest
  (``chrome/fxcss-install.json``) lists every file the install created, so
  uninstalling removes exactly those files and nothing a user added since.
* **No prompts here.** This module is pure filesystem logic; questions are the
  CLI's job, and the CLI never asks them in CI or without a terminal.

The install itself is what a theme's own install.sh does: copy chrome/ into
the profile, copy the optional sheets that were asked for, and make sure
``toolkit.legacyUserProfileCustomizations.stylesheets`` is true in **user.js**
(prefs.js is Firefox's own file and gets rewritten on exit; user.js is the
supported place for a value that must survive).
"""

import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

MANIFEST_NAME = "fxcss-install.json"

BLOCK_BEGIN = "/* >>> fxcss install >>> */"
BLOCK_END = "/* <<< fxcss install <<< */"

STYLESHEET_PREF = (
    'user_pref("toolkit.legacyUserProfileCustomizations.stylesheets", true);')

IMPORT_RE = re.compile(r'@import\s+(?:url\(\s*)?["\']([^"\')]+)["\']')


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


def discover_profiles(roots=None):
    """Real profiles on this machine, each with an absolute path.

    Returns [{name, path, root, default}, ...]. At most one entry per root is
    marked default; a machine with several roots (Linux with both a distro and
    a snap Firefox) can legitimately have several, which the CLI treats as
    ambiguous rather than picking one.
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

def strip_user_js_block(text):
    """Remove every well-formed fxcss block, touching nothing else.

    A BEGIN marker without its END is left completely alone: deleting to the
    end of the file could take a line the user wrote, and a duplicated pref is
    harmless where a lost one is not.
    """
    out = []
    lines = text.splitlines(keepends=True)
    i = 0
    while i < len(lines):
        if lines[i].strip() == BLOCK_BEGIN:
            for j in range(i + 1, len(lines)):
                if lines[j].strip() == BLOCK_END:
                    # Also swallow one blank line the block was set off with.
                    if out and out[-1].strip() == "":
                        out.pop()
                    i = j + 1
                    break
            else:
                out.append(lines[i])
                i += 1
            continue
        out.append(lines[i])
        i += 1
    return "".join(out)


def user_js_with_block(existing, body):
    """user.js text with exactly one fxcss block holding `body` at the end."""
    base = strip_user_js_block(existing)
    if base and not base.endswith("\n"):
        base += "\n"
    if base.strip():
        base += "\n"
    return base + BLOCK_BEGIN + "\n" + body.rstrip("\n") + "\n" + BLOCK_END + "\n"


def user_js_body(theme_root):
    """What goes inside the block: the pref that turns userChrome.css on,
    plus whatever configuration/user.js the theme ships."""
    body = STYLESHEET_PREF
    shipped = Path(theme_root) / "configuration" / "user.js"
    if shipped.is_file():
        body += "\n\n" + shipped.read_text(encoding="utf-8",
                                           errors="replace").rstrip("\n")
    return body


# --- optional sheets --------------------------------------------------------

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
        for match in IMPORT_RE.finditer(text):
            target = match.group(1).strip()
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


# --- install ----------------------------------------------------------------

def _timestamp():
    return time.strftime("%Y%m%d%H%M%S")


def _spare_name(profile, base):
    """`base`, or `base-2`, `-3`… when two runs land in the same second."""
    candidate = profile / base
    counter = 2
    while candidate.exists():
        candidate = profile / f"{base}-{counter}"
        counter += 1
    return candidate


def install_theme(theme_root, profile, theme_id, sheets=(), stamp=None):
    """Copy a theme into a real profile. Returns a summary dict.

    Pure filesystem, no prompts: the caller has already decided which profile
    and confirmed with the user. `sheets` are paths to optional stylesheets
    (the theme's custom/*.css) to install alongside.
    """
    theme_root, profile = Path(theme_root), Path(profile)
    source = theme_root / "chrome"
    if not (source / "userChrome.css").is_file():
        raise RuntimeError(f"no chrome/userChrome.css under {theme_root}")
    if not profile.is_dir():
        raise RuntimeError(f"profile directory does not exist: {profile}")

    stamp = stamp or _timestamp()
    chrome = profile / "chrome"

    backup = None
    if chrome.exists():
        backup = _spare_name(profile, f"chrome.backup-{stamp}")
        shutil.move(str(chrome), str(backup))
        backup = backup.name

    shutil.copytree(source, chrome)
    files = sorted(p.relative_to(profile).as_posix()
                   for p in chrome.rglob("*") if p.is_file())

    # Some themes @import customChrome.css without shipping it; an empty file
    # keeps the import resolving (same courtesy core.build_profile extends).
    placeholder = chrome / "customChrome.css"
    if not placeholder.exists() and "customChrome.css" in \
            (chrome / "userChrome.css").read_text(encoding="utf-8",
                                                  errors="replace"):
        placeholder.write_text("/* placeholder created by fxcss */\n",
                               encoding="utf-8")
        files.append(placeholder.relative_to(profile).as_posix())

    installed_sheets = []
    for sheet in sheets:
        sheet = Path(sheet)
        dest = variant_destination(chrome, sheet.name)
        if dest is None:
            # No stylesheet asks for it by name: copy it in and import it,
            # the way `fxcss try --with` layers sheets on.
            dest = chrome / "custom" / sheet.name
            user_chrome = chrome / "userChrome.css"
            with user_chrome.open("a", encoding="utf-8") as handle:
                handle.write("\n/* optional sheet installed by fxcss "
                             f"--with */\n@import \"custom/{sheet.name}\";\n")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sheet, dest)
        relative = dest.relative_to(profile).as_posix()
        if relative not in files:
            files.append(relative)
        installed_sheets.append(sheet.stem)

    user_js = profile / "user.js"
    user_js_created = not user_js.exists()
    existing = "" if user_js_created else user_js.read_text(encoding="utf-8",
                                                            errors="replace")
    user_js.write_text(user_js_with_block(existing, user_js_body(theme_root)),
                       encoding="utf-8")

    manifest = {
        "theme": str(theme_id),
        "installed": time.strftime("%Y-%m-%d %H:%M:%S"),
        "backup": backup,
        "user_js_created": user_js_created,
        "sheets": installed_sheets,
        "files": sorted(files),
    }
    (chrome / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    return manifest


# --- uninstall --------------------------------------------------------------

def read_manifest(profile):
    """The manifest a previous install left, or None."""
    path = Path(profile) / "chrome" / MANIFEST_NAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _manifest_paths(profile, data):
    """The manifest's file list, refusing anything that strays.

    The manifest sits in a directory a theme (or anything else) could have
    edited, so it is untrusted input: only plain relative paths that stay
    under chrome/ are honoured.
    """
    safe = []
    for entry in data.get("files", []):
        parts = Path(entry).parts
        if not parts or parts[0] != "chrome" or ".." in parts \
                or Path(entry).is_absolute():
            continue
        safe.append(Path(profile).joinpath(*parts))
    return safe


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
    """Undo an install. Returns a summary dict.

    With a manifest: delete exactly the files it lists, then put the recorded
    backup back if the way is clear. Without one: never delete -- the current
    chrome/ (which fxcss cannot prove it wrote) is moved aside, and the newest
    chrome.backup-* is restored. No manifest and no backup means there is
    nothing fxcss can safely claim, and that is an error.
    """
    profile = Path(profile)
    chrome = profile / "chrome"
    manifest = read_manifest(profile)
    backups = sorted(p for p in profile.glob("chrome.backup-*") if p.is_dir())
    stamp = stamp or _timestamp()
    summary = {"removed": 0, "restored": None, "moved_aside": None,
               "kept": [], "theme": manifest.get("theme") if manifest else None}

    if manifest:
        for path in _manifest_paths(profile, manifest):
            if path.is_file():
                path.unlink()
                summary["removed"] += 1
        manifest_file = chrome / MANIFEST_NAME
        if manifest_file.exists():
            manifest_file.unlink()
        _prune_empty_dirs(chrome)
        if chrome.exists():
            # Files fxcss did not write are still in there; they survive, and
            # the backup stays where it is rather than clobbering them.
            summary["kept"] = sorted(
                p.relative_to(profile).as_posix()
                for p in chrome.rglob("*") if p.is_file())
        recorded = manifest.get("backup")
        if recorded and (profile / recorded).is_dir() and not chrome.exists():
            shutil.move(str(profile / recorded), str(chrome))
            summary["restored"] = recorded
    elif backups:
        if chrome.exists():
            moved = _spare_name(profile, f"chrome.removed-{stamp}")
            shutil.move(str(chrome), str(moved))
            summary["moved_aside"] = moved.name
        newest = backups[-1]
        shutil.move(str(newest), str(chrome))
        summary["restored"] = newest.name
    else:
        raise RuntimeError(
            f"nothing fxcss installed in {profile}: no "
            f"chrome/{MANIFEST_NAME} and no chrome.backup-* to restore")

    user_js = profile / "user.js"
    if user_js.is_file():
        text = user_js.read_text(encoding="utf-8", errors="replace")
        stripped = strip_user_js_block(text)
        if stripped != text:
            if not stripped.strip() and manifest \
                    and manifest.get("user_js_created"):
                user_js.unlink()
            else:
                user_js.write_text(stripped, encoding="utf-8")
            summary["user_js_cleaned"] = True
    return summary
