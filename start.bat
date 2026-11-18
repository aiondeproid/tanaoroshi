@echo off
rem Start the tanaoroshi (inventory) web app. Press Ctrl+C to stop.
rem Keep this file ASCII-only with CRLF line endings (cmd.exe misreads UTF-8/LF).
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo First-time setup: installing libraries...
  python -m venv .venv
  if errorlevel 1 goto error
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 goto error
)

rem Admin PIN. Change this before real use.
if "%TANAOROSHI_ADMIN_PIN%"=="" set TANAOROSHI_ADMIN_PIN=1234

rem Mail to the next approver. notes = send from HCL Notes on this PC (keep Notes running and logged in).
rem outlook = send from Outlook on this PC. none = send no mail.
if "%TANAOROSHI_MAIL_VIA%"=="" set TANAOROSHI_MAIL_VIA=notes
rem To use a mail server instead, set TANAOROSHI_MAIL_VIA=smtp and e.g.
rem   set TANAOROSHI_SMTP_HOST=smtp.example.local
rem   set TANAOROSHI_SMTP_PORT=25
rem   set TANAOROSHI_MAIL_FROM=tanaoroshi@example.local
rem Link written in the mail:
if "%TANAOROSHI_URL%"=="" set TANAOROSHI_URL=http://%COMPUTERNAME%:8000

echo.
echo Open  http://%COMPUTERNAME%:8000  on a tablet or phone.
echo Press Ctrl+C to stop.
echo.
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000
pause
goto :eof

:error
echo Setup failed. Check that Python is installed and on PATH.
pause
