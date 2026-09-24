#!/bin/bash

# Local Stop Script for KDR Trading System
# This script stops the running trading system

set -e

# Configuration
PID_FILE="$PWD/logs/kdr.pid"
LOG_FILE="$PWD/logs/system.log"

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${GREEN}[$(date '+%Y-%m-%d %H:%M:%S')]${NC} $1"
}

print_error() {
    echo -e "${RED}[$(date '+%Y-%m-%d %H:%M:%S')]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[$(date '+%Y-%m-%d %H:%M:%S')]${NC} $1"
}

# Check if PID file exists
if [ ! -f "$PID_FILE" ]; then
    print_error "No KDR PID file found at $PID_FILE"
    print_error "The system may not be running."
    exit 1
fi

# Read the PID
PID=$(cat "$PID_FILE")

# Check if process is running
if ! ps -p $PID > /dev/null 2>&1; then
    print_error "Process with PID $PID is not running."
    print_warning "Removing stale PID file."
    rm "$PID_FILE"
    exit 1
fi

# Check if it's the KDR process
if ! ps -o comm= -p $PID | grep -q "python3.14"; then
    print_error "PID $PID does not seem to be running the KDR Trading System."
    print_warning "Are you sure you want to stop this process?"
    read -p "Continue? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        print_status "Operation cancelled."
        exit 1
    fi
fi

# Stop the process
print_status "Stopping KDR Trading System (PID: $PID)..."
kill $PID

# Wait for graceful shutdown
for i in {1..10}; do
    if ! ps -p $PID > /dev/null 2>&1; then
        print_status "KDR Trading System stopped successfully."
        rm "$PID_FILE"
        print_status "Log file saved to: $LOG_FILE"
        exit 0
    fi
    sleep 1
done

# Force kill if needed
print_warning "Graceful shutdown failed. Force killing..."
kill -9 $PID

# Wait a moment to ensure cleanup
sleep 2

# Final check
if ! ps -p $PID > /dev/null 2>&1; then
    print_status "KDR Trading System force-stopped."
    rm "$PID_FILE"
    print_status "Log file saved to: $LOG_FILE"
    exit 0
else
    print_error "Failed to stop KDR Trading System."
    print_error "Please manually check and kill the process."
    exit 1
fi