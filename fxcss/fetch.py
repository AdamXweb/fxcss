#!/usr/bin/env python3
"""Fetch a theme from a public forge so it can be test-driven or installed.

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

Hosts: GitHub is first-class (releases, branch tips, the git-tree API that
`adopt` compares against, and tokens from GITHUB_TOKEN / GH_TOKEN / `gh`).
Codeberg and other Forgejo instances, and GitLab, are supported for
resolving, downloading and update checks through their public APIs; `adopt`
says so when it meets one of them rather than guessing. A GitHub token is
only ever sent to GitHub hosts, including across redirects.
"""

import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.github.com"


class FetchError(RuntimeError):
    """A problem reaching or reading a theme, reported as a message, not a traceback."""


class RepoSpecError(FetchError, ValueError):
    """The argument is neither owner/name nor a URL on a supported host."""


USER_AGENT = "fxcss (+https://github.com/AdamXweb/fxcss)"

# A theme is small. Anything wildly bigger than this is not one, and unpacking it
# blindly would be careless.
MAX_ARCHIVE_BYTES = 80 * 1024 * 1024
MAX_UNPACKED_BYTES = 200 * 1024 * 1024

# Hosts a repository can come from, and which API dialect each speaks.
# "forgejo" covers Codeberg, git.gay and any other Forgejo/Gitea instance
# listed here; adding one is a line.
HOSTS = {
    "github.com": "github",
    "codeberg.org": "forgejo",
    "git.gay": "forgejo",
    "gitlab.com": "gitlab",
}
REMOTE_KINDS = ("github", "forgejo", "gitlab")
# Where a GitHub token may travel. Anywhere else gets an anonymous request.
GITHUB_TOKEN_HOSTS = ("api.github.com", "codeload.github.com", "github.com",
                      "raw.githubusercontent.com", "objects.githubusercontent.com")

REPO_SPEC = re.compile(
    r"^(?:https?://)?(?:www\.)?(?:github\.com/)?([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")
# A browser URL for a branch or folder: github.com/o/n/tree/<ref>[/<path>],
# codeberg.org/o/n/src/branch/<ref>[/<path>], gitlab.com/o/n/-/tree/<ref>[/<path>].
TREE_URL = re.compile(
    r"^(?:https?://)?(?:www\.)?(?P<host>[\w.-]+)/(?P<owner>[\w.-]+)/(?P<name>[\w.-]+?)"
    r"(?:\.git)?(?:/(?:-/)?(?:tree|src/branch|src/tag|blob|src)/(?P<ref>[^/]+)"
    r"(?:/(?P<subpath>.*?))?)?/?$")

INSTALL_SCRIPTS = [
    "install.sh", "install.bat", "install.ps1", "install.py",
    "setup.sh", "Makefile", "makefile", "justfile",
]

# Themes commonly keep optional tweaks in one of these (beside chrome/ or
# inside it). One list for installation, captures and generated workflows.
from .inventory import OPTION_DIRS as _OPTION_DIRS  # noqa: E402
VARIANT_DIRS = list(_OPTION_DIRS)

# "- `-c` Left hand side tab close button" and similar list items. Inside
# backticks the flag keeps its argument: "- `-t <name>` Colour theme".
FLAG_LINE = re.compile(
    r"^\s*[-*+]\s*`?((?<=`)-{1,2}[\w-]+(?:[\s=][^`]*[^`\s])?(?=`)|-{1,2}[\w-]+)`?"
    r"\s*[—:-]?\s+(.{4,120}?)\s*$")


def parse_repo_spec(spec: str):
    """Take a repository argument apart.

    Returns {host, kind, owner, name, ref, subpath}. `ref` and `subpath`
    are set only for a branch/folder URL such as
    ``https://github.com/o/n/tree/photon-style/themes/x``; the catalogue
    carries such URLs, and the branch in them is part of what the author
    means by "this theme". A bare ``owner/name`` is GitHub.
    """
    text = (spec or "").strip()
    match = REPO_SPEC.match(text)
    if match:
        return {"host": "github.com", "kind": "github", "owner": match.group(1),
                "name": match.group(2), "ref": None, "subpath": None}
    match = TREE_URL.match(text)
    if match and match.group("host") in HOSTS:
        name = match.group("name")
        subpath = (match.group("subpath") or "").strip("/") or None
        return {"host": match.group("host"), "kind": HOSTS[match.group("host")],
                "owner": match.group("owner"), "name": name,
                "ref": match.group("ref") or None, "subpath": subpath}
    if match and "." in match.group("host"):
        raise RepoSpecError(
            f"{match.group('host')} is not a host fxcss can fetch from; supported: "
            + ", ".join(sorted(HOSTS)) + ". Clone the theme and pass its folder.")
    raise RepoSpecError(
        f"could not read {spec!r} as a repository. Use owner/name, a github.com "
        "URL (optionally /tree/<branch>/<folder>), or a codeberg.org / gitlab.com URL.")


def parse_repo(spec: str):
    """(owner, name) for a GitHub repository spec; the long-standing contract."""
    parsed = parse_repo_spec(spec)
    if parsed["kind"] != "github":
        raise RepoSpecError(
            f"{parsed['host']} repositories need the full URL form here; "
            "this operation compares against GitHub's API and supports GitHub only.")
    return parsed["owner"], parsed["name"]


# A token is optional. GITHUB_TOKEN or GH_TOKEN, when set, is sent with every
# GitHub request: it raises the anonymous rate limit and opens private
# repositories. Without one, fxcss asks the GitHub CLI for its login, but
# only after an anonymous request found nothing or was rate-limited, since a
# private repository looks exactly like a missing one to anyone who cannot
# read it. The token itself is never printed; messages name its source.
_cli_login = {"token": None, "tried": False}


def _token():
    """The token in use and where it came from, or (None, None)."""
    for var in ("GITHUB_TOKEN", "GH_TOKEN"):
        if os.environ.get(var):
            return os.environ[var], var
    if _cli_login["token"]:
        return _cli_login["token"], "gh auth token"
    return None, None


def _use_cli_login():
    """Pick up `gh auth token` once. True when a new token is now in use."""
    if _token()[0] or _cli_login["tried"]:
        return False
    _cli_login["tried"] = True
    gh = shutil.which("gh")
    if not gh:
        return False
    try:
        result = subprocess.run([gh, "auth", "token"], capture_output=True,
                                text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return False
    token = result.stdout.strip()
    if result.returncode != 0 or not token:
        return False
    _cli_login["token"] = token
    return True


def _github_host(url):
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return host in GITHUB_TOKEN_HOSTS


class _NoLeakRedirects(urllib.request.HTTPRedirectHandler):
    """Drop the Authorization header when a redirect leaves GitHub.

    urllib copies every header onto the redirected request; a token meant for
    api.github.com must not follow a 302 to an arbitrary host.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and not _github_host(newurl):
            new.remove_header("Authorization")
        return new


# Installed as the module-wide opener so that every urlopen in this process
# -- and anything a test patches -- goes through the same redirect rule.
urllib.request.install_opener(urllib.request.build_opener(_NoLeakRedirects))


def _request(url, accept="application/vnd.github+json"):
    headers = {"User-Agent": USER_AGENT, "Accept": accept}
    token = _token()[0]
    if token and _github_host(url):
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.Request(url, headers=headers)


def _open(request, timeout):
    return urllib.request.urlopen(request, timeout=timeout)


def _unreachable(exc, what="GitHub"):
    return FetchError(f"could not reach {what} ({getattr(exc, 'reason', exc)}). "
                      "Check the connection and try again.")


def _rate_limited(exc):
    remaining = exc.headers.get("X-RateLimit-Remaining") if exc.headers else None
    return exc.code == 429 or (exc.code == 403 and remaining in (None, "0"))


def _api(path):
    """One GitHub API call; None for 404, FetchError for anything else.

    An anonymous request that hits the rate limit is retried once with the
    GitHub CLI's login when one exists: a signed-in person should not be
    told to sign in.
    """
    try:
        with _open(_request(API + path), timeout=45) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        if exc.code == 401:
            raise FetchError(
                f"GitHub rejected the token from {_token()[1]} (401 Unauthorized). "
                "It may have expired or been revoked: replace it, or unset it to "
                "use public repositories without one.") from exc
        if exc.code in (403, 429):
            if _rate_limited(exc) and _use_cli_login():
                return _api(path)
            token, source = _token()
            hint = ("The request was made with the token from "
                    f"{source}; wait for the limit to reset, or pass --ref to "
                    "skip the lookups." if token else
                    "Set GITHUB_TOKEN, or sign in with `gh auth login`, to raise "
                    "it; or pass --ref to skip the lookups.")
            raise FetchError(f"GitHub refused the request (rate limit). {hint}") from exc
        raise FetchError(f"GitHub answered {exc.code} {exc.reason} for {path}") from exc
    except urllib.error.URLError as exc:
        raise _unreachable(exc) from exc


def _api_url(url, what):
    """One call to another host's JSON API; None for 404."""
    try:
        with _open(_request(url, accept="application/json"), timeout=45) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        if exc.code in (403, 429):
            raise FetchError(f"{what} refused the request (rate limit); try again later "
                             "or pass --ref to skip the lookups.") from exc
        raise FetchError(f"{what} answered {exc.code} {exc.reason} for {url}") from exc
    except urllib.error.URLError as exc:
        raise _unreachable(exc, what) from exc


def not_found(owner, name, host="github.com"):
    """What to say when a repository cannot be read, and what to try next."""
    token, source = _token()
    if host != "github.com":
        return FetchError("\n".join([
            f"{host} has no public repository {owner}/{name}.",
            f"  Check the name at https://{host}/{owner}/{name}",
            "  For a theme on this computer, pass its folder instead, "
            "such as ~/src/my-theme",
        ]))
    if token:
        first = (f"no repository {owner}/{name} that the GitHub token from "
                 f"{source} can read.")
        access = ("  If it is private, use a token or `gh` login whose account "
                  "can read it.")
    else:
        first = f"GitHub has no public repository {owner}/{name}."
        access = ("  If it is private, sign in with `gh auth login` or set "
                  "GITHUB_TOKEN to a token that can read it.")
    return FetchError("\n".join([
        first,
        f"  Check the name at https://github.com/{owner}/{name}",
        access,
        "  For a theme on this computer, pass its folder instead, "
        "such as ~/src/my-theme",
    ]))


def _commit_info(sha, date, message):
    sha = sha or ""
    return {"sha": sha[:7], "full_sha": sha, "date": date or "",
            "message": (message or "").splitlines()[0][:72] if message else ""}


def resolve(owner, name, host="github.com", branch=None):
    """Report what is available: the default branch tip and the latest release.

    `branch` names a branch the person asked for (from a /tree/<branch> URL
    or a manifest); its tip is reported under `tracked` so an update check
    compares against the right thing. Renamed repositories resolve to their
    canonical owner/name, reported under `renamed_from`, because the old
    name still answers on the API but not on the download host.
    """
    kind = HOSTS.get(host, "github")
    if kind == "forgejo":
        return _resolve_forgejo(owner, name, host, branch)
    if kind == "gitlab":
        return _resolve_gitlab(owner, name, host, branch)

    repo = _api(f"/repos/{owner}/{name}")
    if repo is None and _use_cli_login():
        repo = _api(f"/repos/{owner}/{name}")
    if repo is None:
        raise not_found(owner, name)

    renamed_from = None
    full_name = repo.get("full_name") or ""
    if "/" in full_name and full_name.lower() != f"{owner}/{name}".lower():
        renamed_from = f"{owner}/{name}"
        owner, name = full_name.split("/", 1)

    default = repo.get("default_branch", "main")
    commit = _api(f"/repos/{owner}/{name}/commits/{urllib.parse.quote(default, safe='')}") or {}
    release = _api(f"/repos/{owner}/{name}/releases/latest")
    tracked = None
    if branch and branch != default:
        data = _api(f"/repos/{owner}/{name}/branches/{urllib.parse.quote(branch, safe='')}")
        if data and isinstance(data.get("commit"), dict):
            inner = data["commit"].get("commit") or {}
            tracked = dict(_commit_info(data["commit"].get("sha"),
                                        ((inner.get("author") or {}).get("date")),
                                        inner.get("message")), ref=branch)
        else:
            tracked = {"ref": branch, "missing": True}

    info = {
        "owner": owner, "name": name, "host": host, "kind": "github",
        "renamed_from": renamed_from,
        "description": repo.get("description") or "",
        "stars": repo.get("stargazers_count", 0),
        "licence": (repo.get("license") or {}).get("spdx_id"),
        "default_branch": default,
        "commit": _commit_info(
            commit.get("sha"),
            ((commit.get("commit", {}) or {}).get("author", {}) or {}).get("date", ""),
            (commit.get("commit", {}) or {}).get("message")) if commit else None,
        "release": {
            "tag": release.get("tag_name"),
            "name": release.get("name") or release.get("tag_name"),
            "date": release.get("published_at", ""),
        } if release else None,
        "tracked": tracked,
    }
    return info


def _resolve_forgejo(owner, name, host, branch):
    base = f"https://{host}/api/v1/repos/{owner}/{name}"
    repo = _api_url(base, host)
    if repo is None:
        raise not_found(owner, name, host)
    renamed_from = None
    full_name = repo.get("full_name") or ""
    if "/" in full_name and full_name.lower() != f"{owner}/{name}".lower():
        renamed_from = f"{owner}/{name}"
        owner, name = full_name.split("/", 1)
        base = f"https://{host}/api/v1/repos/{owner}/{name}"
    default = repo.get("default_branch") or "main"

    def branch_tip(ref):
        data = _api_url(f"{base}/branches/{urllib.parse.quote(ref, safe='')}", host)
        if not data or not isinstance(data.get("commit"), dict):
            return None
        commit = data["commit"]
        return _commit_info(commit.get("id"), commit.get("timestamp"), commit.get("message"))

    release = _api_url(f"{base}/releases/latest", host)
    tip = branch_tip(default)
    tracked = None
    if branch and branch != default:
        tracked = branch_tip(branch)
        tracked = dict(tracked, ref=branch) if tracked else {"ref": branch, "missing": True}
    return {
        "owner": owner, "name": name, "host": host, "kind": "forgejo",
        "renamed_from": renamed_from,
        "description": repo.get("description") or "",
        "stars": repo.get("stars_count", 0),
        "licence": None,
        "default_branch": default,
        "commit": tip,
        "release": {"tag": release.get("tag_name"),
                    "name": release.get("name") or release.get("tag_name"),
                    "date": release.get("published_at", "")} if release else None,
        "tracked": tracked,
    }


def _resolve_gitlab(owner, name, host, branch):
    project = urllib.parse.quote(f"{owner}/{name}", safe="")
    base = f"https://{host}/api/v4/projects/{project}"
    repo = _api_url(base, host)
    if repo is None:
        raise not_found(owner, name, host)
    renamed_from = None
    full_name = repo.get("path_with_namespace") or ""
    if "/" in full_name and full_name.lower() != f"{owner}/{name}".lower():
        renamed_from = f"{owner}/{name}"
        owner, name = full_name.rsplit("/", 1)
        base = f"https://{host}/api/v4/projects/{urllib.parse.quote(full_name, safe='')}"
    default = repo.get("default_branch") or "main"

    def branch_tip(ref):
        data = _api_url(f"{base}/repository/branches/{urllib.parse.quote(ref, safe='')}", host)
        if not data or not isinstance(data.get("commit"), dict):
            return None
        commit = data["commit"]
        return _commit_info(commit.get("id"), commit.get("committed_date"),
                            commit.get("title") or commit.get("message"))

    releases = _api_url(f"{base}/releases?per_page=1", host) or []
    release = releases[0] if isinstance(releases, list) and releases else None
    tip = branch_tip(default)
    tracked = None
    if branch and branch != default:
        tracked = branch_tip(branch)
        tracked = dict(tracked, ref=branch) if tracked else {"ref": branch, "missing": True}
    return {
        "owner": owner, "name": name, "host": host, "kind": "gitlab",
        "renamed_from": renamed_from,
        "description": repo.get("description") or "",
        "stars": repo.get("star_count", 0),
        "licence": None,
        "default_branch": default,
        "commit": tip,
        "release": {"tag": release.get("tag_name"),
                    "name": release.get("name") or release.get("tag_name"),
                    "date": release.get("released_at", "")} if release else None,
        "tracked": tracked,
    }


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


def known_refs(owner, name, limit=10):
    """Versions worth checking a profile's files against, newest first.

    Releases first, because a theme installed by hand was almost certainly
    installed from one, then any remaining tags. The default branch goes last
    -- someone who cloned rather than downloaded is on it, but it is a moving
    target and a poor thing to pin a profile to without being asked. A
    branch entry carries the commit it points at, so what gets recorded is
    the commit and not the name.
    """
    refs, seen = [], set()

    def add(ref, kind, sha=None):
        if ref and ref not in seen:
            seen.add(ref)
            refs.append({"ref": ref, "kind": kind, "sha": sha})

    for release in _api(f"/repos/{owner}/{name}/releases?per_page={limit}") or []:
        add(release.get("tag_name"), "release")
    for tag in _api(f"/repos/{owner}/{name}/tags?per_page={limit}") or []:
        add(tag.get("name"), "tag")
    repo = _api(f"/repos/{owner}/{name}") or {}
    default = repo.get("default_branch")
    if default:
        head = _api(f"/repos/{owner}/{name}/commits/{urllib.parse.quote(default, safe='')}") or {}
        add(default, "branch", head.get("sha"))
    return refs[:limit]


def branch_head(owner, name, branch):
    """The full commit SHA a GitHub branch points at now, or None."""
    data = _api(f"/repos/{owner}/{name}/commits/{urllib.parse.quote(branch, safe='')}")
    return (data or {}).get("sha") or None


def require_commit(sha, ref):
    """Require an immutable Git commit before asserting branch provenance."""
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
        raise FetchError(f"could not resolve branch {ref!r} to a full commit SHA; "
                         "nothing was installed or adopted")
    return sha.lower()


def snapshot_ref(ref, info):
    """Pin a known branch while retaining its name separately for tracking."""
    if not info:
        return ref  # explicit refs deliberately skip discovery
    if ref == info.get("default_branch"):
        commit = info.get("commit") or {}
    elif ref == (info.get("tracked") or {}).get("ref"):
        commit = info["tracked"]
    else:
        return ref  # release/tag or an explicitly requested ref
    return require_commit(commit.get("full_sha") or commit.get("sha"), ref)


def tree(owner, name, ref):
    """{path: blob sha} for every file in a repository at a ref.

    One request per ref rather than an archive download: identifying a theme
    means comparing against several versions, and this keeps that cheap
    enough to do. Returns {} when the tree comes back truncated -- GitHub
    caps very large trees, and a partial listing would understate how much of
    a theme matches, which reads as "this is not it".
    """
    from urllib.parse import quote
    data = _api(f"/repos/{owner}/{name}/git/trees/{quote(str(ref), safe='')}"
                "?recursive=1")
    if not data or data.get("truncated"):
        return {}
    return {entry["path"]: entry["sha"] for entry in data.get("tree", [])
            if entry.get("type") == "blob" and entry.get("path")}


def same_commit(recorded, current):
    """Do two commit ids name the same commit?

    Older manifests recorded the seven-character short form; a full SHA
    recorded now still has to compare equal to it. Either way an empty value
    matches nothing.
    """
    recorded, current = (recorded or "").lower(), (current or "").lower()
    if not recorded or not current:
        return False
    if len(recorded) < 7 or len(current) < 7:
        return recorded == current
    return recorded.startswith(current) or current.startswith(recorded)


def update_state(source, info):
    """Is what a profile has installed still what upstream offers?

    `source` is a manifest's source dict, `info` what `resolve()` reports now.
    Returns {state, ref, label, detail, also}, where state is one of:

      current      installed ref is upstream's newest of the kind tracked
      available    something newer exists; `ref` is what to move to
      pinned       an explicit --ref was asked for, so newer is not "behind"
      unknown      the manifest predates recording how the ref was chosen
      unsupported  a local directory install: there is no upstream to ask

    `also` carries the other side of the picture without acting on it: a
    release-tracked install that is current while the branch has moved on
    says so, and stays on releases. Kept separate from any fetching so it
    can be tested without a network, and stated as outcomes rather than a
    boolean because "fxcss cannot tell" has to be renderable.
    """
    source = source or {}
    kind = source.get("kind")
    if kind == "local":
        return {"state": "unsupported", "ref": None, "label": "local install",
                "detail": source.get("path") or "", "also": ""}
    if kind not in REMOTE_KINDS:
        return {"state": "unknown", "ref": None, "label": "unknown source",
                "detail": "nothing recorded about where this came from", "also": ""}

    ref_kind = source.get("ref_kind")
    if ref_kind == "explicit":
        return {"state": "pinned", "ref": None,
                "label": f"pinned to {source.get('ref')}",
                "detail": "installed with --ref, so nothing is checked", "also": ""}
    if ref_kind == "release":
        latest = (info.get("release") or {}).get("tag")
        if not latest:
            return {"state": "unknown", "ref": None,
                    "label": "no releases upstream",
                    "detail": "the theme published none to compare against", "also": ""}
        also = ""
        if commit_is_newer(info):
            commit = info.get("commit") or {}
            also = (f"{info.get('default_branch')} has moved on since the release "
                    f"({(commit.get('date') or '')[:10]}); `--commit` would follow it")
        if latest == source.get("ref"):
            return {"state": "current", "ref": latest, "label": latest,
                    "detail": "", "also": also}
        return {"state": "available", "ref": latest, "label": latest,
                "detail": (info.get("release") or {}).get("date", "")[:10], "also": also}
    if ref_kind == "branch":
        ref = source.get("ref")
        tracked = info.get("tracked") or {}
        if tracked.get("ref") == ref and ref != info.get("default_branch"):
            if tracked.get("missing"):
                return {"state": "unknown", "ref": None,
                        "label": f"branch {ref} no longer exists upstream",
                        "detail": "choose another with --ref", "also": ""}
            commit = tracked
        else:
            commit = info.get("commit") or {}
        sha = commit.get("full_sha") or commit.get("sha")
        if not sha:
            return {"state": "unknown", "ref": None,
                    "label": "branch tip unreadable", "detail": "", "also": ""}
        short = sha[:7]
        if same_commit(source.get("resolved"), sha):
            return {"state": "current", "ref": ref,
                    "label": f"{ref} @ {short}", "detail": "", "also": ""}
        return {"state": "available", "ref": ref,
                "label": f"{ref} @ {short}",
                "detail": (commit.get("message") or "")[:52], "also": ""}
    return {"state": "unknown", "ref": None, "label": "ref kind not recorded",
            "detail": "installed before fxcss recorded this; reinstall to fix", "also": ""}


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
            raise FetchError(f"archive tried to write outside the target: {member.name}")
        total += max(member.size, 0)
        if total > MAX_UNPACKED_BYTES:
            raise FetchError("archive unpacks to more than 200 MB; refusing")
        yield member


def archive_url(owner, name, ref, host="github.com"):
    """Where a host serves a tar.gz of a repository at a ref."""
    kind = HOSTS.get(host, "github")
    quoted = urllib.parse.quote(ref, safe="/")
    if kind == "forgejo":
        return f"https://{host}/{owner}/{name}/archive/{quoted}.tar.gz"
    if kind == "gitlab":
        flat = ref.replace("/", "-")
        return f"https://{host}/{owner}/{name}/-/archive/{quoted}/{name}-{flat}.tar.gz"
    return f"https://codeload.github.com/{owner}/{name}/tar.gz/{quoted}"


def download(owner, name, ref, into: Path, host="github.com"):
    """Download and unpack a repository at a ref. Returns the unpacked root.

    On GitHub a renamed repository still answers on the API but 404s on the
    download host under its old name, so a 404 here is followed by one
    canonical-name lookup before giving up.
    """
    url = archive_url(owner, name, ref, host)
    what = "GitHub" if host == "github.com" else host

    def fetch(target):
        with _open(_request(target, accept="*/*"), timeout=180) as response:
            return response.read(MAX_ARCHIVE_BYTES + 1)

    try:
        try:
            payload = fetch(url)
        except urllib.error.HTTPError as exc:
            if exc.code != 404 or host != "github.com":
                raise
            # With --ref there was no lookup first, so this may be the first
            # sign that the repository is private -- or that it was renamed.
            if _use_cli_login():
                try:
                    payload = fetch(url)
                except urllib.error.HTTPError as again:
                    if again.code != 404:
                        raise
                    payload = None
            else:
                payload = None
            if payload is None:
                repo = _api(f"/repos/{owner}/{name}") or {}
                canonical = repo.get("full_name") or ""
                if "/" in canonical and canonical.lower() != f"{owner}/{name}".lower():
                    owner, name = canonical.split("/", 1)
                    payload = fetch(archive_url(owner, name, ref, host))
                else:
                    raise
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise FetchError(
                f"could not download {owner}/{name} at {ref!r}: that repository "
                "or ref was not found.\n"
                f"  {not_found(owner, name, host).args[0].splitlines()[0]}") from exc
        raise FetchError(f"could not download {owner}/{name}@{ref}: {exc}") from exc
    except urllib.error.URLError as exc:
        raise _unreachable(exc, what) from exc
    if len(payload) > MAX_ARCHIVE_BYTES:
        raise FetchError("archive is larger than 80 MB; refusing")

    into.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            members = _safe_members(archive, into)
            try:
                # Python 3.12+: the 'data' filter also strips absolute paths and
                # rejects links, complementing the checks in _safe_members.
                archive.extractall(into, members=members, filter="data")
            except TypeError:  # Python < 3.12 has no filter parameter
                archive.extractall(into, members=members)
    except tarfile.TarError as exc:
        raise FetchError(f"the download from {what} was not a readable archive: {exc}") from exc

    entries = [p for p in into.iterdir() if p.is_dir()]
    if len(entries) == 1:
        return entries[0]  # hosts wrap everything in one directory
    return into


def find_theme_root(unpacked: Path):
    """Locate the folder that holds chrome/userChrome.css.

    Delegates to the layout planner, so the answer is never a directory
    outside `unpacked` and is None for every layout that needs a plan
    (flat, content-only, several editions) rather than a guess.
    """
    from .layout import find_theme_root as planned
    return planned(unpacked)


def _inside(path, root):
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


def describe(repo_root: Path, theme_root: Path, plan=None):
    """What this theme offers, and how its author says to install it.

    `variants` keeps its old shape (a list of sheet paths) for callers that
    show names; `options` carries the same sheets with their stable relative
    identities, from the shared inventory. With `plan` (a layout plan whose
    theme root was materialised elsewhere) those identities are still
    repository-relative, so a manifest written from a flat layout names
    ``tweaks/x.css`` and not a temporary folder.
    """
    from . import inventory

    scripts = [name for name in INSTALL_SCRIPTS if (repo_root / name).is_file()]

    options = []
    seen = set()
    roots = [theme_root] if theme_root else []
    if theme_root is None or (_inside(theme_root, repo_root)
                              and Path(theme_root).resolve() != Path(repo_root).resolve()):
        roots.append(repo_root)
    selected = (plan or {}).get("selected") or {}
    chrome_dir = selected.get("chrome_dir", "") if (plan or {}).get("kind") != "standard" else None
    for root in roots:
        chrome = (root / "chrome") if root else None
        for option in inventory.options(root, chrome if chrome and chrome.is_dir() else None):
            key = option["path"].resolve()
            if key in seen:
                continue
            seen.add(key)
            if _inside(option["path"], repo_root):
                option["id"] = option["path"].resolve().relative_to(
                    Path(repo_root).resolve()).as_posix()
            elif chrome_dir is not None and chrome and _inside(option["path"], chrome):
                inner = option["path"].resolve().relative_to(chrome.resolve()).as_posix()
                option["id"] = f"{chrome_dir}/{inner}" if chrome_dir else inner
            options.append(option)

    if (plan or {}).get("kind") == "named" and theme_root:
        # A named edition's loose sheets -- WhiteSur's src/common/hide-single-tab.css,
        # which its installer copies in on request -- are options too. Only
        # stylesheets nothing reaches count, under their repository path.
        source_dir = Path(plan["repo_root"]) / (selected.get("chrome_dir") or "")
        reachable = inventory.reachable_paths(theme_root / "chrome")
        for folder in ("", "common", "shared"):
            directory = source_dir / folder if folder else source_dir
            if not directory.is_dir():
                continue
            for css in sorted(directory.glob("*.css")):
                if not css.is_file() or inventory._is_hook(css.name):
                    continue
                if any(css.name == Path(r).name for r in reachable):
                    continue
                from .layout import NAMED_ENTRY_RE
                if NAMED_ENTRY_RE.match(css.name) or css.resolve() in seen:
                    continue
                seen.add(css.resolve())
                options.append({"id": css.relative_to(repo_root).as_posix(), "path": css,
                                "stem": css.stem, "folder": folder or "edition",
                                "inside_chrome": False})

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
        "variants": [option["path"] for option in options],
        "options": options,
        "flags": flags[:14],
        "stylesheets": len(sheets),
        "bytes": sum(p.stat().st_size for p in sheets),
        "has_configuration": (theme_root / "configuration").is_dir() if theme_root else False,
    }


def sheet_continuity(wanted, variants, options=None):
    """Do the optional sheets someone installed still exist upstream?

    Returns {kept: [Path], missing: [str]}, matching each recorded name
    against the new version: by relative path first when `options` (the
    inventory's option dicts) are given, then by stem. A sheet that has been
    renamed or dropped between versions is the quiet way an upgrade takes
    away a setting: the @import simply stops resolving and the option turns
    itself off. Naming the loss is the whole job here -- the caller decides
    whether that stops the upgrade.
    """
    from . import inventory

    if options is None:
        options = [{"id": sheet.as_posix(), "path": sheet, "stem": sheet.stem,
                    "folder": sheet.parent.name, "inside_chrome": False}
                   for sheet in variants]
    kept, missing = [], []
    for name in wanted or []:
        option = inventory.match_option(name, options)
        if option is None:
            missing.append(str(name))
        elif option["path"] not in kept:
            kept.append(option["path"])
    return {"kept": kept, "missing": missing}


def apply_variants(profile: Path, chosen):
    """Layer optional stylesheets on top, by import rather than by copying.

    Themes disagree about where an optional sheet belongs -- some expect it
    inside their own folder, some rely on their installer editing an import
    list. An import-only wrapper loads the base before the optional sheets,
    and relative url()s inside each sheet still resolve because the files stay
    where they were unpacked.
    """
    user_chrome = profile / "chrome" / "userChrome.css"
    if not user_chrome.exists() or not chosen:
        return []
    from .core import layer_stylesheets
    layer_stylesheets(user_chrome, [sheet.resolve().as_uri() for sheet in chosen])
    return chosen


def humanise(info):
    """Lines describing what was found upstream."""
    out = []
    header = f"{info['owner']}/{info['name']}"
    if info.get("host") and info["host"] != "github.com":
        header = f"{info['host']}/{header}"
    if info["stars"]:
        header += f"  ★{info['stars']}"
    if info["licence"] and info["licence"] != "NOASSERTION":
        header += f"  {info['licence']}"
    out.append(header)
    if info.get("renamed_from"):
        out.append(f"  (moved from {info['renamed_from']}; recorded under its new name)")
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
    tracked = info.get("tracked")
    if tracked and not tracked.get("missing"):
        out.append(f"  branch {tracked['ref']:<10} {tracked['sha']}"
                   f"  ({tracked['date'][:10]})  {tracked['message']}")
    elif tracked:
        out.append(f"  branch {tracked['ref']}: not found upstream")
    return out
