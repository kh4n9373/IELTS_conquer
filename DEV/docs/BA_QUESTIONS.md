# Câu hỏi từ Dev gửi BA và PO

| Mục | Giá trị |
|---|---|
| Ngày | 2026-11-21 |
| Căn cứ | Spec 1.1, MVP Plan 1.1, [ARCHITECTURE.md](ARCHITECTURE.md) |
| Tác động hiện tại | Không chặn M1. Các mục dưới đây chặn từ M3 trở đi, theo cột "Chặn" |

Theo HANDOFF mục 4, Dev không tự đổi hành vi sản phẩm. Mỗi mục có đề xuất để PO chốt nhanh.

| # | Vấn đề | Tác động | Chặn | Đề xuất của Dev | Người quyết |
|---|---|---|---|---|---|
| 1 | HANDOFF xem chuyển dữ liệu ra nước ngoài là vấn đề của AI ở M6. Nhưng nếu database đặt ở nước ngoài thì dữ liệu cá nhân đã ra nước ngoài từ kỳ thi thật đầu tiên | Có thể phát sinh nghĩa vụ LGL-07 sớm hơn dự kiến | M4 | Đặt database và tệp tại Việt Nam, hoặc hỏi luật sư trước M4 | PO, luật sư |
| 2 | OQ-09 còn mở: câu trả lời chưa đồng bộ khi hết giờ | Chưa chốt được logic nộp bài | M3 | Nhận trong 30 giây sau hạn. Câu đến muộn hơn được giữ lại, không chấm, giáo viên xem được | PO |
| 3 | "AI chữa ngay" (D-14) có thể mâu thuẫn với công bố kết quả muộn (ASG-04) và với việc làm lại, vì AI sẽ lộ đáp án sớm | Có nguy cơ vi phạm điều không được vi phạm số 1 | M3 | AI chỉ mở khi kết quả đã công bố. Nếu được làm lại, công bố sau lần làm cuối hoặc khi hết hạn | PO |
| 4 | Chưa quy định khi học viên bắt đầu sát hạn nộp: được đủ thời lượng hay bị cắt tại hạn nộp | Đồng hồ thi có thể sai kỳ vọng | M3 | Giờ kết thúc là thời điểm sớm hơn giữa "bắt đầu + thời lượng" và hạn nộp | PO |
| 5 | Kiểm tra trích dẫn (AIB-02) đối chiếu với bản OCR. OCR sai có thể làm câu trích sai vẫn qua, hoặc câu đúng bị chặn | Ảnh hưởng cổng phát hành AI | M5 | Giáo viên soát bản OCR theo từng passage trước khi bật AI | PO |
| 6 | Cần nội dung điều khoản upload và đồng ý xử lý dữ liệu (ACC-04, LGL-01, LGL-03, LGL-08) | Chưa thể cho người dùng thật đăng ký | M4 | Dev dùng nội dung giữ chỗ có đánh dấu rõ cho đến khi có bản chính thức | PO, luật sư |
| 7 | Chưa quy định viết tắt `Y`/`N` cho dạng Yes/No/Not Given | Chấm đề IELTS chuẩn có thể không nhất quán | M3 | Xử lý giống `T`/`F` | PO |
| 8 | Tài liệu ghi `DEV/exam/Reading_...pdf`, tệp thật nằm ở `DEV/exam/exam/` | Chỉ sai đường dẫn | Không | BA sửa đường dẫn trong MVP_PLAN và PILOT_EXAM_KEY | BA |
