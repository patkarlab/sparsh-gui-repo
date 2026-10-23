"""
build.py
Master build script for SPARSH ONT Run Manager.

Run this on the target platform:
    python build.py

It will:
  1. Install PyInstaller if needed
  2. Run PyInstaller with sparsh_gui.spec
  3. Create a platform-native installer:
       Windows  → Inno Setup .exe  (requires Inno Setup installed)
       macOS    → .dmg disk image  (requires create-dmg: brew install create-dmg)
       Linux    → .tar.gz + .desktop file  (no extra tools needed)
"""

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT       = Path(__file__).parent.resolve()
DIST_DIR   = ROOT / "dist"
BUILD_DIR  = ROOT / "build_cache"
APP_NAME   = "SPARSH Run Manager"
VERSION    = "1.0.0"
BUNDLE_DIR = DIST_DIR / APP_NAME        # PyInstaller output folder

SYSTEM = platform.system()   # "Windows" | "Darwin" | "Linux"


# ── Helpers ──────────────────────────────────────────────────────────────────

def run(cmd, **kwargs):
    print(f"\n▶ {' '.join(str(c) for c in cmd)}")
    result = subprocess.run(cmd, **kwargs)
    if result.returncode != 0:
        print(f"ERROR: command failed with exit code {result.returncode}")
        sys.exit(result.returncode)


def ensure_pyinstaller():
    try:
        import PyInstaller
        print(f"✓ PyInstaller {PyInstaller.__version__} already installed")
    except ImportError:
        print("Installing PyInstaller…")
        run([sys.executable, "-m", "pip", "install", "pyinstaller"])


def clean():
    for d in (DIST_DIR, BUILD_DIR):
        if d.exists():
            print(f"  Removing {d}")
            shutil.rmtree(d)


# ── PyInstaller ───────────────────────────────────────────────────────────────

def pyinstaller_build():
    run([
        sys.executable, "-m", "PyInstaller",
        "--clean",
        "--noconfirm",
        "--distpath", str(DIST_DIR),
        "--workpath", str(BUILD_DIR),
        str(ROOT / "sparsh_gui.spec"),
    ])


# ── Windows installer (Inno Setup) ───────────────────────────────────────────

def build_windows_installer():
    # Find Inno Setup 6 compiler
    iscc_candidates = [
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
    ]
    iscc = next((p for p in iscc_candidates if Path(p).exists()), None)

    if not iscc:
        print("\n⚠  Inno Setup 6 not found — skipping .exe installer.")
        print("   Download free from https://jrsoftware.org/isdl.php")
        print("   Then re-run:  python build.py")
        print(f"\n   Raw app folder:  {BUNDLE_DIR}")
        return

    iss_path = ROOT / "installer" / "setup.iss"
    if not iss_path.exists():
        print(f"\n⚠  {iss_path} not found.")
        return

    # Run ISCC with the installer output dir set to installer/
    run([
        iscc,
        f"/O{ROOT / 'installer'}",   # output directory
        f"/DAppVersion={VERSION}",   # can override version from CLI
        str(iss_path),
    ])

    installer_path = ROOT / "installer" / f"SPARSH-RunManager-Setup-{VERSION}.exe"
    if installer_path.exists():
        size_mb = installer_path.stat().st_size / 1024 / 1024
        print(f"\n✅ Windows installer: {installer_path}  ({size_mb:.1f} MB)")


# ── macOS installer (.dmg) ────────────────────────────────────────────────────

def build_macos_dmg():
    app_path = DIST_DIR / f"{APP_NAME}.app"
    if not app_path.exists():
        print(f"\n⚠  {app_path} not found — PyInstaller may have failed.")
        return

    if not shutil.which("create-dmg"):
        print("\n⚠  create-dmg not found — skipping .dmg installer.")
        print("   Install with:  brew install create-dmg")
        print(f"\n   The .app bundle is at:\n   {app_path}")
        return

    dmg_path = ROOT / "installer" / f"SPARSH-RunManager-{VERSION}.dmg"
    dmg_path.parent.mkdir(exist_ok=True)
    if dmg_path.exists():
        dmg_path.unlink()

    run([
        "create-dmg",
        "--volname", f"{APP_NAME} {VERSION}",
        "--window-pos", "200", "120",
        "--window-size", "600", "400",
        "--icon-size", "100",
        "--icon", f"{APP_NAME}.app", "175", "190",
        "--hide-extension", f"{APP_NAME}.app",
        "--app-drop-link", "425", "190",
        "--no-internet-enable",
        str(dmg_path),
        str(app_path),
    ])
    if dmg_path.exists():
        print(f"\n✅ macOS installer: {dmg_path}")


# ── Linux tarball + .desktop ──────────────────────────────────────────────────

DESKTOP_ENTRY = """\
[Desktop Entry]
Version=1.0
Type=Application
Name=SPARSH Run Manager
Comment=Real-time ONT run monitoring and SPARSH analysis
Exec={install_dir}/SPARSH Run Manager
Icon={install_dir}/icon.png
Terminal=false
Categories=Science;Biology;
"""

LINUX_INSTALL_SH = """\
#!/usr/bin/env bash
# install.sh — installs SPARSH Run Manager for the current user
set -e
SCRIPT_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
INSTALL_DIR="$HOME/.local/share/SPARSH-Run-Manager"
BIN_LINK="$HOME/.local/bin/sparsh-run-manager"
DESKTOP_DIR="$HOME/.local/share/applications"

echo "Installing SPARSH Run Manager to $INSTALL_DIR …"
mkdir -p "$INSTALL_DIR"
cp -r "$SCRIPT_DIR/SPARSH Run Manager/." "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR/SPARSH Run Manager"

# Launcher symlink
mkdir -p "$HOME/.local/bin"
ln -sf "$INSTALL_DIR/SPARSH Run Manager" "$BIN_LINK"

# .desktop entry
mkdir -p "$DESKTOP_DIR"
cat > "$DESKTOP_DIR/sparsh-run-manager.desktop" << EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=SPARSH Run Manager
Comment=Real-time ONT run monitoring and SPARSH analysis
Exec=$INSTALL_DIR/SPARSH Run Manager
Icon=$INSTALL_DIR/icon.png
Terminal=false
Categories=Science;Biology;
EOF
chmod +x "$DESKTOP_DIR/sparsh-run-manager.desktop"
update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true

echo ""
echo "✅ Installed. Launch from your app menu or run:"
echo "   sparsh-run-manager"
"""

def build_linux_tarball():
    installer_dir = ROOT / "installer"
    installer_dir.mkdir(exist_ok=True)
    tarball = installer_dir / f"SPARSH-RunManager-{VERSION}-linux-x86_64.tar.gz"

    # Write install.sh into the dist folder
    install_sh = DIST_DIR / "install.sh"
    install_sh.write_text(LINUX_INSTALL_SH)
    install_sh.chmod(0o755)

    import tarfile
    with tarfile.open(tarball, "w:gz") as tar:
        tar.add(DIST_DIR, arcname=f"SPARSH-RunManager-{VERSION}")

    print(f"\n✅ Linux tarball: {tarball}")
    print("   Users extract and run install.sh — adds app menu entry and launcher symlink.")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"╔══════════════════════════════════════════╗")
    print(f"║   SPARSH Run Manager — build.py          ║")
    print(f"║   Platform: {SYSTEM:<28}  ║")
    print(f"║   Version:  {VERSION:<28}  ║")
    print(f"╚══════════════════════════════════════════╝")

    ensure_pyinstaller()

    if "--clean" in sys.argv:
        clean()

    print("\n── Step 1: PyInstaller ──────────────────────")
    pyinstaller_build()

    print("\n── Step 2: Platform installer ───────────────")
    if SYSTEM == "Windows":
        build_windows_installer()
    elif SYSTEM == "Darwin":
        build_macos_dmg()
    elif SYSTEM == "Linux":
        build_linux_tarball()
    else:
        print(f"Unknown platform '{SYSTEM}' — skipping installer step.")

    print("\n─────────────────────────────────────────────")
    print("Build complete.")


if __name__ == "__main__":
    main()
