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
    "nc_calls": 0,    # so lan goi Namecheap API (getList + setCustom)
    "nc_rate_limit": {},  # domain -> so lan con lai tra "Too many requests"
    "email": {},      # zone_id -> {enabled, status, rules:[], addresses:[]}
    "settings": {},   # zone_id -> {ssl, always_use_https, automatic_https_rewrites}
    "rulesets": {},   # zone_id -> {id, phase, rules:[]} hoac chua co
    "next_id": 1,
}


def _zone_state(zid):
    STATE["email"].setdefault(zid, {"enabled": False, "status": "unconfigured",
                                    "rules": [], "addresses": [],
                                    "catch_all": None})
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
                    # API that tra ca catch-all trong list (matchers [{type: all}]).
                    rules = list(em["rules"])
                    if em.get("catch_all"):
                        rules.append(em["catch_all"])
                    self._json(self._cf_ok(rules))
                    return
                if len(parts) == 7 and parts[6] == "addresses":
                    self._json(self._cf_ok(em["addresses"]))
                    return
                if len(parts) == 8 and parts[6] == "rules" and parts[7] == "catch_all":
                    ca = em.get("catch_all")
                    if ca is None:
                        self._cf_err("catch-all not found", 404)
                    else:
                        self._json(self._cf_ok(ca))
                    return
            if len(parts) == 5 and parts[4] == "settings":
                # Bulk: GET /zones/<id>/settings tra het setting 1 lan.
                stt = STATE["settings"][zid]
                self._json(self._cf_ok([{"id": k, "value": v, "editable": True}
                                        for k, v in stt.items()]))
                return
            if len(parts) == 6 and parts[4] == "settings":
                name = parts[5]
                self._json(self._cf_ok({"id": name, "value": STATE["settings"][zid].get(name),
                                        "editable": True}))
                return
            if len(parts) == 8 and parts[4] == "rulesets" and parts[5] == "phases" \
                    and parts[6] == "http_request_cache_settings" and parts[7] == "entrypoint":
                rs = STATE["rulesets"].get(zid)
                if rs is None:
                    self._cf_err("The ruleset does not exist.", 404)
                else:
                    self._json(self._cf_ok(rs))
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
            # XML that co xmlns mac dinh nhu Namecheap that (de test boc
            # namespace); domain badip.com gia lap loi 1011150.
            NS = ' xmlns="http://api.namecheap.com/xml.response"'
            if dom == "badip.com":
                xml = (f'<?xml version="1.0" encoding="utf-8"?>'
                       f'<ApiResponse Status="ERROR"{NS}><Errors>'
                       f'<Error Number="1011150">Invalid request IP: 9.9.9.9</Error>'
                       f'</Errors><Warnings /><RequestedCommand />'
                       f'<Server>Server 1</Server></ApiResponse>')
            elif STATE.get("nc_rate_limit", {}).get(dom, 0) > 0:
                # Gia lap Namecheap gioi han toc do: tra "Too many requests"
                # vai lan roi moi cho qua (de test tu thu lai).
                STATE["nc_rate_limit"][dom] -= 1
                STATE["nc_calls"] += 1
                xml = (f'<?xml version="1.0" encoding="utf-8"?>'
                       f'<ApiResponse Status="ERROR"{NS}><Errors>'
                       f'<Error Number="...">Too many requests</Error>'
                       f'</Errors><Warnings /><RequestedCommand />'
                       f'<Server>Server 1</Server></ApiResponse>')
            elif cmd == "namecheap.domains.dns.getList":
                STATE["nc_calls"] += 1
                ns = STATE["nc_ns"].get(dom, ["dns1.registrar-servers.com",
                                               "dns2.registrar-servers.com"])
                items = "".join(f"<Nameserver>{n}</Nameserver>" for n in ns)
                xml = (f'<?xml version="1.0"?><ApiResponse Status="OK"{NS}><Errors/>'
                       f'<RequestedCommand>{cmd}</RequestedCommand><CommandResponse>'
                       f'<DomainDNSGetListResult Domain="{dom}" IsUsingOurDNS="false">'
                       f"{items}</DomainDNSGetListResult></CommandResponse></ApiResponse>")
            elif cmd == "namecheap.domains.dns.setCustom":
                STATE["nc_calls"] += 1
                ns = q.get("Nameservers", [""])[0].split(",")
                STATE["nc_ns"][dom] = [n.strip().lower() for n in ns if n.strip()]
                xml = (f'<?xml version="1.0"?><ApiResponse Status="OK"{NS}><Errors/>'
                       f'<RequestedCommand>{cmd}</RequestedCommand><CommandResponse>'
                       f'<DomainDNSSetCustomResult Domain="{dom}" Updated="true" />'
                       f'</CommandResponse></ApiResponse>')
            else:
                xml = (f'<?xml version="1.0"?><ApiResponse Status="ERROR"{NS}><Errors>'
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
            z = {"id": zid, "name": name, "status": "active",
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
                    # API that: zone chua active thi khong bat duoc email.
                    z = next((zz for zz in STATE["zones"].values()
                              if zz.get("id") == zid), {})
                    if z.get("status") != "active":
                        self._cf_err("Active zone required", 400)
                        return
                    # API that: con MX khong phai cua Cloudflare thi loi 2008.
                    bad_mx = [r for r in STATE["records"].get(zid, [])
                              if r.get("type") == "MX"
                              and not str(r.get("content", "")).endswith(".mx.cloudflare.net")]
                    if bad_mx:
                        self._cf_err("Non-Cloudflare MX records exist", 400)
                        return
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
                             "content": ct, "proxied": False, "ttl": 1,
                             "locked": True})  # API that khoa record email
                        STATE["next_id"] += 1
                    self._json(self._cf_ok({"enabled": True, "status": "ready"}))
                    return
                if len(parts) == 7 and parts[6] == "disable":
                    em["enabled"] = False
                    em["status"] = "unconfigured"
                    self._json(self._cf_ok({"enabled": False, "status": "unconfigured"}))
                    return
                if len(parts) == 7 and parts[6] == "rules":
                    # API that: POST all-matcher vao /rules bi tu choi.
                    mts = body.get("matchers") or []
                    if mts and mts[0].get("type") == "all":
                        self._cf_err("Invalid rule operation", 400)
                        return
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
            if len(parts) == 5 and parts[4] == "rulesets":
                rs = {"id": f"rs-{STATE['next_id']}", "name": body.get("name", "default"),
                      "kind": body.get("kind", "zone"), "phase": body.get("phase"),
                      "rules": body.get("rules", [])}
                STATE["next_id"] += 1
                STATE["rulesets"][zid] = rs
                self._json(self._cf_ok(rs))
                return
        self.send_response(404)
        self.end_headers()

    def do_PUT(self):
        p = urllib.parse.urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n).decode() or "{}")
        parts = [x for x in p.path.split("/") if x]
        if len(parts) == 8 and parts[:3] == ["client", "v4", "zones"] and parts[4] == "rulesets":
            zid = parts[3]
            rs = STATE["rulesets"].get(zid)
            if rs is None:
                self._cf_err("The ruleset does not exist.", 404)
                return
            rs["rules"] = body.get("rules", rs["rules"])
            self._json(self._cf_ok(rs))
            return
        if len(parts) == 8 and parts[:3] == ["client", "v4", "zones"] \
                and parts[4] == "email" and parts[5] == "routing" \
                and parts[6] == "rules" and parts[7] == "catch_all":
            zid = parts[3]
            _zone_state(zid)
            em = STATE["email"][zid]
            ca = {"id": "catch_all", "name": body.get("name", "Catch-all"),
                  "matchers": body.get("matchers", [{"type": "all"}]),
                  "actions": body.get("actions", []),
                  "enabled": body.get("enabled", True)}
            em["catch_all"] = ca
            self._json(self._cf_ok(ca))
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
            if len(parts) == 4:
                # DELETE /zones/{id} — xoa zone + toan bo state lien quan.
                name = next((k for k, v in STATE["zones"].items() if v["id"] == zid), None)
                if name:
                    del STATE["zones"][name]
                for store in ("email", "settings", "records", "rulesets"):
                    STATE[store].pop(zid, None)
                self._json(self._cf_ok({"id": zid}))
                return
            if len(parts) == 6 and parts[4] == "dns_records":
                rid = parts[5]
                STATE["records"][zid] = [r for r in STATE["records"].get(zid, []) if r["id"] != rid]
                self._json(self._cf_ok({"id": rid}))
                return
            if len(parts) == 8 and parts[4] == "email" and parts[5] == "routing" and parts[6] == "rules":
                rid = parts[7]
                if rid == "catch_all":
                    # API that: khong xoa catch-all qua /rules/{id}.
                    self._cf_err("Invalid rule operation", 400)
                    return
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
    import nc_api
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
    # Rule thuong: tao + xoa duoc qua /rules.
    lit = cf.create_email_rule(z["id"], "info@welvella.com", ["owner@gmail.com"])
    assert len(cf.list_email_rules(z["id"])) == 1
    cf.delete_email_rule(z["id"], lit["id"])
    assert cf.list_email_rules(z["id"]) == []
    # Catch-all: endpoint rieng, khong xoa duoc qua /rules/{id} (API that).
    rule = cf.set_catch_all(z["id"], ["owner@gmail.com"])
    assert rule["matchers"] == [{"type": "all"}], rule
    assert len(cf.list_email_rules(z["id"])) == 1
    try:
        cf.delete_email_rule(z["id"], rule["id"])
        raise AssertionError("le ra phai loi Invalid rule operation")
    except Exception as e:
        assert "Invalid rule operation" in str(e), e
    STATE["email"][z["id"]]["catch_all"] = None  # don dep cho test sau
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

    print("TEST 17: Cache Rules — By Pass (tao / trung / go)...", flush=True)
    z2 = cf.get_zone("welvella.com")
    assert cf.has_cache_bypass(z2["id"]) is False
    r1 = cf.set_cache_bypass(z2["id"])
    assert r1["already"] is False
    assert r1["rule"]["action"] == "set_cache_settings"
    assert r1["rule"]["action_parameters"] == {"cache": False}
    assert r1["rule"]["expression"] == "true"
    assert cf.has_cache_bypass(z2["id"]) is True
    # Tao lan 2 phai tra already, khong tao trung rule.
    r2 = cf.set_cache_bypass(z2["id"])
    assert r2["already"] is True
    rs = cf.get_cache_ruleset(z2["id"])
    assert len(rs["rules"]) == 1, rs["rules"]
    # Them 1 rule khac thu cong roi set bypass lai: rule cu phai con.
    rs["rules"].append({"action": "set_cache_settings",
                        "action_parameters": {"cache": True},
                        "expression": "(http.request.uri.path starts_with \"/static/\")",
                        "description": "Cache static", "enabled": True})
    cf._req("PUT", f"/zones/{z2['id']}/rulesets/phases/http_request_cache_settings/entrypoint",
            {"rules": rs["rules"]})
    cf.remove_cache_bypass(z2["id"])
    assert cf.has_cache_bypass(z2["id"]) is False
    rs2 = cf.get_cache_ruleset(z2["id"])
    assert len(rs2["rules"]) == 1 and rs2["rules"][0]["description"] == "Cache static", rs2
    print("  OK", flush=True)

    print("TEST 19: catch-all qua endpoint rieng; POST all-matcher vao /rules bi tu choi...", flush=True)
    z4, _c = cf.get_or_create_zone("catchtest.com")
    assert cf.get_catch_all(z4["id"]) is None
    # Duong cu (POST {type: all} vao /rules) — API that tra 'Invalid rule operation'.
    try:
        cf._req("POST", f"/zones/{z4['id']}/email/routing/rules",
                {"name": "x", "matchers": [{"type": "all"}],
                 "actions": [{"type": "forward", "value": ["owner@gmail.com"]}],
                 "enabled": True})
        raise AssertionError("le ra phai loi Invalid rule operation")
    except Exception as e:
        assert "Invalid rule operation" in str(e), e
    ca = cf.set_catch_all(z4["id"], ["owner@gmail.com"])
    assert ca["matchers"] == [{"type": "all"}], ca
    assert ca["actions"] == [{"type": "forward", "value": ["owner@gmail.com"]}], ca
    assert cf.get_catch_all(z4["id"])["actions"][0]["value"] == ["owner@gmail.com"]
    # create_email_rule('*') phai tu chuyen sang catch-all endpoint.
    cf.create_email_rule(z4["id"], "*", ["other@gmail.com"])
    assert cf.get_catch_all(z4["id"])["actions"][0]["value"] == ["other@gmail.com"]
    print("  OK", flush=True)

    print("TEST 20: setup domain co MX cu — records step xoa MX, email bat duoc, catch-all dung...", flush=True)
    z5, _c = cf.get_or_create_zone("mxold.com")
    cf.create_record(z5["id"], "MX", "mxold.com", "aspmx.l.google.com", False)
    cf.create_record(z5["id"], "A", "mxold.com", "9.9.9.9", True)
    cfg6 = dict(cfg)
    cfg6["email_preset"] = {"enabled": True, "rules": [
        {"match": "support@", "to": "owner@gmail.com"},
        {"match": "*", "to": "owner@gmail.com"}]}
    cfg6["https_preset"] = {"enabled": True, "ssl": "flexible",
                            "always_https": "on", "min_tls_version": "1.2",
                            "rewrites": "on"}
    # NS da tro dung tren DoH gia lap de qua buoc NS.
    STATE["nc_ns"]["mxold.com"] = [NS1, NS2]
    res9 = dash.setup_domain("mxold.com", "web", cfg6)
    assert res9["ok"], res9
    recs5 = cf.list_records(z5["id"])
    assert not any(r["type"] == "MX" and "google" in r["content"] for r in recs5), recs5
    assert any(r["type"] == "MX" and r["content"].startswith("route") for r in recs5), recs5
    assert sum(1 for r in recs5 if r["type"] == "A" and r["content"] == "1.11.11.11") == 2, recs5
    assert not any(r["type"] == "A" and r["content"] == "9.9.9.9" for r in recs5), recs5
    assert cf.get_catch_all(z5["id"])["actions"][0]["value"] == ["owner@gmail.com"]
    assert len(cf.list_email_rules(z5["id"])) == 2
    print("  OK", flush=True)

    print("TEST 21: setup khong co preset — bao loi, KHONG xoa record cu...", flush=True)
    z6, _c = cf.get_or_create_zone("nopreset.com")
    cf.create_record(z6["id"], "A", "nopreset.com", "8.8.8.8", True)
    STATE["nc_ns"]["nopreset.com"] = [NS1, NS2]
    cfg7 = dict(cfg6)
    cfg7["email_preset"] = {"enabled": False, "rules": []}
    cfg7["default_preset"] = ""  # gia lap user chua chon preset mac dinh
    res10 = dash.setup_domain("nopreset.com", "", cfg7)
    assert res10["ok"] is False, res10
    assert any(s["step"] == "Ap preset" and s["ok"] is False for s in res10["steps"]), res10["steps"]
    assert len(cf.list_records(z6["id"])) == 1, cf.list_records(z6["id"])
    print("  OK", flush=True)
    print("  OK", flush=True)

    print("TEST 22: _apply_email_preset tu xoa MX cu khi bat email (khong qua buoc records)...", flush=True)
    z7, _c = cf.get_or_create_zone("mxretry.com")
    cf.create_record(z7["id"], "MX", "mxretry.com", "mx1.mail.host.com", False)
    cfg8 = dict(cfg6)
    steps22, ok22 = dash._apply_email_preset("mxretry.com", cfg8, cf, {"id": z7["id"]})
    assert ok22, steps22
    assert any(s["step"] == "Xoa MX cu" for s in steps22), steps22
    assert cf.get_email_routing(z7["id"])["enabled"] is True
    assert not any(r["type"] == "MX" and "host.com" in r["content"]
                   for r in cf.list_records(z7["id"]))
    print("  OK", flush=True)

    print("TEST 23: Namecheap XML co xmlns + loi 1011150 hien ro IP...", flush=True)
    try:
        nc.set_nameservers("badip.com", [NS1, NS2])
        raise AssertionError("le ra phai loi 1011150")
    except Exception as e:
        assert "9.9.9.9" in str(e) and "whitelist" in str(e), e
        print("  OK:", str(e)[:100], flush=True)

    print("TEST 24: zone chua co ruleset cache — 404 that ('The ruleset does not exist.') phai tra None, khong phai loi quyen...", flush=True)
    z8 = cf.get_zone("elvellia.com")
    assert cf.get_cache_ruleset(z8["id"]) is None
    assert cf.has_cache_bypass(z8["id"]) is False
    st24 = dash.check_domain("elvellia.com", cfg6)
    assert st24["cache_bypass"] is False, st24
    assert not st24.get("cache_error"), st24
    print("  OK", flush=True)

    print("TEST 25: zone pending — _apply_email_preset doi roi bao ro, active lai thi ok...", flush=True)
    z9, _c = cf.get_or_create_zone("pendingzone.com")
    STATE["zones"]["pendingzone.com"]["status"] = "pending"
    dash.EMAIL_WAIT_ACTIVE_S = 2
    dash.EMAIL_POLL_S = 1
    steps25, ok25 = dash._apply_email_preset(
        "pendingzone.com", cfg6, cf, {"id": z9["id"], "status": "pending"})
    assert ok25 is False, steps25
    assert any((not s["ok"]) and "chua active" in s["detail"]
               for s in steps25), steps25
    STATE["zones"]["pendingzone.com"]["status"] = "active"
    steps25b, ok25b = dash._apply_email_preset(
        "pendingzone.com", cfg6, cf, {"id": z9["id"], "status": "active"})
    assert ok25b, steps25b
    assert any(s["step"].startswith("Rule support@pendingzone.com")
               for s in steps25b), steps25b
    assert any(s["step"].startswith("Rule * (catch-all)")
               for s in steps25b), steps25b
    dash.EMAIL_WAIT_ACTIVE_S = 300
    dash.EMAIL_POLL_S = 30
    print("  OK", flush=True)

    print("TEST 26: GSC — them TXT @, trung thi giu, khac thi thay, Setup giu lai...", flush=True)
    z10, _c = cf.get_or_create_zone("gsctest.com")
    ok26, dt26 = dash.set_gsc_record("gsctest.com", "abc123", cfg6)
    assert ok26 and dt26 == "da them", (ok26, dt26)
    g = [r for r in cf.list_records(z10["id"])
         if r["type"] == "TXT" and "google-site-verification" in r["content"]]
    assert len(g) == 1 and g[0]["content"] == "google-site-verification=abc123", g
    assert g[0]["proxied"] is False, g
    assert dash._gsc_from_records(cf.list_records(z10["id"])) == "google-site-verification=abc123"
    ok26b, dt26b = dash.set_gsc_record(
        "gsctest.com", "google-site-verification=abc123", cfg6)
    assert ok26b and dt26b == "da co san", (ok26b, dt26b)
    ok26c, dt26c = dash.set_gsc_record("gsctest.com", "xyz999", cfg6)
    assert ok26c and dt26c == "da cap nhat", (ok26c, dt26c)
    g2 = [r for r in cf.list_records(z10["id"])
          if r["type"] == "TXT" and "google-site-verification" in r["content"]]
    assert len(g2) == 1 and g2[0]["content"] == "google-site-verification=xyz999", g2
    # Setup (ap preset) xoa record cu nhung GIU record GSC.
    res26 = dash.setup_domain("gsctest.com", "web", cfg6)
    assert res26["ok"], res26
    g3 = [r for r in cf.list_records(z10["id"])
          if r["type"] == "TXT" and "google-site-verification" in r["content"]]
    assert len(g3) == 1 and g3[0]["content"] == "google-site-verification=xyz999", g3
    print("  OK", flush=True)

    print("TEST 27: tab Setup email — check_domain tra ve routing + dns_locked...", flush=True)
    z11, _c = cf.get_or_create_zone("emailtab.com")
    st27 = dash.check_domain("emailtab.com", cfg6)
    assert st27["email"]["enabled"] is False, st27["email"]
    assert st27["email"]["dns_locked"] is False, st27["email"]
    dash._apply_email_preset("emailtab.com", cfg6, cf, {"id": z11["id"], "status": "active"})
    st27b = dash.check_domain("emailtab.com", cfg6)
    assert st27b["email"]["enabled"] is True, st27b["email"]
    assert st27b["email"]["dns_locked"] is True, st27b["email"]
    assert st27b["email"]["rules"] == 2, st27b["email"]
    print("  OK", flush=True)

    print("TEST 28: ap preset kieu gop — chi xoa record trung, giu lai record khac...", flush=True)
    z12, _c = cf.get_or_create_zone("merge.com")
    for t, nm, ct in [("A", "@", "1.2.3.4"), ("A", "www", "1.2.3.4"),
                      ("TXT", "@", "custom-verification=abc"),
                      ("CNAME", "blog", "example.com")]:
        cf.create_record(z12["id"], t, nm, ct, False)
    cf.enable_email_routing(z12["id"])  # +3 MX + SPF + DKIM = 9 record
    assert len(cf.list_records(z12["id"])) == 9, len(cf.list_records(z12["id"]))
    res28 = dash.apply_preset("merge.com", "web", cfg6)
    assert res28["ok"], res28
    recs28 = cf.list_records(z12["id"])
    assert len(recs28) == 9, [(r["type"], r["name"], r["content"]) for r in recs28]
    assert any(r["type"] == "A" and r["content"] == "1.11.11.11" for r in recs28)
    assert not any(r["type"] == "A" and r["content"] == "1.2.3.4" for r in recs28)
    assert any(r["type"] == "TXT" and r["content"] == "custom-verification=abc" for r in recs28)
    assert any(r["type"] == "CNAME" and r["content"] == "example.com" for r in recs28)
    # Ap lai lan 2: khong doi gi, khong xoa gi.
    res28b = dash.apply_preset("merge.com", "web", cfg6)
    assert res28b["ok"] and "da xoa 0 record trung" in res28b["steps"][0]["detail"], res28b
    assert len(cf.list_records(z12["id"])) == 9
    print("  OK", flush=True)

    print("TEST 29: get_https doc 1 lan qua /settings bulk...", flush=True)
    cf.set_https(z12["id"], ssl="full", always_https="on",
                 rewrites="on", min_tls="1.2")
    h29 = cf.get_https(z12["id"])
    assert h29 == {"ssl": "full", "always_https": "on", "rewrites": "on",
                   "min_tls": "1.2", "ok": True}, h29
    print("  OK", flush=True)

    print("TEST 30: check_domain bo qua Namecheap khi DoH da xac nhan NS...", flush=True)
    z13, _c = cf.get_or_create_zone("ncskip.com")
    STATE["nc_ns"]["ncskip.com"] = [NS1, NS2]  # DoH tra dung NS zone
    STATE["nc_calls"] = 0
    st30 = dash.check_domain("ncskip.com", cfg6)
    assert st30["ns_ok"], st30
    assert st30["registrar_ns"] == [NS1, NS2], st30
    assert STATE["nc_calls"] == 0, STATE["nc_calls"]
    # DoH chua tro dung -> van goi NC de biet registrar dang ghi gi.
    z14, _c = cf.get_or_create_zone("ncneed.com")
    STATE["nc_calls"] = 0
    st30b = dash.check_domain("ncneed.com", cfg6)
    assert not st30b["ns_ok"], st30b
    assert STATE["nc_calls"] == 1, STATE["nc_calls"]
    assert st30b["registrar_ns"] == ["dns1.registrar-servers.com",
                                     "dns2.registrar-servers.com"], st30b
    print("  OK", flush=True)

    print("TEST 31: _setup_email_one — da set thi already_set, pending thi bo qua ngay...", flush=True)
    z15, _c = cf.get_or_create_zone("emailbatch.com")
    r31 = dash._setup_email_one("emailbatch.com", cfg6)
    assert r31["ok"] and not r31.get("already_set"), r31
    r31b = dash._setup_email_one("emailbatch.com", cfg6)
    assert r31b["ok"] and r31b.get("already_set"), r31b
    # Xoa 1 rule thuong -> lan sau phai tao lai, khong phai already_set.
    rules31 = cf.list_email_rules(z15["id"])
    non_ca = [r for r in rules31
              if not (r.get("matchers") and r["matchers"][0].get("type") == "all")]
    assert non_ca, rules31
    cf.delete_email_rule(z15["id"], non_ca[0]["id"])
    r31c = dash._setup_email_one("emailbatch.com", cfg6)
    assert r31c["ok"] and not r31c.get("already_set"), r31c
    # Zone pending -> bo qua ngay, khong doi 5 phut.
    z16, _c = cf.get_or_create_zone("emailpend.com")
    STATE["zones"]["emailpend.com"]["status"] = "pending"
    r31d = dash._setup_email_one("emailpend.com", cfg6)
    assert not r31d["ok"] and "chua active" in r31d["steps"][0]["detail"], r31d
    # Khong co zone.
    r31e = dash._setup_email_one("nozone-x.com", cfg6)
    assert not r31e["ok"], r31e
    print("  OK", flush=True)

    print("TEST 32: _save_check_result — check moi loi khong xoa trang thai tot cu...", flush=True)
    good = {"domain": "good.com", "zone": {"id": "z1", "status": "active",
            "name_servers": [NS1, NS2]}, "error": None}
    err = {"domain": "good.com", "zone": None,
           "error": "Please wait and consider throttling your request speed"}
    st32 = {"good.com": dict(good)}
    assert dash._save_check_result(st32, "good.com", err) is False, st32
    assert st32["good.com"]["zone"] is not None and not st32["good.com"]["error"], st32
    # Domain moi chua co trang thai: luu loi de con thay ma kiem tra lai.
    assert dash._save_check_result(st32, "new.com", dict(err, domain="new.com")) is True
    assert st32["new.com"]["error"], st32
    # Trang thai cu cung loi: check moi tot se ghi de.
    assert dash._save_check_result(st32, "new.com", dict(good, domain="new.com")) is True
    assert st32["new.com"]["zone"] is not None, st32
    # _check_many: check loi (khong co token) giu trang thai tot, dem kept.
    cfg_notoken = dict(cfg6)
    cfg_notoken["cf_token"] = ""
    st32b = {"good.com": dict(good)}
    checked, limited, kept = dash._check_many(["good.com", "new.com"], cfg_notoken, st32b)
    assert checked == 2 and not limited and kept == 1, (checked, limited, kept)
    assert st32b["good.com"]["zone"] is not None, st32b
    assert st32b["new.com"]["error"], st32b
    print("  OK", flush=True)

    print("TEST 33: check_domain(skip_nc=True) — loat check khong goi Namecheap...", flush=True)
    z17, _c = cf.get_or_create_zone("skipncbatch.com")
    STATE["nc_ns"]["skipncbatch.com"] = ["dns1.registrar-servers.com",
                                         "dns2.registrar-servers.com"]  # DoH chua tro dung NS zone
    STATE["nc_calls"] = 0
    st33 = dash.check_domain("skipncbatch.com", cfg6)
    assert not st33["ns_ok"] and STATE["nc_calls"] == 1, (st33, STATE["nc_calls"])
    STATE["nc_calls"] = 0
    st33b = dash.check_domain("skipncbatch.com", cfg6, skip_nc=True)
    assert not st33b["ns_ok"] and STATE["nc_calls"] == 0, (st33b, STATE["nc_calls"])
    assert st33b["registrar_ns"] == [], st33b
    # _check_many (loat) cung bo qua NC.
    STATE["nc_calls"] = 0
    st33c = {}
    checked, limited, kept = dash._check_many(["skipncbatch.com"], cfg6, st33c)
    assert checked == 1 and not limited and STATE["nc_calls"] == 0, \
        (checked, limited, STATE["nc_calls"])
    print("  OK", flush=True)

    print("TEST 34: NCClient tu thu lai khi dinh 'Too many requests'...", flush=True)
    nc_api.NC_RETRY_WAITS = (0, 0)  # test khoi doi that
    nc = dash._nc_client(cfg6)
    STATE["nc_rate_limit"] = {"ratelimit-once.com": 1, "ratelimit-always.com": 99}
    STATE["nc_calls"] = 0
    ns34 = nc.get_nameservers("ratelimit-once.com")
    assert ns34 == ["dns1.registrar-servers.com", "dns2.registrar-servers.com"], ns34
    assert STATE["nc_calls"] == 2, STATE["nc_calls"]  # loi 1 lan roi thu lai thanh cong
    # Loi mai: het luot thu thi bao loi ro (co huong dan doi 2 phut).
    try:
        nc.get_nameservers("ratelimit-always.com")
        raise AssertionError("phai bao loi")
    except Exception as e:
        assert "giới hạn tốc độ" in str(e) and "2 phút" in str(e), e
    assert STATE["nc_calls"] == 2 + 3, STATE["nc_calls"]  # 1 + 2 lan thu lai
    nc_api.NC_RETRY_WAITS = (30, 60)
    print("  OK", flush=True)

    print("TEST 35: phat hien gioi han toc do ('Too many requests')...", flush=True)
    assert dash._is_rate_limited({"error": "Too many requests"}), "is_rate_limited"
    assert dash._is_rate_limited({"registrar_ns_error": "Too many requests"})
    assert not dash._is_rate_limited({"error": None})
    assert dash._setup_hit_rate_limit({"steps": [
        {"step": "Đổi NS ở Namecheap", "ok": False,
         "detail": "Namecheap giới hạn tốc độ (Too many requests) — đợi ~2 phút rồi thử lại"}]})
    assert not dash._setup_hit_rate_limit({"steps": [
        {"step": "Đổi NS ở Namecheap", "ok": True, "detail": "registrar da ghi: a, b"}]})
    print("  OK", flush=True)

    print("TEST 36: _check_dns_light — chi check DNS, khong goi Namecheap...", flush=True)
    z18, _c = cf.get_or_create_zone("lightdns.com")
    STATE["nc_ns"]["lightdns.com"] = [NS1, NS2]  # DoH tra dung NS zone
    STATE["nc_ns"]["lightdns2.com"] = ["dns1.registrar-servers.com",
                                       "dns2.registrar-servers.com"]
    z19, _c = cf.get_or_create_zone("lightdns2.com")
    STATE["nc_calls"] = 0
    st36 = {}
    out36 = dash._check_dns_light(["lightdns.com", "lightdns2.com", "nozone-light.com"], cfg6, st36)
    assert STATE["nc_calls"] == 0, STATE["nc_calls"]  # khong goi NC
    assert out36["lightdns.com"]["light"] and out36["lightdns.com"]["ns_ok"], out36
    assert out36["lightdns.com"]["zone"]["status"] == "active", out36
    assert out36["lightdns2.com"]["light"] and not out36["lightdns2.com"]["ns_ok"], out36
    assert out36["nozone-light.com"]["light"] and out36["nozone-light.com"]["zone"] is None, out36
    # Khong ghi de trang thai day du (co zone).
    full36 = {"domain": "lightdns.com", "zone": {"id": "z", "status": "active",
              "name_servers": [NS1, NS2]}, "ns_ok": True, "records": [{"id": "r1"}],
              "records_ok": True, "email": {"enabled": True}, "https": {"ok": True},
              "error": None}
    st36["lightdns.com"] = dict(full36)
    dash._check_dns_light(["lightdns.com"], cfg6, st36)
    assert st36["lightdns.com"] == full36, st36
    # Khong ghi de trang thai day du (khong co zone).
    st36["nozone-light.com"] = {"domain": "nozone-light.com", "zone": None,
                                "ns_ok": False, "error": None}
    dash._check_dns_light(["nozone-light.com"], cfg6, st36)
    assert st36["nozone-light.com"] == {"domain": "nozone-light.com", "zone": None,
                                        "ns_ok": False, "error": None}, st36
    # Trang thai loi thi duoc thay bang trang thai nhe.
    st36["lightdns.com"] = {"domain": "lightdns.com", "zone": None,
                            "error": "Too many requests"}
    dash._check_dns_light(["lightdns.com"], cfg6, st36)
    assert st36["lightdns.com"]["light"] and st36["lightdns.com"]["ns_ok"] \
        and not st36["lightdns.com"]["error"], st36
    print("  OK", flush=True)

    print("TEST 37: _sync_ns — DoH xac nhan thi chi 1 call NC (setCustom)...", flush=True)
    z20, _c = cf.get_or_create_zone("syncns.com")
    STATE["nc_ns"]["syncns.com"] = ["dns1.registrar-servers.com",
                                    "dns2.registrar-servers.com"]  # DoH chua tro dung
    STATE["nc_calls"] = 0
    steps37, ok37 = dash._sync_ns("syncns.com", [NS1, NS2], cfg6)
    assert ok37, steps37
    assert STATE["nc_calls"] == 1, STATE["nc_calls"]  # chi setCustom, khong getList
    assert [s["step"] for s in steps37] == ["Doi NS o Namecheap", "DNS lan truyen"], steps37
    assert all(s["ok"] for s in steps37), steps37
    # Lan truyen cham (DoH khong xac nhan) -> goi getList xac minh registrar.
    import time as _time
    orig_doh = dash._doh
    orig_sleep = _time.sleep
    dash._doh = lambda d: ["dns1.registrar-servers.com", "dns2.registrar-servers.com"]
    _time.sleep = lambda s: None
    try:
        STATE["nc_ns"]["syncns2.com"] = ["dns1.registrar-servers.com",
                                         "dns2.registrar-servers.com"]
        z21, _c = cf.get_or_create_zone("syncns2.com")
        STATE["nc_calls"] = 0
        steps37b, ok37b = dash._sync_ns("syncns2.com", [NS1, NS2], cfg6)
        assert ok37b, steps37b
        assert STATE["nc_calls"] == 2, STATE["nc_calls"]  # setCustom + getList
        by_step = {s["step"]: s for s in steps37b}
        assert by_step["Doi NS o Namecheap"]["ok"], steps37b
        assert not by_step["DNS lan truyen"]["ok"], steps37b
    finally:
        dash._doh = orig_doh
        _time.sleep = orig_sleep
    print("  OK", flush=True)

    print("TEST 38: _records_preset_match — so voi MOI preset, tra ten preset khop...", flush=True)
    cfg38 = dict(cfg)
    cfg38["presets"] = {
        "web": [{"type": "A", "name": "@", "content": "1.11.11.11", "proxied": True},
                {"type": "A", "name": "www", "content": "1.11.11.11", "proxied": True}],
        "Web Long": [{"type": "A", "name": "@", "content": "2.22.22.22", "proxied": True},
                     {"type": "A", "name": "www", "content": "2.22.22.22", "proxied": True}],
    }
    cfg38["default_preset"] = "web"
    recs_web = [{"type": "A", "name": "preset38.com", "content": "1.11.11.11"},
                {"type": "A", "name": "www.preset38.com", "content": "1.11.11.11"}]
    recs_long = [{"type": "A", "name": "preset38.com", "content": "2.22.22.22"},
                 {"type": "A", "name": "www.preset38.com", "content": "2.22.22.22"}]
    assert dash._records_preset_match(recs_web, cfg38, "preset38.com") == "web"
    assert dash._records_preset_match(recs_long, cfg38, "preset38.com") == "Web Long"
    assert dash._records_preset_match([], cfg38, "preset38.com") is None
    assert dash._records_preset_match(
        [{"type": "A", "name": "preset38.com", "content": "9.9.9.9"}], cfg38, "preset38.com") is None
    # check_domain: record khop preset khong phai mac dinh thi van tra ten.
    z38, _c = cf.get_or_create_zone("preset38.com")
    cf.create_record(z38["id"], "A", "preset38.com", "2.22.22.22", True)
    cf.create_record(z38["id"], "A", "www.preset38.com", "2.22.22.22", True)
    STATE["nc_ns"]["preset38.com"] = [NS1, NS2]
    st38 = dash.check_domain("preset38.com", cfg38)
    assert st38["records_preset"] == "Web Long" and st38["records_ok"] is True, st38
    # Record khong khop preset nao -> records_preset None, records_ok False.
    z38b, _c = cf.get_or_create_zone("preset38b.com")
    cf.create_record(z38b["id"], "A", "preset38b.com", "9.9.9.9", True)
    STATE["nc_ns"]["preset38b.com"] = [NS1, NS2]
    st38b = dash.check_domain("preset38b.com", cfg38)
    assert st38b["records_preset"] is None and st38b["records_ok"] is False, st38b
    print("  OK", flush=True)

    print("TEST 39: _sync_records — xung dot CNAME + setup_domain dung preset duoc chon (khong phai mac dinh)...", flush=True)
    # (a) Domain co CNAME @, preset muon A @ -> xoa CNAME truoc roi tao A.
    z39, _c = cf.get_or_create_zone("cname39.com")
    cf.create_record(z39["id"], "CNAME", "cname39.com", "stores.sarivon.com", True)
    d39, ok39 = dash._sync_records("cname39.com",
        [{"type": "A", "name": "@", "content": "1.11.11.11", "proxied": True},
         {"type": "A", "name": "www", "content": "1.11.11.11", "proxied": True}], cf, z39)
    assert ok39, d39
    r39 = cf.list_records(z39["id"])
    assert not any(x["type"] == "CNAME" for x in r39), r39
    assert any(x["type"] == "A" and x["content"] == "1.11.11.11" for x in r39), r39
    # (b) Domain co A @, preset muon CNAME @ -> xoa A truoc roi tao CNAME.
    z39b, _c = cf.get_or_create_zone("cname39b.com")
    cf.create_record(z39b["id"], "A", "cname39b.com", "1.11.11.11", True)
    d39b, ok39b = dash._sync_records("cname39b.com",
        [{"type": "CNAME", "name": "@", "content": "stores.sarivon.com", "proxied": True}], cf, z39b)
    assert ok39b, d39b
    r39b = cf.list_records(z39b["id"])
    assert not any(x["type"] == "A" for x in r39b), r39b
    assert any(x["type"] == "CNAME" and x["content"] == "stores.sarivon.com" for x in r39b), r39b
    # (c) CNAME giong het thi giu, khong xoa.
    z39c, _c = cf.get_or_create_zone("cname39c.com")
    cf.create_record(z39c["id"], "CNAME", "cname39c.com", "stores.sarivon.com", True)
    d39c, ok39c = dash._sync_records("cname39c.com",
        [{"type": "CNAME", "name": "@", "content": "stores.sarivon.com", "proxied": True}], cf, z39c)
    assert ok39c and "giu 1 record giong het" in d39c, d39c
    assert len(cf.list_records(z39c["id"])) == 1
    # (d) Record he thong Email Routing (MX) khong bi xoa khi preset muon CNAME @.
    z39d, _c = cf.get_or_create_zone("cname39d.com")
    cf.create_record(z39d["id"], "MX", "cname39d.com", "route1.mx.cloudflare.net", False)
    d39d, ok39d = dash._sync_records("cname39d.com",
        [{"type": "CNAME", "name": "@", "content": "stores.sarivon.com", "proxied": True}], cf, z39d)
    r39d = cf.list_records(z39d["id"])
    assert any(x["type"] == "MX" and "mx.cloudflare.net" in x["content"] for x in r39d), r39d
    # (e) setup_domain dung preset duoc truyen vao, khong phai mac dinh.
    cfg39 = dict(cfg)
    cfg39["presets"] = dict(cfg["presets"])
    cfg39["presets"]["Web Long"] = [{"type": "CNAME", "name": "@", "content": "stores.sarivon.com", "proxied": True}]
    cfg39["default_preset"] = "web"
    STATE["nc_ns"]["long39.com"] = ["dns1.registrar-servers.com", "dns2.registrar-servers.com"]
    res39 = dash.setup_domain("long39.com", "Web Long", cfg39)
    assert res39["ok"], res39
    assert any(s["step"] == "Ap preset 'Web Long'" and s["ok"] for s in res39["steps"]), res39["steps"]
    st39 = dash.check_domain("long39.com", cfg39)
    assert any(x["type"] == "CNAME" and x["content"] == "stores.sarivon.com" for x in st39["records"]), st39
    assert st39["records_preset"] == "Web Long", st39
    print("  OK", flush=True)

    print("TEST 40: _sync_records go record preset cu (1 domain 1 cau hinh) + setup_domain skip_records...", flush=True)
    cfg40 = dict(cfg)
    cfg40["presets"] = {
        "Web Chị Hân": [{"type": "A", "name": "@", "content": "134.209.163.195", "proxied": True},
                        {"type": "A", "name": "www", "content": "134.209.163.195", "proxied": True}],
        "Web Anh Long": [{"type": "CNAME", "name": "@", "content": "stores.sarivon.com", "proxied": True}],
    }
    cfg40["default_preset"] = "Web Chị Hân"
    # (a) Domain dang dung 'Web Chi Han', ap 'Web Anh Long' -> A @ bi xoa
    # do xung dot CNAME, A www bi go vi thuoc preset cu, record tu do + MX
    # he thong duoc giu.
    z40, _c = cf.get_or_create_zone("one40.com")
    cf.create_record(z40["id"], "A", "one40.com", "134.209.163.195", True)
    cf.create_record(z40["id"], "A", "www.one40.com", "134.209.163.195", True)
    cf.create_record(z40["id"], "TXT", "blog.one40.com", "custom", False)
    cf.create_record(z40["id"], "MX", "one40.com", "route1.mx.cloudflare.net", False)
    other40 = dash._other_preset_keys("one40.com", cfg40, "Web Anh Long")
    d40, ok40 = dash._sync_records("one40.com", cfg40["presets"]["Web Anh Long"], cf, z40, other40)
    assert ok40, d40
    assert "da go 1 record cua preset khac" in d40, d40
    r40 = cf.list_records(z40["id"])
    assert any(x["type"] == "CNAME" and x["content"] == "stores.sarivon.com" for x in r40), r40
    assert not any(x["type"] == "A" for x in r40), r40
    assert any(x["type"] == "TXT" and x["content"] == "custom" for x in r40), r40
    assert any(x["type"] == "MX" and "mx.cloudflare.net" in x["content"] for x in r40), r40
    # (b) setup_domain skip_records=True: giu nguyen record, van lam email + HTTPS.
    STATE["nc_ns"]["skip40.com"] = ["dns1.registrar-servers.com", "dns2.registrar-servers.com"]
    res40 = dash.setup_domain("skip40.com", "Web Anh Long", cfg40, skip_records=True)
    assert res40["ok"], res40
    step40 = [s for s in res40["steps"] if s["step"] == "Ap preset 'Web Anh Long'"]
    assert len(step40) == 1 and step40[0]["ok"] and "bỏ qua" in step40[0]["detail"], res40["steps"]
    z40b, _c = cf.get_or_create_zone("skip40.com")
    assert cf.list_records(z40b["id"]) == [], cf.list_records(z40b["id"])
    print("  OK", flush=True)

    print("TEST 41: Google Sheet — doc B/H/J tu start_row, map rule, setup tung hang + ghi cot J...", flush=True)
    cfg41 = dict(cfg)
    cfg41["presets"] = {
        "Web Chị Hân": [{"type": "A", "name": "@", "content": "134.209.163.195", "proxied": True},
                        {"type": "A", "name": "www", "content": "134.209.163.195", "proxied": True}],
        "Web Anh Long": [{"type": "CNAME", "name": "@", "content": "stores.sarivon.com", "proxied": True}],
    }
    cfg41["default_preset"] = "Web Chị Hân"
    cfg41["sheet_rules"] = [
        {"name": "Web Thuần Hân", "preset": "Web Chị Hân"},
        {"name": "Web Thuần Long", "preset": "Web Anh Long"},
    ]
    cfg41["sheet_start_row"] = 1402
    cfg41["sheet_csv_url"] = "http://example.invalid/sheet.csv"
    cfg41["sheet_review"] = []
    cfg41["sheet_ignored"] = []
    def fake_csv(url):
        assert url == cfg41["sheet_csv_url"], url
        rows = [["", "Tên Domain", "Hạn Domain", "DaysLeft", "LLC", "Address",
                 "Số lần làm lại web", "Loại web", "Tên Dev", "Đã hoàn thành"]]
        while len(rows) < 1401:
            rows.append([""] * 12)
        def mk(domain, loai, j):
            r = [""] * 12
            r[1] = domain; r[7] = loai; r[9] = j
            return r
        rows.append(mk("stylenora.com", "Web Thuần Hân", "Cần set up"))   # 1402
        rows.append(mk("vellvyn.com", "Web Thuần Hân", ""))                # 1403 bo qua (J trong)
        rows.append(mk("vallisse.com", "Web Thuần Hân", "đã set up"))       # 1404 skip
        rows.append(mk("conflict41.com", "Web Thuần Long", "Cần set up"))   # 1405 review
        rows.append(mk("unmapped41.com", "Web Thuần", "Cần set up"))        # 1406 skip (chua co rule)
        rows.append(mk("stylenora.com", "Web Thuần Hân", "Cần set up"))    # 1407 trung, giu dong cuoi
        return rows
    dash._sheet_fetch_csv = fake_csv
    # conflict41.com dang dung 'Web Chi Han' (se xung dot voi 'Web Anh Long' trong sheet).
    z41, _c = cf.get_or_create_zone("conflict41.com")
    cf.create_record(z41["id"], "A", "conflict41.com", "134.209.163.195", True)
    cf.create_record(z41["id"], "A", "www.conflict41.com", "134.209.163.195", True)
    st41 = {}
    dash._save_check_result(st41, "conflict41.com",
                            dash.check_domain("conflict41.com", cfg41, skip_nc=True))
    assert st41["conflict41.com"].get("records_preset") == "Web Chị Hân", st41
    rows41, err41, src41 = dash.sheet_read_rows(cfg41)
    assert not err41 and src41 == "csv", (err41, src41)  # chua co sheet_id/sa_key -> CSV
    by41 = {r["domain"]: r for r in rows41}
    assert set(by41) == {"stylenora.com", "vallisse.com", "conflict41.com", "unmapped41.com"}, by41
    assert by41["stylenora.com"]["row"] == 1407, by41["stylenora.com"]  # trung -> giu dong cuoi
    cls41 = dash._sheet_classify(rows41, cfg41, st41)
    act41 = {r["domain"]: r["action"] for r in cls41}
    assert act41 == {"stylenora.com": "setup", "vallisse.com": "skip",
                     "conflict41.com": "review", "unmapped41.com": "skip"}, act41
    # Rule doi ten duoc: _sheet_preset_for doc tu cfg (khong cung SHEET_MAP).
    assert dash._sheet_preset_for("Web Thuần Hân", cfg41) == "Web Chị Hân"
    cfg41["sheet_rules"][0] = {"name": "Web Thuần Hân Mới", "preset": "Web Anh Long"}
    assert dash._sheet_preset_for("Web Thuần Hân", cfg41) is None
    assert dash._sheet_preset_for("Web Thuần Hân Mới", cfg41) == "Web Anh Long"
    cfg41["sheet_rules"][0] = {"name": "Web Thuần Hân", "preset": "Web Chị Hân"}
    # Setup 1 hang: stylenora.com (clean) -> xong + ghi 'da set up' ve cot J (mock).
    STATE["nc_ns"]["stylenora.com"] = ["dns1.registrar-servers.com", "dns2.registrar-servers.com"]
    written = []
    _real_write = dash.sheet_write_status
    def fake_write(row, value, cfg):
        written.append((row, value))
        return True, "ok"
    dash.sheet_write_status = fake_write
    one41 = dash._sheet_setup_one("stylenora.com", 1407, "Web Chị Hân", cfg41, st41)
    assert one41["action"] == "done", one41
    assert written == [(1407, "đã set up")], written
    z41b, _c = cf.get_or_create_zone("stylenora.com")
    assert any(x["type"] == "A" and x["content"] == "134.209.163.195"
               for x in cf.list_records(z41b["id"])), cf.list_records(z41b["id"])
    # Batch: conflict vao hang cho duyet, unmapped/vallisse skip, stylenora setup lai (da co preset).
    res41 = dash.sheet_setup_clean(cfg41, st41)
    assert res41["ok"], res41
    assert [q["domain"] for q in res41["queued"]] == ["conflict41.com"], res41
    assert any(q["domain"] == "conflict41.com" for q in cfg41["sheet_review"]), cfg41["sheet_review"]
    dash.sheet_write_status = _real_write  # khoi phuc de TEST 42 dung ham that
    print("  OK", flush=True)

    print("TEST 42: Google Sheet — doc qua Sheets API (live), fallback CSV, ghi cot J...", flush=True)
    import json as _json
    from http.server import BaseHTTPRequestHandler
    put_seen = []
    class SheetsHandler(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def _send(self, obj, code=200):
            data = _json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        def do_GET(self):
            if "/values/" in self.path:
                self._send({"values": [
                    ["", "Tên Domain", "Hạn Domain", "DaysLeft", "LLC", "Address",
                     "Số lần làm lại web", "Loại web", "Tên Dev", "Đã hoàn thành"],
                    ["", "apinora.com", "", "", "", "", "", "Web Thuần Hân", "", "Cần set up"],
                    ["", "apilong.com", "", "", "", "", "", "Web Thuần Long", "", "Cần set up"],
                    ["", "short.com", "Web Thuần Hân"],  # dong ngan (thieu cot J)
                ]})
            elif self.path.startswith("/v4/spreadsheets/"):
                self._send({"sheets": [
                    {"properties": {"sheetId": 724861620, "title": "Danh sách"}},
                    {"properties": {"sheetId": 123, "title": "Khác"}}]})
            else:
                self.send_response(404); self.end_headers()
        def do_PUT(self):
            n = int(self.headers.get("Content-Length") or 0)
            put_seen.append((self.path, _json.loads(self.rfile.read(n).decode())))
            self._send({"updatedCells": 1})
    sh_srv = ThreadingHTTPServer(("127.0.0.1", 18098), SheetsHandler)
    threading.Thread(target=sh_srv.serve_forever, daemon=True).start()
    dash.SHEETS_API_BASE = "http://127.0.0.1:18098"
    dash._sa_access_token = lambda cfg: "test-token"
    cfg42 = dict(cfg41)
    cfg42["sheet_id"] = "test-sheet-id"
    cfg42["sheet_sa_key"] = "/tmp/fake-sa.json"
    cfg42["sheet_start_row"] = 2  # mock API chi tra 4 dong (header + 3 dong)
    cfg42["sheet_csv_url"] = ("https://docs.google.com/spreadsheets/d/e/2PACX-1vX/pub"
                              "?gid=724861620&single=true&output=csv")
    # fallback dung fake_csv cua TEST 41 nhung khong kiem tra URL (cfg42 doi URL de test gid).
    _fake_csv41 = fake_csv
    dash._sheet_fetch_csv = lambda url: _fake_csv41("http://example.invalid/sheet.csv")
    # _sheet_fetch_api: tim dung tab theo gid + tra ve rows.
    api_rows = dash._sheet_fetch_api(cfg42)
    assert api_rows[1][1] == "apinora.com", api_rows
    assert api_rows[3][1] == "short.com", api_rows
    # sheet_read_rows uu tien API khi co sheet_id + sa_key.
    rows42, err42, src42 = dash.sheet_read_rows(cfg42)
    assert not err42 and src42 == "api", (err42, src42)
    assert {r["domain"] for r in rows42} == {"apinora.com", "apilong.com"}, rows42
    # Ghi cot J qua API: PUT dung range (tab theo gid) + gia tri.
    ok42, det42 = dash.sheet_write_status(1402, "đã set up", cfg42)
    assert ok42, det42
    assert len(put_seen) == 1 and "J1402" in put_seen[0][0], put_seen
    assert put_seen[0][1]["values"] == [["đã set up"]], put_seen
    # API loi -> fallback CSV (van doc duoc, source csv).
    cfg42["sheet_start_row"] = 1402  # fake_csv co dong 1402+
    dash._sheet_fetch_api = lambda cfg: (_ for _ in ()).throw(RuntimeError("api down"))
    rows42b, err42b, src42b = dash.sheet_read_rows(cfg42)
    assert not err42b and src42b == "csv", (err42b, src42b)
    assert "stylenora.com" in {r["domain"] for r in rows42b}, rows42b
    # Khong co sheet_id/sa_key -> CSV thang.
    cfg42["sheet_id"] = ""
    rows42c, err42c, src42c = dash.sheet_read_rows(cfg42)
    assert not err42c and src42c == "csv", (err42c, src42c)
    sh_srv.shutdown()
    print("  OK", flush=True)

    srv.shutdown()
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
