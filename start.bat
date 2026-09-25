@echo off
chcp 65001 > nul
rem 原料棚卸アプリを起動する。止めるときはこの画面で Ctrl+C。
cd /d %~dp0
if not exist .venv (
  echo 初回セットアップ中...
  python -m venv .venv || goto :error
  .venv\Scripts\python -m pip install -r requirements.txt || goto :error
)
rem 管理者PIN。必ず変更すること
if "%TANAOROSHI_ADMIN_PIN%"=="" set TANAOROSHI_ADMIN_PIN=1234
echo.
echo タブレット・スマホから http://%COMPUTERNAME%:8000 を開いてください
echo.
.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
goto :eof
:error
echo セットアップに失敗しました
pause
