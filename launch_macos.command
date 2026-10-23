#!/usr/bin/env bash
# SPARSH Run Manager — macOS dev launcher
# Double-click this in Finder to run without building a .app.
# Requires Python 3.10+ (e.g. from conda or python.org).

cd "$(dirname "$0")"

# Find python3
PYTHON=$(which python3 2>/dev/null || which python 2>/dev/null)
if [ -z "$PYTHON" ]; then
    osascript -e 'display alert "Python not found" message "Install Python 3.10+ from https://python.org or via conda, then try again." as warning'
    exit 1
fi

# Install deps if missing
"$PYTHON" -c "import customtkinter" 2>/dev/null || {
    echo "Installing dependencies..."
    "$PYTHON" -m pip install customtkinter watchdog
}

"$PYTHON" main.py
