@echo off
setlocal
set "DIR=%~dp0"
if exist "%DIR%venv\Scripts\python.exe" (
    "%DIR%venv\Scripts\python.exe" -u "%DIR%ytget_host.py" %*
) else if exist "%DIR%.venv\Scripts\python.exe" (
    "%DIR%.venv\Scripts\python.exe" -u "%DIR%ytget_host.py" %*
) else (
    python -u "%DIR%ytget_host.py" %*
)
endlocal
