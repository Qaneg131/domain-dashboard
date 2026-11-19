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
- **Bảng quản lý** — hiện hết domain trong tài khoản, khỏi bấm Thêm:
  mở trang là tool tự đưa hết zone vào bảng quản lý (domain nào bạn đã
  xóa khỏi bảng thì không đưa lại). Có thanh tìm kiếm, phân trang
  5 domain/trang; mở trang nào tool tự kiểm tra trạng thái 5 domain
  trang đó (khỏi bấm "Kiểm tra tất cả"). Mỗi dòng hiện đủ trạng thái
  như trên Cloudflare, badge Yes/No các cột luôn cùng 1 hàng, thông tin
  phụ (NS, số record, số rule, SSL/TLS…) xuống hàng dưới. Mỗi dòng có
  nút **GSC** riêng — bấm là mở popup Google Search Console đúng domain
  đó để dán mã xác minh. Trang hiển thị full width. Tên domain xếp
  **A–Z mặc định** (bấm tiêu đề Name để đổi Z–A); mỗi cột DNS / Record /
  Email routing / HTTPS / Cache có **bộ lọc riêng** (dropdown dưới tiêu
  đề cột) — lọc theo Đã set / Khớp preset / Enabled / Yes / By Pass,
  Chưa set, hoặc Chưa kiểm tra. Domain chưa kiểm tra chi tiết vẫn hiện
  DNS (zone active/pending + NS) lấy từ danh sách tài khoản nên thấy
  ngay khi mở trang.
- **Kiểm tra tất cả (theo loat 30 cái)** — nút này không quet 1 lần
  410 domain nữa (Cloudflare giới hạn ~1200 request/5 phút nên quet
  1 lần là dính 429). Nó kiểm tra **30 domain/lần, xong loat thì tự
  động tiếp loat sau** (nghỉ 5s giữa các loat), có thanh tiến độ và nút
  **Dừng**. Chỉ kiểm tra domain **chưa có trạng thái** — domain đã
  kiểm tra thì bỏ qua (trạng thái lưu sẵn trên server nên mở lại trang
  hay bấm lại nút đều tiếp tục từ chỗ dừng, vd đã có 60/410 thì kiểm
  tra từ 61, không quét lại từ đầu). Dính 429 thì dừng ngay, đợi ~5
  phút rồi bấm lại để **tiếp tục từ chỗ dừng**. Domain nào cần gấp thì
  tự kiểm tra ưu tiên: tick ô vuông rồi bấm "Kiểm tra đã chọn" (bảng
  quản lý) hoặc "Kiểm tra trang này" (tab mail). Tốc độ mỗi domain đã
  ở mức an toàn (5 call Cloudflare/domain, ~1.8s/domain — nhanh hơn
  nữa sẽ vượt giới hạn 1200 request/5 phút của Cloudflare nên không
  song song hóa thêm). Mỗi lần kiểm tra 1 domain giờ chỉ còn ~5 call
  Cloudflare (HTTPS đọc 1 lần qua endpoint /settings bulk thay vì
  4 lần; Namecheap chỉ gọi khi DoH chưa xác nhận NS) nên ít dính 429
  hơn.
- **Domain bị lỗi** — domain nào kiểm tra lỗi (vd Namecheap báo "Too
  many requests") thì **không tính là đã xong**: "Kiểm tra tất cả" sẽ
  kiểm tra lại nó ở lần sau, thanh công cụ hiện số domain bị lỗi
  (vd "⚠️ 87 domain bị lỗi") kèm nút **Kiểm tra lại domain bị lỗi**
  (chỉ chạy những domain lỗi, theo loat 30), và bộ lọc DNS có thêm
  mục **Lỗi**. Kiểm tra lại mà lỗi thì **giữ nguyên trạng thái tốt cũ**
  — lỗi thoáng qua (429/throttle) không xóa dữ liệu đã có.
- **Namecheap giới hạn tốc độ (Too many requests)** — Namecheap API
  giới hạn khá chặt: gọi dồn nhiều domain cùng lúc (nhất là chạy
  Setup nhiều domain, hoặc chạy Kiểm tra + Setup cùng lúc) thì bước
  "Đổi NS ở Namecheap" sẽ lỗi "Too many requests". Tool tự xử lý:
  mỗi lần gọi Namecheap mà dính giới hạn thì **tự đợi rồi thử lại
  2 lần** (30s rồi 60s); vẫn lỗi thì báo rõ "Namecheap giới hạn tốc
  độ — đợi ~2 phút rồi thử lại" (ô chẩn đoán hiện lỗi đỏ, cách sửa
  xanh). Loat Setup (theo danh sách / đã chọn) **tự dừng** khi gặp
  lỗi này để các domain sau không lỗi theo — đợi ~2 phút rồi chạy
  lại chỉ những domain chưa xong. Mẹo: đừng chạy Kiểm tra tất cả +
  Setup cùng lúc.
- **Cửa sổ thông báo sau khi lệnh xong** — mỗi lệnh chạy xong (Setup,
  Setup danh sách, Áp record, Áp HTTPS, Bypass cache, Xóa zone,
  Kiểm tra, Setup email, Thêm domain) đều hiện cửa sổ thông báo tóm
  tắt: xong bao nhiêu domain, bỏ qua bao nhiêu (đã set đủ), lỗi bao
  nhiêu — kèm tên domain (vd "Setup email xong 3 domain: a.com,
  b.com, c.com"). Bấm "Đóng" hoặc bấm ra ngoài để tắt.
- **Thanh điều hướng dọc** — bên trái có 2 mục: **Setup domain** (cài đặt,
  preset record, HTTPS preset, setup theo danh sách, bảng quản lý) và
  **Setup email** (email preset, setup email theo danh sách, bảng email).
  Log chạy chung ở dưới cùng, lúc nào cũng thấy.
- **Setup email theo danh sách** — ở mục Setup email có ô nhập nhiều
  domain (mỗi dòng 1 domain), bấm Setup email: tool kiểm tra từng
  domain, domain nào đã set đủ (Email Routing đã bật + rule đã có theo
  preset) thì **bỏ qua**, chưa set thì setup; zone pending thì bỏ qua
  luôn (không đợi 5 phút như nút setup lẻ). Có nút thêm các domain đó
  vào bảng quản lý.
- **Tab Setup email** — hiện hết domain đang quản lý (không chỉ
  active), mỗi dòng có cột **DNS** (zone active/pending + NS, lấy từ
  danh sách tài khoản nên thấy ngay), Routing status, DNS records
  (Locked/Unlocked) và nút Setup email (zone pending thì khóa nút).
  Phân trang **10 domain/trang**, tick checkbox để chọn nhiều domain
  rồi bấm **"Setup email đã chọn"** setup 1 lần. Mỗi cột DNS / Routing
  status / DNS records có **bộ lọc riêng**. Không còn nút Setup
  email tất cả.
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
