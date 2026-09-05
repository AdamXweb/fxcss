"""Fixture setup must replace an unusable initial browser and stay idempotent."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
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
    with delayed_document() as url, core.Session(
            theme, core.find_firefox(),
            extra_prefs='user_pref("intl.l10n.pseudo", "bidi");\n') as session:
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
        discarded = False
        navigations = 0

        def lose_context_once(name, args=None):
            nonlocal discarded, navigations
            if name == "WebDriver:Navigate":
                navigations += 1
            if name == "WebDriver:Navigate" and not discarded:
                discarded = True
                # Leave Marionette pointing at a genuinely discarded content
                # context, as a process switch can do during Windows startup.
                session.m.set_context("chrome")
                throwaway = core.Marionette._unwrap(command(
                    "WebDriver:NewWindow", {"type": "tab"}))
                command("WebDriver:SwitchToWindow", {"handle": throwaway["handle"]})
                session.m.script('''
                    const gb = Services.wm.getMostRecentWindow("navigator:browser").gBrowser;
                    gb.removeTab(gb.selectedTab, {animate: false});
                ''')
                session.m.set_context("content")
            return command(name, args)

        session.m.command = lose_context_once
        wait_for_pages = session._wait_for_fixture_pages

        def late_label_direction():
            wait_for_pages()
            # A title arriving before pseudo-localisation finishes retains LTR
            # alignment even after the chrome switches to RTL.
            session.m.script('''
                const win = Services.wm.getMostRecentWindow("navigator:browser");
                for (const tab of win.gBrowser.tabs) {
                    tab.removeAttribute("labelendaligned");
                }
            ''')

        session._wait_for_fixture_pages = late_label_direction
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
                labels: Array.from(gb.tabs, t => [t.label,
                    t.getAttribute("labeldirection"), t.hasAttribute("labelendaligned")]),
                broken: !!win.document.querySelector("[fxcss-broken-startup]")
            };
        '''
        state = session.m.script(state_script)
        urls = [session.urls[name] for name in ("start.html", "docs.html", "issues.html")]
        assert state["urls"] == urls, state
        assert state["pinned"] == [True, False, False], state
        assert state["selected"] == urls[1], state
        assert state["broken"] is False, state
        assert discarded, "the discarded-context regression was not exercised"
        assert navigations >= 4, "the discarded context did not trigger recovery"
        assert state["direction"] == "rtl", state
        assert state["labels"] == [[title, "ltr", True] for title in
                                   ("Start", "Documentation", "Issue tracker")], state
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
    print("Startup recovered unusable and discarded contexts, waited for a slow page, "
          "finalised RTL labels, and repeated setup kept the same loaded tabs", flush=True)


if __name__ == "__main__":
    main()
