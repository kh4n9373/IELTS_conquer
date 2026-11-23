# Thiết kế M3: Giao đề, phòng thi, chấm điểm, kết quả

| Mục | Giá trị |
|---|---|
| Trạng thái | PO đã duyệt ngày 2026-11-23 |
| Căn cứ | `BA/PRODUCT_SPEC.md` 1.1, `BA/MVP_PLAN.md` 1.1, `DEV/docs/ARCHITECTURE.md`, spec M2 |
| Yêu cầu | ASG-01 đến 05, EXM-01, 02, 03, 04, 05, 07, 08, 09, GRD-01, 02, RDG-01, RDG-03 |
| Nghiệm thu | S1, S2, S3, S4, S5, S11, S13, S14 |

## 1. Mục đích

Giáo viên giao được đề đã xuất bản. Học viên làm bài có giờ, không mất câu đã lưu, được chấm tự động và xem kết quả đúng thời điểm công bố.

**M3 hoàn thành khi:** một tài khoản học viên thử làm trọn đề pilot đã xuất bản, từ lúc nhận đề đến lúc xem kết quả, và 8 kịch bản nghiệm thu đều đạt.

## 2. Mục tiêu và giới hạn

**Trong M3:** giao bài, theo dõi trạng thái, phòng thi, autosave, tự nộp, chấm điểm, band cho đề IELTS chuẩn, màn kết quả và đáp án sau công bố, e2e an toàn với môi trường pilot.

**Ngoài M3:**

- Giáo viên chữa bài, chấm tay, nguyên nhân lỗi, thống kê (M4).
- Sửa đáp án sau khi có bài nộp và chấm lại (KEY-06, S6, M4). Đề đã xuất bản vẫn bị khóa đáp án như M2.
- AI, highlight và ghi chú trên PDF (EXM-06), nhắc học viên (ASG-06).
- Lỗi OCR tồn đọng (quét không ra đáp án vẫn báo xong).

**Không được xảy ra:**

- Đáp án hoặc đúng/sai xuống trình duyệt trước thời điểm công bố.
- Mất câu trả lời đã autosave.
- Đồng hồ bị kéo dài do tải lại trang hoặc đổi giờ máy.
- Một bài bị nộp hoặc chấm hai lần.
- Đề theo khóa có band ở bất kỳ phản hồi hoặc bảng nào.
- Test hoặc tiến trình phát triển ghi vào `npa_m2_pilot` ngoài bước chạy thật đã được PO duyệt.

## 3. Các phương án đã cân nhắc

| Phương án chấm | Đánh đổi | Kết quả |
|---|---|---|
| **Chấm trong lúc nộp bằng thư viện Python thuần** | Đạt mốc 5 giây, không phụ thuộc worker | **Chọn** |
| Chấm hoàn toàn bằng job nền | Worker dừng thì học viên không có điểm | Không chọn |
| Chấm trên trình duyệt | Đáp án lộ xuống client | Loại |

## 4. Quyết định của PO

| Nội dung | Quyết định |
|---|---|
| Hạn nộp | Mốc sớm hơn giữa giờ bắt đầu + thời lượng và hạn giao đề |
| Đáp án đến trễ | Trễ tối đa 30 giây vẫn nhận. Trễ hơn thì không chấm, nhưng giáo viên vẫn thấy |
| Y/N | Chấm như Yes/No, giống T/F |
| Công bố kết quả | Giáo viên chọn theo từng bài giao. Mặc định: bài 1 lần làm công bố ngay sau khi nộp; bài nhiều lần làm công bố sau lần cuối hoặc khi hết hạn |
| Bài giao cho nhiều học viên | Công bố cho cả nhóm khi tất cả đã xong hoặc khi hết hạn, để đáp án không lộ cho người đang làm. Giao cho 1 học viên thì theo dòng trên |
| Dữ liệu thi thử | Lớp, học viên và bài giao thử có nhãn thử, được giữ lại. Thống kê M4 và tiến độ lần đầu bỏ qua các bài này |

## 5. Luồng

### 5.1 Giao bài

Giáo viên chọn đề đã xuất bản, lớp hoặc một số học viên trong lớp, giờ mở, hạn nộp, số lần làm (1 đến 5), chế độ và cách công bố.

| Cách công bố | Điều kiện |
|---|---|
| Ngay sau khi nộp | Mặc định cho bài 1 lần làm. Nếu giao cho nhiều học viên thì chờ tất cả nộp xong hoặc hết hạn |
| Sau lần cuối hoặc khi hết hạn | Mặc định cho bài nhiều lần làm. Nếu giao cho nhiều học viên thì chờ tất cả xong lượt cuối hoặc hết hạn |
| Vào giờ giáo viên chọn | Bài nhiều lần làm hoặc nhiều học viên phải chọn giờ không sớm hơn hạn nộp |

Thời lượng lấy từ đề. Lớp đã lưu trữ và đề chưa xuất bản không giao được. Trạng thái bài giao (sắp mở, đang mở, đã đóng) tính từ thời gian. Giáo viên thấy từng học viên chưa làm, đang làm hoặc đã nộp, và thấy các câu đến trễ không được chấm.

### 5.2 Phòng thi

- PDF bên trái, khung đáp án bên phải. Mỗi passage có nút chuyển Bài đọc / Câu hỏi một thao tác, giữ vị trí đọc và câu đang nhập.
- Lưới câu hỏi cho thấy câu đã trả lời, chưa trả lời và câu đánh dấu xem lại.
- Server lưu hạn nộp lúc bắt đầu. Trình duyệt chỉ hiện đếm ngược theo giờ server.
- Autosave khi đổi đáp án (debounce) và tối đa mỗi 10 giây, có số thứ tự theo từng câu. Câu chưa gửi được giữ trong IndexedDB và gửi lại khi có mạng.
- Thiết bị mở sau cùng giành quyền ghi. Thiết bị cũ được báo và chuyển sang chỉ xem.
- Chế độ Luyện tập dùng cùng phòng thi. Vẫn tính giờ theo thời lượng đề, được lưu riêng là Luyện tập, và kết quả công bố ngay sau mỗi lần nộp.

### 5.3 Nộp và tự nộp

- Học viên bấm nộp: khóa bài, chấm trong cùng giao dịch.
- Hết giờ: bài chuyển sang tự nộp. Server nhận thêm đáp án trong 30 giây, rồi chốt và chấm.
- Việc quét bài hết giờ chạy **trong tiến trình API**, cộng với kiểm tra trên mỗi request. Không dùng `npa-worker`, vì worker đó không được chạy với database pilot.
- Khóa dòng để mỗi bài chỉ nộp và chấm một lần, kể cả khi API khởi động lại giữa chừng.

### 5.4 Chấm điểm

Hàm chấm là thư viện thuần, không I/O, cùng đầu vào luôn cho cùng kết quả.

| Quy tắc | Áp dụng |
|---|---|
| Không phân biệt hoa thường | Mọi đề |
| `T`/`F` và `Y`/`N` | Mọi đề, đúng theo dạng câu. `T` không được chấp nhận cho câu Yes/No |
| Giới hạn từ, từ có gạch nối là một từ | Mọi đề |
| Sai chính tả là sai; Anh/Mỹ chỉ đúng khi có trong biến thể đã duyệt | Mọi đề |
| Câu chọn nhiều đáp án: mỗi đáp án đúng 1 điểm, không xét thứ tự | Các câu giáo viên khai báo cùng nhóm |
| `NG` và bỏ một mạo từ đầu sau khi kiểm tra giới hạn từ | Chỉ đề bật bộ quy tắc pilot (BR-19, D-20) |

**Thay đổi nhỏ so với M2:** M2 chưa có cách khai báo các câu cùng nhóm "chọn nhiều đáp án", nên S4 chưa làm được. M3 thêm trường nhóm tùy chọn vào cấu trúc đề. Đề pilot không có dạng này nên không đổi.

Mỗi lần chấm lưu một bản không sửa được, ghi phiên bản đáp án và phiên bản quy tắc.

### 5.5 Kết quả

- Trước công bố: học viên chỉ thấy "đã nộp" và giờ dự kiến có kết quả.
- Sau công bố: điểm x/y, tỷ lệ, điểm theo passage, từng câu gồm câu trả lời, đúng/sai và đáp án.
- Đề IELTS chuẩn đủ 40 câu: thêm band có nhãn "ước tính" và phiên bản bảng quy đổi.
- Đề theo khóa: chỉ có số câu đúng và tỷ lệ. Database từ chối lưu band cho loại đề này.
- Bài có lần làm lại: tiến độ tính lần đầu (D-15).

## 6. Ràng buộc kỹ thuật

- Backend Python, giữ React UI. Màn hình phải được PO duyệt trên canvas trước khi sửa `DEV/frontend`.
- Mọi bảng mới có RLS. Vai trò học viên không có quyền trên đáp án. Bản chấm chỉ đọc được qua hàm kiểm tra công bố.
- Chỉ học viên được giao mới lấy được link PDF, link có hạn. Phản hồi của học viên dùng danh sách trường cho phép.
- Migration M3 chỉ thêm bảng, cột và hàm, để bản API M2 đang chạy không bị hỏng.
- Không commit, in, log hay copy `DEV/exam`, đáp án thật, `DEV/.env`, `BAIDU_OCR_ACCESS_TOKEN`. Không thêm dòng Claude co-author hoặc generated-by.

## 7. An toàn e2e và đề pilot

- E2E lấy cổng trống riêng cho API và web, kiểm tra cổng trước khi khởi động, và **dừng ngay nếu cổng đã bị chiếm**. Test chỉ chạy sau khi xác nhận đúng tiến trình của nó đang nghe.
- S14 đọc đáp án từ `BA/PILOT_EXAM_KEY.md` lúc chạy, chỉ chạy với marker `pilot`, không đưa giá trị vào fixture, log hay thông báo lỗi. Khi sai chỉ báo số thứ tự ca.
- **Bước chạy thật cần PO duyệt riêng ngay trước khi làm:** áp migration M3 lên `npa_m2_pilot`, bật bộ quy tắc pilot cho đề, tạo lớp, học viên và bài giao có nhãn thử, chạy API và web M3 ở cổng riêng, không chạy `npa-worker`, không gọi PaddleOCR. Web pilot ở 5174 và 3100 giữ nguyên.

## 8. Phần để bên xây dựng quyết định

- Lược đồ bảng, endpoint, cách chia hàm SQL.
- Khoảng thời gian quét bài hết giờ, miễn là bài được chốt ngay sau 30 giây ân hạn.
- Cách lưu tạm trong IndexedDB và chiến lược gửi lại.

## 9. Các điểm người viết tự chọn, PO kiểm tra giúp

1. Luyện tập vẫn tính giờ và công bố ngay sau mỗi lần nộp.
2. Số lần làm tối đa là 5.
3. Học viên bị xóa khỏi lớp giữa giờ thi: không vào lại được, câu đã lưu vẫn được tự nộp và chấm, giáo viên vẫn thấy kết quả.
4. Đề bị lưu trữ khi đang có bài giao: bài đang làm vẫn hoàn tất, không tạo được lượt mới.
5. Sửa bài giao: giáo viên sửa hoặc hủy được khi chưa ai bắt đầu. Khi đã có người làm thì khóa, vì hạn nộp của lượt làm đã được lưu.
6. Người nhận được chốt lúc giao. Học viên vào lớp sau không tự nhận bài cũ.
7. Chuẩn hóa trước khi so khớp: bỏ khoảng trắng đầu cuối, gộp khoảng trắng liên tiếp, không phân biệt hoa thường, bỏ dấu `. , ; : ! ?` ở cuối câu trả lời. Dấu bên trong từ giữ nguyên. Định dạng số chỉ đúng khi có trong biến thể đã duyệt (OQ-04).
8. Trước bước chạy thật, kiểm tra đề pilot đã xuất bản và đủ 40 đáp án đã duyệt, chỉ đếm và không in giá trị. Đáp án đã duyệt không sửa được, nên nếu thiếu biến thể D-20 thì dừng và báo PO, không tự sửa.
