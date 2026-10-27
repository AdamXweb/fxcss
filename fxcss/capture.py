"""The expected screenshot inventory and its machine-readable coverage report."""

import fnmatch
import json
import re
from collections import namedtuple
from pathlib import Path

from . import __version__

REPORT = "capture-coverage.json"
# Where each full-window capture's landmarks were, so a comparison can say
# which part of the UI a change is in rather than only which view it was in.
LAYOUT = "layout.json"
VARIANT = re.compile(r"variant-[a-z0-9][a-z0-9+-]*\Z")
VIEW_PATTERN = re.compile(r"[a-z0-9*?!+\[\]-]{1,160}\Z")

# The areas of the UI, in the order reports present them. Every view belongs
# to one; a change inside a full-window view is placed by where it is (see
# LAYOUT_LANDMARKS), so a tab strip change reads as Tabs in any view.
CATEGORIES = ("Window", "Tabs", "Toolbars", "Address bar", "Find bar", "Sidebar",
              "Content", "Dialogs", "Panels", "Settings", "Optional stylesheets")

# key: the name after the light-/dark-/extra- prefix. schemes: captured once per
# colour scheme, or once in light. since: the first fxcss release that captures
# it -- a baseline made before then may lack it. unsupported: the only reasons
# a run may skip it, each an explicitly detected capability.
View = namedtuple("View", "key title category schemes since unsupported")

SCREEN_CAPTURE_REASONS = ("screen capture needs Pillow",
                          "screen capture cannot see the Firefox window")
NO_SEARCH_BUTTON = "no search engine button on this Firefox"

VIEWS = (
    View("01-window", "Browser window", "Window", True, None, ()),
    View("02-urlbar", "Address bar focused", "Address bar", True, None, ()),
    View("03-findbar", "Find bar", "Find bar", True, None, ()),
    View("04-dialog", "Modal dialog", "Dialogs", True, None, ("no in-window modal prompts",)),
    View("05-toolbar-hover", "Toolbar buttons hovered", "Toolbars", True, (0, 25), ()),
    View("06-search-engine", "Search engine button", "Address bar", True, (0, 25),
         (NO_SEARCH_BUTTON,)),
    View("07-search-menu", "Search engine menu", "Address bar", True, (0, 25),
         (NO_SEARCH_BUTTON,) + SCREEN_CAPTURE_REASONS),
    View("08-app-menu", "App menu", "Panels", True, (0, 25), SCREEN_CAPTURE_REASONS),
    View("09-extensions-panel", "Extensions panel", "Panels", True, (0, 25),
         ("no extensions button on this Firefox",) + SCREEN_CAPTURE_REASONS),
    View("10-account-panel", "Account panel", "Panels", True, (0, 25),
         ("no account button on this Firefox",) + SCREEN_CAPTURE_REASONS),
    View("11-settings", "Settings", "Settings", True, (0, 25), ()),
    View("04-audio", "Tab playing audio", "Tabs", False, None, ()),
    View("05-muted", "Tab muted", "Tabs", False, None, ()),
    View("06-containers", "Container tabs", "Tabs", False, None, ()),
    View("07-many-tabs", "Tab strip overflowing", "Tabs", False, None, ()),
    View("08-private", "Private window", "Window", False, None, ()),
    View("09-compact", "Compact density", "Window", False, None, ()),
    View("10-sidebar-bookmarks", "Bookmarks sidebar", "Sidebar", False, None, ()),
    View("11-sidebar-history", "History sidebar", "Sidebar", False, None, ()),
    View("12-rtl", "Right-to-left UI", "Window", False, None, ()),
    View("13-customize", "Customize mode", "Toolbars", False, None, ()),
    View("14-vertical-tabs", "Vertical tabs", "Tabs", False, None, ("Firefox before 133",)),
    View("15-toolbar", "Custom toolbar", "Toolbars", False, None, ()),
)
SCHEME_VIEWS = tuple(v for v in VIEWS if v.schemes)
EXTRA_VIEWS = tuple(v for v in VIEWS if not v.schemes)
EXTRAS = tuple(v.key for v in EXTRA_VIEWS)

# Selectors whose boxes place a change in a full-window view. Where boxes nest
# (the address bar inside the navigation toolbar), the smallest one that holds
# most of the change wins.
LAYOUT_LANDMARKS = (
    ("Address bar", "#urlbar"),
    ("Address bar", "#identity-box"),
    ("Tabs", "#TabsToolbar"),
    ("Tabs", "#vertical-tabs"),
    ("Toolbars", "#nav-bar"),
    ("Toolbars", "#PersonalToolbar"),
    ("Toolbars", "#toolbar-menubar"),
    ("Toolbars", "#customization-container"),
    ("Sidebar", "#sidebar-main"),
    ("Sidebar", "#sidebar-box"),
    ("Find bar", "findbar"),
    ("Content", "#tabbrowser-tabpanels"),
)


def view_name(view, mode):
    return f"{mode}-{view.key}" if view.schemes else f"extra-{view.key}"


def view_info(name):
    """The View a capture name refers to, or None for a variant or unknown name."""
    mode, _, key = name.partition("-")
    for view in (SCHEME_VIEWS if mode in ("light", "dark") else
                 EXTRA_VIEWS if mode == "extra" else ()):
        if view.key == key:
            return view
    return None


def title_for(name):
    """(title, mode) for a capture name; variants are named after their sheet."""
    mode, _, key = name.partition("-")
    if mode == "variant":
        return "Variant: " + key, "variant"
    view = view_info(name)
    return (view.title if view else name), mode


def category_for(name):
    if VARIANT.fullmatch(name):
        return "Optional stylesheets"
    view = view_info(name)
    return view.category if view else "Window"


def view_order(name):
    """Sort key putting views in category order, then capture order."""
    view = view_info(name)
    category = category_for(name)
    index = VIEWS.index(view) if view else len(VIEWS)
    mode = name.partition("-")[0]
    return (CATEGORIES.index(category), index, ("light", "dark", "extra").index(mode)
            if mode in ("light", "dark", "extra") else 3, name)


def is_standard_view(name):
    return view_info(name) is not None


def _version_tuple(text):
    numbers = re.findall(r"\d+", str(text or ""))[:2]
    return tuple(int(n) for n in numbers) + (0,) * (2 - len(numbers))


def new_since(version):
    """Standard views a capture made by fxcss `version` cannot contain."""
    made = _version_tuple(version)
    return {view_name(view, mode) for view in VIEWS if view.since and made < view.since
            for mode in (("light", "dark") if view.schemes else ("extra",))}


def expected_views(variants=(), modes=("light", "dark")):
    return ([view_name(view, mode) for mode in modes for view in SCHEME_VIEWS]
            + [view_name(view, "extra") for view in EXTRA_VIEWS]
            + [f"variant-{slug}" for slug in sorted(variants)])


def parse_view_patterns(spec):
    """Turn a --views value into shell-style patterns, or None for every view.

    Accepts a comma-separated string or a list of strings, so the same value
    reads naturally on a command line and in .fxcss.json:

        light-*,extra-07-many-tabs      light views plus the overflowing tab strip
        *-02-urlbar                     the focused address bar in both schemes
        variant-*                       every optional stylesheet capture
    """
    if spec is None:
        return None
    if isinstance(spec, str):
        parts = spec.split(",")
    elif isinstance(spec, list) and all(isinstance(p, str) for p in spec):
        parts = spec
    else:
        raise ValueError("views must be a comma-separated string or a list of patterns")
    patterns = []
    for part in (p.strip().lower() for p in parts):
        if not part:
            continue
        if not VIEW_PATTERN.fullmatch(part):
            raise ValueError(f"invalid view pattern {part!r}; use view names such as "
                             "light-01-window, with * and ? as wildcards")
        if part not in patterns:
            patterns.append(part)
    return patterns or None


def select_views(names, patterns):
    """The names matching any pattern; a pattern that matches nothing is an error.

    A typo must not quietly shrink what is compared, so every pattern has to
    account for at least one view.
    """
    names = sorted(names)
    if not patterns:
        return names
    unmatched = [p for p in patterns if not any(fnmatch.fnmatchcase(n, p) for n in names)]
    if unmatched:
        raise ValueError("no view matches " + ", ".join(unmatched)
                         + "; views: " + (", ".join(names) or "none"))
    return [n for n in names if any(fnmatch.fnmatchcase(n, p) for p in patterns)]


def write_coverage(directory, info, expected, unsupported=None, failed=None,
                   interrupted=False):
    unsupported, failed = unsupported or {}, failed or {}
    views = {}
    for name in expected:
        if name in failed:
            views[name] = {"status": "failed", "reason": failed[name]}
        elif (directory / f"{name}.png").is_file():
            views[name] = {"status": "captured"}
        elif name in unsupported:
            views[name] = {"status": "unsupported", "reason": unsupported[name]}
        elif interrupted:
            views[name] = {"status": "not_completed",
                           "reason": "capture stopped before this view completed"}
        else:
            views[name] = {"status": "failed", "reason": "expected view was not captured"}
    report = {"format": 1, "fxcss": __version__, "browser": info, "views": views}
    (directory / REPORT).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def validate_coverage(directory, expected=None):
    """Reject partial runs, including first baselines with no comparison yet."""
    directory = Path(directory)
    path = directory / REPORT
    if not path.is_file():
        raise ValueError(f"missing {REPORT} in {directory}; capture again before updating the baseline")
    report = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(report, dict) or report.get("format") != 1 or not isinstance(report.get("views"), dict):
        raise ValueError(f"invalid capture coverage in {path}")
    if not isinstance(report.get("browser"), dict):
        raise ValueError(f"invalid browser metadata in {path}")
    views = report["views"]
    wanted = set(expected if expected is not None else expected_views())
    if expected is None:
        # A saved baseline: it may predate views a newer fxcss captures.
        wanted.update(name for name in views if VARIANT.fullmatch(name))
        wanted -= new_since(report.get("fxcss")) - set(views)
    if set(views) != wanted:
        raise ValueError(f"capture inventory does not match expected views in {path}")
    failed = []
    for name in sorted(wanted):
        record = views[name]
        if not isinstance(record, dict):
            failed.append(name)
            continue
        status = record.get("status")
        if status == "captured":
            if not (directory / f"{name}.png").is_file():
                failed.append(name)
        elif status == "unsupported":
            # Only explicitly detected capabilities may relax the required set.
            reason = record.get("reason")
            view = view_info(name)
            valid = bool(view and reason in view.unsupported)
            if name == "extra-14-vertical-tabs" and reason == "Firefox before 133":
                version = re.match(r"\d+", str(report.get("browser", {}).get("version", "")))
                valid = bool(version and int(version[0]) < 133)
            if not valid:
                failed.append(name)
        else:
            failed.append(name)
    if failed:
        raise ValueError("incomplete capture: " + ", ".join(failed) + f"; see {path}")
    return report
