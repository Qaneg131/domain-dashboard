#!/usr/bin/env python3
"""Cloudflare API v4 — chi dung urllib (stdlib), khong can requests.

Dung de:
- lay/tao zone (lay 2 nameserver ngay, khong can bam UI)
- doc/xoa/them DNS records

Can 1 API token (My Profile -> API Tokens) voi quyen Zone:Edit + DNS:Edit.
Token truyen qua CFClient(token), hoac de trong neu chi doc.
"""
import json
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://api.cloudflare.com/client/v4"


class CFError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status  # HTTP status (404, 403...) neu co; None neu khong ro


def expand_name(zone_name, name):
    """Doi ten record dang ngan ('@', 'www', 'blog') thanh FQDN cho API.
    '@' -> chinh domain; 'www' -> www.<domain>; ten da du domain thi giu nguyen."""
    name = (name or "").strip()
    if not name or name == "@":
        return zone_name
    if name.lower() == zone_name.lower() or name.lower().endswith("." + zone_name.lower()):
        return name
    return f"{name}.{zone_name}"


class CFClient:
    def __init__(self, token, account_id=None, base=BASE):
        self.token = token
        self.account_id = (account_id or "").strip() or None
        self.base = base.rstrip("/")

    def _req(self, method, path, body=None):
        url = self.base + path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            try:
                payload = json.loads(e.read().decode())
                errs = payload.get("errors") or []
                msg = "; ".join(x.get("message", "") for x in errs) or f"HTTP {e.code}"
            except Exception:
                msg = f"HTTP {e.code}"
            raise CFError(msg, status=e.code)
        except urllib.error.URLError as e:
            raise CFError(f"khong noi duoc Cloudflare API: {e.reason}")

    def _ok(self, payload):
        if not payload.get("success"):
            errs = payload.get("errors") or []
            raise CFError("; ".join(x.get("message", "") for x in errs) or "Cloudflare API error")
        return payload.get("result")

    # ------------------------------------------------------------- zone
    def get_zone(self, name):
        """Tra ve zone dict hoac None neu chua co zone nay."""
        payload = self._req("GET", f"/zones?name={urllib.parse.quote(name)}")
        for z in self._ok(payload) or []:
            if z.get("name", "").lower() == name.lower():
                return z
        return None

    def create_zone(self, name):
        """Tao zone moi (mac dinh goi Free). Tra ve zone dict kem name_servers."""
        body = {"name": name, "type": "full"}
        if self.account_id:
            body["account"] = {"id": self.account_id}
        payload = self._req("POST", "/zones", body)
        return self._ok(payload)

    def get_or_create_zone(self, name):
        """(zone, created?) — co roi thi lay, chua co thi tao."""
        z = self.get_zone(name)
        if z:
            return z, False
        return self.create_zone(name), True

    def list_zones(self):
        """Tat ca zone trong tai khoan (de tab 'Tat ca trong tai khoan')."""
        out, page = [], 1
        while True:
            payload = self._req("GET", f"/zones?per_page=100&page={page}")
            out.extend(self._ok(payload) or [])
            info = payload.get("result_info") or {}
            if page >= info.get("total_pages", 1):
                break
            page += 1
        return out

    # ---------------------------------------------------------- records
    def list_records(self, zone_id):
        out, page = [], 1
        while True:
            payload = self._req(
                "GET", f"/zones/{zone_id}/dns_records?per_page=100&page={page}")
            out.extend(self._ok(payload) or [])
            info = payload.get("result_info") or {}
            if page >= info.get("total_pages", 1):
                break
            page += 1
        return out

    def create_record(self, zone_id, rtype, name, content, proxied=True, ttl=1):
        # Cloudflare chi proxy A/AAAA/CNAME; loai khac bat buoc proxied=false.
        if rtype not in ("A", "AAAA", "CNAME"):
            proxied = False
        body = {"type": rtype, "name": name, "content": content,
                "proxied": proxied, "ttl": 1 if proxied else ttl}
        payload = self._req("POST", f"/zones/{zone_id}/dns_records", body)
        return self._ok(payload)

    def delete_record(self, zone_id, record_id):
        payload = self._req("DELETE", f"/zones/{zone_id}/dns_records/{record_id}")
        return self._ok(payload)

    def delete_all_records(self, zone_id, preserve_email=True):
        """Xoa het record cu. Tra ve so record da xoa.
        Mac dinh GIU LAI record he thong cua Email Routing (3 MX
        route*.mx.cloudflare.net + SPF + DKIM) de ap preset khong lam
        hong email — cac record nay do chuc nang Email Routing quan ly.
        Cung GIU LAI record TXT xac minh Google Search Console (set tay)
        de Setup/ap preset khong lam mat xac minh."""
        recs = self.list_records(zone_id)
        kept = 0
        for r in recs:
            if preserve_email and _is_email_routing_record(r):
                kept += 1
                continue
            if _is_gsc_record(r):
                kept += 1
                continue
            self.delete_record(zone_id, r["id"])
        return len(recs) - kept

    # --------------------------------------------------- zone settings (HTTPS)
    def get_setting(self, zone_id, name):
        """Doc 1 setting: 'ssl', 'always_use_https', 'automatic_https_rewrites'..."""
        payload = self._req("GET", f"/zones/{zone_id}/settings/{name}")
        return self._ok(payload)

    def set_setting(self, zone_id, name, value):
        """Ghi 1 setting. Tra ve {id, value, ...}."""
        payload = self._req("PATCH", f"/zones/{zone_id}/settings/{name}",
                            {"value": value})
        return self._ok(payload)

    def get_https(self, zone_id):
        """{'ssl', 'always_https', 'rewrites', 'min_tls', 'ok'} —
        ok = ssl != off va always_https on."""
        ssl = self.get_setting(zone_id, "ssl").get("value", "off")
        auh = self.get_setting(zone_id, "always_use_https").get("value", "off")
        rew = self.get_setting(zone_id, "automatic_https_rewrites").get("value", "off")
        tls = self.get_setting(zone_id, "min_tls_version").get("value", "1.0")
        return {"ssl": ssl, "always_https": auh, "rewrites": rew, "min_tls": tls,
                "ok": ssl != "off" and auh == "on"}

    def set_https(self, zone_id, ssl=None, always_https=None, rewrites=None,
                  min_tls=None):
        out = {}
        if ssl is not None:
            out["ssl"] = self.set_setting(zone_id, "ssl", ssl).get("value")
        if always_https is not None:
            out["always_https"] = self.set_setting(
                zone_id, "always_use_https", always_https).get("value")
        if rewrites is not None:
            out["rewrites"] = self.set_setting(
                zone_id, "automatic_https_rewrites", rewrites).get("value")
        if min_tls is not None:
            out["min_tls"] = self.set_setting(
                zone_id, "min_tls_version", min_tls).get("value")
        return out

    # --------------------------------------------------- email routing
    def get_email_routing(self, zone_id):
        """{'enabled': bool, 'status': 'ready|unconfigured|...'}.
        Can quyen Zone Settings (token can co Zone Settings:Edit)."""
        payload = self._req("GET", f"/zones/{zone_id}/email/routing")
        return self._ok(payload)

    def enable_email_routing(self, zone_id):
        """Bat Email Routing. LUU Y: Cloudflare se THEM/KHOA 3 MX
        (route1/2/3.mx.cloudflare.net) + SPF + DKIM — ghi de mail cu."""
        payload = self._req("POST", f"/zones/{zone_id}/email/routing/enable")
        return self._ok(payload)

    def disable_email_routing(self, zone_id):
        payload = self._req("POST", f"/zones/{zone_id}/email/routing/disable")
        return self._ok(payload)

    def list_email_rules(self, zone_id):
        payload = self._req("GET", f"/zones/{zone_id}/email/routing/rules")
        return self._ok(payload) or []

    def create_email_rule(self, zone_id, match_value, destinations, name=None):
        """Tao rule forward. match_value: 'info@domain.com' hoac '*' (catch-all).
        destinations: list email nhan. Dia chi dich phai duoc verify truoc
        (loi 2054 neu chua) — xem create_email_address.
        LUU Y: '*' KHONG phai rule thuong — no la singleton o endpoint
        rieng /rules/catch_all (POST all-matcher vao /rules bi API tra
        'Invalid rule operation'), nen '*' duoc chuyen sang set_catch_all."""
        if match_value == "*":
            return self.set_catch_all(zone_id, destinations)
        matchers = [{"type": "literal", "field": "to", "value": match_value}]
        body = {"name": name or f"forward {match_value}",
                "matchers": matchers,
                "actions": [{"type": "forward", "value": list(destinations)}],
                "enabled": True}
        payload = self._req("POST", f"/zones/{zone_id}/email/routing/rules", body)
        return self._ok(payload)

    def get_catch_all(self, zone_id):
        """Rule catch-all hien tai hoac None (chua set / dang tat)."""
        try:
            payload = self._req(
                "GET", f"/zones/{zone_id}/email/routing/rules/catch_all")
            return self._ok(payload)
        except CFError as e:
            if "404" in str(e) or "not found" in str(e).lower():
                return None
            raise

    def set_catch_all(self, zone_id, destinations, name="Catch-all"):
        """PUT catch-all (singleton cua zone): forward moi mail khong khop
        rule nao. Catch-all chi cho DUNG 1 dia chi dich (API bat buoc),
        nen chi lay dia chi dau tien."""
        dest = list(destinations)[:1]
        body = {"name": name,
                "matchers": [{"type": "all"}],
                "actions": [{"type": "forward", "value": dest}],
                "enabled": True}
        payload = self._req(
            "PUT", f"/zones/{zone_id}/email/routing/rules/catch_all", body)
        return self._ok(payload)

    def delete_email_rule(self, zone_id, rule_id):
        payload = self._req("DELETE", f"/zones/{zone_id}/email/routing/rules/{rule_id}")
        return self._ok(payload)

    def list_email_addresses(self, zone_id):
        payload = self._req("GET", f"/zones/{zone_id}/email/routing/addresses")
        return self._ok(payload) or []

    def create_email_address(self, zone_id, email):
        """Them dia chi dich + gui mail xac minh (user phai bam link trong mail)."""
        payload = self._req("POST", f"/zones/{zone_id}/email/routing/addresses",
                            {"email": email})
        return self._ok(payload)

    # --------------------------------------------------- cache rules (bypass)
    def get_cache_ruleset(self, zone_id):
        """Ruleset phase http_request_cache_settings (entrypoint) hoac None
        neu chua co rule nao. Can quyen Zone Cache Rules (Edit) tren token
        (+ Account Rulesets/Filter Lists theo tai lieu Cloudflare)."""
        try:
            payload = self._req(
                "GET", f"/zones/{zone_id}/rulesets/phases/"
                       f"http_request_cache_settings/entrypoint")
            return self._ok(payload)
        except CFError as e:
            # Chua co ruleset nao o phase nay -> HTTP 404. KHONG doan bang
            # chu trong message: API that tra 'The ruleset does not
            # exist.' (khong co '404'/'not found') nen phai dung status.
            if e.status == 404 or "not found" in str(e).lower():
                return None
            raise

    def has_cache_bypass(self, zone_id, name="By Pass"):
        rs = self.get_cache_ruleset(zone_id)
        if not rs:
            return False
        for r in rs.get("rules") or []:
            ap = r.get("action_parameters") or {}
            if (r.get("action") == "set_cache_settings"
                    and ap.get("cache") is False
                    and (r.get("description") == name
                         or r.get("expression") == "true")):
                return True
        return False

    def set_cache_bypass(self, zone_id, name="By Pass"):
        """Them rule Bypass cache cho MOI request (nhu anh dashboard:
        rule 'By Pass', All incoming requests, Cache eligibility: Bypass).
        PUT thay the toan bo ruleset nen GET truoc roi gop — khong xoa
        rule cu. Rule da co thi khong tao trung."""
        rs = self.get_cache_ruleset(zone_id)
        rules = list((rs or {}).get("rules") or [])
        for r in rules:
            ap = r.get("action_parameters") or {}
            if (r.get("action") == "set_cache_settings"
                    and ap.get("cache") is False
                    and (r.get("description") == name
                         or r.get("expression") == "true")):
                return {"already": True, "rule": r}
        new_rule = {"action": "set_cache_settings",
                    "action_parameters": {"cache": False},
                    "expression": "true",
                    "description": name,
                    "enabled": True}
        rules.append(new_rule)
        if rs:
            payload = self._req(
                "PUT", f"/zones/{zone_id}/rulesets/phases/"
                       f"http_request_cache_settings/entrypoint",
                {"rules": rules})
            self._ok(payload)
        else:
            payload = self._req("POST", f"/zones/{zone_id}/rulesets",
                                {"name": "default", "kind": "zone",
                                 "phase": "http_request_cache_settings",
                                 "rules": rules})
            self._ok(payload)
        return {"already": False, "rule": new_rule}

    def remove_cache_bypass(self, zone_id, name="By Pass"):
        rs = self.get_cache_ruleset(zone_id)
        if not rs:
            return {"removed": False}
        rules = [r for r in (rs.get("rules") or [])
                 if not (r.get("action") == "set_cache_settings"
                         and (r.get("action_parameters") or {}).get("cache") is False
                         and (r.get("description") == name
                              or r.get("expression") == "true"))]
        if len(rules) == len(rs.get("rules") or []):
            return {"removed": False}
        payload = self._req(
            "PUT", f"/zones/{zone_id}/rulesets/phases/"
                   f"http_request_cache_settings/entrypoint",
            {"rules": rules})
        self._ok(payload)
        return {"removed": True}

    # --------------------------------------------------- xoa zone
    def delete_zone(self, zone_id):
        """Xoa zone tren Cloudflare (khong khoi phuc duoc)."""
        payload = self._req("DELETE", f"/zones/{zone_id}")
        return self._ok(payload)


def _is_email_routing_record(r):
    """Record do Email Routing tao/khoa (khong thuoc preset web)."""
    t = (r.get("type") or "").upper()
    name = (r.get("name") or "").lower()
    content = (r.get("content") or "").lower()
    if t == "MX" and "mx.cloudflare.net" in content:
        return True
    if t == "TXT" and "include:_spf.mx.cloudflare.net" in content:
        return True
    if t == "TXT" and name.startswith("cf2024-1._domainkey"):
        return True
    return False


def _is_gsc_record(r):
    """Record TXT xac minh Google Search Console — set tay, giu lai khi
    xoa record cu (giong record he thong Email Routing)."""
    if (r.get("type") or "").upper() != "TXT":
        return False
    return str(r.get("content") or "").strip().strip('"').startswith(
        "google-site-verification=")
