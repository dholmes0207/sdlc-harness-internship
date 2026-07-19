# Báo cáo insight khi sử dụng SDLC Harness

## 1. Bối cảnh

Trong bài thực tập này, em sử dụng SDLC Harness v5.3.0 kết hợp với Claude
Code để xây dựng một Product Search API bằng FastAPI.

Dữ liệu gồm 20 sản phẩm điện máy được chọn từ bộ dữ liệu Điện Máy Xanh mà
nhóm em từng xử lý trong cuộc thi Vietnam AI Innovation.

Sản phẩm hỗ trợ:

- Tìm kiếm tiếng Việt có dấu hoặc không dấu.
- Lọc theo ngành hàng, thương hiệu và khoảng giá.
- Phân trang kết quả.
- Swagger UI và Docker.
- Unit test và API integration test.

## 2. Quy trình thực hiện

Em đã đi qua các bước:

1. Cài đặt và xác minh Harness.
2. Discover yêu cầu và kiểm tra dữ liệu thực tế.
3. Lập plan, red-team và human review.
4. Phê duyệt plan.
5. Cook theo TDD qua 6 phase.
6. Independent testing và code review.
7. Kiểm tra Swagger bằng Uvicorn và Docker.
8. Chạy ship preflight.

Kết quả cuối:

- Hoàn thành 6/6 phase.
- 135 test pass.
- Independent tester: PASS.
- Code review: PASS.
- Acceptance criteria A1–A19 có bằng chứng.
- Swagger chạy đúng bằng Uvicorn và Docker.
- Truy vấn `may in` trả đúng 4 sản phẩm.

## 3. Những insight quan trọng

### Harness buộc AI kiểm tra giả định trước khi code

Trong bước discovery, Harness phát hiện nhiều vấn đề mà em có thể bỏ qua
nếu triển khai trực tiếp:

- Unicode NFD không tự chuyển `đ` thành `d`.
- Tìm kiếm substring khiến “máy in” khớp nhầm với “Inverter”.
- `/products/search` có thể bị route `/{product_id}` bắt nhầm.
- HTTP 200 không đảm bảo response body đúng.
- Dữ liệu JSON có cấu trúc khác với giả định ban đầu.

Điều này giúp giảm nguy cơ AI xây dựng giải pháp dựa trên thông tin sai.

### Test xanh chưa chắc đã là test tốt

Trong Phase 1, một test bảo vệ cấu hình `pythonpath` luôn pass kể cả khi cấu
hình bị xóa. Harness yêu cầu viết lại test và chứng minh:

1. Có cấu hình thì test pass.
2. Xóa cấu hình thì test fail.
3. Khôi phục cấu hình thì test pass trở lại.

Theo em, đây là insight kỹ thuật quan trọng nhất:

> Test không chỉ cần pass trên code đúng mà còn phải fail khi hành vi cần
> bảo vệ bị phá vỡ.

### Nhiều lớp review vẫn tạo thêm giá trị

Sau khi 131 test đã pass, independent tester vẫn phát hiện hai test chỉ
kiểm tra HTTP status mà chưa kiểm tra nội dung response.

Code reviewer tiếp tục phát hiện:

- Nguy cơ rò field ngoài response contract.
- Loader có thể nuốt exception.
- Token index có thể lập chỉ mục sai field.
- `lookup()` trả về mutable set dùng chung, khiến caller có thể vô tình làm
  hỏng index.

Sau khi sửa các vấn đề này, tổng số test tăng lên 135.

## 4. Khó khăn và hạn chế

Quy trình tương đối nặng đối với một demo chỉ có 20 sản phẩm và 4 endpoint.
Plan dài hơn 2.300 dòng, sử dụng nhiều agent và tiêu tốn khá nhiều token.

Phiên Claude Code từng chạm giới hạn khi bắt đầu Phase 3. Tuy nhiên, nhờ
Harness commit và lưu verification artifact theo từng phase, em có thể tiếp
tục mà không mất kết quả đã hoàn thành.

Em cũng gặp vấn đề khi push GitHub vì một số file `.pyc` của Harness chứa
chuỗi secret mẫu. Em phải tạo một repository public đã làm sạch lịch sử.
Do repository gốc và repository public có lịch sử Git khác nhau, phần
transport tự động của ship không thể thực hiện an toàn.

Ship preflight vẫn xác nhận test, verification, review, approval và secret
scan đều pass. Việc công bố sản phẩm được thực hiện thủ công qua GitHub.

## 5. Quan điểm cá nhân

Theo em, SDLC Harness không làm cho AI luôn đúng. Giá trị chính của công cụ
là khiến lỗi của AI dễ phát hiện, có bằng chứng và có thể truy vết.

Harness phù hợp với các dự án:

- Có nhiều edge case hoặc rủi ro cao.
- Có nhiều người hoặc nhiều agent tham gia.
- Cần audit và lưu lại quyết định kỹ thuật.
- Cần kiểm soát việc AI sửa, commit hoặc push code.

Đối với task nhỏ, chi phí thời gian và token có thể lớn hơn lợi ích.

Sau trải nghiệm này, em nhận thấy mô hình phù hợp nhất không phải là để AI
tự động làm toàn bộ, mà là:

> AI thực hiện và tự kiểm tra nhiều lớp; con người đánh giá bằng chứng,
> quyết định trade-off và phê duyệt kết quả cuối cùng.

## 6. Liên kết bài nộp

- Repository: `https://github.com/dholmes0207/sdlc-harness-internship`
- GitHub Release: `https://github.com/dholmes0207/sdlc-harness-internship/releases`
