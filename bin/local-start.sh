#!/bin/bash

# Local Start Script for KDR Trading System
# This script starts the trading system in the background with logging

set -e

# Configuration
PYTHON_CMD="python3.14"
SCRIPT_PATH="${SCRIPT_PATH:-$PWD}"
LOG_FILE="$PWD/logs/system.log"
PID_FILE="$PWD/logs/kdr.pid"
DATA_DIR="$PWD/outputs"
DB_DIR="$PWD/.kdr_db"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
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

# Check if Python 3.14 is installed
if ! command -v $PYTHON_CMD &> /dev/null; then
    print_error "Python 3.14 is not installed. Please install it first."
    print_error "You can install it using: brew install python@3.14"
    exit 1
fi

# Create necessary directories
mkdir -p logs
mkdir -p outputs
mkdir -p .kdr_db
mkdir -p .env_check

print_status "Setting up KDR Trading System environment..."

# Check for .env file
if [ ! -f ".env" ]; then
    print_warning ".env file not found!"
    print_warning "Please create a .env file with the required environment variables:"
    echo ""
    echo "Required variables in .env:"
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
    local required_vars=()
    local missing_vars=()

    for var in ALPACA_API_KEY ALPACA_SECRET_KEY ADANOS_API_KEY DATABASE_URL; do
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

# Check for required Python packages
print_status "Checking Python dependencies..."
$PYTHON_CMD -c "import alpaca_trade_api" 2>/dev/null || {
    print_error "alpaca-trade-api is not installed."
    print_error "Run: $PYTHON_CMD -m pip install -r requirements.txt"
    exit 1
}

$PYTHON_CMD -c "import psycopg2" 2>/dev/null || {
    print_warning "psycopg2 is not installed. Database operations may fail."
    print_warning "Run: $PYTHON_CMD -m pip install psycopg2-binary"
}

$PYTHON_CMD -c "import gymnasium" 2>/dev/null || {
    print_warning "gymnasium is not installed. RL training may fail."
    print_warning "Run: $PYTHON_CMD -m pip install gymnasium"
}

# Stop any existing instance
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if ps -p $PID > /dev/null 2>&1; then
        print_warning "Stopping existing KDR instance (PID: $PID)..."
        kill $PID
        sleep 2
        if ps -p $PID > /dev/null 2>&1; then
            print_error "Failed to stop existing instance. Force killing..."
            kill -9 $PID
        fi
    fi
    rm "$PID_FILE"
fi

# Start the trading system
print_status "Starting KDR Trading System..."
print_status "This will run the scraper in debug mode for local testing."

NOHUP $PYTHON_CMD src/scraper.py --debug > "$LOG_FILE" 2>&1 &
KDR_PID=$!

# Save the PID
echo $KDR_PID > "$PID_FILE"

# Wait for the system to initialize
sleep 3

# Check if the process is still running
if ps -p $KDR_PID > /dev/null 2>&1; then
    print_status "KDR Trading System started successfully!"
    print_status "PID: $KDR_PID"
    echo ""
    echo "To view live logs, run: tail -f $LOG_FILE"
    echo "To stop the system, run: bin/local-stop.sh"
    echo "To monitor system health, check: $LOG_FILE"
    echo "Output data will be saved to: $DATA_DIR"
    echo ""
    print_status "Your system is now running in local testing mode."
else
    print_error "Failed to start KDR Trading System."
    print_error "Check the log file for details: $LOG_FILE"
    rm "$PID_FILE"
    exit 1
fi