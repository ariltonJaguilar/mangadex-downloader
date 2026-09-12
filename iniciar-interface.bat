@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto check_dependencies

echo Preparando o MangaDex Downloader pela primeira vez...
where py >nul 2>nul
if not errorlevel 1 set "MANGADEX_BOOTSTRAP=py -3"
if defined MANGADEX_BOOTSTRAP goto create_environment

where python >nul 2>nul
if not errorlevel 1 set "MANGADEX_BOOTSTRAP=python"
if defined MANGADEX_BOOTSTRAP goto create_environment

echo.
echo Python 3 nÃ£o foi encontrado. Instale-o por https://www.python.org/downloads/
echo Durante a instalaÃ§Ã£o, marque a opÃ§Ã£o "Add Python to PATH".
pause
exit /b 1

:create_environment
%MANGADEX_BOOTSTRAP% -m venv ".venv"
if errorlevel 1 goto setup_failed

:check_dependencies
".venv\Scripts\python.exe" -c "import requests_doh,tqdm,pathvalidate,packaging,jwt,bs4,PIL,chardet,py7zr,orjson,lxml,authlib" >nul 2>nul
if not errorlevel 1 goto launch

echo Instalando os componentes necessÃ¡rios. Isso acontece apenas na primeira abertura.
echo Ã‰ necessÃ¡rio estar conectado Ã  internet...
".venv\Scripts\python.exe" -m pip install -r requirements.txt -r requirements-optional.txt
if errorlevel 1 goto setup_failed

".venv\Scripts\python.exe" -m pip check
if errorlevel 1 goto setup_failed

:launch
if /i "%~1"=="--setup-only" exit /b 0
start "" ".venv\Scripts\pythonw.exe" "%~dp0interface.pyw"
exit /b 0

:setup_failed
echo.
echo NÃ£o foi possÃ­vel concluir a instalaÃ§Ã£o automÃ¡tica.
echo Verifique sua conexÃ£o com a internet e execute este arquivo novamente.
pause
exit /b 1
