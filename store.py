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
    "cf_token": "",
    "cf_account_id": "c30bace6d8d169fb77ae278b7d0fc82a",
    "nc_user": "tuanah252",
    "nc_api_key": "",
    "nc_client_ip": "",
    "domains": ["elvellia.com", "welvella.com"],
    "presets": {
        "web": [
            {"type": "A", "name": "@", "content": "1.11.11.11", "proxied": True},
            {"type": "A", "name": "www", "content": "1.11.11.11", "proxied": True},
        ],
    },
    "default_preset": "web",
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
