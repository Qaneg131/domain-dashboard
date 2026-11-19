# Domain Dashboard — Namecheap + Cloudflare (API, không cần Chrome)

Giao diện web chạy local để quản lý domain: xem domain nào đã set NS chưa,
đã có record chưa, Email Routing + HTTPS ra sao — rồi bấm **Setup** là tự
chạy hết bằng API, không cần mở trình duyệt.

## Chạy

Windows: double-click `run.bat` (mở http://127.0.0.1:8787).
Linux/Mac: `python3 app.py`.

Không cần cài thêm gì (chỉ dùng thư viện chuẩn của Python 3.8+).

## Cần chuẩn bị (nhập 1 lần ở mục Cài đặt trong giao diện)

### 1. Cloudflare API token

Cloudflare Dashboard → My Profile → API Tokens → Create Token → Custom token,
quyền:

| Quyền | Để làm gì |
|---|---|
| Zone — Zone — Read | đọc zone |
| Zone — Zone — Edit | tạo zone |
| Zone — DNS — Edit | đọc/xóa/thêm record |
| Zone — Zone Settings — Edit | đọc/bật Email Routing, SSL/HTTPS |
| Zone — Email Routing Rules — Edit | tạo/xóa rule forward mail |
| Account — Email Routing Addresses — Edit | thêm địa chỉ nhận mail |

Account ID lấy ở URL dashboard (`dash.cloudflare.com/<account-id>`) — đã điền
sẵn nếu bạn dùng tài khoản cũ, sửa lại nếu khác.

### 2. Namecheap API (để đổi NS không cần trình duyệt)

- Namecheap → Profile → Tools → API Access: bật lên, lấy **API key**.
- **Whitelist IP** máy chạy tool (nút "Lấy IP hiện tại" trong giao diện sẽ
  hiện IP của bạn — điền IP đó vào whitelist).
- Lưu ý: IP nhà bạn đổi (mạng động) thì API sẽ báo lỗi — whitelist lại IP mới.

Nếu chưa cấu hình Namecheap API, tool vẫn làm được mọi thứ phía Cloudflare
(zone, record, email, HTTPS) và vẫn check NS bằng DNS thật; riêng bước đổi
NS sẽ báo "chưa cấu hình Namecheap API".

## Tính năng

- **Bảng tổng quan** — mỗi domain 1 dòng: DNS (NS đã trỏ đúng chưa, check
  bằng DNS thật), Record (có record không, có khớp preset không),
  Email routing (bật chưa, mấy rule), HTTPS (SSL mode + Always Use HTTPS).
- **Setup** — 1 click: tạo/lấy zone Cloudflare → đổi NS ở Namecheap (API)
  → áp preset record. Domain nào NS đã set rồi thì tự bỏ qua bước đó.
- **Edit (popup)** — bấm Edit ở từng domain để chỉnh 4 mục:
  - **DNS**: xem NS (DNS công / Namecheap / Cloudflare), nút "Đồng bộ NS ngay".
  - **Record**: áp preset hoặc sửa tay từng record rồi Lưu.
  - **Email routing**: bật/tắt, thêm rule forward (`info@domain` hoặc `*`
    catch-all → mail thật), gửi mail xác minh địa chỉ nhận. Lưu ý: bật
    Email Routing sẽ thêm/khóa 3 MX + SPF + DKIM của Cloudflare (ghi đè
    mail cũ); địa chỉ nhận phải bấm link xác minh trong mail thì rule mới
    chạy.
  - **HTTPS**: SSL mode (Off/Flexible/Full/Full strict), Always Use HTTPS,
    Auto HTTPS Rewrites. Khuyên dùng Full + Always Use HTTPS bật.
- **Preset record** — lưu cấu hình record để áp nhanh, vd preset `web`:
  `A @ → 1.11.11.11`, `A www → 1.11.11.11`. Áp preset sẽ xóa hết record
  cũ rồi thêm mới.
- **Thêm/xóa domain**, **Kiểm tra tất cả**, **Setup tất cả**.

## File cấu hình

`config.json` (token/key, domain, preset) và `status.json` (kết quả check
gần nhất) chỉ nằm trên máy bạn — đã có trong `.gitignore`, không commit
lên git.

## Test (không cần key thật)

```bash
python3 tests/test_api.py   # API clients + setup flow với mock server
python3 tests/test_e2e.py   # chạy server thật, gọi các endpoint
```
