from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from fakes import make_pdf
from test_exam_keys import items_of, make, review
from test_ocr_worker import Clock, FakeProvider

from conftest import Harness
from npa_api.exams.key_import import KeyDraft, KeyLine, fits_question, parse_key_lines
from npa_api.exams.keys import parse_pasted_key
from npa_api.ocr.provider import OcrLine, OcrPage
from npa_api.ocr.worker import run_once

BBOX = (1.0, 2.0, 3.0, 4.0)


@pytest.fixture(scope="module")
def t(h: Harness) -> dict[str, Any]:
    a = h.create_user("imp-a@test.local", "teacher")
    return {"a": h.login("imp-a@test.local"), "id": a}


@pytest.fixture(autouse=True)
def clean(h: Harness) -> Iterator[None]:
    h.owner_rows("delete from ocr_jobs")
    yield


def line(text: str, confidence: float | None = None, *, scanned: bool = False) -> KeyLine:
    return KeyLine(text=text, page=1, bbox=None, confidence=confidence, scanned=scanned)


def one(text: str, confidence: float | None = None, *, scanned: bool = False) -> KeyDraft:
    drafts = parse_key_lines([line(text, confidence, scanned=scanned)])
    assert len(drafts) == 1
    return drafts[0]


def put_key(h: Harness, t: dict[str, Any], tid: str, pdf: bytes) -> None:
    h.client.cookies.clear()
    res = h.client.put(
        f"/api/tests/{tid}/key-source",
        content=pdf,
        headers={"content-type": "application/pdf", "cookie": t["a"]},
    )
    assert res.status_code == 200


def jobs(h: Harness, tid: str) -> list[dict[str, Any]]:
    return h.owner_rows(
        """select j.* from ocr_jobs j join test_assets a on a.id = j.asset_id
           where a.test_id = %s and a.kind = 'key_source'""",
        (tid,),
    )


def stored(h: Harness, tid: str) -> dict[int, dict[str, Any]]:
    rows = h.owner_rows(
        """select i.* from keys.answer_key_items i
           join keys.answer_key_versions v on v.id = i.version_id where v.test_id = %s""",
        (tid,),
    )
    return {r["question_number"]: r for r in rows}


def post(h: Harness, t: dict[str, Any], tid: str, action: str) -> Any:
    return h.call(t["a"], "POST", f"/api/tests/{tid}/key/{action}")


def scan_page(*lines: tuple[str, float | None]) -> OcrPage:
    ocr = [OcrLine(text=text, bbox=BBOX, confidence=conf) for text, conf in lines]
    return OcrPage(page=1, text="\n".join(text for text, _ in lines), lines=ocr)


def scanned_test(h: Harness, t: dict[str, Any]) -> str:
    tid = make(h, t)
    put_key(h, t, tid, make_pdf(1, text=None))
    return tid


def work(h: Harness, provider: Any) -> None:
    clock = Clock(h.now)
    assert run_once(h.db.app_pool, h.store, provider, clock, sleep=clock.sleep) is True


# ---- parsing ----


@pytest.mark.parametrize("text", ["1. E", "1) E", "1: E", "1 - E", "1 E"])
def test_parser_reads_common_layouts(text: str) -> None:
    draft = one(text)
    assert (draft.number, draft.answers, draft.flagged) == (1, ["E"], False)


@pytest.mark.parametrize("text", ["1. E", "1) E", "1: E", "1 - E", "1 E"])
def test_paste_reads_common_layouts(text: str) -> None:
    assert parse_pasted_key(text) == {1: ["E"]}


@pytest.mark.parametrize("text", ["1.5 km", "1:E", "1)E", "1 -E"])
def test_paste_requires_space_after_separator(text: str) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        parse_pasted_key(text)


def test_parser_ignores_decimal_and_prose_lines() -> None:
    lines = ["1.5 km", "Section 1 answers", "– 1 –", "12"]
    assert parse_key_lines([line(text) for text in lines]) == []


def test_parser_reads_two_columns_on_one_line() -> None:
    drafts = parse_key_lines([line("4 alpha 8 not given")])
    assert [(d.number, d.answers) for d in drafts] == [(4, ["alpha"]), (8, ["not given"])]


def test_parser_keeps_smaller_number_inside_answer() -> None:
    assert one("23 platform 3").answers == ["platform 3"]


def test_parser_splits_answer_variants() -> None:
    assert one("23. three; 3").answers == ["three", "3"]


def test_low_confidence_answer_is_flagged() -> None:
    assert one("1. E", 0.89, scanned=True).flagged is True
    assert one("1. E", 0.90, scanned=True).flagged is False


def test_scan_line_without_confidence_is_flagged() -> None:
    assert one("1. E", None, scanned=True).flagged is True
    assert one("1. E", float("nan"), scanned=True).flagged is True


def test_text_line_without_confidence_is_not_flagged() -> None:
    assert one("1. E", None).flagged is False


def test_empty_answer_is_flagged() -> None:
    draft = one("7.")
    assert (draft.number, draft.answers, draft.flagged) == (7, [], True)


def test_duplicate_question_number_is_flagged() -> None:
    drafts = parse_key_lines([line("3. A"), line("3. B")])
    assert len(drafts) == 1
    assert drafts[0].flagged is True


def test_duplicate_also_flags_pairs_sharing_its_line() -> None:
    drafts = parse_key_lines([line("2 room 5 east"), line("5 B")])
    assert {d.number: d.flagged for d in drafts} == {2: True, 5: True}


def test_trailing_larger_number_is_flagged() -> None:
    draft = one("4 alpha 8")
    assert (draft.answers, draft.flagged) == (["alpha 8"], True)
    assert one("23 platform 3").flagged is False


@pytest.mark.parametrize("text", ["5 east 5 west", "10 alpha 5 beta", "4 alpha 8 beta 6 gamma"])
def test_not_larger_numbered_pair_is_flagged(text: str) -> None:
    drafts = parse_key_lines([line(text)])
    assert drafts[-1].flagged is True


@pytest.mark.parametrize("text", ["4. alpha 8.", "4) alpha 8)", "4 alpha 8:", "4 alpha 8 -", "10 alpha 5."])
def test_punctuated_trailing_number_is_flagged(text: str) -> None:
    assert one(text).flagged is True


def test_empty_first_column_is_flagged() -> None:
    assert one("1. 2. beta").flagged is True


@pytest.mark.parametrize("text", ["23 platform 3", "23. platform 3", "2 5 km", "2 5"])
def test_real_numeric_answers_stay_clean(text: str) -> None:
    assert one(text).flagged is False


def test_tfng_value_outside_allowed_set_is_flagged() -> None:
    for value in ("TRUE", "false", "Not Given", "T", "f", "NG"):
        assert fits_question([value], "tfng", None)
    for value in ("YES", "maybe"):
        assert not fits_question([value], "tfng", None)


def test_ynng_allowed_values() -> None:
    for value in ("YES", "no", "NOT GIVEN", "Y", "n", "NG"):
        assert fits_question([value], "ynng", None)
    assert not fits_question(["TRUE"], "ynng", None)


@pytest.mark.parametrize(
    "kind", ["multiple_choice", "matching_features", "matching_information", "sentence_endings"]
)
def test_letter_types_take_a_single_letter(kind: str) -> None:
    assert fits_question(["c"], kind, None)
    assert not fits_question(["CD"], kind, None)
    assert not fits_question(["1"], kind, None)


def test_headings_take_a_roman_numeral() -> None:
    for value in ("iv", "VIII", "x"):
        assert fits_question([value], "matching_headings", None)
    for value in ("A", "iiii", "vx"):
        assert not fits_question([value], "matching_headings", None)


def test_word_limit_is_enforced() -> None:
    assert fits_question(["two words"], "sentence_completion", 2)
    assert not fits_question(["three whole words"], "sentence_completion", 2)


# ---- import route ----


def test_text_layer_import_never_calls_provider(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t)
    put_key(h, t, tid, make_pdf(1, text="1. TRUE 2. FALSE"))
    res = post(h, t, tid, "import")
    assert res.status == 200
    assert res.json == {"filled": 2, "flagged": 0, "kept": 0, "unmatched": 0, "conflicts": []}
    assert jobs(h, tid) == []


def test_imported_answers_stay_unreviewed(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t)
    put_key(h, t, tid, make_pdf(1, text="1. TRUE"))
    post(h, t, tid, "import")
    row = stored(h, tid)[1]
    assert (row["origin"], row["reviewed_at"], row["source_page"]) == ("text_layer", None, 1)
    item = items_of(h, t, tid)[1]
    assert (item["reviewed"], item["sourcePage"]) == (False, 1)


def test_import_counts_flagged_unmatched_and_empty(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t)
    put_key(h, t, tid, make_pdf(1, text="1. maybe 2. TRUE 99. Z"))
    res = post(h, t, tid, "import")
    # 99 is no question here, so every answer read from that line is suspect.
    assert res.json == {"filled": 2, "flagged": 2, "kept": 0, "unmatched": 1, "conflicts": []}
    rows = stored(h, tid)
    assert (rows[1]["answers"], rows[1]["flagged"]) == (["maybe"], True)
    assert rows[2]["flagged"] is True
    assert 99 not in rows


def test_import_keeps_manual_and_reviewed(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t)
    body = {"items": [{"number": 1, "answers": ["F"]}, {"number": 2, "answers": ["F"]}]}
    assert h.call(t["a"], "PUT", f"/api/tests/{tid}/key/items", body).status == 200
    review(h, t, tid, [2])
    put_key(h, t, tid, make_pdf(1, text="1. TRUE 2. TRUE 3. TRUE"))
    res = post(h, t, tid, "import")
    assert res.json == {"filled": 1, "flagged": 0, "kept": 2, "unmatched": 0, "conflicts": [2]}
    rows = stored(h, tid)
    assert (rows[1]["answers"], rows[2]["answers"]) == (["F"], ["F"])
    assert rows[2]["reviewed_at"] is not None


def test_import_replaces_unreviewed_imported_draft(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t)
    put_key(h, t, tid, make_pdf(1, text="1. FALSE"))
    post(h, t, tid, "import")
    put_key(h, t, tid, make_pdf(1, text="1. TRUE"))
    assert post(h, t, tid, "import").json["filled"] == 1
    assert stored(h, tid)[1]["answers"] == ["TRUE"]


def test_manual_change_clears_import_source(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t)
    put_key(h, t, tid, make_pdf(1, text="1. TRUE"))
    post(h, t, tid, "import")
    body = {"items": [{"number": 1, "answers": ["FALSE"]}]}
    assert h.call(t["a"], "PUT", f"/api/tests/{tid}/key/items", body).status == 200
    row = stored(h, tid)[1]
    assert (row["origin"], row["source_page"], row["source_bbox"]) == ("manual", None, None)


def test_retyped_identical_value_becomes_manual_and_survives_import(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t)
    put_key(h, t, tid, make_pdf(1, text="1. TRUE"))
    post(h, t, tid, "import")
    body = {"items": [{"number": 1, "answers": ["TRUE"]}]}
    assert h.call(t["a"], "PUT", f"/api/tests/{tid}/key/items", body).status == 200
    assert stored(h, tid)[1]["origin"] == "manual"
    put_key(h, t, tid, make_pdf(1, text="1. FALSE"))
    res = post(h, t, tid, "import")
    assert res.json == {"filled": 0, "flagged": 0, "kept": 1, "unmatched": 0, "conflicts": []}
    row = stored(h, tid)[1]
    assert (row["answers"], row["origin"]) == (["TRUE"], "manual")


def test_retyping_keeps_review_only_when_identical(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t)
    put_key(h, t, tid, make_pdf(1, text="1. TRUE 2. TRUE"))
    post(h, t, tid, "import")
    review(h, t, tid, [1, 2])
    body = {"items": [{"number": 1, "answers": ["TRUE"]}, {"number": 2, "answers": ["FALSE"]}]}
    assert h.call(t["a"], "PUT", f"/api/tests/{tid}/key/items", body).status == 200
    rows = stored(h, tid)
    assert (rows[1]["origin"], rows[2]["origin"]) == ("manual", "manual")
    assert rows[1]["reviewed_at"] is not None
    assert rows[2]["reviewed_at"] is None


def test_empty_reimport_flags_old_draft(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t)
    put_key(h, t, tid, make_pdf(1, text="1. TRUE"))
    post(h, t, tid, "import")
    put_key(h, t, tid, make_pdf(1, text="1."))
    res = post(h, t, tid, "import")
    assert res.json == {"filled": 0, "flagged": 1, "kept": 0, "unmatched": 0, "conflicts": []}
    row = stored(h, tid)[1]
    assert (row["answers"], row["flagged"], row["reviewed_at"]) == (["TRUE"], True, None)
    assert row["source_page"] == 1


def test_empty_reimport_leaves_manual_and_reviewed_clean(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t)
    body = {"items": [{"number": 1, "answers": ["TRUE"]}]}
    assert h.call(t["a"], "PUT", f"/api/tests/{tid}/key/items", body).status == 200
    put_key(h, t, tid, make_pdf(1, text="1."))
    assert post(h, t, tid, "import").json["kept"] == 1
    assert stored(h, tid)[1]["flagged"] is False


def test_import_audit_has_no_answer_text(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t)
    put_key(h, t, tid, make_pdf(1, text="14. zebrafish"))
    post(h, t, tid, "import")
    rows = h.owner_rows("select * from audit_log where action = 'key.import' and entity_id = %s", (tid,))
    assert len(rows) == 1
    assert "zebrafish" not in str(rows[0])


def test_import_errors(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t)
    res = post(h, t, tid, "import")
    assert (res.status, res.json["error"]) == (409, "key_source_missing")
    put_key(h, t, tid, make_pdf(1, text=None))
    res = post(h, t, tid, "import")
    assert (res.status, res.json["error"]) == (409, "key_no_text_layer")
    assert jobs(h, tid) == []
    h.owner_rows("update tests set status = 'published' where id = %s", (tid,))
    res = post(h, t, tid, "import")
    assert (res.status, res.json["error"]) == (409, "test_locked")


# ---- scan route ----


def test_scan_on_text_layer_pdf_returns_409_key_has_text_layer(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t)
    put_key(h, t, tid, make_pdf(1, text="1. TRUE"))
    res = post(h, t, tid, "scan")
    assert (res.status, res.json["error"]) == (409, "key_has_text_layer")
    assert jobs(h, tid) == []


def test_scan_errors(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t)
    res = post(h, t, tid, "scan")
    assert (res.status, res.json["error"]) == (409, "key_source_missing")
    put_key(h, t, tid, make_pdf(1, text=None))
    h.owner_rows("update tests set status = 'published' where id = %s", (tid,))
    res = post(h, t, tid, "scan")
    assert (res.status, res.json["error"]) == (409, "test_locked")


def test_scan_runs_only_on_explicit_request(h: Harness, t: dict[str, Any]) -> None:
    tid = scanned_test(h, t)
    assert jobs(h, tid) == []
    res = post(h, t, tid, "scan")
    assert res.status == 202
    assert [j["state"] for j in jobs(h, tid)] == ["queued"]
    audit = h.owner_rows("select 1 from audit_log where action = 'key.scan' and entity_id = %s", (tid,))
    assert len(audit) == 1


@pytest.mark.parametrize("state", ["queued", "running"])
def test_scan_in_progress_returns_409(h: Harness, t: dict[str, Any], state: str) -> None:
    tid = scanned_test(h, t)
    post(h, t, tid, "scan")
    h.owner_rows("update ocr_jobs set state = %s", (state,))
    res = post(h, t, tid, "scan")
    assert (res.status, res.json["error"]) == (409, "ocr_in_progress")


@pytest.mark.parametrize("state", ["failed", "done"])
def test_scan_replaces_finished_job(h: Harness, t: dict[str, Any], state: str) -> None:
    tid = scanned_test(h, t)
    post(h, t, tid, "scan")
    first = jobs(h, tid)[0]["id"]
    h.owner_rows("update ocr_jobs set state = %s", (state,))
    assert post(h, t, tid, "scan").status == 202
    after = jobs(h, tid)
    assert len(after) == 1
    assert after[0]["id"] != first
    assert after[0]["state"] == "queued"


def key_scan(h: Harness, t: dict[str, Any], tid: str) -> Any:
    res = h.call(t["a"], "GET", f"/api/tests/{tid}/key")
    assert res.status == 200
    return res.json["scan"]


def test_key_scan_status_is_null_without_job(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t)
    assert key_scan(h, t, tid) is None
    put_key(h, t, tid, make_pdf(1, text=None))
    assert key_scan(h, t, tid) is None


def test_key_scan_status_ignores_student_pdf_job(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t, pdf=False)
    h.client.cookies.clear()
    res = h.client.put(
        f"/api/tests/{tid}/pdf",
        content=make_pdf(1),
        headers={"content-type": "application/pdf", "cookie": t["a"]},
    )
    assert res.status_code == 200
    assert key_scan(h, t, tid) is None


def test_key_scan_status_queued(h: Harness, t: dict[str, Any]) -> None:
    tid = scanned_test(h, t)
    post(h, t, tid, "scan")
    assert key_scan(h, t, tid) == {"state": "queued", "errorCode": None}


def test_key_scan_status_failed_exposes_only_state_and_code(h: Harness, t: dict[str, Any]) -> None:
    tid = scanned_test(h, t)
    post(h, t, tid, "scan")
    h.owner_rows(
        """update ocr_jobs set state = 'failed', error_code = 'bad_result',
             provider_job_id = 'private-provider-id', lease_until = %s""",
        (h.now,),
    )
    assert key_scan(h, t, tid) == {"state": "failed", "errorCode": "bad_result"}
    assert "private-provider-id" not in str(h.call(t["a"], "GET", f"/api/tests/{tid}/key").json)


# ---- worker ----


def test_worker_stores_scan_drafts_and_no_page_texts(h: Harness, t: dict[str, Any]) -> None:
    tid = scanned_test(h, t)
    post(h, t, tid, "scan")
    asset = jobs(h, tid)[0]["asset_id"]
    work(h, FakeProvider(result=[scan_page(("1. TRUE", 0.99), ("2. FALSE", 0.5), ("99. Z", 0.99))]))
    assert jobs(h, tid)[0]["state"] == "done"
    assert h.owner_rows("select 1 from page_texts where asset_id = %s", (asset,)) == []
    rows = stored(h, tid)
    assert set(rows) == {1, 2}
    assert (rows[1]["origin"], rows[1]["flagged"], rows[1]["reviewed_at"]) == ("scan", False, None)
    assert (rows[1]["source_page"], rows[1]["source_bbox"]) == (1, list(BBOX))
    assert rows[2]["flagged"] is True


def test_rescan_keeps_reviewed_answers(h: Harness, t: dict[str, Any]) -> None:
    tid = scanned_test(h, t)
    post(h, t, tid, "scan")
    work(h, FakeProvider(result=[scan_page(("1. TRUE", 0.99), ("2. TRUE", 0.99))]))
    review(h, t, tid, [1])
    assert post(h, t, tid, "scan").status == 202
    work(h, FakeProvider(result=[scan_page(("1. FALSE", 0.99), ("2. FALSE", 0.99))]))
    rows = stored(h, tid)
    assert rows[1]["answers"] == ["TRUE"]
    assert rows[1]["reviewed_at"] is not None
    assert rows[2]["answers"] == ["FALSE"]


def test_empty_rescan_replaces_old_source_region(h: Harness, t: dict[str, Any]) -> None:
    tid = scanned_test(h, t)
    post(h, t, tid, "scan")
    work(h, FakeProvider(result=[scan_page(("1. TRUE", 0.99))]))
    assert stored(h, tid)[1]["source_bbox"] == list(BBOX)
    assert post(h, t, tid, "scan").status == 202
    blank = OcrPage(page=1, text="1.", lines=[OcrLine(text="1.", bbox=None, confidence=0.99)])
    work(h, FakeProvider(result=[blank]))
    row = stored(h, tid)[1]
    assert (row["flagged"], row["source_page"], row["source_bbox"]) == (True, 1, None)


def test_worker_skips_provider_when_test_already_locked(h: Harness, t: dict[str, Any]) -> None:
    tid = scanned_test(h, t)
    post(h, t, tid, "scan")
    h.owner_rows("update tests set status = 'published' where id = %s", (tid,))
    provider = FakeProvider(result=[scan_page(("1. TRUE", 0.99))])
    work(h, provider)
    job = jobs(h, tid)[0]
    assert (job["state"], job["error_code"]) == ("failed", "test_locked")
    assert provider.submitted == []
    assert stored(h, tid) == {}


def test_worker_writes_nothing_when_test_locks_during_scan(h: Harness, t: dict[str, Any]) -> None:
    tid = scanned_test(h, t)
    post(h, t, tid, "scan")

    class Publishes(FakeProvider):
        def pages(self, status: Any) -> list[OcrPage]:
            h.owner_rows("update tests set status = 'published' where id = %s", (tid,))
            return super().pages(status)

    work(h, Publishes(result=[scan_page(("1. TRUE", 0.99))]))
    job = jobs(h, tid)[0]
    assert (job["state"], job["error_code"], job["attempts"]) == ("failed", "test_locked", 1)
    assert stored(h, tid) == {}


def test_stale_worker_is_fenced_after_source_replaced(h: Harness, t: dict[str, Any]) -> None:
    tid = scanned_test(h, t)
    post(h, t, tid, "scan")

    class Replaces(FakeProvider):
        def pages(self, status: Any) -> list[OcrPage]:
            put_key(h, t, tid, make_pdf(1, text=None))
            return super().pages(status)

    work(h, Replaces(result=[scan_page(("1. TRUE", 0.99))]))
    assert stored(h, tid) == {}


def test_lost_lease_writes_no_drafts(h: Harness, t: dict[str, Any]) -> None:
    tid = scanned_test(h, t)
    post(h, t, tid, "scan")

    class Loses(FakeProvider):
        def pages(self, status: Any) -> list[OcrPage]:
            h.owner_rows("update ocr_jobs set attempts = attempts + 1")
            return super().pages(status)

    work(h, Loses(result=[scan_page(("1. TRUE", 0.99))]))
    assert stored(h, tid) == {}


def test_partial_scan_writes_nothing(h: Harness, t: dict[str, Any]) -> None:
    tid = make(h, t)
    put_key(h, t, tid, make_pdf(2, text=None))
    post(h, t, tid, "scan")
    work(h, FakeProvider(result=[scan_page(("1. TRUE", 0.99))]))
    job = jobs(h, tid)[0]
    assert (job["state"], job["error_code"]) == ("queued", "bad_result")
    assert job["lease_until"] >= h.now + timedelta(seconds=1)
    assert stored(h, tid) == {}
