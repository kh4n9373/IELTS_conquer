import os
import re
import secrets
import signal
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, expect, sync_playwright
from ports import pick_free_port, wait_for_owned_server, wait_until_port_free

API_PORT = pick_free_port()
WEB_PORT = pick_free_port()
while WEB_PORT == API_PORT:
    WEB_PORT = pick_free_port()
WEB_URL = f"http://127.0.0.1:{WEB_PORT}"
E2E_NONCE = secrets.token_hex(16)
PASSWORD = "teacher-password-123"
HERE = Path(__file__).parent
DEV_ROOT = HERE.parents[2]
MAIL_FILE = HERE / ".e2e-mail.log"


def stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(15)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


@pytest.fixture(scope="module")
def servers() -> Iterator[None]:
    for port in (API_PORT, WEB_PORT):
        wait_until_port_free(port)
    base_env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    api = subprocess.Popen(
        [sys.executable, str(HERE / "server.py")],
        env={
            **base_env,
            "PORT": str(API_PORT),
            "PUBLIC_URL": WEB_URL,
            "E2E_MAIL_FILE": str(MAIL_FILE),
            "E2E_NONCE": E2E_NONCE,
        },
        start_new_session=True,
    )
    web = subprocess.Popen(
        [
            "npm",
            "run",
            "dev",
            "-w",
            "@npa/web",
            "--",
            "--host",
            "127.0.0.1",
            "--port",
            str(WEB_PORT),
            "--strictPort",
        ],
        cwd=DEV_ROOT,
        env={**base_env, "API_URL": f"http://127.0.0.1:{API_PORT}"},
        start_new_session=True,
        stdout=subprocess.DEVNULL,
    )
    try:
        wait_for_owned_server(f"http://127.0.0.1:{API_PORT}/api/e2e/whoami", E2E_NONCE, api)
        wait_for_owned_server(f"{WEB_URL}/api/e2e/whoami", E2E_NONCE, web)
        yield
    finally:
        stop(web)
        stop(api)
        MAIL_FILE.unlink(missing_ok=True)


def last_link() -> str:
    links = re.findall(r"http\S+token=\S+", MAIL_FILE.read_text(encoding="utf-8"))
    assert links, "no link in mail log"
    return links[-1]


def login(page: Page, email: str, password: str) -> None:
    page.goto(f"{WEB_URL}/login")
    page.get_by_label("Email").fill(email)
    page.get_by_label("Mật khẩu").fill(password)
    page.get_by_role("button", name="Đăng nhập").click()


def provision_teacher(browser: Browser, admin: Page, name: str, email: str) -> Page:
    admin.get_by_label("Họ tên").fill(name)
    admin.get_by_label("Email").fill(email)
    admin.get_by_role("button", name="Tạo tài khoản").click()
    expect(admin.get_by_text(f"Đã gửi liên kết kích hoạt tới {email}")).to_be_visible()

    page = browser.new_context().new_page()
    page.goto(last_link())
    page.get_by_label("Mật khẩu mới").fill(PASSWORD)
    page.get_by_role("button", name="Lưu mật khẩu").click()
    expect(page.get_by_text("Đã đặt mật khẩu mới.")).to_be_visible()
    login(page, email, PASSWORD)
    expect(page.get_by_role("heading", name="Lớp học")).to_be_visible()
    return page


@pytest.mark.e2e
def test_admin_teacher_class_invite_student_with_tenant_isolation(servers: None) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        admin = browser.new_context().new_page()
        errors: list[str] = []
        admin.on("pageerror", lambda e: errors.append(str(e)))

        login(admin, "admin@test.local", "admin-password-123")
        expect(admin.get_by_role("heading", name="Giáo viên")).to_be_visible()

        teacher = provision_teacher(browser, admin, "Cô Lan", "lan@test.local")
        teacher.get_by_label("Tên lớp").fill("IELTS Reading K1")
        teacher.get_by_role("button", name="Tạo lớp").click()
        teacher.get_by_role("link", name="IELTS Reading K1").click()
        teacher.get_by_role("button", name="Tạo mã mời 14 ngày").click()
        code_box = teacher.locator(".code").first
        expect(code_box).to_have_text(re.compile(r"^\s*[A-Z2-9]{8}\s*$"))
        # text_content, not inner_text: the row's entry animation can make rendered text briefly empty.
        code = (code_box.text_content() or "").strip()
        assert re.fullmatch(r"[A-Z2-9]{8}", code)
        class_url = teacher.url

        student = browser.new_context().new_page()
        student.goto(f"{WEB_URL}/join/{code}")
        expect(student.get_by_role("heading", name="Tham gia lớp IELTS Reading K1")).to_be_visible()

        student.get_by_label("Dưới 18 tuổi").check()
        expect(student.get_by_role("alert")).to_contain_text("từ 18 tuổi")
        expect(student.get_by_role("button", name="Tạo tài khoản và vào lớp")).to_have_count(0)

        student.get_by_label("Từ 18 tuổi trở lên").check()
        student.get_by_label("Họ tên").fill("Minh")
        student.get_by_role("textbox", name="Email").fill("minh@test.local")
        student.get_by_label("Mật khẩu", exact=False).first.fill("student-password-1")
        student.get_by_label(re.compile("Tôi đồng ý xử lý dữ liệu")).check()
        student.get_by_role("button", name="Tạo tài khoản và vào lớp").click()
        expect(student.get_by_role("heading", name="Trang chủ")).to_be_visible()
        expect(student.get_by_text("IELTS Reading K1")).to_be_visible()

        teacher.reload()
        expect(teacher.get_by_text("minh@test.local")).to_be_visible()

        other = provision_teacher(browser, admin, "Thầy Nam", "nam@test.local")
        expect(other.get_by_text("IELTS Reading K1")).to_have_count(0)
        other.goto(class_url)
        expect(other.get_by_role("alert")).to_contain_text("Không tìm thấy")
        expect(other.get_by_text("minh@test.local")).to_have_count(0)

        teacher.set_viewport_size({"width": 320, "height": 640})
        teacher.reload()
        expect(teacher.get_by_text("minh@test.local")).to_be_visible()
        assert teacher.evaluate("document.documentElement.scrollWidth <= window.innerWidth")

        assert errors == []
        browser.close()
