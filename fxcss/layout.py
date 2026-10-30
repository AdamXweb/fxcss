#!/usr/bin/env python3
"""Find the installable theme inside a source tree, and say how to lay it out.

A theme repository is rarely just ``chrome/userChrome.css``. Of the 132
themes in the FirefoxCSS Store, roughly half keep ``userChrome.css`` at the
repository root with its parts beside it, several ship sibling editions
(``1.Normal/chrome``, ``2.WithTST/chrome``; ``Simplify Mint/Clean``), some
ship only ``userContent.css``, a few commit a ``chrome.zip``, and a couple
ship ``src/userChrome-<edition>.css`` files that an installer assembles.
The old heuristic -- "the shallowest userChrome.css, and its grandparent is
the theme" -- returned the parent of the download for flat layouts and
picked whichever edition happened to be shallowest for the rest.

This module replaces that with a *plan*: every edition found, which one is
selected and why, where each file goes, and -- when nothing can be done
without guessing or running a script -- a precise reason instead. The plan
is pure filesystem reading; nothing from the source is executed, archives
are read as data within fixed bounds, and no path it returns is ever outside
the source directory it was given.

``materialize`` turns a plan into the ``<root>/chrome/…`` shape the rest of
fxcss expects, copying files into a work directory for layouts that need
rearranging and returning the real directory for ones that do not.
"""

import os
import re
import shutil
import zipfile
from pathlib import Path

from . import inventory

# The layouts a plan can describe.
STANDARD = "standard"        # <root>/chrome/userChrome.css
FLAT = "flat"                # <root>/userChrome.css with its parts beside it
CONTENT_ONLY = "content-only"  # userContent.css and no userChrome.css
NAMED = "named"              # <root>/userChrome-<edition>.css, chosen by name
FRAGMENT = "fragment"        # no entry sheet; the user names one to @import

ENTRY_NAMES = {name.lower(): name for name in inventory.ENTRY_SHEETS}
NAMED_ENTRY_RE = re.compile(r"^(userChrome|userContent)[-_.](.+)\.css$", re.I)

# Directory names that mark something other than the current theme: an
# archive of older versions, screenshots, documentation, another application.
# Matched per path component, case-insensitively, as whole words.
DEMOTED_RE = re.compile(
    r"(?:^|[\s._()\[\]-])(old|older|legacy|archive|archived|deprecated|backup|"
    r"backups|previous|history|historical|screenshots?|images?|img|pictures?|"
    r"docs?|documentation|examples?|samples?|tests?|preview|previews|"
    r"thunderbird|node_modules)(?:[\s._()\[\]-]|$)", re.I)
# Never descended into.
PRUNED_DIRS = {".git", ".hg", ".svn", "node_modules", "__pycache__"}
# Files beside a flat theme that are documentation or tooling, not theme
# files, unless a stylesheet refers to them.
DOC_FILE_RE = re.compile(
    r"^(readme|license|licence|copying|changelog|changes|contributing|"
    r"code_of_conduct|security|authors|notice|install|funding)(\..*)?$"
    r"|^\.(gitignore|gitattributes|gitmodules|editorconfig|prettierrc|"
    r"prettierignore|npmrc|nvmrc)$|^.*\.md$|^package(-lock)?\.json$|^\.ds_store$",
    re.I)
DOC_DIR_RE = re.compile(
    r"^(\.github|\.vscode|\.idea|\.husky|screenshots?|images?|img|pictures?|"
    r"docs?|documentation|examples?|samples?|previews?|demo|media|assets|"
    r"scripts?|screens.*)$", re.I)
INSTALL_SCRIPTS = ("install.sh", "install.bat", "install.ps1", "install.py",
                   "setup.sh", "uninstall.sh", "makefile", "justfile", "build.sh")

# Bounds on how much of a source tree is read.
MAX_WALK_ENTRIES = 50000
MAX_DEPTH = 12
# Bounds on archives read as data. zip only: it is in the standard library
# and the format committed to theme repositories; rar and 7z need tools fxcss
# will not assume.
MAX_ARCHIVE_BYTES = 80 * 1024 * 1024
MAX_UNPACKED_BYTES = 200 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 20000
ARCHIVE_SUFFIXES = {".zip": "zip", ".rar": "rar", ".7z": "7z",
                    ".tar.gz": "tar", ".tgz": "tar", ".tar": "tar", ".xpi": "xpi"}


class LayoutError(RuntimeError):
    """A plan cannot proceed; the message says why and what would help."""


def _posix(path):
    text = Path(path).as_posix()
    return "" if text == "." else text


def _demoted(relative):
    """Which path component, if any, marks this as not the current theme."""
    for part in Path(relative).parts:
        match = DEMOTED_RE.search(part)
        if match:
            return match.group(1)
    return None


def _walk(root):
    """(directory, files) pairs below root, pruned and bounded."""
    root = Path(root)
    count = 0
    base_depth = len(root.parts)
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in PRUNED_DIRS
                         and not Path(current, d).is_symlink())
        if len(Path(current).parts) - base_depth >= MAX_DEPTH:
            dirs[:] = []
        count += len(files) + len(dirs)
        yield Path(current), sorted(files)
        if count > MAX_WALK_ENTRIES:
            return


def _editions(root):
    """Every place in the tree that could be a theme, as edition dicts."""
    root = Path(root)
    editions, truncated = [], False
    seen_count = 0
    for directory, files in _walk(root):
        seen_count += len(files)
        if seen_count > MAX_WALK_ENTRIES:
            truncated = True
            break
        relative = directory.relative_to(root)
        entries, misnamed = [], False
        named = {}
        for name in files:
            lowered = name.lower()
            if lowered in ENTRY_NAMES:
                entries.append(name)
                if name != ENTRY_NAMES[lowered]:
                    misnamed = True
                continue
            match = NAMED_ENTRY_RE.match(name)
            if match and not DOC_FILE_RE.match(name):
                named.setdefault(match.group(2), {})[match.group(1).lower()] = name
        if entries:
            standard = directory.name == "chrome" and directory != root
            subpath = _posix(relative.parent) if standard else _posix(relative)
            has_chrome = any(n.lower() == "userchrome.css" for n in entries)
            editions.append({
                "key": subpath or ".",
                "subpath": subpath,
                "chrome_dir": _posix(relative),
                "kind": (STANDARD if standard else
                         FLAT if has_chrome else CONTENT_ONLY),
                "entries": sorted(entries, key=str.lower),
                "misnamed": misnamed,
                "demoted": _demoted(relative),
                "named": None,
                "depth": len(relative.parts),
            })
        # A lone userChrome-hover.css inside a theme is a part, not an
        # edition: named editions come in sets (WhiteSur ships ten) or as a
        # userChrome/userContent pair, and never beside a plain entry sheet.
        if entries or (len(named) < 2 and not any(
                "userchrome" in parts and "usercontent" in parts for parts in named.values())):
            named = {}
        for edition_name, parts in sorted(named.items()):
            if "userchrome" not in parts and "usercontent" not in parts:
                continue
            entry = parts.get("userchrome") or parts.get("usercontent")
            editions.append({
                "key": _posix(relative / entry),
                "subpath": _posix(relative),
                "chrome_dir": _posix(relative),
                "kind": NAMED,
                "entries": [parts[k] for k in ("userchrome", "usercontent") if k in parts],
                "misnamed": False,
                "demoted": _demoted(relative),
                "named": {"name": edition_name, "files": parts},
                "depth": len(relative.parts),
            })
    return editions, truncated


def _label(edition):
    kind = {STANDARD: "chrome/ layout", FLAT: "flat layout",
            CONTENT_ONLY: "userContent.css only",
            NAMED: "named edition"}[edition["kind"]]
    where = edition["chrome_dir"] or "repository root"
    label = f"{edition['key']}  ({kind}"
    if edition["kind"] == NAMED:
        label += f" {edition['named']['name']!r}"
    label += f"; stylesheets in {where})"
    if edition["demoted"]:
        label += f"  [looks like {edition['demoted']}]"
    if edition["misnamed"]:
        label += "  [entry file name case differs: Linux needs userChrome.css exactly]"
    return label


def _archives(root):
    """Archive files at the top level of a source tree, by kind."""
    root = Path(root)
    found = []
    for path in sorted(root.iterdir()):
        if not path.is_file() or path.is_symlink():
            continue
        lowered = path.name.lower()
        for suffix, kind in ARCHIVE_SUFFIXES.items():
            if lowered.endswith(suffix):
                found.append({"path": path, "kind": kind,
                              "name": path.relative_to(root).as_posix()})
                break
    return found


def extract_zip(archive, destination):
    """Unpack a zip as data, within bounds, never outside `destination`.

    Links, devices, absolute paths and ``..`` members are refused; nested
    archives inside are left as files. Returns `destination`; `discover`
    finds the theme inside it wherever the archive put it.
    """
    archive, destination = Path(archive), Path(destination)
    if archive.stat().st_size > MAX_ARCHIVE_BYTES:
        raise LayoutError(f"{archive.name} is larger than "
                          f"{MAX_ARCHIVE_BYTES // (1024 * 1024)} MB; refusing to unpack it")
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    total = 0
    try:
        with zipfile.ZipFile(archive) as zipped:
            members = zipped.infolist()
            if len(members) > MAX_ARCHIVE_MEMBERS:
                raise LayoutError(f"{archive.name} holds more than "
                                  f"{MAX_ARCHIVE_MEMBERS} entries; refusing")
            for member in members:
                name = member.filename
                if not name or name.endswith("/"):
                    continue
                mode = (member.external_attr >> 16) & 0o170000
                if mode and mode != 0o100000:
                    continue  # symlink, device or other non-file
                parts = Path(name.replace("\\", "/")).parts
                if Path(name).is_absolute() or ".." in parts or not parts:
                    raise LayoutError(f"{archive.name} tries to write outside its folder: {name}")
                target = root.joinpath(*parts)
                if not str(target).startswith(str(root) + os.sep):
                    raise LayoutError(f"{archive.name} tries to write outside its folder: {name}")
                total += max(member.file_size, 0)
                if total > MAX_UNPACKED_BYTES:
                    raise LayoutError(f"{archive.name} unpacks to more than "
                                      f"{MAX_UNPACKED_BYTES // (1024 * 1024)} MB; refusing")
                target.parent.mkdir(parents=True, exist_ok=True)
                with zipped.open(member) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst, 131072)
    except zipfile.BadZipFile as exc:
        raise LayoutError(f"{archive.name} is not a readable zip archive: {exc}") from exc
    return destination


def discover(source, subpath=None, entry=None, workdir=None):
    """Read a source tree and plan how to install it.

    `subpath` selects an edition by its key (a directory relative to the
    source, or the entry file of a named edition, or an archive name);
    `entry` names a stylesheet to load when the theme ships no entry sheet
    (a *fragment*, which gets a generated userChrome.css that @imports it).
    `workdir` is where a packaged theme is unpacked; without one, archives
    are reported but not read.

    Returns a plan dict:

      status      "ok" | "ambiguous" | "unsupported"
      reason      why, when not "ok"
      source      the directory given
      repo_root   the directory the plan reads from (the unpacked archive
                  for a packaged theme, otherwise `source`)
      editions    every edition found, with labels
      selected    the chosen edition, or None
      kind        the selected edition's layout kind, or None
      subpath / entry   the selection, as recorded for later upgrades
      notes       things worth telling the person
      packaged    the archive name, when one was unpacked
      archives    archives seen at the top level
      scripts     install scripts seen at the top level (never run)
      truncated   True when the tree was too large to read in full

    No path in the plan is outside `source` (or the unpacked archive).
    """
    source = Path(source)
    plan = {"status": "ok", "reason": "", "source": source, "repo_root": source,
            "editions": [], "selected": None, "kind": None,
            "subpath": subpath or None, "entry": entry or None, "notes": [],
            "packaged": None, "archives": [], "scripts": [], "truncated": False}
    if not source.is_dir():
        return _unsupported(plan, f"{source} is not a directory")

    plan["scripts"] = [name for name in sorted(os.listdir(source))
                       if name.lower() in INSTALL_SCRIPTS]
    editions, plan["truncated"] = _editions(source)
    archives = _archives(source)
    plan["archives"] = [a["name"] for a in archives]

    # A repository that is only an archive (plus README) is a packaged theme.
    if not editions and archives:
        zips = [a for a in archives if a["kind"] == "zip"]
        chosen = None
        if subpath:
            chosen = next((a for a in archives if a["name"] == subpath), None)
            if chosen is None:
                return _unsupported(plan, f"no archive named {subpath!r} here; "
                                    f"archives: {', '.join(plan['archives'])}")
            if chosen["kind"] != "zip":
                return _unsupported(plan, _archive_reason(chosen))
        elif len(zips) == 1 and len(archives) == 1:
            chosen = zips[0]
        elif zips:
            plan["status"] = "ambiguous"
            plan["reason"] = ("several archives; choose one with --subpath: "
                              + ", ".join(a["name"] for a in archives))
            return plan
        else:
            return _unsupported(plan, _archive_reason(archives[0], all_of=archives))
        if workdir is None:
            return _unsupported(plan, f"{chosen['name']} must be unpacked to read the "
                                "theme (no work directory was given)")
        try:
            unpacked = extract_zip(chosen["path"], Path(workdir) / "unpacked")
        except LayoutError as exc:
            return _unsupported(plan, str(exc))
        inner = discover(unpacked, entry=entry)
        inner["source"] = source
        inner["packaged"] = chosen["name"]
        inner["subpath"] = chosen["name"]
        inner["archives"] = plan["archives"]
        inner["scripts"] = plan["scripts"]
        inner["notes"].insert(0, f"theme read from {chosen['name']} (unpacked as data; "
                                 "nothing executed)")
        return inner

    plan["editions"] = editions
    for edition in editions:
        edition["label"] = _label(edition)

    if entry:
        return _fragment(plan, entry)

    if not editions:
        hint = _fragment_hint(source)
        reason = "no userChrome.css or userContent.css anywhere, so nothing Firefox loads by name"
        if plan["scripts"]:
            reason += (f"; the shipped {', '.join(plan['scripts'])} assembles the theme, "
                       "and fxcss does not run downloaded scripts")
        if hint:
            reason += ("; if one of these is the sheet the README says to @import, pass "
                       "--entry <path>: " + ", ".join(hint))
        return _unsupported(plan, reason)

    selected = None
    if subpath:
        wanted = _posix(Path(subpath))
        matches = [e for e in editions if e["key"] == wanted or e["key"] == (wanted or ".")
                   or e["subpath"] == wanted or e["chrome_dir"] == wanted]
        if not matches:
            return _unsupported(plan, f"no edition at {subpath!r}; editions here: "
                                + ", ".join(e["key"] for e in editions))
        if len(matches) > 1:
            # A directory holding both a flat entry and a chrome/ folder.
            standard = [e for e in matches if e["kind"] == STANDARD]
            if len(standard) == 1:
                matches = standard
            else:
                plan["status"] = "ambiguous"
                plan["reason"] = (f"several editions at {subpath!r}; choose an exact "
                                  "--subpath: " + ", ".join(e["key"] for e in matches))
                return plan
        selected = matches[0]
    else:
        selected = _choose(plan, editions)
        if selected is None:
            return plan

    plan["selected"] = selected
    plan["kind"] = selected["kind"]
    plan["subpath"] = selected["key"] if selected["key"] != "." else None
    nested = [e for e in editions if e is not selected
              and e["chrome_dir"].startswith(selected["chrome_dir"] + "/")
              and selected["chrome_dir"] != ""]
    if nested:
        plan["notes"].append(f"{len(nested)} alternative theme(s) live inside the "
                             "selected chrome/ and are copied with it as files")
    others = [e for e in editions if e is not selected and e not in nested]
    if others:
        plan["notes"].append("other editions here (choose with --subpath): "
                             + ", ".join(f"{e['key']} ({e['kind']}{', misnamed entry' if e['misnamed'] else ''})" for e in others[:8])
                             + (" …" if len(others) > 8 else ""))
    if selected["misnamed"]:
        plan["notes"].append("entry file name case differs from userChrome.css; it is "
                             "installed under the exact name Firefox needs")
    if selected["kind"] == CONTENT_ONLY:
        plan["notes"].append("userContent.css only: this theme styles web pages and "
                             "extension pages, not the browser window")
    if plan["scripts"]:
        plan["notes"].append(f"ships {', '.join(plan['scripts'])}; fxcss copies files "
                             "itself and never runs it")
    if plan["truncated"]:
        plan["notes"].append(f"only the first {MAX_WALK_ENTRIES} entries were read")
    return plan


def _unsupported(plan, reason):
    plan["status"] = "unsupported"
    plan["reason"] = reason
    return plan


def _archive_reason(archive, all_of=None):
    kinds = {a["kind"] for a in (all_of or [archive])}
    names = ", ".join(a["name"] for a in (all_of or [archive])[:6])
    if kinds <= {"rar", "7z"}:
        return (f"the theme is shipped as {names}; rar/7z archives need an external "
                "tool fxcss does not assume -- extract it yourself and pass the folder")
    if kinds <= {"tar"}:
        return (f"the theme is shipped as {names}; pass the unpacked folder instead")
    return f"the theme is shipped only as archives ({names}) that fxcss does not unpack"


def _choose(plan, editions):
    """Pick the edition a person would mean, or declare the choice theirs."""
    candidates = [e for e in editions if not e["demoted"] and not e["misnamed"]]
    if not candidates:
        candidates = [e for e in editions if not e["demoted"]] or list(editions)
    at_root = [e for e in candidates if e["subpath"] == ""]
    standard_at_root = [e for e in at_root if e["kind"] == STANDARD]
    if len(standard_at_root) == 1:
        return standard_at_root[0]
    if standard_at_root:
        return standard_at_root[0]
    flat_at_root = [e for e in at_root if e["kind"] in (FLAT, CONTENT_ONLY)]
    if len(flat_at_root) == 1:
        return flat_at_root[0]
    if len(candidates) == 1:
        return candidates[0]
    # Several editions, none at the root: the right one depends on what the
    # person wants (an edition, a Firefox version, a window-control side).
    named = [e for e in candidates if e["kind"] == NAMED]
    if named and len(named) == len(candidates):
        plan["reason"] = ("this theme ships several named editions; choose one with "
                          "--subpath <entry file>: " + ", ".join(e["key"] for e in candidates[:12]))
    else:
        plan["reason"] = ("this repository holds several themes; choose one with "
                          "--subpath: " + ", ".join(e["key"] for e in candidates[:12]))
    if len(candidates) > 12:
        plan["reason"] += f" … ({len(candidates)} in all)"
    plan["status"] = "ambiguous"
    return None


def _fragment_hint(source):
    """Stylesheets a README might tell someone to @import, as a hint."""
    found = []
    for directory, files in _walk(source):
        relative = directory.relative_to(source)
        if _demoted(relative):
            continue
        for name in files:
            if name.lower().endswith(".css"):
                found.append(_posix(relative / name))
    if len(found) > 8:
        preferred = [f for f in found if re.search(
            r"(import|main|index|theme|firefox|chrome|style)", Path(f).stem, re.I)]
        found = (preferred or found)[:8]
    return found


def _fragment(plan, entry):
    """Plan a theme whose entry the person named explicitly."""
    source = plan["repo_root"]
    relative = _posix(Path(entry))
    target = source / relative
    try:
        target.resolve().relative_to(source.resolve())
    except ValueError:
        return _unsupported(plan, f"--entry {entry!r} is outside the theme")
    if not target.is_file() or not relative.lower().endswith(".css"):
        return _unsupported(plan, f"--entry {entry!r} is not a stylesheet in the theme")
    # The fragment's own directory is the chrome/ tree: its relative imports
    # and url()s resolve from there, and the generated entry sits beside it.
    parent = _posix(Path(relative).parent)
    edition = {
        "key": relative, "subpath": parent, "chrome_dir": parent, "kind": FRAGMENT,
        "entries": [], "misnamed": False, "demoted": None, "named": None,
        "depth": len(Path(parent).parts), "entry": Path(relative).name,
    }
    edition["label"] = f"{relative}  (fragment; a generated userChrome.css imports it)"
    plan["editions"] = [edition] + plan["editions"]
    plan["selected"] = edition
    plan["kind"] = FRAGMENT
    plan["entry"] = relative
    plan["subpath"] = None
    plan["notes"].append(f"userChrome.css is generated to @import {Path(relative).name}, "
                         f"with {parent or 'the repository root'} as chrome/")
    return plan


# --- materialising ----------------------------------------------------------

def _copy_tree(src, dest, skip):
    """Copy a directory, skipping `skip(relative_posix)` paths and links."""
    src, dest = Path(src), Path(dest)
    for directory, files in _walk(src):
        relative = directory.relative_to(src)
        if relative.parts and skip(_posix(relative), True):
            continue
        for name in files:
            path = directory / name
            rel = _posix(relative / name)
            if path.is_symlink() or not path.is_file() or skip(rel, False):
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def materialize(plan, workdir):
    """Give a plan the ``<root>/chrome/…`` shape the rest of fxcss reads.

    A standard edition is returned where it is -- the real directory, no
    copy. Every other kind is assembled under `workdir`: entry sheets under
    their exact Firefox names, the edition's other files beside them in the
    same relative places (so @imports and url()s resolve as upstream), a
    generated entry for a fragment, and ``configuration/`` carried across
    when the edition has one. Documentation, VCS metadata and install
    scripts are left out unless a stylesheet refers to them.

    Returns the theme root. Raises LayoutError when the plan is not "ok".
    """
    if plan["status"] != "ok" or not plan["selected"]:
        raise LayoutError(plan["reason"] or "no theme selected")
    repo_root = Path(plan["repo_root"])
    edition = plan["selected"]
    if edition["kind"] == STANDARD and not edition["misnamed"]:
        return repo_root / edition["subpath"] if edition["subpath"] else repo_root

    root = Path(workdir) / "theme"
    if root.exists():
        shutil.rmtree(root)
    chrome = root / "chrome"
    chrome.mkdir(parents=True)
    source_dir = repo_root / edition["chrome_dir"] if edition["chrome_dir"] else repo_root

    # What the entry sheets reach decides which doc-like files stay.
    entries = _entry_files(edition, source_dir)
    roots = [src for src, _ in entries] or ([edition["entry"]] if edition.get("entry") else [])
    referenced = inventory.reachable_paths(source_dir, entries=roots) if roots else set()

    other_entries = set()
    if edition["kind"] == NAMED:
        for name in os.listdir(source_dir):
            if NAMED_ENTRY_RE.match(name) and name not in edition["named"]["files"].values():
                other_entries.add(name)
    nested_editions = [e["chrome_dir"] for e in plan["editions"]
                       if e is not edition and e["chrome_dir"]
                       and e["chrome_dir"] != edition["chrome_dir"]
                       and e["chrome_dir"].startswith(edition["chrome_dir"] + "/" if edition["chrome_dir"] else "")]

    def skip(rel, is_dir):
        if not is_dir and "/" not in rel and Path(rel).name.lower() in ENTRY_NAMES \
                and edition["kind"] != FRAGMENT:
            return True  # copied once under the exact Firefox spelling below
        if rel in referenced:
            return False
        name = Path(rel).name
        top = Path(rel).parts[0]
        if is_dir:
            if top in PRUNED_DIRS or (DOC_DIR_RE.match(name) and rel not in referenced
                                      and not any(r.startswith(rel + "/") for r in referenced)):
                return True
            if rel == "configuration" and not any(r.startswith("configuration/") for r in referenced):
                return True  # placed beside chrome/, where install reads it
            return False
        if name in other_entries or name.lower() in ENTRY_NAMES and edition["kind"] != FRAGMENT:
            return True  # entries are placed by _copy_entries under exact names
        if "/" not in rel and (DOC_FILE_RE.match(name) or name.lower() in INSTALL_SCRIPTS):
            return True
        if DOC_DIR_RE.match(top) and not any(r.startswith(top + "/") for r in referenced):
            return True
        return False

    _copy_tree(source_dir, chrome, skip)
    _copy_entries(entries, chrome, source_dir)
    if edition["kind"] == NAMED:
        _overlay_common(plan, source_dir, chrome)
    if edition["kind"] == FRAGMENT:
        (chrome / "userChrome.css").write_text(
            "/* generated by fxcss: this theme ships no userChrome.css */\n"
            f'@import "{edition["entry"]}";\n', encoding="utf-8")
    extras_root = (repo_root / edition["subpath"]
                   if edition["kind"] == STANDARD else source_dir)
    extras = ("configuration", *inventory.OPTION_DIRS) if edition["kind"] == STANDARD else ("configuration",)
    for extra in extras:
        candidate = extras_root / extra
        if candidate.is_dir() and not (chrome / extra).exists():
            shutil.copytree(candidate, root / extra)
    return root


def _overlay_common(plan, source_dir, chrome):
    """Declarative assembly for named editions: merge common/ into the
    edition's folder when that is what the imports expect.

    WhiteSur's install.sh copies ``src/common/*`` into ``src/<Edition>/``
    before installing. The same effect is reproduced here from the data
    alone: for each import that resolves to nothing, if the same relative
    path exists under a sibling ``common/`` directory, that directory is
    copied into the import's first folder without overwriting the edition's
    own files. Nothing is executed; the step is recorded in the plan's notes.
    """
    chrome = Path(chrome)
    common = next((Path(source_dir) / name for name in ("common", "shared")
                   if (Path(source_dir) / name).is_dir()), None)
    if common is None:
        return
    found = inventory.scan(chrome)
    targets = set()
    for edge in found["unresolved"]:
        resolved = Path(edge["resolved"])
        if inventory.is_inside(edge["resolved"]) and len(resolved.parts) > 1:
            rest = Path(*resolved.parts[1:])
            if (common / rest).is_file():
                targets.add(resolved.parts[0])
    for folder in sorted(targets):
        dest = chrome / folder
        for directory, files in _walk(common):
            relative = directory.relative_to(common)
            for name in files:
                src = directory / name
                if src.is_symlink() or not src.is_file():
                    continue
                target = dest / relative / name
                if target.exists():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, target)
        plan["notes"].append(f"{common.name}/ merged into {folder}/ (what the theme's "
                             "installer does; done here as a plain copy)")


def _entry_files(edition, source_dir):
    """[(source name, exact Firefox name)] for the edition's entry sheets."""
    if edition["kind"] == NAMED:
        files = edition["named"]["files"]
        return [(files[k], ENTRY_NAMES[k + ".css"]) for k in ("userchrome", "usercontent")
                if k in files]
    if edition["kind"] == FRAGMENT:
        return []
    return [(name, ENTRY_NAMES[name.lower()]) for name in edition["entries"]]


def _copy_entries(entries, chrome, source_dir):
    chrome = Path(chrome)
    chrome.mkdir(parents=True, exist_ok=True)
    for src, dest in entries:
        shutil.copy2(Path(source_dir) / src, chrome / dest)


def describe_plan(plan):
    """Human lines summarising a plan: selection, alternatives, notes."""
    lines = []
    if plan["packaged"]:
        lines.append(f"packaged theme: read from {plan['packaged']}")
    selected = plan["selected"]
    if selected:
        lines.append(f"theme: {selected['label']}")
    if plan["status"] == "ambiguous":
        lines.append("several editions:")
        for edition in plan["editions"]:
            lines.append(f"  {edition['label']}")
    elif plan["status"] == "unsupported":
        lines.append(f"unsupported: {plan['reason']}")
    for note in plan["notes"]:
        lines.append(f"note: {note}")
    return lines


def find_theme_root(unpacked):
    """The folder holding chrome/userChrome.css, or None.

    Kept for callers of the old heuristic. Unlike before it never returns a
    directory outside `unpacked`, and it answers only when the plan selects a
    standard chrome/ layout; every other layout needs `discover` + `materialize`.
    """
    plan = discover(unpacked)
    if plan["status"] != "ok" or plan["kind"] != STANDARD:
        return None
    root = Path(unpacked) / plan["selected"]["subpath"] if plan["selected"]["subpath"] \
        else Path(unpacked)
    try:
        root.resolve().relative_to(Path(unpacked).resolve())
    except ValueError:
        return None
    return root


# Standard roots resolved for commands that only read a theme (shot, audit,
# doctor …): a flat layout is materialised once per process and reused.
_RESOLVED = {}
_PLANS = {}
_WORKDIRS = []


def standard_root(theme, subpath=None, entry=None, with_plan=False):
    """A ``<root>/chrome/…`` directory for `theme`, materialising if needed.

    Returns `theme` itself when it already has that shape. For other layouts
    the synthetic root lives in a temporary directory that is removed at
    exit. Raises LayoutError with the plan's reason when nothing can be done.
    """
    import atexit
    import tempfile

    theme = Path(theme)
    if any((theme / "chrome" / name).is_file() for name in inventory.ENTRY_SHEETS) \
            and not subpath and not entry and not with_plan:
        return theme
    key = (str(theme.resolve()), subpath, entry)
    if key in _RESOLVED and _RESOLVED[key].exists():
        return (_RESOLVED[key], _PLANS[key]) if with_plan else _RESOLVED[key]
    workdir = Path(tempfile.mkdtemp(prefix="fxcss-layout-"))
    _WORKDIRS.append(workdir)
    if len(_WORKDIRS) == 1:
        atexit.register(lambda: [shutil.rmtree(w, ignore_errors=True) for w in _WORKDIRS])
    plan = discover(theme, subpath=subpath, entry=entry, workdir=workdir)
    if plan["status"] != "ok":
        raise LayoutError(plan["reason"])
    root = materialize(plan, workdir)
    _RESOLVED[key] = root
    _PLANS[key] = plan
    return (root, plan) if with_plan else root


def patch_source_paths(plan, materialized, relative_files):
    """Map generated patch paths back to the real repository, or refuse.

    ZIP and assembled editions have no one-to-one writable source mapping.
    A copied file must still match the original before proposing an edit.
    """
    from .scaffold import repository_root
    if plan["packaged"] or plan["kind"] in (NAMED, FRAGMENT):
        raise LayoutError("cannot produce a source patch for a packaged or assembled "
                          "theme; audit the unpacked/built source folder instead")
    selected = plan["selected"]
    source = Path(plan["repo_root"]).resolve()
    directory = source / selected["chrome_dir"]
    git_root = repository_root(source).resolve()
    renamed = {dest: src for src, dest in _entry_files(selected, directory)}
    result = {}
    for relative in relative_files:
        parts = Path(relative).parts
        if not parts or parts[0] != "chrome" or ".." in parts:
            raise LayoutError(f"no source mapping for {relative!r}; no patch written")
        tail = Path(*parts[1:])
        original = directory / renamed.get(tail.as_posix(), tail.as_posix())
        try:
            original.resolve().relative_to(source)
            target = original.resolve().relative_to(git_root).as_posix()
        except ValueError as exc:
            raise LayoutError(f"patch source {relative!r} is outside the repository") from exc
        if not original.is_file() or original.read_bytes() != (Path(materialized) / relative).read_bytes():
            raise LayoutError(f"source changed or was assembled for {relative!r}; no patch written")
        result[relative] = target
    return result
