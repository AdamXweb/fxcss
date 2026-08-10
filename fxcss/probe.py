#!/usr/bin/env python3
"""Identify browser UI elements and the rules that style them.

Two ways in:

* `pick` overlays a highlighter on the running browser -- hover any part of the
  UI and click to get a selector you can paste into your theme.
* `inspect` takes a selector you already have and reports what it matches.

Both finish by grepping the theme for rules that target the element, so the
answer is not just "what is this" but "where do I edit it".

Named probe.py rather than inspect.py so it cannot shadow the standard
library's inspect module for the sibling imports.
"""

import time

# Builds a selector from an element. Prefers an id, because userChrome rules
# overwhelmingly key off ids, then falls back to tag + classes, then to a short
# ancestor path so a nameless element is still addressable.
SELECTOR_HELPERS = """
const esc = (s) => (typeof CSS !== "undefined" && CSS.escape) ? CSS.escape(s) : s;
function classListOf(el) {
  const c = el.getAttribute && el.getAttribute("class");
  return c ? c.trim().split(/\\s+/).filter(Boolean) : [];
}
function selOf(el) {
  if (!el || el.nodeType !== 1) return "";
  if (el.id) return "#" + esc(el.id);
  let s = el.localName;
  const cls = classListOf(el);
  if (cls.length) s += "." + cls.map(esc).join(".");
  return s;
}
function pathOf(el) {
  const parts = [];
  let e = el;
  while (e && e.nodeType === 1 && parts.length < 6) {
    parts.unshift(selOf(e));
    if (e.id) break;
    e = e.parentElement;
  }
  return parts.join(" ");
}
function describe(el) {
  const r = el.getBoundingClientRect();
  const win = Services.wm.getMostRecentWindow("navigator:browser");
  const cs = win.getComputedStyle(el);
  const styles = {};
  for (const p of ["background-color","color","border-radius","box-shadow",
                   "list-style-image","font-size","padding","margin","opacity",
                   "height","width","display"]) {
    const v = cs.getPropertyValue(p);
    if (v && !["none","normal","auto","0px","rgba(0, 0, 0, 0)","1"].includes(v)) {
      styles[p] = v;
    }
  }
  return {
    selector: selOf(el), path: pathOf(el),
    tag: el.localName, id: el.id || null,
    classes: classListOf(el),
    attrs: [...el.attributes].map(a => a.name).filter(n => n !== "class" && n !== "id"),
    rect: {x: Math.round(r.x), y: Math.round(r.y),
           w: Math.round(r.width), h: Math.round(r.height)},
    styles,
  };
}
"""

START_PICKER = SELECTOR_HELPERS + """
const win = Services.wm.getMostRecentWindow("navigator:browser");
const doc = win.document;
if (win._fxcssStopPicker) { win._fxcssStopPicker(); }
win._fxcssPicked = null;

const HTML = "http://www.w3.org/1999/xhtml";
const box = doc.createElementNS(HTML, "div");
box.style.cssText = "position:fixed;z-index:2147483647;pointer-events:none;" +
  "border:2px solid #ff0080;background:rgba(255,0,128,.14);box-sizing:border-box;" +
  "border-radius:3px;display:none;";
const tag = doc.createElementNS(HTML, "div");
tag.style.cssText = "position:fixed;z-index:2147483647;pointer-events:none;" +
  "background:#ff0080;color:#fff;padding:2px 6px;border-radius:3px;display:none;" +
  "font:11px ui-monospace,Menlo,monospace;white-space:nowrap;";
doc.documentElement.appendChild(box);
doc.documentElement.appendChild(tag);

let current = null;
function onMove(ev) {
  const el = ev.originalTarget || ev.target;
  if (!el || el.nodeType !== 1 || el === box || el === tag) return;
  current = el;
  const r = el.getBoundingClientRect();
  box.style.display = "block";
  box.style.left = r.left + "px"; box.style.top = r.top + "px";
  box.style.width = r.width + "px"; box.style.height = r.height + "px";
  tag.style.display = "block";
  tag.textContent = selOf(el);
  tag.style.left = Math.max(0, r.left) + "px";
  tag.style.top = Math.max(0, r.top - 19) + "px";
}
function onPick(ev) {
  if (!current) return;
  ev.preventDefault(); ev.stopPropagation();
  win._fxcssPicked = describe(current);
}
function onKey(ev) {
  if (ev.key === "Escape") { ev.preventDefault(); win._fxcssStopPicker(); }
}

// Capture phase so a click lands on the highlighter rather than activating the
// button underneath it.
doc.addEventListener("mousemove", onMove, true);
doc.addEventListener("mousedown", onPick, true);
doc.addEventListener("click", onPick, true);
doc.addEventListener("keydown", onKey, true);

win._fxcssStopPicker = function () {
  doc.removeEventListener("mousemove", onMove, true);
  doc.removeEventListener("mousedown", onPick, true);
  doc.removeEventListener("click", onPick, true);
  doc.removeEventListener("keydown", onKey, true);
  box.remove(); tag.remove();
  win._fxcssStopPicker = null;
  win._fxcssPickerStopped = true;
};
win._fxcssPickerStopped = false;
return true;
"""

READ_PICK = """
const win = Services.wm.getMostRecentWindow("navigator:browser");
const p = win._fxcssPicked;
win._fxcssPicked = null;
return {picked: p, stopped: !!win._fxcssPickerStopped};
"""

STOP_PICKER = """
const win = Services.wm.getMostRecentWindow("navigator:browser");
if (win._fxcssStopPicker) { win._fxcssStopPicker(); }
return true;
"""

QUERY = SELECTOR_HELPERS + """
const win = Services.wm.getMostRecentWindow("navigator:browser");
const doc = win.document;
let nodes = [];
try { nodes = [...doc.querySelectorAll(arguments[0])]; }
catch (e) { return {error: String(e)}; }
return {count: nodes.length, matches: nodes.slice(0, 8).map(describe)};
"""


def _print_element(info, repo, references):
    print(f"\n  {info['tag']}  →  {info['selector']}")
    if info["path"] and info["path"] != info["selector"]:
        print(f"  path      {info['path']}")
    if info["classes"]:
        print(f"  classes   {' '.join(info['classes'])}")
    if info["attrs"]:
        print(f"  attrs     {' '.join(info['attrs'][:10])}")
    r = info["rect"]
    print(f"  box       {r['w']}×{r['h']} at ({r['x']}, {r['y']})"
          + ("   (not visible in this layout)" if not r["w"] or not r["h"] else ""))
    if info["styles"]:
        print("  styles")
        for k, v in info["styles"].items():
            print(f"    {k}: {v}")

    hits = references(repo, info["selector"])
    if hits:
        print(f"  styled by {len(hits)} rule{'s' if len(hits) != 1 else ''} in this theme")
        for h in hits[:8]:
            print(f"    {h['file']}:{h['line']}  {h['text'][:88]}")
        if len(hits) > 8:
            print(f"    … and {len(hits) - 8} more")
    else:
        print("  styled by no rules in this theme (Firefox defaults only)")


def inspect_selector(session, selector, repo, references):
    result = session.m.script(QUERY, [selector])
    if isinstance(result, dict) and result.get("error"):
        print(f"invalid selector: {result['error']}")
        return 2
    count = result["count"]
    if not count:
        print(f"no elements match {selector!r} in this Firefox")
        return 1
    print(f"{count} element{'s' if count != 1 else ''} match {selector!r}"
          + ("  (showing first 8)" if count > 8 else ""))
    for info in result["matches"]:
        _print_element(info, repo, references)
    return 0


def pick(session, repo, references):
    session.m.script(START_PICKER)
    print("\n  Move the mouse over the browser window; the element under the")
    print("  cursor is outlined. Click to capture it. Esc in the browser, or")
    print("  Ctrl-C here, to stop.\n")
    picked = 0
    try:
        while True:
            time.sleep(0.35)
            state = session.m.script(READ_PICK)
            if state.get("picked"):
                picked += 1
                _print_element(state["picked"], repo, references)
            if state.get("stopped"):
                print("\n  picker stopped.")
                break
    except KeyboardInterrupt:
        print("\n  stopping.")
        try:
            session.m.script(STOP_PICKER)
        except Exception:
            pass
    return 0
