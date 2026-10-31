"""Check optional-sheet loading in a real Firefox, using disposable profiles."""

import re
import tempfile
import time
from pathlib import Path

from fxcss import core, fetch, install

# Distinct sizes tell the two fixture images apart once decoded: a URL in a
# computed style only proves the reference resolved, not that the file at the
# other end is the one the theme shipped.
BASE_IMAGE_SIZE = (1, 1)
OPTION_IMAGE_SIZE = (7, 3)

DECODE_IMAGE = """
const done = arguments[arguments.length - 1];
const win = Services.wm.getMostRecentWindow("navigator:browser");
const img = new win.Image();
img.onload = () => done([img.naturalWidth, img.naturalHeight]);
img.onerror = () => done("error: the image did not load");
img.src = arguments[0];
"""

COMPUTED_STYLE = """
const win = Services.wm.getMostRecentWindow("navigator:browser");
const style = win.getComputedStyle(win.document.documentElement);
return arguments[0].map(name => style.getPropertyValue(name).trim());
"""


def _svg(width, height):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
            f'height="{height}"/>')


def write_variant_theme(root):
    """A theme whose optional sheet reaches outside chrome/ for its own files.

    The base sheet @imports a nested sheet and refers to an image, both inside
    chrome/. The first optional sheet lives in custom/ beside chrome/, the way
    themes ship optional components, and @imports a sheet of its own whose
    rule refers to an image under custom/. `fxcss try` loads the optional
    sheet in place, so those references resolve against the theme; `fxcss
    install` must carry the nested sheet and image into the profile.

    Returns the theme and the optional sheets in cascade order.
    """
    root = Path(root)
    theme = root / "theme"
    chrome = theme / "chrome"
    custom = theme / "custom"
    (chrome / "nested").mkdir(parents=True)
    (chrome / "assets").mkdir()
    (custom / "parts").mkdir(parents=True)
    (custom / "assets").mkdir()
    (chrome / "nested" / "base.css").write_text(
        ":root { --fxcss-smoke-relative: loaded; }", encoding="utf-8")
    (chrome / "assets" / "probe.svg").write_text(_svg(*BASE_IMAGE_SIZE), encoding="utf-8")
    (chrome / "userChrome.css").write_text(
        '@charset "UTF-8";\n@import "nested/base.css";\n'
        '@namespace xul url("http://www.mozilla.org/keymaster/gatekeeper/there.is.only.xul");\n'
        ':root { --fxcss-smoke-color: red; --fxcss-smoke-order: base;\n'
        'background-image: url("assets/probe.svg") !important; }\n',
        encoding="utf-8")
    first, last = (custom / name for name in ("blue option.css", "last.css"))
    first.write_text('@import "parts/option extra.css";\n'
                     ":root { --fxcss-smoke-color: blue; "
                     "--fxcss-smoke-order: first; }\n", encoding="utf-8")
    (custom / "parts" / "option extra.css").write_text(
        ":root { --fxcss-smoke-nested: loaded;\n"
        'list-style-image: url("../assets/option.svg") !important; }\n',
        encoding="utf-8")
    (custom / "assets" / "option.svg").write_text(_svg(*OPTION_IMAGE_SIZE), encoding="utf-8")
    last.write_text(":root { --fxcss-smoke-order: last; }", encoding="utf-8")
    return theme, [first, last]


def image_url(computed):
    """The URL inside a computed `url("...")` image value."""
    match = re.fullmatch(r'url\("(.*)"\)', computed)
    assert match, computed
    return match.group(1)


def check_image(session, computed, expected_uri, expected_size):
    url = image_url(computed)
    assert url == expected_uri, (url, expected_uri)
    size = session.m.async_script(DECODE_IMAGE, [url])
    assert size == list(expected_size), (url, size, expected_size)


def main():
    firefox = core.find_firefox()
    with tempfile.TemporaryDirectory(prefix="fxcss-variant-smoke-") as td:
        root = Path(td)
        theme, (first, last) = write_variant_theme(root)
        for mode in ("install", "try"):
            if mode == "install":
                profile = root / "installed"
                profile.mkdir()
                install.install_theme(theme, profile, "smoke", sheets=[first, last])
                session = core.Session(profile, firefox)
            else:
                session = core.Session(theme, firefox)
                fetch.apply_variants(session.profile, [first, last])
            with session:
                session_chrome = session.profile / "chrome"
                expected_base = (session_chrome / "assets" / "probe.svg").as_uri()
                if mode == "install":
                    # Multiple options can live in separate trees to avoid
                    # same-name asset collisions. Dependencies must remain
                    # beside the installed sheet, whatever its allocated path.
                    first_copy, = session_chrome.rglob(first.name)
                    option_image = first_copy.parent / "assets" / "option.svg"
                    assert option_image.is_file(), sorted(session_chrome.rglob("*"))
                    expected_option = option_image.as_uri()
                else:
                    expected_option = (theme / "custom" / "assets" / "option.svg").resolve().as_uri()
                    assert not (session_chrome / "custom").exists(), "try copied optional files"
                names = ["--fxcss-smoke-color", "--fxcss-smoke-order",
                         "--fxcss-smoke-relative", "--fxcss-smoke-nested",
                         "background-image", "list-style-image"]
                loaded = ["blue", "last", "loaded", "loaded"]
                for _ in range(40):
                    values = session.m.script(COMPUTED_STYLE, [names])
                    if values[:4] == loaded:
                        break
                    time.sleep(0.25)
                assert values[:4] == loaded, (mode, dict(zip(names, values)))
                check_image(session, values[4], expected_base, BASE_IMAGE_SIZE)
                check_image(session, values[5], expected_option, OPTION_IMAGE_SIZE)
                print(f"{mode}: options override the base in order; nested imports and "
                      "image references resolve and decode inside and outside chrome/", flush=True)


if __name__ == "__main__":
    main()
