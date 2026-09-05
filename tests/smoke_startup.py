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
    with delayed_document() as url, core.Session(theme, core.find_firefox()) as session:
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
                broken: !!win.document.querySelector("[fxcss-broken-startup]")
            };
        '''
        state = session.m.script(state_script)
        urls = [session.urls[name] for name in ("start.html", "docs.html", "issues.html")]
        assert state["urls"] == urls, state
        assert state["pinned"] == [True, False, False], state
        assert state["selected"] == urls[1], state
        assert state["broken"] is False, state
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
    print("Startup replaced an unusable browser, waited for a deliberately slow page, and repeated setup kept the same loaded tabs", flush=True)


if __name__ == "__main__":
    main()
