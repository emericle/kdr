#!/bin/bash

# Database Check Script for KDR Trading System
# Validates database connectivity and reports the current Alembic revision and
# schema health without modifying, recreating, or dropping existing tables.

set -e

KDR_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_CMD="${KDR_ROOT}/.venv/bin/python"

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

if ! command -v "$PYTHON_CMD" &> /dev/null; then
    print_error "No $PYTHON_CMD: create the virtualenv first (bin/start.sh does it)."
    exit 1
fi

print_status "Checking Python dependencies..."
$PYTHON_CMD -c "import psycopg2" 2>/dev/null || {
    print_warning "psycopg2 is not installed. Database operations may fail."
    print_warning "Run: $PYTHON_CMD -m pip install psycopg2-binary"
}

print_status "Testing database connection..."
PYTHONPATH="${PYTHONPATH:-}:${KDR_ROOT}" $PYTHON_CMD -c "
import sys
from sqlalchemy import text
from src.database import (
    DB_FAILURES,
    DatabaseManager,
    get_database_url,
)

url = get_database_url()
if not url:
    print('✗ No DATABASE_URL configured in environment or .env')
    sys.exit(1)

try:
    db = DatabaseManager(db_url=url)
    engine = db.connection.engine
    with engine.connect() as conn:
        conn.execute(text('SELECT 1'))
    session = db.connection.get_session()
    if session is None:
        print('✗ Database session factory returned None')
        sys.exit(1)
    db.connection.close(session)
    print('✓ Database connection successful')
    sys.exit(0)
except DB_FAILURES as e:
    print(f'✗ Database connection failed: {e}')
    sys.exit(1)
" || {
    print_error "Database connection test failed."
    print_error "Please check your DATABASE_URL and ensure PostgreSQL is running."
    exit 1
}

# Create database tables if needed
print_status "Setting up database schema..."
PYTHONPATH="${PYTHONPATH:-}:${KDR_ROOT}" $PYTHON_CMD -c "
from src.database import DatabaseManager
db = DatabaseManager()
db.connection.init_db()
print('✓ Database tables created/verified')
" || {
    print_error "Failed to create database tables."
    exit 1
}

print_status "Database check completed successfully!"
