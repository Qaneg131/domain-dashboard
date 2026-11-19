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
    mock.shutdown()
    print("E2E OK")


if __name__ == "__main__":
    main()
