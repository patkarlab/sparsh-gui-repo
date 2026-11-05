#!/usr/bin/env bash
# Installs SPARSH Run Manager for the current user. No sudo needed.
#   The app:            ~/.local/share/sparsh-run-manager/
#   A command:          ~/.local/bin/sparsh-run-manager
#   Launchers:          the application menu and ~/Desktop
# Settings live in ~/.config/sparsh-run-manager/ and are kept across updates.
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$HOME/.local/share/sparsh-run-manager"
BIN_LINK="$HOME/.local/bin/sparsh-run-manager"
MENU_FILE="$HOME/.local/share/applications/sparsh-run-manager.desktop"
DESKTOP_FILE="$HOME/Desktop/sparsh-run-manager.desktop"

if [ ! -x "$SCRIPT_DIR/sparsh-run-manager" ]; then
    echo "install.sh must be run from the unpacked release folder (sparsh-run-manager/ with the app inside)." >&2
    exit 1
fi

echo "Installing SPARSH Run Manager to $INSTALL_DIR ..."
mkdir -p "$INSTALL_DIR"
cp -r "$SCRIPT_DIR/." "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR/sparsh-run-manager"

mkdir -p "$HOME/.local/bin"
ln -sf "$INSTALL_DIR/sparsh-run-manager" "$BIN_LINK"

# An older version kept its settings inside the app folder; move them to ~/.config.
if [ -f "$INSTALL_DIR/_internal/sparsh_config.json" ] && [ ! -f "$HOME/.config/sparsh-run-manager/sparsh_config.json" ]; then
    mkdir -p "$HOME/.config/sparsh-run-manager"
    cp "$INSTALL_DIR/_internal/sparsh_config.json" "$HOME/.config/sparsh-run-manager/sparsh_config.json"
fi

ENTRY="[Desktop Entry]
Version=1.0
Type=Application
Name=SPARSH Run Manager
Comment=Follows an Oxford Nanopore run and runs SPARSH on each new set of BAM files
Exec=$INSTALL_DIR/sparsh-run-manager
Icon=$INSTALL_DIR/icon.png
Terminal=false
Categories=Science;Biology;"
mkdir -p "$(dirname "$MENU_FILE")"
printf '%s\n' "$ENTRY" > "$MENU_FILE"
chmod +x "$MENU_FILE"
if [ -d "$HOME/Desktop" ]; then
    printf '%s\n' "$ENTRY" > "$DESKTOP_FILE"
    chmod +x "$DESKTOP_FILE"
    gio set "$DESKTOP_FILE" metadata::trusted true 2>/dev/null || true
fi
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true

echo "Done. Start SPARSH Run Manager from the application menu, the desktop icon,"
echo "or with the command:  sparsh-run-manager"
case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *) echo "(~/.local/bin is not on your PATH yet; log out and back in, or run $BIN_LINK)" ;;
esac
