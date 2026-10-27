#!/usr/bin/env python3
"""Generate CI workflows for a theme repository: the machinery behind
`fxcss init`.

The templates ship inside the package, so a `pipx install fxcss` user gets
them without ever visiting the repository. Four things are substituted per
theme, which is exactly the hand-editing that used to trip people up:

* the fxcss version pin (a PyPI release), set to the version doing the
  generating
* a bounded filename pattern for optional stylesheet screenshots, including
  variants added after the workflows were generated, next to the standard
  views, their titles and their categories from capture.VIEWS
* the showcase URL, taken from the repository's origin remote
* the PR preview's settings: which optional stylesheets to capture, which
  views to show, the smallest change worth picturing, and the oldest Firefox
  the theme supports (also used by the Firefox watch)
"""

import json
import math
import re
import subprocess
from importlib import resources
from pathlib import Path
from urllib.parse import urlsplit

from . import __version__, capture, core
from .fetch import VARIANT_DIRS

PREVIEW_DEFAULTS = {"variants": "all", "views": "", "min_changed_percent": "0",
                    "oldest_firefox": "esr"}

PREVIEW_FILES = ("pr-preview.yml", "pr-preview-publish.yml", "pr-preview-cleanup.yml")
WATCH_FILE = "firefox-watch.yml"
SHOWCASE_FILE = "showcase.yml"
PREVIEWS_FILE = "readme-previews.yml"


def variant_alternation(slugs):
    """Admit safe variant slugs, including options introduced by future PRs."""
    return "|variant-[a-z0-9][a-z0-9+-]{0,159}"


def view_alternation():
    """The standard view names, enumerated for the publish allowlist."""
    def keys(views):
        names = [view.key for view in views]
        assert all(re.fullmatch(r"[0-9]{2}-[a-z0-9-]+", name) for name in names)
        return "|".join(names)
    return (f"(?:light|dark)-(?:{keys(capture.SCHEME_VIEWS)})"
            f"|extra-(?:{keys(capture.EXTRA_VIEWS)})")


def view_table():
    """Title and category per view, as a JavaScript object literal."""
    return json.dumps({f"{'scheme' if view.schemes else 'extra'}:{view.key}":
                       [view.title, view.category] for view in capture.VIEWS},
                      ensure_ascii=False, separators=(",", ":"))


def https_repo_url(remote):
    """Normalise a git remote to a browsable https URL, or None."""
    if not remote:
        return None
    remote = remote.strip()
    if remote.startswith("ssh://"):
        try:
            url = urlsplit(remote)
            host = url.hostname
        except ValueError:
            return None
        path = url.path.rstrip("/").removesuffix(".git").lstrip("/")
        if not host or not path or url.query or url.fragment:
            return None
        # SSH usernames and ports belong to the transport, not the web URL.
        if ":" in host:
            host = f"[{host}]"
        return f"https://{host}/{path}"
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


def preview_settings(theme: Path, variants="all", views="", min_changed_percent=0.0,
                     oldest_firefox="esr"):
    """Validate PR preview settings now, not on the first pull request.

    Returns the strings written into the workflows' env blocks.
    """
    from .support import parse_oldest
    variants = (variants or "").strip()
    chosen = core.parse_variant_spec(variants, core.find_variant_sheets(theme))
    patterns = capture.parse_view_patterns(views or "")
    capture.select_views(capture.expected_views(chosen), patterns)
    if not math.isfinite(min_changed_percent) or not 0 <= min_changed_percent <= 100:
        raise ValueError("--min-changed-percent must be a number from 0 to 100")
    oldest = (oldest_firefox or "").strip()
    return {"variants": variants, "views": ",".join(patterns or []),
            "min_changed_percent": f"{min_changed_percent:g}",
            "oldest_firefox": str(parse_oldest(oldest)) if oldest else ""}


def _yaml_quoted(value):
    """Body of a single-quoted YAML scalar, which the template supplies the quotes for."""
    if "\n" in value or "\r" in value:
        raise ValueError("workflow settings must be a single line")
    return value.replace("'", "''")


def render_template(text, version, variant_alt, repo_url, preview=None):
    preview = {**PREVIEW_DEFAULTS, **(preview or {})}
    text = text.replace("__FXCSS_PREVIEW_VARIANTS__", _yaml_quoted(preview["variants"]))
    text = text.replace("__FXCSS_PREVIEW_VIEWS__", _yaml_quoted(preview["views"]))
    text = text.replace("__FXCSS_PREVIEW_MIN_PERCENT__",
                        _yaml_quoted(preview["min_changed_percent"]))
    text = text.replace("__FXCSS_PREVIEW_OLDEST__", _yaml_quoted(preview["oldest_firefox"]))
    text = text.replace("__FXCSS_VERSION__", version)
    text = text.replace("__FXCSS_VIEW_ALT__", view_alternation())
    text = text.replace("__FXCSS_VARIANT_ALT__", variant_alt)
    text = text.replace("__FXCSS_VIEW_TABLE__", view_table())
    text = text.replace("__FXCSS_CATEGORIES__", json.dumps(list(capture.CATEGORIES)))
    text = text.replace("__FXCSS_REPO_URL__", repo_url or "https://example.com")
    paths = ["chrome", "configuration", *VARIANT_DIRS]
    text = text.replace("__FXCSS_THEME_PATHS__", "\n".join(
        f"      - '{folder}/**'" for folder in paths))
    return text


def _template(name):
    return (resources.files("fxcss") / "templates" / name).read_text(encoding="utf-8")


def write_workflows(theme: Path, variant_slugs, watch=False, showcase=False,
                    previews=False, force=False, version=None, preview=None):
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
    if previews:
        wanted.append(PREVIEWS_FILE)

    outdir = theme / ".github" / "workflows"
    outdir.mkdir(parents=True, exist_ok=True)

    written, skipped = [], []
    for name in wanted:
        target = outdir / name
        rel = target.relative_to(theme)
        if target.exists() and not force:
            skipped.append(rel)
            continue
        target.write_text(render_template(_template(name), version, alt, repo_url, preview),
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
    if target.exists():
        if not target.is_dir():
            raise FileExistsError(f"{target} exists and is not a directory")
        if any(target.iterdir()):
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


def next_steps(written, skipped, variant_slugs, watch, showcase, previews=False):
    """Human instructions for what was generated. Returned, not printed, so it
    is unit-testable and the CLI owns all output."""
    lines = []
    if written:
        lines.append("Wrote:")
        lines += [f"  {p}" for p in written]
    if skipped:
        lines.append("Left alone (already exist; rerun with --force to replace):")
        lines += [f"  {p}" for p in skipped]
    lines.append("New optional stylesheets are included automatically; variant filenames "
                 "and PNG headers are validated before publishing.")
    lines += [
        "",
        "Worth knowing before the first pull request:",
        "  - The preview comment only starts appearing once these files are on",
        "    your default branch: workflow_run always uses the default-branch copy.",
        "  - A first-time contributor's run waits for your 'Approve and run",
        "    workflows' click. That is GitHub policy, not a misconfiguration.",
        "  - Images are published to an orphan ci-previews branch and cleaned up",
        "    when each pull request closes.",
        "  - What the preview captures and shows (optional stylesheets, views and",
        "    the smallest change worth picturing) is set at the top of",
        "    pr-preview.yml; edit it there or rerun init with --force.",
        "  - Each comment opens by saying whether merging drops styling for an",
        "    older Firefox, back to FXCSS_OLDEST_FIREFOX (default: the current ESR).",
    ]
    if watch:
        lines.append("  - firefox-watch runs Mondays and needs no further setup.")
    if showcase:
        lines.append("  - showcase publishes to a `showcase` branch on each release.")
    if previews:
        lines += [
            "  - readme-previews renders every view and variant on each change to",
            "    your default branch and force-pushes them to a `previews` branch,",
            "    for a README that keeps its own screenshots current. Embed them as",
            "    https://raw.githubusercontent.com/<owner>/<repo>/previews/<view>.png",
        ]
    lines += [
        "",
        "If you would like to say so in your README:",
        f"  {BADGE}",
    ]
    return "\n".join(lines)
