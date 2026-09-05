#!/usr/bin/env python3
"""Shared machinery for the fxcss toolkit.

Drives Firefox over Marionette, its built-in automation protocol. Marionette is
plain TCP with length-prefixed JSON, so nothing outside the Python standard
library is needed -- no Selenium, no geckodriver, and therefore no
driver-to-browser version matching to keep working.

Two things here are worth knowing before changing anything:

* Screenshots are taken in Marionette's *chrome* context, which captures the
  browser window's own document. An ordinary WebDriver screenshot only captures
  page content, so toolbars and tabs would never appear.
* Native popup widgets (context menus, the app menu) are separate OS-level
  windows and are absent from those screenshots. See README.md.
"""

import base64
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

MARIONETTE_DEFAULT_PORT = 2828
WINDOW_WIDTH = 1280
# Tall enough for the chrome, a strip of page content, and the find bar docked
# at the bottom -- without a screenful of empty page padding in every capture.
WINDOW_HEIGHT = 480


class MarionetteError(RuntimeError):
    pass


def free_port():
    """Pick an unused port for this session's Marionette listener.

    Firefox's default is a fixed 2828. If a previous run leaked a browser (a
    hard kill skips cleanup), a new session would silently attach to that stale
    browser instead of its own -- which looks like the theme mysteriously not
    applying. A per-session port makes that impossible and lets several
    sessions run at once.
    """
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Marionette:
    """Minimal Marionette client. Wire framing is '<byte-length>:<json>'."""

    def __init__(self, host="127.0.0.1", port=MARIONETTE_DEFAULT_PORT):
        self.host, self.port = host, port
        self.sock = None
        self._msgid = 0
        self._buf = b""

    def connect(self, timeout=120):
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            try:
                self.sock = socket.create_connection((self.host, self.port), timeout=30)
                self.sock.settimeout(180)
                break
            except OSError as exc:
                last = exc
                time.sleep(0.5)
        else:
            raise MarionetteError(f"could not connect to Marionette in {timeout}s: {last}")

        handshake = self._recv()
        if "marionetteProtocol" not in handshake:
            raise MarionetteError(f"unexpected Marionette handshake: {handshake}")
        self.command("WebDriver:NewSession", {"capabilities": {}})

    def _read_more(self):
        chunk = self.sock.recv(1 << 16)
        if not chunk:
            raise MarionetteError("Marionette connection closed unexpectedly")
        self._buf += chunk

    def _recv(self):
        while b":" not in self._buf:
            self._read_more()
        length, _, rest = self._buf.partition(b":")
        need = int(length)
        self._buf = rest
        while len(self._buf) < need:
            self._read_more()
        payload, self._buf = self._buf[:need], self._buf[need:]
        return json.loads(payload.decode("utf-8"))

    def command(self, name, params=None):
        self._msgid += 1
        msg = json.dumps([0, self._msgid, name, params or {}]).encode("utf-8")
        self.sock.sendall(str(len(msg)).encode("ascii") + b":" + msg)
        while True:
            resp = self._recv()
            if isinstance(resp, list) and len(resp) == 4 and resp[0] == 1:
                _, msgid, error, result = resp
                if msgid != self._msgid:
                    continue
                if error:
                    raise MarionetteError(f"{name} failed: {error}")
                return result

    def set_context(self, value):
        # Context switching is a Marionette extension rather than a WebDriver
        # spec command, and its namespace has moved between Firefox versions.
        tried = []
        for name in ("Marionette:SetContext", "WebDriver:SetContext", "setContext"):
            try:
                return self.command(name, {"value": value})
            except MarionetteError as exc:
                if "unknown command" not in str(exc):
                    raise
                tried.append(name)
        raise MarionetteError(f"no usable SetContext command (tried {tried})")

    @staticmethod
    def _unwrap(result):
        if isinstance(result, dict) and set(result) == {"value"}:
            return result["value"]
        return result

    def script(self, source, args=None):
        return self._unwrap(self.command("WebDriver:ExecuteScript", {
            "script": source, "args": args or [],
            "sandbox": "system", "newSandbox": False,
        }))

    def async_script(self, source, args=None, timeout=30000):
        return self._unwrap(self.command("WebDriver:ExecuteAsyncScript", {
            "script": source, "args": args or [],
            "sandbox": "system", "newSandbox": False, "scriptTimeout": timeout,
        }))

    def screenshot(self):
        return base64.b64decode(self.command(
            "WebDriver:TakeScreenshot", {"full": True, "hash": False})["value"])

    def quit(self):
        try:
            self.command("Marionette:Quit", {"flags": ["eForceQuit"]})
        except Exception:
            pass
        finally:
            if self.sock:
                try:
                    self.sock.close()
                except Exception:
                    pass


# --- profile ---------------------------------------------------------------

EXTRA_PREFS = """
user_pref("toolkit.legacyUserProfileCustomizations.stylesheets", true);
user_pref("browser.tabs.inTitlebar", 1);
user_pref("browser.tabs.drawInTitlebar", true);
user_pref("browser.uidensity", 0);
user_pref("marionette.port", %(port)d);

// Skip everything that would otherwise cover the window on first launch.
user_pref("browser.startup.page", 0);
user_pref("browser.startup.homepage", "about:blank");
user_pref("browser.startup.firstrunSkipsHomepage", true);
user_pref("browser.aboutwelcome.enabled", false);
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.newtabpage.enabled", false);
user_pref("browser.messaging-system.whatsNewPanel.enabled", false);
user_pref("datareporting.policy.dataSubmissionEnabled", false);
user_pref("datareporting.healthreport.uploadEnabled", false);
user_pref("toolkit.telemetry.reportingpolicy.firstRun", false);
user_pref("app.update.auto", false);
user_pref("app.update.enabled", false);
user_pref("extensions.update.enabled", false);

// Promos and rollout-gated features add toolbar items that come and go with
// Mozilla's campaigns. Left enabled they can differ between two runs and show
// up as diffs that have nothing to do with the change under test.
user_pref("browser.vpn_promo.enabled", false);
user_pref("browser.promo.focus.enabled", false);
user_pref("browser.contentblocking.report.hide_vpn_banner", true);
user_pref("browser.ipProtection.enabled", false);
user_pref("browser.urlbar.quicksuggest.enabled", false);
user_pref("browser.urlbar.suggest.quicksuggest.sponsored", false);
user_pref("extensions.pocket.enabled", false);
user_pref("app.normandy.enabled", false);
user_pref("app.shield.optoutstudies.enabled", false);
user_pref("messaging-system.rsexperimentloader.enabled", false);
user_pref("browser.discovery.enabled", false);

// Determinism.
user_pref("toolkit.cosmeticAnimations.enabled", false);
user_pref("ui.prefersReducedMotion", 1);
// A blinking caret in a focused text field lands in a different phase on every
// run, so a screenshot of the find bar would never match itself.
user_pref("ui.caretBlinkTime", 0);
// Firefox flashes the find bar yellow for a moment when it opens, to draw the
// eye. It is transient, so a screenshot lands on it or misses it depending on
// timing -- which made the find bar view differ between two runs of an
// unchanged theme. 0 disables the flash.
user_pref("accessibility.typeaheadfind.flashBar", 0);
// Stops the find bar being pre-filled from a page selection, which would make
// its contents depend on what happened to be selected.
user_pref("accessibility.typeaheadfind.prefillwithselection", false);
user_pref("browser.findbar.prefillWithSelection", false);
user_pref("browser.search.region", "US");
user_pref("signon.rememberSignons", false);
user_pref("browser.toolbars.bookmarks.visibility", "always");
user_pref("browser.bookmarks.restore_default_bookmarks", false);
user_pref("browser.places.importBookmarksHTML", false);
"""

# The Browser Toolbox is the devtools window that can inspect the browser's own
# UI rather than page content -- the only built-in way to hover a toolbar button
# and read its selector. It is off by default and needs all four of these.
DEVTOOLS_PREFS = """
user_pref("devtools.chrome.enabled", true);
user_pref("devtools.debugger.remote-enabled", true);
// Without this, attaching raises a modal that has to be clicked every time.
user_pref("devtools.debugger.prompt-connection", false);
user_pref("devtools.everOpened", true);
user_pref("devtools.f12.enabled", true);
user_pref("devtools.toolbox.host", "window");
"""

# Hides artifacts of the automation harness itself -- never theme rules -- so
# what you see is what a real user would see. Injected as its own user sheet
# rather than written into the profile: an earlier version put these rules in
# customChrome.css, which only takes effect for themes that happen to @import
# it, so most themes showed the automation icons in every capture.
HARNESS_CSS = """/* Injected by fxcss -- harness only.
 * Firefox marks automated sessions with a robot icon in the address bar. */
#remote-control-box, #remote-control-icon { display: none !important; }

/* Rollout-gated Mozilla feature button: present or absent depending on a
 * remote config rather than on this repo. */
#ipprotection-button { display: none !important; }
"""

# Firefox paints a red diagonal hatch across the address bar background while a
# session is under remote control -- its equivalent of Chrome's "controlled by
# automated software" banner. Left alone it appears in every capture and makes
# a perfectly good theme look broken.
#
# Loaded as an *agent* sheet with no !important, unlike HARNESS_CSS above, so a
# theme's own rules still win: agent sheets lose to user sheets for normal
# declarations. The robot icon must win over a theme, this must lose to one.
AUTOMATION_DEFAULTS_CSS = """
.urlbar-background { background-image: none; }
"""

XULSTORE = {
    "chrome://browser/content/browser.xhtml": {
        "main-window": {
            "screenX": "0", "screenY": "0",
            "width": str(WINDOW_WIDTH), "height": str(WINDOW_HEIGHT),
            "sizemode": "normal",
        }
    }
}

SAMPLE_PAGES = {
    "start.html": ("Start", "<h1>Theme preview</h1><p>First tab.</p>"),
    "docs.html": ("Documentation", "<h1>Documentation</h1><p>Second tab.</p>"),
    "issues.html": ("Issue tracker", "<h1>Issues</h1><p>Third tab.</p>"),
    "audio.html": ("Now playing", "<h1>Audio indicators</h1><p>Playing and muted "
                   "tab states for theme screenshots.</p>"),
}


def build_pages(dest=None):
    """Local pages so a capture never depends on the network.

    The directory name is derived from the page content rather than being a
    fresh mkdtemp each run, because the file:// path is *visible in the address
    bar*. A random path there changes the rendered URL text between two runs of
    an unchanged theme, which reads as a real pixel difference. Content
    addressing keeps the path stable while still invalidating when these pages
    change.
    """
    if dest is None:
        digest = hashlib.sha256(
            json.dumps(SAMPLE_PAGES, sort_keys=True).encode("utf-8")
        ).hexdigest()[:10]
        dest = Path(tempfile.gettempdir()) / f"fxcss-pages-{digest}"
    dest.mkdir(parents=True, exist_ok=True)
    urls = {}
    for name, (title, body) in SAMPLE_PAGES.items():
        path = dest / name
        _write_atomic(
            path,
            "<!doctype html><meta charset=utf-8>"
            f"<title>{title}</title>"
            "<link rel=icon href=\"data:image/svg+xml,"
            "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E"
            "%3Ccircle cx='8' cy='8' r='7' fill='%23315bef'/%3E%3C/svg%3E\">"
            "<body style=\"background:#fff;color:#222;padding:36px;"
            "font:16px -apple-system,'Segoe UI',sans-serif\">" + body)
        urls[name] = path.resolve().as_uri()
    return urls


def _write_atomic(path: Path, text: str):
    """Write via a temp file and rename, so a concurrent session never reads a
    half-written page out of the shared directory."""
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def build_profile(repo: Path, profile: Path, dark=False, native_menus=None,
                  empty_user_chrome=False, port=MARIONETTE_DEFAULT_PORT,
                  devtools=False, extra_prefs=None):
    """Install the theme into a fresh profile the way install.sh does.

    empty_user_chrome leaves userChrome.css blank so the caller owns the
    stylesheet entirely -- used by watch mode, where replacing one sheet gives
    exact fidelity even when a rule is deleted.
    """
    profile.mkdir(parents=True, exist_ok=True)
    shutil.copytree(repo / "chrome", profile / "chrome", dirs_exist_ok=True)

    # userChrome.css @imports customChrome.css, which the repo does not ship.
    # Some themes @import this; create it empty so the import resolves.
    (profile / "chrome" / "customChrome.css").write_text(
        "/* placeholder created by fxcss */\n", encoding="utf-8")
    if empty_user_chrome:
        (profile / "chrome" / "userChrome.css").write_text(
            '@import "customChrome.css";\n', encoding="utf-8")

    prefs = ""
    repo_userjs = repo / "configuration" / "user.js"
    if repo_userjs.exists():
        prefs += repo_userjs.read_text(encoding="utf-8") + "\n"
    prefs += EXTRA_PREFS % {"port": port}
    if devtools:
        prefs += DEVTOOLS_PREFS
    # The theme's dark rules sit behind @media (prefers-color-scheme: dark),
    # so this pref is what switches between the two.
    prefs += 'user_pref("ui.systemUsesDarkTheme", %d);\n' % (1 if dark else 0)
    if extra_prefs:
        prefs += extra_prefs
    if native_menus is not None:
        # On macOS Firefox uses native context menus by default, and CSS cannot
        # style them at all. Turning this off makes them XUL menus, which the
        # theme does style.
        val = "true" if native_menus else "false"
        for p in ("widget.macos.native-context-menus", "widget.gtk.native-context-menus"):
            prefs += f'user_pref("{p}", {val});\n'
    (profile / "user.js").write_text(prefs, encoding="utf-8")
    (profile / "xulstore.json").write_text(json.dumps(XULSTORE), encoding="utf-8")


# --- session ---------------------------------------------------------------

LAUNCH_FLAGS = [
    "--marionette",
    # Firefox 137+ requires this opt-in before Marionette will hand out the
    # chrome context that makes browser-UI screenshots possible.
    "-remote-allow-system-access",
    "--no-remote",
]


def _is_startup_race(exc):
    """Is this Marionette failure the initial browser's not-yet-attached tab?

    Marionette starts answering commands while the first window's browser is
    still wiring itself up, and a loadURI in that gap throws from deep inside
    tabbrowser -- seen intermittently on Windows CI runners as `TypeError:
    can't access property "maybeCancelContentJSExecution",
    this._browser.frameLoader.remoteTab is null`. That is a timing accident,
    not a real failure, so it is the one error worth retrying; anything else
    should surface unchanged.
    """
    message = str(exc)
    return "remoteTab is null" in message or "frameLoader is null" in message


# Seconds to wait before each retry of the startup race, one entry per retry.
# The original budget -- two retries, 2s apart -- was not enough: a
# windows-latest run burned both and failed on the same race, so a slow
# runner can need well over 4s to attach the initial browser's remoteTab.
# Backing off exponentially keeps the first retry cheap for runners that only
# need a nudge while giving the slowest ones ~30s before we give up.
STARTUP_RACE_DELAYS = (2.0, 4.0, 8.0, 16.0)


def _retry_startup_race(operation, delays=STARTUP_RACE_DELAYS, sleep=time.sleep):
    """Run operation, waiting out the initial browser's loadURI race.

    Only the failure _is_startup_race recognises is retried; anything else
    surfaces unchanged, and so does the race itself once the delays run out.
    """
    for delay in delays:
        try:
            return operation()
        except MarionetteError as exc:
            if not _is_startup_race(exc):
                raise
            print(f"  note: initial browser raced loadURI, retrying in "
                  f"{delay:.0f}s ({exc})", flush=True)
            sleep(delay)
    return operation()


def _fixture_pages_loaded(state, expected):
    return (isinstance(state, list) and len(state) == len(expected)
            and all(isinstance(tab, dict) and tab.get("url") == url
                    and tab.get("title") == title
                    and tab.get("busy") is False and tab.get("loading") is False
                    and tab.get("iconReady") is True
                    for tab, (url, title) in zip(state, expected)))


class Session:
    """A running Firefox with a themed profile and a Marionette connection."""

    def __init__(self, repo: Path, firefox: str, dark=False, native_menus=None,
                 empty_user_chrome=False, keep_profile=False, devtools=False,
                 extra_prefs=None):
        self.repo, self.firefox = Path(repo), firefox
        self.workdir = Path(tempfile.mkdtemp(prefix="fxcss-"))
        self.profile = self.workdir / "profile"
        self.keep_profile = keep_profile
        self.urls = build_pages()
        self.port = free_port()
        build_profile(self.repo, self.profile, dark=dark, native_menus=native_menus,
                      empty_user_chrome=empty_user_chrome, port=self.port,
                      devtools=devtools, extra_prefs=extra_prefs)
        self.proc = None
        self.m = None
        self._generation = 0
        self._window_ready = False

    def __enter__(self):
        env = dict(os.environ)
        env["MOZ_DISABLE_AUTO_SAFE_MODE"] = "1"
        env["MOZ_CRASHREPORTER_DISABLE"] = "1"
        # Chrome UI does not paint in headless mode.
        env.pop("MOZ_HEADLESS", None)
        cmd = [self.firefox, "--profile", str(self.profile), *LAUNCH_FLAGS,
               "--new-window", "about:blank"]
        self.proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
        self.m = Marionette(port=self.port)
        self.m.connect()
        self.m.set_context("chrome")
        self.m.script(RESIZE, [WINDOW_WIDTH, WINDOW_HEIGHT])
        self.apply_harness_css()
        return self

    def __exit__(self, *exc):
        if self.m:
            self.m.quit()
        if self.proc:
            try:
                self.proc.wait(timeout=45)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if not self.keep_profile:
            shutil.rmtree(self.workdir, ignore_errors=True)

    def info(self):
        return self.m.script(BROWSER_INFO)

    def setup_window(self, pinned=True):
        # Idempotent: callers legitimately nest (a command sets the window up,
        # then hands the session to something that does the same). Seeding
        # bookmarks twice used to leave the toolbar showing each one twice.
        if self._window_ready:
            return
        _retry_startup_race(lambda: self._open_fixture_tabs(pinned))
        # Selecting a tab through browser chrome does not update Marionette's
        # content context. Re-select this chrome window through the protocol so
        # content commands target its selected tab instead of the discarded one.
        handle = Marionette._unwrap(self.m.command("WebDriver:GetWindowHandle"))
        self.m.command("WebDriver:SwitchToWindow", {"handle": handle})
        self._wait_for_fixture_pages()
        result = self.m.async_script(SEED_BOOKMARKS)
        if result is not True:
            print(f"  note: bookmark seeding returned {result!r}", flush=True)
        time.sleep(3.0)
        self._window_ready = True

    def _open_fixture_tabs(self, pinned):
        original = self.m.script(INITIAL_TABS)
        # NewWindow waits for the browser's initial context; Navigate waits
        # for the page load. Direct addTab(url) calls can start navigation
        # before that context exists and leave every tab busy at about:blank
        # on Windows. Keep the original tabs until all replacements load.
        self.m.command("WebDriver:SetTimeouts", {"pageLoad": 30000})
        try:
            for name in ("start.html", "docs.html", "issues.html"):
                window = Marionette._unwrap(self.m.command(
                    "WebDriver:NewWindow", {"type": "tab"}))
                self.m.command("WebDriver:SwitchToWindow", {"handle": window["handle"]})
                self.m.set_context("content")
                self.m.command("WebDriver:Navigate", {"url": self.urls[name]})
                self.m.set_context("chrome")
        finally:
            self.m.set_context("chrome")
        self.m.script(SETUP_TABS, [original, pinned])

    def _wait_for_fixture_pages(self, timeout=30):
        # A loading page can paint the same blank frame twice, so _shot's
        # visual stability check cannot establish that navigation finished.
        expected = [(self.urls[name], SAMPLE_PAGES[name][0]) for name in
                    ("start.html", "docs.html", "issues.html")]
        deadline = time.monotonic() + timeout
        while True:
            state = self.m.script(FIXTURE_PAGES_STATE)
            if _fixture_pages_loaded(state, expected):
                return
            if time.monotonic() >= deadline:
                raise MarionetteError(
                    f"sample pages did not finish loading in {timeout}s: {state}")
            time.sleep(0.25)

    def apply_harness_css(self):
        """Hide artifacts of the automation harness in every window.

        Two sheets with deliberately different precedence: the agent sheet
        neutralises Firefox's automation markings but yields to any theme rule,
        while the user sheet hides harness-only widgets and must win.
        """
        self.m.script(LOAD_AGENT_SHEET, [AUTOMATION_DEFAULTS_CSS])
        return self.m.script(LOAD_HARNESS_SHEET, [HARNESS_CSS])

    def apply_css(self, css_text):
        """Load a small ad-hoc rule set as a user sheet (for experiments)."""
        return self.m.script(SWAP_SHEET, [css_text])

    def reload_theme(self):
        """Re-read chrome/ from the repo and swap it into the running browser.

        Each reload copies the tree to a fresh numbered directory and loads
        userChrome.css from there by file URI. The new path gives every file --
        the entry sheet and each @import beneath it -- a URI Firefox has not
        seen, which is what actually defeats the style-sheet cache.

        Copying rather than concatenating matters: @namespace is scoped to the
        stylesheet that declares it, so inlining imports into one sheet would
        let one file's namespace leak across all the others and silently change
        which elements match.
        """
        self._generation += 1
        dest = self.profile / "chrome" / f"live-{self._generation}"
        shutil.copytree(self.repo / "chrome", dest, dirs_exist_ok=True)
        # Keep the CI-only overrides that customChrome.css normally supplies.
        (dest / "customChrome.css").write_text(
            "/* placeholder created by fxcss */\n", encoding="utf-8")

        uri = (dest / "userChrome.css").resolve().as_uri()
        self.m.script(SWAP_FILE_SHEET, [uri])
        # Re-apply after the theme so the harness rules stay on top.
        self.apply_harness_css()

        previous = self.profile / "chrome" / f"live-{self._generation - 1}"
        if previous.exists():
            shutil.rmtree(previous, ignore_errors=True)
        return uri

    def set_dark(self, dark):
        self.m.script(SET_DARK, [1 if dark else 0])


# --- chrome-context scripts ------------------------------------------------

RESIZE = """
const win = Services.wm.getMostRecentWindow("navigator:browser");
win.moveTo(0, 0);
win.resizeTo(arguments[0], arguments[1]);
return [win.outerWidth, win.outerHeight];
"""

SEED_BOOKMARKS = """
const done = arguments[arguments.length - 1];
(async () => {
  try {
    const {PlacesUtils} =
      Services.wm.getMostRecentWindow("navigator:browser");
    for (const [title, url] of [["GitHub", "https://github.com/"],
                                ["Mozilla", "https://www.mozilla.org/"],
                                ["Example", "https://example.com/"]]) {
      await PlacesUtils.bookmarks.insert({
        parentGuid: PlacesUtils.bookmarks.toolbarGuid,
        type: PlacesUtils.bookmarks.TYPE_BOOKMARK, title, url});
    }
    done(true);
  } catch (e) { done("error: " + e); }
})();
"""

INITIAL_TABS = """
const win = Services.wm.getMostRecentWindow("navigator:browser");
return Array.from(win.gBrowser.tabs, tab => tab.linkedPanel);
"""

SETUP_TABS = """
const [originalIds, pinned] = arguments;
const win = Services.wm.getMostRecentWindow("navigator:browser");
const gb = win.gBrowser;
const original = Array.from(gb.tabs).filter(tab => originalIds.includes(tab.linkedPanel));
const fresh = Array.from(gb.tabs).filter(tab => !originalIds.includes(tab.linkedPanel));
if (fresh.length !== 3) { throw new Error("expected three loaded fixture tabs"); }
if (pinned) { gb.pinTab(fresh[0]); }
gb.selectedTab = fresh[1];
for (const tab of original) { gb.removeTab(tab, {animate: false}); }
return gb.tabs.length;
"""

FIXTURE_PAGES_STATE = """
const win = Services.wm.getMostRecentWindow("navigator:browser");
const icons = Services.prefs.getBoolPref("browser.chrome.site_icons", true);
return Array.from(win.gBrowser.tabs, tab => {
  const browser = tab.linkedBrowser;
  return {
    url: browser.currentURI.spec,
    title: browser.contentTitle,
    busy: tab.hasAttribute("busy"),
    loading: browser.webProgress.isLoadingDocument,
    iconReady: !icons || !!tab.getAttribute("image"),
  };
});
"""

# Live sheets used to load into the top browser window's windowUtils only,
# which silently missed every other chrome document: the window-modal dialog
# (commonDialog.xhtml is its own document inside #window-modal-dialog), and
# any window opened after the swap. So a `watch` session showed edits
# everywhere except the quit prompt, which kept rendering the theme the
# profile started with -- with no error anywhere. The injector walks every
# chrome docshell for the swap itself, keeps the current sheet URIs on the
# shared system global (the one place that outlives Marionette's per-script
# sandboxes), and re-applies them to each chrome document as it is created --
# the same reach the profile's real userChrome.css gets.
_INJECTOR_JS = """
const CU = typeof Cu !== "undefined" ? Cu : Components.utils;
const sysGlobal = CU.getGlobalForObject(Services);
let injector = sysGlobal._fxcssInjector;
if (!injector) {
  injector = sysGlobal._fxcssInjector = {
    sheets: {},
    observe(subject) {
      try {
        const doc = subject.document;
        if (!doc || !doc.nodePrincipal.isSystemPrincipal) { return; }
        const wu = subject.windowUtils;
        for (const uri of Object.values(injector.sheets)) {
          if (uri) {
            try { wu.loadSheetUsingURIString(uri, wu.USER_SHEET); } catch (e) {}
          }
        }
      } catch (e) {}
    },
  };
  Services.obs.addObserver(injector, "chrome-document-global-created");
}
function fxcssChromeWindows() {
  const found = [];
  for (const win of Services.wm.getEnumerator(null)) {
    try {
      for (const shell of win.docShell.getAllDocShellsInSubtree(
          Ci.nsIDocShellTreeItem.typeChrome, Ci.nsIDocShell.ENUMERATE_FORWARDS)) {
        if (shell.domWindow) { found.push(shell.domWindow); }
      }
    } catch (e) { found.push(win); }
  }
  return found;
}
function fxcssSwapSheet(slot, uri) {
  const old = injector.sheets[slot];
  for (const win of fxcssChromeWindows()) {
    let wu;
    try { wu = win.windowUtils; } catch (e) { continue; }
    if (old) {
      try { wu.removeSheetUsingURIString(old, wu.USER_SHEET); } catch (e) {}
    }
    if (uri) {
      try { wu.loadSheetUsingURIString(uri, wu.USER_SHEET); } catch (e) {}
    }
  }
  injector.sheets[slot] = uri;
}
"""

SWAP_SHEET = _INJECTOR_JS + """
const uri = "data:text/css;charset=utf-8," + encodeURIComponent(arguments[0]);
fxcssSwapSheet("adhoc", uri);
return uri.length;
"""

LOAD_AGENT_SHEET = """
const win = Services.wm.getMostRecentWindow("navigator:browser");
const u = win.windowUtils;
const uri = "data:text/css;charset=utf-8," + encodeURIComponent(arguments[0]);
if (win._fxcssAgentSheet) {
  try { u.removeSheetUsingURIString(win._fxcssAgentSheet, u.AGENT_SHEET); } catch (e) {}
}
u.loadSheetUsingURIString(uri, u.AGENT_SHEET);
win._fxcssAgentSheet = uri;
return true;
"""

LOAD_HARNESS_SHEET = """
const win = Services.wm.getMostRecentWindow("navigator:browser");
const u = win.windowUtils;
const uri = "data:text/css;charset=utf-8," + encodeURIComponent(arguments[0]);
if (win._fxcssHarnessSheet) {
  try { u.removeSheetUsingURIString(win._fxcssHarnessSheet, u.USER_SHEET); } catch (e) {}
}
u.loadSheetUsingURIString(uri, u.USER_SHEET);
win._fxcssHarnessSheet = uri;
return true;
"""

SWAP_FILE_SHEET = _INJECTOR_JS + """
fxcssSwapSheet("theme", arguments[0]);
return arguments[0];
"""

SET_DENSITY = """
Services.prefs.setIntPref("browser.uidensity", arguments[0]);
return Services.prefs.getIntPref("browser.uidensity");
"""

# SidebarController.show is async. Marionette's ExecuteScript does not await,
# so the plain-script version of this returned while the panel's inner document
# was still loading and the tree was empty -- measured: currentID flips at
# +0.02s, the document reaches "complete" with rows at +0.07s. Awaiting removes
# the guesswork entirely. An unknown panel id resolves false and leaves the
# previous panel up rather than throwing, so the boolean must be checked.
SHOW_SIDEBAR_PANEL = """
const done = arguments[arguments.length - 1];
const win = Services.wm.getMostRecentWindow("navigator:browser");
const ui = win.SidebarController;
if (!ui) { done(false); }
else {
  (async () => {
    try {
      const ok = await ui.show(arguments[0]);
      done(ok === true && ui.currentID === arguments[0]);
    } catch (e) { done(false); }
  })();
}
"""

# A fresh profile shows history as a single collapsed "Today" row and bookmarks
# as three collapsed folders -- a panel with nothing in it for a theme to style.
# Expanding is what makes these views worth capturing. Bottom-up, because
# opening a container inserts rows below it and invalidates later indices.
EXPAND_SIDEBAR_TREE = """
const win = Services.wm.getMostRecentWindow("navigator:browser");
const browser = win.document.getElementById("sidebar");
const doc = browser && browser.contentDocument;
if (!doc) { return null; }
const tree = doc.getElementById("historyTree") || doc.getElementById("bookmarks-view");
if (!tree || !tree.view) { return null; }
for (let i = tree.view.rowCount - 1; i >= 0; i--) {
  if (tree.view.getLevel(i) === 0 &&
      tree.view.isContainer(i) && !tree.view.isContainerOpen(i)) {
    tree.view.toggleOpenState(i);
  }
}
return {id: tree.id, rows: tree.view.rowCount};
"""

HIDE_SIDEBAR = """
const win = Services.wm.getMostRecentWindow("navigator:browser");
const ui = win.SidebarController;
if (ui) { try { ui.hide(); } catch (e) {} }
return true;
"""

# One pref does it: Firefox writes sidebar.revamp itself in response. Setting
# revamp alone does nothing (measured: the strip stayed horizontal in
# #TabsToolbar-customization-target). This applies live -- unlike the RTL pref,
# the startup route is actively worse here, producing a nav bar missing its
# stop/reload, downloads and account buttons.
ENABLE_VERTICAL_TABS = """
Services.prefs.setBoolPref("sidebar.verticalTabs", true);
return Services.prefs.getBoolPref("sidebar.verticalTabs");
"""

# The pref reads back true long before -- and independently of -- the layout
# moving, so the pref is not the test. #tabbrowser-tabs is physically relocated
# into #vertical-tabs, and that relocation is what a theme's #TabsToolbar
# descendant selectors stop matching against.
VERTICAL_TABS_STATE = """
const win = Services.wm.getMostRecentWindow("navigator:browser");
const tabs = win.document.querySelector("#tabbrowser-tabs");
if (!tabs) { return null; }
const rect = tabs.getBoundingClientRect();
return {
  orient: tabs.getAttribute("orient"),
  parent: tabs.parentElement ? tabs.parentElement.id : null,
  width: Math.round(rect.width),
  height: Math.round(rect.height),
};
"""

# win.CustomizableUI, not ChromeUtils.importESModule: the module URL moved
# between builds (moz-src:/// on 153, resource:/// on ESR 140) and each hard
# fails on the other, while the window property is the same object on both.
#
# addWidgetToArea neither throws nor validates -- a misspelled id is written
# into the placements and getWidget() answers plausibly for it. An actual DOM
# node is the only proof a widget id was real, which is what makes the typo in
# someone's --toolbar an error message rather than a silently empty capture.
APPLY_TOOLBAR = """
const win = Services.wm.getMostRecentWindow("navigator:browser");
const CUI = win.CustomizableUI;
if (!CUI) { return null; }
const applied = [], unknown = [];
for (const op of arguments[0]) {
  try {
    if (op.op === "remove") {
      CUI.removeWidgetFromArea(op.widget);
    } else if (op.position === null) {
      CUI.addWidgetToArea(op.widget, op.area);
    } else {
      CUI.addWidgetToArea(op.widget, op.area, op.position);
    }
  } catch (e) {
    unknown.push(op.widget);
    continue;
  }
  const node = win.document.getElementById(op.widget);
  const ok = op.op === "remove" ? !node : !!node;
  (ok ? applied : unknown).push(op.widget);
}
const navbar = win.document.getElementById("nav-bar");
return {
  applied: applied,
  unknown: unknown,
  overflowing: navbar ? navbar.hasAttribute("overflowing") : false,
};
"""

GET_DIRECTION = """
const win = Services.wm.getMostRecentWindow("navigator:browser");
return win.getComputedStyle(win.document.documentElement).direction;
"""

ENTER_CUSTOMIZE = """
const win = Services.wm.getMostRecentWindow("navigator:browser");
try { win.gCustomizeMode.enter(); return true; } catch (e) { return false; }
"""

EXIT_CUSTOMIZE = """
const win = Services.wm.getMostRecentWindow("navigator:browser");
try { win.gCustomizeMode.exit(); } catch (e) {}
return true;
"""

LOAD_VARIANT_SHEET = """
const win = Services.wm.getMostRecentWindow("navigator:browser");
const u = win.windowUtils;
if (!win._fxcssVariantSheets) { win._fxcssVariantSheets = []; }
u.loadSheetUsingURIString(arguments[0], u.USER_SHEET);
win._fxcssVariantSheets.push(arguments[0]);
return win._fxcssVariantSheets.length;
"""

UNLOAD_VARIANT_SHEETS = """
const win = Services.wm.getMostRecentWindow("navigator:browser");
const u = win.windowUtils;
for (const uri of (win._fxcssVariantSheets || [])) {
  try { u.removeSheetUsingURIString(uri, u.USER_SHEET); } catch (e) {}
}
win._fxcssVariantSheets = [];
return true;
"""

SET_DARK = """
Services.prefs.setIntPref("ui.systemUsesDarkTheme", arguments[0]);
return Services.prefs.getIntPref("ui.systemUsesDarkTheme");
"""

FOCUS_URLBAR = """
const win = Services.wm.getMostRecentWindow("navigator:browser");
win.gURLBar.focus();
win.gURLBar.value = arguments[0];
win.gURLBar.setPageProxyState("invalid");
win.gURLBar.selectionStart = win.gURLBar.selectionEnd = arguments[0].length;
return win.gURLBar.value;
"""

BLUR_URLBAR = """
const win = Services.wm.getMostRecentWindow("navigator:browser");
if (win.gURLBar.view.isOpen) { win.gURLBar.view.close(); }
win.gURLBar.value = "";
win.gURLBar.blur();
win.gBrowser.selectedBrowser.focus();
return true;
"""

OPEN_FINDBAR = """
const win = Services.wm.getMostRecentWindow("navigator:browser");
win.document.getElementById("cmd_find").doCommand();
return true;
"""

# The find bar is captured with an empty field on purpose.
#
# Firefox recolours the input to reflect the result of a search, and that state
# is set asynchronously and persists across close/reopen. With a term in the
# field, two runs of an unchanged theme could settle on different colours --
# reliably so in dark mode, which reuses the bar after the light pass. Both runs
# were internally stable, so waiting longer never converged them.
#
# An empty bar still shows everything a theme styles here: the field, the
# previous/next buttons, the checkboxes and the bar's own background. Trading a
# little realism for a view that always matches itself is the right way round
# for a tool whose whole job is comparing renders.
SELECT_TAB = """
const win = Services.wm.getMostRecentWindow("navigator:browser");
const gb = win.gBrowser;
const i = Math.min(arguments[0], gb.tabs.length - 1);
gb.selectedTab = gb.tabs[i];
return i;
"""

RESET_FINDBAR = """
const win = Services.wm.getMostRecentWindow("navigator:browser");
const bar = win.gFindBar || win.gBrowser.getFindBar();
if (bar && bar._findField) {
  bar._findField.value = "";
  // Deliberately no input event: dispatching one runs a search, and a search
  // is the only thing that sets the status attribute below.
  bar._findField.removeAttribute("status");
  if (bar.removeAttribute) { bar.removeAttribute("status"); }
  const box = bar.querySelector(".findbar-textbox, .findbar-container");
  if (box) { box.removeAttribute("status"); }
}
return !!bar;
"""

CLOSE_FINDBAR = """
const win = Services.wm.getMostRecentWindow("navigator:browser");
if (win.gFindBar) { win.gFindBar.close(); }
return true;
"""

OPEN_AUDIO_TAB = """
const [url] = arguments;
const win = Services.wm.getMostRecentWindow("navigator:browser");
const sp = Services.scriptSecurityManager.getSystemPrincipal();
// This local page has no media playback. The capture fixture controls the
// indicator attributes, so machines without an audio device render the same UI.
const tab = win.gBrowser.addTab(url, {triggeringPrincipal: sp});
win.gBrowser.selectedTab = tab;
return true;
"""

AUDIO_STATE = """
const win = Services.wm.getMostRecentWindow("navigator:browser");
const tab = win.gBrowser.selectedTab;
return {playing: tab.hasAttribute("soundplaying"), muted: tab.hasAttribute("muted")};
"""

SET_AUDIO_STATE = """
const win = Services.wm.getMostRecentWindow("navigator:browser");
const tab = win.gBrowser.selectedTab;
if (arguments[0] === null) {
  tab.removeAttribute("soundplaying");
  tab.removeAttribute("muted");
} else {
  tab.setAttribute("soundplaying", "true");
  tab.toggleAttribute("muted", Boolean(arguments[0]));
}
if (typeof win.gBrowser._tabAttrModified === "function") {
  win.gBrowser._tabAttrModified(tab, ["soundplaying", "muted"]);
}
return true;
"""


def _capture_audio_views(m, outdir):
    """Render Firefox's indicator states without requiring an OS sound device."""
    try:
        for muted, name in ((False, "extra-04-audio"), (True, "extra-05-muted")):
            m.script(SET_AUDIO_STATE, [muted])
            time.sleep(0.8)
            state = m.script(AUDIO_STATE) or {}
            if not state.get("playing") or state.get("muted") is not muted:
                raise RuntimeError(f"could not establish audio indicator state for {name}: {state}")
            _shot(m, outdir, name)
    finally:
        # Audio-only theme rules must not affect later sidebar or toolbar views.
        m.script(SET_AUDIO_STATE, [None])



# After the strip overflows, Firefox scrolls the selected tab into view -- and
# where that lands is bistable between runs (seen live: two runs settling at
# different offsets, each internally stable, ~1% of pixels apart). Pinning the
# strip to its start before capture removes the freedom. Runs as a _shot
# `before` hook because a relayout (density change, sidebar opening) triggers
# the ensure-visible scroll again.
PIN_TABSTRIP = """
const win = Services.wm.getMostRecentWindow("navigator:browser");
const box = win.gBrowser.tabContainer.arrowScrollbox;
if (box && box.scrollbox) {
  box.scrollbox.scrollTo({left: 0, top: 0, behavior: "instant"});
}
return true;
"""

MANY_TABS = """
const [url, count] = arguments;
const win = Services.wm.getMostRecentWindow("navigator:browser");
const sp = Services.scriptSecurityManager.getSystemPrincipal();
for (let i = 0; i < count; i++) {
  win.gBrowser.addTab(url, {triggeringPrincipal: sp});
}
return win.gBrowser.tabs.length;
"""

# Off the window, for the same reason APPLY_TOOLBAR takes CustomizableUI that
# way: Firefox 154 moved this module from resource://gre/modules/ to
# moz-src:///toolkit/components/contextualidentity/, and importing either URL
# hard fails on the build that does not have it -- which is how it broke, in
# the field, the day 154 shipped. browser.js declares it with
# ChromeUtils.defineESModuleGetters(this, ...) on both, so the window property
# is the same object either side and names no URL to go stale.
CONTAINER_TABS = """
const [url] = arguments;
const win = Services.wm.getMostRecentWindow("navigator:browser");
const sp = Services.scriptSecurityManager.getSystemPrincipal();
Services.prefs.setBoolPref("privacy.userContext.enabled", true);
const {ContextualIdentityService} = win;
const ids = ContextualIdentityService.getPublicIdentities().slice(0, 3);
for (const identity of ids) {
  win.gBrowser.addTab(url, {triggeringPrincipal: sp, userContextId: identity.userContextId});
}
if (ids.length) {
  win.gBrowser.selectedTab = win.gBrowser.tabs[win.gBrowser.tabs.length - 1];
}
return ids.map(i => i.userContextId);
"""

OPEN_PRIVATE = """
const win = Services.wm.getMostRecentWindow("navigator:browser");
win.OpenBrowserWindow({private: true});
return true;
"""

PRIVATE_READY = """
const win = Services.wm.getMostRecentWindow("navigator:browser");
win.moveTo(0, 0);
win.resizeTo(arguments[0], arguments[1]);
return win.document.documentElement.getAttribute("privatebrowsingmode");
"""

CLOSE_WINDOW = """
const win = Services.wm.getMostRecentWindow("navigator:browser");
win.close();
return true;
"""

NAVIGATE = """
const [url] = arguments;
const win = Services.wm.getMostRecentWindow("navigator:browser");
const sp = Services.scriptSecurityManager.getSystemPrincipal();
win.gBrowser.selectedBrowser.loadURI(Services.io.newURI(url), {triggeringPrincipal: sp});
return true;
"""

PAGE_TITLE = """
const win = Services.wm.getMostRecentWindow("navigator:browser");
return {title: win.gBrowser.selectedTab.label,
        busy: win.gBrowser.selectedTab.hasAttribute("busy")};
"""

BROWSER_INFO = """
const win = Services.wm.getMostRecentWindow("navigator:browser");
const pref = (n) => { try { return Services.prefs.getBoolPref(n); } catch (e) { return null; } };
return {
  version: Services.appinfo.version,
  buildID: Services.appinfo.appBuildID,
  os: Services.appinfo.OS,
  dpr: win.devicePixelRatio,
  outer: [win.outerWidth, win.outerHeight],
  legacyStylesheets: pref("toolkit.legacyUserProfileCustomizations.stylesheets"),
  nativeContextMenus: {
    macos: pref("widget.macos.native-context-menus"),
    gtk: pref("widget.gtk.native-context-menus"),
    windows: pref("widget.windows.native-context-menus"),
  },
};
"""


# --- views -----------------------------------------------------------------

# The window-modal prompt (commonDialog.xhtml inside #window-modal-dialog --
# the quit confirmation and friends). It is painted by its own chrome
# document, not the browser window's, which is exactly why themes get it
# wrong without noticing: WhiteSur's dialog body rendered white under every
# dark palette because the token it overrode had been dropped, and no other
# view would ever have shown it.
#
# This view cannot go through _shot. Marionette's prompt listener treats an
# open modal dialog as something to handle: it aborts whatever script is in
# flight the moment one opens, and dismisses the dialog itself on the next
# command. So the whole open -> settle -> draw -> close sequence runs
# fire-and-forget inside this one script, which returns immediately and
# writes its result to a file for Python to poll -- no Marionette command is
# sent while the dialog exists. Sleeps are nsITimer because the parent window
# is in a modal state, which suspends its own setTimeout callbacks. drawWindow
# sees the dialog because it is in-document top-layer DOM, not an OS popup.
DIALOG_VIEW = """
const outPath = arguments[0];
const win = Services.wm.getMostRecentWindow("navigator:browser");
const put = obj => win.IOUtils.writeUTF8(outPath, JSON.stringify(obj));
const sleep = ms => new Promise(resolve => {
  const timer = Cc["@mozilla.org/timer;1"].createInstance(Ci.nsITimer);
  timer.initWithCallback(resolve, ms, Ci.nsITimer.TYPE_ONE_SHOT);
  win._fxcssDialogTimers = (win._fxcssDialogTimers || []);
  win._fxcssDialogTimers.push(timer);
});
(async () => {
  const ps = Services.prompt;
  if (!ps.asyncConfirmEx || !("MODAL_TYPE_INTERNAL_WINDOW" in ps)) {
    await put({done: true, skip: "no in-window modal prompts on this Firefox"});
    return;
  }
  const flags = ps.BUTTON_POS_0 * ps.BUTTON_TITLE_IS_STRING +
                ps.BUTTON_POS_1 * ps.BUTTON_TITLE_IS_STRING +
                ps.BUTTON_POS_0_DEFAULT;
  // MODAL_TYPE_INTERNAL_WINDOW is the in-window gDialogBox path Firefox's own
  // quit prompt takes. MODAL_TYPE_WINDOW opens a separate OS-modal window and
  // blocks a nested event loop until a human closes it -- never use it here.
  const dlgPromise = ps.asyncConfirmEx(win.browsingContext,
    ps.MODAL_TYPE_INTERNAL_WINDOW, "Close this window?",
    "A modal prompt, as the quit confirmation renders it.", flags,
    "Close", "Cancel", null, null, false);

  let frame, idoc, dlg;
  for (let i = 0; i < 40; i++) {
    frame = win.document.querySelector("#window-modal-dialog browser");
    idoc = frame && frame.contentDocument;
    dlg = idoc && idoc.readyState === "complete" && idoc.querySelector("dialog");
    if (dlg && dlg.getButton && dlg.getButton("accept")) { break; }
    dlg = null;
    await sleep(250);
  }
  if (!dlg) {
    await put({done: true, skip: "the modal prompt never became ready"});
    return;
  }
  await sleep(600);

  const scale = win.devicePixelRatio;
  const width = win.innerWidth, height = win.innerHeight;
  const draw = () => {
    const canvas = win.document.createElementNS(
      "http://www.w3.org/1999/xhtml", "canvas");
    canvas.width = width * scale;
    canvas.height = height * scale;
    const ctx = canvas.getContext("2d");
    ctx.scale(scale, scale);
    ctx.drawWindow(win, 0, 0, width, height, "#ffffff");
    return canvas.toDataURL("image/png");
  };
  // The same contract _shot enforces: two consecutive identical captures.
  let previous = draw(), shot = previous;
  for (let i = 0; i < 8; i++) {
    await sleep(500);
    shot = draw();
    if (shot === previous) { break; }
    previous = shot;
  }

  dlg.getButton("cancel").click();
  await Promise.race([dlgPromise.catch(() => {}), sleep(3000)]);
  await put({done: true, shot});
})().catch(async e => {
  try {
    const browser = win.document.querySelector("#window-modal-dialog browser");
    const dlg = browser && browser.contentDocument &&
                browser.contentDocument.querySelector("dialog");
    if (dlg) { dlg.getButton("cancel").click(); }
  } catch (e2) {}
  await put({done: true, skip: "modal prompt capture failed: " + e});
});
return "kicked";
"""


def _dialog_shot(session: "Session", outdir: Path, name: str, timeout=45.0):
    """Capture the window-modal prompt as `name`, via DIALOG_VIEW's handoff
    file. A timeout cannot wedge the run: the next Marionette command
    dismisses a dialog left open, that being the listener behaviour the
    fire-and-forget shape exists to sidestep."""
    handoff = outdir / f".{name}.dialog.json"
    if handoff.exists():
        handoff.unlink()
    kicked = session.m.script(DIALOG_VIEW, [str(handoff)])
    if kicked != "kicked":
        print(f"  note: could not start the modal prompt; skipping {name}",
              flush=True)
        return
    result = None
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.5)
        if not handoff.exists():
            continue
        try:
            data = json.loads(handoff.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue  # a write in progress
        if data.get("done"):
            result = data
            break
    try:
        handoff.unlink()
    except OSError:
        pass
    if result is None:
        print(f"  note: {name} timed out; skipping that view", flush=True)
        return
    if result.get("skip"):
        print(f"  note: {result['skip']}; skipping {name}", flush=True)
        if result["skip"] == "no in-window modal prompts on this Firefox":
            return "no in-window modal prompts"
        return
    png = base64.b64decode(result["shot"].split(",", 1)[1])
    if len(png) < 2000:
        raise RuntimeError(f"screenshot {name} is implausibly small ({len(png)} bytes)")
    (outdir / f"{name}.png").write_bytes(png)
    print(f"  captured {name}.png ({len(png) // 1024} KB)", flush=True)


def capture_views(session: Session, outdir: Path, modes=("light", "dark"),
                  variants=None, toolbar=None):
    """Capture views and require every supported state to be represented."""
    from . import capture
    outdir.mkdir(parents=True, exist_ok=True)
    expected = capture.expected_views(variants or (), modes)
    # Old generated images cannot mask a failed view on a repeated run.
    for path in outdir.glob("*.png"):
        if path.stem in capture.expected_views() or capture.VARIANT.fullmatch(path.stem):
            path.unlink()
    coverage = {"info": {}, "unsupported": {}, "failed": {}}
    try:
        info = _capture_views(session, outdir, modes, variants, toolbar, coverage)
    finally:
        capture.write_coverage(outdir, coverage["info"], expected,
                               coverage["unsupported"], coverage["failed"])
    capture.validate_coverage(outdir, expected)
    return info


def _capture_views(session: Session, outdir: Path, modes=("light", "dark"),
                   variants=None, toolbar=None, coverage=None):
    """Capture the standard set of views. Returns the browser info dict."""
    outdir.mkdir(parents=True, exist_ok=True)
    session.setup_window()
    info = session.info()
    coverage["info"] = info
    print(f"  firefox {info['version']} ({info['os']}), dpr={info['dpr']}, "
          f"window={info['outer']}, legacyStylesheets={info['legacyStylesheets']}",
          flush=True)
    if not info["legacyStylesheets"]:
        raise RuntimeError(
            "toolkit.legacyUserProfileCustomizations.stylesheets is false; "
            "userChrome.css would not be applied and this would be a preview "
            "of unthemed Firefox")

    m = session.m
    for mode in modes:
        session.set_dark(mode == "dark")
        if mode == "dark":
            time.sleep(2.0)

        _shot(m, outdir, f"{mode}-01-window")

        m.script(FOCUS_URLBAR, ["firefox css theme"])
        time.sleep(1.0)
        _shot(m, outdir, f"{mode}-02-urlbar")
        m.script(BLUR_URLBAR)
        time.sleep(0.8)

        # Find bars belong to a tab, and one that has already been used keeps
        # state from that use. Light mode ran on the previous tab, so give dark
        # its own never-opened find bar rather than reusing a dirty one.
        m.script(SELECT_TAB, [1 if mode == "light" else 2])
        time.sleep(1.0)
        m.script(OPEN_FINDBAR)
        time.sleep(1.2)
        m.script(RESET_FINDBAR)
        time.sleep(0.8)
        _shot(m, outdir, f"{mode}-03-findbar", before=RESET_FINDBAR)
        m.script(CLOSE_FINDBAR)
        time.sleep(0.6)

        # The window-modal prompt is a per-scheme view like the three above:
        # its body is painted by its own document, and dark is where themes
        # break it (see DIALOG_VIEW). Captured through its handoff file, not
        # _shot -- no Marionette command may run while the dialog is up.
        unsupported = _dialog_shot(session, outdir, f"{mode}-04-dialog")
        if unsupported:
            coverage["unsupported"][f"{mode}-04-dialog"] = unsupported

    # Extra chrome states, captured once rather than per colour scheme: each is
    # about a distinct piece of UI appearing, not about light versus dark.
    session.set_dark(False)
    time.sleep(1.5)

    # Set the same attributes Firefox uses for playing/muted tabs. This is a
    # CSS state fixture: OS audio devices are absent on many CI machines, and
    # playback failure must not turn an audio screenshot into an ordinary tab.
    m.script(OPEN_AUDIO_TAB, [session.urls["audio.html"]])
    time.sleep(2.0)
    _capture_audio_views(m, outdir)
    info["audioIndicators"] = "controlled playing and muted tab attributes"

    # Container tabs: each carries an identity colour along the tab and an
    # identity label in the address bar, both of which themes style and neither
    # of which appears in an ordinary window.
    containers = m.script(CONTAINER_TABS, [session.urls["docs.html"]])
    time.sleep(3.0)
    if containers:
        _shot(m, outdir, "extra-06-containers")
    else:
        print("  note: no container identities available; skipping that view", flush=True)

    # Enough tabs to overflow the strip, which brings out the scroll controls
    # and the shrunken tab layout.
    m.script(MANY_TABS, [session.urls["docs.html"], 18])
    time.sleep(3.0)
    _shot(m, outdir, "extra-07-many-tabs", before=PIN_TABSTRIP)

    # A private window is a separate window with its own styling; plenty of
    # themes style it and never look at it again.
    #
    # Marionette screenshots the window it is *switched to*, not the most
    # recently opened one, so opening a window is not enough -- without the
    # switch this captured the original window again and looked like the view
    # was simply duplicated.
    before = set(m.command("WebDriver:GetWindowHandles"))
    m.script(OPEN_PRIVATE)
    time.sleep(3.5)
    opened = [h for h in m.command("WebDriver:GetWindowHandles") if h not in before]
    if opened:
        m.command("WebDriver:SwitchToWindow", {"handle": opened[0]})
        # Harness sheets are loaded per window, so a newly opened one starts
        # without them and would show the automation icons.
        session.apply_harness_css()
        mode = m.script(PRIVATE_READY, [WINDOW_WIDTH, WINDOW_HEIGHT])
        # Load a known local page rather than leaving about:privatebrowsing up.
        # That page is tall enough to need a scrollbar in some runs and not
        # others, and a 2px scrollbar appearing is a real pixel difference. The
        # private chrome is what this view is for; the content is incidental.
        m.script(NAVIGATE, [session.urls["start.html"]])
        time.sleep(3.0)
        if mode:
            _shot(m, outdir, "extra-08-private")
        else:
            print("  note: new window is not private; skipping that view", flush=True)
        m.script(CLOSE_WINDOW)
        time.sleep(1.5)
        remaining = m.command("WebDriver:GetWindowHandles")
        if remaining:
            m.command("WebDriver:SwitchToWindow", {"handle": remaining[0]})
    else:
        print("  note: private window did not open; skipping that view", flush=True)

    # Density, direction, the sidebar and customize mode are in-window states
    # of the same browser, captured once each. Every one restores what it
    # changed so the next view starts from the same place.
    m.script(SET_DENSITY, [1])
    time.sleep(1.5)
    _shot(m, outdir, "extra-09-compact", before=PIN_TABSTRIP)
    m.script(SET_DENSITY, [0])
    time.sleep(1.0)

    for panel, name in (("viewBookmarksSidebar", "extra-10-sidebar-bookmarks"),
                        ("viewHistorySidebar", "extra-11-sidebar-history")):
        if m.async_script(SHOW_SIDEBAR_PANEL, [panel]) is True:
            time.sleep(1.0)
            m.script(EXPAND_SIDEBAR_TREE)
            time.sleep(0.8)
            _shot(m, outdir, name, before=PIN_TABSTRIP)
        else:
            print(f"  note: {panel} unavailable here; skipping that view",
                  flush=True)
    m.script(HIDE_SIDEBAR)
    time.sleep(1.0)

    # Current Firefox ignores intl.uidirection entirely -- the pref reaches the
    # profile and the chrome stays LTR (measured, not assumed). The supported
    # lever is pseudo-localisation: intl.l10n.pseudo="bidi" builds the chrome
    # right-to-left, applied at startup in a short dedicated session. Labels
    # come out in Firefox's fake-bidi lettering by design; the point of the
    # view is the mirrored layout, not the strings.
    with Session(session.repo, session.firefox,
                 extra_prefs='user_pref("intl.l10n.pseudo", "bidi");\n') as rtl:
        rtl.setup_window()
        if rtl.m.script(GET_DIRECTION) == "rtl":
            _shot(rtl.m, outdir, "extra-12-rtl")
        else:
            print("  note: chrome did not come up RTL; skipping that view", flush=True)

    if m.script(ENTER_CUSTOMIZE):
        time.sleep(2.5)
        _shot(m, outdir, "extra-13-customize", before=PIN_TABSTRIP)
        m.script(EXIT_CUSTOMIZE)
        time.sleep(2.0)
    else:
        print("  note: customize mode unavailable; skipping that view", flush=True)

    # Vertical tabs and a customised toolbar both leave the window changed in
    # ways that do not undo, so each gets its own short Session rather than
    # contaminating everything captured after it. Turning vertical tabs back off
    # restores the tab strip's geometry but leaves the launcher rail behind, and
    # CustomizableUI.reset() does not restore a nav bar that has overflowed --
    # measured at 1.0% of pixels on Firefox 153 and 2.8% on ESR 140, with the
    # placements reading as default the whole time.
    with Session(session.repo, session.firefox) as vt:
        vt.setup_window()
        vt.m.script(ENABLE_VERTICAL_TABS)
        time.sleep(2.5)
        state = vt.m.script(VERTICAL_TABS_STATE) or {}
        # Not "taller than wide": at this window size ESR's strip is 242x227,
        # so that test would skip a view that is working perfectly. The
        # relocation into #vertical-tabs is the real signal, and a non-zero
        # width rules out the hidden-sidebar state, which keeps orient=vertical
        # while rendering no strip at all.
        if (state.get("orient") == "vertical"
                and state.get("parent") == "vertical-tabs"
                and state.get("width")):
            _shot(vt.m, outdir, "extra-14-vertical-tabs")
        else:
            version = re.match(r"\d+", str(info.get("version", "")))
            if version and int(version[0]) < 133:
                coverage["unsupported"]["extra-14-vertical-tabs"] = "Firefox before 133"
            print("  note: vertical tabs unavailable on this Firefox "
                  "(needs 133+); skipping that view", flush=True)

    with Session(session.repo, session.firefox) as tb:
        tb.setup_window()
        result = tb.m.script(APPLY_TOOLBAR, [toolbar or default_toolbar_ops()])
        if result and result.get("applied"):
            if result.get("unknown"):
                print(f"  note: no such toolbar widget: "
                      f"{', '.join(result['unknown'])}", flush=True)
            if result.get("overflowing"):
                print("  note: the nav bar overflowed, so some widgets are "
                      "behind the chevron rather than visible", flush=True)
            time.sleep(2.0)
            _shot(tb.m, outdir, "extra-15-toolbar", before=PIN_TABSTRIP)
        else:
            print("  note: could not rearrange the toolbar; skipping that view",
                  flush=True)

    # Optional stylesheets, one capture per variant. Loaded live as a user
    # sheet by file URI -- relative url()s inside the sheet keep resolving --
    # then removed, so variants never contaminate each other.
    #
    # In its own session, like the other contaminating states above: by this
    # point the shared window is 18 tabs deep with a container tab selected,
    # and every variant capture inherited that. Measured on a real run, a
    # variant image sat 0.63% from the many-tabs view and 3.69% from a clean
    # window -- so what a reader saw was mostly Firefox's overflowed strip and
    # a container identity stripe, with the stylesheet's effect somewhere
    # underneath. These captures are the ones a theme's README shows off, so
    # they get a clean window.
    if variants:
        with Session(session.repo, session.firefox) as vr:
            vr.setup_window()
            for slug, sheets in sorted(variants.items()):
                # A value may be one sheet or several: a combo loads them
                # together, the way a user stacking install options would.
                for sheet in ([sheets] if isinstance(sheets, Path) else sheets):
                    vr.m.script(LOAD_VARIANT_SHEET, [sheet.resolve().as_uri()])
                time.sleep(1.5)
                _shot(vr.m, outdir, "variant-" + slug)
                vr.m.script(UNLOAD_VARIANT_SHEETS)
                time.sleep(0.8)

    (outdir / "render-info.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    return info


def _capture_png(m, attempts=4, delay=1.5):
    """Take a screenshot, tolerating transient capture failures.

    Windows runners occasionally fail a single WebDriver:TakeScreenshot with
    "Unable to capture screenshot" from the canvas layer and then succeed
    immediately afterwards. A screenshot is idempotent, so a short bounded
    retry is strictly better than aborting a multi-minute capture run. A
    browser that is genuinely gone still raises, after the last attempt.
    """
    last = None
    for attempt in range(attempts):
        try:
            return m.screenshot()
        except MarionetteError as exc:
            last = exc
            if attempt < attempts - 1:
                print(f"  note: screenshot attempt {attempt + 1} failed "
                      f"({str(exc)[:80]}); retrying", flush=True)
                time.sleep(delay)
    raise last


def _shot(m, outdir: Path, name: str, tries=8, delay=0.5, before=None):
    """Capture once the window has stopped changing.

    Some UI state arrives asynchronously -- the find bar recolours its field
    once a search reports back, for instance -- so a fixed sleep races it and
    the same theme can render two different screenshots. Waiting for two
    consecutive identical captures removes that whole class of flake without
    having to know which widget is late.

    Compares encoded PNG bytes rather than pixels so this stays dependency-free.

    `before` is a chrome script re-run ahead of every capture attempt, for state
    a widget may set again asynchronously after being cleared once. Two
    consecutive identical captures then mean it stayed cleared, rather than
    merely having been cleared at some earlier point.
    """
    if before:
        m.script(before)
    previous = _capture_png(m)
    png = previous
    for attempt in range(tries):
        time.sleep(delay)
        if before:
            m.script(before)
        png = _capture_png(m)
        if png == previous:
            break
        previous = png
    else:
        print(f"  warning: {name} never settled after {tries} attempts; "
              f"this view may compare as changed when nothing did", flush=True)

    if len(png) < 2000:
        raise RuntimeError(f"screenshot {name} is implausibly small ({len(png)} bytes)")
    (outdir / f"{name}.png").write_bytes(png)
    print(f"  captured {name}.png ({len(png) // 1024} KB)", flush=True)


# Exactly the areas CustomizableUI registers, measured on both builds. Naming a
# real-but-unregistered area (toolbar-menubar, say) throws "Unknown
# customization area" deep inside Firefox; catching it here says so in English.
TOOLBAR_AREAS = ("nav-bar", "TabsToolbar", "PersonalToolbar", "vertical-tabs",
                 "unified-extensions-area", "widget-overflow-fixed-list")

# What the toolbar view arranges when nobody says otherwise. Moving
# new-tab-button into the nav bar is not an arbitrary choice: it is the
# rearrangement WhiteSur's own README asks users to make by hand, and themes
# that document such a setup have no other way to test it. Kept deliberately
# small -- a longer list overflows the nav bar at this window width, which hides
# the very widgets the view exists to show.
DEFAULT_TOOLBAR = ("new-tab-button>nav-bar, home-button>nav-bar, "
                   "bookmarks-menu-button>nav-bar, history-panelmenu>nav-bar, "
                   "preferences-button>nav-bar")


def parse_toolbar_spec(spec):
    """Turn "widget>area@position, -widget" into CustomizableUI operations.

    'widget>area' moves a widget into an area, optionally at '@position';
    '-widget' removes one. Order is preserved, because position indices are
    resolved against the arrangement as it stands when each step runs.
    """
    ops = []
    for raw in (spec or "").split(","):
        item = raw.strip()
        if not item:
            continue
        if item.startswith("-"):
            widget = item[1:].strip()
            if not widget:
                raise ValueError("'-' with no widget after it")
            ops.append({"op": "remove", "widget": widget,
                        "area": None, "position": None})
            continue
        if ">" not in item:
            raise ValueError(
                f"{item!r} is not a toolbar move: write 'widget>area', "
                f"e.g. 'new-tab-button>nav-bar', or '-widget' to remove one")
        widget, _, target = (part.strip() for part in item.partition(">"))
        area, _, position = (part.strip() for part in target.partition("@"))
        if not widget:
            raise ValueError(f"{item!r} has no widget before the '>'")
        if area not in TOOLBAR_AREAS:
            raise ValueError(
                f"{area!r} is not a toolbar area. Known areas: "
                f"{', '.join(TOOLBAR_AREAS)}")
        if position:
            try:
                position = int(position)
            except ValueError:
                raise ValueError(f"{position!r} is not a position number "
                                 f"in {item!r}") from None
        ops.append({"op": "move", "widget": widget, "area": area,
                    "position": position if position != "" else None})
    return ops


def default_toolbar_ops():
    return parse_toolbar_spec(DEFAULT_TOOLBAR)


def layer_stylesheets(user_chrome: Path, urls):
    """Load the base sheet, then optional sheets, through an import-only entry.

    Appending @import after style rules or @namespace makes it invalid.
    Prepending it would reverse the intended cascade. Keep the original in
    the same directory so its relative imports and image URLs still resolve,
    and give each stylesheet its own namespace and charset scope.
    """
    if not urls:
        return
    base = user_chrome.with_name("fxcss-base-userChrome.css")
    counter = 2
    while base.exists() or base.is_symlink():
        base = user_chrome.with_name(f"fxcss-base-userChrome-{counter}.css")
        counter += 1
    user_chrome.rename(base)
    lines = ["/* Base theme, then optional sheets layered on by fxcss. */",
             f'@import "{base.name}";']
    lines.extend(f"@import {json.dumps(url, ensure_ascii=False)};" for url in urls)
    user_chrome.write_text("\n".join(lines) + "\n", encoding="utf-8")


def find_variant_sheets(theme: Path):
    """Optional stylesheets a theme ships for users to layer on.

    Looks in the same folders `fxcss try --with` does. Keys are slugs safe for
    a filename, so a capture can be named after its variant.
    """
    from .fetch import VARIANT_DIRS
    sheets = {}
    for folder in VARIANT_DIRS:
        directory = theme / folder
        if directory.is_dir():
            for css in sorted(directory.glob("*.css")):
                slug = re.sub(r"[^a-z0-9-]+", "-", css.stem.lower()).strip("-")
                if slug:
                    sheets[slug] = css
    return sheets


def parse_variant_spec(spec, available):
    """Turn a --variants value into {slug: [sheet, ...]}.

    Commas separate captures; a plus combines sheets within one capture, the
    way a user stacking install options would run them:

        all                     every optional sheet, one capture each
        a,b                     two captures
        a+b,c                   a and b together, then c alone

    Raises ValueError naming anything unknown, listing what exists.
    """
    if not spec:
        return {}
    spec = spec.strip()
    if spec.lower() == "all":
        return {slug: [path] for slug, path in available.items()}
    chosen = {}
    unknown = []
    for part in (p.strip().lower() for p in spec.split(",") if p.strip()):
        names = [n.strip() for n in part.split("+") if n.strip()]
        missing = [n for n in names if n not in available]
        if missing:
            unknown += missing
            continue
        chosen["+".join(names)] = [available[n] for n in names]
    if unknown:
        raise ValueError(
            "no optional stylesheet named " + ", ".join(sorted(set(unknown)))
            + "; available: " + (", ".join(sorted(available)) or "none"))
    return chosen


# Order matters twice over: it is the menu order, and the first entry is the
# default when nothing is chosen. Gecko forks are included because userChrome
# themes are as popular there as in Firefox proper, and they speak Marionette.
CHANNEL_ORDER = ("stable", "beta", "developer", "nightly", "esr",
                 "librewolf", "floorp", "waterfox", "zen")

CHANNEL_ALIASES = {"dev": "developer", "developer-edition": "developer",
                   "release": "stable", "firefox": "stable"}


def _label_for(name):
    """Which build a file or app name looks like, or None."""
    n = name.lower()
    for label in CHANNEL_ORDER[1:]:
        if label in n:
            return label
    if "firefox" in n or n in ("zen browser",):
        return "stable"
    return None


def _mac_binary(app: Path):
    macos_dir = app / "Contents" / "MacOS"
    if not macos_dir.is_dir():
        return None
    for name in ("firefox", "librewolf", "floorp", "waterfox", "zen"):
        candidate = macos_dir / name
        if candidate.is_file():
            return candidate
    executables = [p for p in macos_dir.iterdir()
                   if p.is_file() and os.access(p, os.X_OK)]
    return executables[0] if len(executables) == 1 else None


def discover_firefoxes(extra_roots=None):
    """Every Gecko build installed in the usual places.

    Returns [{"label", "path"}] in CHANNEL_ORDER, one entry per label, first
    found wins. FXCSS_FIREFOX_ROOTS (os.pathsep-separated directories) extends
    the search, for builds kept somewhere unusual.
    """
    roots = list(extra_roots or [])
    env_roots = os.environ.get("FXCSS_FIREFOX_ROOTS", "")
    roots += [Path(r) for r in env_roots.split(os.pathsep) if r]

    found = {}

    def record(label, binary):
        if label and binary and label not in found:
            found[label] = str(Path(binary).resolve())

    if sys.platform == "darwin" or extra_roots or env_roots:
        for root in roots + [Path("/Applications"), Path.home() / "Applications"]:
            if not Path(root).is_dir():
                continue
            for app in sorted(Path(root).glob("*.app")):
                record(_label_for(app.stem), _mac_binary(app))

    if sys.platform == "win32":
        program_dirs = [Path(r"C:\Program Files"), Path(r"C:\Program Files (x86)")]
        names = {"Mozilla Firefox": "stable", "Firefox Nightly": "nightly",
                 "Firefox Developer Edition": "developer", "Firefox Beta": "beta",
                 "Mozilla Firefox ESR": "esr", "LibreWolf": "librewolf",
                 "Ablaze Floorp": "floorp", "Waterfox": "waterfox",
                 "Zen Browser": "zen"}
        for base in program_dirs + [Path(r) for r in roots]:
            for folder, label in names.items():
                for exe_name in ("firefox.exe", "librewolf.exe", "floorp.exe",
                                 "waterfox.exe", "zen.exe"):
                    exe = Path(base) / folder / exe_name
                    if exe.is_file():
                        record(label, exe)

    if sys.platform.startswith("linux"):
        which_names = {"firefox": "stable", "firefox-esr": "esr",
                       "firefox-beta": "beta", "firefox-nightly": "nightly",
                       "firefox-developer-edition": "developer",
                       "librewolf": "librewolf", "floorp": "floorp",
                       "waterfox": "waterfox", "zen-browser": "zen"}
        for name, label in which_names.items():
            binary = shutil.which(name)
            if binary:
                record(label, binary)
        for opt in sorted(Path("/opt").glob("firefox*")) if Path("/opt").is_dir() else []:
            binary = opt / "firefox"
            if binary.is_file():
                record(_label_for(opt.name) or "stable", binary)

    return [{"label": label, "path": found[label]}
            for label in CHANNEL_ORDER if label in found]


def firefox_version(binary):
    """The build's version string, or None if it will not say."""
    try:
        out = subprocess.run([str(binary), "--version"], capture_output=True,
                             text=True, timeout=15).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return out.split("Firefox")[-1].strip() or out or None


def find_firefox(explicit=None):
    """Resolve a Firefox: a path, a channel name, or the best install found.

    `explicit` may be a filesystem path, or a channel/fork name -- stable,
    beta, developer (or dev), nightly, esr, librewolf, floorp, waterfox, zen --
    resolved against the builds installed on this machine. Always returns an
    absolute path: Firefox resolves its own application directory from argv[0],
    and a relative path surfaces much later as a Marionette connection timeout.
    """
    if explicit:
        looks_like_path = (os.sep in explicit or explicit.startswith("~")
                           or Path(explicit).exists())
        if looks_like_path:
            return str(Path(explicit).expanduser().resolve())
        wanted = CHANNEL_ALIASES.get(explicit.lower(), explicit.lower())
        builds = discover_firefoxes()
        for build in builds:
            if build["label"] == wanted:
                return build["path"]
        installed = ", ".join(b["label"] for b in builds) or "none found"
        raise SystemExit(
            f"No {explicit!r} build installed (installed: {installed}). "
            f"Pass a path instead, or set FXCSS_FIREFOX_ROOTS if it lives "
            f"somewhere unusual.")

    env = os.environ.get("FIREFOX_BIN")
    if env:
        return str(Path(env).expanduser().resolve())

    builds = discover_firefoxes()
    if builds:
        return builds[0]["path"]
    found = shutil.which("firefox")
    if found:
        return found
    raise SystemExit(
        "Could not find Firefox. Pass --firefox /path/to/firefox or set FIREFOX_BIN.")


def slugify_url(url):
    """A short, filesystem-safe name for a URL."""
    trimmed = re.sub(r"^https?://(www\.)?", "", url)
    trimmed = re.sub(r"[?#].*$", "", trimmed).strip("/")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", trimmed).strip("-").lower()
    return (slug or "page")[:48]


def capture_live(session, outdir: Path, urls, modes=("light", "dark"), settle=6.0):
    """Screenshot the theme against real websites.

    Written into a `live/` subdirectory, which is deliberate: `compare` only
    looks at PNGs at the top level, so these never take part in the pass/fail
    comparison. They cannot -- someone else's page can change its content, its
    title or its favicon between two runs, and a theme pull request would get
    blamed for it. These are for looking at, not for diffing.
    """
    live_dir = Path(outdir) / "live"
    live_dir.mkdir(parents=True, exist_ok=True)
    m = session.m
    session.setup_window()

    captured = []
    for url in urls:
        slug = slugify_url(url)
        m.script(NAVIGATE, [url])

        # Wait for the tab to stop reporting itself busy, then let the page
        # settle. _shot additionally waits for two identical frames, which
        # covers late-loading images without needing to understand the page.
        deadline = time.time() + 45
        while time.time() < deadline:
            time.sleep(1.0)
            if not m.script(PAGE_TITLE).get("busy"):
                break
        time.sleep(settle)

        info = m.script(PAGE_TITLE)
        print(f"  {url}\n    loaded: {info['title'][:64]!r}", flush=True)
        for mode in modes:
            session.set_dark(mode == "dark")
            time.sleep(2.0)
            _shot(m, live_dir, f"{slug}-{mode}")
            captured.append(live_dir / f"{slug}-{mode}.png")
        session.set_dark(False)
        time.sleep(1.0)
    return captured
