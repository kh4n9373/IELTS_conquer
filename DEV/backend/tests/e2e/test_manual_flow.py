"""Both ways of making a test, in a real browser. All content is invented, never real exam material."""

import os
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fakes import make_pdf
from playwright.sync_api import Browser, Page, expect, sync_playwright
from test_m1 import PASSWORD, WEB_URL, login
from test_m1 import servers as servers  # noqa: PLC0414
from test_m3_room import api

SHOTS = os.environ.get("E2E_SHOTS")
# Several lines of invented test text on one page, drawn as separate text runs.
MINI_TEST = ") Tj ET BT /F1 6 Tf 10 {} Td (".join(
    [
        "Zorbs live in caves and eat moss.",
        "Questions 1-2 Write TRUE FALSE or NOT GIVEN",
        "1 Zorbs live in trees.",
        "2 Zorbs eat moss.",
        "Question 3 Choose A B or C",
        "3 What do zorbs eat?",
        "A rocks B moss C fish",
    ]
).format(80, 70, 60, 50, 40, 30)


def shot(page: Page, name: str) -> None:
    if SHOTS:
        page.wait_for_timeout(1000)  # let the entrance animation finish
        page.screenshot(path=f"{SHOTS}/{name}.png", full_page=True)


def teacher_page(browser: Browser, teacher: str, students: list[str]) -> tuple[Page, str]:
    page = browser.new_context().new_page()
    seeded = page.request.post(
        f"{WEB_URL}/api/e2e/seed-class", data={"password": PASSWORD, "teacher": teacher, "students": students}
    )
    login(page, teacher, PASSWORD)
    expect(page.get_by_role("heading", name="Lớp học")).to_be_visible()
    return page, str(seeded.json()["classId"])


def new_test(page: Page, mode: str, title: str) -> str:
    page.goto(f"{WEB_URL}/tests/new")
    page.get_by_role("button", name=re.compile(f"^{mode}")).click()
    page.get_by_label("Tên đề").fill(title)
    shot(page, f"1-create-{mode}")
    page.get_by_role("button", name="Tiếp tục").click()
    expect(page).to_have_url(re.compile(r"/tests/[^/]+/structure$"))
    return page.url.split("/")[-2]


def options(group: Any, *texts: str, label: str = "Các lựa chọn") -> None:
    for i, text in enumerate(texts):
        group.get_by_label(f"{label} {'ABCDEFGH'[i] if label == 'Các lựa chọn' else i + 1}").fill(text)


@pytest.mark.e2e
def test_teacher_writes_test_and_student_takes_it(servers: None) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        teacher, class_id = teacher_page(browser, "manual-teacher@test.local", ["manual-student@test.local"])
        tid = new_test(teacher, "Tự soạn", "Đề soạn tay")

        teacher.get_by_role("button", name="Tạo câu").click()
        teacher.get_by_label("Nội dung (bài đọc, đoạn văn…)").fill("Zorbs live in caves.\nThey eat moss.")
        add = teacher.get_by_role("button", name="Thêm câu hỏi")
        add.click()  # 1: one answer
        q1 = teacher.get_by_role("group", name="Câu hỏi 1")
        q1.get_by_label("Đề câu hỏi").fill("Where do zorbs live?")
        options(q1, "trees", "caves", "rivers", "sky")
        q1.get_by_role("button", name="Chọn B là đáp án đúng").click()
        add.click()  # 2: several answers
        q2 = teacher.get_by_role("group", name="Câu hỏi 2")
        q2.get_by_label("Dạng câu hỏi").select_option(label="Trắc nghiệm nhiều đáp án")
        q2.get_by_label("Đề câu hỏi").fill("Pick TWO true facts")
        options(q2, "caves", "fly", "moss", "fire")
        q2.get_by_role("button", name="Chọn A là đáp án đúng").click()
        q2.get_by_role("button", name="Chọn C là đáp án đúng").click()
        add.click()  # 3: gap fill
        q3 = teacher.get_by_role("group", name="Câu hỏi 3")
        q3.get_by_label("Dạng câu hỏi").select_option(label="Điền vào chỗ trống")
        q3.get_by_label("Đề câu hỏi").fill("Zorbs eat ___.")
        q3.get_by_label("Đáp án câu hỏi 3").fill("moss; green moss")
        add.click()  # 4: word bank
        q4 = teacher.get_by_role("group", name="Câu hỏi 4")
        q4.get_by_label("Dạng câu hỏi").select_option(label="Điền từ gợi ý")
        q4.get_by_label("Đề câu hỏi").fill("They live in ___.")
        options(q4, "caves", "towns", label="Từ gợi ý")
        q4.get_by_label("Đáp án câu hỏi 4").select_option("caves")
        shot(teacher, "2-manual-editor")
        expect(teacher.get_by_text("Đã lưu câu hỏi và đáp án.")).to_be_visible()

        key = api(teacher, "GET", f"/api/tests/{tid}/key")
        got = {i["number"]: i["answers"] for i in key["items"]}
        assert got == {1: ["B"], 2: ["A, C"], 3: ["moss", "green moss"], 4: ["caves"]}
        items = [{"number": n, "answers": a} for n, a in got.items()]
        api(teacher, "POST", f"/api/tests/{tid}/key/review", {"items": items})
        teacher.goto(f"{WEB_URL}/tests/{tid}/publish")
        expect(teacher.get_by_text("Đã có PDF đề")).to_have_count(0)
        api(teacher, "POST", f"/api/tests/{tid}/publish")
        now = datetime.now(UTC)
        api(teacher, "POST", "/api/assignments", {
            "testId": tid, "classId": class_id, "opensAt": (now - timedelta(hours=1)).isoformat(),
            "dueAt": (now + timedelta(hours=6)).isoformat(), "maxAttempts": 1, "mode": "exam",
            "releasePolicy": "after_submit"}, 201)  # fmt: skip

        page = browser.new_context().new_page()
        login(page, "manual-student@test.local", PASSWORD)
        expect(page).not_to_have_url(re.compile(r"/login"))
        page.goto(f"{WEB_URL}/assignments")
        page.get_by_role("button", name=re.compile("^(Bắt đầu|Làm tiếp)$")).click()
        expect(page.get_by_text("Zorbs live in caves.")).to_be_visible()
        page.get_by_role("radio").nth(1).check()  # B, right
        page.get_by_role("checkbox").nth(2).check()
        page.get_by_role("checkbox").nth(0).check()
        page.locator("#exam-q3").fill("Moss")
        page.locator("#exam-q4").select_option("towns")  # wrong: 3 of 4
        expect(page.get_by_role("status").first).to_contain_text("Đã lưu lúc")
        shot(page, "3-room")
        page.get_by_role("button", name="Nộp bài").first.click()
        shot(page, "4-submit")
        page.get_by_role("alertdialog").get_by_role("button", name="Nộp bài").click()
        expect(page).to_have_url(re.compile(r"/results/"))
        expect(page.get_by_text(re.compile(r"\b3\s*/\s*4\b")).first).to_be_visible()
        browser.close()


def upload_and_process(teacher: Page, text: str) -> None:
    teacher.get_by_label("PDF đề thi").set_input_files(
        {"name": "invented.pdf", "mimeType": "application/pdf", "buffer": make_pdf(pages=2, text=text)}
    )
    teacher.get_by_role("button", name="Tải lên và xử lý").click()
    expect(teacher.get_by_text("Đang đọc đề và tách câu hỏi")).to_be_visible()
    teacher.goto(f"{WEB_URL}/tests")
    expect(teacher.get_by_text("Đang xử lý")).to_be_visible()
    teacher.go_back()


@pytest.mark.e2e
def test_pdf_is_processed_into_questions_and_teacher_fills_answers(servers: None) -> None:
    parsed = {"sections": [{"title": "Passage 1", "passagePages": [1], "questionPages": [2], "questions": [
        {"number": 1, "questionType": "tfng", "prompt": "Zorbs live in trees.", "options": []},
        {"number": 2, "questionType": "multiple_choice", "prompt": "What do zorbs eat?",
         "options": ["rocks", "moss", "fish"]},
    ]}]}  # fmt: skip
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        teacher, _ = teacher_page(browser, "pdf-teacher@test.local", [])
        tid = new_test(teacher, "Từ đề PDF", "Đề PDF")
        upload_and_process(teacher, "Invented passage text")
        assert api(teacher, "POST", "/api/e2e/run-worker", {"structure": parsed})["worked"] is True
        q1 = teacher.get_by_role("group", name="Câu hỏi 1")
        expect(q1.get_by_label("Đề câu hỏi")).to_have_value("Zorbs live in trees.", timeout=15000)
        expect(teacher.locator("canvas").first).to_be_visible()
        q1.get_by_label("Đáp án câu hỏi 1").select_option("FALSE")
        teacher.get_by_role("group", name="Câu hỏi 2").get_by_role(
            "button", name="Chọn B là đáp án đúng"
        ).click()
        shot(teacher, "4-pdf-editor")
        expect(teacher.get_by_text("Đã lưu câu hỏi và đáp án.")).to_be_visible()
        got = {i["number"]: i["answers"] for i in api(teacher, "GET", f"/api/tests/{tid}/key")["items"]}
        assert got == {1: ["FALSE"], 2: ["B"]}
        browser.close()


@pytest.mark.e2e
@pytest.mark.skipif(not os.environ.get("GEMINI_API_KEY"), reason="needs GEMINI_API_KEY for a live parse")
def test_live_gemini_parses_an_invented_pdf(servers: None) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        teacher, _ = teacher_page(browser, "live-teacher@test.local", [])
        tid = new_test(teacher, "Từ đề PDF", "Đề Gemini")
        upload_and_process(teacher, MINI_TEST)
        assert api(teacher, "POST", "/api/e2e/run-worker", {"live": True})["worked"] is True
        expect(teacher.get_by_role("group", name="Câu hỏi 3")).to_be_visible(timeout=15000)
        shot(teacher, "5-live-gemini")
        questions = api(teacher, "GET", f"/api/tests/{tid}")["sections"][0]["questions"]
        assert [q["questionType"] for q in questions] == ["tfng", "tfng", "multiple_choice"]
        assert questions[2]["options"] == ["rocks", "moss", "fish"]
        browser.close()


@pytest.mark.e2e
def test_edits_survive_moving_between_steps_without_waiting(servers: None) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        teacher, _ = teacher_page(browser, "steps-teacher@test.local", [])
        new_test(teacher, "Tự soạn", "Đề chuyển bước")
        teacher.get_by_role("button", name="Tạo câu").click()
        teacher.get_by_role("button", name="Thêm câu hỏi").click()
        q1 = teacher.get_by_role("group", name="Câu hỏi 1")
        q1.get_by_label("Đề câu hỏi").fill("Where do zorbs live?")
        options(q1, "trees", "caves")
        q1.get_by_role("button", name="Chọn B là đáp án đúng").click()
        teacher.get_by_role("link", name="3. Duyệt đáp án").click()  # straight away, before the timer saves
        expect(teacher.get_by_label("Đáp án câu 1")).to_have_value("B")
        expect(teacher.get_by_text("Đã duyệt 1/1")).to_be_visible()  # typed by the teacher: already reviewed
        teacher.go_back()  # the browser's Back is the previous step, not the empty create form
        expect(teacher).to_have_url(re.compile(r"/structure$"))
        q1 = teacher.get_by_role("group", name="Câu hỏi 1")
        expect(q1.get_by_label("Đề câu hỏi")).to_have_value("Where do zorbs live?")
        picked = q1.get_by_role("button", name="Chọn B là đáp án đúng")
        expect(picked).to_have_attribute("aria-pressed", "true")
        browser.close()
