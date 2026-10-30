"""Small original themes covering layouts used by Firefox CSS authors."""
from pathlib import Path

# The computed `--fxcss-layout` each fixture theme produces once its chrome/
# and optional sheets are fully applied, in write_themes order.
EXPECTED_LAYOUTS = {
    "nested imports": "nested",
    "content café": "content",
    "stacked options": "last",
    "shipped custom chrome": "custom",
}

# What a theme that ships its own customChrome.css puts in it. fxcss writes an
# empty placeholder for themes that @import the file without shipping it; this
# content must never be replaced by that placeholder.
SHIPPED_CUSTOM_CHROME = ':root { --fxcss-custom-chrome: shipped; }\n'


def write_themes(root):
    layouts = {
        "nested imports": {
            "chrome/userChrome.css": '@import "parts/theme.css";\n',
            "chrome/parts/theme.css": '@import "../palette.css";\n@namespace xul url("http://www.mozilla.org/keymaster/gatekeeper/there.is.only.xul");\n:root { --fxcss-layout: nested; }\n',
            "chrome/palette.css": ':root { --fxcss-palette: loaded; }\n',
        },
        "content café": {
            "chrome/userChrome.css": ':root { --fxcss-layout: content; }\n',
            "chrome/userContent.css": ':root { --fxcss-content: loaded; }\n',
            "configuration/user.js": 'user_pref("fxcss.fixture", true);\n',
        },
        "stacked options": {
            "chrome/userChrome.css": ':root { --fxcss-layout: base; }\n',
            "optional/first.css": ':root { --fxcss-layout: first; }\n',
            "optional/last.css": ':root { --fxcss-layout: last; }\n',
        },
        "shipped custom chrome": {
            "chrome/userChrome.css": '@import "customChrome.css";\n:root { --fxcss-layout: custom; }\n',
            "chrome/customChrome.css": SHIPPED_CUSTOM_CHROME,
        },
    }
    assert list(layouts) == list(EXPECTED_LAYOUTS)
    result = []
    for name, files in layouts.items():
        theme = Path(root) / name
        for relative, text in files.items():
            path = theme / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        result.append(theme)
    return result
