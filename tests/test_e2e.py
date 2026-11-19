#!/usr/bin/env python3
"""E2E: chay server dashboard that (port 8787) voi mock API o 18099,
goi cac endpoint bang urllib nhu trinh duyet that se goi."""
import json
import os
import sys
import threading
import time
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
    mock = ThreadingHTTPServer(("127.0.0.1", MOCK_PORT), MockHandler)
    threading.Thread(target=mock.serve_forever, daemon=True).start()
    STATE["nc_ns"]["elvellia.com"] = [NS1, NS2]
    STATE["nc_ns"]["welvella.com"] = ["dns1.registrar-servers.com",
                                      "dns2.registrar-servers.com"]
    import app as dash
    threading.Thread(target=dash.main, daemon=True).start()
    time.sleep(1)

    html = urllib.request.urlopen("http://127.0.0.1:8787/", timeout=10).read().decode()
    assert "Domain Dashboard" in html and "presetSelect" in html
    print("HTML OK,", len(html), "bytes", flush=True)

    st = call("GET", "/api/state")
    assert st["config"]["domains"] == ["elvellia.com", "welvella.com"], st["config"]
    assert st["config"]["presets"]["web"][0]["content"] == "1.11.11.11"
    print("STATE OK", flush=True)

    assert call("POST", "/api/settings", {
        "cf_token": "test-token", "cf_account_id": "c30bace6d8d169fb77ae278b7d0fc82a",
        "nc_user": "tuanah252", "nc_api_key": "test-key",
        "nc_client_ip": "1.2.3.4"})["ok"]
    print("SETTINGS OK", flush=True)

    chk = call("POST", "/api/check", {"all": True})
    print("CHECK:", {d: (v["ns_ok"], v["zone"]["status"] if v["zone"] else None,
                         len(v["records"])) for d, v in chk["status"].items()}, flush=True)

    res = call("POST", "/api/setup", {"domain": "welvella.com", "preset": "web"})
    print("SETUP welvella:", res["ok"], [(s["step"], s["ok"]) for s in res["steps"]], flush=True)
    assert res["ok"], res

    assert call("POST", "/api/presets", {"name": "shop", "records": [
        {"type": "A", "name": "@", "content": "2.2.2.2", "proxied": True}]})["ok"]
    res2 = call("POST", "/api/apply-preset", {"domain": "welvella.com", "preset": "shop"})
    print("APPLY shop:", res2["ok"], res2["steps"][0]["detail"], flush=True)
    assert res2["ok"], res2

    assert "newtest.com" in call("POST", "/api/domains", {"domain": "newtest.com"})["domains"]
    assert "newtest.com" not in call("POST", "/api/domains/delete",
                                      {"domain": "newtest.com"})["domains"]
    print("ADD/DELETE DOMAIN OK", flush=True)

    det = call("POST", "/api/domain-detail", {"domain": "welvella.com"})
    assert det["zone"] and det["email_detail"] is not None and det["https"], det
    print("DETAIL OK: email enabled =", det["email_detail"]["enabled"],
          "| https =", det["https"], flush=True)

    h = call("POST", "/api/set-https", {"domain": "welvella.com", "ssl": "full",
                                        "always_https": "on", "rewrites": "on"})
    assert h["ok"] and h["https"]["ssl"] == "full", h
    print("SET-HTTPS OK", flush=True)

    em = call("POST", "/api/email-enable", {"domain": "welvella.com", "enabled": True})
    assert em["ok"], em
    rl = call("POST", "/api/email-rule", {"domain": "welvella.com", "match": "*",
                                          "destinations": ["owner@gmail.com"]})
    assert rl["ok"], rl
    det2 = call("POST", "/api/domain-detail", {"domain": "welvella.com"})
    assert det2["email_detail"]["enabled"] is True
    assert len(det2["email_detail"]["rules"]) == 1, det2["email_detail"]
    print("EMAIL OK: enabled + 1 catch-all rule", flush=True)

    ns = call("POST", "/api/sync-ns", {"domain": "welvella.com"})
    assert ns["ok"], ns
    print("SYNC-NS OK:", [(s["step"], s["ok"]) for s in ns["steps"]], flush=True)

    hp = call("POST", "/api/https-preset", {"enabled": True, "ssl": "flexible",
                                            "always_https": "on",
                                            "min_tls_version": "1.2",
                                            "rewrites": "on"})
    assert hp["ok"] and hp["https_preset"]["ssl"] == "flexible", hp
    hb = call("POST", "/api/set-https-batch", {"domains": ["welvella.com"]})
    assert hb["ok"] and hb["results"][0]["https"]["min_tls"] == "1.2", hb
    print("HTTPS-PRESET + BATCH OK", flush=True)

    az = call("GET", "/api/account-zones")
    assert "welvella.com" in [z["name"] for z in az["zones"]], az
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
    mock.shutdown()
    print("E2E OK")


if __name__ == "__main__":
    main()
