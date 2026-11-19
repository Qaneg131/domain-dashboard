#!/usr/bin/env python3
"""Check DNS cong (ground truth) qua DNS-over-HTTPS — khong can key.

doh_ns(domain): NS hien tai tren DNS cong (1.1.1.1). Day la cach chac
chan nhat de biet domain da tro NS dung chua — khoi phai load lai trang
Manage o Namecheap roi doan.
"""
import json
import urllib.error
import urllib.parse
import urllib.request

DOH_BASE = "https://cloudflare-dns.com/dns-query"


def doh_ns(domain, base=DOH_BASE):
    """Tra ve list NS dang hien tren DNS cong, vd ['brit.ns.cloudflare.com', ...]."""
    url = base + "?name=" + urllib.parse.quote(domain) + "&type=NS"
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/dns-json")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as e:
        raise RuntimeError(f"DoH lookup that bai: {e}")
    out = []
    for a in data.get("Answer", []) or []:
        if a.get("type") == 2:  # NS record
            v = (a.get("data") or "").strip().rstrip(".").lower()
            if v and v not in out:
                out.append(v)
    return out


def my_ip():
    """IP cong hien tai cua may — de biet whitelist IP nao o Namecheap API."""
    req = urllib.request.Request("https://api.ipify.org")
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read().decode().strip()
