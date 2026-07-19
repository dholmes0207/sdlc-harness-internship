# Báo cáo insight bước đầu khi sử dụng SDLC Harness

> Trạng thái: Báo cáo tạm thời. Sau khi triển khai dùng SDLC Harness, do chưa quen cách > thiết lập và sử dụng nên đốt token khá nhanh, quy trình hiện đã hoàn thành đến Phase 2
> của bước cook. Em sẽ cập nhật kết quả đầy đủ sau khi hoàn thành các bước
> implementation, test, review và ship.

## 1. Bối cảnh thực hiện

Trong bài thực tập này, em sử dụng SDLC Harness v5.3.0 kết hợp với Claude
Code để phát triển một demo Product Search API.
Products hiện tại em tận dụng luôn của Dienmayxanh, một bài tập mà nhóm em đã thực hiện khi tham gia cuộc thi Vietnam AI Innovation, dữ liệu được crawl từ web trong quá trình làm đề thi.

Demo sử dụng:

- Python và FastAPI.
- File `data/products.json` gồm 20 sản phẩm điện máy.
- Tìm kiếm sản phẩm bằng tiếng Việt có dấu hoặc không dấu.
- Lọc theo ngành hàng, thương hiệu và khoảng giá.
- Unit test và API integration test.
- Dockerfile và tài liệu hướng dẫn chạy.

Mục tiêu chính của em không chỉ là hoàn thành API, mà còn là trải nghiệm
đầy đủ quy trình của Harness từ discover, plan, cook, test, review đến ship.

## 2. Cảm nhận ban đầu

Quan điểm ban đầu của em là Harness tạo ra một quy trình kiểm soát AI rất
chặt chẽ. AI không được viết code ngay sau khi nhận yêu cầu, mà phải khảo
sát repository, kiểm tra dữ liệu, xác định phạm vi, lập kế hoạch, đánh giá
rủi ro và chờ con người phê duyệt.

Điều này khác khá nhiều với cách em thường sử dụng AI trước đây. Thông
thường, em mô tả yêu cầu rồi để AI trực tiếp viết hoặc sửa code. Với
Harness, mỗi quyết định quan trọng đều cần có bằng chứng, tiêu chí kiểm tra
và artifact để truy vết.

## 3. Những điểm em đánh giá cao

### 3.1. Harness không hoàn toàn tin vào mô tả ban đầu

Trong bước discovery, Harness đã trực tiếp đọc và kiểm tra
`data/products.json` thay vì chỉ dựa trên mô tả của em.

Quá trình này phát hiện được các vấn đề thực tế như:

- File JSON là một object chứa trường `products`, không phải một list trực
  tiếp.
- Unicode NFD không tự chuyển ký tự `đ` thành `d`.
- Tìm kiếm bằng substring khiến cụm “máy in” khớp nhầm với từ “Inverter”.
- Dữ liệu không đủ để lọc theo trạng thái còn hàng hoặc hiển thị hình ảnh.

Theo em, đây là một điểm tốt vì nó giảm khả năng AI xây dựng giải pháp trên
những giả định sai.

### 3.2. Harness chú trọng các lỗi trả về kết quả có vẻ đúng

Một insight quan trọng em nhận được là HTTP status thành công không đồng
nghĩa API hoạt động đúng.

Harness đã phân tích một số trường hợp API vẫn có thể trả HTTP 200 nhưng
nội dung sai, ví dụ:

- `/products/search` bị route `/{product_id}` bắt nhầm.
- Lifespan không chạy khiến API trả danh sách rỗng.
- Query chỉ gồm khoảng trắng vượt qua validation.
- Filter category rỗng bị hiểu như không có filter.
- Response model được khai báo nhưng không gắn vào endpoint.

Do đó, các bài test không chỉ kiểm tra status code mà còn phải kiểm tra
nội dung response.

### 3.3. Quyết định được ghi lại và có thể truy vết

Harness tạo ra nhiều artifact như:

- Discovery brief.
- Architecture decision.
- System architecture.
- Code standards.
- Plan và các phase.
- Plan approval.
- Verification artifact.
- Commit theo từng phase.

Theo em, cách này hữu ích trong môi trường nhóm vì người review có thể biết
AI đã chọn giải pháp nào, tại sao chọn và đã kiểm tra bằng cách nào.

### 3.4. Harness vẫn yêu cầu con người kiểm soát

Mặc dù có những lợi điểm trên, nhưng trong vòng review plan, em đã phát hiện hai lỗi mà cả planner và red-team hiện tại đều bỏ sót:

- `README.md` đã tồn tại nhưng plan lại coi là file cần tạo mới.
- Lệnh `docker run` chạy foreground sẽ làm quy trình bị treo trước khi chạy
  được các lệnh `curl`.

Sau khi em phản hồi, Harness sửa lại plan, chạy lại validation rồi mới cho
phép phê duyệt.

Điều này cho em thấy Harness hỗ trợ review rất tốt, nhưng không thay thế
hoàn toàn trách nhiệm của người phát triển.

## 4. Khó khăn và hạn chế

### 4.1. Quy trình khá nặng đối với một demo nhỏ

Demo chỉ sử dụng một file JSON gồm 20 sản phẩm, nhưng plan cuối cùng có hơn
2.300 dòng và được chia thành 6 phase.

Thời gian dành cho discover, probe, plan, red-team và sửa plan lớn hơn
nhiều so với thời gian em dự kiến cần để tự viết API.

Theo quan điểm của em, Harness phù hợp hơn với:

- Feature có rủi ro cao.
- Dự án có nhiều thành viên.
- Dự án cần audit và truy vết quyết định.
- Hệ thống mà lỗi AI có thể gây hậu quả lớn.

Đối với một demo nhỏ, governance overhead tương đối lớn so với lượng code
cần triển khai.

### 4.2. Plan đã qua red-team vẫn có thể chứa guard không hiệu quả

Trong Phase 1, một test được thiết kế để bảo vệ cấu hình `pythonpath`.
Tuy nhiên, test ban đầu luôn pass vì `python -m pytest` tự thêm thư mục hiện
tại vào `sys.path`.

Điều này tạo ra một “guard giả”: test có vẻ hợp lệ nhưng không thực sự phát
hiện được khi cấu hình bị xóa.

Cook đã dừng lại, yêu cầu quyết định của người dùng và viết lại test. Test
mới được kiểm tra ở ba trạng thái:

1. Có cấu hình: test pass.
2. Xóa cấu hình: test fail.
3. Khôi phục cấu hình: test pass.

Theo em, đây là insight kỹ thuật quan trọng nhất ở thời điểm hiện tại:
test không chỉ cần chạy xanh, mà còn phải chứng minh được rằng nó sẽ đỏ khi
hành vi cần bảo vệ bị phá vỡ.

### 4.3. Tốn thời gian và giới hạn sử dụng model

Việc sử dụng nhiều agent, chạy probe, red-team và xác minh độc lập tiêu tốn
khá nhiều token và thời gian.

Phiên Claude Code của em đã chạm giới hạn sử dụng khi bắt đầu Phase 3. Tuy
nhiên, nhờ việc Harness commit và ghi verification artifact theo từng
phase, kết quả của Phase 1 và Phase 2 không bị mất.

## 5. Tiến độ hiện tại

Tại thời điểm viết báo cáo này:

- Cài đặt và kiểm tra Harness: hoàn thành.
- Discovery: hoàn thành.
- Plan: hoàn thành.
- Red-team plan: hoàn thành.
- Human review và plan approval: hoàn thành.
- Phase 1 — Project setup: hoàn thành và đã commit.
- Phase 2 — Vietnamese accent folding: hoàn thành và đã commit.
- Phase 3 — Loader và models: chưa hoàn thành do phiên Claude Code chạm
  giới hạn sử dụng.
- Phase 4–6: chưa thực hiện.
- Test tổng thể, code review và ship: chưa thực hiện.

## 6. Quan điểm cá nhân bước đầu

Theo quan điểm của em, SDLC Harness không làm cho AI luôn đúng. Giá trị
chính của công cụ là khiến sai sót của AI dễ quan sát, dễ truy vết và khó
bị bỏ qua hơn.

Harness buộc AI phải:

- Làm rõ yêu cầu trước khi code.
- Đưa ra bằng chứng thay vì chỉ khẳng định.
- Chia công việc thành các phase có tiêu chí hoàn thành.
- Ghi lại quyết định và deviation.
- Chờ con người duyệt ở các điểm quan trọng.
- Kiểm tra lại kết quả do agent khác tạo ra.

Tuy nhiên, chất lượng cuối cùng vẫn phụ thuộc vào khả năng review của con
người. Trong quá trình thực hiện, em đã phát hiện lỗi mà planner và
red-team bỏ sót, đồng thời developer cũng có lúc phản biện lại nhận định
của main agent và chứng minh được nhận định đó chưa chính xác.

Qua đó, em nhận thấy mô hình phù hợp nhất không phải là “AI tự động làm
toàn bộ”, mà là:

> AI thực hiện và tự kiểm tra nhiều lớp, còn con người chịu trách nhiệm
> đánh giá phạm vi, quyết định các trade-off và phê duyệt kết quả.

## 7. Nội dung sẽ cập nhật sau

Sau khi hoàn thành toàn bộ quy trình, em sẽ bổ sung:

- Kết quả của Phase 3–6.
- Tổng số test và kết quả test cuối cùng.
- Kết quả chạy API local và bằng Docker.
- Kết quả code review.
- Kết quả bước ship.
- Link repository hoàn chỉnh.
- So sánh cuối cùng giữa cách dùng AI thông thường và SDLC Harness.
- Kết luận về trường hợp nên và không nên áp dụng Harness.