#!/usr/bin/env python3
"""Turn two sets of theme screenshots into before/after/diff images.

Reads a base/ and a head/ directory of PNGs produced by `fxcss shot`, compares
each matching view, and writes one stacked comparison image per view that
changed, plus a summary.json describing what changed and by how much.

Views that render identically are reported but not drawn -- the point is to put
the reviewer's eye straight on the difference. --views narrows the comparison
to matching captures, and --min-changed-percent reports a view whose change is
smaller than that share of its pixels without drawing it.

One change usually shows in many views: a restyled tab strip is in nearly
every full-window capture. So the changed regions are also grouped across
views into distinct changes, each drawn once (change-NN.png, cropped to the
change) with the views it appears in listed, and placed in the area of the UI
it is in (capture.CATEGORIES).

Usage:
    fxcss compare --base base/ --head head/ --out out/ --platform macos
"""

import argparse
import fnmatch
import json
import math
import re
import sys
from collections import deque
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont

from . import capture
from .capture import parse_view_patterns, select_views, title_for  # noqa: F401

# Width each panel is normalised to. GitHub renders comment images at roughly
# 800px, so this stays sharp without making the comment enormous.
PANEL_WIDTH = 960
LABEL_HEIGHT = 34
GAP = 10
# Per-channel difference below this is treated as noise (antialiasing, subpixel
# text rendering) rather than a real change.
NOISE_THRESHOLD = 24
HIGHLIGHT = (255, 0, 128)

# Changes are found on a grid of CELL-pixel squares; two changed squares at
# most REGION_GAP empty squares apart belong to the same region. A view with
# more separate regions than MAX_REGIONS is treated as one change across it
# (a page shifted by a pixel, say) rather than dozens of tiny ones.
CELL = 8
REGION_GAP = 1
MAX_REGIONS = 8
# Two views share a change when the changed pixels in that region mostly
# coincide. Colour is ignored on purpose: the same rule renders in each
# palette's own colours, and every view stays listed with its own image.
SAME_CHANGE = 0.5
CHANGE_IMAGE = re.compile(r"change-\d{2,3}\.png\Z")


def load_font(size):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # older Pillow: default font has no size argument
        return ImageFont.load_default()


def normalise(im, width=PANEL_WIDTH):
    """Scale down to the comment width; never enlarge a cropped surface."""
    im = im.convert("RGB")
    if im.width <= width:
        return im
    height = round(im.height * width / im.width)
    return im.resize((width, height), Image.LANCZOS)


def diff_stats(base, head):
    """Return (changed_pixel_count, total, mask) ignoring sub-threshold noise.

    Thresholds the per-channel *maximum* difference, not the luminance of the
    difference. Luminance averages the channels, so a chroma-only shift -- a
    toolbar tinted from blue-grey to peach, say -- can slide under the
    threshold while being plainly visible. Recolouring is exactly the kind of
    change a theme differ exists to catch.
    """
    delta = ImageChops.difference(base, head)
    channel_max = delta.split()[0]
    for channel in delta.split()[1:]:
        channel_max = ImageChops.lighter(channel_max, channel)
    mask = channel_max.point(lambda v: 255 if v >= NOISE_THRESHOLD else 0)
    changed = mask.histogram()[255]
    return changed, base.width * base.height, mask


def render_diff_panel(base, mask):
    """Faded base image with changed regions painted in a hot highlight."""
    faded = Image.blend(base, Image.new("RGB", base.size, (255, 255, 255)), 0.72)
    overlay = Image.new("RGB", base.size, HIGHLIGHT)
    # Grow the mask slightly so single-pixel changes stay visible once scaled.
    from PIL import ImageFilter
    grown = mask.filter(ImageFilter.MaxFilter(5))
    return Image.composite(overlay, faded, grown)


def label_bar(width, text, bg, fg=(255, 255, 255)):
    bar = Image.new("RGB", (width, LABEL_HEIGHT), bg)
    draw = ImageDraw.Draw(bar)
    draw.text((12, LABEL_HEIGHT // 2), text, fill=fg, font=load_font(20), anchor="lm")
    return bar


def stack(panels, width):
    """Vertically stack (label, image) pairs onto one canvas."""
    height = sum(LABEL_HEIGHT + im.height + GAP for _, im in panels) + GAP
    canvas = Image.new("RGB", (width, height), (240, 241, 243))
    y = GAP
    for (text, bg), im in panels:
        canvas.paste(label_bar(width, text, bg), (0, y))
        y += LABEL_HEIGHT
        canvas.paste(im, (0, y))
        y += im.height + GAP
    return canvas


def _pad(images):
    """Pad images to a common size with white, so a resized surface still diffs."""
    width = max(im.width for im in images)
    height = max(im.height for im in images)

    def pad(im):
        if im.size == (width, height):
            return im
        padded = Image.new("RGB", (width, height), (255, 255, 255))
        padded.paste(im, (0, 0))
        return padded

    return [pad(im) for im in images]


def compare_view(name, base_path, head_path, outdir, min_percent=0.0, _keep=None):
    base, head = _pad([normalise(Image.open(base_path)), normalise(Image.open(head_path))])

    changed, total, mask = diff_stats(base, head)
    pct = 100.0 * changed / total if total else 0.0

    result = {
        "view": name,
        "title": title_for(name)[0],
        "mode": title_for(name)[1],
        "category": capture.category_for(name),
        "changed_pixels": changed,
        "total_pixels": total,
        "percent": round(pct, 4),
        "image": None,
    }
    # Below the threshold the change is still counted, just not drawn.
    if changed == 0 or pct < min_percent:
        return result

    comparison = stack(
        [
            (("BEFORE  ·  baseline", (90, 96, 104)), base),
            (("AFTER  ·  current capture", (32, 113, 62)), head),
            ((f"CHANGED  ·  {pct:.2f}% of pixels", (176, 21, 92)), render_diff_panel(base, mask)),
        ],
        base.width,
    )
    out_path = outdir / f"{name}.png"
    comparison.save(out_path, optimize=True)
    result["image"] = out_path.name
    if _keep is not None:
        _keep[name] = (mask, base_path, head_path)
    return result


def change_regions(mask):
    """Boxes (x0, y0, x1, y1) around the separate changed regions of a mask."""
    cols, rows = -(-mask.width // CELL), -(-mask.height // CELL)
    grid = mask.reduce(CELL) if mask.width >= CELL and mask.height >= CELL else None
    if grid is None or grid.size != (cols, rows):
        padded = Image.new("L", (cols * CELL, rows * CELL), 0)
        padded.paste(mask, (0, 0))
        grid = padded.reduce(CELL)
    values = grid.tobytes()
    cells = {(i % cols, i // cols) for i, v in enumerate(values) if v}
    reach = REGION_GAP + 1
    regions, seen = [], set()
    for start in sorted(cells, key=lambda c: (c[1], c[0])):
        if start in seen:
            continue
        seen.add(start)
        queue, members = deque([start]), [start]
        while queue:
            x, y = queue.popleft()
            for dy in range(-reach, reach + 1):
                for dx in range(-reach, reach + 1):
                    cell = (x + dx, y + dy)
                    if cell in cells and cell not in seen:
                        seen.add(cell)
                        queue.append(cell)
                        members.append(cell)
        xs, ys = [c[0] for c in members], [c[1] for c in members]
        regions.append((min(xs) * CELL, min(ys) * CELL,
                        min(mask.width, (max(xs) + 1) * CELL), min(mask.height, (max(ys) + 1) * CELL)))
    if len(regions) > MAX_REGIONS:
        return [(min(r[0] for r in regions), min(r[1] for r in regions),
                 max(r[2] for r in regions), max(r[3] for r in regions))]
    return regions


def _area(box):
    return max(0, box[2] - box[0]) * max(0, box[3] - box[1])


def _box_iou(a, b):
    inter = _area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])))
    union = _area(a) + _area(b) - inter
    return inter / union if union else 0.0


def _same_change(change, mask, box):
    """Whether `box` of `mask` holds the same change as an existing group."""
    if change["mask"].width != mask.width or _box_iou(change["box"], box) < 0.3:
        return False
    union = (min(change["box"][0], box[0]), min(change["box"][1], box[1]),
             max(change["box"][2], box[2]), max(change["box"][3], box[3]))
    a, b = change["mask"].crop(union), mask.crop(union)
    both = ImageChops.multiply(a, b).histogram()[255]
    either = ImageChops.lighter(a, b).histogram()[255]
    return bool(either) and both / either >= SAME_CHANGE


def _load_layout(directory):
    path = Path(directory) / capture.LAYOUT
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def locate(name, box, frame, layout):
    """(category, landmark box) for a changed region.

    The landmark is the element in the view's recorded layout holding most of
    the region; nested ones (the address bar inside the toolbar) resolve to the
    smallest. A cropped surface has no layout: the whole image is its landmark.
    """
    entry = layout.get(name)
    whole = (0, 0, frame[0], frame[1])
    best = (capture.category_for(name), whole)
    if not isinstance(entry, dict) or not entry.get("width") or not isinstance(entry.get("boxes"), list):
        return best
    scale = frame[0] / entry["width"]
    best_key = None
    for item in entry["boxes"]:
        if not (isinstance(item, list) and len(item) == 5 and item[0] in capture.CATEGORIES
                and all(isinstance(v, (int, float)) for v in item[1:])):
            continue
        category, x, y, w, h = item
        landmark = (round(x * scale), round(y * scale), round((x + w) * scale), round((y + h) * scale))
        inside = _area((max(box[0], landmark[0]), max(box[1], landmark[1]),
                        min(box[2], landmark[2]), min(box[3], landmark[3]))) / max(1, _area(box))
        key = (round(inside, 2), -_area(landmark))
        if inside >= 0.3 and (best_key is None or key > best_key):
            best, best_key = (category, landmark), key
    return best


def _profile(image, axis):
    """Mean brightness per row (axis 1) or column (axis 0)."""
    size = (1, image.height) if axis else (image.width, 1)
    return image.resize(size, Image.BOX).tobytes()


def _best_offset(a, b, limit):
    """The offset of profile b that best matches a, preferring the smallest.

    A region uniform along the axis (a solid bar, a horizontal line) matches
    at every offset, and then it has not moved along that axis at all.
    """
    best, best_score = 0, None
    for offset in sorted(range(-limit, limit + 1), key=lambda o: (abs(o), o)):
        pairs = [(a[i], b[i - offset]) for i in range(max(0, offset), min(len(a), len(b) + offset))]
        if len(pairs) < max(8, len(a) // 2):
            continue
        score = sum(abs(x - y) for x, y in pairs) / len(pairs)
        if best_score is None or score < best_score - 0.01:
            best, best_score = offset, score
    return best


def detect_shift(base, head, box, limit=8):
    """(dx, dy) in pixels when the head region is the base region moved, else None.

    A toolbar a pixel taller pushes the whole page down in every view. As a
    pixel diff that is text edges everywhere, different in each view; as a
    move it is one change, whatever page the view happens to show.
    """
    b, h = base.crop(box).convert("L"), head.crop(box).convert("L")
    if min(b.size) < 16:
        return None
    dy = _best_offset(_profile(h, 1), _profile(b, 1), limit)
    dx = _best_offset(_profile(h, 0), _profile(b, 0), limit)
    if not dx and not dy:
        return None
    # Rows and columns at the region's edge are where the move starts or ends
    # (a border that went, a strip that grew), so only the inside must match.
    margin = 2 * max(abs(dx), abs(dy)) + 2
    inner = (margin, margin, b.width - margin, b.height - margin)
    if inner[2] <= inner[0] or inner[3] <= inner[1]:
        return None
    moved = b.crop((inner[0] - dx, inner[1] - dy, inner[2] - dx, inner[3] - dy))
    left = ImageChops.difference(h.crop(inner), moved).point(lambda v: 255 if v >= NOISE_THRESHOLD else 0)
    if left.histogram()[255] > 0.002 * moved.width * moved.height:
        return None
    return dx, dy


def _change_box(box, frame, pad=12, min_size=(360, 40)):
    """The change's box with some context around it, inside the frame."""
    x0, y0, x1, y1 = box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad
    for lo, hi, least, limit in ((0, 2, min_size[0], frame[0]), (1, 3, min_size[1], frame[1])):
        coords = [x0, y0, x1, y1]
        short = least - (coords[hi] - coords[lo])
        if short > 0:
            coords[lo] -= short // 2
            coords[hi] += short - short // 2
        if coords[lo] < 0:
            coords[hi] -= coords[lo]
            coords[lo] = 0
        if coords[hi] > limit:
            coords[lo] = max(0, coords[lo] - (coords[hi] - limit))
            coords[hi] = limit
        x0, y0, x1, y1 = coords
    return (int(x0), int(y0), int(x1), int(y1))


def render_change(base_path, head_path, frame, box, views, outdir, number, moved=None):
    """One picture of a change, cropped from the full-resolution captures.

    Small changes are shown at up to twice the scale of a full comparison so a
    one-pixel edge is visible, never more, so pictures stay comparable.
    """
    base, head = _pad([Image.open(base_path).convert("RGB"), Image.open(head_path).convert("RGB")])
    scale = base.width / frame[0] if frame[0] else 1.0
    x0, y0, x1, y1 = _change_box(box, frame)
    crop = (round(x0 * scale), round(y0 * scale), round(x1 * scale), round(y1 * scale))
    before, after = base.crop(crop), head.crop(crop)
    width = min(before.width, (x1 - x0) * 2, PANEL_WIDTH)
    if before.width > width:
        size = (width, max(1, round(before.height * width / before.width)))
        before, after = before.resize(size, Image.LANCZOS), after.resize(size, Image.LANCZOS)
    _, _, mask = diff_stats(before, after)
    label = f"{len(views)} view{'s' * (len(views) != 1)}"
    if moved:
        label = f"{moved}  ·  {label}"
    picture = stack(
        [
            (("BEFORE", (90, 96, 104)), before),
            (("AFTER", (32, 113, 62)), after),
            ((f"CHANGED  ·  {label}", (176, 21, 92)), render_diff_panel(before, mask)),
        ],
        before.width,
    )
    name = f"change-{number:02d}.png"
    picture.save(outdir / name, optimize=True)
    return name, picture.size


def _moved_words(shift, dpr):
    """'moved up 1px' for a (dx, dy) in device pixels."""
    words = []
    for amount, (minus, plus) in ((shift[1], ("up", "down")), (shift[0], ("left", "right"))):
        if amount:
            css = abs(amount) / dpr
            words.append(f"{minus if amount < 0 else plus} {css:g}px")
    return "moved " + " and ".join(words)


def group_changes(kept, layout, outdir, dpr=1):
    """Distinct changes across views, each drawn once, in category order.

    Within a view, changed regions in the same landmark are one change. Across
    views, a region joins an existing change when it is the same move, or when
    its changed pixels mostly coincide with that change's.
    """
    changes = []
    for name in sorted(kept, key=capture.view_order):
        mask, base_path, head_path = kept[name]
        frame = mask.size
        regions = {}
        for box in change_regions(mask):
            category, landmark = locate(name, box, frame, layout)
            if landmark in regions:
                merged = regions[landmark][1]
                box = (min(merged[0], box[0]), min(merged[1], box[1]),
                       max(merged[2], box[2]), max(merged[3], box[3]))
            regions[landmark] = (category, box)
        base = head = None
        for category, box in regions.values():
            if base is None:
                # Moves are measured at full resolution: a 1px move is a
                # fractional, resampled one once scaled to the panel width.
                base, head = _pad([Image.open(base_path).convert("RGB"),
                                   Image.open(head_path).convert("RGB")])
                device = base.width / frame[0] if frame[0] else 1.0
            shift = detect_shift(base, head, tuple(round(v * device) for v in box),
                                 limit=max(8, round(4 * device)))
            for change in changes:
                if shift is not None or change["shift"] is not None:
                    same = (shift is not None and change["shift"] is not None
                            and change["category"] == category and change["shift"] == shift)
                else:
                    same = _same_change(change, mask, box)
                if same:
                    if name not in change["views"]:
                        change["views"].append(name)
                    break
            else:
                changes.append({"view": name, "box": box, "mask": mask, "views": [name],
                                "category": category, "shift": shift})
    changes.sort(key=lambda c: (capture.CATEGORIES.index(c["category"]),
                                capture.view_order(c["view"]), c["box"][1], c["box"][0]))
    records = []
    for number, change in enumerate(changes, 1):
        _, base_path, head_path = kept[change["view"]]
        frame = change["mask"].size
        moved = _moved_words(change["shift"], dpr) if change["shift"] else None
        image, size = render_change(base_path, head_path, frame, change["box"],
                                    change["views"], outdir, number, moved)
        record = {"id": number, "category": change["category"], "view": change["view"],
                  "views": change["views"], "box": list(change["box"]), "frame": list(frame),
                  "image": image, "width": size[0], "height": size[1]}
        if moved:
            record["moved"] = moved
            record["shift"] = [round(v / dpr, 2) for v in change["shift"]]
        records.append(record)
    return records


def _captures(directory):
    if not directory.is_dir():
        raise ValueError(f"screenshot directory does not exist: {directory}")
    shots = {p.stem: p for p in sorted(directory.glob("*.png"))}
    if not shots:
        raise ValueError(f"no PNG screenshots in {directory}")
    for path in shots.values():
        with Image.open(path) as image:
            if image.format != "PNG":
                raise ValueError(f"not a PNG screenshot: {path}")
            image.verify()
    return shots


def _clear_previous(out):
    """Remove only files named by our previous report, preserving other files."""
    previous = out / "summary.json"
    if not previous.exists():
        return
    data = json.loads(previous.read_text(encoding="utf-8"))
    names = ({v["view"] for v in data["views"]} | set(data["only_in_head"])
             | set(data.get("only_in_base", [])))
    changes = {c.get("image") for c in data.get("changes", []) if isinstance(c, dict)}
    for image in changes:
        if isinstance(image, str) and CHANGE_IMAGE.fullmatch(image):
            path = out / image
            if path.is_file() or path.is_symlink():
                path.unlink()
    for name in names:
        if not isinstance(name, str) or Path(name).name != name or "\\" in name:
            raise ValueError("invalid filename in previous comparison summary")
    for name in names:
        for path in (out / f"{name}.png", out / "full" / f"{name}.png"):
            if path.is_file() or path.is_symlink():
                path.unlink()
    previous.unlink()


def run(base_dir: Path, head_dir: Path, out: Path, platform: str, views=None,
        min_changed_percent=0.0):
    try:
        return _run(base_dir.resolve(), head_dir.resolve(), out.resolve(), platform,
                    views, min_changed_percent)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"error: cannot compare screenshots: {exc}", file=sys.stderr)
        return 2


def _run(base_dir, head_dir, out, platform, views=None, min_changed_percent=0.0):
    for source in (base_dir, head_dir):
        if out == source or out in source.parents or source in out.parents:
            raise ValueError("comparison output must be separate from the input directories")
    minimum = min_changed_percent or 0.0
    if (type(minimum) not in (int, float) or not math.isfinite(minimum)
            or not 0 <= minimum <= 100):
        raise ValueError("--min-changed-percent must be a number from 0 to 100")
    patterns = parse_view_patterns(views)

    base_shots = _captures(base_dir)
    head_shots = _captures(head_dir)
    if patterns:
        # Matched against both sides, so selecting a view that one revision
        # lacks still reports it as added or missing.
        selected = set(select_views(set(base_shots) | set(head_shots), patterns))
        base_shots = {k: v for k, v in base_shots.items() if k in selected}
        head_shots = {k: v for k, v in head_shots.items() if k in selected}
    if (out / "full").is_symlink():
        raise ValueError("comparison full/ directory must not be a symlink")
    shared = sorted(set(base_shots) & set(head_shots))
    out.mkdir(parents=True, exist_ok=True)
    _clear_previous(out)

    summary = {
        "platform": platform,
        "views_filter": patterns,
        "min_changed_percent": minimum,
        "views": [],
        "only_in_base": sorted(set(base_shots) - set(head_shots)),
        "only_in_head": sorted(set(head_shots) - set(base_shots)),
    }

    kept = {}
    for name in shared:
        result = compare_view(name, base_shots[name], head_shots[name], out, minimum, kept)
        summary["views"].append(result)
        state = ("changed" if result["image"] else
                 "below threshold" if result["changed_pixels"] else "identical")
        print(f"  {name}: {state} ({result['percent']:.3f}%)", flush=True)

    # Always ship the head screenshots too, so a reviewer can see the whole UI
    # even when a change is subtle or when nothing differs at all.
    full_dir = out / "full"
    full_dir.mkdir(exist_ok=True)
    for name, path in head_shots.items():
        normalise(Image.open(path)).save(full_dir / f"{name}.png", optimize=True)

    summary["changed_views"] = [v for v in summary["views"] if v["image"]]
    try:
        dpr = float(json.loads((head_dir / "render-info.json").read_text(encoding="utf-8"))["dpr"]) or 1
    except (OSError, ValueError, KeyError, TypeError):
        dpr = 1
    summary["changes"] = group_changes(kept, _load_layout(head_dir), out, dpr)
    for view in summary["views"]:
        view["changes"] = [c["id"] for c in summary["changes"] if view["view"] in c["views"]]
    summary["categories"] = list(capture.CATEGORIES)
    # Views the head capture could not take in this environment (see
    # capture.VIEWS), so a reader knows they were not compared at all.
    try:
        coverage = json.loads((head_dir / capture.REPORT).read_text(encoding="utf-8"))
        summary["unsupported"] = sorted(
            name for name, record in coverage.get("views", {}).items()
            if isinstance(record, dict) and record.get("status") == "unsupported"
            and (not patterns or any(fnmatch.fnmatchcase(name, p) for p in patterns)))
    except (OSError, ValueError, AttributeError):
        summary["unsupported"] = []
    summary["below_threshold"] = [v for v in summary["views"]
                                  if v["changed_pixels"] and not v["image"]]
    # A view with no base-side counterpart -- a variant this PR adds, say -- is
    # a visual change too, even though there was nothing to diff it against.
    summary["any_change"] = bool(summary["changed_views"] or summary["only_in_head"]
                                 or summary["only_in_base"])

    src = head_dir / "render-info.json"
    if src.exists():
        summary["render_info"] = json.loads(src.read_text(encoding="utf-8"))

    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n{len(summary['changed_views'])} of {len(shared)} views changed on {platform}")
    if summary["changes"]:
        print(f"{len(summary['changes'])} distinct change"
              f"{'s' * (len(summary['changes']) != 1)}:")
        for change in summary["changes"]:
            print(f"  {change['image']}: {change['category']}"
                  + (f", {change['moved']}" if change.get("moved") else "")
                  + f", in {len(change['views'])} "
                  f"view{'s' * (len(change['views']) != 1)} ({', '.join(change['views'])})")
    if summary["below_threshold"]:
        print(f"{len(summary['below_threshold'])} more changed by less than "
              f"{minimum:g}% and are not drawn")
    if summary["only_in_head"]:
        print(f"{len(summary['only_in_head'])} more exist only on the head side")
    if summary["only_in_base"]:
        print("Missing from the head captures: " + ", ".join(summary["only_in_base"]))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", required=True, type=Path)
    ap.add_argument("--head", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--platform", required=True)
    ap.add_argument("--views", default=None)
    ap.add_argument("--min-changed-percent", type=float, default=0.0)
    args = ap.parse_args()
    return run(args.base, args.head, args.out, args.platform, args.views,
               args.min_changed_percent)


if __name__ == "__main__":
    sys.exit(main())
