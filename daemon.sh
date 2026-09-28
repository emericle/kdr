#!/bin/bash

# KDR Daemon Script
# Manages the KDR service as a background daemon

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

PID_FILE="${SCRIPT_DIR}/kdr.pid"
LOG_FILE="${SCRIPT_DIR}/logs/kdr_$(date +%Y%m%d_%H%M%S).log"

# Function to start the daemon
start_daemon() {
    if [ -f "$PID_FILE" ]; then
        local PID=$(cat "$PID_FILE")
        if ps -p $PID > /dev/null 2>&1; then
            echo "KDR service is already running (PID: $PID)"
            echo "To stop it, run: ./start.sh stop"
            exit 0
        else
            echo "Removing stale PID file"
            rm -f "$PID_FILE"
        fi
    fi

    # Create log directory
    mkdir -p "${SCRIPT_DIR}/logs"

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

    # Start the process in background with daemon support
    nohup .venv/bin/python src/scraper.py "${ARGS[@]}" >> "$LOG_FILE" 2>&1 &

    PID=$!
    echo $PID > "$PID_FILE"

    echo "✓ KDR service started successfully (PID: $PID)"
    echo ""
    echo "Useful commands:"
    echo "  Check status:   ./daemon.sh status"
    echo "  View logs:      tail -f $LOG_FILE"
    echo "  Stop service:   ./daemon.sh stop"
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

    rm -f "$PID_FILE"

    if ps -p $PID > /dev/null 2>&1; then
        echo "Failed to stop KDR service"
        exit 1
    else
        echo "✓ KDR service stopped successfully"
    fi
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

        # Find the latest log file
        LOG_FILE=$(ls -t "${SCRIPT_DIR}"/logs/kdr_*.log 2>/dev/null | head -1)
        if [ -n "$LOG_FILE" ]; then
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