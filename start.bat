@echo off
REM ============================================================================
REM PrivacyScrub — Windows Launcher
REM Self-Hosted Privacy Removal Platform
REM
REM Usage: Double-click start.bat or run from command prompt
REM        SET PORT=8080 && start.bat   (custom port)
REM ============================================================================

echo.
echo   ========================================================
echo        PrivacyScrub v1.0.0
echo        Self-Hosted Privacy Removal Platform
echo   ========================================================
echo.

REM Default port
if "%PORT%"=="" set PORT=5000

REM Check Python
echo [1/4] Checking Python...
python --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Python 3.10+ is required but not found.
    echo Install Python from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation.
    pause
    exit /b 1
)
python -c "import sys; exit(0 if sys.version_info >= (3,10) else 1)" 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Python 3.10+ required.
    pause
    exit /b 1
)
echo   Python found.

REM Create virtual environment
echo [2/4] Setting up virtual environment...
if not exist "venv" (
    echo   Creating virtual environment...
    python -m venv venv
    echo   Virtual environment created.
) else (
    echo   Virtual environment exists.
)

REM Activate venv
call venv\Scripts\activate

REM Install dependencies
echo [3/4] Installing dependencies...
pip install --upgrade pip --quiet 2>nul
pip install -r requirements.txt --quiet 2>nul
echo   Dependencies installed.

REM Create directories
echo [4/4] Preparing directories...
if not exist "reports" mkdir reports
if not exist "legal_templates\state_specific" mkdir legal_templates\state_specific
echo   Directories ready.

REM Start server
echo.
echo   Starting PrivacyScrub...
echo   Dashboard:  http://localhost:%PORT%
echo   API:        http://localhost:%PORT%/api/health
echo.
echo   Press Ctrl+C to stop the server.
echo.

python app.py