"""M3 exam room in a real browser. Every name, answer and PDF page here is invented."""

import re
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fakes import make_pdf
from playwright.sync_api import Browser, BrowserContext, Page, expect, sync_playwright
from test_m1 import PASSWORD, WEB_URL, login
from test_m1 import servers as servers  # noqa: PLC0414  (reused fixture: API with in-memory files + web dev server)

TEACHER = "room-teacher@test.local"
STUDENT = "room-student@test.local"
PASSAGES = 5
PER_PASSAGE = 8
KEY_PREFIX = "zqkey"  # Invented answer text. The student must never see it on the wire.


def api(page: Page, method: str, path: str, body: Any = None, expect_status: int = 200) -> Any:
    res = page.request.fetch(f"{WEB_URL}{path}", method=method, data=body)
    assert res.status == expect_status, f"{method} {path} -> {res.status} {res.text()}"
    return res.json() if res.body() else None


def publish_assignment(browser: Browser) -> str:
    """Seeds a class, publishes a 5-passage, 40-question test and gives it to the class."""
    teacher = browser.new_context().new_page()
    seeded = teacher.request.post(
        f"{WEB_URL}/api/e2e/seed-class",
        data={"password": PASSWORD, "teacher": TEACHER, "students": [STUDENT]},
    )
    assert seeded.ok
    class_id = seeded.json()["classId"]
    login(teacher, TEACHER, PASSWORD)
    expect(teacher.get_by_role("heading", name="Lớp học")).to_be_visible()

    test = api(
        teacher,
        "POST",
        "/api/tests",
        {
            "title": "Đề phòng thi thử",
            "sourceType": "self_authored",
            "testType": "course",
            "durationMinutes": 120,
        },
        201,
    )
    res = teacher.request.put(
        f"{WEB_URL}/api/tests/{test['id']}/pdf",
        data=make_pdf(pages=15, text="Invented passage text"),
        headers={"content-type": "application/pdf"},
    )
    assert res.ok
    sections = []
    for i in range(PASSAGES):
        first = i * PER_PASSAGE + 1
        sections.append(
            {
                "title": f"Passage {i + 1}",
                "passagePages": [i * 3 + 1, i * 3 + 2],
                "questionPages": [i * 3 + 3],
                "ranges": [
                    {
                        "from": first,
                        "to": first + PER_PASSAGE - 1,
                        "questionType": "short_answer",
                        "wordLimit": 3,
                    }
                ],
            }
        )
    api(teacher, "PUT", f"/api/tests/{test['id']}/structure", {"sections": sections})
    items = [{"number": n, "answers": [f"{KEY_PREFIX}{n}"]} for n in range(1, PASSAGES * PER_PASSAGE + 1)]
    api(teacher, "PUT", f"/api/tests/{test['id']}/key/items", {"items": items})
    api(teacher, "POST", f"/api/tests/{test['id']}/key/review", {"items": items})
    api(teacher, "POST", f"/api/tests/{test['id']}/publish")
    now = datetime.now(UTC)
    api(
        teacher,
        "POST",
        "/api/assignments",
        {
            "testId": test["id"],
            "classId": class_id,
            "opensAt": (now - timedelta(hours=1)).isoformat(),
            "dueAt": (now + timedelta(hours=6)).isoformat(),
            "maxAttempts": 1,
            "mode": "exam",
            "releasePolicy": "after_submit",
        },
        201,
    )
    return str(test["id"])


def open_room(context: BrowserContext) -> Page:
    page = context.new_page()
    login(page, STUDENT, PASSWORD)
    expect(page).not_to_have_url(re.compile(r"/login"))
    page.goto(f"{WEB_URL}/assignments")
    page.get_by_role("button", name=re.compile("^(Bắt đầu|Làm tiếp)$")).click()
    expect(page.get_by_role("timer")).to_be_visible()
    return page


def seconds_left(page: Page) -> int:
    h, m, s = (int(x) for x in (page.get_by_role("timer").inner_text()).split(":"))
    return h * 3600 + m * 60 + s


def field(page: Page, number: int) -> Any:
    return page.get_by_label(re.compile(rf"^Câu {number}$"))


@pytest.fixture(scope="module")
def seeded(servers: None) -> Any:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        publish_assignment(browser)
        yield browser


@pytest.mark.e2e
def test_s1_reload_keeps_answers_and_clock(seeded: Browser) -> None:
    context = seeded.new_context()
    page = open_room(context)
    first = seconds_left(page)
    assert 7100 < first <= 7200
    field(page, 1).fill("alpha beta")
    expect(page.get_by_role("status").first).to_contain_text("Đã lưu lúc")
    page.add_init_script("const real = Date.now.bind(Date); Date.now = () => real() + 3600 * 1000;")
    page.reload()
    expect(page.get_by_role("timer")).to_be_visible()
    expect(field(page, 1)).to_have_value("alpha beta")
    # A device clock one hour ahead must not shorten or extend the attempt.
    again = seconds_left(page)
    assert first - 60 < again <= first
    context.close()


@pytest.mark.e2e
def test_s3_network_has_no_key_during_exam(seeded: Browser) -> None:
    context = seeded.new_context()
    bodies: list[str] = []

    def record(response: Any) -> None:
        if "application/json" in response.headers.get("content-type", ""):
            try:
                bodies.append(response.text())
            except Exception:  # noqa: S110  (navigation can drop a body; nothing to scan)
                pass

    context.on("response", record)
    page = open_room(context)
    field(page, 2).fill("gamma")
    expect(page.get_by_role("status").first).to_contain_text("Đã lưu lúc")
    assert bodies
    assert not any(KEY_PREFIX in b for b in bodies)
    context.close()


@pytest.mark.e2e
def test_s13_toggle_keeps_typing_and_page(seeded: Browser) -> None:
    context = seeded.new_context(viewport={"width": 390, "height": 844})
    page = open_room(context)
    page.get_by_role("button", name="Trang sau").click()
    expect(page.get_by_text("Trang 2 / 15")).to_be_visible()
    page.get_by_role("button", name="Câu hỏi", exact=True).click()
    box = field(page, 3)
    box.click()
    box.press_sequentially("half typed")  # no blur, no change event
    page.get_by_role("button", name="Bài đọc", exact=True).click()
    expect(page.get_by_text("Trang 2 / 15")).to_be_visible()
    assert page.evaluate("document.querySelector('canvas').width > 0")
    page.get_by_role("button", name="Câu hỏi", exact=True).click()
    expect(field(page, 3)).to_have_value("half typed")
    context.close()


@pytest.mark.e2e
def test_answer_panel_follows_the_passage_and_keeps_other_answers(seeded: Browser) -> None:
    context = seeded.new_context(viewport={"width": 1280, "height": 800})
    page = open_room(context)
    expect(field(page, 1)).to_be_visible()
    expect(field(page, PER_PASSAGE + 1)).to_have_count(0)  # passage 2 is not on screen yet
    field(page, 1).fill("first passage")
    expect(page.get_by_role("status").first).to_contain_text("Đã lưu lúc")
    page.get_by_role("button", name="Passage 2", exact=True).click()
    expect(field(page, 1)).to_have_count(0)
    expect(field(page, PER_PASSAGE + 1)).to_be_visible()
    expect(page.get_by_text("Trang 4 / 15")).to_be_visible()
    page.get_by_role("button", name="Passage 1", exact=True).click()
    expect(field(page, 1)).to_have_value("first passage")
    page.get_by_role("button", name="Nộp bài").click()
    # The warning still counts every question, not only this passage.
    dialog = page.get_by_role("alertdialog")
    expect(dialog).to_be_visible()
    counts = [int(x) for x in dialog.locator(".exam-counts strong").all_inner_texts()]
    assert counts[0] >= 1 and counts[0] + counts[1] == PASSAGES * PER_PASSAGE
    context.close()


@pytest.mark.e2e
def test_phone_passage_tabs_jump_the_pdf_without_page_scroll(seeded: Browser) -> None:
    context = seeded.new_context(viewport={"width": 320, "height": 640})
    page = open_room(context)
    page.get_by_role("button", name="Passage 3", exact=True).click()
    expect(page.get_by_text("Trang 7 / 15")).to_be_visible()
    page.get_by_role("button", name="Câu hỏi", exact=True).click()
    expect(field(page, 2 * PER_PASSAGE + 1)).to_be_visible()
    expect(field(page, 1)).to_have_count(0)
    page.get_by_role("button", name="Passage 2", exact=True).click()
    expect(field(page, PER_PASSAGE + 1)).to_be_visible()
    page.get_by_role("button", name="Bài đọc", exact=True).click()
    expect(page.get_by_text("Trang 4 / 15")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    context.close()


@pytest.mark.e2e
def test_pdf_link_refreshes_without_reloading_the_room(seeded: Browser) -> None:
    context = seeded.new_context()
    refused: list[int] = []

    def expired(route: Any) -> None:
        if not refused:
            refused.append(1)
            route.fulfill(status=403, body="expired")
        else:
            route.continue_()

    context.route("**/api/e2e-files/*", expired)
    page = open_room(context)
    field(page, 4).fill("typed before pdf")
    expect(page.get_by_text(re.compile(r"Trang 1 / 15"))).to_be_visible(timeout=20000)
    assert refused
    expect(field(page, 4)).to_have_value("typed before pdf")
    context.close()


@pytest.mark.e2e
def test_offline_answer_resent_after_reconnect(seeded: Browser) -> None:
    context = seeded.new_context()
    page = open_room(context)
    expect(page.get_by_text("Trang 1 / 15")).to_be_visible()
    context.set_offline(True)
    field(page, 5).fill("kept offline")
    expect(page.get_by_role("alert")).to_contain_text("Mất mạng")
    expect(page.get_by_role("status").first).to_contain_text("Chưa gửi được 1 câu")
    context.set_offline(False)
    expect(page.get_by_role("status").first).to_contain_text("Đã lưu lúc", timeout=20000)
    page.reload()
    expect(field(page, 5)).to_have_value("kept offline")
    context.close()


@pytest.mark.e2e
def test_old_device_becomes_read_only(seeded: Browser) -> None:
    old_context = seeded.new_context()
    old = open_room(old_context)
    new_context = seeded.new_context()
    new = open_room(new_context)
    new.get_by_label(re.compile(r"^Câu 6$")).fill("from the new device")
    expect(new.get_by_role("status").first).to_contain_text("Đã lưu lúc")

    field(old, 7).fill("typed on the old device")
    expect(old.get_by_role("alert")).to_contain_text("thiết bị khác", timeout=20000)
    expect(field(old, 7)).to_be_disabled()
    expect(old.get_by_role("button", name="Nộp bài")).to_be_disabled()

    old.get_by_role("button", name="Làm tiếp trên máy này").click()
    expect(field(old, 7)).to_be_enabled()
    expect(field(old, 6)).to_have_value("from the new device")
    expect(old.get_by_role("status").first).to_contain_text("Đã lưu lúc", timeout=20000)

    new.reload()
    expect(field(new, 7)).to_have_value("typed on the old device")
    old_context.close()
    new_context.close()
