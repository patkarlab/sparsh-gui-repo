# Linux installation — SPARSH Run Manager

## Download

Download `SPARSH-RunManager-1.0.0-linux-x86_64.tar.gz` from the Releases page.

## Install (three commands)

```bash
tar -xf SPARSH-RunManager-1.0.0-linux-x86_64.tar.gz
cd "SPARSH Run Manager"
bash install.sh
```

`install.sh` does the following:
- Copies the app to `~/.local/share/SPARSH-Run-Manager/`
- Creates a launcher at `~/.local/bin/sparsh-run-manager`
- Adds an entry to your GNOME / KDE application menu

## Launch

After installation, find **SPARSH Run Manager** in your application menu,
or from a terminal:

```bash
sparsh-run-manager
```

## Uninstall

```bash
rm -rf ~/.local/share/SPARSH-Run-Manager
rm ~/.local/bin/sparsh-run-manager
rm ~/.local/share/applications/sparsh-run-manager.desktop
```

## Build from source (optional)

Only needed if you want to build it yourself rather than use the pre-built binary.

**Prerequisites**
- Python 3.10 or later
- `python3-tk` system package

```bash
# Ubuntu / Debian
sudo apt install python3-tk
pip install pyinstaller customtkinter watchdog pillow

# Fedora / RHEL
sudo dnf install python3-tkinter
pip install pyinstaller customtkinter watchdog pillow

# Arch
sudo pacman -S tk
pip install pyinstaller customtkinter watchdog pillow
```

**Build**

```bash
git clone https://github.com/your-org/sparsh-gui
cd sparsh-gui
python make_icon.py
pyinstaller --clean --noconfirm \
  --onedir --windowed \
  --name "SPARSH Run Manager" \
  --add-data "sparsh:sparsh" \
  --hidden-import customtkinter \
  --hidden-import watchdog.observers \
  --hidden-import watchdog.observers.polling \
  main.py
```

The built app is in `dist/SPARSH Run Manager/`. Run it with:

```bash
"dist/SPARSH Run Manager/SPARSH Run Manager"
```
