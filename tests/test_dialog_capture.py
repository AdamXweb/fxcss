"""Run the modal capture script with fake Firefox objects, without a browser.

These cover the asynchronous snapshot lifecycle, not Firefox's rasterization;
the real three-run CI comparison checks that on every supported platform.
"""
import json
import shutil
import subprocess
import unittest

from fxcss import core


HARNESS = r"""
const fs = require("node:fs");
const {source, scale, failDraw} = JSON.parse(fs.readFileSync(0, "utf8"));
const state = {snapshots: [], draws: [], canvases: [], closed: [], cancelled: 0};
let finish;
const completed = new Promise(resolve => { finish = resolve; });
let dismiss;
const prompt = new Promise(resolve => { dismiss = resolve; });
const dialog = {getButton: () => ({click() { state.cancelled++; dismiss(); }})};
const frame = {
  contentDocument: {readyState: "complete", querySelector: () => dialog},
  getBoundingClientRect: () => ({x: 10, y: 20, width: 300, height: 150}),
};
const win = {
  devicePixelRatio: scale, innerWidth: 1264, innerHeight: 472,
  DOMRect: class {constructor(x, y, width, height) {
    Object.assign(this, {x, y, width, height});
  }},
  IOUtils: {async writeUTF8(path, text) { finish({path, ...JSON.parse(text)}); }},
  document: {
    querySelector: () => frame,
    createElementNS() {
      const canvas = {
        width: 0, height: 0,
        getContext() { return {
          drawImage(snapshot, ...coordinates) {
            state.draws.push({id: snapshot.id, coordinates, dismissed: state.cancelled});
            if (failDraw) { throw Error("cannot draw snapshot"); }
            canvas.pixels = snapshot.id === 0 ? "settling" : "stable";
          },
        }; },
        toDataURL() { return "data:image/png;base64," + canvas.pixels; },
      };
      state.canvases.push(canvas);
      return canvas;
    },
  },
  browsingContext: {currentWindowGlobal: {async drawSnapshot(rect, scale, background) {
    const id = state.snapshots.length;
    state.snapshots.push({rect, scale, background});
    // A real snapshot is asynchronous. Returning a promise instead of awaiting
    // it must not be mistaken for two identical captures.
    await new Promise(resolve => setImmediate(resolve));
    return {id, close() { state.closed.push(id); }};
  }}},
};
global.Services = {wm: {getMostRecentWindow: () => win}, prompt: {
  MODAL_TYPE_INTERNAL_WINDOW: 1, BUTTON_POS_0: 1, BUTTON_POS_1: 256,
  BUTTON_TITLE_IS_STRING: 127, BUTTON_POS_0_DEFAULT: 0,
  asyncConfirmEx: () => prompt,
}};
global.Ci = {nsITimer: {TYPE_ONE_SHOT: 0}};
global.Cc = {"@mozilla.org/timer;1": {createInstance: () => ({
  initWithCallback(callback) { setImmediate(callback); },
})}};
(async () => {
  const kicked = new Function(source)("dialog-handoff.json");
  const result = await completed;
  process.stdout.write(JSON.stringify({kicked, result, ...state}));
})().catch(error => { console.error(error); process.exitCode = 1; });
"""


@unittest.skipUnless(shutil.which("node"), "dialog script checks require Node")
class DialogSnapshotTests(unittest.TestCase):
    def run_script(self, scale=1, fail_draw=False):
        result = subprocess.run(
            [shutil.which("node"), "-e", HARNESS],
            input=json.dumps({"source": core.DIALOG_VIEW, "scale": scale,
                              "failDraw": fail_draw}),
            text=True, capture_output=True, check=True, timeout=10,
        )
        return json.loads(result.stdout)

    def test_waits_for_matching_snapshots_at_native_resolution(self):
        for scale in (1, 2):
            with self.subTest(scale=scale):
                state = self.run_script(scale)
                self.assertEqual(state["kicked"], "kicked")
                self.assertEqual(state["result"], {
                    "path": "dialog-handoff.json", "done": True,
                    "shot": "data:image/png;base64,stable", "box": [10, 20, 300, 150],
                })
                self.assertEqual(len(state["snapshots"]), 3)
                for snapshot, canvas in zip(state["snapshots"], state["canvases"]):
                    self.assertEqual(snapshot, {
                        "rect": {"x": 0, "y": 0, "width": 1264, "height": 472},
                        "scale": scale, "background": "#ffffff",
                    })
                    self.assertEqual((canvas["width"], canvas["height"]),
                                     (1264 * scale, 472 * scale))
                self.assertEqual(state["draws"], [
                    {"id": i, "coordinates": [0, 0], "dismissed": 0} for i in range(3)
                ])
                self.assertEqual(state["closed"], [0, 1, 2])
                self.assertEqual(state["cancelled"], 1)

    def test_draw_failure_releases_snapshot_and_dismisses_prompt(self):
        state = self.run_script(fail_draw=True)
        self.assertEqual(state["closed"], [0])
        self.assertEqual(state["cancelled"], 1)
        self.assertNotIn("shot", state["result"])
        self.assertIn("cannot draw snapshot", state["result"]["skip"])


if __name__ == "__main__":
    unittest.main()
