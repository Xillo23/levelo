"""Build Levelo: app folder (PyInstaller) + Windows installer (Inno Setup).

    python build.py            # app + installer  -> dist/
    python build.py --no-setup # app only

Requirements: pip install -r requirements.txt pyinstaller ; Inno Setup 6+ (ISCC.exe)
"""

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "src")
BUILD = os.path.join(ROOT, "build")
DIST = os.path.join(ROOT, "dist")
ASSETS = os.path.join(ROOT, "assets")
sys.path.insert(0, SRC)

from levelo import APP_NAME, __version__  # noqa: E402
from levelo.icons import app_icon, make_icon  # noqa: E402

PUBLISHER = "Levelo contributors"


def make_assets():
    """Icon + setup wizard images, generated from the same drawing as the app."""
    from PIL import Image, ImageDraw
    os.makedirs(ASSETS, exist_ok=True)
    app_icon(256).save(os.path.join(ASSETS, "icon.ico"),
                       sizes=[(s, s) for s in (16, 20, 24, 32, 40, 48, 64, 128, 256)])
    app_icon(512).save(os.path.join(ASSETS, "icon.png"))
    # small wizard image (top right), several DPI sizes
    for size in (55, 64, 83, 110, 138):
        img = Image.new("RGB", (size, size), (255, 255, 255))
        ic = app_icon(int(size * 0.9))
        img.paste(ic, ((size - ic.width) // 2, (size - ic.height) // 2), ic)
        img.save(os.path.join(BUILD, f"wizard-small-{size}.bmp"))
    # large wizard image (left side of welcome/finish pages)
    for w, h in ((164, 314), (246, 471), (328, 628)):
        img = Image.new("RGB", (w, h))
        d = ImageDraw.Draw(img)
        top, bot = (0, 120, 215), (0, 60, 120)
        for y in range(h):
            k = y / (h - 1)
            d.line([(0, y), (w, y)],
                   fill=tuple(int(a * (1 - k) + b * k) for a, b in zip(top, bot)))
        bars = make_icon(int(w * 0.6), fg=(255, 255, 255, 255))
        img.paste(bars, ((w - bars.width) // 2, int(h * 0.30)), bars)
        img.save(os.path.join(BUILD, f"wizard-large-{w}.bmp"))


def version_file():
    v = tuple(int(x) for x in __version__.split(".")) + (0,)
    path = os.path.join(BUILD, "version_info.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={v}, prodvers={v}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', '{PUBLISHER}'),
      StringStruct('FileDescription', '{APP_NAME}'),
      StringStruct('FileVersion', '{__version__}'),
      StringStruct('InternalName', 'Levelo'),
      StringStruct('LegalCopyright', 'GPL-3.0 - {PUBLISHER}'),
      StringStruct('OriginalFilename', 'Levelo.exe'),
      StringStruct('ProductName', '{APP_NAME}'),
      StringStruct('ProductVersion', '{__version__}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""")
    return path


def build_app():
    subprocess.check_call([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--name", "Levelo", "--windowed", "--onedir",
        "--icon", os.path.join(ASSETS, "icon.ico"),
        "--version-file", version_file(),
        "--paths", SRC,
        "--collect-data", "sv_ttk",
        "--hidden-import", "pystray._win32",
        "--exclude-module", "matplotlib", "--exclude-module", "scipy",
        "--workpath", os.path.join(BUILD, "pyi"),
        "--specpath", BUILD,
        "--distpath", os.path.join(BUILD, "app"),
        os.path.join(ROOT, "packaging", "entry.py"),
    ])


def find_iscc():
    candidates = [
        shutil.which("ISCC"), shutil.which("iscc"),
        os.path.expandvars(r"%USERPROFILE%\scoop\apps\inno-setup\current\ISCC.exe"),
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"),
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    sys.exit("ISCC.exe (Inno Setup) not found. Install it: https://jrsoftware.org/isdl.php")


def build_setup():
    subprocess.check_call([
        find_iscc(), f"/DAppVersion={__version__}", f"/DAppPublisher={PUBLISHER}",
        f"/DSourceDir={os.path.join(BUILD, 'app', 'Levelo')}",
        f"/DBuildDir={BUILD}", f"/DRootDir={ROOT}", f"/O{DIST}",
        os.path.join(ROOT, "packaging", "Levelo.iss"),
    ])


def main():
    os.makedirs(BUILD, exist_ok=True)
    os.makedirs(DIST, exist_ok=True)
    make_assets()
    build_app()
    # portable zip
    base = os.path.join(DIST, f"Levelo-{__version__}-portable")
    shutil.make_archive(base, "zip", os.path.join(BUILD, "app"), "Levelo")
    if "--no-setup" not in sys.argv:
        build_setup()
    print("\nDone ->", DIST)
    for f in sorted(os.listdir(DIST)):
        print("  ", f, f"{os.path.getsize(os.path.join(DIST, f)) / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
