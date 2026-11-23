# Thiết kế M2: Tạo đề Reading

| Mục | Giá trị |
|---|---|
| Trạng thái | PO duyệt ngày 2026-11-23 |
| Căn cứ | `BA/PRODUCT_SPEC.md` 1.1, `BA/MVP_PLAN.md` 1.1, `DEV/docs/ARCHITECTURE.md` |
| Yêu cầu | CNT-01, 03, 04, 05, 06, 10, 11, 12, 13 và KEY-01 đến KEY-05 |

## 1. Mục đích

Giáo viên đưa được một đề Reading thật lên hệ thống để M3 có đề giao cho học viên.

**M2 hoàn thành khi:** tạo trọn đề pilot 5 passage, 40 câu, 120 phút, và hệ thống không cho xuất bản khi còn câu thiếu đáp án, thiếu dạng câu hoặc chưa duyệt.

## 2. Mục tiêu và giới hạn

**Trong M2:** tải PDF, khai báo cấu trúc và trang, OCR đề, nhập, dán hoặc quét đáp án, duyệt, xuất bản, lưu trữ đề.

**Ngoài M2:** giao bài, phòng thi, chấm điểm, chấm lại (KEY-06 cần có bài nộp nên thuộc M3 trở đi), tạo đề bằng template, AI giải thích.

**Không được xảy ra:**

- Học viên đọc được đáp án hoặc đề chưa được giao.
- Giáo viên này đọc được đề hoặc đáp án của giáo viên khác.
- Lỗi OCR chặn việc xuất bản hoặc thi.
- Kết quả quét tự trở thành đáp án chính thức khi giáo viên chưa duyệt.
- Đề theo khóa có dữ liệu band.

## 3. Các phương án đã cân nhắc

| Phương án | Đánh đổi | Kết quả |
|---|---|---|
| Tự chạy OCR trên server | Không gửi đề ra ngoài, nhưng tốn tài nguyên và nhận dạng ảnh kém hơn | Không chọn |
| Dùng Claude để OCR | Chính xác, nhưng tốn phí | Không chọn |
| Chưa làm quét ảnh | Nhanh nhất, nhưng cắt KEY-04 | Không chọn |
| **API PaddleOCR trên Baidu AI Studio** | Miễn phí và có AI, nhưng đề được gửi cho bên thứ ba | **PO chọn ngày 2026-11-23** |

## 4. Luồng đã duyệt

1. **Tạo đề:** giáo viên tải PDF lên kho riêng, rồi khai báo loại đề, thời gian, passage, trang bài đọc, trang câu hỏi và dạng câu.
2. **OCR đề:** tác vụ nền gọi API PaddleOCR. Khi API lỗi hoặc hết hạn mức, đề vẫn xuất bản và thi được, vì OCR chỉ phục vụ AI ở M5.
3. **Đáp án:** PDF có lớp chữ được đọc trên server và không gửi ra ngoài. Ảnh đáp án chỉ được gửi khi giáo viên bấm quét. Kết quả là bản nháp, có đánh dấu câu không chắc.
4. **Duyệt và xuất bản:** vòng đời Nháp → Đã xuất bản → Lưu trữ. Chặn xuất bản nếu còn câu thiếu đáp án, thiếu dạng câu hoặc chưa duyệt.
5. **Bảo vệ dữ liệu:** không gửi tên, email hay bài làm của học viên cho OCR. Phần gọi OCR nằm sau một giao diện riêng để đổi nhà cung cấp mà không sửa luồng chính.

## 5. Ràng buộc

- Backend Python. Giao diện React phải được PO duyệt trên canvas trước khi code.
- Mọi bảng mới có RLS theo giáo viên. Đáp án nằm ở vùng mà vai trò học viên không có quyền đọc.
- Nội dung PDF và kết quả OCR là dữ liệu không tin cậy.
- Token đọc từ biến môi trường `BAIDU_OCR_ACCESS_TOKEN`, không ghi vào log, phản hồi API, audit log hoặc git.
- Mọi thay đổi đáp án và trạng thái đề được ghi audit log.

## 6. Rủi ro còn mở

**Chưa xác nhận được** thời gian lưu tệp, việc dùng tệp để huấn luyện và nơi xử lý của API. Đề có thể được chuyển sang Trung Quốc. PO cần đưa nội dung này vào danh sách hỏi luật sư cùng R-01, R-05 và R-11, trước khi thu phí hoặc mở cho giáo viên thứ hai.

## 7. Phần để bên xây dựng quyết định

- Cách lưu tệp trong môi trường phát triển, miễn là tương thích S3 và dùng link ký có thời hạn.
- Giới hạn dung lượng và cách kiểm tra PDF.
- Cách đánh dấu câu không chắc khi quét đáp án.
- Lược đồ bảng và thiết kế endpoint chi tiết.
