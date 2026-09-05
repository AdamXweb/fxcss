"""Test built distributions in clean environments, outside the source checkout.

No third-party test dependencies are required. The images stage installs the
wheel's declared extra. The upgrade stage starts with the latest PyPI release.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import venv
from pathlib import Path
from zipfile import ZipFile


def run(args, cwd, env, expected=0):
    result = subprocess.run([str(a) for a in args], cwd=cwd, env=env,
                            text=True, encoding="utf-8", errors="replace", capture_output=True)
    if result.returncode != expected:
        raise RuntimeError(f"{' '.join(map(str, args))} returned {result.returncode}, expected {expected}\n"
                           + result.stdout + result.stderr)
    return result.stdout + result.stderr


def installed_checks(stage, root, wheel):
    import fxcss
    from fxcss import install, scaffold
    package = Path(fxcss.__file__).resolve().parent
    assert Path(sys.prefix).resolve() in package.parents, package
    # Compare installed payload bytes with the exact artifact under review.
    if stage != "previous":
        with ZipFile(wheel) as archive:
            for name in archive.namelist():
                if name.startswith("fxcss/") and not name.endswith("/"):
                    assert package.joinpath(*name.split('/')[1:]).read_bytes() == archive.read(name), name
    binary = Path(sys.executable).parent / ("fxcss.exe" if os.name == "nt" else "fxcss")
    env = dict(os.environ, PYTHONUTF8="1")
    def cli(*args, expected=0):
        return run([binary, *args], root, env, expected)
    assert fxcss.__version__ in cli("--version")
    assert "install" in cli("--help")
    if stage == "base":
        import importlib.util
        assert importlib.util.find_spec("PIL") is None, "base environment unexpectedly contains Pillow"
        assert "needs Pillow" in cli("check", expected=2)
        cli("new", "generated theme café")
        theme = root / "generated theme café"
        cli("init", "--theme", theme, "--watch", "--showcase", "--previews")
        workflows = list((theme / ".github/workflows").glob("*.yml"))
        assert len(workflows) == 6, workflows
        assert all("__FXCSS_" not in p.read_text(encoding="utf-8") for p in workflows)
        assert (theme / "chrome/userChrome.css").is_file()
        assert (theme / "custom/accent-red.css").is_file()
    elif stage == "images":
        from PIL import Image
        for name, color in (("base", "grey"), ("head", "red")):
            folder = root / name
            folder.mkdir()
            Image.new("RGB", (16, 8), color).save(folder / "light-01-window.png")
        cli("compare", "--base", root / "base", "--head", root / "head", "--out", root / "diff")
        summary = json.loads((root / "diff/summary.json").read_text())
        assert summary["changed_views"], summary
    elif stage == "previous":
        theme, profile = root / "upgrade theme café", root / "profile café"
        scaffold.new_theme(theme)
        (profile / "chrome").mkdir(parents=True)
        (profile / "chrome/userChrome.css").write_text("/* original personal theme */\n")
        (profile / "user.js").write_text('user_pref("personal.setting", true);\n')
        install.install_theme(theme, profile, str(theme.resolve()))
        (profile / "chrome/my-notes.css").write_text("/* user addition */\n")
        second = root / "lifecycle profile"
        second.mkdir()
        install.install_theme(theme, second, str(theme.resolve()))
        (root / "previous-version.txt").write_text(fxcss.__version__)
    elif stage == "upgrade":
        theme, profile = root / "upgrade theme café", root / "profile café"
        previous = install.read_manifest(profile)
        assert previous, "could not read the released version's manifest"
        # Edited installed files survive removal by the new package.
        (profile / "chrome/userChrome.css").write_text("/* edited after installing */\n")
        removed = install.uninstall_theme(profile)
        assert "chrome/userChrome.css" in removed["kept"], removed
        assert (profile / "chrome/my-notes.css").read_text() == "/* user addition */\n"
        assert (profile / "user.js").read_text() == 'user_pref("personal.setting", true);\n'
        # A separate released install exercises upgrade -> rollback -> removal.
        second = root / "lifecycle profile"
        first = install.read_manifest(second)
        assert first, "released lifecycle manifest was not readable"
        upgraded = install.install_theme(theme, second, str(theme.resolve()),
                                          origin_backup=first["origin_backup"])
        install.rollback_to(second, upgraded["backup"])
        install.uninstall_theme(second)
        assert not (second / "chrome").exists()
        assert not (second / "user.js").exists()
    print(f"{stage}: fxcss {fxcss.__version__} from {package}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path)
    parser.add_argument("--stage", choices=("base", "images", "previous", "upgrade", "source"))
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--root", type=Path)
    args = parser.parse_args()
    if args.stage:
        installed_checks(args.stage, args.root, args.wheel)
        return
    if args.dist is None:
        parser.error("--dist is required")
    wheel, = args.dist.resolve().glob("*.whl")
    sdist, = args.dist.resolve().glob("*.tar.gz")
    script = Path(__file__).resolve()
    env = dict(os.environ, PYTHONUTF8="1", PIP_DISABLE_PIP_VERSION_CHECK="1")
    env.pop("PYTHONPATH", None)
    with tempfile.TemporaryDirectory(prefix="fxcss package café ") as td:
        root = Path(td)
        python = root / "env" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        venv.EnvBuilder(with_pip=True).create(root / "env")
        def pip(*options):
            return run([python, "-m", "pip", *options], root, env)
        def check(stage):
            print(run([python, script, "--stage", stage, "--root", root, "--wheel", wheel], root, env), flush=True)
        pip("install", "--no-deps", wheel)
        check("base")
        # Extras are resolved from artifact metadata, not a hand-maintained dependency list.
        pip("install", f"{wheel}[images]")
        check("images")
        pip("uninstall", "-y", "fxcss")
        pip("install", "--no-cache-dir", "fxcss")
        check("previous")
        pip("install", "--force-reinstall", "--no-deps", wheel)
        check("upgrade")
        # Building the source distribution must reproduce the installed package payload.
        pip("install", "--force-reinstall", "--no-deps", sdist)
        check("source")
        print("Verified wheel SHA256:", hashlib.sha256(wheel.read_bytes()).hexdigest(), flush=True)


if __name__ == "__main__":
    main()
