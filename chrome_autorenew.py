#!/usr/bin/env python3
"""
Bat/tat Auto-Renew tren Namecheap BANG CHROME (bam toggle that).
Thuoc ve Domain Dashboard — chay tu tab Auto-renew trong Dashboard.

Tai sao phai bam tay: API namecheap.domains.setAutoRenew bao
IsSuccess=true nhung toggle tren web khong doi (xac minh 2026-11-20),
nen phai mo Chrome, tim domain trong Domain List roi bam toggle that.

Cach chay (Dashboard goi san, khong can go tay):
    python3 chrome_autorenew.py --prepare-nc [--profile-dir "Profile 1"]
    python3 chrome_autorenew.py --domains-file domains.txt --autorenew on [--privacy yes|no] [--profile-dir "Profile 1"]

- --prepare-nc: chi mo tab Namecheap Domain List de dang nhap truoc.
- --autorenew on: tim tung domain, doc trang thai toggle truoc; da BAT
  roi thi BO QUA (khong bam — bam se TAT di), chi bam khi dang TAT.
  Hop thoai Domain Privacy: --privacy yes = bam Yes, no = bam No.
- --profile-dir: ho so Chrome can dung, vd 'Default' hay 'Profile 1'
  (mac dinh: ho so dung gan nhat). Tool copy ho so sang thu muc rieng
  chrome-profile-tool/ de giu dang nhap giua cac lan chay — thu muc nay
  chua cookie nen TUYET DOI khong commit len git (da co trong .gitignore).
- Bao cao CSV ghi vao reports/chrome_setup_<ngay_gio>.csv.
"""
import argparse
import csv
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

import requests

# Windows: console mac dinh dung cp1258, khong in duoc vai chu tieng Viet
# -> ep stdout/stderr sang UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    # De --help van chay duoc khi chua cai playwright; loi chi bao khi chay that.
    sync_playwright = None

BASE_DIR = Path(__file__).resolve().parent
REPORTS_DIR = BASE_DIR / "reports"
REPORTS_DIR.mkdir(exist_ok=True)
CLOUDFLARE_LOGIN_URL = "https://dash.cloudflare.com/login"


CDP_PORT = 9222

def log(msg):
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        # Phòng khi console vẫn không phải UTF-8: in bản không dấu thay vì crash.
        print(msg.encode("ascii", "replace").decode("ascii"), flush=True)

def tool_version():
    """Mã commit đang chạy — in ra log để biết chắc user đang chạy bản mới
    hay bản cũ (nhiều lần sửa xong user chạy lại vẫn là bản cũ vì chưa pull)."""
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(BASE_DIR),
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() or "?"
    except Exception:
        return "?"

def default_profile_dir():
    system = platform.system()
    home = Path.home()
    if system == "Windows":
        local = os.environ.get("LOCALAPPDATA", "")
        return Path(local) / "Google" / "Chrome" / "User Data" if local else home
    if system == "Darwin":
        return home / "Library" / "Application Support" / "Google" / "Chrome"
    return home / ".config" / "google-chrome"

def default_chrome_exe():
    system = platform.system()
    candidates = []
    if system == "Windows":
        pf = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        pfx = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        local = os.environ.get("LOCALAPPDATA", "")
        candidates = [Path(pf) / "Google" / "Chrome" / "Application" / "chrome.exe",
                      Path(pfx) / "Google" / "Chrome" / "Application" / "chrome.exe",
                      Path(local) / "Google" / "Chrome" / "Application" / "chrome.exe"]
    elif system == "Darwin":
        candidates = [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")]
    else:
        candidates = [Path("/usr/bin/google-chrome"), Path("/usr/bin/google-chrome-stable"),
                      Path("/opt/google/chrome/chrome")]
    for c in candidates:
        if c.exists():
            return str(c)
    return None

def cdp_alive(port=CDP_PORT):
    try:
        r = requests.get(f"http://127.0.0.1:{port}/json/version", timeout=2)
        return r.status_code == 200
    except Exception:
        return False

NAMECHEAP_LOGIN_URL = "https://www.namecheap.com/myaccount/login/"

NAMECHEAP_LIST_URL = "https://ap.www.namecheap.com/domains/list/"

SESSION_FILE = REPORTS_DIR / "last-chrome.json"

def write_session(data):
    try:
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        SESSION_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                encoding="utf-8")
    except Exception:
        pass

def open_tab_via_cdp(port, url):
    """Mở 1 tab mới trong Chrome đang chạy (qua cổng debug HTTP)."""
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/json/new?{urllib.parse.urlencode({'url': url})}",
            method="PUT")
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as e:
        log(f"  Mở tab {url} lỗi: {e}")
        return False

def prepare_login_chrome(args, profile, profile_dir_name, chrome_exe, urls=None):
    """Nút 1 — chỉ mở Chrome với các tab đăng nhập rồi DỪNG Ở ĐÓ.
    Không điều khiển trang, không kiểm tra, không reload — user tự
    đăng nhập xong rồi bấm nút 2 để chạy tiếp. Trả về exit code.
    urls: mac dinh [Cloudflare login, Namecheap login]; che do
    auto-renew chi mo [Namecheap Domain List]."""
    if urls is None:
        urls = [CLOUDFLARE_LOGIN_URL, NAMECHEAP_LOGIN_URL]
    names = {"Cloudflare": "Cloudflare", "Namecheap": "Namecheap"}
    if cdp_alive(args.cdp_port):
        log(f"Chrome đã mở sẵn qua cổng debug {args.cdp_port} — mở tab đăng nhập trong đó.")
        for u in urls:
            open_tab_via_cdp(args.cdp_port, u)
        write_session({"cdp_port": args.cdp_port, "profile_copy": None, "mode": "cdp"})
        log(f"Đã mở {len(urls)} tab: " + " + ".join(
            "Cloudflare" if "cloudflare" in u else "Namecheap" for u in urls) + ".")
        log("Đăng nhập xong rồi bấm nút 2 (Chạy tiếp).")
        return 0
    if not chrome_exe or not Path(chrome_exe).exists():
        log("Không tìm thấy chrome.exe — hãy chạy start-chrome.bat trước rồi bấm nút 1 lại.")
        return 2
    profile_copy = get_tool_profile_dir(profile, profile_dir_name, fresh=args.fresh_profile)
    use_dir = profile_copy or profile
    if not profile_copy:
        log("  (Không copy được profile — Chrome sẽ mở bằng profile thật,")
        log("   tab đăng nhập vẫn mở, nhưng lúc chạy có thể phải đăng nhập lại.)")
    cmd = [chrome_exe, f"--user-data-dir={use_dir}",
           f"--remote-debugging-port={args.cdp_port}", "--no-first-run",
           *urls]
    log("Mở Chrome với tab đăng nhập (" + " + ".join(
        "Cloudflare" if "cloudflare" in u else "Namecheap" for u in urls) + ")...")
    kwargs = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(cmd, **kwargs)  # Chrome tách ra chạy riêng, không đóng khi tool thoát
    for _ in range(30):
        if cdp_alive(args.cdp_port):
            break
        time.sleep(1)
    if not cdp_alive(args.cdp_port):
        log("Chrome mở lên nhưng không bật được cổng debug — thử bấm nút 1 lại.")
        return 2
    write_session({"cdp_port": args.cdp_port, "profile_copy": profile_copy, "mode": "copy"})
    log(f"Đã mở Chrome với {len(urls)} tab đăng nhập.")
    log("Đăng nhập xong rồi bấm nút 2 (Chạy tiếp).")
    return 0

def chrome_already_running():
    """Chrome đang mở sẵn thì profile bị khoá: tool mở Chrome mới sẽ chỉ
    hiện 1 cửa sổ about:blank trong Chrome cũ và tool không điều khiển
    được gì. Phát hiện trước để báo rõ, thay vì mở cửa sổ trắng rồi kẹt."""
    try:
        if platform.system() == "Windows":
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq chrome.exe",
                                  "/FO", "CSV"], capture_output=True, text=True,
                                 timeout=10).stdout
            return "chrome.exe" in out.lower()
        import shutil
        if shutil.which("pgrep"):
            r = subprocess.run(["pgrep", "-x", "Google Chrome"], capture_output=True,
                               timeout=10)
            if r.returncode == 0:
                return True
            r = subprocess.run(["pgrep", "-x", "chrome"], capture_output=True,
                               timeout=10)
            return r.returncode == 0
    except Exception:
        pass
    return False

COPY_SKIP_DIRS = {"Cache", "Code Cache", "GPUCache", "Service Worker",
                  "File System", "blob_storage", "Crashpad", "ShaderCache",
                  "DawnCache", "GraphiteDawnCache",
                  "optimization_guide_model_store", "DownloadService"}

COPY_SKIP_FILES = {"SingletonLock", "SingletonCookie", "SingletonSocket", "LOCK"}

def list_chrome_profiles(profile_root):
    """Liệt kê các hồ sơ Chrome trong thư mục User Data.
    Trả về (danh_sách, last_used) với danh_sách = [(thư_mục, tên_hiển_thị)].
    Tên hiển thị lấy từ Local State (profile.info_cache) — chính là tên
    hiện trong menu đổi hồ sơ của Chrome. last_used là hồ sơ dùng gần nhất.
    """
    root = Path(profile_root)
    profiles = []
    last_used = None
    try:
        ls = root / "Local State"
        if ls.exists():
            data = json.loads(ls.read_text(encoding="utf-8"))
            prof = data.get("profile", {}) or {}
            last_used = prof.get("last_used")
            cache = prof.get("info_cache", {}) or {}
            for dirname, info in cache.items():
                if (root / dirname).is_dir():
                    profiles.append((dirname, (info or {}).get("name") or dirname))
    except Exception:
        pass
    if not profiles:
        for d in sorted(root.iterdir()) if root.exists() else []:
            if d.is_dir() and (d.name == "Default" or d.name.startswith("Profile ")):
                profiles.append((d.name, d.name))
    if last_used not in [p[0] for p in profiles]:
        last_used = profiles[0][0] if profiles else None
    return profiles, last_used

def cleanup_stale_copies(max_age_hours=24):
    """Xoá các bản copy profile cũ hơn 1 ngày trong thư mục tạm."""
    try:
        tmp = Path(tempfile.gettempdir())
        now = time.time()
        for d in tmp.glob("domain-chrome-profile-*"):
            try:
                if d.is_dir() and now - d.stat().st_mtime > max_age_hours * 3600:
                    shutil.rmtree(d, ignore_errors=True)
            except Exception:
                pass
    except Exception:
        pass

TOOL_PROFILE_ROOT = BASE_DIR / "chrome-profile-tool"

def tool_profile_dir(profile_name="Default"):
    """Đường dẫn thư mục profile tool của 1 hồ sơ Chrome (vd 'Profile 1')."""
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", profile_name or "Default").strip("._") or "Default"
    return TOOL_PROFILE_ROOT / safe

def get_tool_profile_dir(profile_root, profile_name="Default", fresh=False):
    """Trả về thư mục profile tool để mở Chrome từ đó.
    - Lần đầu (chưa có thư mục): copy từ hồ sơ Chrome thật như trước.
    - Các lần sau: DÙNG LẠI y nguyên, KHÔNG copy đè — copy đè sẽ xoá mất
      cookie đăng nhập trong Chrome của tool, làm user phải đăng nhập lại
      mỗi lần chạy (đúng cái user than phiền).
    - fresh=True: xoá rồi copy lại từ đầu (đăng xuất hết / làm mới)."""
    dest = tool_profile_dir(profile_name)
    if fresh:
        shutil.rmtree(dest, ignore_errors=True)
    if (dest / "Local State").exists() or (dest / "Default").exists():
        log(f"Dùng lại profile tool đã lưu: {dest} (cookie đăng nhập còn nguyên).")
        return str(dest)
    copied = prepare_profile_copy(profile_root, profile_name, dest_dir=str(dest))
    if copied:
        log("  Lần đầu: đã copy hồ sơ Chrome sang profile tool — đăng nhập")
        log("  1 lần trong Chrome của tool, các lần sau mở lên vẫn còn.")
    return copied

def prepare_profile_copy(profile_root, profile_name="Default", dest_dir=None):
    """Copy 1 hồ sơ Chrome thật -> dest_dir (mặc định: thư mục tạm), đặt
    thành Default trong bản copy để Chrome mở lên dùng đúng hồ sơ đó.
    Trả về đường dẫn bản copy, hoặc None nếu không copy được (sẽ dùng
    profile thật như cũ). profile_name là tên thư mục hồ sơ, vd 'Default'
    hay 'Profile 1'."""
    src = Path(profile_root)
    if not src.exists():
        log(f"  Không thấy profile Chrome ở {src}")
        return None
    if dest_dir is None:
        cleanup_stale_copies()
        dst = Path(tempfile.mkdtemp(prefix="domain-chrome-profile-"))
    else:
        dst = Path(dest_dir)
        dst.mkdir(parents=True, exist_ok=True)
    copied = [0]
    skipped = []

    def tolerant_copy(s, d):
        try:
            shutil.copy2(s, d)
            copied[0] += 1
        except Exception as e:
            # File đang bị Chrome khoá/ghi dở (vd Cookies) -> bỏ qua file đó,
            # không crash cả quá trình copy.
            skipped.append(f"{Path(s).name}: {e}")

    # Local State chứa khoá mã hoá cookie — bắt buộc phải có để giữ đăng nhập.
    local_state = src / "Local State"
    chosen_src = src / profile_name
    if not local_state.exists() and not chosen_src.exists():
        log(f"  Profile Chrome ở {src} trống/không có gì để copy — bỏ qua bản copy.")
        shutil.rmtree(dst, ignore_errors=True)
        return None
    if local_state.exists():
        tolerant_copy(str(local_state), str(dst / "Local State"))
    if chosen_src.exists():
        def ignore(d, names):
            return [n for n in names
                    if n in COPY_SKIP_DIRS or n in COPY_SKIP_FILES]
        try:
            shutil.copytree(chosen_src, dst / "Default", ignore=ignore,
                            copy_function=tolerant_copy)
        except Exception as e:
            log(f"  Copy profile lỗi: {e}")
            return None
    # Sửa Local State trong bản copy: bảo Chrome mở Default (chính là hồ sơ
    # vừa copy sang), tránh trường hợp nó mở hồ sơ khác còn ghi trong file.
    try:
        ls_path = dst / "Local State"
        if ls_path.exists():
            data = json.loads(ls_path.read_text(encoding="utf-8"))
            prof = data.setdefault("profile", {})
            info = (prof.get("info_cache", {}) or {}).get(profile_name, {}) or {}
            prof["info_cache"] = {"Default": info if info.get("name") else {"name": profile_name}}
            prof["last_used"] = "Default"
            prof["last_active_profiles"] = ["Default"]
            ls_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    log(f"  Đã copy hồ sơ Chrome '{profile_name}' sang {dst} ({copied[0]} file)"
        + (f", bỏ qua {len(skipped)} file đang khoá" if skipped else "")
        + " — Chrome thật của bạn vẫn mở bình thường, không cần đóng.")
    return str(dst)

def wait_text(page, text, timeout=15000):
    try:
        page.get_by_text(text, exact=False).first.wait_for(state="visible", timeout=timeout)
        return True
    except Exception:
        return False

def shot(page, domain, step):
    try:
        p = REPORTS_DIR / f"shot_{domain}_{step}_{int(time.time())}.png"
        page.screenshot(path=str(p))
        log(f"    đã chụp màn hình: {p.name}")
    except Exception:
        pass

def safe_goto(page, url, what="trang"):
    """Mở trang, timeout rõ ràng + thử lại 1 lần. Nếu vẫn không mở được thì
    báo lỗi dễ hiểu thay vì để tab đứng ở about:blank với traceback dài
    (bản cũ: goto timeout là tool chết luôn, Chrome bỏ lại toàn tab trắng)."""
    for attempt in (1, 2):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            return
        except Exception as e:
            log(f"  Mở {what} ({url}) lần {attempt} lỗi: {str(e)[:150]}")
            if attempt == 2:
                raise RuntimeError(f"không mở được {what} ({url}) — kiểm tra mạng "
                                   "và Chrome có bị kẹt ở trang trắng không")
            page.wait_for_timeout(2000)

def _any_visible(page, text, max_check=20):
    """True nếu CÓ ÍT NHẤT 1 chỗ hiện chữ text đang HIỂN THỊ trên trang.
    Không được chỉ kiểm tra phần tử đầu tiên (.first): trên trang thật,
    phần tử đầu tiên khớp chữ thường nằm trong menu dropdown đang ẨN
    (menu user, menu Domains, menu Actions đứng trước nội dung chính
    trong DOM) nên .first.is_visible() luôn False dù trang đã đăng nhập
    và chữ đó đang hiện rõ ở chỗ khác — đó chính là bug làm tool chờ mãi
    trên trang Domain List đã đăng nhập. is_visible() kiểm tra ngay
    (không chờ), nên duyệt tối đa 20 phần tử vẫn rất nhanh."""
    try:
        for el in page.get_by_text(text).all()[:max_check]:
            try:
                if el.is_visible():
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False

def _show(page):
    """Đưa tab làm việc lên trước để user xem trực tiếp bước đang chạy
    (2 tab làm việc: Namecheap + Cloudflare, đổi tab qua lại)."""
    try:
        page.bring_to_front()
    except Exception:
        pass

def ensure_logged_in(page, site, start_url, logged_in_texts, wait_minutes=5):
    # Không dùng input() nữa: app/web chạy nền không có bàn phím để nhấn Enter.
    # Thay vào đó tự kiểm tra mỗi 5 giây — user đăng nhập xong trong cửa sổ
    # Chrome vừa mở là tool tự chạy tiếp. Quá 5 phút vẫn chưa đăng nhập thì lỗi.
    # QUAN TRỌNG 1: chỉ điều hướng 1 lần duy nhất rồi CHỈ ĐỌC nội dung khi chờ.
    # Bản cũ gọi page.goto() trong mỗi vòng lặp (~7 giây/lần) nên trang cứ
    # reload liên tục, user đang gõ mật khẩu / làm xác minh thì bị reset —
    # không đăng nhập được.
    # QUAN TRỌNG 2: KHÔNG tìm chuỗi "sign in"/"log in" trong toàn bộ HTML nữa.
    # Trang dashboard đã đăng nhập vẫn chứa mấy chuỗi đó ở chỗ ẩn (menu đổi
    # tài khoản, Turnstile, script...) nên bản cũ tưởng chưa đăng nhập và
    # đứng im chờ đủ 5 phút dù user đã đăng nhập xong. Giờ chỉ tin 2 dấu hiệu:
    # URL có phải trang login không, và có thấy dòng chữ chỉ hiện KHI ĐÃ
    # đăng nhập không (Cloudflare: "Account home" ở menu trái; Namecheap:
    # "Domain List" tiêu đề trang / nút "MANAGE" ở từng dòng domain).
    # LƯU Ý: đừng chọn chữ nằm trong menu ẩn — "My Account" của Namecheap
    # chỉ nằm trong menu dropdown của tên user nên trang Domain List đã
    # đăng nhập vẫn không thấy nó, tool cứ chờ mãi dù user đã đăng nhập.
    # Chưa đăng nhập thì trang login sẽ redirect về URL chứa "login"
    # nên vẫn bắt được.
    if isinstance(logged_in_texts, str):
        logged_in_texts = [logged_in_texts]
    deadline = time.time() + wait_minutes * 60
    warned = False
    last_reminder = 0.0
    safe_goto(page, start_url, site)
    page.wait_for_timeout(2000)
    while True:
        url = page.url.lower()
        if any(m in url for m in ("login", "sign-in", "signin")):
            needs_login = True
        else:
            needs_login = not any(_any_visible(page, t) for t in logged_in_texts)
        if not needs_login:
            log(f"  đã đăng nhập {site} (Chrome sẵn).")
            return
        if time.time() > deadline:
            raise RuntimeError(f"chưa đăng nhập {site} sau {wait_minutes} phút — "
                               "hãy đăng nhập trong cửa sổ Chrome rồi chạy lại")
        if not warned:
            # Nói rõ trang đang hiện gì: nhiều khi URL vẫn là /domains/list
            # nhưng Namecheap hiện form đăng nhập ngay tại URL đó khi chưa
            # đăng nhập — user nhìn URL tưởng đã vào được list.
            on_login_form = _any_visible(page, "Log in to your account") or \
                _any_visible(page, "Sign in")
            try:
                title = page.title()
            except Exception:
                title = "(không đọc được tiêu đề)"
            log(f"  !! Chưa đăng nhập {site} ở Chrome này. Đang ở: {page.url}")
            log(f"     Tiêu đề tab tool đang nhìn: {title}")
            if on_login_form:
                log("     (Trang đang hiện form đăng nhập — chưa vào được, hãy nhập tài khoản rồi bấm Sign in.)")
            else:
                log("     (Không thấy form đăng nhập — chụp lại màn hình tab tool đang nhìn để kiểm tra.)")
            shot(page, site, "waiting-login")
            log("     Hãy đăng nhập NGAY TRONG cửa sổ Chrome vừa mở,")
            log("     tool sẽ tự chạy tiếp khi thấy đã đăng nhập (không cần bấm gì).")
            warned = True
            last_reminder = time.time()
        elif time.time() - last_reminder > 30:
            try:
                title = page.title()
            except Exception:
                title = "(không đọc được tiêu đề)"
            log(f"  ... vẫn đang đợi đăng nhập {site} (đang ở: {page.url} | tiêu đề: {title})")
            last_reminder = time.time()
        page.wait_for_timeout(5000)

def _first_visible_now(loc, max_check=20):
    """Trả về phần tử đầu tiên đang HIỂN THỊ trong locator (kiểm tra ngay,
    không chờ). Dùng khi locator khớp nhiều phần tử mà phần tử đầu có thể
    nằm trong menu/panel đang ẨN (vd ô search mobile) — .first sẽ trúng
    phần tử ẩn và chờ mãi không hiện, dù ô thật đang hiện rõ ở chỗ khác."""
    try:
        for el in loc.all()[:max_check]:
            try:
                if el.is_visible():
                    return el
            except Exception:
                continue
    except Exception:
        pass
    return None

def _find_list_search(page, timeout=15000):
    """Tìm ô Search của Domain List (ô cạnh Filters). Trang là SPA, list
    load xong ô mới hiện nên chờ tối đa timeout, mỗi vòng thử nhiều cách
    (kiểm tra ngay, không chờ từng cái):
    1. input có placeholder đúng 'Search' — ô tìm domain ở đầu trang là
       'Search for your next domain' nên không khớp cái này.
    2. placeholder đúng 'Search' (Playwright exact).
    3. textbox có tên 'Search'.
    4. bất kỳ ô nào có placeholder chứa 'search', trừ ô tìm domain ở đầu
       trang ('...next domain') — phòng khi placeholder đổi chữ."""
    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        el = _first_visible_now(page.locator("input[placeholder='Search']"))
        if el:
            return el
        el = _first_visible_now(page.get_by_placeholder("Search", exact=True))
        if el:
            return el
        el = _first_visible_now(page.get_by_role("textbox", name="Search", exact=True))
        if el:
            return el
        try:
            for cand in page.locator("input[placeholder*='search' i]").all():
                try:
                    ph = (cand.get_attribute("placeholder") or "").lower()
                    if "next domain" in ph:
                        continue
                    if cand.is_visible():
                        return cand
                except Exception:
                    continue
        except Exception:
            pass
        page.wait_for_timeout(400)
    return None

def _search_domain_in_list(page, search, domain):
    """Gõ domain vào ô Search của Domain List rồi chờ dòng domain hiện ra.
    Trang lọc live theo từng phím gõ (keyup + debounce) nên fill() một
    lần rồi bấm Enter KHÔNG kích hoạt bộ lọc — đúng như ảnh user gửi:
    ô đã có chữ mà list vẫn hiện tất cả, không lọc. Phải gõ từng ký tự
    như người gõ thật: press_sequentially tạo đủ keydown/input/keyup
    cho từng ký tự nên bộ lọc chạy như gõ tay. Không cần Enter vì gõ
    xong list tự lọc; chỉ bấm Enter dự phòng nếu sau 12s vẫn chưa thấy."""
    search.click()
    try:
        search.fill("")
    except Exception:
        pass
    search.click()
    search.press_sequentially(domain, delay=60)
    # Cho list lọc xong (gõ xong là tự lọc, không cần Enter).
    deadline = time.time() + 12
    while time.time() < deadline:
        if _any_visible(page, domain):
            break
        page.wait_for_timeout(400)
    else:
        # Dự phòng: thử bấm Enter rồi chờ thêm.
        page.keyboard.press("Enter")
        deadline = time.time() + 10
        while time.time() < deadline:
            if _any_visible(page, domain):
                break
            page.wait_for_timeout(400)
        else:
            shot(page, domain, "nc-domain-not-found")
            raise RuntimeError(f"không tìm thấy {domain} trong Domain List của Namecheap "
                               "(đã search mà không ra — kiểm tra domain có ở Namecheap không)")
    # Cho bộ lọc ổn định hẳn (các dòng khác đang ẩn dần) rồi mới đọc
    # toggle — đọc ngay có thể vớ phải toggle của dòng khác còn hiện.
    page.wait_for_timeout(1200)
    if not _any_visible(page, domain):
        shot(page, domain, "nc-domain-not-found")
        raise RuntimeError(f"không tìm thấy {domain} trong Domain List của Namecheap "
                           "(đã search mà không ra — kiểm tra domain có ở Namecheap không)")

def _autorenew_toggle_state(page, domain):
    """Trang thai toggle Auto-Renew cua domain trong Domain List.
    Tra ve (state, detail, cands): state True (dang BAT) / False (dang
    TAT) / None (khong xac dinh duoc); detail mo ta ngan gon phan tu
    da doc (de biet vi sao doc dung/sai); cands la cac ung vien da xem.
    Cach doc (tim DONG chua domain truoc, roi chi tim TRONG dong do —
    khong doc lung tung ca trang):
    1. [role="switch"] co aria-checked (tin cay nhat).
    2. checkbox nam trong container switch/toggle (toggle dang checkbox
       an + label trang tri). Checkbox chon dong o dau dong KHONG nam
       trong container switch/toggle nen khong doc no.
    3. phan tu class switch/toggle co tu on/off/active/checked/enabled/disabled.
    Loi cu (2026-11-20): doc ca trang bang inDomainRow long leo nen
    doc nham phan tu khac roi bao 'da BAT' trong khi toggle dang TAT."""
    try:
        res = page.evaluate("""(domain) => {
          const isVis = e => {
            const r = e.getBoundingClientRect();
            return r.width > 0 && r.height > 0 &&
              getComputedStyle(e).visibility !== 'hidden';
          };
          const desc = el => {
            const tag = el.tagName.toLowerCase();
            const cls = String(el.className || '').slice(0, 90);
            const aria = el.getAttribute('aria-checked');
            const chk = el.type === 'checkbox' ? String(!!el.checked) : '-';
            return tag + '|' + cls + '|aria=' + aria + '|checked=' + chk;
          };
          // Tim DONG chua domain: <tr> hien thi co chu domain; neu
          // layout khong phai bang thi lay to tien gan nhat co class
          // row/domain/item cua phan tu la chua ten domain.
          let row = null;
          const trs = [...document.querySelectorAll('tr')].filter(
            tr => isVis(tr) && tr.textContent.includes(domain));
          if (trs.length) row = trs[0];
          if (!row) {
            const leaf = [...document.querySelectorAll('*')].find(
              e => e.children.length === 0 && e.textContent.trim() === domain && isVis(e));
            if (leaf) {
              let a = leaf.parentElement;
              while (a) {
                if (a.className && /row|domain|item/i.test(String(a.className))) { row = a; break; }
                a = a.parentElement;
              }
            }
          }
          if (!row) return {state: null, detail: 'khong tim thay dong cua ' + domain, cands: []};
          const cands = [];
          // 1. role=switch co aria-checked.
          for (const el of row.querySelectorAll('[role="switch"]')) {
            if (!isVis(el)) continue;
            const aria = el.getAttribute('aria-checked');
            cands.push('switch:' + desc(el));
            if (aria === 'true') return {state: true, detail: 'switch aria-checked=true (' + desc(el) + ')', cands};
            if (aria === 'false') return {state: false, detail: 'switch aria-checked=false (' + desc(el) + ')', cands};
          }
          // 2. checkbox trong container switch/toggle.
          for (const el of row.querySelectorAll('input[type="checkbox"]')) {
            if (!isVis(el)) continue;
            let a = el.parentElement, inSw = false;
            while (a && a !== row) {
              if (/switch|toggle/i.test(String(a.className || ''))) { inSw = true; break; }
              a = a.parentElement;
            }
            cands.push((inSw ? 'toggle-cb:' : 'row-cb:') + desc(el));
            if (inSw) return {state: !!el.checked, detail: 'toggle checkbox checked=' + el.checked + ' (' + desc(el) + ')', cands};
          }
          // 3. class switch/toggle co tu on/off.
          for (const el of row.querySelectorAll('[class*="switch" i], [class*="toggle" i]')) {
            if (!isVis(el)) continue;
            const cls = String(el.className || '').toLowerCase();
            cands.push('cls:' + desc(el));
            if (/\\bon\\b|\\bactive\\b|\\bchecked\\b|\\benabled\\b/.test(cls))
              return {state: true, detail: 'class co on/active/checked/enabled (' + desc(el) + ')', cands};
            if (/\\boff\\b|\\bdisabled\\b/.test(cls))
              return {state: false, detail: 'class co off/disabled (' + desc(el) + ')', cands};
          }
          return {state: null, detail: 'khong xac dinh duoc (da xem ' + cands.length + ' ung vien)', cands};
        }""", domain)
        if isinstance(res, dict):
            return res.get("state"), res.get("detail", ""), res.get("cands") or []
        return None, "ket qua doc toggle khong phai dict", []
    except Exception as e:
        return None, "loi khi doc toggle: " + str(e)[:120], []

def _click_autorenew_toggle(page, domain):
    """Bam toggle Auto-Renew trong dong cua domain (bang JS). Tim dong
    chua domain truoc roi bam switch trong dong do — cung cach tim nhu
    _autorenew_toggle_state de chac la bam dung cai toggle vua doc.
    Tra ve True neu bam duoc."""
    try:
        return bool(page.evaluate("""(domain) => {
          const isVis = e => {
            const r = e.getBoundingClientRect();
            return r.width > 0 && r.height > 0 &&
              getComputedStyle(e).visibility !== 'hidden';
          };
          let row = null;
          const trs = [...document.querySelectorAll('tr')].filter(
            tr => isVis(tr) && tr.textContent.includes(domain));
          if (trs.length) row = trs[0];
          if (!row) {
            const leaf = [...document.querySelectorAll('*')].find(
              e => e.children.length === 0 && e.textContent.trim() === domain && isVis(e));
            if (leaf) {
              let a = leaf.parentElement;
              while (a) {
                if (a.className && /row|domain|item/i.test(String(a.className))) { row = a; break; }
                a = a.parentElement;
              }
            }
          }
          if (!row) return false;
          for (const el of row.querySelectorAll('[role="switch"]')) {
            if (isVis(el)) { el.click(); return true; }
          }
          for (const el of row.querySelectorAll('input[type="checkbox"]')) {
            if (!isVis(el)) continue;
            let a = el.parentElement, inSw = false;
            while (a && a !== row) {
              if (/switch|toggle/i.test(String(a.className || ''))) { inSw = true; break; }
              a = a.parentElement;
            }
            if (inSw) { el.click(); return true; }
          }
          for (const el of row.querySelectorAll('[class*="switch" i], [class*="toggle" i]')) {
            if (isVis(el)) { el.click(); return true; }
          }
          return false;
        }""", domain))
    except Exception:
        return False

def _click_privacy_dialog(page, want_yes):
    """Bam Yes/No trong hop thoai 'Turn On Domain Auto-Renewal' (hoi co
    bat luon auto-renew cho Domain Privacy khong). Tim nut trong khung
    dialog chua tieu de do; du phong tim trong to tien gan nhat chua
    ca tieu de va nut. Tra ve True neu bam duoc."""
    label = "Yes" if want_yes else "No"
    try:
        return bool(page.evaluate("""(label) => {
          const isVis = e => {
            const r = e.getBoundingClientRect();
            return r.width > 0 && r.height > 0 &&
              getComputedStyle(e).visibility !== 'hidden';
          };
          const titleEls = [...document.querySelectorAll('*')].filter(
            e => e.children.length === 0 &&
              e.textContent.includes('Turn On Domain Auto-Renewal') && isVis(e));
          let scope = document;
          if (titleEls.length) {
            let a = titleEls[0];
            while (a) {
              const cls = String(a.className || '');
              if (a.getAttribute('role') === 'dialog' ||
                  /modal|dialog|popup/i.test(cls)) { scope = a; break; }
              a = a.parentElement;
            }
            if (scope === document) {
              let b = titleEls[0].parentElement;
              while (b) {
                if (b.textContent.includes(label)) { scope = b; break; }
                b = b.parentElement;
              }
            }
          }
          const btns = [...scope.querySelectorAll(
            'button, a, [role="button"]')].filter(
            b => isVis(b) && b.textContent.trim() === label);
          if (!btns.length) return false;
          btns[0].click();
          return true;
        }""", label))
    except Exception:
        return False

def _log_toggle_diagnosis(page, domain):
    """Khi khong xac dinh duoc trang thai toggle: ghi HTML that cua
    dong domain vao log de lan sau biet toggle la dang nao."""
    try:
        rows = page.locator("tr", has_text=domain)
        if rows.count() > 0:
            html = rows.first.evaluate("e => e.outerHTML.slice(0, 600)")
            log(f"    [chẩn đoán] dòng {domain}: {html}")
            return
    except Exception:
        pass
    log(f"    [chẩn đoán] không có dòng <tr> chứa {domain}.")

def autorenew_one(page, domain, enabled=True, privacy_yes=False):
    """Bat (hoac tat) auto-renew cho 1 domain trong Domain List.
    Tra ve dict {domain, status, nameservers, records, error} (cung
    dang bao cao CSV nhu setup_one de app doc hien bang).
    status: 'ok' (da doi xong), 'da-bat' (da o dung trang thai — BO
    QUA, khong bam toggle), 'failed' (loi).
    Luu y: toggle la cong tac nen PHAI doc trang thai truoc; da BAT
    roi thi khong bam (bam se TAT di) ma chuyen domain tiep theo."""
    log(f"  Auto-renew {domain}: tim trong Domain List")
    try:
        safe_goto(page, NAMECHEAP_LIST_URL, "Domain List")
        search = _find_list_search(page)
        if not search:
            raise RuntimeError("khong tim thay o Search cua Domain List")
        _search_domain_in_list(page, search, domain)
        cur, detail, cands = _autorenew_toggle_state(page, domain)
        log(f"    doc toggle {domain}: {detail}")
        if cands:
            log(f"    [chẩn đoán] ung vien da xem: {'; '.join(cands[:5])}")
        if cur is None:
            _log_toggle_diagnosis(page, domain)
            raise RuntimeError("khong xac dinh duoc trang thai toggle "
                               "Auto-Renew — khong bam (so tat nham)")
        if cur == enabled:
            log(f"    {domain}: toggle da {'BAT' if enabled else 'TAT'} roi — bo qua, khong bam.")
            return {"domain": domain, "status": "da-bat", "nameservers": "",
                    "records": "", "error": "da o dung trang thai, bo qua"}
        if not _click_autorenew_toggle(page, domain):
            raise RuntimeError("khong tim thay / khong bam duoc toggle Auto-Renew")
        if enabled:
            # Hop thoai hoi co bat luon cho Domain Privacy khong.
            # Co domain khong co Privacy thi khong hien hop thoai —
            # khong thay thi bo qua, kiem tra lai toggle ben duoi.
            if wait_text(page, "Turn On Domain Auto-Renewal", timeout=15000):
                if not _click_privacy_dialog(page, privacy_yes):
                    raise RuntimeError("khong bam duoc nut Yes/No trong "
                                       "hop thoai Domain Privacy")
        deadline = time.time() + 15
        while time.time() < deadline:
            cur2, detail2, _c = _autorenew_toggle_state(page, domain)
            if cur2 == enabled:
                log(f"    {domain}: da {'BAT' if enabled else 'TAT'} auto-renew.")
                return {"domain": domain, "status": "ok", "nameservers": "",
                        "records": "", "error": ""}
            time.sleep(1)
        raise RuntimeError("bam toggle roi nhung kiem tra lai toggle van chua doi "
                           f"(lan cuoi doc: {detail2})")
    except Exception as e:
        log(f"    {domain}: LOI auto-renew — {e}")
        return {"domain": domain, "status": "failed", "nameservers": "",
                "records": "", "error": str(e)}

def main():
    ap = argparse.ArgumentParser(description="Bat/tat Auto-Renew tren Namecheap bang Chrome (bam toggle that, khong dung API)")
    ap.add_argument("--domain")
    ap.add_argument("--domains-file")
    ap.add_argument("--cdp-port", type=int, default=CDP_PORT)
    ap.add_argument("--profile", help="Duong dan profile Chrome (mac dinh tu tim)")
    ap.add_argument("--profile-dir", help="Ho so Chrome can dung, vd 'Default' hay 'Profile 1' (mac dinh: ho so dung gan nhat)")
    ap.add_argument("--chrome-path", help="Duong dan chrome.exe")
    ap.add_argument("--keep-open", action="store_true", help="Chay xong khong dong Chrome")
    ap.add_argument("--fresh-profile", action="store_true",
                    help="Xoa profile tool roi copy lai tu dau (dang xuat het / lam moi)")
    ap.add_argument("--reset-profile", action="store_true",
                    help="Chi xoa profile tool da luu (dang xuat het) roi thoat, khong chay gi")
    ap.add_argument("--prepare-nc", action="store_true",
                    help="Nut 1 (tab Auto-renew): chi mo tab Namecheap Domain List de dang nhap truoc")
    ap.add_argument("--autorenew", choices=["on", "off"],
                    help="Tab Auto-renew: bat/tat auto-renew cho cac domain trong Domain List (bam toggle that, khong dung API)")
    ap.add_argument("--privacy", choices=["yes", "no"], default="no",
                    help="Khi hop thoai hoi co bat luon auto-renew cho Domain Privacy khong: yes = bam Yes, no = bam No (mac dinh no)")
    args = ap.parse_args()

    profile = args.profile or str(default_profile_dir())
    chrome_exe = args.chrome_path or default_chrome_exe()
    # Chon ho so Chrome: user chi dinh > ho so dung gan nhat > Default.
    profiles, last_used = list_chrome_profiles(profile)
    if args.profile_dir:
        profile_dir_name = args.profile_dir
    elif last_used:
        profile_dir_name = last_used
    else:
        profile_dir_name = "Default"
    log(f"Auto-renew tool — ban {tool_version()} (kiem tra ma nay khop voi ban moi nhat tren git chua)")
    if args.reset_profile:
        dest = tool_profile_dir(profile_dir_name)
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
            log(f"Da xoa profile tool: {dest} (dang xuat het).")
            log("Chay lai lan sau tool se copy moi tu ho so Chrome that.")
        else:
            log(f"Chua co profile tool nao de xoa ({dest}).")
        sys.exit(0)
    if args.prepare_nc:
        sys.exit(prepare_login_chrome(args, profile, profile_dir_name, chrome_exe,
                                      urls=[NAMECHEAP_LIST_URL]))

    domains = []
    if args.domain:
        domains = [args.domain.strip().lower()]
    elif args.domains_file:
        domains = [l.strip().lower() for l in Path(args.domains_file).read_text(encoding="utf-8").splitlines()
                   if l.strip() and not l.strip().startswith("#")]
    if not domains:
        ap.error("can --domain hoac --domains-file")
    if sync_playwright is None:
        print("Thieu playwright. Chay:  pip install playwright  &&  playwright install chromium")
        sys.exit(1)

    display = dict(profiles).get(profile_dir_name, profile_dir_name)
    log(f"Profile Chrome: {profile}")
    if profiles:
        log(f"Ho so dung: {display} ({profile_dir_name}) — co {len(profiles)} ho so: "
            + ", ".join(f"{n} ({d})" for d, n in profiles))
    log(f"Chrome: {chrome_exe or '(dung channel chrome cua Playwright)'}")
    log(f"Domain ({len(domains)}): {', '.join(domains)} | auto-renew {args.autorenew}")

    results = []
    profile_copy = None
    with sync_playwright() as p:
        browser = None
        context = None
        cdp_mode = False
        if cdp_alive(args.cdp_port):
            log(f"Noi vao Chrome dang mo qua CDP port {args.cdp_port}...")
            browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{args.cdp_port}")
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            cdp_mode = True
        else:
            if chrome_already_running():
                log("Chrome cua ban dang mo san — khong sao, tool dung ban copy")
                log("profile nen khong can dong Chrome.")
            profile_copy = get_tool_profile_dir(profile, profile_dir_name, fresh=args.fresh_profile)
            use_dir = profile_copy or profile
            if profile_copy:
                log("Chrome chua bat cong debug — tool se mo Chrome tu profile tool da luu.")
            else:
                log("Khong copy duoc profile — thu mo Chrome bang profile that.")
                log("(Neu bao 'profile dang duoc dung', hay dong het Chrome roi chay lai.)")
            try:
                kwargs = dict(user_data_dir=use_dir, headless=False,
                              chromium_sandbox=True,
                              ignore_default_args=["--enable-automation"],
                              args=["--no-first-run"],
                              viewport={"width": 1280, "height": 900},
                              ignore_https_errors=True)
                if chrome_exe:
                    kwargs["executable_path"] = chrome_exe
                else:
                    kwargs["channel"] = "chrome"
                context = p.chromium.launch_persistent_context(**kwargs)
            except Exception as e:
                log(f"LOI mo Chrome: {e}")
                if "already in use" in str(e) or "dang duoc dung" in str(e):
                    log("Profile tool dang bi 1 cua so Chrome cua tool giu (lan chay")
                    log("truoc chua dong) — dong cua so Chrome do roi chay lai.")
                    log("Neu van ket: --reset-profile de xoa profile tool roi chay lai.")
                else:
                    log("Cach sua: chay start-chrome.bat/.sh truoc, roi chay tool.")
                sys.exit(2)
        for _ in range(20):
            if context.pages:
                break
            time.sleep(0.5)
        log("Chrome dang mo %d tab: %s" % (len(context.pages),
            ", ".join(pg.url[:60] for pg in context.pages[:5]) or "(chua co tab nao)"))
        if context.pages and context.pages[0].url == "about:blank":
            nc_page = context.pages[0]
        else:
            nc_page = context.new_page()
        try:
            log("Da mo tab Namecheap (che do auto-renew — chi can Namecheap).")
            log("Da noi vao Chrome, dang kiem tra dang nhap Namecheap...")
            _show(nc_page)
            ensure_logged_in(nc_page, "Namecheap", NAMECHEAP_LIST_URL,
                             ["Domain List", "MANAGE"])
            enabled = args.autorenew == "on"
            privacy_yes = args.privacy == "yes"
            for d in domains:
                log(f"=== {d} (auto-renew {'BAT' if enabled else 'TAT'}) ===")
                results.append(autorenew_one(nc_page, d, enabled, privacy_yes))
        finally:
            if not args.keep_open and not cdp_mode:
                try:
                    context.close()
                except Exception:
                    pass
            elif cdp_mode:
                try:
                    browser.close()  # chi ngat ket noi, khong dong Chrome cua ban
                except Exception:
                    pass

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = REPORTS_DIR / f"chrome_setup_{ts}.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["domain", "status", "nameservers", "records", "error"])
        w.writeheader()
        w.writerows(results)
    ok = sum(1 for r in results if r["status"] not in ("failed",))
    log(f"\nXong: {ok}/{len(results)} domain OK. Bao cao: {out}")
    for r in results:
        log(f" - {r['domain']}: {r['status']} {r.get('error','')}")


if __name__ == "__main__":
    main()
