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
    pass


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
            raise CFError(msg)
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

    def delete_all_records(self, zone_id):
        """Xoa het record cu. Tra ve so record da xoa."""
        recs = self.list_records(zone_id)
        for r in recs:
            self.delete_record(zone_id, r["id"])
        return len(recs)

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
        """{'ssl': 'off|flexible|full|strict', 'always_https': 'on|off',
        'rewrites': 'on|off', 'ok': bool} — ok = ssl != off va always_https on."""
        ssl = self.get_setting(zone_id, "ssl").get("value", "off")
        auh = self.get_setting(zone_id, "always_use_https").get("value", "off")
        rew = self.get_setting(zone_id, "automatic_https_rewrites").get("value", "off")
        return {"ssl": ssl, "always_https": auh, "rewrites": rew,
                "ok": ssl != "off" and auh == "on"}

    def set_https(self, zone_id, ssl=None, always_https=None, rewrites=None):
        out = {}
        if ssl is not None:
            out["ssl"] = self.set_setting(zone_id, "ssl", ssl).get("value")
        if always_https is not None:
            out["always_https"] = self.set_setting(
                zone_id, "always_use_https", always_https).get("value")
        if rewrites is not None:
            out["rewrites"] = self.set_setting(
                zone_id, "automatic_https_rewrites", rewrites).get("value")
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
        (loi 2054 neu chua) — xem create_email_address."""
        if match_value == "*":
            matchers = [{"type": "all"}]
        else:
            matchers = [{"type": "literal", "field": "to", "value": match_value}]
        body = {"name": name or f"forward {match_value}",
                "matchers": matchers,
                "actions": [{"type": "forward", "value": list(destinations)}],
                "enabled": True}
        payload = self._req("POST", f"/zones/{zone_id}/email/routing/rules", body)
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
