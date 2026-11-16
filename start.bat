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
  echo [ZHIYIN] NOTE: .env not found. Credentials may still come from ZHIYIN_ENV_FILE
  echo [ZHIYIN]       or .zhiyin_key_path. qwen stays OFF by default; offline demo works.
  echo [ZHIYIN]       To enable: copy .env.example to .env, fill QWEN_API_KEY, set LLM_ENABLED=true.
)

echo [ZHIYIN] Phase 3: A1 coach / A2 browser voice / A3 GitHub metadata / A4 hybrid RRF.
echo [ZHIYIN] Optional components (BGE / LangGraph / stores / Celery) degrade gracefully.
echo [ZHIYIN] If WorkBuddy's bundled Python was upgraded it may wipe site-packages;
echo [ZHIYIN] restore with: python -m pip install -r requirements.txt -r requirements-optional.txt
echo [ZHIYIN] Open http://127.0.0.1:8848/  (Ctrl+C to stop)
python server.py %*
