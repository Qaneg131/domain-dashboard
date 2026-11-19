#!/bin/sh
# Domain Dashboard — chay server local.
cd "$(dirname "$0")"
# Cai google-auth neu chua co (chi can cho tinh nang ghi cot J cua Google Sheet).
python3 -c "import google.auth" 2>/dev/null || pip3 install google-auth
python3 app.py
