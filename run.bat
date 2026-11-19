@echo off
REM Domain Dashboard — chay server local roi mo trinh duyet.
cd /d "%~dp0"
REM Cai google-auth neu chua co (chi can cho tinh nang ghi cot J cua Google Sheet).
python -c "import google.auth" 2>nul || python -m pip install google-auth
start "" /min cmd /c "python app.py"
timeout /t 2 /nobreak >nul
start http://127.0.0.1:8787
echo Dashboard chay o http://127.0.0.1:8787 — tat cua so python app.py de dung.
