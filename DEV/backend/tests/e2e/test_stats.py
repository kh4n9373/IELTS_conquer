"""Teacher statistics for an assignment, in a real browser. Every name and answer here is invented."""

import os
import re
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Browser, expect, sync_playwright
from test_m1 import PASSWORD, WEB_URL, login
from test_m1 import (
    servers as servers,  # noqa: PLC0414  (reused fixture: API with in-memory files + web dev server)
)
from test_m3_flow import KEY_PREFIX, publish, start, student_page
from test_m3_room import api

SHOTS = os.environ.get("STATS_SHOTS")  # a folder to save screenshots into, when set


@pytest.fixture(scope="module")
def browser(servers: None) -> Any:
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        yield b
        b.close()


def answer(page: Any, picks: list[str | None], flags: set[int]) -> None:
    attempt = start(page)
    token = api(page, "POST", f"/api/attempts/{attempt}/claim")["writerToken"]
    items = [
        {"number": n, "value": v, "markedForReview": n in flags, "seq": n}
        for n, v in enumerate(picks, 1)
        if v is not None
    ]
    api(page, "PUT", f"/api/attempts/{attempt}/answers", {"writerToken": token, "items": items})
    api(page, "POST", f"/api/attempts/{attempt}/submit", {"writerToken": token})


def seed(browser: Browser, teacher_email: str, students: list[str]) -> tuple[Any, str]:
    """Like test_m3_flow.seed, plus the first-run profile setup new accounts now go through."""
    teacher = browser.new_context().new_page()
    res = teacher.request.post(
        f"{WEB_URL}/api/e2e/seed-class",
        data={"password": PASSWORD, "teacher": teacher_email, "students": students},
    )
    assert res.ok
    login(teacher, teacher_email, PASSWORD)
    expect(teacher).not_to_have_url(re.compile(r"/login"))
    api(teacher, "POST", "/api/me/setup", {"displayName": "Co Stats", "age": 30})
    return teacher, str(res.json()["classId"])


def key(n: int) -> str:
    return f"{KEY_PREFIX}{n}"


@pytest.mark.e2e
def test_teacher_sees_class_question_and_student_stats(browser: Browser) -> None:
    names = ["stats-a@test.local", "stats-b@test.local", "stats-c@test.local"]
    teacher, class_id = seed(browser, "stats-teacher@test.local", names)
    publish(teacher, class_id, sizes=[3, 3, 3], test_type="course", minutes=30)
    plans: list[tuple[list[str | None], set[int]]] = [
        ([key(n) for n in range(1, 9)] + ["trap"], {9}),
        ([key(1), key(2), key(3), key(4), "trap", key(6), key(7), "nope", "trap"], {5, 8}),
        ([key(1), "trap", key(3), key(4), "trap", None, None, None, None], {2}),
    ]
    for email, (picks, flags) in zip(names, plans, strict=True):
        answer(student_page(browser, email), picks, flags)

    assignment = api(teacher, "GET", "/api/assignments")[0]
    teacher.goto(f"{WEB_URL}/assignments/{assignment['id']}")
    tabs = teacher.get_by_role("navigation", name="Thống kê bài giao")
    expect(tabs).to_be_visible()
    expect(teacher.get_by_text("Điểm trung bình", exact=True)).to_be_visible()
    dot = teacher.get_by_label(re.compile(r" · 8/9 câu · 89%$"))
    dot.hover()
    expect(dot).to_be_visible()
    expect(teacher.get_by_role("heading", name="Bẫy nhiều bạn cùng mắc")).to_be_visible()
    expect(teacher.get_by_text("2/3 chọn “trap”")).to_have_count(2)
    shot(teacher, "1-overview")

    tabs.get_by_role("button", name="Từng câu hỏi").click()
    expect(teacher.get_by_role("heading", name="Câu 9")).to_be_visible()  # hardest first
    expect(teacher.get_by_role("heading", name="Độ khó từng câu")).to_be_visible()
    shot(teacher, "2-questions")

    tabs.get_by_role("button", name="Từng học viên").click()
    expect(teacher.get_by_role("heading", name="Kết quả bài làm")).to_be_visible()
    expect(teacher.get_by_role("heading", name="Câu sai: lỗi chung hay lỗi riêng?")).to_be_visible()
    expect(teacher.get_by_text("Sure and right")).to_have_count(0)
    shot(teacher, "3-student")


def shot(page: Any, name: str) -> None:
    if SHOTS:
        page.wait_for_timeout(1500)  # let the entrance animation finish
        page.screenshot(path=str(Path(SHOTS) / f"{name}.png"), full_page=True)
