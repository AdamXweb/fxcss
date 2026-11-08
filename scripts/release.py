"""Release rules shared by the Release, Release version and Publish workflows.

A release is one pull request titled "Prepare vX.Y.Z" that changes
fxcss.__version__ to X.Y.Z. Merging it tags vX.Y.Z and starts Publish. Once
PyPI serves the release, Publish moves the README's pins and refreshes the
website. These rules keep each of those steps from being skipped or run for a
version nobody meant to release.

    python3 scripts/release.py check-pr BASE_SHA   # Release version (pull requests)
    python3 scripts/release.py plan                # Release (main)
    python3 scripts/release.py bump-pins VERSION README.md

check-pr reads PR_TITLE from the environment, and plan reads EVENT_NAME and
PR_TITLE, so a title is never interpolated into a shell script.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = "fxcss/__init__.py"
VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
PIN = re.compile(r"(fxcss(?:\[[A-Za-z0-9_,-]+\])?==)(\d+\.\d+\.\d+)")


def parse(version):
    match = VERSION.match(version)
    if not match:
        raise ValueError(f"{version!r} is not a MAJOR.MINOR.PATCH version")
    return tuple(int(part) for part in match.groups())


def read_version(text):
    match = re.search(r'^__version__ = "([^"]+)"$', text, re.M)
    if not match:
        raise ValueError(f"no __version__ in {VERSION_FILE}")
    parse(match[1])
    return match[1]


def released(tags):
    """The versions of v-prefixed MAJOR.MINOR.PATCH tags."""
    versions = []
    for tag in tags:
        if tag.startswith("v") and VERSION.match(tag[1:]):
            versions.append(tag[1:])
    return versions


def is_prepare_title(title, version):
    """A release PR says "Prepare vX.Y.Z" in its title, as its squash commit will."""
    return re.search(r"\bprepare v" + re.escape(version) + r"(?![\w.])", title or "", re.I) is not None


def check_pr(base, head, title, tags):
    """Problems with a pull request that changes the version, or [] if none."""
    if base == head:
        return []
    problems = []
    if parse(head) <= parse(base):
        problems.append(f"The version moves from {base} to {head}; a release must increase it.")
    if head in released(tags):
        problems.append(f"v{head} is already tagged; choose the next version.")
    if not is_prepare_title(title, head):
        problems.append(
            f"This pull request changes the version from {base} to {head}, so merging it "
            f"releases fxcss {head}. Only a release PR may do that: title it "
            f"\"Prepare v{head}\", or leave __version__ at {base} and let the release PR move it.")
    return problems


def plan(version, tags, event, title):
    """Return (release, message). A message without a release is a failure only if it says so."""
    versions = released(tags)
    if version in versions:
        return False, f"fxcss {version} is already tagged; nothing to release."
    newest = max(versions, key=parse) if versions else None
    if newest and parse(version) <= parse(newest):
        return False, (f"error: main is at fxcss {version}, which is not newer than the "
                       f"latest release, v{newest}.")
    if event == "workflow_dispatch" or is_prepare_title(title, version):
        return True, f"Releasing fxcss {version}."
    return False, (f"error: main is at fxcss {version}, which is not tagged, but this commit "
                   f"is not from a \"Prepare v{version}\" pull request, so it was not released. "
                   "Run the Release workflow by hand to release it, or set __version__ "
                   "back to the latest release.")


def bump_pins(text, version):
    """Move every fxcss==X.Y.Z pin older than version to version."""
    target = parse(version)
    count = 0

    def replace(match):
        nonlocal count
        if parse(match[2]) < target:
            count += 1
            return match[1] + version
        return match[0]

    return PIN.sub(replace, text), count


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, check=True, text=True,
                          capture_output=True).stdout


def output(**values):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            for key, value in values.items():
                handle.write(f"{key}={value}\n")


def main(argv):
    command, *args = argv or [""]
    if command == "check-pr" and len(args) == 1:
        base = read_version(git("show", f"{args[0]}:{VERSION_FILE}"))
        head = read_version((ROOT / VERSION_FILE).read_text(encoding="utf-8"))
        problems = check_pr(base, head, os.environ.get("PR_TITLE", ""),
                            git("tag", "--list", "v*").split())
        for problem in problems:
            print(f"::error title=Release version::{problem}")
        if not problems:
            print(f"fxcss {head}: " + ("no version change." if base == head
                                         else f"release PR from {base}."))
        return 1 if problems else 0
    if command == "plan" and not args:
        version = read_version((ROOT / VERSION_FILE).read_text(encoding="utf-8"))
        release, message = plan(version, git("tag", "--list", "v*").split(),
                                os.environ.get("EVENT_NAME", ""), os.environ.get("PR_TITLE", ""))
        failed = message.startswith("error: ")
        print(f"::error title=Release::{message[7:]}" if failed else
              f"::notice title=Release::{message}")
        output(version=version, release="yes" if release else "no")
        return 1 if failed else 0
    if command == "bump-pins" and len(args) == 2:
        version, path = args
        parse(version)
        text = Path(path).read_text(encoding="utf-8")
        text, count = bump_pins(text, version)
        if count:
            Path(path).write_text(text, encoding="utf-8")
        print(f"Moved {count} pin(s) in {path} to fxcss {version}.")
        output(changed="yes" if count else "no")
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
