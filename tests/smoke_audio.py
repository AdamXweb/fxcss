"""Audio indicator CSS must render even when Firefox cannot open an audio device."""
import tempfile
import time
from pathlib import Path
from PIL import Image
from fxcss import core, scaffold


def main():
    with tempfile.TemporaryDirectory(prefix="fxcss-audio-state-") as td:
        root = Path(td)
        theme = root / "theme"
        scaffold.new_theme(theme)
        with (theme / "chrome/userChrome.css").open("a", encoding="utf-8") as sheet:
            sheet.write('''
.tabbrowser-tab[soundplaying]:not([muted]) .tab-content { background: #e6194b !important; }
.tabbrowser-tab[muted] .tab-content { background: #4363d8 !important; }
''')
        with core.Session(theme, core.find_firefox(),
                          extra_prefs='user_pref("media.cubeb.force_null_context", true);\n') as session:
            session.setup_window()
            background = session.m.script('''
                const win = Services.wm.getMostRecentWindow("navigator:browser");
                const el = win.document.querySelector("#urlbar-background, .urlbar-background");
                const value = win.getComputedStyle(win.document.documentElement)
                    .getPropertyValue("--demo-field").trim();
                const probe = win.document.createElement("div");
                probe.style.backgroundColor = value;
                return {actual: el && win.getComputedStyle(el).backgroundColor,
                        expected: probe.style.backgroundColor};
            ''')
            assert background["actual"] == background["expected"], background
            session.m.script(core.OPEN_AUDIO_TAB, [session.urls["audio.html"]])
            time.sleep(2.0)
            core._capture_audio_views(session.m, root)
            state = session.m.script(core.AUDIO_STATE)
            assert state == {"playing": False, "muted": False}, state
        for view, color in (("extra-04-audio", (230, 25, 75)), ("extra-05-muted", (67, 99, 216))):
            with Image.open(root / f"{view}.png") as image:
                image = image.convert("RGB")
                colors = dict((rgb, count) for count, rgb in image.getcolors(image.width * image.height))
                assert colors.get(color, 0) > 100, (view, color, colors.get(color, 0))
        print("Playing and muted CSS states painted correctly with the audio backend disabled; starter address-bar styles applied", flush=True)


if __name__ == "__main__":
    main()
