#!/bin/bash

# Local Stop Script for KDR Trading System
# This script stops the running trading system
#
# Usage:
#   bin/local-stop.sh   # Stop the running scraper

set -e

# Same start/stop mechanics as the start scripts, including the one PID file.
KDR_ROOT=$(cd "$(dirname "$0")/.." && pwd)
source "$KDR_ROOT/bin/_start_common.sh"

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

print_status() {
    echo -e "${GREEN}[$(date '+%Y-%m-%d %H:%M:%S')]${NC} $1"
}

print_error() {
    echo -e "${RED}[$(date '+%Y-%m-%d %H:%M:%S')]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[$(date '+%Y-%m-%d %H:%M:%S')]${NC} $1"
}

# The PID file holds the interpreter path, not the script, so the identity of
# the process is read from its arguments: `src/scraper.py` is what makes a
# process ours. Matching on the command name would compare against the
# resolved interpreter (`Python`), which never spells out the version.
is_kdr_process() {
    ps -o args= -p "$1" 2>/dev/null | grep -q "src/scraper.py"
}

PID=$(kdr_running_pid || true)

if [ -z "$PID" ]; then
    print_error "No KDR process found at $KDR_PID_FILE"
    print_error "The system may not be running."
    exit 1
fi

if ! is_kdr_process "$PID"; then
    print_error "PID $PID does not seem to be running the KDR Trading System."
    print_warning "Are you sure you want to stop this process?"
    read -p "Continue? (y/n) " -n 1 -r || true
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        print_status "Operation cancelled."
        exit 1
    fi
fi

# Stop the process
print_status "Stopping KDR Trading System (PID: $PID)..."
kill "$PID"

# Wait for graceful shutdown
for i in {1..10}; do
    if ! ps -p "$PID" > /dev/null 2>&1; then
        print_status "KDR Trading System stopped successfully."
        rm -f "$KDR_PID_FILE"
        print_status "Log file saved to: $KDR_LOG"
        exit 0
    fi
    sleep 1
done

# Force kill if needed
print_warning "Graceful shutdown failed. Force killing..."
kill -9 "$PID" || true

# Wait a moment to ensure cleanup
sleep 2

# Final check
if ! ps -p "$PID" > /dev/null 2>&1; then
    print_status "KDR Trading System force-stopped."
    rm -f "$KDR_PID_FILE"
    print_status "Log file saved to: $KDR_LOG"
    exit 0
else
    print_error "Failed to stop KDR Trading System."
    print_error "Please manually check and kill the process."
    exit 1
fi
