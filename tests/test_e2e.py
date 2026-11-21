#!/usr/bin/env python3
"""E2E: chay server dashboard that (port 8787) voi mock API o 18099,
goi cac endpoint bang urllib nhu trinh duyet that se goi."""
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from test_api import MockHandler, STATE, NS1, NS2

MOCK_PORT = 18099
BASE = f"http://127.0.0.1:{MOCK_PORT}"
os.environ["CF_API_BASE"] = BASE + "/client/v4"
os.environ["NC_API_BASE"] = BASE + "/xml.response"
os.environ["DOH_BASE"] = BASE + "/dns-query"
os.environ["PORT"] = "8787"


def call(method, path, body=None):
    req = urllib.request.Request("http://127.0.0.1:8787" + path,
                                 data=json.dumps(body).encode() if body else None,
                                 method=method)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode())


def main():
    # Reset config/status ve trang thai sach truoc khi chay — lan chay
    # truoc de lai batch1.com trong config.json (va status cu trong
    # status.json) nen E2E phai doc lap, khong phu thuoc lan chay truoc.
    import copy
    import store
    # Reset config ve mac dinh (trong — khong chua thong tin user), roi
    # them 2 domain test vao (E2E can 2 domain nay de check).
    cfg0 = copy.deepcopy(store.DEFAULT_CONFIG)
    cfg0["domains"] = ["testa.com", "testb.com"]
    store.save_config(cfg0)
    if store.STATUS_FILE.exists():
        store.STATUS_FILE.unlink()
    mock = ThreadingHTTPServer(("127.0.0.1", MOCK_PORT), MockHandler)
    threading.Thread(target=mock.serve_forever, daemon=True).start()
    STATE["nc_ns"]["testa.com"] = [NS1, NS2]
    STATE["nc_ns"]["testb.com"] = ["dns1.registrar-servers.com",
                                      "dns2.registrar-servers.com"]
    import app as dash
    threading.Thread(target=dash.main, daemon=True).start()
    time.sleep(1)

    html = urllib.request.urlopen("http://127.0.0.1:8787/", timeout=10).read().decode()
    assert "Domain Dashboard" in html and "presetSelect" in html
    print("HTML OK,", len(html), "bytes", flush=True)

    st = call("GET", "/api/state")
    assert st["config"]["domains"] == ["testa.com", "testb.com"], st["config"]
    assert st["config"]["presets"]["web"][0]["content"] == "1.11.11.11"
    print("STATE OK", flush=True)

    assert call("POST", "/api/settings", {
        "cf_token": "test-token", "cf_account_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "nc_user": "testuser", "nc_api_key": "test-key",
        "nc_client_ip": "1.2.3.4"})["ok"]
    print("SETTINGS OK", flush=True)

    chk = call("POST", "/api/check", {"all": True})
    print("CHECK:", {d: (v["ns_ok"], v["zone"]["status"] if v["zone"] else None,
                         len(v["records"])) for d, v in chk["status"].items()}, flush=True)

    res = call("POST", "/api/setup", {"domain": "testb.com", "preset": "web"})
    print("SETUP testb:", res["ok"], [(s["step"], s["ok"]) for s in res["steps"]], flush=True)
    assert res["ok"], res

    assert call("POST", "/api/presets", {"name": "shop", "records": [
        {"type": "A", "name": "@", "content": "2.2.2.2", "proxied": True}]})["ok"]
    # Luu preset trung het record voi preset 'shop' -> bi tu choi (400).
    try:
        call("POST", "/api/presets", {"name": "shop-copy", "records": [
            {"type": "A", "name": "@", "content": "2.2.2.2", "proxied": True}]})
        raise AssertionError("duplicated preset should be rejected")
    except urllib.error.HTTPError as e:
        dup = json.loads(e.read().decode())
        assert e.code == 400 and "trung" in (dup.get("error") or ""), dup
    # Sua lai 1 record thi luu duoc.
    assert call("POST", "/api/presets", {"name": "shop-copy", "records": [
        {"type": "A", "name": "@", "content": "3.3.3.3", "proxied": True}]})["ok"]

    # Sheet rules: mac dinh trong — them 2 rule roi moi test them / doi ten / trung ten / xoa.
    # (preset trong E2E chi co web/shop/shop-copy nen rule dung 'shop'/'web'.)
    rules0 = call("GET", "/api/state")["config"]["sheet_rules"]
    assert rules0 == [], rules0
    assert call("POST", "/api/sheet-rule", {"name": "Web Thuần Hân", "preset": "web"})["ok"]
    assert call("POST", "/api/sheet-rule", {"name": "Web Thuần Long", "preset": "shop"})["ok"]
    rules0 = call("GET", "/api/state")["config"]["sheet_rules"]
    assert rules0 == [{"name": "Web Thuần Hân", "preset": "web"},
                      {"name": "Web Thuần Long", "preset": "shop"}], rules0
    r1 = call("POST", "/api/sheet-rule", {"name": "Web Thuần", "preset": "shop"})
    assert r1["ok"] and len(r1["rules"]) == 3, r1
    r2 = call("POST", "/api/sheet-rule", {"index": 2, "name": "Web Thuần Mới", "preset": "web"})
    assert r2["ok"] and r2["rules"][2] == {"name": "Web Thuần Mới", "preset": "web"}, r2
    try:
        call("POST", "/api/sheet-rule", {"name": "Web Thuần Hân", "preset": "shop"})
        raise AssertionError("duplicate rule name should be rejected")
    except urllib.error.HTTPError as e:
        assert e.code == 400, e.code
    r3 = call("POST", "/api/sheet-rule-delete", {"index": 2})
    assert r3["ok"] and len(r3["rules"]) == 2, r3
    print("SHEET-RULES OK: them/doi ten/trung ten bi tu choi/xoa", flush=True)

    # Sheet auto-setup: bat/tat + khoang thoi gian, /api/state tra sheet_auto_last.
    a1 = call("POST", "/api/sheet-settings", {"auto": True, "interval": 3})
    assert a1["ok"], a1
    st1 = call("GET", "/api/state")
    assert st1["config"]["sheet_auto"] is True, st1["config"]
    assert st1["config"]["sheet_auto_interval"] == 3, st1["config"]
    assert "sheet_auto_last" in st1 and st1["sheet_auto_last"]["at"] == "", st1
    a2 = call("POST", "/api/sheet-settings", {"auto": False})
    assert a2["ok"] and call("GET", "/api/state")["config"]["sheet_auto"] is False
    print("SHEET-AUTO OK: bat/tat + interval + state tra sheet_auto_last", flush=True)
    res2 = call("POST", "/api/apply-preset", {"domain": "testb.com", "preset": "shop"})
    print("APPLY shop:", res2["ok"], res2["steps"][0]["detail"], flush=True)
    assert res2["ok"], res2

    assert "newtest.com" in call("POST", "/api/domains", {"domain": "newtest.com"})["domains"]
    assert "newtest.com" not in call("POST", "/api/domains/delete",
                                      {"domain": "newtest.com"})["domains"]
    print("ADD/DELETE DOMAIN OK", flush=True)

    det = call("POST", "/api/domain-detail", {"domain": "testb.com"})
    assert det["zone"] and det["email_detail"] is not None and det["https"], det
    print("DETAIL OK: email enabled =", det["email_detail"]["enabled"],
          "| https =", det["https"], flush=True)

    h = call("POST", "/api/set-https", {"domain": "testb.com", "ssl": "full",
                                        "always_https": "on", "rewrites": "on"})
    assert h["ok"] and h["https"]["ssl"] == "full", h
    print("SET-HTTPS OK", flush=True)

    em = call("POST", "/api/email-enable", {"domain": "testb.com", "enabled": True})
    assert em["ok"], em
    rl = call("POST", "/api/email-rule", {"domain": "testb.com", "match": "*",
                                          "destinations": ["owner@gmail.com"]})
    assert rl["ok"], rl
    det2 = call("POST", "/api/domain-detail", {"domain": "testb.com"})
    assert det2["email_detail"]["enabled"] is True
    assert len(det2["email_detail"]["rules"]) == 1, det2["email_detail"]
    print("EMAIL OK: enabled + 1 catch-all rule", flush=True)

    ns = call("POST", "/api/sync-ns", {"domain": "testb.com"})
    assert ns["ok"], ns
    print("SYNC-NS OK:", [(s["step"], s["ok"]) for s in ns["steps"]], flush=True)

    hp = call("POST", "/api/https-preset", {"enabled": True, "ssl": "flexible",
                                            "always_https": "on",
                                            "min_tls_version": "1.2",
                                            "rewrites": "on"})
    assert hp["ok"] and hp["https_preset"]["ssl"] == "flexible", hp
    hb = call("POST", "/api/set-https-batch", {"domains": ["testb.com"]})
    assert hb["ok"] and hb["results"][0]["https"]["min_tls"] == "1.2", hb
    print("HTTPS-PRESET + BATCH OK", flush=True)

    az = call("GET", "/api/account-zones")
    assert "testb.com" in [z["name"] for z in az["zones"]], az
    print("ACCOUNT-ZONES OK:", [z["name"] for z in az["zones"]], flush=True)

    ep = call("POST", "/api/email-preset", {"enabled": True, "rules": [
        {"match": "info@", "to": "owner@gmail.com"},
        {"match": "*", "to": "owner@gmail.com"}]})
    assert ep["ok"] and len(ep["email_preset"]["rules"]) == 2, ep
    print("EMAIL-PRESET OK", flush=True)

    batch = call("POST", "/api/setup-batch", {"domains": ["batch1.com", "batch1.com ", " batch2.com"]})
    assert len(batch["results"]) == 2, batch  # trung + khoang trang da loai
    assert all(r["ok"] for r in batch["results"]), batch
    det3 = call("POST", "/api/domain-detail", {"domain": "batch1.com"})
    assert det3["email_detail"]["enabled"] is True
    assert len(det3["email_detail"]["rules"]) == 2, det3["email_detail"]
    print("SETUP-BATCH OK: 2 domain, moi domain co 2 rule email", flush=True)

    # Bulk edit: By Pass cache + ap record + HTTPS Full + xoa zone.
    cb = call("POST", "/api/cache-bypass-batch", {"domains": ["batch1.com", "batch2.com"], "enabled": True})
    assert cb["ok"] and all(r["ok"] for r in cb["results"]), cb
    det4 = call("POST", "/api/domain-detail", {"domain": "batch1.com"})
    assert det4["cache_bypass"] is True, det4
    cb2 = call("POST", "/api/cache-bypass-batch", {"domains": ["batch1.com"], "enabled": True})
    assert cb2["results"][0]["detail"] == "da co roi", cb2
    print("CACHE-BYPASS-BATCH OK (tao lan 2 khong trung)", flush=True)

    ar = call("POST", "/api/apply-records-batch", {"domains": ["batch1.com"], "preset": "web"})
    assert ar["ok"] and "da xoa" in ar["results"][0]["detail"], ar
    print("APPLY-RECORDS-BATCH OK:", ar["results"][0]["detail"], flush=True)

    hs = call("POST", "/api/set-https-batch", {"domains": ["batch1.com"], "ssl": "full"})
    assert hs["ok"] and hs["results"][0]["https"]["ssl"] == "full", hs
    print("SET-HTTPS-BATCH SSL OVERRIDE OK: full", flush=True)

    dz = call("POST", "/api/delete-zones-batch", {"domains": ["batch2.com"]})
    assert dz["ok"] and dz["results"][0]["ok"], dz
    st2 = call("GET", "/api/state")
    assert "batch2.com" not in st2["config"]["domains"], st2["config"]["domains"]
    print("DELETE-ZONES-BATCH OK: batch2.com da xoa khoi bang", flush=True)

    # Popup tao rule '*' (catch-all) — phai di qua endpoint rieng, khong trung.
    er = call("POST", "/api/email-rule", {"domain": "batch1.com", "match": "*",
                                          "destinations": ["owner@gmail.com"]})
    assert er["ok"], er
    det5 = call("POST", "/api/domain-detail", {"domain": "batch1.com"})
    assert len(det5["email_detail"]["rules"]) == 2, det5["email_detail"]
    print("EMAIL-RULE CATCH-ALL OK: khong tao trung rule", flush=True)

    # Google Search Console: them TXT @ bang ma nhap tay.
    gsc = call("POST", "/api/gsc", {"domain": "batch1.com", "value": "gsc-test-123"})
    assert gsc["ok"] and gsc["detail"] == "da them", gsc
    gsc2 = call("POST", "/api/gsc", {"domain": "batch1.com",
                                     "value": "google-site-verification=gsc-test-123"})
    assert gsc2["ok"] and gsc2["detail"] == "da co san", gsc2
    det6 = call("POST", "/api/domain-detail", {"domain": "batch1.com"})
    assert det6["gsc"] == "google-site-verification=gsc-test-123", det6.get("gsc")
    assert det6["email"]["enabled"] is True and det6["email"]["dns_locked"] is True, det6["email"]
    print("GSC OK: them TXT @, trung thi giu, domain-detail hien dung", flush=True)
    print("EMAIL TAB OK: routing Enabled + DNS records Locked hien dung", flush=True)

    # Ap preset kieu gop: record tu do (khong nam trong preset) phai con.
    det7 = call("POST", "/api/domain-detail", {"domain": "batch1.com"})
    recs7 = [{"type": r["type"], "name": r["name"], "content": r["content"],
              "proxied": r["proxied"]} for r in det7["records"]
             if not r["content"].endswith(".mx.cloudflare.net")
             and "include:_spf.mx.cloudflare.net" not in r["content"]
             and "DKIM1" not in r["content"]]
    recs7.append({"type": "TXT", "name": "@", "content": "custom-keep=1", "proxied": False})
    sr = call("POST", "/api/set-records", {"domain": "batch1.com", "records": recs7})
    assert sr["ok"], sr
    ar2 = call("POST", "/api/apply-records-batch", {"domains": ["batch1.com"], "preset": "web"})
    assert ar2["ok"], ar2
    det8 = call("POST", "/api/domain-detail", {"domain": "batch1.com"})
    assert any(r["type"] == "TXT" and r["content"] == "custom-keep=1" for r in det8["records"]), det8["records"]
    # set-records khong lam trung record he thong Email Routing.
    assert sum(1 for r in det8["records"] if r["type"] == "MX") == 3, det8["records"]
    print("MERGE OK: ap preset giu lai record tu do, khong trung MX", flush=True)

    # Tab Setup email: chi chay buoc email (da co san thi giu nguyen).
    se = call("POST", "/api/setup-email", {"domain": "batch1.com"})
    assert se["ok"], se
    assert any(s["step"].startswith("Rule info@batch1.com") and s["detail"] == "da co san"
               for s in se["steps"]), se["steps"]
    print("SETUP-EMAIL OK: chi chay buoc email, rule cu giu nguyen", flush=True)

    # Tu dong gop: /api/account-zones dua het zone vao danh sach quan ly
    # (batch1.com co zone nhung chua trong bang); domain da xoa zone
    # (batch2.com) thi khong dua lai.
    az2 = call("GET", "/api/account-zones")
    assert "batch1.com" in [z["name"] for z in az2["zones"]], az2
    st2 = call("GET", "/api/state")
    assert "batch1.com" in st2["config"]["domains"], st2["config"]["domains"]
    assert "batch2.com" not in st2["config"]["domains"], st2["config"]["domains"]
    # /api/check theo loat: mo trang nao tu kiem tra 5 domain trang do.
    chk2 = call("POST", "/api/check", {"domains": ["batch1.com"]})
    assert "batch1.com" in chk2["status"], chk2
    assert chk2["status"]["batch1.com"]["zone"]["status"] == "active", chk2
    print("AUTO-MERGE + CHECK-BATCH OK: het zone vao bang, check theo loat", flush=True)

    # Setup email theo danh sach: da set thi bo qua, chua set thi setup.
    eb = call("POST", "/api/setup-email-batch",
              {"domains": ["batch1.com", "nozone-x.com"]})
    assert eb["ok"], eb
    by_d = {r["domain"]: r for r in eb["results"]}
    assert by_d["batch1.com"].get("already_set") is True, by_d
    assert by_d["nozone-x.com"]["ok"] is False, by_d
    print("SETUP-EMAIL-BATCH OK: da set thi bo qua, khong zone thi bao loi", flush=True)

    # Kiem tra nhanh DNS: chi check zone + DoH, khong goi Namecheap.
    STATE["zones"]["lightcheck.com"] = {"id": "z-light", "name": "lightcheck.com",
                                        "status": "active",
                                        "name_servers": [NS1, NS2]}
    STATE["nc_ns"]["lightcheck.com"] = [NS1, NS2]
    ld = call("POST", "/api/check-dns", {"domains": ["lightcheck.com", "batch1.com"]})
    assert ld["ok"], ld
    assert ld["status"]["lightcheck.com"]["light"] and ld["status"]["lightcheck.com"]["ns_ok"], ld
    # batch1.com da co trang thai day du (tu buoc AUTO-MERGE) -> khong bi ghi de.
    assert "lightcheck.com" in ld["status"] and "batch1.com" not in ld["status"], ld
    st3 = call("GET", "/api/state")
    assert st3["status"]["lightcheck.com"]["light"], st3["status"]
    print("CHECK-DNS NHANH OK: chi DNS, khong goi Namecheap, khong ghi de trang thai day du", flush=True)

    # Han domain: doc ngay het han tu Namecheap getList.
    STATE.setdefault("nc_expiry", {})["ar1.com"] = "12/04/2026"
    STATE["nc_expiry"]["ar2.com"] = "01/15/2027"
    STATE.setdefault("nc_autorenew", {})["ar1.com"] = True
    STATE["nc_autorenew"]["ar2.com"] = False
    exp = call("GET", "/api/expiry")
    assert exp["ok"], exp
    assert exp["expiry"]["ar1.com"] == "2026-12-04", exp
    assert exp["expiry"]["ar2.com"] == "2027-01-15", exp
    print("EXPIRY OK: ngay het han tu Namecheap getList, dang YYYY-MM-DD", flush=True)

    # Auto-renew bang Chrome: dung script GIA (fake chrome_autorenew.py)
    # de test toan bo duong day subprocess + doc bao cao CSV ma khong
    # mo Chrome that. Fake ghi bao cao CSV giong that. Chi duong dan
    # script that sang fake bang cach gan lai bien module cua app.
    import tempfile
    fake_dir = tempfile.mkdtemp(prefix="fake-ar-")
    fake_reports = os.path.join(fake_dir, "reports")
    os.makedirs(fake_reports, exist_ok=True)
    fake_lines = [
        "import argparse, csv, os, sys",
        "p = argparse.ArgumentParser()",
        "p.add_argument('--prepare-nc', action='store_true')",
        "p.add_argument('--autorenew')",
        "p.add_argument('--privacy', default='no')",
        "p.add_argument('--profile-dir')",
        "p.add_argument('--domains-file')",
        "p.add_argument('--keep-open', action='store_true')",
        "a = p.parse_args()",
        "print('fake: profile-dir =', a.profile_dir)",
        "if a.prepare_nc:",
        "    print('fake: da mo tab Namecheap')",
        "    sys.exit(0)",
        "doms = [l.strip() for l in open(a.domains_file, encoding='utf-8') if l.strip()]",
        "out = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'reports', 'chrome_setup_20260101_000000.csv')",
        "with open(out, 'w', newline='', encoding='utf-8') as fh:",
        "    w = csv.writer(fh)",
        "    w.writerow(['domain', 'status', 'nameservers', 'records', 'error'])",
        "    for i, d in enumerate(doms):",
        "        w.writerow([d, 'da-bat' if i == 0 else 'ok', '', '', ''])",
        "print('fake: xong', len(doms), 'domain')",
    ]
    fake_script = os.path.join(fake_dir, "chrome_autorenew.py")
    with open(fake_script, "w", encoding="utf-8") as f:
        f.write("\n".join(fake_lines) + "\n")
    dash.AR_SCRIPT = Path(fake_script)
    dash.AR_REPORTS_DIR = Path(fake_reports)
    prep = call("POST", "/api/autorenew-browser-prepare", {"profile": "Profile 1"})
    assert prep["ok"], prep
    assert "fake: da mo tab Namecheap" in prep.get("log", ""), prep
    assert "profile-dir = Profile 1" in prep.get("log", ""), prep
    ar = call("POST", "/api/autorenew-browser",
              {"domains": ["ar1.com", "ar2.com"], "privacy": "no", "profile": "Profile 1"})
    assert ar["ok"], ar
    assert len(ar["results"]) == 2, ar
    assert ar["results"][0]["domain"] == "ar1.com" and ar["results"][0]["status"] == "da-bat", ar
    assert ar["results"][1]["status"] == "ok", ar
    # Khong nhap domain nao thi bao loi 400 (khong phu thuoc tool).
    try:
        call("POST", "/api/autorenew-browser", {"domains": [], "privacy": "no"})
        raise AssertionError("autorenew phai bao 400 khi khong co domain")
    except urllib.error.HTTPError as e:
        assert e.code == 400, e
        payload = json.loads(e.read().decode())
        assert "chua nhap domain" in payload.get("error", ""), payload
    # Endpoint liet ke ho so Chrome: tra ve dang dung (o may test khong
    # co Chrome that nen danh sach rong, nhung phai co cac khoa).
    profs = call("GET", "/api/chrome-profiles")
    assert profs["ok"] and "profiles" in profs and "last_used" in profs, profs
    # Tinh trang Sus: doc tu file CSV gia (file://) qua endpoint.
    sus_csv = os.path.join(fake_dir, "sus.csv")
    with open(sus_csv, "w", encoding="utf-8") as f:
        f.write(",WEB,Ngay ve,Bao cao,Loai web,Tinh Trang Sus,REG,So ngay\n"
                ",susa.com,20/12/2025,,Shopify,Đã Sus,An,336\n"
                ",susb.com,02/01/2026,,Shopify,Xanh,An,323\n"
                ",susc.com,05/01/2026,,Shopify,Sus mail,An,320\n")
    call("POST", "/api/settings", {"sus_sheet_csv_url": "file://" + sus_csv})
    sus = call("GET", "/api/sus-status")
    assert sus["ok"] and sus["count"] == 3, sus
    assert sus["sus"] == {
        "susa.com": {"status": "Đã Sus", "date": "20/12/2025", "days": "336"},
        "susb.com": {"status": "Xanh", "date": "02/01/2026", "days": "323"},
        "susc.com": {"status": "Sus mail", "date": "05/01/2026", "days": "320"},
    }, sus
    call("POST", "/api/settings", {"sus_sheet_csv_url": ""})
    sus2 = call("GET", "/api/sus-status")
    assert not sus2["ok"] and "chua nhap link" in sus2.get("error", ""), sus2
    print("SUS-STATUS OK: doc CSV sheet Sus qua endpoint + bao loi khi chua nhap link", flush=True)
    # Trang thai auto-renew: doc co AutoRenew tu getList (mock).
    # (E2E da set nc_user/key/IP + NC_API_BASE tro ve mock o tren.)
    ar = call("GET", "/api/autorenew-status")
    assert ar["ok"] and ar["autorenew"].get("ar1.com") is True, ar
    assert ar["autorenew"].get("ar2.com") is False, ar
    print("AUTORENEW-STATUS OK: doc co AutoRenew tu getList qua endpoint", flush=True)
    print("AUTORENEW-BROWSER OK: prepare + chay fake script + doc CSV + chon profile + bao loi khi khong co domain + liet ke ho so", flush=True)
    mock.shutdown()
    print("E2E OK")


if __name__ == "__main__":
    main()
