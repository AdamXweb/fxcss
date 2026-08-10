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

def _sine_wav_data_uri(seconds=6, hz=440, rate=8000):
    """A short sine tone as a data: URI.

    Generated rather than shipped as a binary, and inlined rather than fetched,
    so the tab-playing-audio state stays local and identical on every run.
    """
    import base64
    import math
    import struct
    frames = b"".join(
        struct.pack("<h", int(9000 * math.sin(2 * math.pi * hz * i / rate)))
        for i in range(rate * seconds))
    header = (b"RIFF" + struct.pack("<I", 36 + len(frames)) + b"WAVEfmt "
              + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
              + b"data" + struct.pack("<I", len(frames)))
    return "data:audio/wav;base64," + base64.b64encode(header + frames).decode("ascii")


SAMPLE_PAGES = {
    "start.html": ("Start", "<h1>Theme preview</h1><p>First tab.</p>"),
    "docs.html": ("Documentation", "<h1>Documentation</h1><p>Second tab.</p>"),
    "issues.html": ("Issue tracker", "<h1>Issues</h1><p>Third tab.</p>"),
    "audio.html": ("Now playing", "<h1>Audio</h1><p>Plays a tone so the tab shows "
                   "its sound indicator.</p>"
                   "<audio src=\"__AUDIO__\" loop autoplay></audio>"),
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
    tone = None
    for name, (title, body) in SAMPLE_PAGES.items():
        if "__AUDIO__" in body:
            tone = tone or _sine_wav_data_uri()
            body = body.replace("__AUDIO__", tone)
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
                  devtools=False):
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


class Session:
    """A running Firefox with a themed profile and a Marionette connection."""

    def __init__(self, repo: Path, firefox: str, dark=False, native_menus=None,
                 empty_user_chrome=False, keep_profile=False, devtools=False):
        self.repo, self.firefox = Path(repo), firefox
        self.workdir = Path(tempfile.mkdtemp(prefix="fxcss-"))
        self.profile = self.workdir / "profile"
        self.keep_profile = keep_profile
        self.urls = build_pages()
        self.port = free_port()
        build_profile(self.repo, self.profile, dark=dark, native_menus=native_menus,
                      empty_user_chrome=empty_user_chrome, port=self.port,
                      devtools=devtools)
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
        self._window_ready = True
        result = self.m.async_script(SEED_BOOKMARKS)
        if result is not True:
            print(f"  note: bookmark seeding returned {result!r}", flush=True)
        self.m.script(SETUP_TABS, [[self.urls["start.html"], self.urls["docs.html"],
                                    self.urls["issues.html"]], pinned])
        time.sleep(3.0)

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
    const {PlacesUtils} = ChromeUtils.importESModule(
      "resource://gre/modules/PlacesUtils.sys.mjs");
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

SETUP_TABS = """
const [urls, pinned] = arguments;
const sp = Services.scriptSecurityManager.getSystemPrincipal();
const win = Services.wm.getMostRecentWindow("navigator:browser");
const gb = win.gBrowser;
while (gb.tabs.length > 1) { gb.removeTab(gb.tabs[gb.tabs.length - 1]); }
gb.selectedBrowser.loadURI(Services.io.newURI(urls[0]), {triggeringPrincipal: sp});
for (let i = 1; i < urls.length; i++) { gb.addTab(urls[i], {triggeringPrincipal: sp}); }
if (pinned) { gb.pinTab(gb.tabs[0]); }
gb.selectedTab = gb.tabs[1];
return gb.tabs.length;
"""

SWAP_SHEET = """
const win = Services.wm.getMostRecentWindow("navigator:browser");
const u = win.windowUtils;
const uri = "data:text/css;charset=utf-8," + encodeURIComponent(arguments[0]);
if (win._fxcssSheet) {
  try { u.removeSheetUsingURIString(win._fxcssSheet, u.USER_SHEET); } catch (e) {}
}
u.loadSheetUsingURIString(uri, u.USER_SHEET);
win._fxcssSheet = uri;
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

SWAP_FILE_SHEET = """
const win = Services.wm.getMostRecentWindow("navigator:browser");
const u = win.windowUtils;
const uri = arguments[0];
if (win._fxcssSheet) {
  try { u.removeSheetUsingURIString(win._fxcssSheet, u.USER_SHEET); } catch (e) {}
}
u.loadSheetUsingURIString(uri, u.USER_SHEET);
win._fxcssSheet = uri;
return uri;
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
// 0 = allow autoplay. Without this the tab never starts playing and the sound
// indicator never appears.
Services.prefs.setIntPref("media.autoplay.default", 0);
Services.prefs.setIntPref("media.autoplay.blocking_policy", 0);
const tab = win.gBrowser.addTab(url, {triggeringPrincipal: sp});
win.gBrowser.selectedTab = tab;
return true;
"""

AUDIO_STATE = """
const win = Services.wm.getMostRecentWindow("navigator:browser");
const tab = win.gBrowser.selectedTab;
return {playing: tab.hasAttribute("soundplaying"), muted: tab.hasAttribute("muted")};
"""

MUTE_TAB = """
const win = Services.wm.getMostRecentWindow("navigator:browser");
win.gBrowser.selectedTab.toggleMuteAudio();
return win.gBrowser.selectedTab.hasAttribute("muted");
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

CONTAINER_TABS = """
const [url] = arguments;
const win = Services.wm.getMostRecentWindow("navigator:browser");
const sp = Services.scriptSecurityManager.getSystemPrincipal();
Services.prefs.setBoolPref("privacy.userContext.enabled", true);
const {ContextualIdentityService} = ChromeUtils.importESModule(
  "resource://gre/modules/ContextualIdentityService.sys.mjs");
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

def capture_views(session: Session, outdir: Path, modes=("light", "dark")):
    """Capture the standard set of views. Returns the browser info dict."""
    outdir.mkdir(parents=True, exist_ok=True)
    session.setup_window()
    info = session.info()
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

    # Extra chrome states, captured once rather than per colour scheme: each is
    # about a distinct piece of UI appearing, not about light versus dark.
    session.set_dark(False)
    time.sleep(1.5)

    # A tab playing audio, then the same tab muted -- the speaker and mute
    # indicators are separate pieces of tab styling and themes get them wrong
    # independently.
    m.script(OPEN_AUDIO_TAB, [session.urls["audio.html"]])
    time.sleep(4.0)
    state = m.script(AUDIO_STATE)
    if not state.get("playing"):
        print("  note: audio tab is not reporting sound; capturing anyway", flush=True)
    _shot(m, outdir, "extra-04-audio")
    m.script(MUTE_TAB)
    time.sleep(1.2)
    _shot(m, outdir, "extra-05-muted")

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
    _shot(m, outdir, "extra-07-many-tabs")

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


def find_firefox(explicit=None):
    """Locate a Firefox binary, preferring an explicit path.

    Always returns an absolute path: Firefox resolves its own application
    directory from argv[0], and a relative path can leave it unable to find its
    resources, which surfaces much later as a Marionette connection timeout.
    """
    if explicit:
        return str(Path(explicit).expanduser().resolve())
    env = os.environ.get("FIREFOX_BIN")
    if env:
        return str(Path(env).expanduser().resolve())
    candidates = [
        "/Applications/Firefox.app/Contents/MacOS/firefox",
        str(Path.home() / "Applications/Firefox.app/Contents/MacOS/firefox"),
        r"C:\Program Files\Mozilla Firefox\firefox.exe",
        r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
        "/usr/bin/firefox", "/usr/local/bin/firefox", "/snap/bin/firefox",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
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
