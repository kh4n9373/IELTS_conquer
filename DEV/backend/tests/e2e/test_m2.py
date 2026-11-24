"""M2 browser flow: a teacher creates, structures, keys, reviews and publishes a test.

Everything is invented: generated PDFs, an in-memory file store and no OCR worker.
The failed OCR job is written straight to the database by a test-only route.
"""

import re
from typing import Any

import pytest
from fakes import make_jbig2_pdf, make_jpx_pdf, make_pdf
from playwright.sync_api import Page, expect, sync_playwright
from test_m1 import WEB_URL, login, provision_teacher
from test_m1 import servers as servers  # noqa: PLC0414  (reused fixture: API with in-memory files + web dev server)

PDF_PAGES = 15


def question(number: int, kind: str, **extra: Any) -> dict[str, Any]:
    return {
        "number": number,
        "questionType": kind,
        "prompt": f"Invented question {number}",
        "options": [],
    } | extra


def passage(title: str, reading: list[int], asked: list[int], *questions: dict[str, Any]) -> dict[str, Any]:
    return {"title": title, "passagePages": reading, "questionPages": asked, "questions": list(questions)}


def create_pdf_test(page: Page, title: str, pdf: bytes, *sections: dict[str, Any]) -> str:
    """New PDF test: upload with consent, then one worker pass whose parser returns `sections`."""
    page.get_by_label("Tên đề").fill(title)
    page.get_by_role("button", name="Tiếp tục").click()
    page.get_by_label("PDF đề thi").set_input_files(
        {"name": "exam.pdf", "mimeType": "application/pdf", "buffer": pdf}
    )
    page.get_by_role("button", name="Tải lên và xử lý").click()
    expect(page.get_by_text("Đang đọc đề và tách câu hỏi")).to_be_visible()
    run = page.request.post(f"{WEB_URL}/api/e2e/run-worker", data={"structure": {"sections": list(sections)}})
    assert run.ok and run.json()["worked"]
    expect(page.get_by_text("Đang đọc đề và tách câu hỏi")).to_have_count(0, timeout=15_000)
    found = re.search(r"/tests/([0-9a-f-]{36})/structure$", page.url)
    assert found is not None
    return found.group(1)


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

        # Two passages, four questions, as the parser found them.
        matching = {"options": ["Dr Lee", "Ms Kim", "Mr Park"]}
        tid = create_pdf_test(
            teacher,
            "Reading thử nghiệm",
            pdf_bytes,
            passage("Passage 1", [2, 3], [4], question(1, "matching_features", **matching),
                    question(2, "matching_features", **matching)),
            passage("Passage 2", [5], [6], question(3, "short_answer", wordLimit=2),
                    question(4, "short_answer", wordLimit=2)),
        )  # fmt: skip

        # The PDF is drawn by PDF.js beside the parsed questions, opened at the first passage.
        expect(teacher.get_by_text(f"Trang 2 / {PDF_PAGES}")).to_be_visible()
        expect(teacher.locator("canvas").first).to_be_visible()
        assert teacher.evaluate("document.querySelector('canvas').width > 0")
        expect(teacher.get_by_text("2 câu, 4 câu hỏi")).to_be_visible()

        # Publishing is blocked while the key is missing.
        teacher.get_by_role("link", name="4. Xuất bản").click()
        expect(teacher.get_by_role("heading", name="Chưa xuất bản được")).to_be_visible()
        expect(teacher.get_by_role("button", name="Xuất bản đề")).to_be_disabled()

        # A failed run never blocks anything: this test is keyed, reviewed and published while it fails.
        teacher.request.post(f"{WEB_URL}/api/e2e/ocr-fail", data={"testId": tid})
        teacher.get_by_role("link", name="2. Câu hỏi").click()
        teacher.reload()
        expect(teacher.get_by_text("Chưa xử lý được PDF này")).to_be_visible()
        expect(teacher.get_by_role("button", name="Thử lại")).to_be_visible()

        # Typed key. Nothing is reviewed yet, so publishing stays blocked.
        teacher.get_by_role("link", name="3. Duyệt đáp án").click()
        for n, typed in enumerate(["C", "B", "river bank", "two; platform two"], start=1):
            box = teacher.get_by_label(f"Đáp án câu {n}")
            box.fill(typed)
            box.blur()
        expect(teacher.get_by_label("Đáp án câu 3")).to_have_value("river bank")
        expect(teacher.get_by_text("Đã duyệt 0/4")).to_be_visible()
        teacher.get_by_role("link", name="4. Xuất bản").click()
        expect(teacher.get_by_role("heading", name="Chưa xuất bản được")).to_be_visible()
        expect(teacher.get_by_role("button", name="Xuất bản đề")).to_be_disabled()

        # An emptied box goes back to the stored answer, so Review can never approve a hidden one.
        teacher.get_by_role("link", name="3. Duyệt đáp án").click()
        box = teacher.get_by_label("Đáp án câu 3")
        box.fill("")
        box.blur()
        expect(box).to_have_value("river bank")
        # A rejected save (answer over 100 characters) also restores the stored answer.
        box.fill("x" * 101)
        box.blur()
        expect(teacher.get_by_role("alert")).to_be_visible()
        expect(box).to_have_value("river bank")
        # While a box differs from the stored answer, review stays off.
        box.fill("river bank side")
        expect(teacher.get_by_role("button", name="Duyệt các câu không gắn cờ")).to_be_disabled()
        box.blur()
        expect(box).to_have_value("river bank side")
        expect(teacher.get_by_role("button", name="Duyệt các câu không gắn cờ")).to_be_enabled()

        # Full review, then an edit after review takes that answer back out of "reviewed".
        teacher.get_by_role("button", name="Duyệt các câu không gắn cờ").click()
        expect(teacher.get_by_text("Đã duyệt 4/4")).to_be_visible()
        first = teacher.get_by_label("Đáp án câu 1")
        first.fill("D")
        first.blur()
        expect(teacher.get_by_text("Đã duyệt 3/4")).to_be_visible()
        teacher.get_by_role("button", name="Duyệt câu 1").click()
        expect(teacher.get_by_text("Đã duyệt 4/4")).to_be_visible()
        teacher.get_by_role("link", name="4. Xuất bản").click()
        expect(teacher.get_by_role("heading", name="Sẵn sàng xuất bản")).to_be_visible()
        teacher.get_by_role("button", name="Xuất bản đề").click()
        expect(teacher.get_by_text("Đề đã xuất bản")).to_be_visible()

        # A published test cannot be processed again.
        teacher.get_by_role("link", name="2. Câu hỏi").click()
        expect(teacher.get_by_text("Chưa xử lý được PDF này")).to_be_visible()
        expect(teacher.get_by_role("button", name="Thử lại")).to_have_count(0)

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
            teacher.goto(f"{WEB_URL}/tests/{tid}/{step}")
            expect(teacher.locator("table, fieldset").first).to_be_visible()
            assert teacher.evaluate("document.documentElement.scrollWidth <= window.innerWidth")

        assert errors == []
        browser.close()


DARK_PIXELS = """() => {
  const c = document.querySelector('canvas');
  if (!c || !c.width) return 0;
  const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
  let n = 0;
  for (let i = 0; i < d.length; i += 4) if (d[i] < 60 && d[i + 3] > 200) n++;
  return n;
}"""


@pytest.mark.e2e
def test_jpeg2000_and_jbig2_pages_are_drawn(servers: None) -> None:
    """PDF.js decodes these formats with wasm files, which must be served from this origin."""
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        admin = browser.new_context().new_page()
        login(admin, "admin@test.local", "admin-password-123")
        teacher = provision_teacher(browser, admin, "Thầy Hùng", "hung@test.local")
        errors: list[str] = []
        teacher.on("pageerror", lambda e: errors.append(str(e)))

        teacher.goto(f"{WEB_URL}/tests/new")
        create_pdf_test(
            teacher,
            "Ảnh JPEG2000",
            make_jpx_pdf(),
            passage("Passage 1", [1], [1], question(1, "short_answer")),
        )
        expect(teacher.get_by_text("Trang 1 / 1")).to_be_visible()
        # The picture is half black. A page that decoded to nothing has no dark pixels.
        teacher.wait_for_function(f"({DARK_PIXELS})() > 1000", timeout=15_000)

        # Replacing the PDF processes it again and keeps the questions already there.
        with teacher.expect_response(lambda r: "/pdf?process=true" in r.url):
            teacher.locator(".file-button input").set_input_files(
                {"name": "jbig2.pdf", "mimeType": "application/pdf", "buffer": make_jbig2_pdf()}
            )
        assert teacher.request.post(f"{WEB_URL}/api/e2e/run-worker", data={"structure": {"sections": []}}).ok
        expect(teacher.get_by_role("group", name="Câu hỏi 1")).to_be_visible(timeout=15_000)
        expect(teacher.get_by_text("Trang 1 / 1")).to_be_visible()
        teacher.wait_for_function(f"({DARK_PIXELS})() > 1000", timeout=15_000)

        assert errors == []
        browser.close()
