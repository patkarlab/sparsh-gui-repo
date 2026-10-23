#!/usr/bin/env bash
# SPARSH Run Manager — Linux dev launcher
# Run: bash launch_linux.sh
# Or make executable: chmod +x launch_linux.sh && ./launch_linux.sh

cd "$(dirname "$0")"

PYTHON=$(which python3 2>/dev/null || which python 2>/dev/null)
if [ -z "$PYTHON" ]; then
    echo "ERROR: Python not found. Install python3 and try again."
    exit 1
fi

# Install deps if missing
"$PYTHON" -c "import customtkinter" 2>/dev/null || {
    echo "Installing dependencies..."
    "$PYTHON" -m pip install --user customtkinter watchdog
}

"$PYTHON" main.py
