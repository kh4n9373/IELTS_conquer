"""Class statistics for one assignment, built from graded attempts. Pure: no I/O.

Each student counts once, with their latest graded attempt. Time per question is an estimate:
the gap between one answer's last save and the save before it.
"""

from dataclasses import dataclass, field
from datetime import datetime
from statistics import median
from typing import Any

from ..explain.batch import canon, choices, key_values, lettered

TRAP_MIN = 2  # a wrong pick shared by at least this many students
TRAP_SHARE = 0.2  # and by at least this share of the graded students
STRONG, WEAK = 75, 50  # topic percent bands, same as the colours on the page
OUT_OF_TIME = 0.95  # used this share of the time or more


@dataclass(frozen=True)
class Question:
    number: int
    type: str
    prompt: str
    options: list[str]
    topic: str
    explanation: str | None = None
    explanation_source: str | None = None


@dataclass(frozen=True)
class Item:
    given: str | None
    correct: bool
    accepted: list[str]


@dataclass(frozen=True)
class Save:
    saved_at: datetime
    flagged: bool


@dataclass(frozen=True)
class Graded:
    student_id: str
    name: str
    attempt_id: str
    started_at: datetime
    ended_at: datetime
    correct: int
    total: int
    percent: float
    band: str | None
    items: dict[int, Item]
    saves: dict[int, Save] = field(default_factory=dict)


def _blank(given: str | None) -> bool:
    return given is None or not given.strip()


def _result(item: Item | None) -> str:
    if item is None or _blank(item.given):
        return "blank"
    return "ok" if item.correct else "bad"


def _pct(part: float, whole: float) -> int:
    return round(part / whole * 100) if whole else 0


def _seconds(g: Graded, duration: int) -> dict[int, int]:
    """Estimated seconds per answered question, from the order of the last saves."""
    out: dict[int, int] = {}
    prev = g.started_at
    for n, s in sorted(g.saves.items(), key=lambda kv: (kv[1].saved_at, kv[0])):
        if _blank(g.items[n].given if n in g.items else None):
            continue
        out[n] = max(0, min(duration, round((s.saved_at - prev).total_seconds())))
        prev = max(prev, s.saved_at)
    return out


def _pace(g: Graded, seconds: dict[int, int]) -> list[list[int]]:
    points, cum = [[0, 0]], 0
    order = sorted(seconds, key=lambda n: (g.saves[n].saved_at, n))
    for i, n in enumerate(order, 1):
        cum += seconds[n]
        points.append([cum, i])
    return points


def _choices(q: Question, graded: list[Graded], key: list[str]) -> list[dict[str, Any]]:
    """Each value students could pick or wrote, with who picked it. Fixed choices keep their order."""
    keys = key_values(q.type, key) if key else set()
    picks: dict[str, list[str]] = {}
    raw: dict[str, str] = {}
    for g in graded:
        item = g.items.get(q.number)
        if item is None or _blank(item.given):
            continue
        c = canon(q.type, item.given or "")
        picks.setdefault(c, []).append(g.name)
        raw.setdefault(c, (item.given or "").strip())
    fixed = choices(q.type, q.options) if q.type != "multiple_choice_multi" else []
    out: list[dict[str, Any]] = []
    if fixed:
        for i, value in enumerate(fixed):
            c = canon(q.type, value)
            text = q.options[i] if lettered(q.type) else ""
            out.append({"value": value, "text": text, "isKey": c in keys, "students": picks.pop(c, [])})
    elif key:  # free text: show the key even when nobody wrote it
        c = canon(q.type, key[0])
        out.append({"value": key[0], "text": "", "isKey": True, "students": picks.pop(c, [])})
    rest = sorted(picks.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    for c, names in rest:
        out.append({"value": raw[c], "text": "", "isKey": c in keys, "students": names})
    for o in out:
        o["count"] = len(o["students"])
    return out


def _discrimination(q: int, ranked: list[Graded]) -> str | None:
    """Whether the top third gets the question right more often than the bottom third."""
    n = len(ranked)
    if n < 2:
        return None
    size = max(1, n // 3)
    top = sum(_result(g.items.get(q)) == "ok" for g in ranked[:size]) / size
    low = sum(_result(g.items.get(q)) == "ok" for g in ranked[-size:]) / size
    d = top - low
    return "good" if d >= 0.3 else "reverse" if d < 0 else "flat"


def build_stats(questions: list[Question], graded: list[Graded], duration: int) -> dict[str, Any]:
    n = len(graded)
    questions = sorted(questions, key=lambda q: q.number)
    topics = list(dict.fromkeys(q.topic for q in questions))
    by_topic = {t: [q.number for q in questions if q.topic == t] for t in topics}
    ranked = sorted(graded, key=lambda g: (-g.correct, g.name.casefold(), g.student_id))
    seconds = {g.student_id: _seconds(g, duration) for g in graded}
    results = {g.student_id: {q.number: _result(g.items.get(q.number)) for q in questions} for g in graded}

    qs: list[dict[str, Any]] = []
    for q in questions:
        key = next((g.items[q.number].accepted for g in graded if q.number in g.items), [])
        opts = _choices(q, graded, key)
        ok = sum(results[g.student_id][q.number] == "ok" for g in graded)
        blank_names = [g.name for g in graded if results[g.student_id][q.number] == "blank"]
        times = [seconds[g.student_id][q.number] for g in graded if q.number in seconds[g.student_id]]
        wrong = [o for o in opts if not o["isKey"] and o["count"] >= max(TRAP_MIN, TRAP_SHARE * n)]
        trap = max(wrong, key=lambda o: o["count"], default=None)
        qs.append(
            {
                "number": q.number,
                "type": q.type,
                "topic": q.topic,
                "prompt": q.prompt,
                "key": key,
                "percent": _pct(ok, n),
                "correct": ok,
                "avgSeconds": round(sum(times) / len(times)) if times else None,
                "flags": sum(1 for g in graded if g.saves.get(q.number, Save(g.started_at, False)).flagged),
                "choices": opts,
                "blank": blank_names,
                "trap": {"value": trap["value"], "count": trap["count"]} if trap else None,
                "discrimination": _discrimination(q.number, ranked),
                "explanation": (
                    {"text": q.explanation, "source": q.explanation_source} if q.explanation else None
                ),
            }
        )
    by_number = {q["number"]: q for q in qs}

    def topic_pct(rs: dict[int, str], t: str) -> int:
        return _pct(sum(rs[x] == "ok" for x in by_topic[t]), len(by_topic[t]))

    students: list[dict[str, Any]] = []
    for g in ranked:
        rs, secs = results[g.student_id], seconds[g.student_id]
        shared = own = blanks = 0
        for x, r in rs.items():
            if r == "blank":
                blanks += 1
            elif r == "bad":
                others = [o for o in graded if o.student_id != g.student_id]
                if any(results[o.student_id][x] != "ok" for o in others):
                    shared += 1
                else:
                    own += 1
        trailing = 0
        for q in reversed(questions):
            if rs[q.number] != "blank":
                break
            trailing += 1
        used = max(0, min(duration, round((g.ended_at - g.started_at).total_seconds())))
        students.append(
            {
                "id": g.student_id,
                "name": g.name,
                "attemptId": g.attempt_id,
                "rank": 1 + sum(o.correct > g.correct for o in graded),
                "correct": g.correct,
                "total": g.total,
                "percent": float(g.percent),
                "band": g.band,
                "seconds": used,
                "blank": blanks,
                "trailingBlank": trailing,
                "topics": [topic_pct(rs, t) for t in topics],
                "mistakes": {"shared": shared, "own": own, "blank": blanks},
                "pace": _pace(g, secs),
                "answers": [
                    {
                        "number": q.number,
                        "given": (g.items[q.number].given or "").strip() if q.number in g.items else "",
                        "result": rs[q.number],
                        "flagged": q.number in g.saves and g.saves[q.number].flagged,
                        "seconds": secs.get(q.number),
                        # how many of the other students also missed it: 0 means only this one did
                        "othersMissed": sum(
                            results[o.student_id][q.number] != "ok" for o in graded if o is not g
                        ),
                    }
                    for q in questions
                ],
            }
        )

    class_topics = []
    for i, t in enumerate(topics):
        times = [by_number[x]["avgSeconds"] for x in by_topic[t] if by_number[x]["avgSeconds"] is not None]
        class_topics.append(
            {
                "name": t,
                "percent": round(sum(s["topics"][i] for s in students) / n) if n else 0,
                "avgSeconds": round(sum(times) / len(times)) if times else None,
                "questions": by_topic[t],
            }
        )

    return {
        "graded": n,
        "total": len(questions),
        "durationSeconds": duration,
        "summary": _summary(students, qs, class_topics, duration) if n else None,
        "topics": class_topics,
        "questions": qs,
        "students": students,
    }


def _summary(
    students: list[dict[str, Any]], qs: list[dict[str, Any]], topics: list[dict[str, Any]], duration: int
) -> dict[str, Any]:
    scores = [s["correct"] for s in students]
    total = students[0]["total"] or len(qs)
    flag_total = sum(a["flagged"] for s in students for a in s["answers"])
    flag_ok = sum(a["flagged"] and a["result"] == "ok" for s in students for a in s["answers"])
    traps = sorted((q for q in qs if q["trap"]), key=lambda q: (-q["trap"]["count"], q["number"]))
    short = [
        {"name": s["name"], "blank": s["trailingBlank"], "seconds": s["seconds"]}
        for s in students
        if s["trailingBlank"] > 0 and s["seconds"] >= duration * OUT_OF_TIME
    ]
    rated = [t for t in topics if t["questions"]]
    strong = max(rated, key=lambda t: t["percent"], default=None)
    weak = min(rated, key=lambda t: t["percent"], default=None)
    return {
        "mean": round(sum(scores) / len(scores), 1),
        "median": median(scores),
        "best": {"name": students[0]["name"], "correct": students[0]["correct"]},
        "worst": {"name": students[-1]["name"], "correct": students[-1]["correct"]},
        "avgSeconds": round(sum(s["seconds"] for s in students) / len(students)),
        "blank": sum(s["blank"] for s in students),
        "blankStudents": [s["name"] for s in students if s["blank"]],
        "passed": sum(s["correct"] * 2 >= total for s in students),
        "allCorrect": sum(q["percent"] == 100 for q in qs),
        "allWrong": sum(q["percent"] == 0 for q in qs),
        "flagTotal": flag_total,
        "flagCorrect": flag_ok,
        "strongTopic": {"name": strong["name"], "percent": strong["percent"]} if strong else None,
        "weakTopic": {"name": weak["name"], "percent": weak["percent"]} if weak else None,
        "topTrap": (
            {
                "number": traps[0]["number"],
                "value": traps[0]["trap"]["value"],
                "count": traps[0]["trap"]["count"],
                "key": traps[0]["key"],
            }
            if traps
            else None
        ),
        "outOfTime": short,
        "trapCount": len(traps),
        "bands": {"strong": STRONG, "weak": WEAK},
    }
