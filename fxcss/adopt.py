#!/usr/bin/env python3
"""Work out what theme is already in a profile, and which version of it.

Most themed profiles were not themed by fxcss: someone ran the theme's
install.sh, or copied a chrome/ folder in by hand, long before any of this.
Those profiles can be described (`fxcss profiles` reports them) but not acted
on -- there is no record of what the theme is, so nothing can be upgraded or
rolled back. `fxcss adopt` writes that record.

The identification is by **content**, not by name. Given a candidate
repository, every file under the profile's chrome/ is hashed the way git
hashes a blob and compared against the repository's own tree at each recent
tag. A tag where every file matches is not a guess; it is the same bytes.
A tag where most match is reportable as exactly that -- "v2.0.0, with four
files edited" -- which is more useful than either a bare guess or a shrug.

Two things this deliberately does not do.

**No table of known themes.** Recognising a directory layout as "WhiteSur"
would be a hardcoded fingerprint list: it would go stale as themes change,
and it would invite confident wrong answers for the many themes not in it.
The same reasoning keeps a version table out of `audit`.

**No downloads.** Comparison uses the git tree API, which returns a blob sha
per path, so identifying a theme across ten tags costs ten small API calls
rather than ten tarballs. Someone adopting a theme is usually on a slow path
already -- and archive downloads are the first thing GitHub rate-limits.
"""

import hashlib
import re
from pathlib import Path

# What a theme's chrome/ can hold that names where it came from. Checked in
# this order: a clone knows its own origin, everything else is a mention.
GIT_URL = re.compile(r"""^\s*url\s*=\s*(\S+)""", re.MULTILINE)
# Owner and name run to the first character neither can contain, which is how
# a URL in running prose ("…/owner/theme, with palette ideas from…") gives up
# the repository and not the punctuation after it.
GITHUB_URL = re.compile(r"github\.com[/:]([\w.-]+)/([\w.-]+)")


# Only text a human would have written a URL into, and not much of it: this
# reads files from a directory fxcss did not create.
READABLE_SUFFIXES = {".css", ".md", ".txt", ".json", ".cfg", ".ini"}
MAX_SCAN_BYTES = 2 * 1024 * 1024


def _repos_in(text):
    """Every owner/name a piece of text mentions, in order, tidied."""
    found = []
    for owner, name in GITHUB_URL.findall(text):
        if name.endswith(".git"):
            name = name[:-len(".git")]
        # A trailing dot is the end of a sentence, not part of a repository.
        name, owner = name.rstrip(".-"), owner.rstrip(".-")
        if owner and name:
            found.append(f"{owner}/{name}")
    return found


def blob_sha(data):
    """git's hash for a file's contents: sha1 of "blob <len>\\0" + data.

    Matching git exactly is the point -- it is what lets a local file be
    compared against a repository's tree listing without downloading
    anything. Verified against `git hash-object` for text and binary alike.
    """
    digest = hashlib.sha1()
    digest.update(b"blob %d\0" % len(data))
    digest.update(data)
    return digest.hexdigest()


def file_blob_sha(path):
    """`blob_sha` for a file, plus the same for its contents with CRLF
    collapsed to LF. Returns (sha, sha_as_if_lf); the second is None for
    anything that looks binary.

    Git can be configured to check files out with native line endings, so a
    theme cloned on Windows can differ from its own repository in every text
    file while being, in every way that matters, identical. Comparing both
    turns that from "nothing matches" into a match worth reporting.
    """
    data = path.read_bytes()
    raw = blob_sha(data)
    if b"\0" in data[:8192] or b"\r\n" not in data:
        return raw, None
    return raw, blob_sha(data.replace(b"\r\n", b"\n"))


def git_config_remote(chrome_dir):
    """The origin URL of a clone sitting in chrome/, or None.

    Someone who cloned the theme straight into their profile has told us
    exactly what it is; nothing else here is as good as this.
    """
    config = Path(chrome_dir) / ".git" / "config"
    if not config.is_file():
        try:  # a worktree or submodule keeps .git as a file pointing elsewhere
            pointer = Path(chrome_dir) / ".git"
            if not pointer.is_file():
                return None
            target = pointer.read_text(encoding="utf-8", errors="replace")
            gitdir = target.partition("gitdir:")[2].strip()
            config = (Path(chrome_dir) / gitdir / "config").resolve()
        except OSError:
            return None
    if not config.is_file():
        return None
    try:
        text = config.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for match in GIT_URL.finditer(text):
        for repo in _repos_in(match.group(1)):
            return repo
    return None


def mentioned_repos(chrome_dir):
    """GitHub repos named anywhere in the theme's own text, most-cited first.

    A hint and never more: plenty of themes credit an upstream project, a
    palette or an issue thread, and any of those can appear here. It gives
    someone a name to confirm rather than having to remember one -- and for
    plenty of themes, WhiteSur among them, there is nothing here at all.
    """
    counts = {}
    scanned = 0
    for path in sorted(Path(chrome_dir).rglob("*")):
        if not path.is_file() or path.suffix.lower() not in READABLE_SUFFIXES:
            continue
        try:
            if scanned + path.stat().st_size > MAX_SCAN_BYTES:
                break
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        scanned += len(text)
        for repo in _repos_in(text):
            counts[repo] = counts.get(repo, 0) + 1
    return [repo for repo, _ in sorted(counts.items(),
                                       key=lambda kv: (-kv[1], kv[0]))]


def candidates(chrome_dir):
    """Where this chrome/ might have come from. [{repo, why, certain}]."""
    found = []
    cloned = git_config_remote(chrome_dir)
    if cloned:
        found.append({"repo": cloned, "certain": True,
                      "why": "chrome/ is a git clone of it"})
    for repo in mentioned_repos(chrome_dir):
        if repo != cloned:
            found.append({"repo": repo, "certain": False,
                          "why": "named in the theme's own files"})
    return found


def chrome_prefix(paths):
    """Which directory inside a repository is the chrome/ folder.

    Themes do not agree on where it lives -- repository root, src/, or one of
    several variants side by side -- so it is found the same way
    `fetch.find_theme_root` finds it on disk: by locating userChrome.css and
    taking the shallowest one.
    """
    best = None
    for path in paths:
        if path == "userChrome.css" or path.endswith("/userChrome.css"):
            prefix = path[:-len("userChrome.css")]
            if best is None or prefix.count("/") < best.count("/"):
                best = prefix
    return best


def compare(chrome_dir, tree):
    """Match a profile's chrome/ against one repository tree.

    `tree` is {repo path: blob sha}. Returns counts, the paths that differ,
    and a `score` -- the share of the version's own files that are present
    and identical. Scored against the *version's* file list, not the
    profile's, so that a profile with extra files added by hand can still
    match a release exactly.
    """
    chrome_dir = Path(chrome_dir)
    prefix = chrome_prefix(tree)
    result = {"ref": None, "matched": 0, "differing": [], "missing": [],
              "extra": [], "score": 0.0, "eol": False, "files": 0}
    if prefix is None:
        return result

    wanted = {path[len(prefix):]: sha for path, sha in tree.items()
              if path.startswith(prefix)}
    result["files"] = len(wanted)
    seen = set()
    for relative, sha in sorted(wanted.items()):
        path = chrome_dir / relative
        seen.add(relative)
        if not path.is_file():
            result["missing"].append(relative)
            continue
        try:
            raw, as_lf = file_blob_sha(path)
        except OSError:
            result["differing"].append(relative)
            continue
        if raw == sha:
            result["matched"] += 1
        elif as_lf is not None and as_lf == sha:
            result["matched"] += 1
            result["eol"] = True
        else:
            result["differing"].append(relative)

    for path in sorted(chrome_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(chrome_dir).as_posix()
        if relative not in seen and not relative.startswith(".git/"):
            result["extra"].append(relative)

    result["score"] = result["matched"] / (len(wanted) or 1)
    return result


def better(new, old):
    """Is `new` a closer match than `old`? More matched files wins ties."""
    if old is None:
        return True
    return (new["score"], new["matched"]) > (old["score"], old["matched"])


def exact(result):
    """Every file of that version is present and identical."""
    return bool(result["files"]) and not result["differing"] \
        and not result["missing"]


def describe(result):
    """One line: what this profile holds, in terms of that version."""
    if exact(result):
        extra = (f", plus {len(result['extra'])} file(s) not in it"
                 if result["extra"] else "")
        eol = " (line endings differ, contents do not)" if result["eol"] else ""
        return f"exactly {result['ref']}{extra}{eol}"
    parts = [f"{result['matched']}/{result['files']} files match"]
    if result["differing"]:
        parts.append(f"{len(result['differing'])} edited")
    if result["missing"]:
        parts.append(f"{len(result['missing'])} missing")
    if result["extra"]:
        parts.append(f"{len(result['extra'])} added")
    return f"{result['ref']}: " + ", ".join(parts)
