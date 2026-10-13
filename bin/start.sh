#!/bin/bash
# =============================================================================
# KDR Complete Pipeline Startup
# =============================================================================
# Starts the scraper (Alpaca data ingestion) and web server.
#
# Usage:
#   bin/start.sh             # Start scraper in background, web server in foreground
#   bin/start.sh --debug     # Enable verbose logging
#   bin/start.sh --dry-run   # Run without placing orders
#   bin/start.sh --stop      # Stop the running scraper and free port 8001
#   bin/start.sh --status    # Report whether the scraper is running
#   bin/start.sh --help      # Show this help
#
# The scraper runs in the background and logs to logs/kdr.log. The web server
# runs in the foreground and logs to the console.
#
# Start/stop mechanics come from _start_common.sh, the same library daemon.sh
# and local-start.sh use, so every entry point agrees on the PID file, the
# interpreter, and what a clean shutdown means.
# =============================================================================

KDR_ROOT=$(cd "$(dirname "$0")/.." && pwd)
source "$KDR_ROOT/bin/_start_common.sh"

# --- Options -------------------------------------------------------------------
DEBUG_FLAG=""
DRY_RUN_FLAG=""

usage() {
    echo "Usage: bin/start.sh [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --debug     Enable verbose logging"
    echo "  --dry-run   Run without placing orders"
    echo "  --stop      Stop the running scraper and free port 8001"
    echo "  --status    Report whether the scraper is running"
    echo "  --help      Show this help"
    echo ""
    echo "Logs are written to logs/kdr.log"
}

kdr_parse_args "$@" || {
    usage
    exit 1
}

if [ "$KDR_HELP" = "1" ]; then
    usage
    exit 0
fi

if [ "$KDR_STOP" = "1" ]; then
    PID=$(kdr_stop_running)
    if [ -z "$PID" ]; then
        echo "scraper not running"
    else
        echo "stopped scraper (PID $PID)"
    fi
    exit 0
fi

if [ "$KDR_STATUS" = "1" ]; then
    PID=$(kdr_running_pid)
    if [ -n "$PID" ]; then
        echo "scraper running (PID $PID)"
    else
        echo "scraper not running"
    fi
    exit 0
fi

if [ -n "$PARSE_DEBUG" ]; then
    DEBUG_FLAG="--debug"
fi
if [ -n "$PARSE_DRY_RUN" ]; then
    DRY_RUN_FLAG="--dry-run"
fi

# --- Pre-flight ----------------------------------------------------------------

# Check if scraper is already running
PID=$(kdr_running_pid)
if [ -n "$PID" ]; then
    echo "warning: scraper already running (PID $PID); restart it first"
    exit 1
fi

# Verify Python 3.13
kdr_check_interpreter || exit 1

# Verify imports
echo "Verifying imports..."
kdr_preflight_import || exit 1

# --- Start Scraper -------------------------------------------------------------------
echo ""
echo "Starting scraper..."
kdr_start_background "${KDR_LOG}"

# --- Start Web Server (foreground) ---------------------------------------------------
echo ""
echo "Starting web server..."
echo "  URL: http://localhost:8001"
echo "  Press Ctrl+C to stop"
echo ""

# Start web server in foreground
.venv/bin/python -m src.web_server "$DEBUG_FLAG" "$DRY_RUN_FLAG"
