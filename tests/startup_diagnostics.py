"""Temporary Windows startup investigation; runs only in its branch workflow."""
from contextlib import ExitStack
import json
from pathlib import Path
import subprocess
from unittest.mock import patch

from fxcss import core, omni
from tests import smoke_themes


STATE = """
const win = Services.wm.getMostRecentWindow("navigator:browser");
return {
  pid: Services.appinfo.processID,
  version: Services.appinfo.version,
  startup: win.gBrowserInit.delayedStartupFinished,
  tabs: Array.from(win.gBrowser.tabs, tab => {
    const b = tab.linkedBrowser;
    const bc = b?.browsingContext;
    return {id: tab.linkedPanel, selected: tab.selected,
      uri: b?.currentURI?.spec, connected: b?.isConnected,
      remote: b?.remoteType, crashed: tab.hasAttribute("crashed"),
      contextId: bc?.id, discarded: bc?.isDiscarded,
      contextURI: bc?.currentURI?.spec,
      documentURI: bc?.currentWindowGlobal?.documentURI?.spec,
      childPid: b?.frameLoader?.remoteTab?.osPid};
  })
};
"""


def main():
    dest = Path("out/startup-diagnostics")
    dest.mkdir(parents=True, exist_ok=True)
    firefox = core.find_firefox()
    for pack in omni.pack_paths(firefox):
        for name, data in omni.read_entries(pack):
            if name.endswith(("marionette/driver.sys.mjs", "marionette/browser.sys.mjs",
                              "webdriver/Session.sys.mjs", "NavigableManager.sys.mjs")):
                (dest / ("source-" + Path(name).name)).write_bytes(data)

    original_popen = subprocess.Popen
    original_setup = core.Session.setup_window
    processes = []
    with ExitStack() as stack:
        def popen(args, **kwargs):
            log = stack.enter_context((dest / f"firefox-{len(processes)}.log").open("wb"))
            kwargs.update(stdout=log, stderr=subprocess.STDOUT)
            process = original_popen(args, **kwargs)
            processes.append(process.pid)
            print("START", process.pid, args, flush=True)
            return process

        def setup(session, *args, **kwargs):
            command = session.m.command
            print("BEFORE SETUP", json.dumps(session.m.script(STATE)), flush=True)

            def trace(name, params=None):
                try:
                    result = command(name, params)
                except core.MarionetteError:
                    if name == "WebDriver:Navigate":
                        command("Marionette:SetContext", {"value": "chrome"})
                        try:
                            state = session.m.script(STATE)
                            print("FAILED NAVIGATION", params, json.dumps(state), flush=True)
                        finally:
                            command("Marionette:SetContext", {"value": "content"})
                    raise
                if name in ("WebDriver:NewWindow", "WebDriver:SwitchToWindow", "WebDriver:Navigate"):
                    print(name, params, result, flush=True)
                return result

            session.m.command = trace
            try:
                return original_setup(session, *args, **kwargs)
            finally:
                session.m.command = command

        stack.enter_context(patch.object(core.subprocess, "Popen", side_effect=popen))
        stack.enter_context(patch.object(core.Session, "setup_window", setup))
        for attempt in range(8):
            print("ROUND", attempt + 1, flush=True)
            smoke_themes.main()


if __name__ == "__main__":
    main()
