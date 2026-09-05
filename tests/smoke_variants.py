"""Check optional-sheet loading in a real Firefox, using disposable profiles."""

import tempfile
import time
from pathlib import Path

from fxcss import core, fetch, install


def main():
    firefox = core.find_firefox()
    with tempfile.TemporaryDirectory(prefix="fxcss-variant-smoke-") as td:
        root = Path(td)
        theme = root / "theme"
        chrome = theme / "chrome"
        (chrome / "nested").mkdir(parents=True)
        (chrome / "assets").mkdir()
        (theme / "custom").mkdir()
        (chrome / "nested" / "base.css").write_text(
            ":root { --fxcss-smoke-relative: loaded; }", encoding="utf-8")
        (chrome / "assets" / "probe.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>',
            encoding="utf-8")
        (chrome / "userChrome.css").write_text(
            '@charset "UTF-8";\n@import "nested/base.css";\n'
            '@namespace xul url("http://www.mozilla.org/keymaster/gatekeeper/there.is.only.xul");\n'
            ':root { --fxcss-smoke-color: red; --fxcss-smoke-order: base;\n'
            'background-image: url("assets/probe.svg") !important; }\n',
            encoding="utf-8")
        first, last = (theme / "custom" / name
                       for name in ("blue option.css", "last.css"))
        first.write_text(":root { --fxcss-smoke-color: blue; "
                         "--fxcss-smoke-order: first; }", encoding="utf-8")
        last.write_text(":root { --fxcss-smoke-order: last; }", encoding="utf-8")
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
                expected_image = (session.profile / "chrome" / "assets" / "probe.svg").as_uri()
                for _ in range(40):
                    values = session.m.script("""
                        const win = Services.wm.getMostRecentWindow("navigator:browser");
                        const style = win.getComputedStyle(win.document.documentElement);
                        return ["--fxcss-smoke-color", "--fxcss-smoke-order",
                                "--fxcss-smoke-relative", "background-image"]
                            .map(name => style.getPropertyValue(name).trim());
                    """)
                    if values[:3] == ["blue", "last", "loaded"]:
                        break
                    time.sleep(0.25)
                assert values[:3] == ["blue", "last", "loaded"], (mode, values)
                assert expected_image in values[3], (mode, values[3], expected_image)
                print(f"{mode}: options override the base in order; relative imports and URLs resolve")


if __name__ == "__main__":
    main()
