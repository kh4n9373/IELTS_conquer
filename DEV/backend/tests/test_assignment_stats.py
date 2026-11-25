from datetime import UTC, datetime, timedelta

import pytest
from m3_helpers import make_class_with_students, publish_test
from test_attempts import enter, item, save, submit_with

from conftest import Harness
from npa_api.grading.stats import Graded, Item, Question, Save, build_stats

T0 = datetime(2026, 11, 25, 9, tzinfo=UTC)
KEY = ["B", "A", "C", "D"]
OPTS = ["one", "two", "three", "four"]


def mcq(n: int, topic: str) -> Question:
    return Question(
        n, "multiple_choice", f"Q{n}", OPTS, topic, explanation=f"why {n}", explanation_source="ai"
    )


QUESTIONS = [mcq(1, "Tenses"), mcq(2, "Tenses"), mcq(3, "Conditionals"), mcq(4, "Conditionals")]


def student(
    sid: str, picks: str, *, flags: tuple[int, ...] = (), gap: int = 60, used: int | None = None
) -> Graded:
    items, saves = {}, {}
    for i, p in enumerate(picks, 1):
        given = None if p == "-" else p
        items[i] = Item(given, given == KEY[i - 1], [KEY[i - 1]])
        if given:
            saves[i] = Save(T0 + timedelta(seconds=gap * i), i in flags)
    correct = sum(it.correct for it in items.values())
    end = T0 + timedelta(seconds=used if used is not None else gap * len(picks))
    return Graded(sid, sid.title(), f"att-{sid}", T0, end, correct, 4, correct * 25, None, items, saves)


@pytest.fixture
def stats() -> dict:
    return build_stats(
        QUESTIONS,
        [
            student("ann", "BACC", flags=(4,)),  # 3/4, wrong on 4 with C
            student("bob", "BAAC", flags=(3,), gap=90),  # 2/4, shares C on 4
            student("cat", "BC--", gap=280, used=595),  # 1/4, ran out of time
        ],
        600,
    )


def test_question_percent_choices_and_trap(stats: dict) -> None:
    q4 = stats["questions"][3]
    assert q4["percent"] == 0
    assert q4["trap"] == {"value": "C", "count": 2}
    by_value = {c["value"]: c for c in q4["choices"]}
    assert by_value["D"]["isKey"] and by_value["D"]["count"] == 0
    assert by_value["C"]["students"] == ["Ann", "Bob"]
    assert q4["blank"] == ["Cat"]
    assert stats["questions"][0]["percent"] == 100
    assert stats["questions"][0]["explanation"] == {"text": "why 1", "source": "ai"}


def test_students_ranked_with_topics_and_mistake_kinds(stats: dict) -> None:
    names = [s["name"] for s in stats["students"]]
    assert names == ["Ann", "Bob", "Cat"]
    ann, bob, cat = stats["students"]
    assert ann["rank"] == 1 and cat["rank"] == 3
    assert ann["topics"] == [100, 50]
    assert cat["topics"] == [50, 0]
    assert ann["mistakes"] == {"shared": 1, "own": 0, "blank": 0}
    assert bob["mistakes"] == {"shared": 2, "own": 0, "blank": 0}
    assert cat["mistakes"] == {"shared": 0, "own": 1, "blank": 2}
    assert [a["othersMissed"] for a in ann["answers"]] == [0, 1, 2, 2]
    assert cat["trailingBlank"] == 2


def test_time_is_estimated_from_save_gaps(stats: dict) -> None:
    bob = stats["students"][1]
    assert [a["seconds"] for a in bob["answers"]] == [90, 90, 90, 90]
    assert bob["pace"] == [[0, 0], [90, 1], [180, 2], [270, 3], [360, 4]]
    assert stats["questions"][0]["avgSeconds"] == round((60 + 90 + 280) / 3)


def test_summary_insights(stats: dict) -> None:
    s = stats["summary"]
    assert s["mean"] == 2.0 and s["median"] == 2
    assert s["best"] == {"name": "Ann", "correct": 3}
    assert s["strongTopic"] == {"name": "Tenses", "percent": 83}
    assert s["weakTopic"] == {"name": "Conditionals", "percent": 17}
    assert s["topTrap"] == {"number": 4, "value": "C", "count": 2, "key": ["D"]}
    assert s["outOfTime"] == [{"name": "Cat", "blank": 2, "seconds": 595}]
    assert (s["flagTotal"], s["flagCorrect"]) == (2, 0)
    assert s["allCorrect"] == 1 and s["allWrong"] == 1


def test_discrimination_and_free_text_groups() -> None:
    q = Question(1, "short_answer", "", [], "Vocab")
    out = build_stats(
        [q],
        [
            Graded("a", "A", "1", T0, T0, 1, 1, 100, None, {1: Item("river", True, ["river"])}),
            Graded("b", "B", "2", T0, T0, 0, 1, 0, None, {1: Item("lake ", False, ["river"])}),
            Graded("c", "C", "3", T0, T0, 0, 1, 0, None, {1: Item("Lake", False, ["river"])}),
        ],
        600,
    )
    q1 = out["questions"][0]
    assert q1["discrimination"] == "good"
    assert [(c["value"], c["count"], c["isKey"]) for c in q1["choices"]] == [
        ("river", 1, True),
        ("lake", 2, False),
    ]
    assert q1["trap"] == {"value": "lake", "count": 2}


def test_no_graded_attempts() -> None:
    out = build_stats(QUESTIONS, [], 600)
    assert out["graded"] == 0 and out["summary"] is None and out["students"] == []


def test_stats_endpoint(h: Harness) -> None:
    h.create_user("stats-teacher@test.local", "teacher")
    teacher = h.login("stats-teacher@test.local")
    test_id = publish_test(h, teacher, answers={1: ["alpha"], 2: ["beta"]})
    class_id, students = make_class_with_students(h, teacher, 3)
    res = h.call(
        teacher,
        "POST",
        "/api/assignments",
        {
            "testId": test_id,
            "classId": class_id,
            "opensAt": "2026-11-22T04:00:00Z",
            "dueAt": "2026-11-23T04:00:00Z",
            "maxAttempts": 1,
            "mode": "exam",
        },
    )
    assert res.status == 201
    aid = res.json["id"]
    with h.later(hours=2):
        for s, values in zip(students[:2], (["alpha", "beta"], ["alpha", "gamma"]), strict=True):
            attempt, token = enter(h, s, aid)
            for n, v in enumerate(values, 1):
                assert save(h, s, attempt, token, item(n, v, n, marked=n == 2)).status == 200
            assert submit_with(h, s, attempt, token).status == 200
    with h.later(hours=3):
        out = h.call(teacher, "GET", f"/api/assignments/{aid}/stats")
    assert out.status == 200
    body = out.json
    assert body["graded"] == 2 and body["total"] == 2
    assert [q["percent"] for q in body["questions"]] == [100, 50]
    assert [s["correct"] for s in body["students"]] == [2, 1]
    assert body["summary"]["flagTotal"] == 2 and body["summary"]["flagCorrect"] == 1
    assert body["topics"][0]["name"] == "Passage 1"
    assert h.call(students[0].cookie, "GET", f"/api/assignments/{aid}/stats").status in (403, 404)
