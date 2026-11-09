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
if "%TBO_DEMO_MODE%"=="" set "TBO_DEMO_MODE=1"
set "TBO_HOST=127.0.0.1"
set "TBO_PORT=%PORT%"
echo.
echo Сервер запускается: http://localhost:%PORT%   (остановить: Ctrl+C)
echo При первом запуске ниже появится одноразовый КОД НАСТРОЙКИ - введите его в браузере и создайте администратора.
echo.
start "" "http://localhost:%PORT%"
".venv\Scripts\python.exe" -m app.serve
pause
