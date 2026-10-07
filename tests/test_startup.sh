#!/bin/bash
set -e

# Determine script and project root directories
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
if [ -f "${SCRIPT_DIR}/../requirements.txt" ]; then
    PROJECT_ROOT="$( cd "${SCRIPT_DIR}/.." && pwd )"
elif [ -f "${SCRIPT_DIR}/requirements.txt" ]; then
    PROJECT_ROOT="${SCRIPT_DIR}"
else
    PROJECT_ROOT="$(pwd)"
fi

cd "$PROJECT_ROOT"

# Prefer virtualenv Python if present, then python3.14, then python3
if [ -x "${PROJECT_ROOT}/.venv/bin/python" ]; then
    PYTHON_EXEC="${PROJECT_ROOT}/.venv/bin/python"
elif command -v python3.14 >/dev/null 2>&1; then
    PYTHON_EXEC="python3.14"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_EXEC="python3"
else
    PYTHON_EXEC="python"
fi

exec "$PYTHON_EXEC" "${SCRIPT_DIR}/test_startup.py" "$@"
