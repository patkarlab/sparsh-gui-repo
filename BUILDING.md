# Building installers

## Prerequisites (all platforms)

```bash
pip install pyinstaller pillow customtkinter watchdog
python make_icon.py      # generates assets/icon.png, .ico, .icns
```

---

## Windows → `SPARSH-RunManager-Setup-1.0.0.exe`

### Extra prerequisite
Download and install **Inno Setup 6** (free):
https://jrsoftware.org/isdl.php

The default install path (`C:\Program Files (x86)\Inno Setup 6\`) is detected automatically.

### Build

```bat
python build.py
```

### What the installer does for the user

| Step | Detail |
|---|---|
| Welcome screen | App name, version, brief description |
| Install location | `%LocalAppData%\SPARSH Run Manager` (no admin rights needed) |
| Options | Desktop shortcut ✓ (default on) · Launch at startup (default off) |
| Progress bar | Files copying with live progress |
| Finish screen | Checkbox: "Launch SPARSH Run Manager now" |
| Uninstaller | Listed in Add/Remove Programs, removes all files + config |

The installer also checks whether the app is already running before
starting and blocks installation if so (prevents file-lock errors).

### Output
```
installer/
└── SPARSH-RunManager-Setup-1.0.0.exe   (~80–120 MB, self-contained)
```

---

## macOS → `SPARSH-RunManager-1.0.0.dmg`

### Extra prerequisite
```bash
brew install create-dmg
```

### Build
```bash
python build.py
```

### Output
```
installer/
└── SPARSH-RunManager-1.0.0.dmg
```

Users open the `.dmg`, drag `SPARSH Run Manager.app` into Applications, and launch from Spotlight or Launchpad.

> **Note on Gatekeeper:** unsigned apps will show a warning on first open.
> Users right-click → Open → Open to bypass it.
> To avoid the warning permanently, sign the app with an Apple Developer certificate:
> ```bash
> codesign --deep --force --verify --verbose \
>   --sign "Developer ID Application: Your Name (TEAMID)" \
>   "dist/SPARSH Run Manager.app"
> ```

---

## Linux → `.tar.gz` + `install.sh`

No extra tools needed.

### Build
```bash
python build.py
```

### Output
```
installer/
└── SPARSH-RunManager-1.0.0-linux-x86_64.tar.gz
```

Users extract and run `install.sh`:
```bash
tar xf SPARSH-RunManager-1.0.0-linux-x86_64.tar.gz
cd SPARSH-RunManager-1.0.0
bash install.sh
```

This places the app in `~/.local/share/SPARSH-Run-Manager/`, creates a
`~/.local/bin/sparsh-run-manager` symlink, and registers a `.desktop` entry
so the app appears in GNOME/KDE application menus.

---

## Automated builds via GitHub Actions

Push a version tag to build all three installers in parallel:

```bash
git tag v1.0.0
git push origin v1.0.0
```

GitHub Actions builds on `windows-latest`, `macos-latest`, and `ubuntu-latest`
and attaches the three installer files to a GitHub Release automatically.
See `.github/workflows/build.yml`.
