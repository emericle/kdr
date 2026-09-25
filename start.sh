#!/bin/bash

# KDR Startup Script
# Checks environment variables, sets up venv, and starts the scraper process in background

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo -e "${GREEN}╔════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║        KDR System Startup              ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════╝${NC}"
echo ""

# Step 1: Check Python Version
echo -e "${YELLOW}[1/4]${NC} Checking Python version..."
if ! command -v python3.14 &> /dev/null; then
    echo -e "${RED}Error: Python 3.14 is required but not found.${NC}"
    echo -e "${YELLOW}Please install Python 3.14 or add it to your PATH.${NC}"
    exit 1
fi

PYTHON_VERSION=$(python3.14 --version 2>&1 | awk '{print $2}')
echo -e "${GREEN}✓ Python version: $PYTHON_VERSION${NC}"

# Step 2: Check Environment Variables
echo ""
echo -e "${YELLOW}[2/4]${NC} Checking environment variables..."

# Load .env file if present
if [ -f "$SCRIPT_DIR/.env" ]; then
    echo -e "${YELLOW}Loading environment from .env file...${NC}"
    set -a
    # Source .env without failing if comments/formatting exist
    source "$SCRIPT_DIR/.env"
    set +a
fi

REQUIRED_VARS=(
    "ADANOS_API_KEY"
    "ALPACA_API_KEY"
    "ALPACA_SECRET_KEY"
    "DATABASE_URL"
)

MISSING_VARS=()

for var in "${REQUIRED_VARS[@]}"; do
    if [ -z "${!var}" ]; then
        MISSING_VARS+=("$var")
    fi
done

if [ ${#MISSING_VARS[@]} -ne 0 ]; then
    echo -e "${RED}✗ Missing required environment variables:${NC}"
    for var in "${MISSING_VARS[@]}"; do
        echo -e "${RED}  - $var${NC}"
    done
    echo ""
    echo -e "${YELLOW}Please set up your .env file with all required variables.${NC}"
    echo -e "${YELLOW}You can find an example .env template in the repository.${NC}"
    exit 1
fi

echo -e "${GREEN}✓ All required environment variables are present${NC}"

# Step 3: Setup/Activate Virtual Environment
echo ""
echo -e "${YELLOW}[3/4]${NC} Setting up virtual environment..."

if [ ! -d ".venv" ]; then
    echo -e "${YELLOW}Creating new virtual environment at .venv...${NC}"
    python3.14 -m venv .venv
    echo -e "${GREEN}✓ Virtual environment created${NC}"
else
    echo -e "${GREEN}✓ Virtual environment already exists${NC}"
fi

# Activate virtual environment
echo -e "${YELLOW}Activating virtual environment...${NC}"
source .venv/bin/activate

# Upgrade pip
echo -e "${YELLOW}Upgrading pip...${NC}"
pip install --upgrade pip setuptools wheel > /dev/null 2>&1

# Install/update requirements
echo -e "${YELLOW}Installing dependencies...${NC}"
pip install -r requirements.txt > /dev/null 2>&1
echo -e "${GREEN}✓ Dependencies installed${NC}"

# Step 4: Start KDR Service in Background
echo ""
echo -e "${YELLOW}[4/4]${NC} Starting KDR service in background..."

# Create logs directory if it doesn't exist
mkdir -p logs

# Create PID file for process management
PID_FILE="${SCRIPT_DIR}/kdr.pid"
LOG_FILE="${SCRIPT_DIR}/logs/kdr_$(date +%Y%m%d_%H%M%S).log"

# Function to stop the service
stop_kdr() {
    if [ ! -f "$PID_FILE" ]; then
        echo -e "${RED}✗ No KDR service is running${NC}"
        exit 1
    fi

    local PID=$(cat "$PID_FILE")

    if ! ps -p $PID > /dev/null 2>&1; then
        echo -e "${RED}✗ Process $PID is not running${NC}"
        rm -f "$PID_FILE"
        exit 1
    fi

    echo -e "${YELLOW}Stopping KDR service (PID: $PID)...${NC}"
    kill -TERM $PID

    # Wait for process to terminate
    local count=0
    while ps -p $PID > /dev/null 2>&1; do
        sleep 1
        count=$((count + 1))
        if [ $count -ge 30 ]; then
            echo -e "${RED}✗ Process did not terminate gracefully${NC}"
            kill -KILL $PID
            sleep 2
            break
        fi
    done

    rm -f "$PID_FILE"

    if ps -p $PID > /dev/null 2>&1; then
        echo -e "${RED}✗ Failed to stop KDR service${NC}"
        exit 1
    else
        echo -e "${GREEN}✓ KDR service stopped${NC}"
    fi
}

# Parse command line arguments
if [ "$1" == "stop" ]; then
    stop_kdr
    exit 0
fi

# Parse arguments for the main process
ARGS=()
for arg in "$@"; do
    # Skip unsupported short flags
    if [ "$arg" == "-d" ]; then
        echo -e "${YELLOW}Note: Use --debug (not -d) for debug mode${NC}"
        continue
    fi
    # Include supported flags
    if [ "$arg" == "--debug" ]; then
        ARGS+=("$arg")
        echo -e "${YELLOW}Debug mode enabled${NC}"
    else
        ARGS+=("$arg")
    fi
done

# Set PYTHONPATH to include the project root for module imports
export PYTHONPATH="${PYTHONPATH}:$(pwd)"

# Start the process in background and redirect output to log file
echo -e "${GREEN}Starting KDR Data Ingestion Pipeline & Real-Time Dashboard${NC}"
echo -e "${BLUE}  ➔ Real-Time Dashboard: http://localhost:8001${NC}"
echo -e "${YELLOW}  (Open this URL in Chrome or Firefox to monitor live data & decisions)${NC}"
echo ""

# Run the main process in background
nohup .venv/bin/python src/scraper.py "${ARGS[@]}" >> "$LOG_FILE" 2>&1 &
PID=$!

# Save PID
echo $PID > "$PID_FILE"

# Wait a moment to verify process started
sleep 2

if ps -p $PID > /dev/null; then
    echo -e "${GREEN}✓ KDR service started successfully (PID: $PID)${NC}"
    echo -e "${GREEN}✓ Log file: $LOG_FILE${NC}"
    echo ""
    echo -e "${YELLOW}To check status, run: tail -f $LOG_FILE${NC}"
    echo -e "${YELLOW}To stop the service, run: ./start.sh stop${NC}"
else
    echo -e "${RED}✗ Failed to start KDR service${NC}"
    echo -e "${RED}Check log file for details: $LOG_FILE${NC}"
    exit 1
fi

exit 0