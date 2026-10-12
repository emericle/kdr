#!/bin/bash

# KDR Daemon Script
# Manages the KDR service as a background daemon

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
if [ -f "${SCRIPT_DIR}/../requirements.txt" ]; then
    PROJECT_ROOT="$( cd "${SCRIPT_DIR}/.." && pwd )"
elif [ -f "${SCRIPT_DIR}/requirements.txt" ]; then
    PROJECT_ROOT="${SCRIPT_DIR}"
else
    PROJECT_ROOT="$(pwd)"
fi

cd "$PROJECT_ROOT"

PID_FILE="${PROJECT_ROOT}/kdr.pid"
# One rotating log: src/scraper.py caps it (RotatingFileHandler) and rotates it,
# so a long running daemon cannot fill the disk with a per-run file.
LOG_FILE="${PROJECT_ROOT}/logs/kdr.log"

# Function to start the daemon
start_daemon() {
    if [ -f "$PID_FILE" ]; then
        local PID=$(cat "$PID_FILE")
        if ps -p $PID > /dev/null 2>&1; then
            echo "KDR service is already running (PID: $PID)"
            echo "To stop it, run: ./bin/start.sh stop"
            exit 0
        else
            echo "Removing stale PID file"
            rm -f "$PID_FILE"
        fi
    fi

    # Create log directory
    mkdir -p "${PROJECT_ROOT}/logs"

    echo "Starting KDR Data Ingestion Pipeline..."
    echo "Log file: $LOG_FILE"
    echo ""

    # Parse arguments
    ARGS=()
    if [ "$1" == "-d" ] || [ "$1" == "--debug" ]; then
        ARGS+=("--debug")
        echo "Debug mode enabled"
    fi

    # Set PYTHONPATH
    export PYTHONPATH="${PYTHONPATH}:$(pwd)"

    # Fail visibly here rather than swallowing the error in the background: an
    # import failure (missing .env, missing dependency) would otherwise be lost.
    if ! IMPORT_ERROR=$(.venv/bin/python -c "import src.scraper" 2>&1); then
        echo "scraper.py cannot be imported:"
        echo "$IMPORT_ERROR"
        exit 1
    fi

    # Start the process in background with daemon support. stdout and stderr go
    # to /dev/null because src/scraper.py already writes every record to the
    # rotating log; a second copy of the same stream is what grew logs/ to
    # hundreds of megabytes.
    nohup .venv/bin/python src/scraper.py "${ARGS[@]}" >/dev/null 2>&1 &

    PID=$!
    echo $PID > "$PID_FILE"

    echo "✓ KDR service started successfully (PID: $PID)"
    echo ""
    echo "Useful commands:"
    echo "  Check status:   ./bin/daemon.sh status"
    echo "  View logs:      tail -f $LOG_FILE"
    echo "  Stop service:   ./bin/daemon.sh stop"
}

# Function to stop the daemon
stop_daemon() {
    if [ ! -f "$PID_FILE" ]; then
        echo "No KDR service is running"
        return 1
    fi

    local PID=$(cat "$PID_FILE")

    if ! ps -p $PID > /dev/null 2>&1; then
        echo "Process $PID is not running"
        rm -f "$PID_FILE"
        return 1
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

    local PORT_PIDS=$(lsof -ti :8001 -sTCP:LISTEN 2>/dev/null || true)
    if [ -n "$PORT_PIDS" ]; then
        echo "Releasing port 8001 listener (PIDs: $PORT_PIDS)..."
        kill -9 $PORT_PIDS 2>/dev/null || true
    fi

    rm -f "$PID_FILE"

    echo "✓ KDR service stopped successfully"
}

# Function to show status
status_daemon() {
    if [ ! -f "$PID_FILE" ]; then
        echo "KDR service is not running"
        exit 1
    fi

    local PID=$(cat "$PID_FILE")

    if ps -p $PID > /dev/null 2>&1; then
        echo "KDR service is running"
        echo "PID: $PID"

        # The rotating log has a fixed name.
        if [ -f "$LOG_FILE" ]; then
            echo "Log file: $LOG_FILE"
            echo "Log file: $LOG_FILE"

            # Show recent log entries
            echo ""
            echo "Recent log entries:"
            tail -n 5 "$LOG_FILE"
        fi
    else
        echo "KDR service is not running (stale PID file)"
        rm -f "$PID_FILE"
        exit 1
    fi
}

# Main function to handle commands
case "$1" in
    start)
        start_daemon
        ;;
    stop)
        stop_daemon
        ;;
    status)
        status_daemon
        ;;
    *)
        echo "KDR Daemon Control Script"
        echo ""
        echo "Usage: $0 {start|stop|status}"
        echo ""
        echo "Commands:"
        echo "  start    - Start KDR service in background"
        echo "  stop     - Stop KDR service gracefully"
        echo "  status   - Check KDR service status"
        exit 1
        ;;
esac