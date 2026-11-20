#!/usr/bin/env python3
"""Namecheap API — xml.response, chi dung urllib + ElementTree (stdlib).

Dung de doc/ghi nameserver o Namecheap ma khong can mo trinh duyet:
- namecheap.domains.dns.getList   -> NS hien tai o Namecheap
- namecheap.domains.dns.setCustom -> doi NS sang Cloudflare

Can bat API access trong tai khoan Namecheap (Profile -> Tools) va
WHITELIST IP may chay tool (ClientIp). IP doi (mang dong) thi phai
whitelist lai — xem nut 'Lay IP hien tai' o giao dien.
"""
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime

PROD = "https://api.namecheap.com/xml.response"

# Namecheap API gioi han toc do kha chat (goi don nhieu domain lien
# tuc la dinh "Too many requests" / "throttling"). Khi dinh gioi han
# thi doi roi thu lai: lan 1 doi 30s, lan 2 doi 60s, het luot thi bao
# loi ro de nguoi dung doi ~2 phut roi chay lai. (Test dat
# nc_api.NC_RETRY_WAITS = (0, 0) de khoi doi.)
NC_RETRY_WAITS = (30, 60)


def _parse_nc_date(s):
    """Ngay Namecheap tra ve (dang M/D/YYYY, vd 12/4/2026) -> 'YYYY-MM-DD'.
    None neu khong doc duoc."""
    s = (s or "").strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _is_rate_limit_msg(msg):
    m = str(msg or "").lower()
    return "too many requests" in m or "throttl" in m or "429" in m

# TLD 2 cap pho bien (de tach SLD/TLD dung, vd a.co.uk -> SLD=a, TLD=co.uk).
TWO_LEVEL_TLDS = {"co.uk", "org.uk", "me.uk", "com.au", "net.au", "org.au",
                  "co.nz", "com.br", "co.jp", "com.cn", "co.in", "com.mx"}


class NCError(Exception):
    pass


def split_domain(domain):
    """'example.com' -> ('example', 'com')."""
    parts = domain.strip().lower().split(".")
    if len(parts) < 2:
        raise NCError(f"domain khong hop le: {domain}")
    last2 = ".".join(parts[-2:])
    if last2 in TWO_LEVEL_TLDS and len(parts) >= 3:
        return ".".join(parts[:-2]), last2
    return ".".join(parts[:-1]), parts[-1]


class NCClient:
    def __init__(self, user, api_key, client_ip, base=PROD):
        self.user = user
        self.api_key = api_key
        self.client_ip = client_ip
        self.base = base

    def _call(self, command, **params):
        """Goi API, tu thu lai khi dinh gioi han toc do (xem NC_RETRY_WAITS)."""
        last = None
        for attempt in range(len(NC_RETRY_WAITS) + 1):
            try:
                return self._call_once(command, **params)
            except NCError as e:
                last = e
                if _is_rate_limit_msg(str(e)) and attempt < len(NC_RETRY_WAITS):
                    time.sleep(NC_RETRY_WAITS[attempt])
                    continue
                if _is_rate_limit_msg(str(e)) and "giới hạn tốc độ" not in str(e):
                    raise NCError(
                        f"Namecheap giới hạn tốc độ ({e}) — đợi ~2 phút rồi thử lại")
                raise
        raise last

    def _call_once(self, command, **params):
        q = {"ApiUser": self.user, "ApiKey": self.api_key, "UserName": self.user,
             "Command": command, "ClientIp": self.client_ip}
        q.update(params)
        url = self.base + "?" + urllib.parse.urlencode(q)
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            raise NCError(f"HTTP {e.code} tu Namecheap API")
        except urllib.error.URLError as e:
            raise NCError(f"khong noi duoc Namecheap API: {e.reason}")
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            raise NCError("Namecheap tra ve XML khong doc duoc")
        # Namecheap tra XML co xmlns mac dinh
        # (http://api.namecheap.com/xml.response) — khong boc namespace
        # thi findall('.//Error'), findall('.//Nameserver'),
        # find('.//DomainDNSSetCustomResult') deu tra ve rong, nen moi
        # loi that bi nuot va setCustom luon bao sai du da doi duoc.
        for el in root.iter():
            if "}" in el.tag:
                el.tag = el.tag.split("}", 1)[1]
        if root.get("Status") != "OK":
            errs = [e.text for e in root.findall(".//Error") if e.text]
            if errs:
                msg = "; ".join(errs)
                # Loi 1011150: IP may chay tool chua duoc whitelist.
                m = re.search(r"Invalid request IP: ([\d.]+)", msg)
                if m:
                    ip = m.group(1)
                    raise NCError(
                        f"Namecheap tu choi: IP {ip} chua duoc whitelist "
                        f"(loi 1011150) — them IP nay vao Namecheap Profile > "
                        f"Tools > API Access > Whitelisted IPs, va sua o Client IP "
                        f"trong Cai dat thanh {ip} (hoac bam 'Lay IP hien tai')")
                raise NCError(msg)
            # Khong co <Error> doc duoc — kem raw XML de doan nguyen nhan
            # (vd domain khong co trong tai khoan, IP chua whitelist...).
            raw_txt = " ".join(raw.decode("utf-8", errors="replace").split())
            raise NCError(f"Namecheap API error (Status={root.get('Status')}) "
                          f"— tra ve: {raw_txt[:300]}")
        return root

    def get_nameservers(self, domain):
        """NS hien tai o Namecheap (phia registrar)."""
        sld, tld = split_domain(domain)
        root = self._call("namecheap.domains.dns.getList", SLD=sld, TLD=tld)
        return [n.text.strip().lower() for n in root.findall(".//Nameserver")
                if n.text and n.text.strip()]

    def set_nameservers(self, domain, ns_list):
        """Doi NS sang danh sach cho truoc. Tra ve True neu Updated=true."""
        sld, tld = split_domain(domain)
        root = self._call("namecheap.domains.dns.setCustom", SLD=sld, TLD=tld,
                          Nameservers=",".join(ns_list))
        el = root.find(".//DomainDNSSetCustomResult")
        if el is None or el.get("Updated") != "true":
            raise NCError("setCustom khong tra ve Updated=true")
        return True

    def get_expiry_map(self):
        """Ngay het han cua tat ca domain trong tai khoan Namecheap.
        Tra ve dict {domain: 'YYYY-MM-DD'} (chi cac domain co Expires
        doc duoc). Doc het cac trang getList (PageSize=100)."""
        out = {}
        page = 1
        while True:
            root = self._call("namecheap.domains.getList", ListType="ALL",
                              Page=str(page), PageSize="100")
            rows = root.findall(".//Domain")
            if not rows:
                break
            for el in rows:
                name = (el.get("Name") or "").strip().lower()
                iso = _parse_nc_date(el.get("Expires") or "")
                if name and iso:
                    out[name] = iso
            if len(rows) < 100 or page >= 50:
                break
            page += 1
        return out
