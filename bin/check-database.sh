#!/bin/bash

# Database Check Script for KDR Trading System
# This script validates database connectivity and creates required tables

set -e

# Configuration
DATABASE_URL="${DATABASE_URL:-postgresql://user:password@localhost:5432/kdr}"
# The application needs psycopg2, which is installed only in the project's own
# virtualenv, so run it with that interpreter rather than a versioned binary.
KDR_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_CMD="${KDR_ROOT}/.venv/bin/python"
SCRIPT_PATH="${SCRIPT_PATH:-$PWD}"

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

# The application needs psycopg2, which lives only in the project's own virtualenv.
if ! command -v "$PYTHON_CMD" &> /dev/null; then
    print_error "No $PYTHON_CMD: create the virtualenv first (bin/start.sh does it)."
    exit 1
fi

# Check for required Python packages
print_status "Checking Python dependencies..."
$PYTHON_CMD -c "import psycopg2" 2>/dev/null || {
    print_warning "psycopg2 is not installed. Database operations may fail."
    print_warning "Run: $PYTHON_CMD -m pip install psycopg2-binary"
}

# Test database connection
print_status "Testing database connection..."
$PYTHON_CMD -c "
import sys
try:
    from src.database import DatabaseManager
    db = DatabaseManager()
    conn = db.connection.get_engine()
    conn.connect()
    print('✓ Database connection successful')
    sys.exit(0)
except Exception as e:
    print(f'✗ Database connection failed: {e}')
    sys.exit(1)
" || {
    print_error "Database connection test failed."
    print_error "Please check your DATABASE_URL and ensure PostgreSQL is running."
    exit 1
}

# Create database tables if needed
print_status "Setting up database schema..."
$PYTHON_CMD -c "
from src.database import DatabaseManager
db = DatabaseManager()
db.connection.init_db()
print('✓ Database tables created/verified')
" || {
    print_error "Failed to create database tables."
    exit 1
}

print_status "Database check completed successfully!"
echo ""
print_status "Your database is ready for KDR trading operations."
print_status "You can now run: ./bin/local-start.sh"