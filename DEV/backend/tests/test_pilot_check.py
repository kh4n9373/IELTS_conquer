import json
from uuid import uuid4

from m3_helpers import publish_test

from conftest import Harness
from npa_api.pilot_check import pilot_counts

INVENTED = {n: [f"invented-{n}-zq"] for n in range(1, 23)} | {23: ["velvet-owl", "velvet owl", "owlvelvet"]}


def teacher_cookie(h: Harness) -> str:
    email = f"pc-{uuid4().hex[:8]}@test.local"
    h.create_user(email, "teacher")
    return h.login(email)


def counts(h: Harness, test_id: str) -> dict[str, int | bool]:
    with h.db.owner_pool.connection() as conn:
        return pilot_counts(conn, test_id)


def snapshot(h: Harness) -> list[list[dict[str, object]]]:
    tables = ["tests", "questions", "keys.answer_key_versions", "keys.answer_key_items"]
    return [h.owner_rows(f"select * from {t} order by ctid") for t in tables]


def test_counts_exact_keys_and_values(h: Harness) -> None:
    tid = publish_test(h, teacher_cookie(h), answers=INVENTED, ranges=[(1, 23, "short_answer", 3)])
    result = counts(h, tid)
    assert set(result) == {"published", "questions", "reviewedAnswers", "approved", "variantsAtQ23"}
    assert result == {
        "published": True,
        "questions": 23,
        "reviewedAnswers": 23,
        "approved": True,
        "variantsAtQ23": 3,
    }


def test_unknown_test_is_all_empty(h: Harness) -> None:
    assert counts(h, str(uuid4())) == {
        "published": False,
        "questions": 0,
        "reviewedAnswers": 0,
        "approved": False,
        "variantsAtQ23": 0,
    }


def test_changes_nothing_and_leaks_no_answers(h: Harness) -> None:
    tid = publish_test(h, teacher_cookie(h), answers=INVENTED, ranges=[(1, 23, "short_answer", 3)])
    before = snapshot(h)
    out = json.dumps(counts(h, tid))
    assert snapshot(h) == before
    for values in INVENTED.values():
        for value in values:
            assert value not in out
