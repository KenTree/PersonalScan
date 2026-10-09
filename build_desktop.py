"""Build a standalone Apple Silicon PersonalScan.app without private user data."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def run(*command):
    subprocess.run(command, cwd=ROOT, check=True)


def main():
    assets = ROOT / "assets"
    iconset = assets / "PersonalScan.iconset"
    iconset.mkdir(parents=True, exist_ok=True)
    source = ROOT / "PersonalScanIcon.jpeg"
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            name = f"icon_{size}x{size}" + ("@2x" if scale == 2 else "") + ".png"
            run("sips", "-s", "format", "png", "-z", str(size * scale), str(size * scale), str(source), "--out", str(iconset / name))
    run("iconutil", "-c", "icns", str(iconset), "-o", str(assets / "PersonalScan.icns"))
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--onedir",
        "--name", "PersonalScan", "--target-architecture", "arm64",
        "--osx-bundle-identifier", "com.personalscan.desktop",
        "--icon", str(assets / "PersonalScan.icns"),
        "--add-data", f"{source}:.",
        "--hidden-import", "keyring.backends.macOS",
        "--collect-data", "googleapiclient",
        "--exclude-module", "PySide6.QtWebEngineCore",
        "--exclude-module", "PySide6.QtWebEngineWidgets", "desktop.py")
    print("Built:", ROOT / "dist" / "PersonalScan.app")


if __name__ == "__main__":
    main()
