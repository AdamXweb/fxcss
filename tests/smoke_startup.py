"""Fixture setup must replace an unusable initial browser and stay idempotent."""
from pathlib import Path

from fxcss import core


def main():
    theme = Path(core.__file__).parent / "templates" / "starter"
    with core.Session(theme, core.find_firefox()) as session:
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
        session.setup_window()
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
        session.setup_window()
        assert session.m.script(state_script) == state, "setup changed existing fixture tabs"
    print("Startup replaced an unusable browser and repeated setup kept the same tabs", flush=True)


if __name__ == "__main__":
    main()
