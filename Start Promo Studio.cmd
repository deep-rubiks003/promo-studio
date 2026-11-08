@echo off
cd /d "%~dp0"
python -c "import fastapi,uvicorn,reportlab" >nul 2>&1
if errorlevel 1 (
  python -m pip install -r requirements.txt
  if errorlevel 1 (pause & exit /b 1)
)
python server.py --open
pause
