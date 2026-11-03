#!/usr/bin/env bash
# Installs SPARSH Run Manager for the current user (no sudo needed).
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$HOME/.local/share/sparsh-run-manager"
BIN_LINK="$HOME/.local/bin/sparsh-run-manager"
DESKTOP_FILE="$HOME/Desktop/sparsh-run-manager.desktop"
echo "Installing SPARSH Run Manager..."
mkdir -p "$INSTALL_DIR"
cp -r "$SCRIPT_DIR/." "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR/sparsh-run-manager"
mkdir -p "$HOME/.local/bin"
ln -sf "$INSTALL_DIR/sparsh-run-manager" "$BIN_LINK"
mkdir -p "$HOME/Desktop"
cat > "$DESKTOP_FILE" << DESKEOF
[Desktop Entry]
Version=1.0
Type=Application
Name=SPARSH Run Manager
Comment=Real-time ONT run monitoring and SPARSH analysis
Exec=$INSTALL_DIR/sparsh-run-manager
Icon=$INSTALL_DIR/icon.png
Terminal=false
Categories=Science;Biology;
DESKEOF
chmod +x "$DESKTOP_FILE"
gio set "$DESKTOP_FILE" metadata::trusted true 2>/dev/null || true
echo "Done! Double-click SPARSH Run Manager on your Desktop."
