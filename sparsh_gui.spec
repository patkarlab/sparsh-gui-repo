# sparsh_gui.spec
# PyInstaller spec for SPARSH ONT Run Manager
# Usage: pyinstaller sparsh_gui.spec
#
# Run this file on each target OS to produce that platform's bundle.
# The OUTPUT goes to dist/SPARSH Run Manager/  (folder mode, not onefile)
# onefile mode is avoided because it is slower to start and triggers AV on Windows.

import sys
import os
from pathlib import Path

ROOT = Path(SPECPATH)

# Collect all sparsh package modules
from PyInstaller.utils.hooks import collect_all

ctk_datas, ctk_binaries, ctk_hiddenimports = collect_all('customtkinter')

a = Analysis(
    [str(ROOT / 'main.py')],
    pathex=[str(ROOT)],
    binaries=ctk_binaries,
    datas=[
        # Include the entire sparsh package
        (str(ROOT / 'sparsh'), 'sparsh'),
        # CustomTkinter themes and assets
        *ctk_datas,
    ],
    hiddenimports=[
        *ctk_hiddenimports,
        'watchdog.observers',
        'watchdog.observers.polling',   # fallback observer (works on all OS)
        'watchdog.events',
        '_tkinter',
        'tkinter',
        'tkinter.messagebox',
        'tkinter.filedialog',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['numpy', 'pandas', 'matplotlib', 'scipy'],  # not needed in GUI
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SPARSH Run Manager',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,           # no terminal window on Windows/macOS
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # Windows icon
    icon=str(ROOT / 'assets' / 'icon.ico') if sys.platform == 'win32' and (ROOT / 'assets' / 'icon.ico').exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='SPARSH Run Manager',
)

# macOS: wrap in a .app bundle
if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='SPARSH Run Manager.app',
        icon=str(ROOT / 'assets' / 'icon.icns') if (ROOT / 'assets' / 'icon.icns').exists() else None,
        bundle_identifier='com.sparsh.ontrunmanager',
        info_plist={
            'CFBundleDisplayName': 'SPARSH Run Manager',
            'CFBundleVersion': '1.0.0',
            'CFBundleShortVersionString': '1.0.0',
            'NSHighResolutionCapable': True,
            'LSMinimumSystemVersion': '11.0',
            'NSRequiresAquaSystemAppearance': False,  # allow dark mode
        },
    )
