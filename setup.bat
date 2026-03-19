@echo off
chcp 65001 >nul
echo ========================================
echo   Reclamation Monitor System Setup
echo ========================================
echo.

cd /d "%~dp0"

REM Check if .env exists
if exist .env (
    echo [OK] .env already exists
) else (
    echo [SETUP] Creating .env from template...
    copy .env.example .env >nul
    echo [DONE] Created .env - EDIT IT with real passwords!
    echo.
    echo IMPORTANT: Open .env and fill in:
    echo   - IMAP_PASSWORD
    echo   - SMTP_PASSWORD
    echo   - BITRIX24_WEBHOOK
    echo.
    pause
    notepad .env
)

REM Check Ollama
echo.
echo [CHECK] Ollama status...
ollama --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Ollama not found!
    echo Install from: https://ollama.com/download
    echo Or run: winget install Ollama.Ollama
    pause
    exit /b 1
)
echo [OK] Ollama installed

REM Check if model exists
echo.
echo [CHECK] Qwen3 model...
ollama list | findstr "qwen3:30b-a3b" >nul 2>&1
if errorlevel 1 (
    echo [SETUP] Model qwen3:30b-a3b not found. Downloading...
    echo This will download ~18GB. Please wait...
    echo.
    ollama pull qwen3:30b-a3b
    if errorlevel 1 (
        echo [ERROR] Failed to download model!
        pause
        exit /b 1
    )
    echo [DONE] Model downloaded
) else (
    echo [OK] Model qwen3:30b-a3b exists
)

REM Check Ollama API
echo.
echo [CHECK] Ollama API...
curl -s http://localhost:11434/api/tags >nul 2>&1
if errorlevel 1 (
    echo [WARN] Ollama API not responding. Starting Ollama...
    start "" ollama serve
    timeout /t 5 >nul
)
echo [OK] Ollama API ready

REM Create venv if not exists
echo.
if exist venv (
    echo [OK] Virtual environment exists
) else (
    echo [SETUP] Creating virtual environment...
    python -m venv venv
    echo [DONE] Created venv
)

REM Install Python deps in venv
echo.
echo [SETUP] Installing Python dependencies...
call venv\Scripts\pip install --upgrade pip >nul 2>&1
call venv\Scripts\pip install python-dotenv requests PyPDF2 python-docx openpyxl chardet pandas docx2txt python-pptx >nul 2>&1
echo [OK] Dependencies installed

echo.
echo ========================================
echo   Setup complete!
echo ========================================
echo.
echo Next steps:
echo 1. Activate venv: venv\Scripts\activate
echo 2. Run: python email_processor_improved.py
echo.
echo Or run directly:
echo   venv\Scripts\python email_processor_improved.py
echo.
pause
