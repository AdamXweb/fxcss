"""The expected screenshot inventory and its machine-readable coverage report."""

import json
import re
from pathlib import Path

REPORT = "capture-coverage.json"
EXTRAS = ("04-audio", "05-muted", "06-containers", "07-many-tabs", "08-private",
          "09-compact", "10-sidebar-bookmarks", "11-sidebar-history", "12-rtl",
          "13-customize", "14-vertical-tabs", "15-toolbar")
VARIANT = re.compile(r"variant-[a-z0-9][a-z0-9+-]*\Z")


def expected_views(variants=(), modes=("light", "dark")):
    return ([f"{mode}-{view}" for mode in modes
             for view in ("01-window", "02-urlbar", "03-findbar", "04-dialog")]
            + [f"extra-{view}" for view in EXTRAS]
            + [f"variant-{slug}" for slug in sorted(variants)])


def write_coverage(directory, info, expected, unsupported=None, failed=None):
    unsupported, failed = unsupported or {}, failed or {}
    views = {}
    for name in expected:
        if name in failed:
            views[name] = {"status": "failed", "reason": failed[name]}
        elif (directory / f"{name}.png").is_file():
            views[name] = {"status": "captured"}
        elif name in unsupported:
            views[name] = {"status": "unsupported", "reason": unsupported[name]}
        else:
            views[name] = {"status": "failed", "reason": "expected view was not captured"}
    report = {"format": 1, "browser": info, "views": views}
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
        wanted.update(name for name in views if VARIANT.fullmatch(name))
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
            valid = (name in ("light-04-dialog", "dark-04-dialog") and reason == "no in-window modal prompts")
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
