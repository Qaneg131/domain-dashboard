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
import json
import os
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cf_api import CFClient, CFError, _is_gsc_record, expand_name
from dns_check import doh_ns, my_ip
from nc_api import NCClient, NCError
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


def _records_match(records, preset, domain):
    """Record hien co co khop preset khong (so theo type + ten day du + content)."""
    want = {(r["type"].upper(), expand_name(domain, r.get("name", "")).lower(),
             str(r.get("content", "")).strip())
            for r in preset}
    have = {(r.get("type", "").upper(), (r.get("name", "") or "").lower(),
             str(r.get("content", "")).strip())
            for r in records}
    return want and want <= have


def check_domain(domain, cfg):
    """Trang thai 1 domain: zone CF, NS (DNS cong + Namecheap), records."""
    domain = domain.strip().lower()
    st = {"domain": domain, "checked_at": time.strftime("%H:%M:%S %d/%m/%Y"),
          "zone": None, "doh_ns": [], "registrar_ns": [],
          "ns_ok": False, "records": [], "records_ok": None,
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
        if _nc_configured(cfg):
            try:
                st["registrar_ns"] = _nc_client(cfg).get_nameservers(domain)
            except Exception as e:
                st["registrar_ns_error"] = str(e)
        if st["zone"] and st["doh_ns"]:
            want = {x.lower() for x in st["zone"]["name_servers"]}
            st["ns_ok"] = bool(want) and set(st["doh_ns"]) == want
        if st["zone"]:
            try:
                st["records"] = [{"id": r.get("id"), "type": r.get("type"),
                                  "name": r.get("name"), "content": r.get("content"),
                                  "proxied": r.get("proxied")}
                                 for r in cf.list_records(st["zone"]["id"])]
                preset = cfg.get("presets", {}).get(cfg.get("default_preset", ""), [])
                if preset:
                    st["records_ok"] = _records_match(st["records"], preset, domain)
            except Exception as e:
                st["error"] = f"khong doc duoc records: {e}"
            # Email Routing + HTTPS (can quyen Zone Settings / Email Routing
            # Rules tren token; loi thi ghi rieng, khong lam hong check chung).
            try:
                er = cf.get_email_routing(st["zone"]["id"])
                rules = cf.list_email_rules(st["zone"]["id"])
                st["email"] = {"enabled": bool(er.get("enabled")),
                               "status": er.get("status"),
                               "rules": len(rules)}
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
        reg = _nc_client(cfg).get_nameservers(domain)
        if set(reg) != set(ns):
            step("Doi NS o Namecheap", False, f"registrar tra ve khac: {', '.join(reg)}")
            return steps, False
        step("Doi NS o Namecheap", True, f"registrar da ghi: {', '.join(reg)}")
        propagated = False
        for _ in range(6):
            time.sleep(5)
            try:
                if set(_doh(domain)) == set(ns):
                    propagated = True
                    break
            except Exception:
                pass
        step("DNS lan truyen", propagated,
             "NS moi da thay tren DNS cong" if propagated
             else "registrar da doi, DNS cong chua thay (cho them roi bam Kiem tra)")
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

        # Catch-all khong nam trong existing_by_match — no duoc xu ly
        # rieng qua endpoint /rules/catch_all (xoa/tap qua /rules/{id}
        # se bi API tra 'Invalid rule operation').
        existing_by_match = {match_key(r): r for r in existing
                             if match_key(r) != "*"}
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
            cur = existing_by_match.get(match)
            cur_dests = []
            if cur:
                for a in cur.get("actions") or []:
                    cur_dests.extend(a.get("value") or [])
            if cur and sorted(cur_dests) == sorted(dests):
                steps.append({"step": f"Rule {match}", "ok": True,
                              "detail": "da co san"})
                continue
            if cur:
                cf.delete_email_rule(zone["id"], cur["id"])
            cf.create_email_rule(zone["id"], match, dests)
            steps.append({"step": f"Rule {match} -> {', '.join(dests)}",
                          "ok": True, "detail": "da tao"})
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


def setup_domain(domain, preset_name, cfg):
    """Setup 1 domain: tao zone CF -> doi NS Namecheap -> ap preset record.
    Tra ve {domain, ok, steps:[{step, ok, detail}]}."""
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

    # 3. Xoa het record cu (LUON chay khi Setup — theo yeu cau cua user:
    # domain nao dem di setup thi tu dong xoa record cu) + ap preset neu co.
    # delete_all_records GIU LAI record he thong cua Email Routing (MX/SPF/
    # DKIM cua Cloudflare) nen khong lam hong email da bat; MX cua nha
    # cung cap cu thi bi xoa — nho vay buoc Email Routing moi bat duoc
    # (khong thi loi 2008 'Non-Cloudflare MX records exist').
    try:
        deleted = cf.delete_all_records(zone["id"])
        if preset_name:
            preset = cfg.get("presets", {}).get(preset_name)
            if not preset:
                step("Xoa record cu", False,
                     f"da xoa {deleted} record cu nhung preset '{preset_name}' "
                     "khong ton tai — vao muc Preset record de tao/chon preset "
                     "mac dinh roi Setup lai")
            else:
                added = 0
                for r in preset:
                    cf.create_record(zone["id"], r["type"].upper(),
                                     expand_name(domain, r.get("name", "")),
                                     str(r.get("content", "")).strip(),
                                     bool(r.get("proxied", True)))
                    added += 1
                step(f"Ap preset '{preset_name}'", True,
                     f"da xoa {deleted} record cu, them {added} record moi")
        else:
            step("Xoa record cu", False,
                 f"da xoa {deleted} record cu nhung chua chon preset mac dinh "
                 "— vao muc Preset record de chon preset mac dinh roi Setup lai")
    except Exception as e:
        step("Xoa record cu / Ap preset", False, str(e))
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
        deleted = cf.delete_all_records(zone["id"])
        added = 0
        for r in preset:
            cf.create_record(zone["id"], r["type"].upper(),
                             expand_name(domain, r.get("name", "")),
                             str(r.get("content", "")).strip(), bool(r.get("proxied", True)))
            added += 1
        return {"domain": domain, "ok": True, "steps": [
            {"step": f"Ap preset '{preset_name}'", "ok": True,
             "detail": f"da xoa {deleted} record cu, them {added} record moi"}]}
    except Exception as e:
        return {"domain": domain, "ok": False, "steps": [
            {"step": f"Ap preset '{preset_name}'", "ok": False, "detail": str(e)}]}


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
            self._json({"config": cfg, "status": st})
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
                self._json({"zones": [{"name": z.get("name"),
                                       "status": z.get("status"),
                                       "name_servers": z.get("name_servers", [])}
                                      for z in zones]})
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
                for k in ("cf_token", "cf_account_id", "nc_user", "nc_api_key", "nc_client_ip"):
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
                    save_config(cfg)
                    self._json({"ok": True, "domains": cfg["domains"]})
            elif path == "/api/domains/delete":
                d = str(body.get("domain", "")).strip().lower()
                cfg["domains"] = [x for x in cfg["domains"] if x != d]
                st.pop(d, None)
                save_config(cfg)
                save_status(st)
                self._json({"ok": True, "domains": cfg["domains"]})
            elif path == "/api/check":
                if body.get("all"):
                    for d in cfg["domains"]:
                        st[d] = check_domain(d, cfg)
                    save_status(st)
                    self._json({"ok": True, "status": st})
                else:
                    d = str(body.get("domain", "")).strip().lower()
                    st[d] = check_domain(d, cfg)
                    save_status(st)
                    self._json({"ok": True, "status": {d: st[d]}})
            elif path == "/api/setup":
                d = str(body.get("domain", "")).strip().lower()
                preset = str(body.get("preset") or cfg.get("default_preset", "")).strip()
                res = setup_domain(d, preset, cfg)
                st[d] = check_domain(d, cfg)
                save_status(st)
                self._json(res)
            elif path == "/api/setup-all":
                preset = str(body.get("preset") or cfg.get("default_preset", "")).strip()
                results = []
                for d in cfg["domains"]:
                    res = setup_domain(d, preset, cfg)
                    results.append(res)
                    st[d] = check_domain(d, cfg)
                save_status(st)
                self._json({"ok": all(r["ok"] for r in results), "results": results})
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
                results = []
                for d in domains:
                    if d not in cfg["domains"]:
                        cfg["domains"].append(d)
                    res = setup_domain(d, preset, cfg)
                    results.append(res)
                    st[d] = check_domain(d, cfg)
                save_config(cfg)
                save_status(st)
                self._json({"ok": all(r["ok"] for r in results), "results": results})
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
                st[d] = check_domain(d, cfg)
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
                st[d] = check_domain(d, cfg)
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
                st[d] = check_domain(d, cfg)
                save_status(st)
                self._json({"domain": d, "ok": ok, "steps": steps})
            elif path == "/api/sync-ns":
                d = str(body.get("domain", "")).strip().lower()
                cf, zone, err = _get_zone_or_error(d, cfg)
                if err:
                    self._json({"domain": d, "ok": False, "steps": [
                        {"step": "Tim zone", "ok": False, "detail": err}]})
                    return
                ns = [x.lower() for x in zone.get("name_servers", [])]
                steps, ok = _sync_ns(d, ns, cfg)
                st[d] = check_domain(d, cfg)
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
                        cf.create_record(zone["id"], str(r.get("type", "A")).upper(),
                                         expand_name(d, r.get("name", "")),
                                         ct, bool(r.get("proxied", True)))
                        added += 1
                    st[d] = check_domain(d, cfg)
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
                    st[d] = check_domain(d, cfg)
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
                    st[d] = check_domain(d, cfg)
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
                        st[d] = check_domain(d, cfg)
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
                        deleted = cf.delete_all_records(zone["id"])
                        added = 0
                        for r in recs:
                            cf.create_record(zone["id"], r["type"].upper(),
                                             expand_name(d, r.get("name", "")),
                                             str(r.get("content", "")).strip(),
                                             bool(r.get("proxied", True)))
                            added += 1
                        st[d] = check_domain(d, cfg)
                        results.append({"domain": d, "ok": True,
                                        "detail": f"da xoa {deleted} record cu, them {added} record moi"})
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
                    st[d] = check_domain(d, cfg)
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
                        st[d] = check_domain(d, cfg)
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
            else:
                self._json({"error": "khong co route"}, 404)
        except Exception:
            traceback.print_exc()
            self._json({"error": "loi server"}, 500)


def main():
    print(f"Domain Dashboard chay o http://127.0.0.1:{PORT} (chi local, Ctrl+C de dung)")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
