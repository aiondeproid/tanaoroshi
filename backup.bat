@echo off
rem Back up the database. Run this from Task Scheduler once a day.
rem Keep this file ASCII-only with CRLF line endings (cmd.exe misreads UTF-8/LF).
cd /d "%~dp0"
".venv\Scripts\python.exe" tools\backup.py >> data\backup.log 2>&1
exit /b %errorlevel%
