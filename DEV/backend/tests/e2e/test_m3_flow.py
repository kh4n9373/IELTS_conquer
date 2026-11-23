"""M3 from assignment to result, in a real browser. Every name, answer and PDF page here is invented."""

import re
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fakes import make_pdf
from playwright.sync_api import Browser, Page, expect, sync_playwright
from test_m1 import PASSWORD, WEB_URL, login
from test_m1 import servers as servers  # noqa: PLC0414  (reused fixture: API with in-memory files + web dev server)
from test_m3_room import api, field

KEY_PREFIX = "zqkey"  # Invented answer text. A student must not see it before release.
COURSE_SIZES = [5, 5]
IELTS_SIZES = [13, 13, 14]


def seed(browser: Browser, teacher_email: str, students: list[str]) -> tuple[Page, str]:
    teacher = browser.new_context().new_page()
    res = teacher.request.post(
        f"{WEB_URL}/api/e2e/seed-class",
        data={"password": PASSWORD, "teacher": teacher_email, "students": students},
    )
    assert res.ok
    login(teacher, teacher_email, PASSWORD)
    expect(teacher.get_by_role("heading", name="Lớp học")).to_be_visible()
    return teacher, str(res.json()["classId"])


def publish(
    teacher: Page,
    class_id: str,
    *,
    sizes: list[int],
    test_type: str,
    minutes: int,
    max_attempts: int = 1,
    mode: str = "exam",
) -> None:
    test = api(
        teacher,
        "POST",
        "/api/tests",
        {
            "title": "Đề thử luồng",
            "sourceType": "self_authored",
            "testType": test_type,
            "durationMinutes": minutes,
        },
        201,
    )
    res = teacher.request.put(
        f"{WEB_URL}/api/tests/{test['id']}/pdf",
        data=make_pdf(pages=3 * len(sizes), text="Invented passage text"),
        headers={"content-type": "application/pdf"},
    )
    assert res.ok
    sections, first = [], 1
    for i, size in enumerate(sizes):
        sections.append(
            {
                "title": f"Passage {i + 1}",
                "passagePages": [i * 3 + 1, i * 3 + 2],
                "questionPages": [i * 3 + 3],
                "ranges": [
                    {"from": first, "to": first + size - 1, "questionType": "short_answer", "wordLimit": 3}
                ],
            }
        )
        first += size
    api(teacher, "PUT", f"/api/tests/{test['id']}/structure", {"sections": sections})
    items = [{"number": n, "answers": [f"{KEY_PREFIX}{n}"]} for n in range(1, sum(sizes) + 1)]
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
            "maxAttempts": max_attempts,
            "mode": mode,
            "releasePolicy": "after_submit",
        },
        201,
    )


def student_page(browser: Browser, email: str) -> Page:
    page = browser.new_context().new_page()
    login(page, email, PASSWORD)
    expect(page).not_to_have_url(re.compile(r"/login"))
    return page


def start(page: Page) -> str:
    """Starts (or resumes) the one assignment and returns the attempt id."""
    assignment = api(page, "GET", "/api/my/assignments")[0]
    made = api(page, "POST", f"/api/my/assignments/{assignment['id']}/attempts", {"device": "desktop"})
    return str(made["attemptId"])


def value(n: int, correct: int) -> str:
    return f"{KEY_PREFIX}{n}" if n <= correct else "x"


def submit(page: Page, attempt: str, correct: int, wrong: int = 0) -> None:
    """Answers the first `correct` questions rightly and the next `wrong` wrongly, then submits."""
    token = api(page, "POST", f"/api/attempts/{attempt}/claim")["writerToken"]
    items = [
        {"number": n, "value": value(n, correct), "markedForReview": False, "seq": 1}
        for n in range(1, correct + wrong + 1)
    ]
    for i in range(0, len(items), 40):
        body = {"writerToken": token, "items": items[i : i + 40]}
        api(page, "PUT", f"/api/attempts/{attempt}/answers", body)
    api(page, "POST", f"/api/attempts/{attempt}/submit", {"writerToken": token})


@pytest.fixture(scope="module")
def browser(servers: None) -> Any:
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        yield b
        b.close()


@pytest.mark.e2e
def test_full_flow_assign_take_submit_result(browser: Browser) -> None:
    teacher, class_id = seed(browser, "flow-teacher@test.local", ["flow-student@test.local"])
    publish(teacher, class_id, sizes=COURSE_SIZES, test_type="course", minutes=30)
    page = student_page(browser, "flow-student@test.local")
    page.goto(f"{WEB_URL}/assignments")
    page.get_by_role("button", name=re.compile("^(Bắt đầu|Làm tiếp)$")).click()
    expect(page.get_by_role("timer")).to_be_visible()
    field(page, 1).fill(f"{KEY_PREFIX}1")
    field(page, 2).fill(f"{KEY_PREFIX}2")
    field(page, 3).fill("wrong guess")
    expect(page.get_by_role("status").first).to_contain_text("Đã lưu lúc")
    page.get_by_role("button", name="Nộp bài").first.click()
    page.get_by_role("alertdialog").get_by_role("button", name="Nộp bài").click()

    expect(page).to_have_url(re.compile(r"/results/"))
    expect(page.get_by_text("2/10", exact=True)).to_be_visible()
    expect(page.get_by_text("20%", exact=True)).to_be_visible()
    expect(page.get_by_text("Passage 1: 2/5")).to_be_visible()
    row = page.get_by_role("row").filter(has_text=f"{KEY_PREFIX}3")
    expect(row).to_contain_text("wrong guess")
    expect(row).to_contain_text("Sai")
    expect(page.get_by_role("row").filter(has_text=re.compile(rf"{KEY_PREFIX}1$"))).to_contain_text("Đúng")
    page.get_by_role("button", name="Chỉ xem câu sai").click()
    expect(page.get_by_role("row").filter(has_text=re.compile(rf"{KEY_PREFIX}1$"))).to_have_count(0)
    expect(page.get_by_role("row").filter(has_text=f"{KEY_PREFIX}3")).to_have_count(1)


@pytest.mark.e2e
def test_group_result_waits_for_everyone(browser: Browser) -> None:
    emails = ["group-a@test.local", "group-b@test.local"]
    teacher, class_id = seed(browser, "group-teacher@test.local", emails)
    publish(teacher, class_id, sizes=COURSE_SIZES, test_type="course", minutes=30)
    first, second = student_page(browser, emails[0]), student_page(browser, emails[1])
    bodies: list[str] = []
    first.on("response", lambda r: bodies.append(r.text()) if "/result" in r.url and r.ok else None)
    attempt = start(first)
    submit(first, attempt, correct=4, wrong=1)

    pending = api(first, "GET", f"/api/attempts/{attempt}/result")
    assert set(pending) == {"released", "status", "expectedAt"}
    assert pending["released"] is False
    assert pending["status"] == "submitted"
    first.goto(f"{WEB_URL}/results/{attempt}")
    expect(first.get_by_role("heading", name="Đã nộp bài")).to_be_visible()
    expect(first.get_by_text("Dự kiến có kết quả")).to_be_visible()
    body = first.locator("body").inner_text()
    assert KEY_PREFIX not in body
    assert "4/10" not in body
    assert not any(KEY_PREFIX in b for b in bodies)

    submit(second, start(second), correct=1)
    first.reload()
    expect(first.get_by_text("4/10", exact=True)).to_be_visible()
    assert api(first, "GET", f"/api/attempts/{attempt}/result")["released"] is True


@pytest.mark.e2e
def test_s2_timeout_auto_submits(browser: Browser) -> None:
    teacher, class_id = seed(browser, "late-teacher@test.local", ["late-student@test.local"])
    publish(teacher, class_id, sizes=COURSE_SIZES, test_type="course", minutes=30)
    page = student_page(browser, "late-student@test.local")
    attempt = start(page)
    token = api(page, "POST", f"/api/attempts/{attempt}/claim")["writerToken"]
    items = [{"number": n, "value": f"{KEY_PREFIX}{n}", "markedForReview": False, "seq": 1} for n in (1, 2)]
    api(page, "PUT", f"/api/attempts/{attempt}/answers", {"writerToken": token, "items": items})
    assert api(page, "POST", "/api/e2e/expire-attempts")["expired"] >= 1  # the student never presses submit

    result = api(page, "GET", f"/api/attempts/{attempt}/result")
    assert result["released"] is True
    assert result["status"] == "auto_submitted"
    assert result["correct"] == 2
    page.goto(f"{WEB_URL}/results/{attempt}")
    expect(page.get_by_text("2/10", exact=True)).to_be_visible()


@pytest.mark.e2e
def test_s5_band_is_labelled_estimate(browser: Browser) -> None:
    teacher, class_id = seed(browser, "ielts-teacher@test.local", ["ielts-student@test.local"])
    publish(teacher, class_id, sizes=IELTS_SIZES, test_type="ielts_standard", minutes=60)
    page = student_page(browser, "ielts-student@test.local")
    attempt = start(page)
    submit(page, attempt, correct=31, wrong=9)
    result = api(page, "GET", f"/api/attempts/{attempt}/result")
    assert result["bandLabel"] == "estimated"
    page.goto(f"{WEB_URL}/results/{attempt}")
    expect(page.get_by_text("31/40", exact=True)).to_be_visible()
    expect(page.get_by_text("78%", exact=True)).to_be_visible()
    band = page.get_by_text(re.compile("Band ước tính"))
    expect(band).to_contain_text(result["bandTableVersion"])
    expect(page.get_by_text("7.0", exact=True)).to_be_visible()


@pytest.mark.e2e
def test_s11_course_shows_no_band(browser: Browser) -> None:
    teacher, class_id = seed(browser, "course-teacher@test.local", ["course-student@test.local"])
    publish(teacher, class_id, sizes=COURSE_SIZES, test_type="course", minutes=30)
    page = student_page(browser, "course-student@test.local")
    attempt = start(page)
    submit(page, attempt, correct=10)
    result = api(page, "GET", f"/api/attempts/{attempt}/result")
    assert not {"band", "bandLabel", "bandTableVersion"} & set(result)
    page.goto(f"{WEB_URL}/results/{attempt}")
    expect(page.get_by_text("10/10", exact=True)).to_be_visible()
    expect(page.get_by_text("Đề theo khóa không quy đổi band")).to_be_visible()
    assert not re.search(r"band ước tính|estimated band", page.locator("body").inner_text(), re.IGNORECASE)


@pytest.mark.e2e
def test_retake_does_not_count_toward_progress(browser: Browser) -> None:
    teacher, class_id = seed(browser, "retake-teacher@test.local", ["retake-student@test.local"])
    publish(
        teacher, class_id, sizes=COURSE_SIZES, test_type="course", minutes=30, max_attempts=2, mode="practice"
    )
    page = student_page(browser, "retake-student@test.local")
    first = start(page)
    submit(page, first, correct=5)
    page.goto(f"{WEB_URL}/results/{first}")
    expect(page.get_by_text("5/10", exact=True)).to_be_visible()
    expect(page.get_by_text("không tính vào tiến độ")).to_have_count(0)

    second = start(page)
    assert second != first
    submit(page, second, correct=8)
    assert api(page, "GET", f"/api/attempts/{second}/result")["countsForProgress"] is False
    page.goto(f"{WEB_URL}/results/{second}")
    expect(page.get_by_text("8/10", exact=True)).to_be_visible()
    expect(page.get_by_text("lần làm lại nên không tính vào tiến độ")).to_be_visible()
