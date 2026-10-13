@echo off
REM KDR Startup Script for Windows
REM Checks environment variables, sets up venv, and starts the scraper process

SETLOCAL EnableDelayedExpansion

echo ========================================
echo        KDR System Startup
echo ========================================
echo.

REM Step 1: Check Python Version
echo [1/4] Checking Python version...
python --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo Error: Python is not installed or not in PATH.
    exit /b 1
)

FOR /F "tokens=2" %%G IN ('python --version 2^>^&1') DO SET PYTHON_VERSION=%%G
echo [OK] Python version: %PYTHON_VERSION%

REM Step 2: Check Environment Variables
echo.
echo [2/4] Checking environment variables...

SET REQUIRED_VARS=ADANOS_API_KEY ALPACA_API_KEY ALPACA_SECRET_KEY DATABASE_URL
SET MISSING_VARS=0

FOR %%V IN (%REQUIRED_VARS%) DO (
    IF NOT defined %%V (
        echo [X] Missing: %%V
        SET /A MISSING_VARS+=1
    )
)

IF %MISSING_VARS% GTR 0 (
    echo.
    echo [ERROR] Missing required environment variables:
    for %%V in (%REQUIRED_VARS%) do if not defined %%V echo   - %%V
    echo.
    echo Please set up your .env file with all required variables.
    echo You can find an example .env template in the repository.
    exit /b 1
)

echo [OK] All required environment variables are present

REM Step 3: Setup/Activate Virtual Environment
echo.
echo [3/4] Setting up virtual environment...

IF NOT exist ".venv" (
    echo Creating new virtual environment at .venv...
    python -m venv .venv
    echo [OK] Virtual environment created
) ELSE (
    echo [OK] Virtual environment already exists
)

REM Activate virtual environment
echo Activating virtual environment...
call .venv\Scripts\activate.bat

REM Upgrade pip
echo Upgrading pip...
pip install --upgrade pip setuptools wheel >nul 2>&1

REM Install/update requirements
echo Installing dependencies...
pip install -r requirements.txt >nul 2>&1
echo [OK] Dependencies installed

REM Step 4: Run the Process
echo.
echo [4/4] Starting KDR process...

REM Parse arguments
SET DEBUG_MODE=
IF "%1"=="--debug" SET DEBUG_MODE=--debug
IF "%1"=="-d" SET DEBUG_MODE=--debug

IF NOT "%DEBUG_MODE%"=="" (
    echo Debug mode enabled
    echo.
)

echo ========================================
echo Starting KDR Data Ingestion Pipeline
echo ========================================
echo.

REM Run the main script
REM Set PYTHONPATH to include the project root for module imports
set PYTHONPATH=%PYTHONPATH%;%CD%
python src/scraper.py %DEBUG_MODE% %*

ENDLOCAL