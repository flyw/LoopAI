#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
PYTHON_BIN=${PYTHON_BIN:-python3}
PYINSTALLER_CONFIG_DIR=${PYINSTALLER_CONFIG_DIR:-$PROJECT_ROOT/build/pyinstaller-config}

if [ "$(uname -s)" != "Darwin" ]; then
    echo "This script must be run on macOS." >&2
    exit 1
fi

if ! "$PYTHON_BIN" -m PyInstaller --version >/dev/null 2>&1; then
    echo "PyInstaller is required. Install it with: $PYTHON_BIN -m pip install pyinstaller" >&2
    exit 1
fi

cd "$PROJECT_ROOT"
mkdir -p "$PYINSTALLER_CONFIG_DIR"
export PYINSTALLER_CONFIG_DIR
"$PYTHON_BIN" -m PyInstaller \
    --noconfirm \
    --clean \
    --onefile \
    --console \
    --name loopai \
    --specpath "$PROJECT_ROOT/build/pyinstaller" \
    --paths "$PROJECT_ROOT/src" \
    --add-data "$PROJECT_ROOT/src/loopai/schemas:loopai/schemas" \
    --distpath "$PROJECT_ROOT/dist" \
    --workpath "$PROJECT_ROOT/build/pyinstaller" \
    "$PROJECT_ROOT/src/loopai_entry.py"

echo "Built: $PROJECT_ROOT/dist/loopai"
file "$PROJECT_ROOT/dist/loopai"
