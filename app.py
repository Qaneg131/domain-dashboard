#!/usr/bin/env python3
"""Domain Dashboard — quan ly domain Namecheap + Cloudflare BANG API,
khong can mo Chrome.

Chay:  python app.py   (hoac double-click run.bat)
Mo:    http://127.0.0.1:8787

Chuc nang:
- Bang tong quan: moi domain da set NS chua (check bang DNS that),
  zone Cloudflare status gi, record nao dang co.
- Bam Setup: tu dong tao zone Cloudflare -> doi NS o Namecheap (API)
  -> ap preset record. Khong mo trinh duyet.
- Luu preset record (vd 'web': A @ + A www) de ap nhanh cho domain moi.
- Them/xoa domain, sua preset, nhap key o muc Cai dat.

Can (nhap o muc Cai dat):
- Cloudflare API token (quyen Zone:Edit + DNS:Edit)
- Namecheap API key + whitelist IP may nay (Profile -> Tools -> API Access)
Chi chay local (127.0.0.1), token/key chi luu o config.json tren may nay.
"""
import csv
import glob
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cf_api import (CFClient, CFError, _is_email_routing_record,
                    _is_gsc_record, expand_name)
from dns_check import doh_ns, my_ip
from nc_api import NCClient, NCError, _is_rate_limit_msg
from store import load_config, save_config, load_status, save_status, BASE_DIR

PORT = int(os.environ.get("PORT", "8787"))
CF_BASE = os.environ.get("CF_API_BASE") or None
NC_BASE = os.environ.get("NC_API_BASE") or None
DOH_BASE = os.environ.get("DOH_BASE") or None

WEB_DIR = BASE_DIR / "web"


# ------------------------------------------------------------------ logic
def _cf_client(cfg):
    kw = {}
    if CF_BASE:
        kw["base"] = CF_BASE
    return CFClient(cfg.get("cf_token", ""), cfg.get("cf_account_id"), **kw)


def _nc_client(cfg):
    kw = {}
    if NC_BASE:
        kw["base"] = NC_BASE
    return NCClient(cfg.get("nc_user", ""), cfg.get("nc_api_key", ""),
                    cfg.get("nc_client_ip", ""), **kw)


def _doh(domain):
    kw = {}
    if DOH_BASE:
        kw["base"] = DOH_BASE
    return doh_ns(domain, **kw)


def _nc_configured(cfg):
    return bool(cfg.get("nc_user") and cfg.get("nc_api_key") and cfg.get("nc_client_ip"))


# ------------------------------------------------------------------ auto-renew
# API setAutoRenew cua Namecheap bao IsSuccess=true nhung toggle tren
# web khong doi (da xac minh 2026-11-20) nen auto-renew phai bam toggle
# that tren trinh duyet. Script Chrome nam NGAY TRONG thu muc Dashboard
# (chrome_autorenew.py — tach tu tool Chrome cu domain-setup-tool, user
# da xoa thu muc cu roi) nen Dashboard goi no dang subprocess, khong
# can tim tool o dau nua. Script doc trang thai toggle truoc, da BAT
# thi bo qua, chi bam khi TAT, xu ly hop thoai Privacy.
AR_SCRIPT = Path(os.environ.get("CHROME_AUTORENEW_SCRIPT") or (BASE_DIR / "chrome_autorenew.py"))
AR_REPORTS_DIR = BASE_DIR / "reports"


def _chrome_profile_dir(cfg):
    """Thu muc User Data cua Chrome (chua cac ho so Default/Profile 1...).
    Mac dinh tu tim theo he dieu hanh; user nhap tay neu Chrome de o
    cho khac (o 'Thu muc User Data Chrome' trong muc Cai dat)."""
    cand = str(cfg.get("chrome_profile_dir") or "").strip()
    if cand:
        return Path(cand).expanduser()
    home = Path.home()
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "")
        return Path(local) / "Google" / "Chrome" / "User Data" if local else home
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Google" / "Chrome"
    return home / ".config" / "google-chrome"


def _list_chrome_profiles(cfg):
    """Liet ke cac ho so Chrome trong thu muc User Data.
    Tra ve (danh_sach, last_used) voi danh_sach = [(thu_muc, ten_hien_thi)].
    Ten hien thi lay tu Local State (profile.info_cache) — chinh la ten
    hien trong menu doi ho so cua Chrome."""
    root = _chrome_profile_dir(cfg)
    profiles, last_used = [], None
    try:
        ls = root / "Local State"
        if ls.exists():
            data = json.loads(ls.read_text(encoding="utf-8"))
            prof = data.get("profile", {}) or {}
            last_used = prof.get("last_used")
            cache = prof.get("info_cache", {}) or {}
            for dirname, info in cache.items():
                if (root / dirname).is_dir():
                    profiles.append((dirname, (info or {}).get("name") or dirname))
    except Exception:
        pass
    if not profiles:
        for d in sorted(root.iterdir()) if root.exists() else []:
            if d.is_dir() and (d.name == "Default" or d.name.startswith("Profile ")):
                profiles.append((d.name, d.name))
    if last_used not in [p[0] for p in profiles]:
        last_used = profiles[0][0] if profiles else None
    return profiles, last_used


def _run_chrome_autorenew(args):
    """Chay chrome_autorenew.py (trong thu muc Dashboard) nhu subprocess,
    doi xong, tra ve (returncode, log stdout+stderr)."""
    cmd = [sys.executable, str(AR_SCRIPT)] + args
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, encoding="utf-8", errors="replace",
                          env=env, cwd=str(BASE_DIR))
    return proc.returncode, proc.stdout or ""


def _read_newest_ar_report(before):
    """Doc bao cao CSV moi nhat ma chrome_autorenew.py vua ghi (reports/
    chrome_setup_*.csv) — so voi danh sach file truoc khi chay de chi
    lay file moi. Tra ve list dict {domain, status, error}."""
    after = set(glob.glob(str(AR_REPORTS_DIR / "chrome_setup_*.csv")))
    new = sorted(after - before)
    if not new:
        return []
    try:
        with open(new[-1], encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        return [{"domain": r.get("domain", ""), "status": r.get("status", ""),
                 "error": r.get("error", "")} for r in rows]
    except Exception:
        return []


# ------------------------------------------------------------------ tinh trang Sus
# Sheet rieng ve tinh trang Sus cua domain (KHAC sheet set domain cu):
# cot B = WEB (ten domain), cot F = Tinh Trang Sus ("Da Sus" / "Xanh" /
# "Sus mail" / trong). Chi DOC de hien o tab Auto-renew, khong ghi gi
# vao sheet, khong doi gi tren domain. Doc qua link CSV xuat ban
# (Google cache ~5 phut) nen co cache trong bo nho 5 phut.
SUS_CACHE = {"at": 0.0, "url": "", "sus": {}, "error": ""}
SUS_CACHE_TTL_S = 300


def _sus_csv_url(raw):
    """Chuan hoa link sheet xuat ban thanh link CSV: pubhtml -> pub +
    output=csv; link pub chua co output thi them output=csv. Link
    khong phai Google Sheets thi giu nguyen."""
    u = str(raw or "").strip()
    if not u or "docs.google.com" not in u:
        return u
    u = u.replace("/pubhtml", "/pub")
    if "output=csv" in u:
        return u
    sep = "&" if "?" in u else "?"
    return u + sep + "output=csv"


def _fetch_sus_map(cfg):
    """Doc sheet Sus -> {domain_lower: {status, date, days}}: status =
    cot F (Tinh Trang Sus), date = cot C (Ngay ve GMC, DD/MM/YYYY),
    days = cot H (So ngay GMC). Domain trung nhau thi lay DONG CUOI
    (giong sheet set domain). Tra ve (sus_map, error). Cache 5 phut
    theo URL."""
    url = _sus_csv_url(cfg.get("sus_sheet_csv_url") or "")
    if not url:
        return {}, "chua nhap link sheet Tinh trang Sus (o tab Auto-renew)"
    now = time.time()
    if SUS_CACHE["url"] == url and now - SUS_CACHE["at"] < SUS_CACHE_TTL_S:
        return dict(SUS_CACHE["sus"]), SUS_CACHE["error"]
    sus, err = {}, ""
    try:
        import io
        req = urllib.request.Request(url, headers={"User-Agent": "domain-dashboard"})
        with urllib.request.urlopen(req, timeout=20) as r:
            text = r.read().decode("utf-8-sig", errors="replace")
        rows = list(csv.reader(io.StringIO(text)))
        for row in rows[1:]:  # bo header
            if len(row) < 6:
                continue
            d = (row[1] or "").strip().lower()
            if not d or "." not in d:
                continue
            sus[d] = {"status": (row[5] or "").strip(),
                      "date": (row[2] or "").strip(),
                      "days": (row[7] or "").strip() if len(row) > 7 else ""}
    except Exception as e:
        err = "khong doc duoc sheet Sus: " + str(e)[:150]
        sus = {}
    SUS_CACHE.update({"at": now, "url": url, "sus": dict(sus), "error": err})
    return sus, err


# ------------------------------------------------- getList Namecheap
# Han + co AutoRenew that tren web Namecheap (doc qua getList, chi
# doc). Cache 5 phut de khong goi Namecheap lien tuc khi doi trang/loc
# (1 lan doc getList phuc vu ca cot Han va cot Auto-renew).
NC_LIST_CACHE = {"at": 0.0, "info": {}, "error": ""}
NC_LIST_CACHE_TTL_S = 300


def _fetch_nc_list_info(cfg, force=False):
    """Thong tin getList cua tat ca domain trong tai khoan Namecheap ->
    {domain_lower: {"expiry": "YYYY-MM-DD"|None, "autorenew": bool}}.
    Tra ve (info, error)."""
    now = time.time()
    if not force and now - NC_LIST_CACHE["at"] < NC_LIST_CACHE_TTL_S:
        return dict(NC_LIST_CACHE["info"]), NC_LIST_CACHE["error"]
    info, err = {}, ""
    try:
        info = {d["name"]: {"expiry": d["expiry"], "autorenew": d["autorenew"]}
                for d in _nc_client(cfg)._get_list_domains()}
    except Exception as e:
        err = "khong doc duoc danh sach domain Namecheap: " + str(e)[:150]
        info = {}
    NC_LIST_CACHE.update({"at": now, "info": dict(info), "error": err})
    return info, err


# ------------------------------------------------- Check mail
# Doc 2 sheet xuat ban: Sheet 1 (cot C = domain, cot H = trang thai,
# chi lay 'Pass'), Sheet 2 (A = Mail, B = Mat khau, C = Ma 2FA,
# D = Mail khoi phuc, G = Domain de noi, I/J = du lieu phu). Chi hien
# mail thuoc domain Pass. Cache 5 phut theo URL.
MAIL_CACHE = {"at": 0.0, "url1": "", "url2": "", "data": None, "error": ""}
MAIL_CACHE_TTL_S = 300


def _fetch_csv_rows(url):
    """Tai 1 sheet CSV xuat ban ve list dong (list str)."""
    import io
    req = urllib.request.Request(url, headers={"User-Agent": "domain-dashboard"})
    with urllib.request.urlopen(req, timeout=20) as r:
        text = r.read().decode("utf-8-sig", errors="replace")
    return list(csv.reader(io.StringIO(text)))


def _norm_mail_domain(s):
    """Chuan hoa domain de so khop: viet thuong, bo khoang trang,
    bo https://, http://, www. va phan duong dan phia sau."""
    import re
    s = (s or "").strip().lower()
    s = re.sub(r"^https?://", "", s)
    s = re.sub(r"^www\.", "", s)
    return s.split("/")[0].strip()


def _split_mail_password(mail, password):
    """Sheet 2 co 1 so dong ghep 'mail mat-khau' chung trong cot A
    (cot B trong) — tach thanh (mail, mat_khau) de mat khau duoc che
    dung cho. Neu cot B da co thi giu nguyen."""
    mail = (mail or "").strip()
    password = (password or "").strip()
    if not password and " " in mail:
        first, rest = mail.split(None, 1)
        return first.strip(), rest.strip()
    return mail, password


def _fetch_mail_check(cfg, force=False):
    """Doc 2 sheet Check mail -> (data, error). data = {pass_domains,
    mails: [{domain, mail, password, tfa, recovery, col_i, col_j,
    note}], no_mail: [domain Pass chua co mail], counts}. Ghi chu lay
    tu cfg['mail_notes'] (chi luu trong app)."""
    url1 = _sus_csv_url(cfg.get("mail_sheet1_csv_url") or "")
    url2 = _sus_csv_url(cfg.get("mail_sheet2_csv_url") or "")
    if not url1 or not url2:
        return None, "chua nhap du 2 link sheet Check mail (o tab Check mail)"
    now = time.time()
    if (not force and MAIL_CACHE["data"] is not None
            and MAIL_CACHE["url1"] == url1 and MAIL_CACHE["url2"] == url2
            and now - MAIL_CACHE["at"] < MAIL_CACHE_TTL_S):
        return MAIL_CACHE["data"], MAIL_CACHE["error"]
    data, err = None, ""
    try:
        # Sheet 1: cot C = domain, cot H = trang thai (chi 'Pass').
        pass_domains, pass_set = [], set()
        for row in _fetch_csv_rows(url1)[1:]:
            if len(row) < 8:
                continue
            if (row[7] or "").strip() != "Pass":
                continue
            d = _norm_mail_domain(row[2])
            if d and "." in d and d not in pass_set:
                pass_set.add(d)
                pass_domains.append(d)
        # Sheet 2: A = Mail, B = Mat khau, C = Ma 2FA, D = Mail khoi
        # phuc, G = Domain, I/J = du lieu phu. Chi giu mail thuoc
        # domain Pass.
        notes = cfg.get("mail_notes") or {}
        mails, matched = [], set()
        for row in _fetch_csv_rows(url2)[1:]:
            if len(row) < 7:
                continue
            d = _norm_mail_domain(row[6])
            if not d or d not in pass_set:
                continue
            mail, password = _split_mail_password(row[0], row[1] if len(row) > 1 else "")
            if not mail:
                continue
            mails.append({
                "domain": d,
                "mail": mail,
                "password": password,
                "tfa": (row[2] or "").strip() if len(row) > 2 else "",
                "recovery": (row[3] or "").strip() if len(row) > 3 else "",
                "col_i": (row[8] or "").strip() if len(row) > 8 else "",
                "col_j": (row[9] or "").strip() if len(row) > 9 else "",
                "note": notes.get(mail.lower(), ""),
            })
            matched.add(d)
        mails.sort(key=lambda m: (m["domain"], m["mail"].lower()))
        no_mail = [d for d in pass_domains if d not in matched]
        data = {"pass_domains": pass_domains, "mails": mails,
                "no_mail": no_mail,
                "counts": {"pass": len(pass_domains),
                           "matched": len(matched),
                           "mails": len(mails)}}
    except Exception as e:
        err = "khong doc duoc sheet Check mail: " + str(e)[:150]
        data = None
    MAIL_CACHE.update({"at": now, "url1": url1, "url2": url2,
                       "data": data, "error": err})
    return data, err


def _records_match(records, preset, domain):
    """Record hien co co khop preset khong (so theo type + ten day du + content)."""
    want = {(r["type"].upper(), expand_name(domain, r.get("name", "")).lower(),
             str(r.get("content", "")).strip())
            for r in preset}
    have = {(r.get("type", "").upper(), (r.get("name", "") or "").lower(),
             str(r.get("content", "")).strip())
            for r in records}
    return want and want <= have


def _records_preset_match(records, cfg, domain):
    """Ten preset ma record hien co khop (so voi MOI preset, preset mac
    dinh truoc) — khong khop preset nao thi None. Nhieu loai web nen
    record co the de trong / khac preset, de trong la duoc."""
    presets = cfg.get("presets", {}) or {}
    ordered = []
    default = cfg.get("default_preset", "")
    if default in presets:
        ordered.append(default)
    ordered.extend(n for n in presets if n != default)
    for name in ordered:
        preset = presets.get(name) or []
        if preset and _records_match(records, preset, domain):
            return name
    return None


def check_domain(domain, cfg, skip_nc=False):
    """Trang thai 1 domain: zone CF, NS (DNS cong + Namecheap), records.
    skip_nc=True (kiem tra theo loat): bo qua luon goi Namecheap API —
    registrar NS chi can khi mo popup Edit (luc do check le se lay),
    con quet loat 410 domain ma goi NC tung domain thi NC throttle ngay."""
    domain = domain.strip().lower()
    st = {"domain": domain, "checked_at": time.strftime("%H:%M:%S %d/%m/%Y"),
          "zone": None, "doh_ns": [], "registrar_ns": [],
          "ns_ok": False, "records": [], "records_ok": None,
          "records_preset": None,
          "email": None, "https": None, "error": None}
    try:
        if not cfg.get("cf_token"):
            st["error"] = "chua nhap Cloudflare API token (muc Cai dat)"
            return st
        cf = _cf_client(cfg)
        zone = cf.get_zone(domain)
        if zone:
            st["zone"] = {"id": zone.get("id"), "status": zone.get("status"),
                          "name_servers": [x.lower() for x in zone.get("name_servers", [])]}
        try:
            st["doh_ns"] = _doh(domain)
        except Exception as e:
            st["error"] = f"DoH lookup loi: {e}"
        if st["zone"] and st["doh_ns"]:
            want = {x.lower() for x in st["zone"]["name_servers"]}
            st["ns_ok"] = bool(want) and set(st["doh_ns"]) == want
        if _nc_configured(cfg) and not skip_nc:
            if st["ns_ok"]:
                # DNS cong da tro dung NS Cloudflare thi chac chan
                # registrar cung da ghi dung — khoi goi Namecheap API
                # (kiem tra loat rat de dinh gioi han toc do ben NC).
                st["registrar_ns"] = list(st["doh_ns"])
            else:
                try:
                    st["registrar_ns"] = _nc_client(cfg).get_nameservers(domain)
                except Exception as e:
                    st["registrar_ns_error"] = str(e)
        if st["zone"]:
            try:
                st["records"] = [{"id": r.get("id"), "type": r.get("type"),
                                  "name": r.get("name"), "content": r.get("content"),
                                  "proxied": r.get("proxied"),
                                  "locked": bool(r.get("locked"))}
                                 for r in cf.list_records(st["zone"]["id"])]
                matched = _records_preset_match(st["records"], cfg, domain)
                st["records_preset"] = matched
                if cfg.get("presets"):
                    st["records_ok"] = matched is not None
            except Exception as e:
                st["error"] = f"khong doc duoc records: {e}"
            # Email Routing + HTTPS (can quyen Zone Settings / Email Routing
            # Rules tren token; loi thi ghi rieng, khong lam hong check chung).
            try:
                er = cf.get_email_routing(st["zone"]["id"])
                rules = cf.list_email_rules(st["zone"]["id"])
                st["email"] = {"enabled": bool(er.get("enabled")),
                               "status": er.get("status"),
                               "rules": len(rules),
                               "dns_locked": _email_records_locked(st.get("records"))}
            except Exception as e:
                st["email"] = {"enabled": False, "status": None, "rules": 0,
                               "error": str(e)}
            try:
                st["https"] = cf.get_https(st["zone"]["id"])
            except Exception as e:
                st["https"] = {"ssl": None, "always_https": None, "rewrites": None,
                               "ok": False, "error": str(e)}
            try:
                st["cache_bypass"] = cf.has_cache_bypass(st["zone"]["id"])
            except Exception as e:
                st["cache_bypass"] = None
                st["cache_error"] = str(e)
    except Exception as e:
        st["error"] = str(e)
    return st


def _sync_ns(domain, ns, cfg):
    """Dong bo NS: check DNS that -> neu chua tro dung thi doi o Namecheap
    (API) roi doi lan truyen. Tra ve (steps, ok). Dung chung cho Setup
    va nut 'Dong bo NS' trong popup Edit."""
    steps = []

    def step(name, ok, detail=""):
        steps.append({"step": name, "ok": ok, "detail": detail})
        return ok

    try:
        doh = _doh(domain)
        if set(doh) == set(ns):
            step("Nameserver", True, f"da tro dung tren DNS: {', '.join(doh)}")
            return steps, True
    except Exception as e:
        step("Check NS tren DNS", False, str(e))
    if not _nc_configured(cfg):
        step("Doi NS o Namecheap", False,
             "chua cau hinh Namecheap API (user/key/IP) — vao muc Cai dat")
        return steps, False
    try:
        _nc_client(cfg).set_nameservers(domain, ns)
        # DoH poll: neu DNS cong da thay NS moi thi chac chan registrar
        # da ghi dung — khoi goi getList xac minh them (tiet kiem 1 call
        # Namecheap moi domain, de setup loat 5-10 domain khong dinh
        # "Too many requests"). Chi goi getList khi DoH chua xac nhan
        # (lan truyen cham) de biet phia registrar da ghi chua.
        propagated = False
        for i in range(4):
            # Check ngay lan dau, roi 3s/lan (toi da ~9s thay vi 30s) —
            # lan truyen chi de xac nhan, setup van chay tiep neu chua
            # thay, nen khong can doi lau.
            if i > 0:
                time.sleep(3)
            try:
                if set(_doh(domain)) == set(ns):
                    propagated = True
                    break
            except Exception:
                pass
        if propagated:
            step("Doi NS o Namecheap", True,
                 f"DNS cong da tro dung NS moi: {', '.join(ns)}")
            step("DNS lan truyen", True, "NS moi da thay tren DNS cong")
            return steps, True
        reg = _nc_client(cfg).get_nameservers(domain)
        if set(reg) != set(ns):
            step("Doi NS o Namecheap", False, f"registrar tra ve khac: {', '.join(reg)}")
            return steps, False
        step("Doi NS o Namecheap", True, f"registrar da ghi: {', '.join(reg)}")
        step("DNS lan truyen", False,
             "registrar da doi, DNS cong chua thay (cho them roi bam Kiem tra)")
        # Registrar da ghi dung la coi nhu dong bo xong (phia minh); lan
        # truyen chi la doi — van cho chay tiep records, buoc lan truyen
        # tu ghi ok/failed de biet.
        return steps, True
    except Exception as e:
        step("Doi NS o Namecheap", False, str(e))
        return steps, False


def _expand_email_match(domain, match):
    """'info@' -> 'info@<domain>'; '*' giu nguyen (catch-all)."""
    match = (match or "").strip()
    if match == "*":
        return "*"
    if match.endswith("@"):
        return match + domain
    return match


# Email Routing chi bat duoc khi zone da active. NS moi doi xong thi
# Cloudflare can vai phut de kiem tra roi kich hoat — Setup doi toi da
# 5 phut thay vi bao loi ngay. Test co the doi 2 gia tri nay.
EMAIL_WAIT_ACTIVE_S = 300
EMAIL_POLL_S = 30
_MSG_ZONE_NOT_ACTIVE = ("zone chua active — Cloudflare chua kich hoat zone "
                        "(NS moi doi xong, thuong mat vai phut) — chay Setup "
                        "lai sau vai phut, cac buoc da xong se tu bo qua")


def _wait_zone_active(domain, cf, zone):
    """Doi zone active (Cloudflare kich hoat sau khi thay NS dung).
    Tra ve (active, waited_s). Zone da active thi tra ngay, khong doi."""
    cur = zone or {}
    if cur.get("status") == "active":
        return True, 0
    waited = 0
    while waited < EMAIL_WAIT_ACTIVE_S:
        time.sleep(EMAIL_POLL_S)
        waited += EMAIL_POLL_S
        try:
            cur = cf.get_zone(domain) or {}
        except Exception:
            cur = {}
        if cur.get("status") == "active":
            return True, waited
    return False, waited


def _email_records_locked(records):
    """DNS records cua Email Routing co dang khoa (Locked) khong.
    Cloudflare khoa 3 MX + SPF + DKIM khi bat routing."""
    return any(_is_email_routing_record(r) and r.get("locked")
               for r in records or [])


# ------------------------------------------------- Google Search Console
def _normalize_gsc_value(value):
    """Chuan hoa ma xac minh GSC: cat khoang trang/ngoac kep; neu user chi
    dan phan ma (khong co tien to) thi them 'google-site-verification='."""
    v = str(value or "").strip().strip('"').strip()
    if not v:
        return ""
    if v.lower().startswith("google-site-verification="):
        return "google-site-verification=" + v.split("=", 1)[1].strip()
    return "google-site-verification=" + v


def _gsc_from_records(records):
    """Record TXT xac minh GSC hien co (@) hoac None."""
    for r in records or []:
        if _is_gsc_record(r):
            return str(r.get("content", "")).strip().strip('"')
    return None


def set_gsc_record(domain, value, cfg):
    """Them/cap nhat record TXT @ xac minh Google Search Console.
    Gia tri nhap tay (lay tu Search Console) — tool khong tu lay duoc.
    Cung gia tri thi giu nguyen, khac thi xoa cu them moi. Tra ve
    (ok, detail)."""
    v = _normalize_gsc_value(value)
    if not v:
        return False, "chua nhap ma xac minh (google-site-verification=...)"
    cf, zone, err = _get_zone_or_error(domain, cfg)
    if err:
        return False, err
    try:
        cur = [r for r in cf.list_records(zone["id"]) if _is_gsc_record(r)]
        if any(str(r.get("content", "")).strip().strip('"') == v for r in cur):
            return True, "da co san"
        for r in cur:
            cf.delete_record(zone["id"], r["id"])
        # TXT bat buoc DNS-only; create_record tu set proxied=False.
        cf.create_record(zone["id"], "TXT", domain, v, proxied=False)
        return True, "da cap nhat" if cur else "da them"
    except Exception as e:
        return False, str(e)


def _apply_email_preset(domain, cfg, cf, zone):
    """Buoc Email Routing trong Setup: bat routing (neu chua) roi dong bo
    rule theo email_preset (rule nao co san giong het thi giu, khac thi
    xoa roi tao lai). Tra ve (steps, ok)."""
    steps = []
    ep = cfg.get("email_preset") or {}
    if not ep.get("enabled"):
        steps.append({"step": "Email routing", "ok": True,
                      "detail": "bo qua (email preset dang tat)"})
        return steps, True
    rules = [r for r in (ep.get("rules") or [])
             if str(r.get("to", "")).strip() and str(r.get("match", "")).strip()]
    if not rules:
        steps.append({"step": "Email routing", "ok": True,
                      "detail": "bo qua (chua nhap dia chi nhan o muc Email preset)"})
        return steps, True
    try:
        er = cf.get_email_routing(zone["id"])
        if not er.get("enabled"):
            active, waited = _wait_zone_active(domain, cf, zone)
            if not active:
                steps.append({"step": "Email routing", "ok": False,
                              "detail": _MSG_ZONE_NOT_ACTIVE})
                return steps, False
            if waited:
                steps.append({"step": "Doi zone active", "ok": True,
                              "detail": f"zone da active sau ~{waited}s"})
            try:
                cf.enable_email_routing(zone["id"])
            except Exception as e:
                # Loi 2008 'Non-Cloudflare MX records exist': zone con MX
                # cua nha cung cap cu (Google/Zoho/...) thi khong bat duoc.
                # Cach sua cua chinh Cloudflare: xoa MX cu roi bat lai ngay.
                if "mx" not in str(e).lower():
                    raise
                recs = cf.list_records(zone["id"])
                deleted = 0
                for rec in recs:
                    if rec.get("type") == "MX" and not str(
                            rec.get("content", "")).endswith(".mx.cloudflare.net"):
                        cf.delete_record(zone["id"], rec["id"])
                        deleted += 1
                steps.append({"step": "Xoa MX cu", "ok": True,
                              "detail": f"da xoa {deleted} MX cu (khong phai cua "
                                        "Cloudflare) de bat Email Routing"})
                cf.enable_email_routing(zone["id"])
            steps.append({"step": "Bat Email Routing", "ok": True,
                          "detail": "da them/khoa 3 MX + SPF + DKIM"})
        existing = cf.list_email_rules(zone["id"])

        def match_key(rule):
            mts = rule.get("matchers") or []
            if mts and mts[0].get("type") == "all":
                return "*"
            vals = [m.get("value", "") for m in mts if m.get("value")]
            return vals[0] if vals else ""

        def rule_dests(rule):
            dests = []
            for a in rule.get("actions") or []:
                dests.extend(a.get("value") or [])
            return dests

        # Catch-all khong nam trong existing_by_match — no duoc xu ly
        # rieng qua endpoint /rules/catch_all (xoa/tap qua /rules/{id}
        # se bi API tra 'Invalid rule operation').
        existing_by_match = {}
        for r in existing:
            k = match_key(r)
            if k != "*":
                existing_by_match.setdefault(k, []).append(r)
        for r in rules:
            match = _expand_email_match(domain, r["match"])
            dests = [x.strip() for x in str(r["to"]).split(",") if x.strip()]
            if match == "*":
                # Catch-all: singleton, GET/PUT /rules/catch_all, chi 1 dich.
                cur_ca = cf.get_catch_all(zone["id"])
                cur_dests = []
                if cur_ca:
                    for a in cur_ca.get("actions") or []:
                        cur_dests.extend(a.get("value") or [])
                if cur_ca and cur_ca.get("enabled") \
                        and sorted(cur_dests) == sorted(dests[:1]):
                    steps.append({"step": "Rule * (catch-all)", "ok": True,
                                  "detail": "da co san"})
                    continue
                cf.set_catch_all(zone["id"], dests[:1])
                detail = "da tao" if not cur_ca else "da cap nhat"
                if len(dests) > 1:
                    detail += " (catch-all chi cho 1 dia chi nhan — lay dia chi dau tien)"
                steps.append({"step": f"Rule * (catch-all) -> {dests[0]}",
                              "ok": True, "detail": detail})
                continue
            cands = existing_by_match.get(match, [])
            same = [c for c in cands
                    if sorted(rule_dests(c)) == sorted(dests)]
            if same:
                # Giu 1 rule giong het, xoa cac rule trung y het (neu co).
                for dup in same[1:]:
                    cf.delete_email_rule(zone["id"], dup["id"])
                detail = "da co san"
                if len(same) > 1:
                    detail += f" (da xoa {len(same) - 1} rule trung)"
                steps.append({"step": f"Rule {match}", "ok": True, "detail": detail})
                continue
            # Cung dia chi nhan khac dich -> xoa het rule cu cung dia chi
            # roi tao moi theo preset; rule cua dia chi khac giu nguyen.
            for c in cands:
                cf.delete_email_rule(zone["id"], c["id"])
            cf.create_email_rule(zone["id"], match, dests)
            detail = "da tao"
            if cands:
                detail += f" (da xoa {len(cands)} rule cu cung dia chi)"
            steps.append({"step": f"Rule {match} -> {', '.join(dests)}",
                          "ok": True, "detail": detail})
        return steps, True
    except Exception as e:
        detail = str(e)
        if "active zone" in detail.lower():
            detail = _MSG_ZONE_NOT_ACTIVE
        steps.append({"step": "Email routing", "ok": False, "detail": detail})
        return steps, False


def _get_zone_or_error(domain, cfg):
    """(cf, zone, error) — lay zone, bao loi ro neu chua co / chua co token."""
    if not cfg.get("cf_token"):
        return None, None, "chua nhap Cloudflare API token"
    cf = _cf_client(cfg)
    zone = cf.get_zone(domain)
    if not zone:
        return cf, None, "chua co zone tren Cloudflare — bam Setup truoc"
    return cf, zone, None


def _other_preset_keys(domain, cfg, preset_name):
    """Tap (type, ten day du, content) cua cac record thuoc preset KHAC
    preset dang ap — dung de go record sot lai cua preset cu khi doi cau
    hinh (1 domain chi duoc co 1 cau hinh, khong de 2 preset chong len
    nhau)."""
    keys = set()
    for name, recs in (cfg.get("presets") or {}).items():
        if name == preset_name:
            continue
        for r in recs or []:
            keys.add((str(r.get("type", "A")).upper(),
                      expand_name(domain, r.get("name", "")).lower(),
                      str(r.get("content", "")).strip()))
    return keys


def _sync_records(domain, records, cf, zone, other_keys=None):
    """Ap record theo kieu gop (merge), KHONG xoa het record cu.
    - record giong het (type+name+content+proxied) -> giu
    - record cung type+name nhung khac content/proxied -> xoa cai cu
      roi them moi (day la 'record trung')
    - record khac (khong nam trong danh sach, khong thuoc preset nao)
      -> GIU NGUYEN (record tu do)
    - record thuoc preset KHAC (other_keys) -> GO, de 1 domain chi co 1
      cau hinh (khong chong 2 preset); record he thong Email Routing /
      GSC thi giu nguyen
    - xung dot CNAME: 1 ten khong the vua co CNAME vua co record loai
      khac — preset muon CNAME o ten X thi xoa cac record khac loai o X
      truoc, preset muon record thuong o ten X ma dang co CNAME o X thi
      xoa CNAME do truoc (khong xoa record he thong Email Routing / GSC;
      neu chung chan thi create se loi ro rang).
    Tra ve (detail, ok). Dung cho Setup, Ap record, Ap record loat."""
    try:
        existing = cf.list_records(zone["id"])
        by_key = {}
        by_name = {}
        for r in existing:
            # Chuan hoa ten ve FQDN (API that tra ten day du; mock tra
            # ten ngan '@'/'www') de so sanh khop voi preset.
            k = ((r.get("type") or "").upper(),
                 expand_name(domain, r.get("name") or "").lower())
            by_key.setdefault(k, []).append(r)
            by_name.setdefault(k[1], []).append(r)
        kept = updated = added = deleted = 0
        for r in records:
            typ = str(r.get("type", "A")).upper()
            name = expand_name(domain, r.get("name", ""))
            content = str(r.get("content", "")).strip()
            proxied = bool(r.get("proxied", True))
            if not content:
                continue
            key = (typ, name.lower())
            for x in list(by_name.get(name.lower(), [])):
                xtype = (x.get("type") or "").upper()
                if xtype == typ:
                    continue  # cung loai — xu ly o phan gop ben duoi
                if typ == "CNAME" or xtype == "CNAME":
                    if _is_email_routing_record(x) or _is_gsc_record(x):
                        continue  # bao ve — khong xoa ngam
                    cf.delete_record(zone["id"], x["id"])
                    deleted += 1
                    by_name[name.lower()].remove(x)
                    old_key = (xtype, name.lower())
                    if x in by_key.get(old_key, []):
                        by_key[old_key].remove(x)
            cur = by_key.get(key, [])
            same = [x for x in cur
                    if str(x.get("content", "")).strip() == content
                    and bool(x.get("proxied", False)) == proxied]
            if same:
                kept += 1
                for x in cur:
                    if x is not same[0]:
                        cf.delete_record(zone["id"], x["id"])
                        deleted += 1
                by_key[key] = [same[0]]
                continue
            for x in cur:
                cf.delete_record(zone["id"], x["id"])
                deleted += 1
            by_key[key] = []
            cf.create_record(zone["id"], typ, name, content, proxied)
            if cur:
                updated += 1
            else:
                added += 1
        removed_other = 0
        if other_keys:
            new_keys = {(str(r.get("type", "A")).upper(),
                         expand_name(domain, r.get("name", "")).lower(),
                         str(r.get("content", "")).strip())
                        for r in records if str(r.get("content", "")).strip()}
            for x in cf.list_records(zone["id"]):
                xk = ((x.get("type") or "").upper(),
                      expand_name(domain, x.get("name") or "").lower(),
                      str(x.get("content", "")).strip())
                if xk in new_keys or xk not in other_keys:
                    continue
                if _is_email_routing_record(x) or _is_gsc_record(x):
                    continue
                cf.delete_record(zone["id"], x["id"])
                removed_other += 1
        detail = (f"giu {kept} record giong het, cap nhat {updated}, "
                  f"them {added} record moi, da xoa {deleted} record trung"
                  + (f", da go {removed_other} record cua preset khac"
                     if removed_other else "") +
                  " — cac record khac giu nguyen")
        return detail, True
    except Exception as e:
        return str(e), False


def setup_domain(domain, preset_name, cfg, skip_records=False,
                 extra_records=None):
    """Setup 1 domain: tao zone CF -> doi NS Namecheap -> ap preset record.
    skip_records=True: giu nguyen record dang co (domain da dung preset
    khac, user khong muon set lai) — bo qua buoc ap preset, di thang toi
    email + HTTPS. extra_records: record them (vd TXT _cf-custom-hostname
    tu cot L) ap cung voi preset. Tra ve {domain, ok, steps:[{step, ok, detail}]}."""
    domain = domain.strip().lower()
    preset_name = preset_name or cfg.get("default_preset", "")
    steps = []

    def step(name, ok, detail=""):
        steps.append({"step": name, "ok": ok, "detail": detail})
        return ok

    if not cfg.get("cf_token"):
        step("Cloudflare API token", False, "chua nhap token (muc Cai dat)")
        return {"domain": domain, "ok": False, "steps": steps}
    cf = _cf_client(cfg)
    try:
        zone, created = cf.get_or_create_zone(domain)
    except Exception as e:
        step("Tao/lay zone Cloudflare", False, str(e))
        return {"domain": domain, "ok": False, "steps": steps}
    step("Tao/lay zone Cloudflare", True,
         ("da tao moi" if created else "da co san") + f" — status: {zone.get('status')}")
    ns = [x.lower() for x in zone.get("name_servers", [])]
    if len(ns) != 2:
        step("Lay nameserver", False, f"zone tra ve NS bat thuong: {ns}")
        return {"domain": domain, "ok": False, "steps": steps}

    # 2. NS da tro dung chua? (check bang DNS that)
    ns_steps, ns_ok = _sync_ns(domain, ns, cfg)
    steps.extend(ns_steps)
    if not ns_ok:
        return {"domain": domain, "ok": False, "steps": steps}

    # 3. Ap preset record theo kieu gop (KHONG xoa het record cu nua):
    # chi xoa record trung (cung type+name, khac noi dung), record khac
    # giu nguyen — tranh mat record tu do (vd domain 9 record, xoa het
    # roi ap lai chi con 7).
    if skip_records:
        step(f"Ap preset '{preset_name}'", True,
             "bỏ qua — giữ nguyên record đang có")
    elif preset_name:
        preset = cfg.get("presets", {}).get(preset_name)
        if not preset:
            step(f"Ap preset '{preset_name}'", False,
                 f"preset '{preset_name}' khong ton tai — vao muc Preset "
                 "record de tao/chon preset mac dinh roi Setup lai")
            return {"domain": domain, "ok": False, "steps": steps}
        detail, ok3 = _sync_records(domain, list(preset) + list(extra_records or []),
                                    cf, zone,
                                    _other_preset_keys(domain, cfg, preset_name))
        step(f"Ap preset '{preset_name}'", ok3, detail)
        if not ok3:
            return {"domain": domain, "ok": False, "steps": steps}
    else:
        step("Ap preset", False,
             "chua chon preset mac dinh — vao muc Preset record de chon "
             "preset mac dinh roi Setup lai")
        return {"domain": domain, "ok": False, "steps": steps}

    # 4. Email routing theo preset (sau records de MX/SPF/DKIM khong bi xoa).
    email_steps, _email_ok = _apply_email_preset(domain, cfg, cf, zone)
    steps.extend(email_steps)

    # 5. HTTPS theo preset (Flexible + Always Use HTTPS + TLS 1.2).
    hp = cfg.get("https_preset") or {}
    if hp.get("enabled"):
        try:
            out = cf.set_https(zone["id"], ssl=hp.get("ssl"),
                               always_https=hp.get("always_https"),
                               rewrites=hp.get("rewrites"),
                               min_tls=hp.get("min_tls_version"))
            step("HTTPS", True,
                 f"ssl={out.get('ssl')}, always_https={out.get('always_https')}, "
                 f"min_tls={out.get('min_tls')}")
        except Exception as e:
            step("HTTPS", False, str(e))
    else:
        step("HTTPS", True, "bo qua (https preset dang tat)")
    ok = all(s["ok"] for s in steps)
    return {"domain": domain, "ok": ok, "steps": steps}


def apply_preset(domain, preset_name, cfg):
    """Chi ap preset record (khong dong NS)."""
    if not cfg.get("cf_token"):
        return {"domain": domain, "ok": False, "steps": [
            {"step": "Cloudflare API token", "ok": False, "detail": "chua nhap token"}]}
    preset = cfg.get("presets", {}).get(preset_name)
    if not preset:
        return {"domain": domain, "ok": False, "steps": [
            {"step": f"Ap preset '{preset_name}'", "ok": False, "detail": "preset khong ton tai"}]}
    try:
        cf = _cf_client(cfg)
        zone = cf.get_zone(domain)
        if not zone:
            return {"domain": domain, "ok": False, "steps": [
                {"step": "Tim zone", "ok": False, "detail": "chua co zone tren Cloudflare — bam Setup truoc"}]}
        detail, ok = _sync_records(domain, preset, cf, zone,
                                   _other_preset_keys(domain, cfg, preset_name))
        return {"domain": domain, "ok": ok, "steps": [
            {"step": f"Ap preset '{preset_name}'", "ok": ok, "detail": detail}]}
    except Exception as e:
        return {"domain": domain, "ok": False, "steps": [
            {"step": f"Ap preset '{preset_name}'", "ok": False, "detail": str(e)}]}


# --- Google Sheet (doc CSV publish, chi 3 cot B/H/J) ---
# Map cot H "Loai web" sang preset record — cau hinh duoc qua
# cfg["sheet_rules"] = [{name, preset}]; user them/sua/xoa doi ten tuy y
# (khong can xoa tao lai). "Web Thuan" (khong ghi Han/Long) chua co rule
# mac dinh nen se bo qua cho den khi user them rule cho no.
DEFAULT_SHEET_RULES = [
    {"name": "Web Thuần Hân", "preset": "Web Chị Hân"},
    {"name": "Web Thuần Long", "preset": "Web Anh Long"},
]


def _sheet_preset_for(loai, cfg):
    """Preset ung voi gia tri cot H, theo rule user da cau hinh."""
    for r in cfg.get("sheet_rules") or DEFAULT_SHEET_RULES:
        if (r.get("name") or "").strip() == (loai or "").strip():
            return r.get("preset")
    return None
# Chi xu ly 2 gia tri nay o cot J — cac gia tri khac ("Can check web",
# "Da hoan thanh", trong...) thi bo qua.
def _sheet_norm_status(s):
    """Chuan hoa trang thai cot J: bo khoang trang/gach noi/gach duoi, lower.
    'Cần set up'/'Cần setup'/'Cần set-up' -> 'cầnsetup';
    'Đã set up'/'Đã setup'/'Đã set-up' -> 'đãsetup'.
    (Sheet cua user dung ca 2 cach viet, dropdown hien 'Đã set up'.)"""
    return (s or "").strip().lower().replace(" ", "").replace("-", "").replace("_", "")


SHEET_WANT = ("cầnsetup", "đãsetup")
SHEET_DONE = "đãsetup"  # gia tri ghi ve cot J khi setup xong (dung dropdown cua user)
SHEET_DONE_DISPLAY = "Đã set up"

# TXT _cf-custom-hostname chi ap cho preset Web Anh Long (gia tri lay tu
# cot L cua sheet). Web khac co cau hinh khac thi them sau.
SHEET_CUSTOM_HOSTNAME_PRESET = "Web Anh Long"
CUSTOM_HOSTNAME_TXT_NAME = "_cf-custom-hostname"


def _sheet_custom_hostname_record(custom_id):
    """Record TXT _cf-custom-hostname cho preset Web Anh Long.
    custom_id = gia tri cot L (UUID cua store, ghi binh thuong khong dau
    ngoac kep). Tra ve None neu cot L trong."""
    cid = (custom_id or "").strip().strip('"').strip()
    if not cid:
        return None
    return {"type": "TXT", "name": CUSTOM_HOSTNAME_TXT_NAME,
            "content": cid}


def _sheet_current_custom_hostname(domain, cfg, st):
    """Gia tri TXT _cf-custom-hostname dang co tren Cloudflare (None neu
    chua co). Doc tu status da luu (neu co records), neu khong thi check
    moi. So sanh bo dau ngoac kep 2 ben cho chac (API co the tra ve co
    dau ngoac)."""
    want_name = expand_name(domain, CUSTOM_HOSTNAME_TXT_NAME).lower()

    def _find(records):
        for r in records or []:
            if ((r.get("type") or "").upper() == "TXT"
                    and expand_name(domain, r.get("name") or "").lower() == want_name):
                v = str(r.get("content") or "").strip().strip('"').strip()
                return v or None
        return None

    s = (st or {}).get(domain) or {}
    recs = s.get("records")
    if recs:
        return _find(recs)
    try:
        new = check_domain(domain, cfg, skip_nc=True)
        _save_check_result(st, domain, new)
        return _find(new.get("records"))
    except Exception:
        return None


# Base URL Sheets API — doi duoc qua env SHEETS_API_BASE (de test voi mock).
SHEETS_API_BASE = os.environ.get("SHEETS_API_BASE",
                                 "https://sheets.googleapis.com")


def _sheet_cell(r, idx):
    """Lay o tai vi tri idx, thieu thi tra ve '' (API co the tra dong ngan)."""
    return (r[idx] if idx < len(r) else "") or ""


def _sheet_gid(cfg):
    """GID cua tab trong link CSV publish (neu co)."""
    import urllib.parse
    q = urllib.parse.parse_qs(urllib.parse.urlparse(
        cfg.get("sheet_csv_url") or "").query)
    if q.get("gid"):
        try:
            return int(q["gid"][0])
        except ValueError:
            return None
    return None


def _sheet_tab_name(cfg, token):
    """Ten tab de doc/ghi: tim theo gid trong link CSV, mac dinh tab dau tien.
    Tra ve (ten_tab, error)."""
    import json as _json
    import urllib.request
    sheet_id = (cfg.get("sheet_id") or "").strip()
    if not sheet_id:
        return None, "chưa nhập Sheet ID"
    try:
        req = urllib.request.Request(
            SHEETS_API_BASE + "/v4/spreadsheets/" + sheet_id,
            headers={"Authorization": "Bearer " + token})
        with urllib.request.urlopen(req, timeout=30) as r:
            meta = _json.loads(r.read().decode())
    except Exception as e:
        return None, f"không đọc được danh sách tab: {e}"
    sheets = meta.get("sheets") or []
    gid = _sheet_gid(cfg)
    if gid is not None:
        for sh in sheets:
            if sh.get("properties", {}).get("sheetId") == gid:
                return sh["properties"].get("title"), ""
    if sheets:
        return sheets[0].get("properties", {}).get("title"), ""
    return None, "không tìm được tab nào trong sheet"


def _sheet_fetch_csv(url):
    """Tai CSV publish ve, tra ve list cac row (list of str)."""
    import csv
    import io
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        text = r.read().decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text)))


def _sheet_fetch_api(cfg):
    """Doc sheet TRUC TIEP qua Sheets API (du lieu live, khong qua cache
    cua link publish). Can sheet_id + service account. Tra ve list cac row
    (list of str), dong 1 la header."""
    import json as _json
    import urllib.parse
    import urllib.request
    token = _sa_access_token(cfg)
    tab, err = _sheet_tab_name(cfg, token)
    if err:
        raise RuntimeError(err)
    # Ten tab luon boc trong dau ' (thoat ' bang '').
    disp = "'" + tab.replace("'", "''") + "'"
    rng = urllib.parse.quote(disp + "!A1:Z")
    req = urllib.request.Request(
        SHEETS_API_BASE + "/v4/spreadsheets/" +
        (cfg.get("sheet_id") or "").strip() + "/values/" + rng,
        headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = _json.loads(r.read().decode())
    rows = data.get("values") or []
    return [[str(c) for c in row] for row in rows]


def sheet_read_rows(cfg):
    """Doc sheet tu start_row tro xuong, chi lay B (domain), H (loai web),
    J (trang thai), L (ghi chu — ID _cf-custom-hostname cho Web Anh Long).
    Chi giu cac dong co J = 'can set up' hoac 'da set up'
    (so sanh khong phan biet hoa/thuong). Trung domain thi giu dong CUOI
    (moi nhat). Uu tien doc qua Sheets API (live) khi da nhap Sheet ID +
    file JSON key service account; neu API loi thi tu fallback ve link CSV
    publish. Tra ve (rows, error, source) — source: 'api' | 'csv';
    rows: [{row, domain, loai, preset, status_j, note_l}]."""
    try:
        start = int(cfg.get("sheet_start_row") or 1)
    except (TypeError, ValueError):
        start = 1
    all_rows, source, api_err = None, "csv", ""
    if (cfg.get("sheet_id") or "").strip() and (cfg.get("sheet_sa_key") or "").strip():
        try:
            all_rows = _sheet_fetch_api(cfg)
            source = "api"
        except Exception as e:
            api_err = str(e)
    if all_rows is None:
        url = (cfg.get("sheet_csv_url") or "").strip()
        if not url:
            return [], ("chưa nhập link CSV publish của sheet"
                       + (f" (API lỗi: {api_err})" if api_err else "")), source
        try:
            all_rows = _sheet_fetch_csv(url)
        except Exception as e:
            return [], f"không tải được sheet: {e}" + (f" (API lỗi: {api_err})" if api_err else ""), source
    by_domain = {}
    for idx, r in enumerate(all_rows):
        row_no = idx + 1
        if row_no < start:
            continue
        domain = _sheet_cell(r, 1).strip().lower()
        if not domain or "." not in domain:
            continue
        status_j = _sheet_cell(r, 9).strip()
        if _sheet_norm_status(status_j) not in SHEET_WANT:
            continue
        loai = _sheet_cell(r, 7).strip()
        note_l = _sheet_cell(r, 11).strip()
        by_domain[domain] = {"row": row_no, "domain": domain, "loai": loai,
                             "preset": None,  # gan sau o _sheet_classify
                             "status_j": status_j, "note_l": note_l}
    return list(by_domain.values()), "", source


def _sheet_current_preset(domain, cfg, st):
    """Preset hien tai cua domain (tu status da luu; chua co thi check moi
    va luu lai). Tra ve ten preset hoac None (chua dung preset nao)."""
    s = (st or {}).get(domain) or {}
    if s.get("records_preset"):
        return s["records_preset"]
    if s and not s.get("error"):
        return None  # da check day du ma khong khop preset nao
    try:
        new = check_domain(domain, cfg, skip_nc=True)
        _save_check_result(st, domain, new)
        return new.get("records_preset")
    except Exception:
        return None


def _sheet_is_fully_setup(domain, preset, cfg, st):
    """Domain da setup day du theo preset chua: record khop preset +
    email da bat (neu preset email bat) + HTTPS dung preset (neu preset
    HTTPS bat). Dung status day du da luu; chua co / chi light / loi thi
    check moi va luu lai. Khong xac dinh duoc thi tra False (se chay
    setup binh thuong)."""
    s = (st or {}).get(domain) or {}
    if (not s or s.get("error") or s.get("light")
            or not s.get("records_preset")):
        try:
            new = check_domain(domain, cfg, skip_nc=True)
            _save_check_result(st, domain, new)
            s = new
        except Exception:
            return False
    if s.get("records_preset") != preset:
        return False
    if (cfg.get("email_preset") or {}).get("enabled"):
        em = s.get("email") or {}
        if not em.get("enabled"):
            return False
    if (cfg.get("https_preset") or {}).get("enabled"):
        hp = cfg.get("https_preset") or {}
        hs = s.get("https") or {}
        if (hs.get("ssl") != hp.get("ssl")
                or hs.get("always_https") != hp.get("always_https")):
            return False
    return True


def _sa_access_token(cfg):
    """Lay access token tu service account JSON (google-auth)."""
    from google.oauth2 import service_account
    key_path = (cfg.get("sheet_sa_key") or "").strip()
    if not key_path:
        raise RuntimeError("chưa nhập đường dẫn file JSON key của service account")
    import os
    if not os.path.exists(key_path):
        raise RuntimeError(f"không thấy file key: {key_path}")
    creds = service_account.Credentials.from_service_account_file(
        key_path, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    from google.auth.transport.requests import Request
    creds.refresh(Request())
    return creds.token


def sheet_write_status(row, value, cfg):
    """Ghi gia tri vao cot J (cot 10) cua dong row trong sheet, qua Sheets
    API (can sheet_id + service account da duoc share quyen Editor).
    Tra ve (ok, detail)."""
    sheet_id = (cfg.get("sheet_id") or "").strip()
    if not sheet_id:
        return False, "chưa nhập Sheet ID (ID trong link edit của sheet)"
    try:
        token = _sa_access_token(cfg)
    except ImportError:
        return False, "chưa cài thư viện google-auth — chạy: pip install google-auth"
    except Exception as e:
        return False, str(e)
    tab, tab_err = _sheet_tab_name(cfg, token)
    if tab_err:
        return False, tab_err
    import json as _json
    import urllib.parse
    import urllib.request
    # Ten tab luon boc trong dau ' (thoat ' bang ''), hop le ca khi khong co khoang trang.
    disp = "'" + tab.replace("'", "''") + "'"
    rng = f"{disp}!J{row}"
    body = _json.dumps({"range": rng, "majorDimension": "ROWS",
                        "values": [[value]]}).encode()
    url = (SHEETS_API_BASE + "/v4/spreadsheets/" + sheet_id +
           "/values/" + urllib.parse.quote(rng) + "?valueInputOption=RAW")
    try:
        req = urllib.request.Request(
            url, data=body, method="PUT",
            headers={"Authorization": "Bearer " + token,
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            _json.loads(r.read().decode())
        return True, f"đã ghi '{value}' vào {rng}"
    except Exception as e:
        return False, str(e)


def _sheet_classify(rows, cfg, st):
    """Gan hanh dong cho moi dong sheet: 'setup' (clean, se tu setup),
    'review' (xung dot preset -> cho duyet), 'skip' (da set up / chua map
    preset / da tu choi). Tra ve rows da gan 'action' + 'detail'."""
    ignored = set(cfg.get("sheet_ignored") or [])
    out = []
    for r in rows:
        d = r["domain"]
        r = dict(r)
        r["preset"] = _sheet_preset_for(r.get("loai"), cfg)
        if _sheet_norm_status(r["status_j"]) == SHEET_DONE:
            r["action"], r["detail"] = "skip", "Đã set up rồi"
            # Da setup roi nhung cot L doi (chi Web Anh Long) -> cap nhat TXT.
            # Domain tung bi tu choi thi khong tu cap nhat (user bam Setup lai tay).
            note_l = (r.get("note_l") or "").strip()
            if (r["preset"] == SHEET_CUSTOM_HOSTNAME_PRESET and note_l
                    and d not in ignored):
                cur_txt = _sheet_current_custom_hostname(d, cfg, st)
                want_txt = note_l.strip('"').strip()
                if cur_txt != want_txt:
                    r["action"] = "setup"
                    r["detail"] = (f"cần cập nhật TXT _cf-custom-hostname: {want_txt}"
                                   + (f" (đang là {cur_txt})" if cur_txt else " (chưa có)"))
        elif d in ignored:
            r["action"], r["detail"] = "skip", "đã từ chối trước đó"
        elif not r["preset"]:
            r["action"] = "skip"
            r["detail"] = (f"Loại web '{r['loai']}' chưa map sang preset nào"
                           if r["loai"] else "chưa ghi Loại web")
        else:
            cur = _sheet_current_preset(d, cfg, st)
            if cur and cur != r["preset"]:
                r["action"] = "review"
                r["detail"] = f"đang dùng '{cur}', sheet muốn '{r['preset']}'"
                r["current"] = cur
            elif cur and _sheet_is_fully_setup(d, r["preset"], cfg, st):
                # Da setup day du roi — chay se chi cap nhat cot J, khong
                # chay setup lai.
                r["action"] = "setup"
                r["detail"] = f"đã setup sẵn theo '{r['preset']}' — chỉ cập nhật cột J"
            else:
                r["action"] = "setup"
                r["detail"] = ("chưa có preset — sẽ setup theo "
                               f"'{r['preset']}'" if not cur else
                               f"đã đúng preset '{r['preset']}' — sẽ hoàn tất phần còn thiếu")
        out.append(r)
    return out


def _sheet_setup_one(domain, row, preset, cfg, st, custom_id=None, attempts=1):
    """Setup 1 dong sheet: chay setup_domain, neu ok thi ghi 'da set up'
    ve cot J cua dong do. Tra ve {domain, row, preset, action, detail}.
    Neu domain tung bi tu choi (trong sheet_ignored) thi go ra khoi danh
    sach do — user da chu dong setup lai.
    custom_id: gia tri cot L — neu preset la Web Anh Long thi kem them
    record TXT _cf-custom-hostname.
    attempts: so lan thu setup (mac dinh 1). Neu that, thu lai toi da
    attempts lan; het attempts ma van loi thi dung lai va bao loi."""
    ignored = cfg.get("sheet_ignored") or []
    if domain in ignored:
        cfg["sheet_ignored"] = [d for d in ignored if d != domain]
    # Da setup day du roi (record khop preset + email + HTTPS) thi KHONG
    # chay setup lai — chi ghi 'Da set up' vao cot J la du. (Chi ap dung
    # khi khong co TXT _cf-custom-hostname can them/cap nhat o cot L.)
    if not custom_id and _sheet_is_fully_setup(domain, preset, cfg, st):
        ok_w, det_w = sheet_write_status(row, SHEET_DONE_DISPLAY, cfg)
        return {"domain": domain, "row": row, "preset": preset,
                "action": "done",
                "detail": ("đã setup sẵn — chỉ cập nhật cột J" if ok_w else
                           f"đã setup sẵn — NHƯNG ghi sheet lỗi: {det_w}")}
    extra = []
    if preset == SHEET_CUSTOM_HOSTNAME_PRESET:
        rec = _sheet_custom_hostname_record(custom_id)
        if rec:
            extra.append(rec)
    try:
        attempts = max(1, min(int(attempts or 1), 5))
    except (TypeError, ValueError):
        attempts = 1
    last_res = None
    for _attempt in range(attempts):
        res = setup_domain(domain, preset, cfg, extra_records=extra)
        last_res = res
        if res.get("ok"):
            _save_check_result(st, domain, check_domain(domain, cfg, skip_nc=True))
            ok_w, det_w = sheet_write_status(row, SHEET_DONE_DISPLAY, cfg)
            return {"domain": domain, "row": row, "preset": preset,
                    "action": "done",
                    "detail": ("setup xong" if ok_w else
                               f"setup xong — NHƯNG ghi sheet lỗi: {det_w}")}
    bad = [f"{s['step']}: {s.get('detail', '')}"
           for s in (last_res or {}).get("steps", []) if not s.get("ok")]
    detail = "setup lỗi — " + ("; ".join(bad) if bad else "xem log")
    if attempts > 1:
        detail += f" (đã thử {attempts} lần)"
    return {"domain": domain, "row": row, "preset": preset,
            "action": "error", "detail": detail}


# Khoa chong 2 luot setup sheet chay chong len nhau (tu dong vs thu cong).
SHEET_LOCK = threading.Lock()
# Ket qua lan tu dong setup gan nhat (de hien o UI) — chi luu trong RAM.
SHEET_AUTO_LAST = {"at": "", "ok": None, "done": 0, "queued": 0, "error": ""}


def _sheet_auto_once(cfg, st):
    """1 luot tu dong setup: neu sheet_auto bat thi chay sheet_setup_clean
    (chi dong sach; domain xung dot thi vao hang cho duyet, khong tu setup).
    Tra ve True neu da chay (ke ca khi loi), False neu tat hoac dang co luot
    setup thu cong chay (bo qua luot nay, khong cho)."""
    import time as _time
    if not cfg.get("sheet_auto"):
        return False
    if not SHEET_LOCK.acquire(blocking=False):
        return False  # dang co setup thu cong -> bo qua
    try:
        res = sheet_setup_clean(cfg, st)
        save_status(st)
        SHEET_AUTO_LAST.update({
            "at": _time.strftime("%H:%M:%S %d/%m/%Y"),
            "ok": bool(res.get("ok")),
            "done": len([r for r in res.get("results", [])
                         if r.get("action") == "done"]),
            "queued": len(res.get("queued", [])),
            "error": res.get("error", "") or "",
        })
        return True
    except Exception as e:
        SHEET_AUTO_LAST.update({"at": _time.strftime("%H:%M:%S %d/%m/%Y"),
                                "ok": False, "done": 0, "queued": 0,
                                "error": str(e)})
        return True
    finally:
        SHEET_LOCK.release()


def _sheet_auto_loop():
    """Thread nen: cu moi sheet_auto_interval phut (mac dinh 5) thi chay
    1 luot _sheet_auto_once."""
    import time as _time
    elapsed = 0.0
    while True:
        _time.sleep(10)
        elapsed += 10
        try:
            cfg = load_config()
        except Exception:
            continue
        if not cfg.get("sheet_auto"):
            elapsed = 0.0
            continue
        try:
            interval = float(cfg.get("sheet_auto_interval") or 5)
        except (TypeError, ValueError):
            interval = 5.0
        interval = max(1.0, min(interval, 60.0))
        if elapsed < interval * 60:
            continue
        elapsed = 0.0
        _sheet_auto_once(cfg, load_status())


def sheet_setup_clean(cfg, st):
    """Doc sheet, setup tuan tu tung dong 'can set up' ma clean (xong 1
    moi qua dong sau, loi cung qua dong sau), ghi 'da set up' ve cot J
    khi xong. Domain xung dot thi dua vao hang cho duyet.
    Tra ve {ok, results:[{domain,row,preset,action,detail}], queued}.
    Luu config (hang cho duyet) + status."""
    rows, err, _src = sheet_read_rows(cfg)
    if err:
        return {"ok": False, "error": err}
    rows = _sheet_classify(rows, cfg, st)
    results, queued = [], []
    review = cfg.setdefault("sheet_review", [])
    for r in rows:
        d = r["domain"]
        if r["action"] == "review":
            q = {"domain": d, "row": r["row"], "preset": r["preset"],
                 "current": r.get("current"), "loai": r["loai"],
                 "note_l": r.get("note_l", "")}
            if q not in review:
                review.append(q)
            queued.append(q)
            results.append({"domain": d, "row": r["row"],
                            "preset": r["preset"], "action": "review",
                            "detail": r["detail"]})
            continue
        if r["action"] != "setup":
            results.append({"domain": d, "row": r["row"],
                            "preset": r["preset"], "action": "skip",
                            "detail": r["detail"]})
            continue
        results.append(_sheet_setup_one(d, r["row"], r["preset"], cfg, st,
                                        custom_id=r.get("note_l")))
    save_config(cfg)
    save_status(st)
    return {"ok": True, "results": results, "queued": queued}


_WRITE_MARKS = ("da tao", "da cap nhat", "da them", "da xoa")


def _setup_email_one(d, cfg):
    """Setup email cho 1 domain (dung cho loat nhieu domain): zone chua
    active thi BO QUA NGAY (khong doi 5 phut nhu nut Setup email le —
    cho ca loat thi doi tung domain se rat lau). Tra ve dict nhu
    /api/setup-email, them co 'already_set' — True khi khong co buoc
    nao ghi gi (routing da bat + tat ca rule da co san)."""
    cf, zone, err = _get_zone_or_error(d, cfg)
    if err:
        return {"domain": d, "ok": False, "steps": [
            {"step": "Tim zone", "ok": False, "detail": err}]}
    if (zone.get("status") or "") != "active":
        return {"domain": d, "ok": False, "steps": [
            {"step": "Email routing", "ok": False,
             "detail": "zone chua active — doi Cloudflare kich hoat roi chay lai"}]}
    steps, ok = _apply_email_preset(d, cfg, cf, zone)
    rule_steps = [s for s in steps
                  if str(s.get("step", "")).startswith("Rule ")]
    already = ok and bool(rule_steps) and not any(
        any(m in s.get("detail", "") for m in _WRITE_MARKS)
        or s.get("step") == "Bat Email Routing"
        for s in steps)
    return {"domain": d, "ok": ok, "steps": steps, "already_set": already}


def _is_rate_limited(st):
    """Trang thai check co dinh gioi han toc do khong — 429 cua
    Cloudflare o bat ky buoc nao, hoac Namecheap bao throttling."""
    texts = [str(st.get("error", ""))]
    for k in ("email", "https"):
        v = st.get(k) or {}
        texts.append(str(v.get("error", "")))
    texts.append(str(st.get("registrar_ns_error", "")))
    joined = " ".join(texts)
    return ("429" in joined or "throttl" in joined.lower()
            or "too many requests" in joined.lower())


def _setup_hit_rate_limit(res):
    """Ket qua setup co dinh gioi han toc do Namecheap/Cloudflare khong
    (buoc nao loi voi 'Too many requests' / 'throttling' / 429) — de
    dung loat setup, khong de domain sau cung loi theo."""
    for s in (res or {}).get("steps", []):
        if not s.get("ok") and _is_rate_limit_msg(str(s.get("detail", ""))):
            return True
    return False


def _save_check_result(st, d, new_st):
    """Luu ket qua check: neu check moi LOI (error) ma trang thai cu da
    tot (co zone) thi GIU trang thai cu — loi thoang qua (429/throttle)
    khong duoc xoa du lieu tot da co. Domain moi (chua co trang thai)
    hoac trang thai cu cung loi thi luu loi moi de con thay ma kiem
    tra lai."""
    old = st.get(d)
    if new_st.get("error") and old and old.get("zone"):
        return False  # giu trang thai cu
    st[d] = new_st
    return True


def _check_dns_light(domains, cfg, st):
    """Kiem tra NHANH chi cot DNS: lay danh sach zone CF 1 lan, DoH
    tung domain, so NS cong voi NS zone -> ns_ok. Khong goi records /
    email / HTTPS / cache, khong goi Namecheap — nhanh, khong dinh
    gioi han toc do. Chi luu trang thai NHE (light: chi co DNS) khi
    chua co trang thai day du hoac trang thai cu dang loi; trang thai
    day du thi giu nguyen. Tra ve dict {domain: trang thai nhe}."""
    if not cfg.get("cf_token"):
        return {"__error__": "chua nhap Cloudflare API token (muc Cai dat)"}
    cf = _cf_client(cfg)
    zones = {}
    for z in cf.list_zones():
        zones[str(z.get("name", "")).lower()] = z
    out = {}
    for d in domains:
        d = str(d).strip().lower()
        if not d:
            continue
        old = st.get(d)
        if old and not old.get("error") and not old.get("light"):
            continue  # da co trang thai day du (co zone hay khong), giu nguyen
        light = {"domain": d, "checked_at": time.strftime("%H:%M:%S %d/%m/%Y"),
                 "zone": None, "doh_ns": [], "registrar_ns": [],
                 "ns_ok": False, "records": [], "records_ok": None,
                 "records_preset": None,
                 "email": None, "https": None, "error": None, "light": True}
        z = zones.get(d)
        if z:
            light["zone"] = {"id": z.get("id"), "status": z.get("status"),
                             "name_servers": [x.lower() for x in z.get("name_servers", [])]}
        try:
            light["doh_ns"] = _doh(d)
        except Exception as e:
            light["error"] = f"DoH lookup loi: {e}"
        if light["zone"] and light["doh_ns"]:
            want = {x.lower() for x in light["zone"]["name_servers"]}
            light["ns_ok"] = bool(want) and set(light["doh_ns"]) == want
        st[d] = light
        out[d] = light
    return out


def _check_many(domains, cfg, st):
    """Kiem tra nhieu domain: nghi 0.3s giua cac domain de bot dot
    API, DUNG NGAY khi gap gioi han toc do (429/throttling) de khong
    dot tiep. Bo qua goi Namecheap trong loat (xem check_domain).
    Tra ve (so domain da check, co bi gioi han khong, so domain giu
    trang thai cu do check moi loi)."""
    checked = 0
    limited = False
    kept = 0
    for d in domains:
        new_st = check_domain(d, cfg, skip_nc=True)
        if not _save_check_result(st, d, new_st):
            kept += 1
        checked += 1
        if _is_rate_limited(new_st):
            limited = True
            break
        time.sleep(0.3)
    return checked, limited, kept


# ------------------------------------------------------------------- server
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n).decode() or "{}")
        except Exception:
            return {}

    def do_GET(self):
        p = urllib.parse.urlparse(self.path)
        path = p.path
        if path in ("/", "/index.html"):
            f = WEB_DIR / "index.html"
            if not f.exists():
                self.send_response(404)
                self.end_headers()
                return
            body = f.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/state":
            cfg = load_config()
            st = load_status()
            self._json({"config": cfg, "status": st,
                        "sheet_auto_last": dict(SHEET_AUTO_LAST)})
            return
        if path == "/api/my-ip":
            try:
                self._json({"ip": my_ip()})
            except Exception as e:
                self._json({"error": str(e)}, 500)
            return
        if path == "/api/account-zones":
            cfg = load_config()
            if not cfg.get("cf_token"):
                self._json({"error": "chua nhap Cloudflare API token"}, 400)
                return
            try:
                zones = _cf_client(cfg).list_zones()
                # Tu dong dua het zone trong tai khoan vao danh sach quan
                # ly — khoi phai bam Them tung domain. Domain nao user da
                # xoa khoi bang (unmanaged) thi khong dua lai.
                unmanaged = set(cfg.get("unmanaged") or [])
                added = 0
                for z in zones:
                    nm = (z.get("name") or "").strip().lower()
                    if nm and nm not in cfg["domains"] and nm not in unmanaged:
                        cfg["domains"].append(nm)
                        added += 1
                if added:
                    save_config(cfg)
                self._json({"zones": [{"name": z.get("name"),
                                       "status": z.get("status"),
                                       "name_servers": z.get("name_servers", [])}
                                      for z in zones]})
            except Exception as e:
                self._json({"error": str(e)}, 500)
            return
        if path == "/api/chrome-profiles":
            # Danh sach ho so Chrome tren may nay (de chon o tab
            # Auto-renew) — doc tu thu muc User Data cua Chrome.
            cfg = load_config()
            profiles, last_used = _list_chrome_profiles(cfg)
            self._json({"ok": True,
                        "profiles": [{"dir": d, "name": n} for d, n in profiles],
                        "last_used": last_used,
                        "profile_dir": str(_chrome_profile_dir(cfg))})
            return
        if path == "/api/autorenew-status":
            # Trang thai auto-renew that tren web Namecheap (co AutoRenew
            # cua getList) + ngay het han. Chi doc, khong doi gi. Tra ve
            # {domain: true/false} va {domain: "YYYY-MM-DD"}.
            # ?force=1 de tai lai bo qua cache. Dung chung cache getList
            # voi /api/expiry (1 lan doc phuc vu ca 2 cot).
            cfg = load_config()
            if not _nc_configured(cfg):
                self._json({"error": "chua nhap du Namecheap API "
                                     "(user/key/IP) o muc Cai dat"}, 400)
                return
            try:
                force = urllib.parse.parse_qs(p.query).get("force", [""])[0] == "1"
                info, err = _fetch_nc_list_info(cfg, force=force)
                self._json({"ok": not err,
                            "autorenew": {d: v["autorenew"] for d, v in info.items()},
                            "expiry": {d: v["expiry"] for d, v in info.items()
                                       if v["expiry"]},
                            "count": len(info), "error": err})
            except Exception as e:
                self._json({"ok": False, "autorenew": {}, "expiry": {},
                            "count": 0, "error": str(e)[:200]})
            return
        if path == "/api/mail-check":
            # Check mail: doc 2 sheet xuat ban, loc domain Pass (cot H
            # Sheet 1), join mail theo cot Domain (Sheet 2). Chi doc,
            # khong ghi gi vao sheet. ?force=1 de tai lai bo qua cache.
            cfg = load_config()
            try:
                force = urllib.parse.parse_qs(p.query).get("force", [""])[0] == "1"
                data, err = _fetch_mail_check(cfg, force=force)
                if err:
                    self._json({"ok": False, "error": err})
                else:
                    self._json({"ok": True, "data": data})
            except Exception as e:
                self._json({"ok": False, "error": str(e)[:200]})
            return
        if path == "/api/sus-status":
            # Tinh trang Sus cua domain (doc tu sheet Sus rieng, chi
            # doc de hien o tab Auto-renew). Tra ve {domain: {status,
            # date (Ngay ve, cot C), days (So ngay, cot H)}}.
            cfg = load_config()
            sus, err = _fetch_sus_map(cfg)
            self._json({"ok": not err, "sus": sus, "count": len(sus),
                        "error": err})
            return
        if path == "/api/expiry":
            # Ngay het han cua cac domain o Namecheap (de hien cot Han +
            # loc domain sap het han). Chi doc getList, khong doi gi
            # tren Namecheap. Dung chung cache getList voi
            # /api/autorenew-status.
            cfg = load_config()
            if not _nc_configured(cfg):
                self._json({"error": "chua nhap du Namecheap API "
                                     "(user/key/IP) o muc Cai dat"}, 400)
                return
            try:
                info, err = _fetch_nc_list_info(cfg)
                self._json({"ok": not err,
                            "expiry": {d: v["expiry"] for d, v in info.items()
                                       if v["expiry"]},
                            "error": err})
            except Exception as e:
                self._json({"error": str(e)}, 500)
            return
        self._json({"error": "khong co route"}, 404)

    def do_POST(self):
        p = urllib.parse.urlparse(self.path)
        path = p.path
        body = self._body()
        cfg = load_config()
        st = load_status()
        try:
            if path == "/api/settings":
                for k in ("cf_token", "cf_account_id", "nc_user", "nc_api_key", "nc_client_ip",
                          "chrome_profile_dir", "chrome_profile", "sus_sheet_csv_url",
                          "mail_sheet1_csv_url", "mail_sheet2_csv_url"):
                    if k in body:
                        cfg[k] = str(body[k]).strip()
                save_config(cfg)
                self._json({"ok": True})
            elif path == "/api/domains":
                d = str(body.get("domain", "")).strip().lower()
                if not d or "." not in d:
                    self._json({"error": "domain khong hop le"}, 400)
                elif d in cfg["domains"]:
                    self._json({"error": "domain da co trong danh sach"}, 400)
                else:
                    cfg["domains"].append(d)
                    cfg["unmanaged"] = [x for x in (cfg.get("unmanaged") or [])
                                        if x != d]
                    save_config(cfg)
                    self._json({"ok": True, "domains": cfg["domains"]})
            elif path == "/api/domains/delete":
                d = str(body.get("domain", "")).strip().lower()
                cfg["domains"] = [x for x in cfg["domains"] if x != d]
                # Ghi nho de lan sau tai danh sach tai khoan khong dua lai.
                unm = set(cfg.get("unmanaged") or [])
                unm.add(d)
                cfg["unmanaged"] = sorted(unm)
                st.pop(d, None)
                save_config(cfg)
                save_status(st)
                self._json({"ok": True, "domains": cfg["domains"]})
            elif path == "/api/check":
                if body.get("all"):
                    checked, limited, kept = _check_many(cfg["domains"], cfg, st)
                    save_status(st)
                    self._json({"ok": True, "status": st,
                                "checked": checked,
                                "total": len(cfg["domains"]),
                                "rate_limited": limited, "kept": kept})
                elif body.get("domains"):
                    # Kiem tra 1 loat domain (trang hien tai cua bang, hoac
                    # 1 loat 30 cai cua "Kiem tra tat ca") — chi tra ve
                    # trang thai cua loat do.
                    wanted = [str(x).strip().lower() for x in body["domains"]
                              if str(x).strip()]
                    checked, limited, kept = _check_many(wanted, cfg, st)
                    save_status(st)
                    self._json({"ok": True,
                                "status": {d: st[d] for d in wanted if d in st},
                                "checked": checked, "rate_limited": limited,
                                "kept": kept})
                else:
                    d = str(body.get("domain", "")).strip().lower()
                    _save_check_result(st, d, check_domain(d, cfg))
                    save_status(st)
                    self._json({"ok": True, "status": {d: st[d]}})
            elif path == "/api/check-dns":
                # Kiem tra NHANH chi cot DNS (zone + DoH) cho 1 loat domain
                # — dung chung danh sach zone cua tai khoan nen khong can
                # check day du tung domain. Nhanh, khong dinh 429.
                wanted = [str(x).strip().lower() for x in (body.get("domains") or [])
                          if str(x).strip()]
                out = _check_dns_light(wanted, cfg, st)
                if "__error__" in out:
                    self._json({"ok": False, "error": out["__error__"]})
                    return
                save_status(st)
                self._json({"ok": True, "status": out})
            elif path == "/api/setup":
                d = str(body.get("domain", "")).strip().lower()
                preset = str(body.get("preset") or cfg.get("default_preset", "")).strip()
                res = setup_domain(d, preset, cfg, skip_records=bool(body.get("skip_records")))
                _save_check_result(st, d, check_domain(d, cfg, skip_nc=True))
                save_status(st)
                self._json(res)
            elif path == "/api/setup-all":
                preset = str(body.get("preset") or cfg.get("default_preset", "")).strip()
                _skip = body.get("skip_records")
                skip_set = (set(str(x).strip().lower() for x in _skip) if isinstance(_skip, list)
                            else (set(cfg["domains"]) if _skip else set()))
                results = []
                rate_limited = False
                for i, d in enumerate(cfg["domains"]):
                    res = setup_domain(d, preset, cfg, skip_records=(d in skip_set))
                    results.append(res)
                    _save_check_result(st, d, check_domain(d, cfg, skip_nc=True))
                    if _setup_hit_rate_limit(res):
                        # Namecheap dang gioi han toc do — dung loat, khong
                        # de cac domain sau cung loi theo.
                        rate_limited = True
                        break
                    if i < len(cfg["domains"]) - 1:
                        # Nghi giua cac domain de Namecheap API khong bi don.
                        time.sleep(3)
                save_status(st)
                self._json({"ok": all(r["ok"] for r in results), "results": results,
                            "rate_limited": rate_limited})
            elif path == "/api/setup-batch":
                # Setup theo danh sach nhap trong phien nay (moi dong 1 domain).
                # Chi xu ly dung cac domain trong danh sach — khong dong
                # den domain khac trong tai khoan.
                raw = body.get("domains") or []
                if isinstance(raw, str):
                    raw = raw.splitlines()
                seen, domains = set(), []
                for x in raw:
                    d = str(x).strip().lower()
                    if d and "." in d and d not in seen:
                        seen.add(d)
                        domains.append(d)
                preset = str(body.get("preset") or cfg.get("default_preset", "")).strip()
                _skip = body.get("skip_records")
                skip_set = (set(str(x).strip().lower() for x in _skip) if isinstance(_skip, list)
                            else (set(domains) if _skip else set()))
                results = []
                rate_limited = False
                for i, d in enumerate(domains):
                    if d not in cfg["domains"]:
                        cfg["domains"].append(d)
                    res = setup_domain(d, preset, cfg, skip_records=(d in skip_set))
                    results.append(res)
                    _save_check_result(st, d, check_domain(d, cfg, skip_nc=True))
                    if _setup_hit_rate_limit(res):
                        # Namecheap dang gioi han toc do — dung loat, khong
                        # de cac domain sau cung loi theo.
                        rate_limited = True
                        break
                    if i < len(domains) - 1:
                        # Nghi giua cac domain de Namecheap API khong bi don
                        # (setup loat 5-10 domain khong dinh "Too many requests").
                        time.sleep(3)
                save_config(cfg)
                save_status(st)
                self._json({"ok": all(r["ok"] for r in results), "results": results,
                            "rate_limited": rate_limited})
            elif path == "/api/autorenew-browser-prepare":
                # Nut 1 (tab Auto-renew): mo tab Namecheap Domain List de
                # dang nhap truoc — chay chrome_autorenew.py --prepare-nc
                # (script nam ngay trong thu muc Dashboard).
                if not AR_SCRIPT.is_file():
                    self._json({"ok": False, "error":
                                "khong tim thay chrome_autorenew.py trong "
                                "thu muc Dashboard"}, 400)
                    return
                args = ["--prepare-nc"]
                prof = str(body.get("profile") or "").strip()
                if prof:
                    args += ["--profile-dir", prof]
                rc, logtxt = _run_chrome_autorenew(args)
                self._json({"ok": rc == 0, "log": logtxt})
            elif path == "/api/autorenew-browser":
                # Nut 2 (tab Auto-renew): bam toggle Auto-Renew that tren
                # Chrome cho tung domain trong danh sach. Domain nao toggle
                # da BAT roi thi ben Chrome tu bo qua (khong bam).
                if not AR_SCRIPT.is_file():
                    self._json({"ok": False, "error":
                                "khong tim thay chrome_autorenew.py trong "
                                "thu muc Dashboard"}, 400)
                    return
                raw = body.get("domains") or []
                if isinstance(raw, str):
                    raw = raw.splitlines()
                seen, domains = set(), []
                for x in raw:
                    d = str(x).strip().lower()
                    if d and "." in d and d not in seen:
                        seen.add(d)
                        domains.append(d)
                if not domains:
                    self._json({"ok": False, "error": "chua nhap domain nao"}, 400)
                    return
                privacy = "yes" if str(body.get("privacy") or "").strip().lower() == "yes" else "no"
                before = set(glob.glob(str(AR_REPORTS_DIR / "chrome_setup_*.csv")))
                tmpdir = Path(tempfile.mkdtemp(prefix="dashboard-ar-"))
                dom_file = tmpdir / "domains.txt"
                dom_file.write_text("\n".join(domains) + "\n", encoding="utf-8")
                args = ["--domains-file", str(dom_file),
                        "--autorenew", "on", "--privacy", privacy, "--keep-open"]
                prof = str(body.get("profile") or "").strip()
                if prof:
                    args += ["--profile-dir", prof]
                rc, logtxt = _run_chrome_autorenew(args)
                results = _read_newest_ar_report(before)
                self._json({"ok": rc == 0 and bool(results),
                            "results": results, "log": logtxt,
                            "error": "" if results else
                            "chay xong nhung khong doc duoc bao cao — xem log"})
            elif path == "/api/email-preset":
                ep = {"enabled": bool(body.get("enabled", True)), "rules": []}
                for r in (body.get("rules") or []):
                    m = str(r.get("match", "")).strip()
                    t = str(r.get("to", "")).strip()
                    if m:
                        ep["rules"].append({"match": m, "to": t})
                cfg["email_preset"] = ep
                save_config(cfg)
                self._json({"ok": True, "email_preset": ep})
            elif path == "/api/apply-preset":
                d = str(body.get("domain", "")).strip().lower()
                preset = str(body.get("preset") or cfg.get("default_preset", "")).strip()
                res = apply_preset(d, preset, cfg)
                _save_check_result(st, d, check_domain(d, cfg, skip_nc=True))
                save_status(st)
                self._json(res)
            elif path == "/api/domain-detail":
                # Chi tiet day du cho popup Edit: DNS + records + email + https.
                d = str(body.get("domain", "")).strip().lower()
                det = check_domain(d, cfg)
                det["presets"] = cfg.get("presets", {})
                det["default_preset"] = cfg.get("default_preset", "")
                det["gsc"] = _gsc_from_records(det.get("records"))
                if det.get("zone"):
                    try:
                        cf = _cf_client(cfg)
                        er = cf.get_email_routing(det["zone"]["id"])
                        det["email_detail"] = {
                            "enabled": bool(er.get("enabled")),
                            "status": er.get("status"),
                            "rules": cf.list_email_rules(det["zone"]["id"]),
                            "addresses": cf.list_email_addresses(det["zone"]["id"]),
                        }
                    except Exception as e:
                        det["email_detail"] = {"enabled": False, "status": None,
                                               "rules": [], "addresses": [],
                                               "error": str(e)}
                self._json(det)
            elif path == "/api/gsc":
                # Them/cap nhat record TXT @ xac minh Google Search Console.
                # Ma xac minh nhap tay (lay tu Search Console).
                d = str(body.get("domain", "")).strip().lower()
                ok, detail = set_gsc_record(d, body.get("value", ""), cfg)
                _save_check_result(st, d, check_domain(d, cfg, skip_nc=True))
                save_status(st)
                self._json({"domain": d, "ok": ok, "detail": detail})
            elif path == "/api/setup-email":
                # Chi chay buoc Email Routing (bat routing + ap rule theo
                # email preset) — dung cho tab 'Setup email' (chi domain
                # zone active). Zone chua active thi _apply_email_preset
                # tu doi toi da 5 phut roi bao ro.
                d = str(body.get("domain", "")).strip().lower()
                cf, zone, err = _get_zone_or_error(d, cfg)
                if err:
                    self._json({"domain": d, "ok": False, "steps": [
                        {"step": "Tim zone", "ok": False, "detail": err}]})
                    return
                steps, ok = _apply_email_preset(d, cfg, cf, zone)
                _save_check_result(st, d, check_domain(d, cfg, skip_nc=True))
                save_status(st)
                self._json({"domain": d, "ok": ok, "steps": steps})
            elif path == "/api/setup-email-batch":
                # Setup email cho nhieu domain 1 lan (o nhap o tab Setup
                # email): domain nao da set du (routing da bat + rule da
                # co san) thi bo qua, chua set thi setup. Dung ngay khi
                # gap 429 de khong dot tiep.
                domains = []
                for x in (body.get("domains") or []):
                    x = str(x).strip().lower()
                    if x and x not in domains:
                        domains.append(x)
                results = []
                for d in domains:
                    r = _setup_email_one(d, cfg)
                    results.append(r)
                    _save_check_result(st, d, check_domain(d, cfg, skip_nc=True))
                    if any("429" in str(s.get("detail", ""))
                           for s in r.get("steps", [])):
                        results.append({"domain": "(con lai)", "ok": False,
                                        "steps": [{"step": "Dung loat",
                                                   "ok": False, "detail":
                                                   "Cloudflare gioi han toc do (429) — "
                                                   "doi vai phut roi chay tiep"}]})
                        break
                    time.sleep(0.5)
                save_status(st)
                self._json({"ok": True, "results": results})
            elif path == "/api/sync-ns":
                d = str(body.get("domain", "")).strip().lower()
                cf, zone, err = _get_zone_or_error(d, cfg)
                if err:
                    self._json({"domain": d, "ok": False, "steps": [
                        {"step": "Tim zone", "ok": False, "detail": err}]})
                    return
                ns = [x.lower() for x in zone.get("name_servers", [])]
                steps, ok = _sync_ns(d, ns, cfg)
                _save_check_result(st, d, check_domain(d, cfg, skip_nc=True))
                save_status(st)
                self._json({"domain": d, "ok": ok, "steps": steps})
            elif path == "/api/set-records":
                d = str(body.get("domain", "")).strip().lower()
                recs = body.get("records") or []
                cf, zone, err = _get_zone_or_error(d, cfg)
                if err:
                    self._json({"domain": d, "ok": False, "steps": [
                        {"step": "Tim zone", "ok": False, "detail": err}]})
                    return
                try:
                    deleted = cf.delete_all_records(zone["id"])
                    added = 0
                    for r in recs:
                        ct = str(r.get("content", "")).strip()
                        if not ct:
                            continue
                        # Record he thong Email Routing / GSC da duoc
                        # delete_all_records GIU LAI — khong tao lai
                        # keo trung lap.
                        probe = {"type": str(r.get("type", "A")),
                                 "name": expand_name(d, r.get("name", "")),
                                 "content": ct}
                        if _is_email_routing_record(probe) or _is_gsc_record(probe):
                            continue
                        cf.create_record(zone["id"], str(r.get("type", "A")).upper(),
                                         expand_name(d, r.get("name", "")),
                                         ct, bool(r.get("proxied", True)))
                        added += 1
                    _save_check_result(st, d, check_domain(d, cfg, skip_nc=True))
                    save_status(st)
                    self._json({"domain": d, "ok": True, "steps": [
                        {"step": "Luu records", "ok": True,
                         "detail": f"da xoa {deleted} record cu, them {added} record moi"}]})
                except Exception as e:
                    self._json({"domain": d, "ok": False, "steps": [
                        {"step": "Luu records", "ok": False, "detail": str(e)}]})
            elif path == "/api/email-enable":
                d = str(body.get("domain", "")).strip().lower()
                enable = bool(body.get("enabled"))
                cf, zone, err = _get_zone_or_error(d, cfg)
                if err:
                    self._json({"domain": d, "ok": False, "error": err})
                    return
                try:
                    if enable:
                        cf.enable_email_routing(zone["id"])
                        msg = ("da bat Email Routing (Cloudflare da them/khoa 3 MX "
                               "+ SPF + DKIM — mail cu se mat)")
                    else:
                        cf.disable_email_routing(zone["id"])
                        msg = "da tat Email Routing"
                    _save_check_result(st, d, check_domain(d, cfg, skip_nc=True))
                    save_status(st)
                    self._json({"domain": d, "ok": True, "message": msg})
                except Exception as e:
                    self._json({"domain": d, "ok": False, "error": str(e)})
            elif path == "/api/email-rule":
                d = str(body.get("domain", "")).strip().lower()
                match = str(body.get("match", "")).strip()
                dests = [x.strip() for x in (body.get("destinations") or [])
                         if str(x).strip()]
                cf, zone, err = _get_zone_or_error(d, cfg)
                if err:
                    self._json({"domain": d, "ok": False, "error": err})
                    return
                if not match or not dests:
                    self._json({"domain": d, "ok": False,
                                "error": "thieu dia chi gui (match) hoac dia chi nhan"})
                    return
                try:
                    r = cf.create_email_rule(zone["id"], match, dests)
                    self._json({"domain": d, "ok": True, "rule": r})
                except Exception as e:
                    self._json({"domain": d, "ok": False, "error": str(e),
                                "hint": ("neu bao 'Destination address is not verified' thi "
                                         "them dia chi dich o duoi roi bam link xac minh trong mail")})
            elif path == "/api/email-rule-delete":
                d = str(body.get("domain", "")).strip().lower()
                rid = str(body.get("rule_id", "")).strip()
                cf, zone, err = _get_zone_or_error(d, cfg)
                if err:
                    self._json({"domain": d, "ok": False, "error": err})
                    return
                try:
                    cf.delete_email_rule(zone["id"], rid)
                    self._json({"domain": d, "ok": True})
                except Exception as e:
                    self._json({"domain": d, "ok": False, "error": str(e)})
            elif path == "/api/email-address":
                d = str(body.get("domain", "")).strip().lower()
                email = str(body.get("email", "")).strip()
                cf, zone, err = _get_zone_or_error(d, cfg)
                if err:
                    self._json({"domain": d, "ok": False, "error": err})
                    return
                try:
                    r = cf.create_email_address(zone["id"], email)
                    self._json({"domain": d, "ok": True, "address": r,
                                "message": "da gui mail xac minh toi " + email +
                                           " — bam link trong mail roi moi dung duoc"})
                except Exception as e:
                    self._json({"domain": d, "ok": False, "error": str(e)})
            elif path == "/api/set-https":
                d = str(body.get("domain", "")).strip().lower()
                cf, zone, err = _get_zone_or_error(d, cfg)
                if err:
                    self._json({"domain": d, "ok": False, "error": err})
                    return
                try:
                    out = cf.set_https(zone["id"],
                                       ssl=body.get("ssl"),
                                       always_https=body.get("always_https"),
                                       rewrites=body.get("rewrites"),
                                       min_tls=body.get("min_tls_version"))
                    _save_check_result(st, d, check_domain(d, cfg, skip_nc=True))
                    save_status(st)
                    self._json({"domain": d, "ok": True, "https": out})
                except Exception as e:
                    self._json({"domain": d, "ok": False, "error": str(e)})
            elif path == "/api/set-https-batch":
                # Ap HTTPS cho NHIEU domain da chon (checkbox o bang).
                # ssl lay tu body (dropdown o bulk bar) neu co, khong thi
                # lay tu https_preset; cac cai khac lay tu https_preset.
                raw = body.get("domains") or []
                domains = [str(x).strip().lower() for x in raw
                           if str(x).strip() and "." in str(x)]
                hp = cfg.get("https_preset") or {}
                ssl = body.get("ssl") or hp.get("ssl")
                results = []
                for d in domains:
                    cf, zone, err = _get_zone_or_error(d, cfg)
                    if err:
                        results.append({"domain": d, "ok": False, "error": err})
                        continue
                    try:
                        out = cf.set_https(zone["id"], ssl=ssl,
                                           always_https=hp.get("always_https"),
                                           rewrites=hp.get("rewrites"),
                                           min_tls=hp.get("min_tls_version"))
                        _save_check_result(st, d, check_domain(d, cfg, skip_nc=True))
                        results.append({"domain": d, "ok": True, "https": out})
                    except Exception as e:
                        results.append({"domain": d, "ok": False, "error": str(e)})
                save_status(st)
                self._json({"ok": all(r["ok"] for r in results), "results": results})
            elif path == "/api/apply-records-batch":
                # Ap preset record cho NHIEU domain da chon (chi record,
                # khong dong NS/email/HTTPS).
                raw = body.get("domains") or []
                domains = [str(x).strip().lower() for x in raw
                           if str(x).strip() and "." in str(x)]
                preset = str(body.get("preset") or cfg.get("default_preset", "")).strip()
                recs = cfg.get("presets", {}).get(preset)
                if not recs:
                    self._json({"ok": False, "error": f"preset '{preset}' khong ton tai"})
                    return
                results = []
                for d in domains:
                    cf, zone, err = _get_zone_or_error(d, cfg)
                    if err:
                        results.append({"domain": d, "ok": False, "error": err})
                        continue
                    try:
                        detail, okb = _sync_records(d, recs, cf, zone)
                        _save_check_result(st, d, check_domain(d, cfg, skip_nc=True))
                        results.append({"domain": d, "ok": okb, "detail": detail})
                    except Exception as e:
                        results.append({"domain": d, "ok": False, "error": str(e)})
                save_status(st)
                self._json({"ok": all(r["ok"] for r in results), "results": results})
            elif path == "/api/cache-bypass":
                d = str(body.get("domain", "")).strip().lower()
                enable = bool(body.get("enabled", True))
                cf, zone, err = _get_zone_or_error(d, cfg)
                if err:
                    self._json({"domain": d, "ok": False, "error": err})
                    return
                try:
                    if enable:
                        r = cf.set_cache_bypass(zone["id"])
                        msg = ("da co rule By Pass roi" if r.get("already")
                               else "da them rule By Pass (tat ca request bypass cache)")
                    else:
                        r = cf.remove_cache_bypass(zone["id"])
                        msg = ("da go rule By Pass" if r.get("removed")
                               else "khong co rule By Pass de go")
                    _save_check_result(st, d, check_domain(d, cfg, skip_nc=True))
                    save_status(st)
                    self._json({"domain": d, "ok": True, "message": msg})
                except Exception as e:
                    self._json({"domain": d, "ok": False, "error": str(e)})
            elif path == "/api/cache-bypass-batch":
                raw = body.get("domains") or []
                domains = [str(x).strip().lower() for x in raw
                           if str(x).strip() and "." in str(x)]
                enable = bool(body.get("enabled", True))
                results = []
                for d in domains:
                    cf, zone, err = _get_zone_or_error(d, cfg)
                    if err:
                        results.append({"domain": d, "ok": False, "error": err})
                        continue
                    try:
                        if enable:
                            r = cf.set_cache_bypass(zone["id"])
                            detail = "da co roi" if r.get("already") else "da them rule By Pass"
                        else:
                            r = cf.remove_cache_bypass(zone["id"])
                            detail = "da go rule" if r.get("removed") else "khong co rule"
                        _save_check_result(st, d, check_domain(d, cfg, skip_nc=True))
                        results.append({"domain": d, "ok": True, "detail": detail})
                    except Exception as e:
                        results.append({"domain": d, "ok": False, "error": str(e)})
                save_status(st)
                self._json({"ok": all(r["ok"] for r in results), "results": results})
            elif path == "/api/delete-zones-batch":
                # XOA ZONE tren Cloudflare (khong khoi phuc duoc) + xoa khoi
                # bang quan ly. Chi chay khi user xac nhan o giao dien.
                raw = body.get("domains") or []
                domains = [str(x).strip().lower() for x in raw
                           if str(x).strip() and "." in str(x)]
                results = []
                for d in domains:
                    cf, zone, err = _get_zone_or_error(d, cfg)
                    if err:
                        results.append({"domain": d, "ok": False, "error": err})
                        continue
                    try:
                        cf.delete_zone(zone["id"])
                        cfg["domains"] = [x for x in cfg["domains"] if x != d]
                        unm = set(cfg.get("unmanaged") or [])
                        unm.add(d)
                        cfg["unmanaged"] = sorted(unm)
                        st.pop(d, None)
                        results.append({"domain": d, "ok": True,
                                        "detail": "da xoa zone tren Cloudflare"})
                    except Exception as e:
                        results.append({"domain": d, "ok": False, "error": str(e)})
                save_config(cfg)
                save_status(st)
                self._json({"ok": all(r["ok"] for r in results), "results": results})
            elif path == "/api/https-preset":
                hp = {"enabled": bool(body.get("enabled", True)),
                      "ssl": str(body.get("ssl") or "flexible"),
                      "always_https": str(body.get("always_https") or "on"),
                      "min_tls_version": str(body.get("min_tls_version") or "1.2"),
                      "rewrites": str(body.get("rewrites") or "on")}
                cfg["https_preset"] = hp
                save_config(cfg)
                self._json({"ok": True, "https_preset": hp})
            elif path == "/api/presets":
                name = str(body.get("name", "")).strip()
                recs = body.get("records") or []
                if not name:
                    self._json({"error": "thieu ten preset"}, 400)
                    return
                clean = []
                for r in recs:
                    t = str(r.get("type", "A")).strip().upper()
                    nm = str(r.get("name", "")).strip()
                    ct = str(r.get("content", "")).strip()
                    if not ct:
                        continue
                    clean.append({"type": t, "name": nm or "@", "content": ct,
                                  "proxied": bool(r.get("proxied", True))})
                if not clean:
                    self._json({"error": "preset can it nhat 1 record co Content"}, 400)
                    return
                # Khong cho 2 preset trung het cau hinh (1 domain chi co
                # 1 cau hinh, trung nhau thi khong biet dang dung preset nao).
                new_keys = sorted((r["type"], r["name"], r["content"], r["proxied"]) for r in clean)
                for other, orecs in (cfg.get("presets") or {}).items():
                    if other == name:
                        continue
                    other_keys = sorted((str(x.get("type", "A")).upper(),
                                         str(x.get("name", "")).strip(),
                                         str(x.get("content", "")).strip(),
                                         bool(x.get("proxied", True))) for x in orecs)
                    if new_keys == other_keys:
                        self._json({"error": f"preset nay trung het record voi preset '{other}' — doi ten hoac sua record roi luu lai"}, 400)
                        return
                cfg["presets"][name] = clean
                if body.get("set_default"):
                    cfg["default_preset"] = name
                save_config(cfg)
                self._json({"ok": True, "presets": cfg["presets"],
                            "default_preset": cfg.get("default_preset")})
            elif path == "/api/presets/delete":
                name = str(body.get("name", "")).strip()
                cfg["presets"].pop(name, None)
                if cfg.get("default_preset") == name:
                    cfg["default_preset"] = next(iter(cfg["presets"]), "")
                save_config(cfg)
                self._json({"ok": True, "presets": cfg["presets"],
                            "default_preset": cfg.get("default_preset")})
            elif path == "/api/preset-default":
                name = str(body.get("name", "")).strip()
                if name in cfg["presets"]:
                    cfg["default_preset"] = name
                    save_config(cfg)
                self._json({"ok": True, "default_preset": cfg.get("default_preset")})
            elif path == "/api/sheet-settings":
                # Luu cau hinh Google Sheet (link CSV, dong bat dau, Sheet ID,
                # duong dan file JSON key service account, tu dong setup).
                if "csv_url" in body:
                    cfg["sheet_csv_url"] = str(body.get("csv_url") or "").strip()
                if "start_row" in body:
                    try:
                        cfg["sheet_start_row"] = int(body.get("start_row"))
                    except (TypeError, ValueError):
                        pass
                if "sheet_id" in body:
                    cfg["sheet_id"] = str(body.get("sheet_id") or "").strip()
                if "sa_key" in body:
                    cfg["sheet_sa_key"] = str(body.get("sa_key") or "").strip()
                if "auto" in body:
                    cfg["sheet_auto"] = bool(body.get("auto"))
                if "interval" in body:
                    try:
                        cfg["sheet_auto_interval"] = max(
                            1, min(60, int(body.get("interval"))))
                    except (TypeError, ValueError):
                        pass
                save_config(cfg)
                self._json({"ok": True})
            elif path == "/api/sheet-rule":
                # Them rule moi hoac sua rule cu (doi ten duoc, khong can
                # xoa tao lai): gui {index} de sua rule o vi tri do, khong
                # gui index thi them moi. {name, preset} bat buoc.
                name = str(body.get("name", "")).strip()
                preset = str(body.get("preset", "")).strip()
                if not name:
                    self._json({"error": "thiếu tên Loại web trong sheet"}, 400)
                    return
                if preset not in cfg.get("presets", {}):
                    self._json({"error": f"preset '{preset}' không tồn tại"}, 400)
                    return
                rules = cfg.setdefault("sheet_rules", [])
                idx = body.get("index")
                if isinstance(idx, int) and 0 <= idx < len(rules):
                    # Sua: doi ten duoc, chi cam trung ten voi rule KHAC.
                    for i, r in enumerate(rules):
                        if i != idx and (r.get("name") or "").strip() == name:
                            self._json({"error": f"đã có rule cho '{name}' rồi"}, 400)
                            return
                    rules[idx] = {"name": name, "preset": preset}
                else:
                    for r in rules:
                        if (r.get("name") or "").strip() == name:
                            self._json({"error": f"đã có rule cho '{name}' rồi"}, 400)
                            return
                    rules.append({"name": name, "preset": preset})
                save_config(cfg)
                self._json({"ok": True, "rules": rules})
            elif path == "/api/sheet-rule-delete":
                idx = body.get("index")
                rules = cfg.setdefault("sheet_rules", [])
                if isinstance(idx, int) and 0 <= idx < len(rules):
                    rules.pop(idx)
                    save_config(cfg)
                self._json({"ok": True, "rules": rules})
            elif path == "/api/sheet-read":
                # Doc sheet + phan loai (khong setup) — de xem truoc.
                # Dong xung dot thi dua luon vao hang cho duyet.
                rows, err, src = sheet_read_rows(cfg)
                if err:
                    self._json({"ok": False, "error": err})
                    return
                rows = _sheet_classify(rows, cfg, st)
                review = cfg.setdefault("sheet_review", [])
                for r in rows:
                    if r.get("action") == "review":
                        q = {"domain": r["domain"], "row": r["row"],
                             "preset": r["preset"], "current": r.get("current"),
                             "loai": r["loai"]}
                        if q not in review:
                            review.append(q)
                save_config(cfg)
                save_status(st)
                self._json({"ok": True, "rows": rows, "review": review,
                            "source": src})
            elif path == "/api/sheet-setup":
                # Setup tuan tu tung dong clean (xong 1 moi qua dong sau).
                if not SHEET_LOCK.acquire(blocking=False):
                    self._json({"ok": False,
                                "error": "đang có 1 lượt setup sheet khác chạy, thử lại sau"},
                               409)
                    return
                try:
                    res = sheet_setup_clean(cfg, st)
                finally:
                    SHEET_LOCK.release()
                self._json(res)
            elif path == "/api/sheet-setup-one":
                # Setup 1 dong (frontend goi tuan tu tung hang).
                d = str(body.get("domain", "")).strip().lower()
                row = body.get("row")
                preset = str(body.get("preset") or "").strip()
                custom_id = str(body.get("custom_id") or "").strip()
                try:
                    attempts = max(1, min(int(body.get("attempts") or 1), 5))
                except (TypeError, ValueError):
                    attempts = 1
                if not d or not isinstance(row, int) or not preset:
                    self._json({"error": "thiếu domain/row/preset"}, 400)
                    return
                if not SHEET_LOCK.acquire(blocking=False):
                    self._json({"error": "đang có 1 lượt setup sheet khác chạy, thử lại sau"}, 409)
                    return
                try:
                    res = _sheet_setup_one(d, row, preset, cfg, st,
                                           custom_id=custom_id, attempts=attempts)
                finally:
                    SHEET_LOCK.release()
                save_config(cfg)
                save_status(st)
                self._json({"ok": True, "result": res})
            elif path == "/api/mail-note":
                # Luu ghi chu Check mail (chi luu trong app — config.json,
                # khong ghi nguoc vao Google Sheet). Key = dia chi mail
                # (viet thuong). Ghi chu rong thi xoa.
                mail = str(body.get("mail") or "").strip().lower()
                note = str(body.get("note") or "")
                if not mail:
                    self._json({"error": "thiếu địa chỉ mail"}, 400)
                    return
                notes = dict(cfg.get("mail_notes") or {})
                if note.strip():
                    notes[mail] = note
                else:
                    notes.pop(mail, None)
                cfg["mail_notes"] = notes
                save_config(cfg)
                # Ghi chu doi thi bo cache de lan doc sau thay ngay.
                MAIL_CACHE["at"] = 0.0
                self._json({"ok": True})
            elif path == "/api/sheet-accept":
                # Chap nhan domain trong hang cho duyet: setup theo preset
                # trong sheet, ghi 'da set up' ve cot J, xoa khoi hang doi.
                d = str(body.get("domain", "")).strip().lower()
                review = cfg.setdefault("sheet_review", [])
                q = next((x for x in review if x.get("domain") == d), None)
                if not q:
                    self._json({"error": "domain không có trong hàng chờ duyệt"}, 400)
                    return
                res = _sheet_setup_one(d, q["row"], q["preset"], cfg, st,
                                       custom_id=q.get("note_l", ""))
                if res.get("action") == "done":
                    cfg["sheet_review"] = [x for x in review if x.get("domain") != d]
                save_config(cfg)
                save_status(st)
                self._json({"ok": True, "result": res})
            elif path == "/api/sheet-reject":
                # Tu choi: xoa khoi hang cho duyet + nho de khong dua lai.
                d = str(body.get("domain", "")).strip().lower()
                cfg["sheet_review"] = [x for x in cfg.get("sheet_review", [])
                                       if x.get("domain") != d]
                if d and d not in cfg.get("sheet_ignored", []):
                    cfg.setdefault("sheet_ignored", []).append(d)
                save_config(cfg)
                self._json({"ok": True})
            else:
                self._json({"error": "khong co route"}, 404)
        except Exception:
            traceback.print_exc()
            self._json({"error": "loi server"}, 500)


def main():
    threading.Thread(target=_sheet_auto_loop, daemon=True).start()
    print(f"Domain Dashboard chay o http://127.0.0.1:{PORT} (chi local, Ctrl+C de dung)")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
