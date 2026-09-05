"""Fixture setup must replace an unusable initial browser and stay idempotent."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import csv
import io
import os
import subprocess
import tempfile
import threading
import time

from fxcss import core


@contextmanager
def delayed_document():
    """A local fixture whose navigation outlasts the old three-second sleep."""
    with tempfile.TemporaryDirectory(prefix="fxcss-delayed-page-") as td:
        core.build_pages(Path(td))
        content = (Path(td) / "docs.html").read_bytes()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                time.sleep(5)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(content)

            def log_message(self, *args):
                pass

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                yield f"http://127.0.0.1:{server.server_port}/docs.html"
            finally:
                server.shutdown()
                worker.join(timeout=5)


def main():
    theme = Path(core.__file__).parent / "templates" / "starter"

    class BrokenFirstProcess(core.Session):
        """A real browser whose first page document is deliberately destroyed."""
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.browser_pids = []

        def _start_browser(self):
            super()._start_browser()
            self.browser_pids.append(self.m.script("return Services.appinfo.processID;"))
            if len(self.browser_pids) == 1:
                self.m.script('''
                    const win = Services.wm.getMostRecentWindow("navigator:browser");
                    win.gBrowser.selectedBrowser.remove();
                ''')

    with delayed_document() as url, BrokenFirstProcess(
            theme, core.find_firefox(),
            extra_prefs='user_pref("intl.l10n.pseudo", "bidi");\n') as session:
        assert len(session.browser_pids) == 2, session.browser_pids
        assert session.browser_pids[0] != session.browser_pids[1], session.browser_pids
        session.urls["docs.html"] = url
        # Reproduce the operation that failed indefinitely on Windows CI. The
        # original browser must never be navigated, even if it looks ready.
        session.m.script('''
            const win = Services.wm.getMostRecentWindow("navigator:browser");
            const browser = win.gBrowser.selectedBrowser;
            browser.setAttribute("fxcss-broken-startup", "true");
            browser.loadURI = () => {
                throw new TypeError("this._browser.frameLoader.remoteTab is null");
            };
        ''')
        command = session.m.command
        discarded = 0
        navigations = 0

        def lose_context_once(name, args=None):
            nonlocal discarded, navigations
            if name == "WebDriver:Navigate":
                navigations += 1
            if name == "WebDriver:Navigate" and discarded < 3:
                discarded += 1
                # Leave Marionette pointing at a genuinely discarded content
                # context, as a process switch can do during Windows startup.
                session.m.set_context("chrome")
                throwaway = core.Marionette._unwrap(command(
                    "WebDriver:NewWindow", {"type": "tab", "focus": True}))
                command("WebDriver:SwitchToWindow", {"handle": throwaway["handle"]})
                session.m.script('''
                    const gb = Services.wm.getMostRecentWindow("navigator:browser").gBrowser;
                    gb.removeTab(gb.selectedTab, {animate: false});
                ''')
                session.m.set_context("content")
            return command(name, args)

        session.m.command = lose_context_once
        started = time.monotonic()
        session.setup_window()
        assert time.monotonic() - started >= 5, "setup returned before the delayed page loaded"
        state_script = '''
            const win = Services.wm.getMostRecentWindow("navigator:browser");
            const gb = win.gBrowser;
            return {
                urls: Array.from(gb.tabs, t => t.linkedBrowser.currentURI.spec),
                ids: Array.from(gb.tabs, t => t.linkedPanel),
                pinned: Array.from(gb.tabs, t => t.pinned),
                selected: gb.selectedBrowser.currentURI.spec,
                direction: win.document.dir,
                broken: !!win.document.querySelector("[fxcss-broken-startup]")
            };
        '''
        state = session.m.script(state_script)
        urls = [session.urls[name] for name in ("start.html", "docs.html", "issues.html")]
        assert state["urls"] == urls, state
        assert state["pinned"] == [True, False, False], state
        assert state["selected"] == urls[1], state
        assert state["broken"] is False, state
        assert discarded == 3, "the persistent discarded-context regression was not exercised"
        assert navigations >= 6, "the discarded contexts did not trigger tab replacement"
        assert state["direction"] == "rtl", state
        session.m.set_context("content")
        try:
            actual = session.m.script("return document.location.href;")
            assert actual == urls[1], actual
            content = session.m.script("return [document.title, document.readyState];")
            assert content == ["Documentation", "complete"], content
        finally:
            session.m.set_context("chrome")
        session.setup_window()
        assert session.m.script(state_script) == state, "setup changed existing fixture tabs"
        # Reproduce stale overflow left by a long loading URL. The short Docs
        # label should return to RTL alignment; a genuinely narrow label must
        # retain its overflow treatment, including any theme-imposed width.
        session.m.script('''
            const gb = Services.wm.getMostRecentWindow("navigator:browser").gBrowser;
            gb.tabs[1].querySelector(".tab-label-container").setAttribute("textoverflow", "true");
            gb.tabs[2].querySelector(".tab-label-container").style.setProperty("max-width", "5px", "important");
        ''')
        overflow_script = '''
            const win = Services.wm.getMostRecentWindow("navigator:browser");
            return Array.from(win.gBrowser.tabs).slice(1).map(tab => {
                const label = tab.querySelector(".tab-label-container");
                return {overflow: label.hasAttribute("textoverflow"),
                        direction: win.getComputedStyle(label).direction,
                        width: label.clientWidth, scroll: label.scrollWidth};
            });
        '''
        stale, narrow = session.m.script(overflow_script)
        assert stale["overflow"] and stale["direction"] == "ltr", stale
        assert stale["scroll"] == stale["width"], stale
        assert narrow["scroll"] > narrow["width"] > 0, narrow
        with tempfile.TemporaryDirectory(prefix="fxcss-startup-shot-") as td:
            core._shot(session.m, Path(td), "rtl-labels", before=core.SYNC_TAB_LABEL_OVERFLOW)
        corrected, narrow = session.m.script(overflow_script)
        assert corrected["overflow"] is False and corrected["direction"] == "rtl", corrected
        assert narrow["overflow"] is True, narrow
        browser_pid = session.m.script("return Services.appinfo.processID;")
    if os.name == "nt":
        listing = subprocess.run(
            ["tasklist", "/FI", f"PID eq {browser_pid}", "/NH", "/FO", "CSV"],
            check=True, capture_output=True, text=True)
        assert not any(len(row) > 1 and row[1] == str(browser_pid)
                       for row in csv.reader(io.StringIO(listing.stdout))), (
                           "the Firefox browser outlived its session", listing.stdout)
    print("Startup restarted a browser missing its initial document, recovered discarded contexts, waited for a slow page, "
          "corrected stale RTL overflow without losing real overflow, and kept setup idempotent", flush=True)


if __name__ == "__main__":
    main()
