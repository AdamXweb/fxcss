"""Which Firefox releases a theme change still works on.

Firefox renames its chrome between releases, and a theme that follows a
rename works on the new release while silently losing that styling on every
older one. A pull request that swaps `#foo` for `.foo` can be exactly right
for Firefox 158 and still take the styling away from everyone on 157, ESR
included, the moment it merges. The preview screenshots cannot say so: they
are all rendered with the newest Firefox.

This compares two revisions of a theme, before and after a change, and asks
of every selector and custom-property override the change removes or adds
which releases it matches in. A removed rule that still matched on older
releases is styling those users lose; an added rule that only matches newer
releases is styling they never get.

Release builds come from archive.mozilla.org as Linux tarballs and only their
omni.ja archives are read (omni.chrome_names), so no older Firefox is run.
Each build becomes a name index of about 120 KB that is cached, and the
release where a rule starts or stops matching is found by bisection. A change
that only edits declarations downloads nothing beyond the oldest build; one
that moves names costs a handful of downloads the first time.

The newest build is read as a Linux build too, so a name that only exists in
macOS or Windows markup is absent from every build compared and never
reported. That keeps the check quiet rather than noisy: it can miss a
platform-specific rename, but it does not invent one.
"""

import bisect
import configparser
import gzip
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from . import audit, omni
from .fetch import USER_AGENT, VARIANT_DIRS

ARCHIVE = "https://archive.mozilla.org/pub/firefox/"
PRODUCT_DETAILS = "https://product-details.mozilla.org/1.0/firefox_versions.json"
INDEX_FORMAT = 1
OMNI_MEMBERS = ("firefox/omni.ja", "firefox/browser/omni.ja")
MAX_OMNI_BYTES = 200 * 1024 * 1024
MAX_DOWNLOADS = 8
VERSION = re.compile(r"([1-9]\d{0,2})\.\d+(?:\.\d+)?(?:a\d+|b\d+|esr)?")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
ATTRIBUTE = re.compile(r"\[[^\]]*\]")
PARENTHESES = re.compile(r"\([^()]*\)")
DECLARED = re.compile(r"(?<![\w-])(--[A-Za-z_][\w-]*)\s*:")


class Unavailable(Exception):
    """The check could not run. The message is safe to show in a report."""


class OutOfDownloads(Unavailable):
    """The budget ran out: a partial boundary, or an unavailable check."""

    def __init__(self, version):
        super().__init__(f"the download budget ran out at Firefox {version}")


def major(version):
    match = VERSION.fullmatch(str(version or ""))
    return int(match.group(1)) if match else None


def parse_oldest(value):
    """'esr' (the current ESR), or a Firefox major version such as 140."""
    value = str(value).strip().lower()
    if value == "esr":
        return value
    if re.fullmatch(r"[1-9]\d{0,2}", value):
        return int(value)
    raise ValueError(f"oldest Firefox must be 'esr' or a major version such as 140, not {value!r}")


def default_cache():
    root = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(root) / "fxcss" / "firefox-builds"


def archive_urls(version):
    """Where the archive keeps this version's Linux build, xz before bz2.

    Releases switched from .tar.bz2 to .tar.xz in Firefox 135; a Nightly is
    only kept under latest-mozilla-central while it is the newest.
    """
    if not VERSION.fullmatch(version):
        raise ValueError(f"not a Firefox version: {version!r}")
    if re.search(r"a\d+$", version):
        stem = f"nightly/latest-mozilla-central/firefox-{version}.en-US.linux-x86_64.tar"
    else:
        stem = f"releases/{version}/linux-x86_64/en-US/firefox-{version}.tar"
    return [f"{ARCHIVE}{stem}.{ext}" for ext in ("xz", "bz2")]


def _open(url, timeout):
    return urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=timeout)


def download_names(version):
    """The name index of a Linux build, or None if the archive has no such build."""
    with tempfile.TemporaryDirectory(prefix="fxcss-build-") as work:
        for url in archive_urls(version):
            paths = []
            try:
                with _open(url, 120) as response, \
                        tarfile.open(fileobj=response, mode="r|*") as tar:
                    for member in tar:
                        if not (member.isfile() and member.name in OMNI_MEMBERS):
                            continue
                        if member.size > MAX_OMNI_BYTES:
                            raise Unavailable(f"{member.name} in Firefox {version} is too large")
                        # Written under our own name: nothing in the tarball
                        # chooses where a file lands.
                        dest = Path(work) / member.name.replace("/", "-")
                        with tar.extractfile(member) as source, dest.open("wb") as out:
                            shutil.copyfileobj(source, out)
                        paths.append(dest)
                        if len(paths) == len(OMNI_MEMBERS):
                            break
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    continue
                raise Unavailable(f"archive.mozilla.org answered {exc.code} for Firefox {version}")
            except (urllib.error.URLError, OSError, tarfile.TarError, EOFError) as exc:
                raise Unavailable(f"could not download Firefox {version}: {exc}") from exc
            if not paths:
                raise Unavailable(f"the Firefox {version} archive has no omni.ja")
            return omni.chrome_names(paths)
    return None


class Builds:
    """Name indexes by version: memory, then the cache, then the archive."""

    def __init__(self, cache, max_downloads=MAX_DOWNLOADS, download=None):
        self.cache = Path(cache)
        self.max_downloads = max_downloads
        self.download = download or download_names
        self.downloads = 0
        self.loaded = {}

    def _path(self, version):
        return self.cache / f"linux-{version}.json.gz"

    def get(self, version):
        if version in self.loaded:
            return self.loaded[version]
        path = self._path(version)
        names = None
        try:
            data = json.loads(gzip.decompress(path.read_bytes()))
            if data.get("format") == INDEX_FORMAT and data.get("version") == version:
                names = {k: set(data[k]) for k in ("ids", "classes", "properties")}
                self.loaded[version] = names
                return names
        except (OSError, ValueError, KeyError, TypeError, EOFError):
            pass
        if self.downloads >= self.max_downloads:
            raise OutOfDownloads(version)
        self.downloads += 1
        names = self.download(version)
        self.loaded[version] = names
        # A build the archive lacks may simply not have shipped yet, and a
        # Nightly is rebuilt daily under one version: neither is kept.
        if names is not None and not re.search(r"a\d+$", version):
            record = {"format": INDEX_FORMAT, "version": version,
                      **{k: sorted(v) for k, v in names.items()}}
            try:
                self.cache.mkdir(parents=True, exist_ok=True)
                path.write_bytes(gzip.compress(json.dumps(record).encode("utf-8")))
            except OSError:
                pass  # a cache that cannot be written only costs a download next time
        return names


def build_version(binary):
    """The version string of a Firefox build, read without running it."""
    binary = Path(binary)
    for ini in (binary.parent / "application.ini",
                binary.parent.parent / "Resources" / "application.ini"):
        if ini.is_file():
            config = configparser.ConfigParser()
            try:
                config.read(ini, encoding="utf-8")
                return config.get("App", "Version", fallback=None)
            except configparser.Error:
                break
    from . import core
    return core.firefox_version(binary)


def release_info(timeout=15):
    """Current release, Beta, Nightly and ESR majors, and the next release date."""
    try:
        with _open(PRODUCT_DETAILS, timeout) as response:
            data = json.loads(response.read(1_000_000))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    info = {}
    for key, field in (("release", "LATEST_FIREFOX_VERSION"),
                       ("beta", "LATEST_FIREFOX_DEVEL_VERSION"),
                       ("nightly", "FIREFOX_NIGHTLY"), ("esr", "FIREFOX_ESR")):
        value = major(data.get(field))
        if value:
            info[key] = value
    date = data.get("NEXT_RELEASE_DATE")
    if isinstance(date, str) and DATE.fullmatch(date):
        info["next_release_date"] = date
    return info or None


# --- the theme side -----------------------------------------------------------

def _blank(text):
    """Comments, strings and url() bodies as spaces, so offsets and lines hold."""
    keep = lambda m: re.sub(r"[^\n]", " ", m.group(0))  # noqa: E731
    text = audit.COMMENT.sub(keep, text)
    text = audit.URLS.sub(keep, text)
    return audit.STRINGS.sub(keep, text)


def _split_list(blanked, original):
    """(key, text, offset) per selector in a list, split on top-level commas.

    Commas and brackets are found in the blanked copy, so one inside a
    string or comment splits nothing. The key, compared between revisions,
    comes from the blanked copy too; the text shown in reports is the
    selector as written, comments aside.
    """
    cuts, depth = [], 0
    for i, ch in enumerate(blanked):
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        elif ch == "," and depth == 0:
            cuts.append(i)
    parts = []
    for a, b in zip([0] + [c + 1 for c in cuts], cuts + [len(blanked)]):
        key = " ".join(blanked[a:b].split())
        if key:
            offset = a + len(blanked[a:b]) - len(blanked[a:b].lstrip())
            parts.append((key, " ".join(audit.COMMENT.sub(" ", original[a:b]).split()), offset))
    return parts


def stylesheet(text):
    """({selector: (line, text)}, {declared custom property: line}) for one sheet.

    Every style rule's selectors at any depth, CSS nesting included; at-rule
    preludes (@media, @-moz-document, @supports) are containers, not rules.
    """
    blanked = _blank(text)
    breaks = [i for i, ch in enumerate(blanked) if ch == "\n"]
    line_at = lambda offset: bisect.bisect_left(breaks, offset) + 1  # noqa: E731
    selectors, start = {}, 0
    for i, ch in enumerate(blanked):
        if ch == "{":
            prelude = blanked[start:i]
            stripped = prelude.strip()
            if stripped and not stripped.startswith("@"):
                for key, written, offset in _split_list(prelude, text[start:i]):
                    selectors.setdefault(key, (line_at(start + offset), written))
            start = i + 1
        elif ch in ";}":
            start = i + 1
    properties = {}
    for match in DECLARED.finditer(blanked):
        properties.setdefault(match.group(1), line_at(match.start()))
    return selectors, properties


def theme_rules(theme):
    """Every selector and declared custom property in a theme, with where it is."""
    theme = Path(theme)
    if not (theme / "chrome").is_dir():
        from . import layout
        try:
            theme = layout.standard_root(theme)
        except layout.LayoutError as exc:
            raise Unavailable(f"no chrome/ folder under {theme}: {exc}") from exc
    selectors, properties = {}, {}
    for folder in ("chrome", *VARIANT_DIRS):
        if not (theme / folder).is_dir():
            continue
        for path in sorted((theme / folder).rglob("*.css")):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            where = path.relative_to(theme).as_posix()
            found, declared = stylesheet(text)
            for key, (line, written) in found.items():
                selectors.setdefault(key, {"file": where, "line": line, "text": written})
            for name, line in declared.items():
                properties.setdefault(name, {"file": where, "line": line})
    return selectors, properties


def required_names(selector):
    """The ids and classes an element must carry for this selector to match.

    Names inside parentheses (:not(), :is(), ::part()) and attribute
    selectors are left out: a missing name inside :not() still matches, and
    :is() needs only one of its names. Leaving them out can only hide a
    warning, never raise a false one.
    """
    flat = ATTRIBUTE.sub("", selector)
    while True:
        reduced = PARENTHESES.sub("", flat)
        if reduced == flat:
            break
        flat = reduced
    names = {kind + name for kind, name in audit.TOKEN.findall(flat)}
    return sorted(names - audit.SPECIFICITY_HACKS)


def candidates(base, head):
    """What the change removes and adds that depends on Firefox's names."""
    (base_sel, base_prop), (head_sel, head_prop) = theme_rules(base), theme_rules(head)
    found = []
    for change, before, after in (("removed", base_sel, head_sel), ("added", head_sel, base_sel)):
        for key in sorted(set(before) - set(after)):
            names = required_names(key)
            if names:
                where = before[key]
                found.append({"change": change, "kind": "selector", "selector": where["text"],
                              "required": names, "file": where["file"], "line": where["line"]})
    for change, before, after in (("removed", base_prop, head_prop), ("added", head_prop, base_prop)):
        for name in sorted(set(before) - set(after)):
            found.append({"change": change, "kind": "property", "selector": name,
                          "required": [name], **before[name]})
    return found


def present(name, names):
    if name.startswith("--"):
        return name in names["properties"]
    return name[1:] in names["ids" if name[0] == "#" else "classes"]


def matches(candidate, names):
    return all(present(n, names) for n in candidate["required"])


# --- the Firefox side ---------------------------------------------------------

def _bisect(at, lo, hi, value, first):
    """First position after lo whose value differs from lo's, up to hi.

    Position lo has value `first`, position hi the other one. Returns
    (lo, hi, exact) narrowed as far as builds allow: exact says no checkable
    build lies between them. Builds the archive does not have are skipped; a
    spent download budget leaves a range.
    """
    skipped = set()
    while True:
        between = [i for i in range(lo + 1, hi) if i not in skipped]
        if not between:
            return lo, hi, True
        mid = between[len(between) // 2]
        try:
            names = at(mid)
        except OutOfDownloads:
            return lo, hi, False
        if names is None:
            skipped.add(mid)
        elif value(names) == first:
            lo = mid
        else:
            hi = mid


def analyse(found, series, builds):
    """(dropped, added) findings, given versions oldest first and newest last.

    A removed rule matters if it still matched on some older release: the
    previous release is always checked as well as the oldest, because a name
    Firefox has just retired is the usual case and may be younger than the
    oldest release checked. An added rule matters if the oldest release does
    not match it.
    """
    def at(i):
        return builds.get(series[i][1])

    last = len(series) - 1
    oldest, newest = at(0), at(last)
    if oldest is None:
        raise Unavailable(f"the archive has no Linux build of Firefox {series[0][1]}")
    previous = None
    for i in range(last - 1, 0, -1):  # the newest older release the archive has
        try:
            names = at(i)
        except OutOfDownloads:
            break
        if names is not None:
            previous = i
            break
    dropped, added = [], []
    for item in found:
        value = lambda names, item=item: matches(item, names)  # noqa: E731
        then, now = value(oldest), value(newest)
        before = value(at(previous)) if previous else then
        if item["change"] == "removed" and not now and (then or before):
            if before:
                lo, hi, exact = _bisect(at, previous or 0, last, value, True)
                # Did it match all the way back to the oldest release?
                start = None if then else _bisect(at, 0, previous, value, False)
            else:
                lo, hi, exact = _bisect(at, 0, previous, value, True)
                start = None
            missing_in, bucket = newest, dropped
        elif item["change"] == "added" and now and not then:
            lo, hi, exact = _bisect(at, 0, last, value, False)
            start, missing_in, bucket = None, oldest, added
        else:
            continue
        finding = {"kind": item["kind"], "selector": item["selector"],
                   "names": [n for n in item["required"] if not present(n, missing_in)],
                   "file": item["file"], "line": item["line"], "since": series[hi][0]}
        if not exact:
            finding["since_low"] = series[lo][0] + 1
        if start is not None:
            # Introduced after the oldest release: older ones never had it.
            finding["from"] = series[start[1]][0]
            exact = exact and start[2]
        bucket.append(finding)
    return dropped, added


def run_check(base, head, firefox, oldest="esr", cache=None, max_downloads=MAX_DOWNLOADS,
              download=None, releases=None):
    """The support report comparing base with head, as a JSON-ready dict."""
    try:
        oldest = parse_oldest(oldest)
    except ValueError as exc:
        raise Unavailable(str(exc)) from exc
    version = build_version(firefox) if firefox else None
    current = major(version)
    if not current:
        raise Unavailable(f"could not read the version of {firefox}")
    info = (releases or release_info)()
    if oldest == "esr":
        if not info or "esr" not in info:
            raise Unavailable("could not look up the current ESR at product-details.mozilla.org")
        floor = info["esr"]
    else:
        floor = oldest
    found = candidates(base, head)
    builds = Builds(cache or default_cache(), max_downloads, download)
    local = omni.pack_paths(firefox)
    if sys.platform.startswith("linux") and local:
        # Already a Linux build: read it in place rather than download it again.
        builds.loaded[version] = omni.chrome_names(local)
    elif floor < current and found:
        try:
            linux = builds.get(version)
        except OutOfDownloads:
            raise  # Exhaustion is not evidence that this build is absent.
        except Unavailable:
            linux = None
        if linux is None:
            # No Linux build of this version (a local or custom build): its own
            # chrome is the next best thing, platform-specific names and all.
            if not local:
                raise Unavailable(f"no Linux build of Firefox {version} and no omni.ja beside {firefox}")
            builds.loaded[version] = omni.chrome_names(local)
    series = [(v, f"{v}.0") for v in range(floor, current)] + [(current, version)]
    report = {"format": 1, "status": "checked", "firefox": version, "current": current,
              "oldest": min(floor, current), "releases": info, "candidates": len(found),
              "dropped": [], "added": [], "breaks_below": None, "new_only_from": None,
              "exact": True}
    if floor < current and found:
        report["dropped"], report["added"] = analyse(found, series, builds)
    elif floor < current:
        builds.get(series[0][1])  # still proves the oldest build is reachable
    for key, bucket in (("breaks_below", "dropped"), ("new_only_from", "added")):
        if report[bucket]:
            report[key] = max(f["since"] for f in report[bucket])
    report["exact"] = not any("since_low" in f for f in report["dropped"] + report["added"])
    report["checked"] = sorted({major(v) for v, names in builds.loaded.items() if names})
    return report


# --- reporting ----------------------------------------------------------------

def _names(findings, limit=6):
    names = []
    for finding in findings:
        for name in finding["names"] or [finding["selector"]]:
            if name not in names:
                names.append(name)
    shown = ", ".join(f"`{n}`" for n in names[:limit])
    return shown + (f" and {len(names) - limit} more" if len(names) > limit else "")


def markdown(report, channel=None):
    """The report as the few lines that go at the top of a pull request."""
    if report.get("status") != "checked":
        return ("> [!NOTE]\n> Firefox support could not be checked: "
                f"{report.get('reason', 'unknown error')}.\n")
    current, oldest = report["current"], report["oldest"]
    releases = report.get("releases") or {}
    span = f"Firefox {oldest}–{current}" if oldest < current else f"Firefox {current}"
    label = f"Firefox {channel} {report['firefox']}" if channel else f"Firefox {report['firefox']}"
    lines = []
    dropped, added = report["dropped"], report["added"]
    if dropped:
        below = report["breaks_below"]
        sure = min(f.get("since_low", f["since"]) for f in dropped) - 1
        first = [f.get("from") for f in dropped]
        who = (f"Firefox {sure} and earlier" if None in first
               else f"Firefox {min(min(first), sure)} to {sure}")
        if sure != below - 1:
            who += f", possibly up to {below - 1}"
        esr = releases.get("esr")
        if esr and any((f.get("from") or oldest) <= esr < f.get("since_low", f["since"])
                       for f in dropped):
            who += f", ESR {esr} included"
        lines += ["> [!WARNING]",
                  f"> **Merging this drops styling for {who}.** It removes rules for "
                  f"{_names(dropped)}, which still match there but no longer match in "
                  f"Firefox {below}. Keeping the old name next to the new one "
                  "(`#old, .new`) keeps both releases styled."]
        if releases.get("release") and releases["release"] < below:
            when = (f" (expected {releases['next_release_date']})"
                    if releases.get("next_release_date") else "")
            lines += [">", f"> Firefox {below} is not a release yet; release is "
                           f"{releases['release']}. Until {below} ships{when}, merging this "
                           "drops the styling for every release user."]
    if added:
        start = min(f.get("since_low", f["since"]) for f in added)
        when = (f"Firefox {report['new_only_from']}" if start == report["new_only_from"]
                else f"somewhere from Firefox {start} to {report['new_only_from']}")
        lines += [">" if lines else "> [!NOTE]",
                  f"> New rules for {_names(added)} only match from {when}; "
                  "older releases do not get them."]
    if not lines:
        lines.append(f"Firefox support is unchanged: nothing this change removes or adds "
                     f"matches differently across {span}.")
    detail = f"Checked {span} against {label}"
    if releases.get("release"):
        detail += f"; release {releases['release']}"
        detail += f", ESR {releases['esr']}" if releases.get("esr") else ""
    lines += ["", f"<sub>{detail}. Read from Linux builds' shipped chrome.</sub>"]
    findings = dropped + added
    if findings:
        lines += ["", "<details><summary>Where these rules are</summary>", ""]
        for f in findings[:20]:
            change = ("no longer matches from" if f in dropped else "matches from")
            lines.append(f"- `{f['file']}:{f['line']}` `{f['selector']}`: {change} "
                         f"Firefox {f['since']}")
        if len(findings) > 20:
            lines.append(f"- and {len(findings) - 20} more")
        lines += ["", "</details>"]
    return "\n".join(lines) + "\n"


def write(report, json_path=None, markdown_path=None, channel=None):
    text = markdown(report, channel)
    for path, content in ((json_path, json.dumps(report, indent=2) + "\n"),
                          (markdown_path, text)):
        if path:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text(content, encoding="utf-8")
    return text


def run(args):
    """`fxcss support`: 0 when the check ran, 2 when it could not."""
    try:
        report = run_check(args.base, args.head, args.firefox, args.oldest, args.cache,
                           args.max_downloads)
        status = 0
    except Unavailable as exc:
        report = {"format": 1, "status": "unavailable", "reason": str(exc)}
        status = 2
    print(write(report, args.json, args.markdown, args.channel))
    return status
