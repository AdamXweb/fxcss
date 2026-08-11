#!/usr/bin/env python3
"""Generate CI workflows for a theme repository: the machinery behind
`fxcss init`.

The templates ship inside the package, so a `pipx install fxcss` user gets
them without ever visiting the repository. Three things are substituted per
theme, which is exactly the hand-editing that used to trip people up:

* the fxcss version pin (a PyPI release), set to the version doing the
  generating
* the publish allowlist's variant names, enumerated from the theme's own
  optional stylesheets -- an allowlist someone has to remember to extend is
  an allowlist that silently drops views
* the showcase URL, taken from the repository's origin remote
"""

import re
import subprocess
from importlib import resources
from pathlib import Path

from . import __version__

PREVIEW_FILES = ("pr-preview.yml", "pr-preview-publish.yml", "pr-preview-cleanup.yml")
WATCH_FILE = "firefox-watch.yml"
SHOWCASE_FILE = "showcase.yml"


def variant_alternation(slugs):
    """The regex fragment that admits this theme's variant captures.

    Empty when there are no variants, so the allowlist stays as tight as the
    theme is simple.
    """
    names = sorted(s for s in slugs if re.fullmatch(r"[a-z0-9+-]+", s))
    if not names:
        return ""
    # Only '+' needs escaping in this position; re.escape would also escape
    # '-', which is harmless but makes the generated workflow harder to read.
    return "|variant-(?:" + "|".join(n.replace("+", "\\+") for n in names) + ")"


def https_repo_url(remote):
    """Normalise a git remote to a browsable https URL, or None."""
    if not remote:
        return None
    remote = remote.strip()
    match = re.match(r"^git@([^:]+):(.+?)(?:\.git)?$", remote)
    if match:
        return f"https://{match.group(1)}/{match.group(2)}"
    match = re.match(r"^(https?://[^ ]+?)(?:\.git)?/?$", remote)
    if match:
        return match.group(1)
    return None


def detect_repo_url(theme: Path):
    try:
        raw = subprocess.run(
            ["git", "-C", str(theme), "config", "--get", "remote.origin.url"],
            capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        raw = ""
    return https_repo_url(raw)


def render_template(text, version, variant_alt, repo_url):
    text = text.replace("__FXCSS_VERSION__", version)
    text = text.replace("__FXCSS_VARIANT_ALT__", variant_alt)
    text = text.replace("__FXCSS_REPO_URL__", repo_url or "https://example.com")
    return text


def _template(name):
    return (resources.files("fxcss") / "templates" / name).read_text(encoding="utf-8")


def write_workflows(theme: Path, variant_slugs, watch=False, showcase=False,
                    force=False, version=None):
    """Write the chosen workflows into <theme>/.github/workflows.

    Returns (written, skipped) lists of paths relative to the theme. Existing
    files are never overwritten unless force is set -- a repo's workflows may
    carry local edits, and clobbering them silently would be worse than making
    someone pass --force.
    """
    version = version or __version__
    alt = variant_alternation(variant_slugs)
    repo_url = detect_repo_url(theme)

    wanted = list(PREVIEW_FILES)
    if watch:
        wanted.append(WATCH_FILE)
    if showcase:
        wanted.append(SHOWCASE_FILE)

    outdir = theme / ".github" / "workflows"
    outdir.mkdir(parents=True, exist_ok=True)

    written, skipped = [], []
    for name in wanted:
        target = outdir / name
        rel = target.relative_to(theme)
        if target.exists() and not force:
            skipped.append(rel)
            continue
        target.write_text(render_template(_template(name), version, alt, repo_url),
                          encoding="utf-8")
        written.append(rel)
    return written, skipped


def new_theme(target: Path):
    """Copy the starter theme into target. Returns the files created.

    The starter is the same theme this repo's CI renders on every push, so a
    scaffold is never something that "should" work -- it is the exact tree the
    determinism and sensitivity checks run against.
    """
    root = resources.files("fxcss") / "templates" / "starter"
    if target.exists() and any(target.iterdir()):
        raise FileExistsError(f"{target} exists and is not empty")
    created = []
    for entry in root.rglob("*"):
        if not entry.is_file():
            continue
        rel = Path(str(entry)).relative_to(Path(str(root)))
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(entry.read_text(encoding="utf-8"), encoding="utf-8")
        created.append(rel)
    return sorted(created)


BADGE = ("[![theme previews by fxcss]"
         "(https://img.shields.io/badge/theme%20previews-fxcss-ff7139)]"
         "(https://github.com/AdamXweb/fxcss)")


def next_steps(written, skipped, variant_slugs, watch, showcase):
    """Human instructions for what was generated. Returned, not printed, so it
    is unit-testable and the CLI owns all output."""
    lines = []
    if written:
        lines.append("Wrote:")
        lines += [f"  {p}" for p in written]
    if skipped:
        lines.append("Left alone (already exist; rerun with --force to replace):")
        lines += [f"  {p}" for p in skipped]
    if variant_slugs:
        lines.append("")
        lines.append(f"Publish allowlist covers {len(variant_slugs)} variant "
                     f"stylesheet(s): {', '.join(sorted(variant_slugs))}.")
        lines.append("Adding a variant later? Re-run `fxcss init --force`, or add it "
                     "to the NAME regex by hand.")
    lines += [
        "",
        "Worth knowing before the first pull request:",
        "  - The preview comment only starts appearing once these files are on",
        "    your default branch: workflow_run always uses the default-branch copy.",
        "  - A first-time contributor's run waits for your 'Approve and run",
        "    workflows' click. That is GitHub policy, not a misconfiguration.",
        "  - Images are published to an orphan ci-previews branch and cleaned up",
        "    when each pull request closes.",
    ]
    if watch:
        lines.append("  - firefox-watch runs Mondays and needs no further setup.")
    if showcase:
        lines.append("  - showcase publishes to a `showcase` branch on each release.")
    lines += [
        "",
        "If you would like to say so in your README:",
        f"  {BADGE}",
    ]
    return "\n".join(lines)
