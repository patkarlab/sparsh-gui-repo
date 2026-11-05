#!/usr/bin/env bash
# Removes SPARSH Run Manager installed by install.sh. Settings in
# ~/.config/sparsh-run-manager/ are kept unless --settings is given.
set -e
rm -rf "$HOME/.local/share/sparsh-run-manager"
rm -f "$HOME/.local/bin/sparsh-run-manager" \
      "$HOME/.local/share/applications/sparsh-run-manager.desktop" \
      "$HOME/Desktop/sparsh-run-manager.desktop"
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
if [ "${1:-}" = "--settings" ]; then
    rm -rf "$HOME/.config/sparsh-run-manager"
    echo "SPARSH Run Manager and its settings were removed."
else
    echo "SPARSH Run Manager was removed. Settings were kept in ~/.config/sparsh-run-manager/"
    echo "(run this script with --settings to remove them too)."
fi
