"""M2 browser flow: a teacher creates, structures, keys, reviews and publishes a test.

Everything is invented: generated PDFs, an in-memory file store and no OCR worker.
The failed OCR job is written straight to the database by a test-only route.
"""

import re

import pytest
from fakes import make_pdf
from playwright.sync_api import Page, expect, sync_playwright
from test_m1 import WEB_URL, login, provision_teacher
from test_m1 import servers as servers  # noqa: PLC0414  (reused fixture: API with in-memory files + web dev server)

PDF_PAGES = 15


def pick_range(page: Page, section: str, number: int, first: int, last: int, kind: str) -> None:
    row = page.get_by_role("group", name=section).get_by_role("group", name=f"Dải câu {number}")
    row.get_by_label("Từ câu").fill(str(first))
    row.get_by_label("Đến câu").fill(str(last))
    row.get_by_label("Dạng câu").select_option(kind)


@pytest.mark.e2e
def test_teacher_authors_and_publishes_a_test(servers: None) -> None:  # noqa: PLR0915
    pdf_bytes = make_pdf(pages=PDF_PAGES, text="Invented passage text")
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        admin = browser.new_context().new_page()
        login(admin, "admin@test.local", "admin-password-123")
        teacher = provision_teacher(browser, admin, "Cô Mai", "mai@test.local")
        errors: list[str] = []
        teacher.on("pageerror", lambda e: errors.append(str(e)))
        requests: list[str] = []
        teacher.on("request", lambda r: requests.append(f"{r.method} {r.url}"))

        # Empty list, then create with a PDF.
        teacher.get_by_role("link", name="Đề thi").click()
        expect(teacher.get_by_role("heading", name="Đề của bạn")).to_be_visible()
        expect(teacher.get_by_text("Chưa có đề nào")).to_be_visible()
        teacher.get_by_role("link", name="Tạo đề đầu tiên").click()

        teacher.get_by_label("Tên đề").fill("Reading thử nghiệm")
        teacher.get_by_label("PDF đề thi").set_input_files(
            {"name": "invented.pdf", "mimeType": "application/pdf", "buffer": pdf_bytes}
        )
        teacher.get_by_role("button", name="Tiếp tục").click()
        expect(teacher.get_by_role("heading", name="Khai báo cấu trúc")).to_be_visible()
        test_id = re.search(r"/tests/([0-9a-f-]{36})/structure$", teacher.url)
        assert test_id is not None

        # The PDF is drawn by PDF.js, and OCR waits in the queue (no worker runs here).
        expect(teacher.get_by_text(f"Trang 1 / {PDF_PAGES}")).to_be_visible()
        expect(teacher.locator("canvas").first).to_be_visible()
        assert teacher.evaluate("document.querySelector('canvas').width > 0")
        expect(teacher.get_by_text("OCR đang chờ")).to_be_visible()

        # Publishing is blocked while the structure and key are missing.
        teacher.get_by_role("link", name="4. Xuất bản").click()
        expect(teacher.get_by_role("heading", name="Chưa xuất bản được")).to_be_visible()
        expect(teacher.get_by_role("button", name="Xuất bản đề")).to_be_disabled()

        # Range assignment: two passages, four questions.
        teacher.get_by_role("link", name="2. Cấu trúc").click()
        teacher.get_by_role("button", name="Thêm passage").click()
        teacher.get_by_role("button", name="Thêm passage").click()
        for n, (reading, questions) in enumerate([("2-3", "4"), ("5", "6")], start=1):
            group = teacher.get_by_role("group", name=f"Passage {n}")
            group.get_by_label("Trang bài đọc").fill(reading)
            group.get_by_label("Trang câu hỏi").fill(questions)
            group.get_by_role("button", name="Thêm dải câu").click()
        pick_range(teacher, "Passage 1", 1, 1, 2, "matching_features")
        pick_range(teacher, "Passage 2", 1, 3, 4, "short_answer")
        teacher.get_by_role("group", name="Passage 2").get_by_role("group", name="Dải câu 1").get_by_label(
            "Giới hạn từ"
        ).fill("2")
        teacher.get_by_role("button", name="Lưu cấu trúc").click()
        expect(teacher.get_by_text("Đã lưu cấu trúc")).to_be_visible()
        expect(teacher.get_by_text("2 passage, 4 câu")).to_be_visible()

        # OCR failure never blocks anything, and can be retried.
        teacher.request.post(f"{WEB_URL}/api/e2e/ocr-fail", data={"testId": test_id.group(1)})
        teacher.reload()
        expect(teacher.get_by_text("OCR lỗi")).to_be_visible()
        expect(teacher.get_by_text("Bạn vẫn xuất bản và thi bình thường")).to_be_visible()

        teacher.get_by_role("button", name="Chạy lại OCR").click()
        expect(teacher.get_by_text("OCR đang chờ")).to_be_visible()

        # Pasted key. Nothing is reviewed yet, so publishing stays blocked.
        teacher.get_by_role("link", name="3. Đáp án").click()
        teacher.get_by_label("Dán danh sách đáp án").fill("1. C\n2. B\n3. river bank\n4. two; platform two")
        teacher.get_by_role("button", name="Nhập danh sách đã dán").click()
        expect(teacher.get_by_label("Đáp án câu 3")).to_have_value("river bank")
        expect(teacher.get_by_text("Đã duyệt 0/4")).to_be_visible()
        teacher.get_by_role("link", name="4. Xuất bản").click()
        expect(teacher.get_by_role("heading", name="Chưa xuất bản được")).to_be_visible()
        expect(teacher.get_by_role("button", name="Xuất bản đề")).to_be_disabled()

        # Full review, then publish.
        teacher.get_by_role("link", name="3. Đáp án").click()
        teacher.get_by_role("button", name="Duyệt các câu không gắn cờ").click()
        expect(teacher.get_by_text("Đã duyệt 4/4")).to_be_visible()
        teacher.get_by_role("link", name="4. Xuất bản").click()
        expect(teacher.get_by_role("heading", name="Sẵn sàng xuất bản")).to_be_visible()
        teacher.get_by_role("button", name="Xuất bản đề").click()
        expect(teacher.get_by_text("Đề đã xuất bản")).to_be_visible()

        # Preview creates no attempt and loads no answers: no /key request, no mutation.
        requests.clear()
        teacher.get_by_role("link", name="Xem như học viên").click()
        expect(teacher.get_by_text("Đây là bản xem trước")).to_be_visible()
        expect(teacher.get_by_label("Câu 4")).to_be_disabled()
        expect(teacher.locator("canvas").first).to_be_visible()
        assert not [r for r in requests if "/key" in r]
        assert not [r for r in requests if not r.startswith("GET ")]

        teacher.get_by_role("link", name="Đề thi").click()
        expect(teacher.get_by_text("Reading thử nghiệm")).to_be_visible()
        expect(teacher.get_by_text("Đã xuất bản", exact=True)).to_be_visible()

        # English and a phone-width screen.
        teacher.get_by_role("button", name="English").click()
        expect(teacher.get_by_role("heading", name="Your tests")).to_be_visible()
        teacher.set_viewport_size({"width": 320, "height": 640})
        teacher.reload()
        assert teacher.evaluate("document.documentElement.scrollWidth <= window.innerWidth")

        for step in ("structure", "key"):
            teacher.goto(f"{WEB_URL}/tests/{test_id.group(1)}/{step}")
            expect(teacher.locator("table, fieldset").first).to_be_visible()
            assert teacher.evaluate("document.documentElement.scrollWidth <= window.innerWidth")

        assert errors == []
        browser.close()
