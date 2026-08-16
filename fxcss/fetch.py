#!/usr/bin/env python3
"""Fetch a theme from GitHub so it can be test-driven.

`fxcss try owner/repo` downloads a theme, installs it into a throwaway profile
and opens it, so someone can see what a theme actually looks like before putting
it anywhere near their real Firefox profile.

**Nothing from the downloaded repository is ever executed.** A theme's install
script is, in practice, `cp -r chrome/ <profile>/` plus flipping a pref -- which
is precisely what fxcss already does itself. So this locates and *reports* the
install script and any options it documents, and then does the installation by
copying files. Running a shell script fetched from a URL to preview a stylesheet
would be a poor trade.

That leaves the theme's own contents, which are CSS, SVG and occasionally a .js
file. Firefox does not execute a .js file sitting in a profile's chrome folder --
that needs an autoconfig hook in the *application* directory, which fxcss does
not create.
"""

import io
import json
import os
import re
import shutil
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.github.com"
USER_AGENT = "fxcss (+https://github.com/AdamXweb/fxcss)"

# A theme is small. Anything wildly bigger than this is not one, and unpacking it
# blindly would be careless.
MAX_ARCHIVE_BYTES = 80 * 1024 * 1024
MAX_UNPACKED_BYTES = 200 * 1024 * 1024

REPO_SPEC = re.compile(
    r"^(?:https?://)?(?:www\.)?(?:github\.com/)?([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")

INSTALL_SCRIPTS = [
    "install.sh", "install.bat", "install.ps1", "install.py",
    "setup.sh", "Makefile", "makefile", "justfile",
]

# Themes commonly keep optional tweaks in one of these.
VARIANT_DIRS = ["custom", "optional", "options", "extras", "variants"]

# "- `-c` Left hand side tab close button" and similar list items.
FLAG_LINE = re.compile(r"^\s*[-*+]\s*`?(-{1,2}[\w-]+)`?\s*[—:-]?\s+(.{4,120}?)\s*$")


def parse_repo(spec: str):
    match = REPO_SPEC.match(spec.strip())
    if not match:
        raise ValueError(
            f"could not read {spec!r} as a GitHub repo. "
            "Use owner/name or a github.com URL.")
    return match.group(1), match.group(2)


def _request(url, accept="application/vnd.github+json"):
    headers = {"User-Agent": USER_AGENT, "Accept": accept}
    # Only raises the anonymous rate limit; never required.
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.Request(url, headers=headers)


def _api(path):
    try:
        with urllib.request.urlopen(_request(API + path), timeout=45) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        if exc.code == 403:
            raise RuntimeError(
                "GitHub refused the request (rate limit). Set GITHUB_TOKEN to "
                "raise it, or pass --ref to skip the lookups.") from exc
        raise


def resolve(owner, name):
    """Report what is available: the default branch tip and the latest release."""
    repo = _api(f"/repos/{owner}/{name}")
    if repo is None:
        raise RuntimeError(f"no such repository: {owner}/{name}")

    branch = repo.get("default_branch", "main")
    commit = _api(f"/repos/{owner}/{name}/commits/{branch}") or {}
    release = _api(f"/repos/{owner}/{name}/releases/latest")

    info = {
        "owner": owner, "name": name,
        "description": repo.get("description") or "",
        "stars": repo.get("stargazers_count", 0),
        "licence": (repo.get("license") or {}).get("spdx_id"),
        "default_branch": branch,
        "commit": {
            "sha": (commit.get("sha") or "")[:7],
            "date": (commit.get("commit", {}).get("author", {}) or {}).get("date", ""),
            "message": (commit.get("commit", {}).get("message") or "").splitlines()[0][:72],
        } if commit else None,
        "release": {
            "tag": release.get("tag_name"),
            "name": release.get("name") or release.get("tag_name"),
            "date": release.get("published_at", ""),
        } if release else None,
    }
    return info


def choose_ref(info, prefer):
    """Pick what to download. Releases are what a theme's author blessed."""
    if prefer == "commit":
        return info["default_branch"], "latest commit"
    if info["release"]:
        return info["release"]["tag"], f"release {info['release']['tag']}"
    return info["default_branch"], f"latest commit on {info['default_branch']}"


def commit_is_newer(info):
    """Has the default branch moved on since the latest release?

    The dates come from the API as ISO-8601 in UTC, so comparing the strings
    compares the instants. False when either side is missing: with nothing to
    contrast there is no choice worth putting to anyone.
    """
    release = (info.get("release") or {}).get("date")
    commit = (info.get("commit") or {}).get("date")
    return bool(release and commit and commit > release)


def ref_options(info):
    """The refs worth offering, the author's blessed one first.

    Themes are the kind of project where the newest tag can be a year behind a
    branch that has quietly fixed the bug you are hitting -- and equally where
    the branch is mid-rewrite. Neither is the safe default in general, so both
    are describable and the caller decides how to ask.

    Returns [{ref, label, why, date, note}], possibly empty.
    """
    out = []
    release = info.get("release") or {}
    if release.get("tag"):
        out.append({
            "ref": release["tag"],
            "label": f"release {release['tag']}",
            "why": f"release {release['tag']}",
            "date": (release.get("date") or "")[:10],
            "note": "what the author last published",
        })
    branch = info.get("default_branch")
    if branch:
        commit = info.get("commit") or {}
        note = commit.get("message") or ""
        if release.get("tag") and commit_is_newer(info):
            note = f"newer than the release — {note}" if note else "newer than the release"
        out.append({
            "ref": branch,
            "label": f"latest commit on {branch}",
            "why": f"latest commit on {branch}",
            "date": (commit.get("date") or "")[:10],
            "note": note,
        })
    return out


def update_state(source, info):
    """Is what a profile has installed still what upstream offers?

    `source` is a manifest's source dict, `info` what `resolve()` reports now.
    Returns {state, ref, label, detail}, where state is one of:

      current      installed ref is upstream's newest of the kind tracked
      available    something newer exists; `ref` is what to move to
      pinned       an explicit --ref was asked for, so newer is not "behind"
      unknown      the manifest predates recording how the ref was chosen
      unsupported  a local directory install: there is no upstream to ask

    Kept separate from any fetching so it can be tested without a network, and
    stated as five outcomes rather than a boolean because "fxcss cannot tell"
    has to be renderable -- an install whose ref_kind was never recorded is
    not the same as one that is up to date, and showing it as up to date
    would be a lie a user would act on.
    """
    source = source or {}
    kind = source.get("kind")
    if kind == "local":
        return {"state": "unsupported", "ref": None, "label": "local install",
                "detail": source.get("path") or ""}
    if kind != "github":
        return {"state": "unknown", "ref": None, "label": "unknown source",
                "detail": "nothing recorded about where this came from"}

    ref_kind = source.get("ref_kind")
    if ref_kind == "explicit":
        return {"state": "pinned", "ref": None,
                "label": f"pinned to {source.get('ref')}",
                "detail": "installed with --ref, so nothing is checked"}
    if ref_kind == "release":
        latest = (info.get("release") or {}).get("tag")
        if not latest:
            return {"state": "unknown", "ref": None,
                    "label": "no releases upstream",
                    "detail": "the theme published none to compare against"}
        if latest == source.get("ref"):
            return {"state": "current", "ref": latest, "label": latest,
                    "detail": ""}
        return {"state": "available", "ref": latest, "label": latest,
                "detail": (info.get("release") or {}).get("date", "")[:10]}
    if ref_kind == "branch":
        commit = info.get("commit") or {}
        sha = commit.get("sha")
        if not sha:
            return {"state": "unknown", "ref": None,
                    "label": "branch tip unreadable", "detail": ""}
        if sha == source.get("resolved"):
            return {"state": "current", "ref": source.get("ref"),
                    "label": f"{source.get('ref')} @ {sha}", "detail": ""}
        return {"state": "available", "ref": source.get("ref"),
                "label": f"{source.get('ref')} @ {sha}",
                "detail": commit.get("message", "")[:52]}
    return {"state": "unknown", "ref": None, "label": "ref kind not recorded",
            "detail": "installed before fxcss recorded this; reinstall to fix"}


def _safe_members(archive, destination: Path):
    """Yield members that stay inside the destination, refusing anything else."""
    total = 0
    root = destination.resolve()
    for member in archive:
        if member.issym() or member.islnk():
            continue  # a link could point anywhere; a theme does not need them
        if not (member.isfile() or member.isdir()):
            continue
        target = (root / member.name).resolve()
        if not str(target).startswith(str(root) + os.sep):
            raise RuntimeError(f"archive tried to write outside the target: {member.name}")
        total += max(member.size, 0)
        if total > MAX_UNPACKED_BYTES:
            raise RuntimeError("archive unpacks to more than 200 MB; refusing")
        yield member


def download(owner, name, ref, into: Path):
    """Download and unpack a repository at a ref. Returns the unpacked root."""
    url = f"https://codeload.github.com/{owner}/{name}/tar.gz/{ref}"
    try:
        with urllib.request.urlopen(_request(url, accept="*/*"), timeout=180) as response:
            payload = response.read(MAX_ARCHIVE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"could not download {owner}/{name}@{ref}: {exc}") from exc
    if len(payload) > MAX_ARCHIVE_BYTES:
        raise RuntimeError("archive is larger than 80 MB; refusing")

    into.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        members = _safe_members(archive, into)
        try:
            # Python 3.12+: the 'data' filter also strips absolute paths and
            # rejects links, complementing the checks in _safe_members.
            archive.extractall(into, members=members, filter="data")
        except TypeError:  # Python < 3.12 has no filter parameter
            archive.extractall(into, members=members)

    entries = [p for p in into.iterdir() if p.is_dir()]
    if len(entries) == 1:
        return entries[0]  # GitHub wraps everything in one directory
    return into


def find_theme_root(unpacked: Path):
    """Locate the folder that holds chrome/userChrome.css.

    Not always the repository root: plenty of themes keep sources under src/ or
    ship several variants side by side.
    """
    candidates = sorted(unpacked.rglob("userChrome.css"),
                        key=lambda p: len(p.relative_to(unpacked).parts))
    for sheet in candidates:
        if sheet.parent.name == "chrome":
            return sheet.parent.parent
    if candidates:
        # userChrome.css not inside a folder called chrome; treat its parent as
        # the chrome folder anyway.
        return candidates[0].parent.parent
    return None


def describe(repo_root: Path, theme_root: Path):
    """What this theme offers, and how its author says to install it."""
    scripts = [name for name in INSTALL_SCRIPTS if (repo_root / name).is_file()]

    variants = []
    for folder in VARIANT_DIRS:
        directory = repo_root / folder
        if directory.is_dir():
            for sheet in sorted(directory.glob("*.css")):
                variants.append(sheet)

    flags = []
    for readme in ("README.md", "readme.md", "README", "README.rst"):
        path = repo_root / readme
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            match = FLAG_LINE.match(line)
            if match:
                flags.append({"flag": match.group(1), "text": match.group(2)})
        break

    sheets = list(theme_root.rglob("*.css")) if theme_root else []
    return {
        "scripts": scripts,
        "variants": variants,
        "flags": flags[:14],
        "stylesheets": len(sheets),
        "bytes": sum(p.stat().st_size for p in sheets),
        "has_configuration": (theme_root / "configuration").is_dir() if theme_root else False,
    }


def sheet_continuity(wanted, variants):
    """Do the optional sheets someone installed still exist upstream?

    Returns {kept: [Path], missing: [str]}, matching on the stem the manifest
    records. A sheet that has been renamed or dropped between versions is the
    quiet way an upgrade takes away a setting: the @import simply stops
    resolving and the option turns itself off. Naming the loss is the whole
    job here -- the caller decides whether that stops the upgrade.
    """
    by_stem = {sheet.stem.lower(): sheet for sheet in variants}
    kept, missing = [], []
    for name in wanted or []:
        sheet = by_stem.get(str(name).lower())
        (kept.append(sheet) if sheet else missing.append(str(name)))
    return {"kept": kept, "missing": missing}


def apply_variants(profile: Path, chosen):
    """Layer optional stylesheets on top, by import rather than by copying.

    Themes disagree about where an optional sheet belongs -- some expect it
    inside their own folder, some rely on their installer editing an import
    list. Appending an absolute @import to the profile's userChrome.css works
    for all of them, and relative url()s inside the sheet still resolve because
    the file stays where it was unpacked.
    """
    user_chrome = profile / "chrome" / "userChrome.css"
    if not user_chrome.exists() or not chosen:
        return []
    lines = ["", "/* optional sheets layered on by fxcss try --with */"]
    for sheet in chosen:
        lines.append(f'@import url("{sheet.resolve().as_uri()}");')
    with user_chrome.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return chosen


def humanise(info):
    """Lines describing what was found upstream."""
    out = []
    header = f"{info['owner']}/{info['name']}"
    if info["stars"]:
        header += f"  ★{info['stars']}"
    if info["licence"] and info["licence"] != "NOASSERTION":
        header += f"  {info['licence']}"
    out.append(header)
    if info["description"]:
        out.append(f"  {info['description'][:96]}")
    if info["release"]:
        out.append(f"  latest release   {info['release']['tag']}"
                   f"  ({info['release']['date'][:10]})")
    else:
        out.append("  latest release   none published")
    if info["commit"]:
        out.append(f"  latest commit    {info['commit']['sha']}"
                   f"  ({info['commit']['date'][:10]})  {info['commit']['message']}")
    return out
