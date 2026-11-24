import re
import threading
from datetime import datetime, timedelta
from typing import Any

import pytest
from test_explain_db import call, typed_test

from conftest import Harness
from npa_api.explain.llm import LlmError
from npa_api.explain.worker import run_once

QUESTIONS = [
    {"number": n, "questionType": "multiple_choice", "prompt": f"Q{n}?", "options": ["x", "y"]}
    for n in range(1, 26)
]
KEY = [{"number": n, "answers": ["A"]} for n in range(1, 26)]


class FakeLlm:
    def __init__(self, fail: set[int] | None = None, fail_all: bool = False) -> None:
        self.prompts: list[str] = []
        self.fail = fail or set()
        self.fail_all = fail_all
        self.lock = threading.Lock()

    def __call__(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        numbers = [int(n) for n in re.findall(r"^Question (\d+) ", prompt, re.M)]
        with self.lock:
            self.prompts.append(prompt)
        if self.fail_all or self.fail & set(numbers):
            raise LlmError("provider_error")
        return {
            "items": [
                {"number": n, "correct": f"why {n}", "options": [{"answer": "B", "reason": f"not B {n}"}]}
                for n in numbers
            ]
        }


class Clock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at


@pytest.fixture(scope="module")
def test_id(h: Harness) -> str:
    _, tid, _ = typed_test(h, "xw-t@test.local", QUESTIONS, KEY, body="Secret passage text.")
    h.owner_rows("update tests set title = 'TITLE-SHOULD-NOT-LEAK' where id = %s", (tid,))
    return tid


@pytest.fixture(autouse=True)
def clean(h: Harness) -> None:
    h.owner_rows("delete from explain_jobs")
    h.owner_rows("delete from keys.question_explanations")


def rows(h: Harness, tid: str) -> list[dict[str, Any]]:
    return h.owner_rows(
        "select q.number, e.correct, e.options, e.source from keys.question_explanations e"
        " join questions q on q.id = e.question_id where e.test_id = %s order by q.number",
        (tid,),
    )


def job_state(h: Harness, tid: str) -> str:
    return str(h.owner_rows("select state from explain_jobs where test_id = %s", (tid,))[0]["state"])


def test_job_writes_explanations_and_finishes(h: Harness, test_id: str) -> None:
    call(h, "select app.explain_enqueue(%s)", test_id)
    llm = FakeLlm()
    assert run_once(h.db.app_pool, llm, Clock(h.now)) is True
    assert len(llm.prompts) == 2
    out = rows(h, test_id)
    assert len(out) == 25
    assert out[0]["correct"] == "why 1" and out[0]["options"] == {"b": "not B 1"}
    assert job_state(h, test_id) == "done"
    assert run_once(h.db.app_pool, llm, Clock(h.now)) is False


def test_second_run_skips_fresh_rows(h: Harness, test_id: str) -> None:
    call(h, "select app.explain_enqueue(%s)", test_id)
    run_once(h.db.app_pool, FakeLlm(), Clock(h.now))
    call(h, "select app.explain_enqueue(%s)", test_id)
    llm = FakeLlm()
    run_once(h.db.app_pool, llm, Clock(h.now))
    assert llm.prompts == []
    assert h.owner_rows("select count(*) as n from explain_jobs where state = 'done'")[0]["n"] == 2


def test_teacher_row_not_overwritten(h: Harness, test_id: str) -> None:
    call(h, "select app.explain_enqueue(%s)", test_id)
    run_once(h.db.app_pool, FakeLlm(), Clock(h.now))
    h.owner_rows(
        "update keys.question_explanations set source = 'teacher', correct = 'mine', content_hash = 'old'"
        " where question_id = (select id from questions where test_id = %s and number = 1)",
        (test_id,),
    )
    call(h, "select app.explain_enqueue(%s)", test_id)
    llm = FakeLlm()
    run_once(h.db.app_pool, llm, Clock(h.now))
    assert llm.prompts == []
    assert rows(h, test_id)[0]["correct"] == "mine"


def test_partial_batch_failure_keeps_others(h: Harness, test_id: str) -> None:
    call(h, "select app.explain_enqueue(%s)", test_id)
    run_once(h.db.app_pool, FakeLlm(fail={21}), Clock(h.now))
    assert len(rows(h, test_id)) == 20
    assert job_state(h, test_id) == "done"


def test_all_batches_fail_requeues_then_fails(h: Harness, test_id: str) -> None:
    call(h, "select app.explain_enqueue(%s)", test_id)
    clock = Clock(h.now)
    llm = FakeLlm(fail_all=True)
    assert run_once(h.db.app_pool, llm, clock)
    assert job_state(h, test_id) == "queued"
    assert run_once(h.db.app_pool, llm, clock) is False  # waits for the retry time
    for wait in (61, 301):
        clock.at += timedelta(seconds=wait)
        assert run_once(h.db.app_pool, llm, clock)
    assert job_state(h, test_id) == "failed"
    assert rows(h, test_id) == []


def test_prompt_has_no_student_or_title(h: Harness, test_id: str) -> None:
    call(h, "select app.explain_enqueue(%s)", test_id)
    llm = FakeLlm()
    run_once(h.db.app_pool, llm, Clock(h.now))
    text = "\n".join(llm.prompts)
    assert "Secret passage text." in text
    assert "TITLE-SHOULD-NOT-LEAK" not in text and "@test.local" not in text
