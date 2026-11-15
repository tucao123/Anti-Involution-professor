@echo off
setlocal
cd /d "%~dp0"

echo [ZHIYIN] Starting local BFF...

where python >nul 2>nul
if errorlevel 1 (
  echo [ZHIYIN] ERROR: Python not found in PATH. Install Python 3.10+ first.
  exit /b 1
)

python -c "import fastapi, uvicorn, pydantic, cryptography" >nul 2>nul
if errorlevel 1 (
  echo [ZHIYIN] Installing runtime dependencies from requirements.txt ...
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo [ZHIYIN] ERROR: dependency installation failed.
    exit /b 1
  )
)

if not exist ".env" (
  echo [ZHIYIN] NOTE: .env not found. Live LLM is disabled; offline demo mode still works.
  echo [ZHIYIN]       To enable: copy .env.example to .env and fill the deepseek key.
)

echo [ZHIYIN] Open http://127.0.0.1:8848/  (Ctrl+C to stop)
python server.py %*
