"""Capture repeatability and sensitivity checks against a real Firefox.

Run from the repository root, with Firefox in FIREFOX_BIN, one step at a time
so each failure is its own workflow step:

  python -m tests.smoke_capture_states capture
  python -m tests.smoke_capture_states determinism --platform Linux
  python -m tests.smoke_capture_states real-change --platform Linux
  python -m tests.smoke_capture_states states --platform Linux

capture writes shots/a (the screenshots `fxcss check` took), shots/b and
shots/c (two `fxcss shot` runs). determinism compares every pair, so a run that
differs from both others is told apart from a check-vs-shot difference that
repeats. real-change and states prove the comparison and the state-specific
views actually see the CSS they are meant to. Every view must match exactly:
no changed pixel is ever written off as noise here.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from fxcss import capture

STARTER = Path(__file__).resolve().parents[1] / "fxcss/templates/starter"
RUNS = ("a", "b", "c")
PAIRS = (("a", "b"), ("a", "c"), ("b", "c"))
RUN_NAMES = {"a": "fxcss check", "b": "fxcss shot", "c": "fxcss shot"}

REAL_CHANGE_CSS = "\n#nav-bar { background-color: #ff2d55 !important; }\n"

# Each rule targets a distinct browser state. Falling back to an ordinary
# window must not pass the state-specific capture checks.
STATE_CHROME_CSS = """
/* The revamped sidebar hides #sidebar-header; its container stays visible. */
#sidebar-box { border: 6px solid #ff2d55 !important; }
#tabbrowser-tabs[orient="vertical"] { background-color: #34c759 !important; }
#home-button { background-color: #ff9500 !important; }
.tabbrowser-tab[soundplaying]:not([muted]) .tab-content { background: #e6194b !important; }
.tabbrowser-tab[muted] .tab-content { background: #4363d8 !important; }
/* Surfaces no full-window view shows: hover, the search engine
   button and its menu, the panels. */
#reload-button:hover > .toolbarbutton-icon { background-color: #af52de !important; }
#urlbar .searchmode-switcher:hover { outline: 4px solid #ff2d55 !important; }
panel:has(> .searchmode-switcher-panel-list) { --panel-background: #34c759 !important; }
.searchmode-switcher-panel-list { background-color: #34c759 !important; }
#appMenu-mainView { background-color: #ff9500 !important; }
#unified-extensions-view { background-color: #4363d8 !important; }
#PanelUI-fxa { background-color: #5856d6 !important; }
"""
STATE_CONTENT_CSS = """@-moz-document url-prefix("about:preferences") {
  body { background-color: #e6194b !important; }
}
"""
STATE_VIEWS = frozenset(
    {"extra-10-sidebar-bookmarks", "extra-11-sidebar-history", "extra-14-vertical-tabs",
     "extra-15-toolbar", "extra-04-audio", "extra-05-muted"}
    | {f"{mode}-{view}" for mode in ("light", "dark")
       for view in ("05-toolbar-hover", "06-search-engine", "07-search-menu",
                    "08-app-menu", "09-extensions-panel", "10-account-panel", "11-settings")})


def fxcss(*args):
    """Run the installed toolkit exactly as a theme author would."""
    print("+ fxcss " + " ".join(str(arg) for arg in args), flush=True)
    subprocess.run([sys.executable, "-m", "fxcss", *map(str, args)], check=True)


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def compare(root, base, head, out, platform):
    fxcss("compare", "--base", root / base, "--head", root / head, "--out", root / out,
          "--platform", platform)
    return load(root / out / "summary.json")


def copy_theme(destination):
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(STARTER, destination)
    return destination


def scratch(root):
    return Path(os.environ.get("RUNNER_TEMP") or root / "out/tmp")


# Three, not two. The flakes this has caught were intermittent, so a single
# pair passed often enough to be misleading.
def capture_runs(root, firefox):
    check = root / "out/check"
    fxcss("check", "--theme", STARTER, "--firefox", firefox, "--out", check)
    report, = check.glob("run-*/summary.json")
    result = load(report)
    if result["exit_code"] != 0:
        raise SystemExit(f"fxcss check failed: {json.dumps(result, indent=2)}")
    browser, = result["browsers"]
    shutil.copytree(report.parent / browser["key"] / "shots", root / "shots/a")
    for run in RUNS[1:]:
        fxcss("shot", "--theme", STARTER, "--out", root / "shots" / run, "--variants", "all")
    return 0


def pixels(view):
    return f"{view['changed_pixels']}/{view['total_pixels']} pixels ({view['percent']}%)"


def differing_views(summary):
    """Every view this comparison found different, with any pixel at all."""
    names = {v["view"] for v in summary["views"] if v["changed_pixels"]}
    names |= {v["view"] for v in summary["changed_views"]}
    return names | set(summary["only_in_base"]) | set(summary["only_in_head"])


def diagnose(differs):
    """Name which run is the odd one out, from the pairs that saw a view differ.

    Every case still fails; this only says where to look. With just one check
    capture, matching shot runs cannot establish whether the check command or
    an intermittent first-run difference caused the mismatch.
    """
    differs = frozenset(differs)
    for odd in RUNS:
        if differs == {pair for pair in PAIRS if odd in pair}:
            if odd == "a":
                return ("run a (fxcss check) differs from runs b and c (fxcss shot), which "
                        "match: investigate check-vs-shot setup and first-run effects; "
                        "the two shot captures show no shot-to-shot noise")
            return (f"run {odd} differs from the other two runs, which match: an intermittent "
                    "difference between repeated captures")
    if len(differs) == len(PAIRS):
        return "all three runs differ from one another: the rendering is unstable"
    pair, = differs
    return (f"only runs {pair[0]} and {pair[1]} differ, and each matches the third: a difference "
            "close to the per-pixel noise threshold")


def determinism_report(summaries):
    """(passed, lines) for the a/b/c pairwise comparisons, keyed by pair."""
    lines, failed, differs = [], False, {}
    for pair in PAIRS:
        s = summaries[pair]
        label = f"run {pair[0]} ({RUN_NAMES[pair[0]]}) vs run {pair[1]} ({RUN_NAMES[pair[1]]})"
        names = differing_views(s)
        if s["any_change"] or names:
            failed = True
            lines.append(f"{label}: rendered differently")
            lines.append(f"    missing: {s['only_in_base']}; added: {s['only_in_head']}")
            for v in s["views"]:
                if v["view"] in names:
                    lines.append(f"    {v['view']}: {pixels(v)}")
        else:
            changed = sum(v["changed_pixels"] for v in s["views"])
            total = sum(v["total_pixels"] for v in s["views"])
            lines.append(f"{label}: all {len(s['views'])} views identical "
                         f"({changed}/{total} pixels changed)")
        for name in names:
            differs.setdefault(name, set()).add(pair)
    if differs:
        lines.append("Diagnosis:")
        for name in sorted(differs):
            lines.append(f"    {name}: {diagnose(differs[name])}")
    return not failed, lines


def determinism(root, platform):
    summaries = {pair: compare(root, f"shots/{pair[0]}", f"shots/{pair[1]}",
                               f"out/determinism-{pair[0]}-{pair[1]}", platform)
                 for pair in PAIRS}
    passed, lines = determinism_report(summaries)
    print("\n".join(lines))
    return 0 if passed else 1


def real_change_report(summary):
    changed = summary["changed_views"]
    if not changed:
        return False, ["An obvious CSS change produced no pixel difference — "
                       "the comparison is not sensitive."]
    lines = [f"{len(changed)} of {len(summary['views'])} views changed, as expected"]
    lines += [f"    {v['view']}: {pixels(v)}" for v in changed]
    return True, lines


def real_change(root, platform):
    theme = copy_theme(scratch(root) / "changed-theme")
    with (theme / "chrome/userChrome.css").open("a", encoding="utf-8") as sheet:
        sheet.write(REAL_CHANGE_CSS)
    fxcss("shot", "--theme", theme, "--out", root / "shots/changed")
    passed, lines = real_change_report(compare(root, "shots/a", "shots/changed",
                                               "out/changed", platform))
    print("\n".join(lines))
    return 0 if passed else 1


def search_button_skip(name, view):
    """A Firefox without the search engine button (an older ESR) cannot show
    it. Only the views capture.VIEWS allows to lack it may be skipped for it;
    nothing else may be skipped at all."""
    info = capture.view_info(name)
    return (bool(info) and capture.NO_SEARCH_BUTTON in info.unsupported
            and view.get("status") == "unsupported"
            and view.get("reason") == capture.NO_SEARCH_BUTTON)


def states_report(summary, coverage):
    changed = {v["view"] for v in summary["changed_views"]}
    views = coverage["views"]
    skipped = {name for name in STATE_VIEWS if search_button_skip(name, views.get(name, {}))}
    lines = [f"{name}: skipped, {capture.NO_SEARCH_BUTTON}" for name in sorted(skipped)]
    missing = sorted(STATE_VIEWS - changed - skipped)
    if missing:
        lines.append("These views did not react to a rule that only they can render, so "
                     "they are not capturing the state they claim: " + ", ".join(missing))
        for name in missing:
            view = views.get(name, {})
            if view.get("status") != "captured":
                lines.append(f"  {name}: {view.get('status')} ({view.get('reason')})")
        return False, lines
    lines.append(f"all {len(STATE_VIEWS - skipped)} state views reacted; "
                 f"{len(changed)} views changed")
    by_name = {v["view"]: v for v in summary["views"]}
    lines += [f"    {name}: {pixels(by_name[name])}" for name in sorted(STATE_VIEWS & changed)]
    return True, lines


def states(root, platform):
    theme = copy_theme(scratch(root) / "state-theme")
    with (theme / "chrome/userChrome.css").open("a", encoding="utf-8") as sheet:
        sheet.write(STATE_CHROME_CSS)
    (theme / "chrome/userContent.css").write_text(STATE_CONTENT_CSS, encoding="utf-8")
    fxcss("shot", "--theme", theme, "--out", root / "shots/state")
    passed, lines = states_report(
        compare(root, "shots/a", "shots/state", "out/state", platform),
        load(root / "shots/state" / capture.REPORT))
    print("\n".join(lines))
    return 0 if passed else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("step", choices=("capture", "determinism", "real-change", "states"))
    parser.add_argument("--platform", default="local")
    parser.add_argument("--firefox", default=os.environ.get("FIREFOX_BIN"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if args.step == "capture":
        if not args.firefox:
            parser.error("capture needs --firefox or FIREFOX_BIN")
        return capture_runs(root, args.firefox)
    if args.step == "determinism":
        return determinism(root, args.platform)
    if args.step == "real-change":
        return real_change(root, args.platform)
    return states(root, args.platform)


if __name__ == "__main__":
    sys.exit(main())
