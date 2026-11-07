@echo off
chcp 65001 >nul
rem Запуск прототипа на Windows: создаёт venv, ставит зависимости, стартует сервер.
cd /d "%~dp0"

set "PY=py"
where py >nul 2>nul || set "PY=python"
where %PY% >nul 2>nul || (
  echo Python не найден. Установите Python 3.11+ с https://www.python.org/downloads/
  echo и отметьте "Add python.exe to PATH".
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Создаю виртуальное окружение...
  %PY% -m venv .venv || (pause & exit /b 1)
)

echo Устанавливаю зависимости...
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt || (pause & exit /b 1)

if "%PORT%"=="" set "PORT=8000"
echo.
echo Сервер запускается: http://localhost:%PORT%   (остановить: Ctrl+C)
echo.
start "" "http://localhost:%PORT%"
".venv\Scripts\python.exe" -m uvicorn app.main:app --port %PORT%
pause
