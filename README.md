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
| Zone — Cache Rules — Edit | tạo rule By Pass (Cache Rules) |
| Account — Rulesets — Edit | bắt buộc kèm theo Cache Rules (Rulesets API) |
| Account — Filter Lists — Edit | bắt buộc kèm theo Cache Rules |
| Zone — Zone — Edit | xóa zone (nút "Xóa zone đã chọn") |

Lưu ý: chỉ tick "Cache Rules" thì cột Cache vẫn báo "cần quyền token" —
phải thêm cả **Account → Rulesets → Edit** và **Account → Filter Lists →
Edit**. Sau khi sửa quyền token thì bấm "Kiểm tra tất cả" để bảng cập
nhật lại (trạng thái cũ không tự đổi).

Account ID lấy ở URL dashboard (`dash.cloudflare.com/<account-id>`) — đã điền
sẵn nếu bạn dùng tài khoản cũ, sửa lại nếu khác.

### 2. Namecheap API (để đổi NS không cần trình duyệt)
Cần bật API access trong tài khoản Namecheap (Profile → Tools) và
**whitelist IP máy chạy tool** (ClientIp). IP đổi (mạng động, bật
Cloudflare WARP/VPN) thì phải whitelist lại — bấm nút "Lấy IP hiện tại"
ở mục Cài đặt để xem IP hiện tại. Nếu Setup báo lỗi 1011150 "Invalid
request IP: ..." thì IP đó chưa được whitelist — thêm đúng IP đó vào
Namecheap Profile → Tools → API Access → Whitelisted IPs, đồng thời sửa
ô Client IP trong Cài đặt thành IP đó. Sau mỗi lần Setup lỗi, tool hiện
ô chẩn đoán ở mục "Kết quả chạy": dòng đỏ là lỗi, dòng xanh là cách sửa.

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
- **Áp preset kiểu gộp (merge)** — Setup / Áp record KHÔNG xóa hết record cũ
  nữa: record giống hệt preset thì giữ, record trùng tên/loại nhưng khác
  nội dung thì xóa cái cũ rồi thêm mới, record khác (không nằm trong
  preset) giữ nguyên. Setup mà chưa chọn preset thì báo lỗi, không xóa gì.
- **Tab "Setup email"** — là tab mail riêng: chứa mục Email preset (rule
  forward) + bảng chỉ hiện domain có zone đã **active**. Mỗi dòng hiện đủ
  2 trạng thái: **Routing status** (Enabled/Disabled + số rule) và **DNS
  records** (Locked khi record MX/SPF/DKIM đang bị khóa). Mỗi dòng có nút
  "Setup email" (chỉ chạy bước Email Routing) và nút "Setup email tất cả
  (active)". Đồng bộ rule: rule giống hệt thì giữ, rule trùng y hệt thì
  xóa bớt, rule cùng địa chỉ nhưng khác đích thì thay bằng rule preset,
  rule của địa chỉ khác giữ nguyên.
- **Bảng quản lý** — bỏ tab "Tất cả trong tài khoản", hiện hết domain
  (đang quản lý + trong tài khoản) ngay ngoài trang quản lý, có thanh
  tìm kiếm lọc cả 2 loại, phân trang 5 domain/trang. Domain chưa trong
  bảng có nút "Thêm" (thêm xong tự kiểm tra trạng thái ngay).
- **Thêm từ tab "Tất cả trong tài khoản"** — bấm "Thêm" xong tool tự kiểm
  tra trạng thái domain đó ngay, khỏi bấm "Kiểm tra tất cả".
  → **xóa hết record cũ** rồi áp preset record → bật Email Routing + tạo
  rule theo email preset → set HTTPS theo https preset (mặc định: SSL
  Flexible + Always Use HTTPS bật + Minimum TLS 1.2 + Auto HTTPS Rewrites
  bật). Domain nào NS đã set rồi thì tự bỏ qua bước đó. Email Routing chỉ
  bật được khi zone đã **active** — zone mới đổi NS xong Cloudflare cần
  vài phút để kích hoạt, nên Setup tự đợi tối đa 5 phút trước khi báo
  lỗi; nếu vẫn chưa active thì chạy Setup lại sau vài phút (các bước đã
  xong sẽ tự bỏ qua). **Lưu ý:** Setup
  luôn xóa record cũ (chỉ giữ lại record hệ thống của Email Routing) nên
  phải kiểm tra preset mặc định có đúng IP/server của bạn không trước khi
  bấm — nếu chưa chọn preset mặc định, Setup vẫn xóa record cũ rồi báo
  rõ trong log để bạn chọn preset rồi Setup lại. Nếu domain còn MX của
  nhà cung cấp mail cũ (Google/Zoho/...), Setup tự xóa MX đó rồi mới bật
  Email Routing (lỗi 2008 của Cloudflare).
- **Setup theo danh sách** — dán nhiều domain (mỗi dòng 1 domain), bấm
  Setup là chạy từng domain một với đủ các bước trên. Chỉ xử lý đúng các
  domain trong ô này, không động đến domain khác.
- **Chọn nhiều domain** — tick ô vuông ở đầu mỗi dòng trong bảng quản lý
  rồi bấm "Setup đã chọn" / "Áp record đã chọn" / "Áp HTTPS đã chọn" (chọn
  SSL Flexible/Full/Full strict ở ô SSL bên cạnh) / "Bypass cache đã chọn"
  (tạo rule "By Pass" cho mọi request, như trong dashboard Cloudflare) /
  "Kiểm tra đã chọn". "Xóa zone đã chọn" sẽ XÓA ZONE trên Cloudflare
  (không khôi phục được) — hỏi xác nhận 2 lần.
- **Tab "Tất cả trong tài khoản"** — liệt kê hết zone trong tài khoản
  Cloudflare, có ô tìm kiếm, bấm "Thêm" để đưa domain vào bảng quản lý.
- **Edit (popup)** — bấm Edit ở từng domain để chỉnh 4 mục:
  - **DNS**: xem NS (DNS công / Namecheap / Cloudflare), nút "Đồng bộ NS ngay".
  - **Record**: áp preset hoặc sửa tay từng record rồi Lưu.
  - **Email routing**: bật/tắt, thêm rule forward (`info@domain` hoặc `*`
    catch-all → mail thật), gửi mail xác minh địa chỉ nhận. Lưu ý: bật
    Email Routing sẽ thêm/khóa 3 MX + SPF + DKIM của Cloudflare (ghi đè
    mail cũ); địa chỉ nhận phải bấm link xác minh trong mail thì rule mới
    chạy.
  - **Cache**: bật/tắt rule "By Pass" (bypass cache cho mọi request).
  - **HTTPS**: SSL mode (Off/Flexible/Full/Full strict), Always Use HTTPS,
    Minimum TLS Version, Auto HTTPS Rewrites. Mặc định khi Setup:
    Flexible + Always Use HTTPS bật + TLS 1.2.
- **Preset record** — lưu cấu hình record để áp nhanh, vd preset `web`:
  `A @ → 1.11.11.11`, `A www → 1.11.11.11`. Áp preset sẽ xóa hết record
  cũ rồi thêm mới (nhưng giữ lại record hệ thống của Email Routing:
  3 MX + SPF + DKIM).
- **Email preset** — rule forward áp trong lúc Setup, mặc định 2 rule:
  `info@` và `*` (catch-all) → địa chỉ nhận bạn nhập. Địa chỉ nhận phải
  bấm link xác minh trong mail thì rule mới chạy. Catch-all là rule đặc
  biệt của Cloudflare (endpoint riêng `.../rules/catch_all`, chỉ cho 1
  địa chỉ nhận) — tool tự xử lý, không tạo qua endpoint rule thường.
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
