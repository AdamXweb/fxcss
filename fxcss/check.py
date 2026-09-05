"""Run a theme's saved audit, capture and comparison settings together."""

import argparse
import contextlib
import hashlib
import json
import math
import shutil
import tempfile
from pathlib import Path

from . import capture, core, __version__
from .fetch import VARIANT_DIRS


DEFAULTS = {
    "firefox": ["stable"],
    "variants": "all",
    "toolbar": None,
    "out": ".fxcss/checks",
    "baseline": None,
    "strict": True,
    "strict_vars": False,
    "max_changed_percent": None,
}


def overlaps(first, second):
    return first == second or first in second.parents or second in first.parents


def load_settings(args):
    theme = args.theme.resolve()
    if not (theme / "chrome/userChrome.css").is_file():
        raise ValueError(f"no chrome/userChrome.css under {theme}")
    config = args.config.resolve() if args.config else theme / ".fxcss.json"
    settings = dict(DEFAULTS)
    if args.config or config.exists():
        data = json.loads(config.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("check configuration must be a JSON object")
        unknown = set(data) - set(DEFAULTS)
        if unknown:
            raise ValueError("unknown check setting(s): " + ", ".join(sorted(unknown)))
        settings.update(data)
    for key in DEFAULTS:
        value = getattr(args, key, None)
        if value is not None:
            settings[key] = str(value) if isinstance(value, Path) else value
    browsers = settings["firefox"]
    if isinstance(browsers, str):
        browsers = [browsers]
    if (not isinstance(browsers, list) or not browsers
            or any(not isinstance(v, str) or not v.strip() or "\n" in v or "\r" in v
                   for v in browsers) or len(set(browsers)) != len(browsers)):
        raise ValueError("firefox must be a browser name/path or a non-empty list of unique names/paths")
    settings["firefox"] = browsers
    if len({browser_key(v) for v in browsers}) != len(browsers):
        raise ValueError("firefox contains duplicate channel aliases")
    for key in ("strict", "strict_vars"):
        if not isinstance(settings[key], bool):
            raise ValueError(f"{key} must be true or false")
    for key in ("variants", "toolbar"):
        if settings[key] is not None and not isinstance(settings[key], str):
            raise ValueError(f"{key} must be a string or null")
    core.parse_variant_spec(settings["variants"], core.find_variant_sheets(theme))
    if settings["toolbar"]:
        core.parse_toolbar_spec(settings["toolbar"])
    threshold = settings["max_changed_percent"]
    if threshold is not None and (type(threshold) not in (int, float)
                                  or not math.isfinite(threshold) or not 0 <= threshold <= 100):
        raise ValueError("max_changed_percent must be a number from 0 to 100, or null")
    for key in ("out", "baseline"):
        value = settings[key]
        if key == "baseline" and value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} must be a non-empty path")
        value = Path(value).expanduser()
        value = (theme / value).resolve()
        if value == theme or value in theme.parents:
            raise ValueError(f"{key} must not contain or replace the theme directory")
        for folder in ("chrome", "configuration", *VARIANT_DIRS):
            if overlaps(value, theme / folder):
                raise ValueError(f"{key} must be separate from theme source directories")
        settings[key] = value
    if settings["baseline"] and overlaps(settings["baseline"], settings["out"]):
        raise ValueError("baseline and out must be separate directories")
    if args.update_baseline and settings["baseline"] is None:
        raise ValueError("--update-baseline requires a baseline path in .fxcss.json or --baseline")
    if threshold is not None and settings["baseline"] is None:
        raise ValueError("max_changed_percent requires a baseline path")
    return theme, settings


def browser_key(browser):
    # Paths can name two binaries with the same basename; never share their baseline.
    name = core.CHANNEL_ALIASES.get(browser.lower(), browser.lower())
    if name in core.CHANNEL_ORDER:
        return name
    return "firefox-" + hashlib.sha256(browser.encode("utf-8")).hexdigest()[:12]


def comparison_status(summary, threshold):
    if summary["only_in_base"]:
        return 1
    if threshold is not None:
        if summary["only_in_head"] or any(v["percent"] > threshold for v in summary["views"]):
            return 1
    return 0


BASELINE_MARKER = ".fxcss-baseline.json"


def update_baseline(baseline, captures):
    """Stage a complete update and restore the previous baseline if activation fails."""
    if baseline.exists() and any(baseline.iterdir()):
        marker = baseline / BASELINE_MARKER
        data = json.loads(marker.read_text()) if marker.is_file() else None
        if not isinstance(data, dict) or data.get("format") != 1:
            raise ValueError(f"refusing to replace an unmanaged baseline: {baseline}")
    baseline.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".fxcss-baseline-", dir=baseline.parent))
    stage, previous = work / "next", work / "previous"
    preserve_recovery = False
    try:
        if baseline.exists():
            shutil.copytree(baseline, stage)
        else:
            stage.mkdir()
        for key, source in captures.items():
            target = stage / key
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(source, target)
        (stage / BASELINE_MARKER).write_text(json.dumps({"format": 1, "fxcss": __version__}) + "\n")
        moved = False
        try:
            if baseline.exists():
                baseline.rename(previous)
                moved = True
            stage.rename(baseline)
        except BaseException:
            if moved:
                try:
                    previous.rename(baseline)
                except OSError as exc:
                    preserve_recovery = True
                    raise RuntimeError(f"restore failed; previous baseline kept at {previous}") from exc
            raise
    finally:
        if not preserve_recovery:
            shutil.rmtree(work)


def write_report(directory, settings, results, status, baseline_updated):
    report = {
        "fxcss": __version__, "exit_code": status,
        "baseline_updated": baseline_updated,
        "settings": {k: str(v) if isinstance(v, Path) else v for k, v in settings.items()},
        "browsers": results,
    }
    (directory / "summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    lines = ["# Theme check", "", f"Result: **{('passed', 'findings', 'error')[status]}**", ""]
    for result in results:
        label = result["firefox"].replace("|", "\\|").replace("<", "&lt;")
        lines += [f"## {label}", "", f"Result: **{('passed', 'findings', 'error')[result['exit_code']]}**", ""]
        info = result.get("render_info", {})
        if info:
            lines += [f"Firefox {info.get('version', 'unknown')} on {info.get('os', 'unknown')}", ""]
        key = result["key"]
        coverage_path = directory / key / "shots" / capture.REPORT
        if coverage_path.is_file():
            lines += [f"- [Screenshot coverage]({key}/shots/{capture.REPORT})"]
        for name, view in result.get("coverage", {}).get("views", {}).items():
            if view["status"] == "unsupported":
                lines += [f"- Unsupported view `{name}`: {view['reason']}"]
        for name in ("audit.txt", "capture.txt", "comparison.txt"):
            if (directory / key / name).exists():
                lines.append(f"- [{name}]({key}/{name})")
        if (directory / key / "fix.diff").exists():
            lines.append(f"- [Suggested selector patch]({key}/fix.diff)")
        lines += [f"- [Browser captures]({key}/shots/)", ""] if result.get("captured") else []
        if "comparison" in result:
            summary = result["comparison"]
            lines += [f"Compared {len(summary['views'])} shared views; "
                      f"{len(summary['changed_views'])} changed.", ""]
            for heading, field in (("Added", "only_in_head"), ("Missing", "only_in_base")):
                if summary[field]:
                    lines += [f"{heading} views: " + ", ".join(f"`{v}`" for v in summary[field]), ""]
            for view in summary["changed_views"]:
                lines += [f"- [{view['view']}: {view['percent']:.3f}% changed]"
                          f"({key}/diff/{view['image']})"]
            lines.append("")
        else:
            lines += ["Visual comparison: " + result.get("comparison_note", "not completed") + ".", ""]
        if result.get("error"):
            lines += ["Error: " + result["error"].replace("<", "&lt;"), ""]
    if baseline_updated:
        lines += ["Baseline updated from this run's captures.", ""]
    elif settings["baseline"]:
        lines += ["The baseline was left unchanged. Use `--update-baseline` to accept a successful capture run.", ""]
    (directory / "report.md").write_text("\n".join(lines), encoding="utf-8")


def run(args):
    from . import cli, compare

    theme, settings = load_settings(args)
    settings["out"].mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="run-", dir=settings["out"]))
    results, captures = [], {}
    for browser in settings["firefox"]:
        key = browser_key(browser)
        dest = directory / key
        dest.mkdir()
        result = {"firefox": browser, "key": key, "exit_code": 0}
        results.append(result)
        print(f"Checking {browser}…", flush=True)
        try:
            selected = browser
            if "/" in browser or "\\" in browser or browser.startswith("~"):
                selected = str((theme / Path(browser).expanduser()).resolve())
            binary = core.find_firefox(selected)
            result["binary"] = str(binary)
            with (dest / "audit.txt").open("w", encoding="utf-8") as log:
                with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                    result["audit_exit_code"] = cli.cmd_audit(argparse.Namespace(
                        theme=theme, firefox=str(binary), no_unused=False, all=False,
                        no_colour=True, patch=dest / "fix.diff", strict=settings["strict"],
                        strict_vars=settings["strict_vars"]))
            result["exit_code"] = result["audit_exit_code"]
            with (dest / "capture.txt").open("w", encoding="utf-8") as log:
                with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                    code = cli.cmd_shot(argparse.Namespace(
                        theme=theme, firefox=str(binary), out=dest / "shots", url=[],
                        only_live=False, variants=settings["variants"], toolbar=settings["toolbar"]))
            if code:
                if (dest / "shots" / capture.REPORT).is_file():
                    capture.validate_coverage(dest / "shots", capture.expected_views(
                        core.parse_variant_spec(settings["variants"], core.find_variant_sheets(theme))))
                raise RuntimeError("capture failed; see capture.txt")
            result["coverage"] = capture.validate_coverage(dest / "shots", capture.expected_views(
                core.parse_variant_spec(settings["variants"], core.find_variant_sheets(theme))))
            compare._captures(dest / "shots")
            result["captured"] = True
            info = dest / "shots/render-info.json"
            if info.exists():
                result["render_info"] = json.loads(info.read_text(encoding="utf-8"))
            captures[key] = dest / "shots"
            baseline = settings["baseline"]
            if args.update_baseline:
                result["comparison_note"] = "baseline update requested"
            elif baseline is None:
                result["comparison_note"] = "no baseline configured"
            else:
                capture.validate_coverage(baseline / key)
                with (dest / "comparison.txt").open("w", encoding="utf-8") as log:
                    with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                        code = compare.run(baseline / key, dest / "shots", dest / "diff", browser)
                if code:
                    raise RuntimeError("comparison failed; check the baseline path and comparison.txt. "
                                       "Use --update-baseline to create or replace a baseline")
                summary = json.loads((dest / "diff/summary.json").read_text())
                result["comparison"] = summary
                result["exit_code"] = max(result["exit_code"], comparison_status(
                    summary, settings["max_changed_percent"]))
        except (OSError, ValueError, RuntimeError, SystemExit) as exc:
            result.update(exit_code=2, error=str(exc))
        print(f"  {browser}: {('passed', 'findings', 'error')[result['exit_code']]}", flush=True)
        if result.get("error"):
            print(f"  {result['error']}", flush=True)
    status = max(r["exit_code"] for r in results)
    updated = False
    if args.update_baseline and status == 0:
        try:
            update_baseline(settings["baseline"], captures)
            updated = True
        except (OSError, ValueError, RuntimeError) as exc:
            status = 2
            results[-1].update(exit_code=2, error=f"baseline update failed: {exc}")
    write_report(directory, settings, results, status, updated)
    print(f"\nReport: {directory / 'report.md'}")
    print(f"Summary: {directory / 'summary.json'}")
    return status
