# KDR Startup Script

This directory contains startup scripts that check all required environment variables, set up the virtual environment, and start the KDR (Knowledge-Driven Trading) process.

## Available Scripts

### Linux/macOS
- `start.sh` - Main startup script for Unix systems

### Windows
- `start.bat` - Main startup script for Windows systems

## Features

The startup scripts perform the following steps:

1. **Python Version Check** - Verifies that Python 3.14+ is installed and available
2. **Environment Variable Validation** - Checks for all required environment variables:
   - `ADANOS_API_KEY` - Authentication for Adanos AI
   - `ALPACA_API_KEY` - Authentication for Alpaca API
   - `ALPACA_SECRET_KEY` - Secret key for Alpaca API
   - `DATABASE_URL` - PostgreSQL connection string
3. **Virtual Environment Setup**:
   - Creates a virtual environment if one doesn't exist
   - Activates the virtual environment
   - Upgrades pip and installs dependencies from requirements.txt
4. **Process Startup** - Runs the KDR data ingestion pipeline

## Usage

### Basic Usage
```bash
# Run with normal logging
./start.sh

# Run with debug mode (verbose logging)
./start.sh --debug
# or
./start.sh -d
```

### Windows Usage
```cmd
REM Run with normal logging
start.bat

REM Run with debug mode (verbose logging)
start.bat --debug
REM or
start.bat -d
```

## Environment Setup

### 1. Copy Environment Template

```bash
cp .env.example .env
```

### 2. Configure Environment Variables

Edit the `.env` file and fill in your actual credentials:

```bash
# Required Variables
ADANOS_API_KEY=your_actual_adanos_key
ALPACA_API_KEY=your_actual_alpaca_key
ALPACA_SECRET_KEY=your_actual_alpaca_secret
DATABASE_URL=postgresql://username:password@localhost:5432/kdr_db
```

### 3. Database Setup

Make sure you have PostgreSQL running and create a database:

```bash
# Using PostgreSQL command line
createdb kdr_db

# Or using SQL
psql -U postgres -c "CREATE DATABASE kdr_db;"
```

## Project Requirements

### Python Version
- **Required**: Python 3.14 or greater
- **Note**: The application explicitly checks for Python 3.14

### Dependencies

All required dependencies are listed in `requirements.txt`:

- gymnasium - Reinforcement learning framework
- psycopg2-binary - PostgreSQL database adapter
- alpaca-trade-api - Alpaca API client
- pandas - Data manipulation and analysis
- plotly - Data visualization
- scikit-learn - Machine learning utilities
- stable-baselines3 - Stable Baselines implementation
- python-dotenv - Environment variable management
- requests - HTTP requests

## Troubleshooting

### Python Not Found
```bash
# On macOS with Homebrew
brew install python@3.14

# On macOS with pyenv
pyenv install 3.14
pyenv local 3.14
```

### Missing Dependencies
If pip fails to install dependencies, try:
```bash
pip install --upgrade pip
pip install --user -r requirements.txt
```

### Database Connection Issues
Ensure your `DATABASE_URL` is correct and PostgreSQL is running:
```bash
# Check PostgreSQL service
pg_isready
```

### Environment Variables Not Loading
Make sure your `.env` file is in the project root directory and has no syntax errors.

## Manual Execution

If you prefer to run the process manually (e.g., for development):

```bash
# Activate virtual environment
source .venv/bin/activate

# Run with debug mode
python src/scraper.py --debug
```

## Process Flow

The KDR system processes data in the following pipeline:

1. **Alpaca Stream** → WebSocket data feed (trades and bars)
2. **DataStreamBuffer** → Thread-safe tick aggregation
3. **DBWriterWorker** → Batch processing and state mapping
4. **PostgreSQL** → Persistent data storage

## Additional Resources

- **Main Entry Point**: `src/scraper.py`
- **Configuration Gatekeeper**: `src/config_gatekeeper.py`
- **Database Manager**: `src/database.py`
- **State Mapper**: `src/state_mapper.py`
- **Documentation**: See the main README.md for detailed system information