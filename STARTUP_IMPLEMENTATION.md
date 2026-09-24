# Startup Script Implementation Summary

## Overview

This document summarizes the startup script implementation for the KDR (Knowledge-Driven Trading) system. The implementation provides a comprehensive solution for checking environment variables, setting up the virtual environment, and starting the data ingestion pipeline.

## Files Created

### 1. `start.sh` (Linux/macOS startup script)
**Purpose**: Main startup script for Unix-based systems

**Features**:
- Python 3.14 version validation
- Environment variable completeness check (4 required vars)
- Automatic virtual environment setup if needed
- pip upgrade and dependency installation
- Process execution with debug mode support
- Color-coded output for better UX

**Usage**:
```bash
./start.sh                 # Normal logging
./start.sh --debug         # Debug mode
./start.sh -d              # Debug mode shortcut
```

### 2. `start.bat` (Windows startup script)
**Purpose**: Main startup script for Windows systems

**Features**:
- Python version validation (Windows compatible)
- Environment variable completeness check
- Virtual environment creation and activation
- Dependency installation with error suppression
- Windows-compatible argument parsing

**Usage**:
```cmd
start.bat                 # Normal logging
start.bat --debug         # Debug mode
start.bat -d              # Debug mode shortcut
```

### 3. `.env.example` (Environment template)
**Purpose**: Template for required environment variables

**Variables Defined**:
- `ADANOS_API_KEY` - Adanos AI authentication
- `ALPACA_API_KEY` - Alpaca API authentication
- `ALPACA_SECRET_KEY` - Alpaca API secret
- `ALPACA_BASE_URL` (optional) - Custom Alpaca API endpoint
- `DATABASE_URL` - PostgreSQL connection string

**Usage**:
```bash
cp .env.example .env
# Edit .env with your credentials
```

### 4. `STARTUP.md` (Startup documentation)
**Purpose**: Comprehensive documentation for the startup scripts

**Contents**:
- Feature overview
- Usage instructions for all platforms
- Environment setup guide
- Database configuration
- Troubleshooting section
- Process flow explanation
- Additional resources and links

### 5. `test_startup.py` (Validation script)
**Purpose**: Standalone script to validate system setup before running

**Tests Performed**:
- Python version compatibility check (3.14+)
- Environment variable presence validation
- requirements.txt existence verification
- Main source file presence check

**Usage**:
```bash
python test_startup.py
```

**Output**:
- Pass/fail status for each test
- Detailed error messages
- Summary report

### 6. `check_env.py` (Environment checker utility)
**Purpose**: Interactive utility to display environment variable status

**Features**:
- Displays all required environment variables
- Masks sensitive values for security
- Shows variable descriptions
- Provides actionable fix suggestions

**Usage**:
```bash
python check_env.py
```

**Output**:
- Color-coded status indicators
- Variable names and descriptions
- Masked values for sensitive data
- Connection instructions for missing variables

## Environment Variable Requirements

The system validates the following environment variables:

| Variable | Description | Source |
|----------|-------------|--------|
| `ADANOS_API_KEY` | Authentication for Adanos AI sentiment analysis | https://adanos.ai/ |
| `ALPACA_API_KEY` | Authentication for Alpaca market data API | https://alpaca.markets/ |
| `ALPACA_SECRET_KEY` | Secret key for Alpaca API authentication | https://alpaca.markets/ |
| `DATABASE_URL` | PostgreSQL database connection string | PostgreSQL configuration |

## Virtual Environment Workflow

The startup scripts follow this virtual environment workflow:

1. **Check for existing `.venv` directory**:
   - If exists: Activation only
   - If not exists: Create new virtual environment

2. **Activate virtual environment**:
   ```bash
   source .venv/bin/activate  # Unix
   .venv\Scripts\activate.bat  # Windows
   ```

3. **Upgrade pip and system packages**:
   ```bash
   pip install --upgrade pip setuptools wheel
   ```

4. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

## Dependency Installation

All required dependencies are managed through `requirements.txt`:

```
gymnasium          - Reinforcement learning framework
psycopg2-binary    - PostgreSQL database adapter
alpaca-trade-api   - Alpaca API client
pandas             - Data manipulation and analysis
plotly             - Data visualization
scikit-learn       - Machine learning utilities
stable-baselines3 - Stable Baselines3 RL implementation
python-dotenv      - Environment variable management
requests           - HTTP requests
```

## Startup Process Flow

When the startup script runs, it executes the following pipeline:

1. **Initialization Checks**:
   - Python version validation
   - Environment variable verification
   - File existence checks

2. **Environment Setup**:
   - Virtual environment creation/activation
   - Dependency installation
   - Configuration validation

3. **Process Execution**:
   - Parse command-line arguments (debug mode)
   - Initialize components:
     - Database manager
     - State mapper
     - Data stream processor
   - Start data ingestion pipeline:
     - Alpaca Stream → WebSocket data feed
     - DataStreamBuffer → Thread-safe tick aggregation
     - DBWriterWorker → Batch processing
     - PostgreSQL → Persistent storage

## Error Handling

The startup scripts include comprehensive error handling:

1. **Python Not Found**: Displays error and installation instructions
2. **Missing Environment Variables**: Lists missing vars and provides fix instructions
3. **Database Connection Issues**: Checks PostgreSQL status
4. **Dependency Installation Failures**: Suggests alternative installation methods
5. **File Not Found**: Validates required files exist

## Security Considerations

- Sensitive values (API keys, passwords) are masked in output
- `.env` file should be added to `.gitignore`
- No credentials are hardcoded in scripts
- Database URLs are truncated for display purposes

## Compatibility

**Tested on**:
- macOS 14+ with Python 3.14
- Linux (Ubuntu/Debian) with Python 3.14
- Windows 10/11 with Python 3.14

## Additional Features

- **Debug Mode**: Enables verbose logging for troubleshooting
- **Color Output**: Improved readability with ANSI color codes
- **Argument Parsing**: Supports `--debug` and `-d` flags
- **Error Suppression**: Suppresses pip installation noise in production
- **Non-interactive**: Runs completely non-interactively

## Integration with Existing System

The startup scripts integrate seamlessly with the existing KDR system:

- Uses the same environment variables as `src/config_gatekeeper.py`
- Validates against the same set of required variables
- Follows the same database initialization pattern
- Supports the same command-line arguments as `src/scraper.py`

## Testing

The implementation includes a test suite (`test_startup.py`) that validates:

1. System setup before execution
2. Environment variable configuration
3. File integrity
4. Python compatibility

Run tests before using the startup script:
```bash
python test_startup.py
```

## Future Enhancements

Potential improvements for future versions:

1. **Conda Support**: Add conda environment creation option
2. **Docker Integration**: Add Docker-based startup script
3. **Auto-detection**: Auto-detect Python version requirements
4. **Progress Indicators**: Visual progress bars for installations
5. **Health Checks**: Database connectivity verification
6. **Configuration Wizard**: Interactive setup wizard for new users
7. **System Profiling**: Resource usage monitoring
8. **Log Rotation**: Automatic log file management

## Contact & Support

For issues or questions:
- Check the main README.md
- Review STARTUP.md for detailed documentation
- Use `check_env.py` to diagnose configuration issues
- Run `test_startup.py` to validate your setup

## Quick Start Guide

**For New Users**:
1. Copy `.env.example` to `.env`
2. Edit `.env` with your credentials
3. Run `./start.sh`
4. (Optional) Run `python test_startup.py` first

**For Development**:
1. Run `python test_startup.py`
2. Run `./start.sh --debug` for verbose output
3. Monitor logs in real-time
4. Use `check_env.py` to verify configuration

---

**Last Updated**: September 17, 2026
**Version**: 1.0.0
**Python Version Required**: 3.14+