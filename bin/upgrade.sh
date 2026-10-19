#!/bin/bash
# =============================================================================
# KDR Upgrade Entry Point
# =============================================================================
# Runs `alembic upgrade head` against DATABASE_URL in place (never dropping
# existing market data) and then starts (or restarts) the background service.
#
# Usage:
#   bin/upgrade.sh                 # Migrate to head, then start/restart service
#   bin/upgrade.sh --migrate-only  # Run alembic upgrade head without starting
#   bin/upgrade.sh --debug         # Migrate, then start with verbose logging
#   bin/upgrade.sh --dry-run       # Migrate, then start without placing orders
#   bin/upgrade.sh --help          # Show this help
# =============================================================================

set -eo pipefail

KDR_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$KDR_ROOT"
source "$KDR_ROOT/bin/_start_common.sh"

MIGRATE_ONLY=0
FORWARD_ARGS=()

usage() {
    echo "Usage: bin/upgrade.sh [OPTIONS]"
    echo ""
    echo "Migrates the database schema to the latest Alembic head in place"
    echo "(preserving market_data, sentiment_data and model_weights) and then"
    echo "starts or restarts the KDR background service."
    echo ""
    echo "Options:"
    echo "  --migrate-only  Run 'alembic upgrade head' and exit without starting"
    echo "  --debug         Enable verbose logging when starting the service"
    echo "  --dry-run       Run the service without placing orders"
    echo "  --help, -h      Show this help"
}

for arg in "$@"; do
    case "$arg" in
        --migrate-only)
            MIGRATE_ONLY=1
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            FORWARD_ARGS+=("$arg")
            ;;
    esac
done

kdr_parse_args "${FORWARD_ARGS[@]+"${FORWARD_ARGS[@]}"}" || {
    usage
    exit 1
}

kdr_check_interpreter || exit 1

echo "Running Alembic migrations to head..."
PYTHONPATH="${PYTHONPATH:-}:${KDR_ROOT}" "$PYTHON_BIN" -m alembic -c "${KDR_ROOT}/alembic.ini" upgrade head
echo "✓ Database schema is at Alembic head."

if [ "$MIGRATE_ONLY" -eq 1 ]; then
    exit 0
fi

kdr_preflight_import || exit 1

RUNNING_PID=$(kdr_running_pid || true)
if [ -n "$RUNNING_PID" ]; then
    echo "Stopping running KDR service (PID $RUNNING_PID)..."
    kdr_stop_running >/dev/null 2>&1 || true
fi

echo "Starting KDR service..."
kdr_start_background "$KDR_LOG"
