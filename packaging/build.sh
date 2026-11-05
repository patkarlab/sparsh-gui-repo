#!/usr/bin/env bash
# Builds the Linux package of SPARSH Run Manager with PyInstaller:
#   dist/sparsh-run-manager/                               the app folder
#   dist/SPARSH-RunManager-<version>-linux-x86_64.tar.gz   the release tarball
# Run it on the oldest Linux the package has to run on: the result needs that
# system's glibc or newer. Needs python3 (3.10 or later), python3-venv, python3-tk
# and binutils. Set PYTHON to build with another interpreter, e.g. PYTHON=python3.12.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
PYTHON="${PYTHON:-python3}"

VERSION=$("${PYTHON}" -c "import sparsh; print(sparsh.__version__)")
echo "Building SPARSH Run Manager ${VERSION} with $("${PYTHON}" --version)"

rm -rf .build-venv
"${PYTHON}" -m venv .build-venv
# shellcheck disable=SC1091
. .build-venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt pyinstaller

TK_SO=$(python -c "import _tkinter; print(_tkinter.__file__)")
pyinstaller --clean --noconfirm --onedir --windowed \
    --name sparsh-run-manager \
    --add-data "sparsh:sparsh" \
    --icon assets/icon.png \
    --hidden-import tkinter --hidden-import tkinter.messagebox \
    --hidden-import tkinter.filedialog --hidden-import _tkinter \
    --collect-all customtkinter \
    --add-binary "${TK_SO}:." \
    main.py

cp assets/icon.png packaging/install.sh packaging/uninstall.sh dist/sparsh-run-manager/
chmod +x dist/sparsh-run-manager/install.sh dist/sparsh-run-manager/uninstall.sh
TARBALL="dist/SPARSH-RunManager-${VERSION}-linux-x86_64.tar.gz"
tar -C dist -czf "${TARBALL}" sparsh-run-manager
echo "Built ${TARBALL}"
