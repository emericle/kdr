#!/bin/bash
# =============================================================================
# KDR Daemon Service Manager (systemd-like interface)
# =============================================================================
# Provides a systemd-like interface for managing the KDR scraper as a daemon
# on systems without systemd (e.g., Raspberry Pi OS Bullseye).
#
# Usage:
#   bin/daemon.sh start    # Start the scraper in the background
#   bin/daemon.sh stop     # Stop the running scraper
#   bin/daemon.sh restart  # Restart the scraper
#   bin/daemon.sh status   # Show current status
#   bin/daemon.sh help     # Show this help
#
# The start/stop mechanics live in _start_common.sh, the same library the
# foreground entry points use, so the two paths cannot disagree about the
# PID file or the interpreter.
# =============================================================================

KDR_ROOT=$(cd "$(dirname "$0")/.." && pwd)
source "$KDR_ROOT/bin/_start_common.sh"

# Daemon log file (separate from the foreground log)
LOG_FILE="${KDR_ROOT}/logs/kdr.log"

# =============================================================================
# Status reporting
# =============================================================================

show_status() {
    local PID=$(kdr_running_pid)

    echo "KDR Daemon Status"
    echo "================="

    if [ -n "$PID" ]; then
        echo "SCRAPER: RUNNING (PID: $PID)"
        echo "  Command: $(ps -p $PID -o args= 2>/dev/null | sed 's/^[[:space:]]*//')"

        # Check log file
        if [ -f "$LOG_FILE" ]; then
            echo "  Log file: $LOG_FILE ($(du -h "$LOG_FILE" | cut -f1))"
            echo "  Last 5 log entries:"
            tail -5 "$LOG_FILE" | sed 's/^/    /'
        fi
    else
        echo "SCRAPER: STOPPED"
    fi

    # Check database connectivity
    echo ""
    echo "Database: Checking..."
    if [ -f "${KDR_ROOT}/.env" ]; then
        source "${KDR_ROOT}/.env"
    fi

    if [ -n "$DATABASE_URL" ]; then
        if .venv/bin/python -c "
import psycopg2
try:
    conn = psycopg2.connect('$DATABASE_URL', connect_timeout=5)
    conn.close()
    print('OK')
except Exception as e:
    print(f'FAILED: {e}')
" 2>/dev/null | grep -q 'OK'; then
            echo "Database: CONNECTED"
        else
            echo "Database: DISCONNECTED"
        fi
    else
        echo "Database: NO DATABASE_URL"
    fi
}

usage() {
    echo "Usage: bin/daemon.sh COMMAND"
    echo ""
    echo "Commands:"
    echo "  start    Start the scraper in the background"
    echo "  stop     Stop the running scraper"
    echo "  restart  Restart the scraper"
    echo "  status   Show current status"
    echo "  help     Show this help"
    echo ""
    echo "Options (with start):"
    echo "  --debug  Enable verbose logging"
    echo "  --dry-run  Run without placing orders"
    echo ""
    echo "Logs are written to logs/kdr.log"
}

# =============================================================================
# Main command dispatcher
# =============================================================================

case "$1" in
    start)
        shift
        kdr_parse_args "$@" || exit 1

        PID=$(kdr_running_pid)
        if [ -n "$PID" ]; then
            echo "already running (PID $PID); use restart to replace it"
            exit 1
        fi

        kdr_check_interpreter || exit 1
        kdr_preflight_import || exit 1

        echo "Starting scraper..."
        kdr_start_background "$LOG_FILE"
        ;;
    stop)
        PID=$(kdr_running_pid)
        if [ -z "$PID" ]; then
            echo "SCRAPER: STOPPED"
            exit 0
        fi

        echo "Stopping KDR service (PID: $PID)..."

        # Send SIGTERM for graceful shutdown
        kill -TERM $PID

        # Wait for process to terminate
        local count=0
        while ps -p $PID > /dev/null 2>&1; do
            sleep 1
            count=$((count + 1))
            if [ $count -ge 30 ]; then
                echo "Process did not terminate gracefully, sending SIGKILL"
                kill -KILL $PID
                sleep 2
                break
            fi
        done

        # Clean up any lingering scraper processes and processes on port 8001
        local STALE_PIDS=$(pgrep -f "python.*src/scraper.py" 2>/dev/null || true)
        if [ -n "$STALE_PIDS" ]; then
            echo "Cleaning up lingering scraper processes: $STALE_PIDS"
            kill -9 $STALE_PIDS 2>/dev/null || true
        fi

        local PORT_PIDS=$(lsof -ti:8001 2>/dev/null || true)
        if [ -n "$PORT_PIDS" ]; then
            echo "Cleaning up processes on port 8001: $PORT_PIDS"
            kill -9 $PORT_PIDS 2>/dev/null || true
        fi

        echo "SCRAPER: STOPPED"
        ;;
    restart)
        shift
        kdr_parse_args "$@" || exit 1
        echo "Restarting KDR service..."
        kdr_stop_running >/dev/null 2>&1 || true
        kdr_check_interpreter || exit 1
        kdr_preflight_import || exit 1
        echo "Starting scraper..."
        kdr_start_background "$LOG_FILE"
        ;;
    status)
        show_status
        ;;
    help|--help|-h)
        usage
        ;;
    *)
        echo "Unknown command: $1"
        usage
        exit 1
        ;;
esac
