@echo off
REM Domain Dashboard — chay server local roi mo trinh duyet.
cd /d "%~dp0"
start "" /min cmd /c "python app.py"
timeout /t 2 /nobreak >nul
start http://127.0.0.1:8787
echo Dashboard chay o http://127.0.0.1:8787 — tat cua so python app.py de dung.
