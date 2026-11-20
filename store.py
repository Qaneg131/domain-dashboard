#!/usr/bin/env python3
"""Luu cau hinh local (config.json) — token/key CHI NAM O MAY NAY, khong
commit len git (da co trong .gitignore).

Cau truc:
  cf_token, cf_account_id   - Cloudflare API token + account ID
  nc_user, nc_api_key, nc_client_ip - Namecheap API
  domains                   - danh sach domain quan ly
  presets                   - {ten: [{type, name, content, proxied}]}
  default_preset            - preset mac dinh khi bam Setup
"""
import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"
STATUS_FILE = BASE_DIR / "status.json"

DEFAULT_CONFIG = {
    # Mac dinh de trong — khong luu bat cu thong tin nao cua user len git.
    # Ai clone ve thi tu nhap token/key/ID/domain/sheet o may local, luu
    # vao config.json (da co trong .gitignore, khong commit).
    "cf_token": "",
    "cf_account_id": "",
    "nc_user": "",
    "nc_api_key": "",
    "nc_client_ip": "",
    "domains": [],
    # Domain user da xoa khoi bang quan ly — tai danh sach tai khoan se
    # khong tu dua lai (chi them tay moi vao).
    "unmanaged": [],
    "presets": {
        "web": [
            {"type": "A", "name": "@", "content": "1.11.11.11", "proxied": True},
            {"type": "A", "name": "www", "content": "1.11.11.11", "proxied": True},
        ],
    },
    "default_preset": "web",
    # Email preset — ap trong buoc Setup (sau records). 'info@' = info@<domain>,
    # '*' = catch-all. 'to' la dia chi nhan that (vd Gmail) — phai nhap
    # thi buoc email moi chay; dia chi nhan phai duoc xac minh (bam link
    # trong mail) thi rule moi forward duoc.
    "email_preset": {
        "enabled": True,
        "rules": [
            {"match": "info@", "to": ""},
            {"match": "*", "to": ""},
        ],
    },
    # HTTPS preset — ap trong buoc Setup (buoc 5). Mac dinh theo y user:
    # SSL Flexible (origin chua co chung chi hop le) + Always Use HTTPS bat
    # + Minimum TLS 1.2 + Auto HTTPS Rewrites bat.
    "https_preset": {
        "enabled": True,
        "ssl": "flexible",
        "always_https": "on",
        "min_tls_version": "1.2",
        "rewrites": "on",
    },
    # Google Sheet — doc CSV publish (chi doc 3 cot B/H/J tu start_row
    # tro xuong). Ghi lai cot J ("da set up") can Sheets API + service
    # account: sheet_id = ID trong link edit cua sheet, sa_key = duong
    # dan file JSON key cua service account (da duoc share sheet quyen
    # Editor). Can them: pip install google-auth.
    "sheet_csv_url": "",
    "sheet_start_row": 1,
    "sheet_id": "",
    "sheet_sa_key": "",
    # Rule map cot H "Loai web" (ten trong sheet) sang preset record.
    # User them/sua/xoa/doi ten tuy y o muc Google Sheet — khong can xoa
    # tao lai.
    "sheet_rules": [],
    # Hang cho duyet (domain xung dot preset: sheet muon preset khac voi
    # preset domain dang dung) + domain da tu choi (khong dua lai nua).
    "sheet_review": [],
    "sheet_ignored": [],
    # Tu dong setup: bat thi server tu doc sheet moi N phut (mac dinh 5)
    # va setup cac dong sach 'Can setup' — chi can them domain vao sheet
    # roi doi cot J thanh 'Can setup' la tu chay, khong can bam nut.
    # Domain xung dot preset thi chi dua vao hang cho duyet, khong tu setup.
    "sheet_auto": False,
    "sheet_auto_interval": 5,
    # Auto-renew bang Chrome (chrome_autorenew.py nam ngay trong thu muc
    # Dashboard): ho so Chrome mac dinh dung + thu muc User Data cua
    # Chrome (de trong thi tu tim theo he dieu hanh). chrome_profile la
    # ten thu muc ho so da chon o tab Auto-renew (vd 'Default', 'Profile 1').
    "chrome_profile_dir": "",
    "chrome_profile": "",
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        saved = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if isinstance(saved, dict):
            cfg.update(saved)
    except Exception:
        pass
    # Dam bao cac khoa list/dict luon co dang dung.
    cfg.setdefault("domains", [])
    cfg.setdefault("presets", {})
    return cfg


def save_config(cfg):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False),
                           encoding="utf-8")
    return cfg


def load_status():
    try:
        st = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        return st if isinstance(st, dict) else {}
    except Exception:
        return {}


def save_status(st):
    STATUS_FILE.write_text(json.dumps(st, indent=2, ensure_ascii=False),
                           encoding="utf-8")
    return st
