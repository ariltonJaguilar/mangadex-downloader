@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Crie o ambiente e instale as dependencias seguindo o README.md.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" main.py %*
if errorlevel 1 pause
