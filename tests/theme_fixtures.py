"""Small original themes covering layouts used by Firefox CSS authors."""
from pathlib import Path


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
    }
    result = []
    for name, files in layouts.items():
        theme = Path(root) / name
        for relative, text in files.items():
            path = theme / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        result.append(theme)
    return result
