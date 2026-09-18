#!/bin/bash

# KDR Startup Script
# Checks environment variables, sets up venv, and starts the scraper process

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
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

# Step 4: Run the Process
echo ""
echo -e "${YELLOW}[4/4]${NC} Starting KDR process..."

# Parse arguments
ARGS=()
for arg in "$@"; do
    if [ "$arg" == "-d" ]; then
        ARGS+=("--debug")
        echo -e "${YELLOW}Debug mode enabled${NC}"
    elif [ "$arg" == "--debug" ]; then
        ARGS+=("--debug")
        echo -e "${YELLOW}Debug mode enabled${NC}"
    else
        ARGS+=("$arg")
    fi
done

echo ""
echo -e "${GREEN}═══════════════════════════════════════════${NC}"
echo -e "${GREEN}Starting KDR Data Ingestion Pipeline${NC}"
echo -e "${GREEN}═══════════════════════════════════════════${NC}"
echo ""

# Run the main script
# Set PYTHONPATH to include the project root for module imports
export PYTHONPATH="${PYTHONPATH}:$(pwd)"
python src/scraper.py "${ARGS[@]}"