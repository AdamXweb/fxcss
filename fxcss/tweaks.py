#!/usr/bin/env python3
"""Document a theme's install options with screenshots: `fxcss tweaks`.

Theme READMEs describe their optional stylesheets in prose -- accordions of
flags, "run install.sh -c -n -s" incantations -- and a user assembles their
preferred setup in their head. This renders the answer instead: the base theme,
then every optional stylesheet (and any combination worth blessing), each with
a before/after crop of the region it actually changes.

Output is a folder of PNGs plus TWEAKS.md, written to be committed: relative
image links, and a <details> accordion per tweak so a long option list stays
scannable on GitHub.

A tweak that changes nothing is reported as exactly that. The same silent-noop
problem audit catches for selectors applies to optional sheets, which rot at
least as fast.
"""

import io
import time
from pathlib import Path

from PIL import Image, ImageDraw

from . import core
from .compare import diff_stats

FULL_WIDTH = 1280       # committed full-window shots, repo-friendly
CROP_PAD = 30           # logical px of context around a diff region
MIN_CROP = 120          # a sliver of a crop is unreadable; grow to at least this

# Cropping to the bounding box of *every* changed pixel works for an option
# that moves one button, and fails for the ones that matter most: swap the tab
# close button and it moves on every tab, so the union spans the whole strip
# and the "crop" is the window again, shrunk until the thing that moved is a
# few pixels across. So the crop is built around one representative cluster of
# changes instead -- one tab, big enough to see -- with these bounds.
CELL = 8                # grid the diff is clustered on, in logical px
FOCUS_MIN = (340, 132)  # grow a tight crop out to at least this, for context
FOCUS_MAX = 0.62        # and never past this fraction of the window
PANEL_TARGET = 560      # each panel is scaled towards this width...
PANEL_MAX_SCALE = 3.0   # ...but never magnified past this, which only blurs


def _capture(session, outdir: Path, name: str):
    core._shot(session.m, outdir, name)
    return Image.open(outdir / f"{name}.png").convert("RGB")


def _shrink(image, width):
    if image.width <= width:
        return image
    return image.resize((width, round(image.height * width / image.width)),
                        Image.LANCZOS)


def _fit(image, width, max_scale=PANEL_MAX_SCALE):
    """Scale a panel towards `width`, up as well as down.

    The old code only ever shrank, so a tight crop of a 16px button arrived in
    a README at 16px: correctly cropped and still unreadable. Magnification is
    capped because past about 3x a chrome screenshot is just blur.
    """
    if image.width == 0:
        return image
    scale = min(width / image.width, max_scale)
    if abs(scale - 1.0) < 0.01:
        return image
    return image.resize((max(1, round(image.width * scale)),
                         max(1, round(image.height * scale))), Image.LANCZOS)


def _clusters(mask, cell=CELL):
    """Group changed pixels into clusters, as [(bbox, changed_pixel_count)].

    Connected components on a coarse grid rather than per pixel: the grid is
    ~1/64th the work, and neighbouring parts of one widget land in the same
    cell anyway. Pure Python on purpose -- a clustering dependency for this
    would be absurd, and the mask is small.
    """
    width, height = mask.size
    cols, rows = (width + cell - 1) // cell, (height + cell - 1) // cell
    counts = [0] * (cols * rows)
    data = mask.getdata()
    for index, value in enumerate(data):
        if value:
            counts[(index // width) // cell * cols + (index % width) // cell] += 1

    seen = [False] * (cols * rows)
    out = []
    for start in range(cols * rows):
        if not counts[start] or seen[start]:
            continue
        seen[start] = True
        stack, cells, total = [start], [], 0
        while stack:
            current = stack.pop()
            cells.append(current)
            total += counts[current]
            cy, cx = divmod(current, cols)
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    ny, nx = cy + dy, cx + dx
                    if 0 <= ny < rows and 0 <= nx < cols:
                        neighbour = ny * cols + nx
                        if counts[neighbour] and not seen[neighbour]:
                            seen[neighbour] = True
                            stack.append(neighbour)
        xs = [c % cols for c in cells]
        ys = [c // cols for c in cells]
        out.append(((min(xs) * cell, min(ys) * cell,
                     min(width, (max(xs) + 1) * cell),
                     min(height, (max(ys) + 1) * cell)), total))
    return out


def _grow(box, size, minimum):
    """Expand a box about its centre to at least `minimum`, inside `size`."""
    left, top, right, bottom = box
    for axis, least in enumerate(minimum):
        low, high = (left, right) if axis == 0 else (top, bottom)
        if high - low < least:
            centre = (low + high) // 2
            low, high = centre - least // 2, centre + least // 2
            if low < 0:
                low, high = 0, min(size[axis], least)
            if high > size[axis]:
                low, high = max(0, size[axis] - least), size[axis]
        if axis == 0:
            left, right = low, high
        else:
            top, bottom = low, high
    return (max(0, left), max(0, top), min(size[0], right), min(size[1], bottom))


def focus_box(mask, size):
    """The region to crop to, given a diff mask. None when nothing changed.

    Takes the busiest cluster of changes rather than the union of all of them,
    then grows it for context and pulls in any other cluster that lands inside
    -- so a change repeated across every tab shows one tab, legibly, instead of
    the whole strip shrunk to nothing.
    """
    clusters = _clusters(mask)
    if not clusters:
        return None
    box, _ = max(clusters, key=lambda item: item[1])
    box = _grow(box, size, FOCUS_MIN)
    for other, _ in clusters:
        if (other[0] < box[2] and other[2] > box[0]
                and other[1] < box[3] and other[3] > box[1]):
            box = (min(box[0], other[0]), min(box[1], other[1]),
                   max(box[2], other[2]), max(box[3], other[3]))
    box = _grow(box, size, FOCUS_MIN)
    return _grow(box, size, (1, 1))[:2] + (
        min(size[0], box[0] + min(box[2] - box[0], round(size[0] * FOCUS_MAX))),
        min(size[1], box[1] + min(box[3] - box[1], round(size[1] * FOCUS_MAX))))


def _crop_box(bbox, size, pad, minimum):
    left, top, right, bottom = bbox
    if right - left < minimum:
        centre = (left + right) // 2
        left, right = centre - minimum // 2, centre + minimum // 2
    if bottom - top < minimum:
        centre = (top + bottom) // 2
        top, bottom = centre - minimum // 2, centre + minimum // 2
    return (max(0, left - pad), max(0, top - pad),
            min(size[0], right + pad), min(size[1], bottom + pad))


def _before_after(base, after, mask, out_path: Path):
    """A labelled side-by-side crop of the region that changed, made legible."""
    box = focus_box(mask, base.size)
    if box is None:
        box = _crop_box(mask.getbbox(), base.size, CROP_PAD * 2, MIN_CROP * 2)
    left = _fit(base.crop(box), PANEL_TARGET)
    right = _fit(after.crop(box), PANEL_TARGET)

    label_h = 26
    gutter = 12
    canvas = Image.new("RGB", (left.width + right.width + gutter,
                               left.height + label_h), (246, 246, 248))
    draw = ImageDraw.Draw(canvas)
    draw.text((6, 6), "before", fill=(90, 90, 96))
    draw.text((left.width + gutter + 6, 6), "after", fill=(20, 110, 60))
    canvas.paste(left, (0, label_h))
    canvas.paste(right, (left.width + gutter, label_h))
    canvas.save(out_path, optimize=True)


def build(session, theme: Path, outdir: Path, variants, flags):
    """Capture base + each tweak, measure each against base, emit TWEAKS.md."""
    outdir.mkdir(parents=True, exist_ok=True)
    session.setup_window()
    time.sleep(2.0)

    base = _capture(session, outdir, "base")
    (outdir / "base.png").unlink()
    _shrink(base, FULL_WIDTH).save(outdir / "base.png", optimize=True)

    entries = []
    captures = {}
    for slug, sheets in sorted(variants.items()):
        for sheet in sheets:
            session.m.script(core.LOAD_VARIANT_SHEET, [sheet.resolve().as_uri()])
        time.sleep(1.5)
        after = _capture(session, outdir, slug)
        session.m.script(core.UNLOAD_VARIANT_SHEETS)
        time.sleep(0.8)

        changed, total, mask = diff_stats(base, after)
        pct = 100.0 * changed / total if total else 0.0
        entry = {"slug": slug, "sheets": sheets, "percent": pct, "image": None}
        if changed:
            _before_after(base, after, mask, outdir / f"{slug}-diff.png")
            entry["image"] = f"{slug}-diff.png"
            _shrink(after, FULL_WIDTH).save(outdir / f"{slug}.png", optimize=True)
        else:
            (outdir / f"{slug}.png").unlink()
        entries.append(entry)
        captures[slug] = after
        state = f"{pct:.2f}% of the chrome" if changed else "nothing on this Firefox"
        print(f"  {slug}: changes {state}", flush=True)

    for entry in entries:
        matched = combo_verdict(entry, captures)
        if matched:
            entry["same_as"] = matched
            print(f"  {entry['slug']}: renders identically to {matched} alone "
                  "— the other sheet(s) have no effect in this combination",
                  flush=True)

    info = session.info()
    markdown = render_markdown(theme, entries, flags, info["version"])
    (outdir / "TWEAKS.md").write_text(markdown, encoding="utf-8")
    return entries


def combo_verdict(entry, captures):
    """The single option a combo capture is pixel-identical to, or None.

    Static analysis can say two sheets set the same declarations; it cannot
    see two sheets that fight over the same pixels through different rules --
    WhiteSur's tabs-swapclose and windows-swapclose both move the tab close
    button while sharing no declarations at all. The rendered images settle
    it: if `a+b` is identical to `b` alone, then `a` did nothing in that
    combination, and that is a fact about pixels rather than a judgement.
    Identical means what it means everywhere else in fxcss -- zero pixels
    changed above diff_stats' noise threshold.

    Only combinations are judged, and only against their own constituents:
    `a+b` matching some unrelated option c would be a coincidence worth
    nothing. Returns the constituent's slug, or None when the combo really is
    more than any one of its parts.
    """
    parts = entry["slug"].split("+")
    if len(parts) < 2 or entry["slug"] not in captures:
        return None
    combo = captures[entry["slug"]]
    for part in parts:
        single = captures.get(part)
        if single is None or single.size != combo.size:
            continue
        changed, _, _ = diff_stats(single, combo)
        if changed == 0:
            return part
    return None


def render_markdown(theme: Path, entries, flags, firefox_version):
    """The committable document. Pure, so it is unit-testable."""
    lines = [
        "# Tweaks and install options",
        "",
        f"Every optional stylesheet this theme ships, rendered against Firefox "
        f"{firefox_version} and compared with the base setup. Generated by "
        f"[fxcss](https://github.com/AdamXweb/fxcss) — regenerate with "
        f"`fxcss tweaks` after adding an option.",
        "",
        "## Base",
        "",
        "![the theme with no options enabled](base.png)",
        "",
        "## Options",
        "",
    ]

    for entry in entries:
        slug = entry["slug"]
        title = slug.replace("+", " + ")
        if entry["image"]:
            summary = f"<b>{title}</b> — changes {entry['percent']:.2f}% of the chrome"
        else:
            summary = (f"<b>{title}</b> — changes nothing on current Firefox, "
                       f"possibly stale")
        lines.append("<details>")
        lines.append(f"<summary>{summary}</summary>")
        lines.append("")
        if entry["image"]:
            lines.append(f"![before and after of {title}]({entry['image']})")
            lines.append("")
            lines.append(f"Full window: [{slug}.png]({slug}.png)")
            lines.append("")
        if entry.get("same_as"):
            others = " + ".join(part for part in slug.split("+")
                                if part != entry["same_as"]) or "the rest"
            lines.append(f"**Not a real combination on this Firefox:** it "
                         f"renders pixel-identically to `{entry['same_as']}` "
                         f"alone — `{others}` has no effect here.")
            lines.append("")
        enable = ", ".join(f"`{sheet_path_for_doc(theme, s)}`" for s in entry["sheets"])
        lines.append(f"Enable by copying {enable} into your profile's `chrome/` "
                     f"folder (or with the installer flags below, if the theme "
                     f"documents one for it).")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    if flags:
        lines += [
            "## Install script options",
            "",
            "As documented by the theme's own README:",
            "",
            "| flag | effect |",
            "| --- | --- |",
        ]
        lines += [f"| `{f['flag']}` | {f['text']} |" for f in flags]
        lines.append("")
    return "\n".join(lines)


def sheet_path_for_doc(theme: Path, sheet: Path):
    try:
        return str(sheet.relative_to(theme))
    except ValueError:
        return sheet.name


def readme_flags(theme: Path):
    """The theme README's documented install flags, if any."""
    from .fetch import FLAG_LINE
    for name in ("README.md", "readme.md", "README", "README.rst"):
        path = theme / name
        if not path.is_file():
            continue
        found = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            match = FLAG_LINE.match(line)
            if match:
                found.append({"flag": match.group(1), "text": match.group(2)})
        return found[:20]
    return []
