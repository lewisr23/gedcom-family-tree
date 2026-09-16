@echo off
setlocal

if not exist .venv (
    echo Creating virtual environment...
    python -m venv .venv || goto :error
)

echo Installing dependencies if needed...
call .venv\Scripts\python.exe -m pip install -r requirements.txt || goto :error

echo Starting server...
echo Open http://localhost:8000 in your browser.
call .venv\Scripts\python.exe -m uvicorn app.main:app --reload
goto :eof

:error
echo.
echo Setup failed. Check that python is on your PATH.
exit /b 1
