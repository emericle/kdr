# Local Environment Setup

## Running Local Tests

### Starting the System
To start the KDR trading system in local testing mode:

```bash
./bin/local-start.sh
```

This will:
- Validate your environment (Python 3.14, environment variables, dependencies)
- Start the system in background with debug logging
- Save the PID for later shutdown
- Create necessary directories (logs, outputs, database)

### Viewing Logs
Monitor the system output in real-time:

```bash
tail -f logs/system.log
```

### Stopping the System
Gracefully stop the running system:

```bash
./bin/local-stop.sh
```

## Prerequisites

### Environment Configuration

Before starting the system, ensure you have:

1. **Python 3.14+ installed**:
   ```bash
   brew install python@3.14
   python3.14 --version
   ```

2. **Environment variables in `.env`**:
   - `ALPACA_API_KEY` - Your Alpaca Trading API key
   - `ALPACA_SECRET_KEY` - Your Alpaca Trading API secret
   - `ADANOS_API_KEY` - Your Adanos sentiment analysis API key
   - `DATABASE_URL` - PostgreSQL connection string

3. **Required Python packages**:
   ```bash
   python3.14 -m pip install -r requirements.txt
   ```

4. **PostgreSQL database** running locally with:
   - Database: `kdr`
   - Port: 5432 (configurable in `.env`)
   - Required tables created automatically

### Manual Testing

For more control over testing, you can run the system manually:

```bash
# Start in continuous streaming mode (default behavior)
python3.14 src/scraper.py

# Start in debug mode
python3.14 src/scraper.py --debug

# Stream specific symbols continuously
python3.14 src/scraper.py --symbols AAPL,MSFT,GOOGL

# Run for a fixed duration (e.g. 60 seconds)
python3.14 src/scraper.py --duration 60

# Run a single non-continuous pass
python3.14 src/scraper.py --no-continuous

# Get help on all CLI options
python3.14 src/scraper.py --help
```

## Test Coverage

The system includes comprehensive testing:

```bash
# Run unit tests
python3.14 -m pytest tests/unit/ -v

# Run integration tests
python3.14 -m pytest tests/integration_tests.py -v

# Run all tests with coverage
python3.14 -m pytest tests/ --cov=. --cov-report=html
```

## Data Output

All trading data and reports are saved to:
- **Data logs**: `outputs/` (CSV files, Plotly charts)
- **System logs**: `logs/system.log`
- **Database**: `~/.kdr_db/` (PostgreSQL)
- **Test coverage**: `htmlcov/` (HTML reports)

## Troubleshooting

### Common Issues

**"Python 3.14 is not installed"**
- Install Python 3.14 via Homebrew or download from official sources

**".env file not found"**
- Copy `.env.template` to `.env` and fill in your credentials

**"Missing required environment variables"**
- Verify all required variables are set in `.env`

**"alpaca-trade-api is not installed"**
- Run: `python3.14 -m pip install -r requirements.txt`

**"psycopg2 is not installed"**
- Run: `python3.14 -m pip install psycopg2-binary`

**"System fails to start"**
- Check: `tail -f logs/system.log`
- Verify: All environment variables are correctly set
- Test: Database connection is working

### Manual Process Management

If the scripts don't work, you can manage the process manually:

```bash
# Start
python3.14 src/scraper.py --debug &

# Check running processes
ps aux | grep python3.14

# Kill process
kill <PID>

# Or force kill
kill -9 <PID>
```