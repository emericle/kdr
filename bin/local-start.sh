#!/bin/bash

# Local Start Script for KDR Trading System
# This script starts the trading system in the background with logging
#
# Usage:
#   bin/local-start.sh             # Start in local testing mode (debug logging)
#   bin/local-start.sh --dry-run   # Also run without placing orders
#   bin/local-start.sh --help      # Show this help

set -e

# Start/stop mechanics come from the same library the other entry points use.
KDR_ROOT=$(cd "$(dirname "$0")/.." && pwd)
source "$KDR_ROOT/bin/_start_common.sh"

# Local testing directories
DATA_DIR="$KDR_ROOT/outputs"
DB_DIR="$KDR_ROOT/.kdr_db"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
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

usage() {
    echo "Usage: bin/local-start.sh [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --debug    Enable verbose logging (the default in local mode)"
    echo "  --dry-run  Run without placing orders"
    echo "  --help     Show this help"
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

# Local testing defaults to verbose logging.
if [ -z "$PARSE_DEBUG" ]; then
    PARSE_DEBUG="--debug"
fi

print_status "Setting up KDR Trading System environment..."

# Verify the interpreter the pipeline actually runs on. The application checks
# for Python 3.13 at startup, so this is the same rule the other entry points
# apply; there is no separate dependency probe.
kdr_check_interpreter || exit 1

# Create necessary directories
mkdir -p logs
mkdir -p outputs
mkdir -p .kdr_db
mkdir -p .env_check

# Check for .env file
if [ ! -f ".env" ]; then
    print_warning ".env file not found!"
    print_warning "Please create a .env file with the required environment variables:"
    echo ""
    echo "Required variables in .env:"
    # Human hint only; bin/check_env.py is what actually decides the list.
    echo "  ALPACA_API_KEY=your_alpaca_api_key"
    echo "  ALPACA_SECRET_KEY=your_alpaca_secret_key"
    echo "  ADANOS_API_KEY=your_adanos_api_key"
    echo "  DATABASE_URL=postgresql://user:password@localhost:5432/kdr"
    echo ""
    print_warning "You can copy .env.template to .env and fill in your credentials."
    exit 1
fi

# Validate required environment variables
print_status "Validating environment variables..."
source .env

check_required_vars() {
    local missing_vars=()
    local required_vars=()
    local required_keys
    local var

    # bin/check_env.py is the single source of truth for which variables are
    # required; read the list from it rather than restating it here. An empty
    # list is not "nothing is required", it is the checker failing, so it stops
    # here instead of letting the pipeline start unconfigured.
    if ! required_keys="$("$PYTHON_BIN" "$KDR_ROOT/bin/check_env.py" --keys 2>/dev/null)"; then
        print_error "Cannot read the required variables from bin/check_env.py"
        exit 1
    fi
    # Unquoted on purpose: the keys are bare identifiers one per line, and word
    # splitting is what turns that list into an array (bash 3.2 has no mapfile).
    required_vars=( $required_keys )
    if [ ${#required_vars[@]} -eq 0 ]; then
        print_error "bin/check_env.py named no required variables"
        exit 1
    fi

    for var in "${required_vars[@]}"; do
        if [ -z "${!var}" ]; then
            missing_vars+=("$var")
        fi
    done

    if [ ${#missing_vars[@]} -gt 0 ]; then
        print_error "Missing required environment variables: ${missing_vars[*]}"
        exit 1
    fi
}

check_required_vars

# Stop any existing instance
RUNNING_PID=$(kdr_running_pid || true)
if [ -n "$RUNNING_PID" ]; then
    print_warning "Stopping existing KDR instance (PID: $RUNNING_PID)..."
    kdr_stop_running || true
    sleep 2
fi

# Start the trading system
print_status "Starting KDR Trading System..."
print_status "This will run the scraper in debug mode for local testing."

# src/scraper.py writes every record to the rotating logs/kdr.log itself, so a
# second copy of the same stream is not kept here.
kdr_start_background "$KDR_LOG"

# Wait for the system to initialize
sleep 3

# Check if the process is still running
KDR_PID=$(kdr_running_pid || true)
if [ -n "$KDR_PID" ]; then
    print_status "KDR Trading System started successfully!"
    print_status "PID: $KDR_PID"
    echo ""
    echo "To view live logs, run: tail -f $KDR_LOG"
    echo "To stop the system, run: bin/local-stop.sh"
    echo "To monitor system health, check: $KDR_LOG"
    echo "Output data will be saved to: $DATA_DIR"
    echo ""
    print_status "Your system is now running in local testing mode."
else
    print_error "Failed to start KDR Trading System."
    print_error "Check the log file for details: $KDR_LOG"
    exit 1
fi
