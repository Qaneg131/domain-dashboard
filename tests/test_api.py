#!/usr/bin/env python3
"""Test dashboard API clients + setup flow voi mock server (khong can key that).

Mock server gia lap:
- Cloudflare API: zones + dns_records (luu state trong RAM)
- Namecheap API: getList/setCustom (XML) — setCustom xong thi DoH cung
  thay theo (gia lap lan truyen ngay lap tuc)
- DoH: tra NS theo state hien tai
"""
import json
import os
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ACCT = "c30bace6d8d169fb77ae278b7d0fc82a"
NS1, NS2 = "brit.ns.cloudflare.com", "langston.ns.cloudflare.com"

STATE = {
    "zones": {},      # name -> {id, name, status, name_servers}
    "records": {},    # zone_id -> [rec]
    "nc_ns": {},      # domain -> [ns...]
    "email": {},      # zone_id -> {enabled, status, rules:[], addresses:[]}
    "settings": {},   # zone_id -> {ssl, always_use_https, automatic_https_rewrites}
    "next_id": 1,
}


def _zone_state(zid):
    STATE["email"].setdefault(zid, {"enabled": False, "status": "unconfigured",
                                    "rules": [], "addresses": []})
    STATE["settings"].setdefault(zid, {"ssl": "flexible", "always_use_https": "off",
                                       "automatic_https_rewrites": "off",
                                       "min_tls_version": "1.0"})
    STATE["records"].setdefault(zid, [])


class MockHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _cf_ok(self, result, result_info=None):
        out = {"success": True, "errors": [], "messages": [], "result": result}
        if result_info:
            out["result_info"] = result_info
        return out

    def _cf_err(self, msg, code=400):
        self._json({"success": False, "errors": [{"message": msg}], "result": None}, code)

    def do_GET(self):
        p = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(p.query)
        if p.path == "/dns-query":
            name = q.get("name", [""])[0]
            ns = STATE["nc_ns"].get(name, [])
            ans = [{"name": name, "type": 2, "TTL": 300, "data": n + "."} for n in ns]
            self._json({"Status": 0, "Answer": ans})
            return
        parts = [x for x in p.path.split("/") if x]
        # /client/v4/zones/<id>/...
        if len(parts) >= 4 and parts[:3] == ["client", "v4", "zones"] and len(parts) > 3:
            zid = parts[3]
            _zone_state(zid)
            if len(parts) == 5 and parts[4] == "dns_records":
                recs = STATE["records"].get(zid, [])
                self._json(self._cf_ok(recs, {"page": 1, "total_pages": 1}))
                return
            if len(parts) >= 6 and parts[4] == "email" and parts[5] == "routing":
                em = STATE["email"][zid]
                if len(parts) == 6:
                    self._json(self._cf_ok({"enabled": em["enabled"], "status": em["status"]}))
                    return
                if len(parts) == 7 and parts[6] == "rules":
                    self._json(self._cf_ok(em["rules"]))
                    return
                if len(parts) == 7 and parts[6] == "addresses":
                    self._json(self._cf_ok(em["addresses"]))
                    return
            if len(parts) == 6 and parts[4] == "settings":
                name = parts[5]
                self._json(self._cf_ok({"id": name, "value": STATE["settings"][zid].get(name),
                                        "editable": True}))
                return
        if p.path.startswith("/client/v4/zones"):
            name = q.get("name", [""])[0]
            if name:
                z = STATE["zones"].get(name)
                self._json(self._cf_ok([z] if z else []))
            else:
                # List tat ca zone (tab 'Tat ca trong tai khoan').
                all_z = list(STATE["zones"].values())
                self._json(self._cf_ok(all_z, {"page": 1, "total_pages": 1}))
            return
        if p.path == "/xml.response":
            cmd = q.get("Command", [""])[0]
            sld, tld = q.get("SLD", [""])[0], q.get("TLD", [""])[0]
            dom = f"{sld}.{tld}"
            if cmd == "namecheap.domains.dns.getList":
                ns = STATE["nc_ns"].get(dom, ["dns1.registrar-servers.com",
                                               "dns2.registrar-servers.com"])
                items = "".join(f"<Nameserver>{n}</Nameserver>" for n in ns)
                xml = (f'<?xml version="1.0"?><ApiResponse Status="OK"><Errors/>'
                       f'<RequestedCommand>{cmd}</RequestedCommand><CommandResponse>'
                       f'<DomainDNSGetListResult Domain="{dom}" IsUsingOurDNS="false">'
                       f"{items}</DomainDNSGetListResult></CommandResponse></ApiResponse>")
            elif cmd == "namecheap.domains.dns.setCustom":
                ns = q.get("Nameservers", [""])[0].split(",")
                STATE["nc_ns"][dom] = [n.strip().lower() for n in ns if n.strip()]
                xml = (f'<?xml version="1.0"?><ApiResponse Status="OK"><Errors/>'
                       f'<RequestedCommand>{cmd}</RequestedCommand><CommandResponse>'
                       f'<DomainDNSSetCustomResult Domain="{dom}" Updated="true" />'
                       f'</CommandResponse></ApiResponse>')
            else:
                xml = ('<?xml version="1.0"?><ApiResponse Status="ERROR"><Errors>'
                       '<Error Number="101">unknown command</Error></Errors></ApiResponse>')
            body = xml.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/xml")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        p = urllib.parse.urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n).decode() or "{}")
        parts = [x for x in p.path.split("/") if x]
        if p.path == "/client/v4/zones":
            name = body["name"]
            zid = f"zone-{STATE['next_id']}"
            STATE["next_id"] += 1
            z = {"id": zid, "name": name, "status": "pending",
                 "name_servers": [NS1, NS2], "account": {"id": ACCT}}
            STATE["zones"][name] = z
            _zone_state(zid)
            self._json(self._cf_ok(z))
            return
        if len(parts) >= 4 and parts[:3] == ["client", "v4", "zones"] and len(parts) > 3:
            zid = parts[3]
            _zone_state(zid)
            if len(parts) == 5 and parts[4] == "dns_records":
                rid = f"rec-{STATE['next_id']}"
                STATE["next_id"] += 1
                rec = {"id": rid, "type": body["type"], "name": body["name"],
                       "content": body["content"], "proxied": body.get("proxied", True),
                       "ttl": body.get("ttl", 1)}
                STATE["records"].setdefault(zid, []).append(rec)
                self._json(self._cf_ok(rec))
                return
            if len(parts) >= 6 and parts[4] == "email" and parts[5] == "routing":
                em = STATE["email"][zid]
                if len(parts) == 7 and parts[6] == "enable":
                    em["enabled"] = True
                    em["status"] = "ready"
                    # Gia lap: enable se them/khoa MX + SPF + DKIM.
                    for t, nm, ct in [("MX", "@", "route1.mx.cloudflare.net"),
                                      ("MX", "@", "route2.mx.cloudflare.net"),
                                      ("MX", "@", "route3.mx.cloudflare.net"),
                                      ("TXT", "@", "v=spf1 include:_spf.mx.cloudflare.net ~all"),
                                      ("TXT", "cf2024-1._domainkey", "v=DKIM1; p=mock")]:
                        STATE["records"][zid].append(
                            {"id": f"rec-{STATE['next_id']}", "type": t, "name": nm,
                             "content": ct, "proxied": False, "ttl": 1})
                        STATE["next_id"] += 1
                    self._json(self._cf_ok({"enabled": True, "status": "ready"}))
                    return
                if len(parts) == 7 and parts[6] == "disable":
                    em["enabled"] = False
                    em["status"] = "unconfigured"
                    self._json(self._cf_ok({"enabled": False, "status": "unconfigured"}))
                    return
                if len(parts) == 7 and parts[6] == "rules":
                    rid = f"rule-{STATE['next_id']}"
                    STATE["next_id"] += 1
                    rule = {"id": rid, "name": body.get("name", ""),
                            "matchers": body.get("matchers", []),
                            "actions": body.get("actions", []),
                            "enabled": True}
                    em["rules"].append(rule)
                    self._json(self._cf_ok(rule))
                    return
                if len(parts) == 7 and parts[6] == "addresses":
                    addr = {"email": body.get("email"), "verified": False,
                            "created": "2026-11-19", "modified": "2026-11-19"}
                    em["addresses"].append(addr)
                    self._json(self._cf_ok(addr))
                    return
        self.send_response(404)
        self.end_headers()

    def do_PATCH(self):
        p = urllib.parse.urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n).decode() or "{}")
        parts = [x for x in p.path.split("/") if x]
        if len(parts) == 6 and parts[:3] == ["client", "v4", "zones"] and parts[4] == "settings":
            zid, name = parts[3], parts[5]
            _zone_state(zid)
            STATE["settings"][zid][name] = body.get("value")
            self._json(self._cf_ok({"id": name, "value": body.get("value"), "editable": True}))
            return
        self.send_response(404)
        self.end_headers()

    def do_DELETE(self):
        p = urllib.parse.urlparse(self.path)
        parts = [x for x in p.path.split("/") if x]
        if len(parts) >= 4 and parts[:3] == ["client", "v4", "zones"]:
            zid = parts[3]
            if len(parts) == 6 and parts[4] == "dns_records":
                rid = parts[5]
                STATE["records"][zid] = [r for r in STATE["records"].get(zid, []) if r["id"] != rid]
                self._json(self._cf_ok({"id": rid}))
                return
            if len(parts) == 8 and parts[4] == "email" and parts[5] == "routing" and parts[6] == "rules":
                rid = parts[7]
                em = STATE["email"].get(zid, {})
                em["rules"] = [r for r in em.get("rules", []) if r["id"] != rid]
                self._json(self._cf_ok({"id": rid}))
                return
        self.send_response(404)
        self.end_headers()


def start_mock():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), MockHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def main():
    srv, port = start_mock()
    base = f"http://127.0.0.1:{port}"
    os.environ["CF_API_BASE"] = base + "/client/v4"
    os.environ["NC_API_BASE"] = base + "/xml.response"
    os.environ["DOH_BASE"] = base + "/dns-query"

    import app as dash
    from cf_api import CFClient
    from nc_api import NCClient, split_domain
    from dns_check import doh_ns

    cfg = {"cf_token": "test-token", "cf_account_id": ACCT,
           "nc_user": "tuanah252", "nc_api_key": "test-key",
           "nc_client_ip": "1.2.3.4",
           "domains": ["elvellia.com"],
           "presets": {"web": [{"type": "A", "name": "@", "content": "1.11.11.11", "proxied": True},
                               {"type": "A", "name": "www", "content": "1.11.11.11", "proxied": True}]},
           "default_preset": "web"}

    print("TEST 1: CF client (zone + records)...", flush=True)
    cf = CFClient("test-token", ACCT, base=base + "/client/v4")
    assert cf.get_zone("elvellia.com") is None
    z, created = cf.get_or_create_zone("elvellia.com")
    assert created and z["name_servers"] == [NS1, NS2], z
    z2, created2 = cf.get_or_create_zone("elvellia.com")
    assert not created2 and z2["id"] == z["id"]
    assert cf.list_records(z["id"]) == []
    cf.create_record(z["id"], "A", "elvellia.com", "1.11.11.11", True)
    assert len(cf.list_records(z["id"])) == 1
    assert cf.delete_all_records(z["id"]) == 1
    assert cf.list_records(z["id"]) == []
    print("  OK", flush=True)

    print("TEST 2: NC client (get/set NS)...", flush=True)
    assert split_domain("elvellia.com") == ("elvellia", "com")
    nc = NCClient("tuanah252", "test-key", "1.2.3.4", base=base + "/xml.response")
    assert nc.get_nameservers("elvellia.com") == ["dns1.registrar-servers.com",
                                                   "dns2.registrar-servers.com"]
    assert nc.set_nameservers("elvellia.com", [NS1, NS2])
    assert nc.get_nameservers("elvellia.com") == [NS1, NS2]
    print("  OK", flush=True)

    print("TEST 3: DoH NS...", flush=True)
    assert doh_ns("elvellia.com", base=base + "/dns-query") == [NS1, NS2]
    print("  OK", flush=True)

    print("TEST 4: setup_domain full flow (zone moi + NS + preset web)...", flush=True)
    STATE["zones"].pop("welvella.com", None)
    STATE["nc_ns"]["welvella.com"] = ["dns1.registrar-servers.com",
                                      "dns2.registrar-servers.com"]
    res = dash.setup_domain("welvella.com", "web", dict(cfg))
    print("  steps:", [(s["step"], s["ok"]) for s in res["steps"]], flush=True)
    assert res["ok"], res
    st = dash.check_domain("welvella.com", cfg)
    assert st["ns_ok"], st
    assert st["records_ok"], st
    assert len(st["records"]) == 2, st["records"]
    print("  OK: ns_ok + records_ok (2 record web)", flush=True)

    print("TEST 5: setup lai domain da set (bo qua NS, ap lai preset)...", flush=True)
    res2 = dash.setup_domain("welvella.com", "web", dict(cfg))
    assert res2["ok"], res2
    assert any("da tro dung" in s["detail"] for s in res2["steps"]), res2["steps"]
    print("  OK", flush=True)

    print("TEST 6: apply_preset khi chua co zone -> loi ro rang...", flush=True)
    res3 = dash.apply_preset("chuaco.com", "web", dict(cfg))
    assert not res3["ok"] and "Setup truoc" in res3["steps"][0]["detail"], res3
    print("  OK", flush=True)

    print("TEST 7: thieu CF token -> bao loi ro, khong crash...", flush=True)
    cfg2 = dict(cfg, cf_token="")
    res4 = dash.setup_domain("elvellia.com", "web", cfg2)
    assert not res4["ok"], res4
    st2 = dash.check_domain("elvellia.com", cfg2)
    assert st2["error"] and "token" in st2["error"], st2
    print("  OK", flush=True)

    print("TEST 8: Email Routing (enable -> rule -> delete -> disable)...", flush=True)
    z = cf.get_zone("welvella.com")
    er = cf.get_email_routing(z["id"])
    assert er["enabled"] is False, er
    cf.enable_email_routing(z["id"])
    er2 = cf.get_email_routing(z["id"])
    assert er2["enabled"] is True and er2["status"] == "ready", er2
    # Enable da them MX/SPF/DKIM vao records.
    assert any(r["type"] == "MX" for r in cf.list_records(z["id"]))
    rule = cf.create_email_rule(z["id"], "*", ["owner@gmail.com"])
    assert rule["matchers"] == [{"type": "all"}], rule
    assert len(cf.list_email_rules(z["id"])) == 1
    cf.delete_email_rule(z["id"], rule["id"])
    assert cf.list_email_rules(z["id"]) == []
    addr = cf.create_email_address(z["id"], "owner@gmail.com")
    assert addr["email"] == "owner@gmail.com" and addr["verified"] is False
    cf.disable_email_routing(z["id"])
    assert cf.get_email_routing(z["id"])["enabled"] is False
    print("  OK", flush=True)

    print("TEST 9: HTTPS settings (get/set ssl + always_use_https + min_tls)...", flush=True)
    https = cf.get_https(z["id"])
    assert https["ssl"] == "flexible" and https["ok"] is False, https
    assert https["min_tls"] == "1.0", https
    cf.set_https(z["id"], ssl="full", always_https="on", rewrites="on", min_tls="1.2")
    https2 = cf.get_https(z["id"])
    assert https2 == {"ssl": "full", "always_https": "on", "rewrites": "on",
                      "min_tls": "1.2", "ok": True}, https2
    print("  OK", flush=True)

    print("TEST 10: check_domain co them email + https...", flush=True)
    st3 = dash.check_domain("welvella.com", cfg)
    assert st3["email"] is not None and st3["https"] is not None, st3
    assert st3["https"]["ok"] is True, st3["https"]
    print("  OK:", st3["email"], st3["https"], flush=True)

    print("TEST 11: delete_all_records GIU LAI record Email Routing...", flush=True)
    z11, _ = cf.get_or_create_zone("test11.com")
    cf.create_record(z11["id"], "A", "test11.com", "1.11.11.11", True)
    cf.create_record(z11["id"], "A", "www.test11.com", "1.11.11.11", True)
    cf.enable_email_routing(z11["id"])  # them 5 record MX/SPF/DKIM
    assert len(cf.list_records(z11["id"])) == 7
    deleted = cf.delete_all_records(z11["id"])
    after = cf.list_records(z11["id"])
    assert deleted == 2, deleted
    assert len(after) == 5, after
    assert all(r["type"] == "MX" or "spf" in r["content"] or "DKIM" in r["content"]
               for r in after), after
    print(f"  OK: xoa {deleted} record web, giu lai {len(after)} record email", flush=True)

    print("TEST 12: setup co email preset (2 rule info@ + catch-all)...", flush=True)
    cfg3 = dict(cfg)
    cfg3["email_preset"] = {"enabled": True, "rules": [
        {"match": "info@", "to": "owner@gmail.com"},
        {"match": "*", "to": "owner@gmail.com"}]}
    # Reset welvella: tat email + xoa rule truoc.
    cf.disable_email_routing(z["id"])
    res5 = dash.setup_domain("welvella.com", "web", cfg3)
    print("  steps:", [(s["step"], s["ok"]) for s in res5["steps"]], flush=True)
    assert res5["ok"], res5
    assert any("info@welvella.com" in s["step"] for s in res5["steps"]), res5["steps"]
    assert any(s["step"].startswith("Rule *") for s in res5["steps"]), res5["steps"]
    st4 = dash.check_domain("welvella.com", cfg3)
    assert st4["email"]["enabled"] is True and st4["email"]["rules"] == 2, st4["email"]
    print("  OK: email enabled + 2 rule", flush=True)

    print("TEST 13: setup lai — rule da co thi giu, khong tao trung...", flush=True)
    res6 = dash.setup_domain("welvella.com", "web", cfg3)
    assert res6["ok"], res6
    assert sum(1 for s in res6["steps"] if s["detail"] == "da co san") == 2, res6["steps"]
    assert len(cf.list_email_rules(z["id"])) == 2
    print("  OK", flush=True)

    print("TEST 14: email preset chua nhap dia chi nhan -> bo qua, van ok...", flush=True)
    cfg4 = dict(cfg)
    cfg4["email_preset"] = {"enabled": True, "rules": [{"match": "info@", "to": ""}]}
    res7 = dash.setup_domain("welvella.com", "web", cfg4)
    assert res7["ok"], res7
    assert any("chua nhap dia chi nhan" in s["detail"] for s in res7["steps"]), res7["steps"]
    print("  OK", flush=True)

    print("TEST 15: list_zones tra het zone trong tai khoan...", flush=True)
    zones = cf.list_zones()
    assert {x["name"] for x in zones} >= {"elvellia.com", "welvella.com"}, zones
    print("  OK:", [x["name"] for x in zones], flush=True)

    print("TEST 16: Setup ap luon HTTPS preset (Flexible + always on + TLS 1.2)...", flush=True)
    cfg5 = dict(cfg)
    cfg5["https_preset"] = {"enabled": True, "ssl": "flexible",
                            "always_https": "on", "min_tls_version": "1.2",
                            "rewrites": "on"}
    cfg5["email_preset"] = {"enabled": False, "rules": []}
    res8 = dash.setup_domain("welvella.com", "web", cfg5)
    assert res8["ok"], res8
    assert any(s["step"] == "HTTPS" and s["ok"] for s in res8["steps"]), res8["steps"]
    st5 = dash.check_domain("welvella.com", cfg5)
    assert st5["https"]["ssl"] == "flexible", st5["https"]
    assert st5["https"]["always_https"] == "on", st5["https"]
    assert st5["https"]["min_tls"] == "1.2", st5["https"]
    assert st5["https"]["ok"] is True, st5["https"]
    print("  OK:", st5["https"], flush=True)

    srv.shutdown()
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
