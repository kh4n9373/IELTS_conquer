# M2 Exam Authoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A teacher can create the full 5-passage, 40-question pilot test, and the system refuses to publish it while any question lacks an answer, a question type or a review.

**Architecture:** New tenant tables under RLS, with answer keys in a separate `keys` schema that the student role cannot reach. PDFs live in S3-compatible private storage. A small Python worker claims OCR jobs from PostgreSQL and calls the PaddleOCR API through a replaceable provider interface.

**Tech Stack:** Python 3.13, FastAPI, psycopg, PostgreSQL 17, boto3, MinIO (dev), pypdfium2, httpx, pytest, React, PDF.js.

**Spec:** `DEV/docs/specs/2026-11-23-m2-exam-authoring-design.md`, with `BA/PRODUCT_SPEC.md` sections 6.3 and 6.4 and `BA/PILOT_EXAM_KEY.md`.

## Global Constraints

- Run backend commands from `DEV/backend`. Every task ends with `uv run ruff check . && uv run mypy && uv run pytest` passing.
- Commits contain no Claude co-author or generated-by lines.
- `DEV/exam/` is never committed, copied into an image or used as a committed fixture.
- Another tenant's object returns 404. Every new route is added to `MATRIX` in `tests/test_tenant_matrix.py`.
- Students get no grant on any M2 table. Student delivery arrives in M3.
- `BAIDU_OCR_ACCESS_TOKEN` never appears in logs, responses, audit rows, job errors or git.
- Only the PDF bytes go to OCR, under an opaque `<uuid>.pdf` filename. Never send a test title, teacher or student data.
- A key PDF with a text layer is parsed locally and is never sent to OCR.
- OCR state never affects publish readiness.
- Scan output is a draft. Only a teacher review marks an answer approved.
- Course tests store no band data.
- PDF and OCR text are untrusted data. Render OCR text only as plain text.
- UI work starts only after the PO approves the M2 screens on the canvas (Task 8).

## Review Focus

- A key edit after review clears that question's approval, so publish is blocked again (Task 3).
- A rescan never overwrites an answer the teacher already reviewed (Task 6).
- An anonymous client cannot make the API buffer a large upload (Task 4).
- A worker crash mid-job leaves the job reclaimable, and repeated failure ends as `failed` without blocking publish (Task 5).
- Replacing the student PDF after pages were mapped cannot leave a page number beyond the new page count (Task 4).

---

### Task 1: Schema, grants and RLS

**Files:**
- Create: `npa_api/migrations/0003_exam_authoring.sql`
- Test: `tests/test_exam_rls.py`

**Interfaces:**
- Produces tables: `tests`, `test_assets`, `sections`, `questions`, `page_texts`, `ocr_jobs`, `keys.answer_key_versions`, `keys.answer_key_items`.
- Produces definer functions, executable only by `npa_app`: `app.ocr_claim(p_now timestamptz, p_lease_until timestamptz)`, `app.ocr_finish(p_job uuid, p_state text, p_error text, p_now timestamptz)`.

Required columns and values:

| Table | Columns |
|---|---|
| `tests` | `id, teacher_id, title, test_type ('ielts_standard','course'), source_type ('self_authored','licensed','commercial','other'), status ('draft','published','archived') default 'draft', duration_minutes (1..300), published_at, created_at, unique (id, teacher_id)` |
| `test_assets` | `id, test_id, teacher_id, kind ('student_pdf','key_source'), storage_key, sha256, byte_size, page_count, has_text_layer, created_at, unique (test_id, kind)` |
| `sections` | `id, test_id, teacher_id, position, title, suggested_minutes, passage_pages int[], question_pages int[], units_primary text[], units_secondary text[], unique (test_id, position)` |
| `questions` | `id, test_id, teacher_id, section_id, number, question_type, word_limit, unique (test_id, number)` |
| `page_texts` | `asset_id, teacher_id, page, text, primary key (asset_id, page)` |
| `ocr_jobs` | `id, asset_id, teacher_id, state ('queued','running','done','failed'), attempts, lease_until, provider_job_id, error_code, created_at, finished_at` |
| `keys.answer_key_versions` | `id, test_id, teacher_id, version, approved_at, approved_by, unique (test_id, version)` |
| `keys.answer_key_items` | `version_id, teacher_id, question_number, answers text[], origin ('manual','text_layer','scan'), flagged, source_page, source_bbox jsonb, reviewed_at, reviewed_by, primary key (version_id, question_number)` |

`question_type` allows: `matching_features, matching_headings, matching_information, sentence_endings, multiple_choice, tfng, ynng, table_completion, note_completion, summary_completion, sentence_completion, short_answer, diagram_label`.

- [ ] **Step 1: Write failing tests**

```python
def test_student_role_cannot_use_keys_schema(h): ...      # InsufficientPrivilege on select
def test_student_role_has_no_grant_on_tests(h): ...       # InsufficientPrivilege
def test_teacher_b_sees_no_teacher_a_rows(h): ...         # every new table returns 0 rows
def test_teacher_cannot_insert_for_other_teacher(h): ...  # RLS violation
def test_course_and_tenant_fks_reject_cross_tenant_section(h): ...
def test_ocr_claim_not_executable_by_teacher_role(h): ...
```

- [ ] **Step 2: Run `uv run pytest tests/test_exam_rls.py -v`. Expected: FAIL, relations missing.**
- [ ] **Step 3: Write the migration.** Use composite foreign keys on `(test_id, teacher_id)` as `class_members` does. Grant `select, insert, update, delete` to `npa_teacher` only. `app.ocr_claim` uses `for update skip locked`, claims `queued` jobs or `running` jobs with an expired lease, increments `attempts`, and returns `id, asset_id, teacher_id, attempts, provider_job_id`.
- [ ] **Step 4: Run the tests. Expected: PASS.**
- [ ] **Step 5: Commit** `feat: add exam authoring schema with tenant isolation`

---

### Task 2: Test and structure API

**Files:**
- Create: `npa_api/exams/__init__.py`, `npa_api/exams/types.py`, `npa_api/exams/readiness.py`, `npa_api/routes/tests.py`
- Modify: `npa_api/app.py:15,97`, `tests/test_tenant_matrix.py`
- Test: `tests/test_exam_structure.py`

**Interfaces:**
- Produces: `QUESTION_GROUPS: dict[str, Literal["matching","completion","multiple_choice","tfng"]]` in `exams/types.py`
- Produces: `readiness(test, assets, sections, questions, items) -> list[str]` in `exams/readiness.py`, pure, returning ordered blocker codes
- Produces routes: `GET, POST /api/tests`, `GET, PATCH /api/tests/{id}`, `PUT /api/tests/{id}/structure`, `GET /api/tests/{id}/readiness`

Blocker codes: `missing_pdf`, `no_sections`, `page_out_of_range`, `question_gap`, `missing_type`, `missing_answer`, `unreviewed_answer`, `ielts_shape`.

`ielts_shape` applies only to `ielts_standard` and requires 3 sections, 40 questions, 60 minutes. Do not enforce a 13/13/14 split.

- [ ] **Step 1: Write failing tests**

```python
def test_range_assignment_sets_type_for_questions_1_to_6(h): ...
def test_structure_rejects_overlapping_question_ranges(h): ...  # 400 invalid_input
def test_course_test_allows_5_passages_120_minutes(h): ...
def test_ielts_test_with_5_passages_reports_ielts_shape(h): ...
def test_pilot_structure_groups_to_20_10_6_4(): ...
def test_every_test_requires_a_source_type(h): ...
```

- [ ] **Step 2: Run them. Expected: FAIL.**
- [ ] **Step 3: Implement the routes and pure readiness check.** `PUT structure` replaces sections and questions in one transaction and accepts `{sections: [{title, suggestedMinutes, passagePages, questionPages, unitsPrimary, unitsSecondary, ranges: [{from, to, questionType, wordLimit}]}]}`. It returns 409 `test_locked` unless the test is a draft.
- [ ] **Step 4: Run the full suite. Expected: PASS, including the tenant matrix.**
- [ ] **Step 5: Commit** `feat: add test structure API and publish readiness`

---

### Task 3: Answer key, review and publish

**Files:**
- Create: `npa_api/exams/keys.py`, `npa_api/routes/test_keys.py`
- Modify: `npa_api/app.py`, `npa_api/routes/tests.py`, `tests/test_tenant_matrix.py`
- Test: `tests/test_exam_keys.py`

**Interfaces:**
- Produces: `parse_pasted_key(text: str) -> dict[int, list[str]]`. Accepts `1. E`, `1 E`, `1) E`, and `;` between variants
- Produces: `upsert_items(tx, version_id, items, origin) -> None`
- Produces routes: `GET /api/tests/{id}/key`, `PUT /api/tests/{id}/key/items`, `POST /api/tests/{id}/key/review`, `POST /api/tests/{id}/publish`, `POST /api/tests/{id}/archive`

- [ ] **Step 1: Write failing tests**

```python
def test_paste_parses_variants_for_question_23():
    assert parse_pasted_key("23. three; platform three; 3; platform 3")[23] == [
        "three", "platform three", "3", "platform 3"]
def test_publish_blocked_when_one_answer_missing(h): ...    # 409 not_ready + blockers
def test_publish_blocked_when_one_type_missing(h): ...
def test_publish_blocked_when_one_answer_unreviewed(h): ...
def test_editing_reviewed_answer_clears_review(h): ...
def test_publish_succeeds_for_40_reviewed_questions_without_ocr(h): ...
def test_published_test_rejects_key_and_structure_edits(h): ...  # 409 test_locked
def test_key_edit_review_publish_write_audit_rows(h): ...
```

- [ ] **Step 2: Run them. Expected: FAIL.**
- [ ] **Step 3: Implement.** Publish locks the test row, recomputes readiness in the same transaction, and sets `approved_at` and `approved_by` on the key version. Audit actions: `key.update`, `key.review`, `test.publish`, `test.archive`.
- [ ] **Step 4: Run the full suite. Expected: PASS.**
- [ ] **Step 5: Commit** `feat: add answer key review and publish gate`

---

### Task 4: PDF upload and private storage

**Files:**
- Create: `npa_api/core/storage.py`, `npa_api/exams/pdf.py`, `npa_api/routes/test_files.py`, `tests/fakes.py`
- Modify: `npa_api/app.py`, `npa_api/core/http.py` (`Ctx.store`), `npa_api/server.py`, `tests/conftest.py`, `tests/test_tenant_matrix.py`, `docker-compose.yml`, `.env.example`, `pyproject.toml`
- Test: `tests/test_exam_files.py`

**Interfaces:**
- Produces: `FileStore` protocol with `put(key: str, path: Path) -> None`, `get(key: str, path: Path) -> None`, `delete(key: str) -> None`, `signed_url(key: str, seconds: int) -> str`
- Produces: `S3FileStore`, and `MemoryFileStore` in `tests/fakes.py`
- Produces: `inspect_pdf(path: Path) -> PdfInfo(page_count: int, has_text_layer: bool, sha256: str)`, raising `PdfRejected(code)` with `not_pdf`, `encrypted_pdf`, `too_many_pages` or `corrupt_pdf`
- Produces routes: `PUT /api/tests/{id}/pdf`, `PUT /api/tests/{id}/key-source`, `GET /api/tests/{id}/pdf-url`

Limits: 30 MB and 100 pages. Signed URLs last 300 seconds. Storage keys are `tests/<test_id>/<uuid>.pdf`.

- [ ] **Step 1: Write failing tests**

```python
def test_anonymous_large_upload_gets_401_without_reading_body(h): ...
def test_upload_over_30mb_returns_413_and_stores_nothing(h): ...
def test_non_pdf_returns_not_pdf(h): ...
def test_encrypted_pdf_returns_encrypted_pdf(h): ...
def test_image_pdf_has_no_text_layer_and_text_pdf_does(tmp_path): ...
def test_replacing_pdf_reports_page_out_of_range_for_old_mapping(h): ...
def test_other_routes_keep_64kb_limit(h): ...
```

- [ ] **Step 2: Run them. Expected: FAIL.**
- [ ] **Step 3: Implement.** Uploads use a raw `application/pdf` body, so no multipart parser is added. Exempt only these two `PUT` paths from `BodyLimit`. The async handler reads `request.stream()` into a temporary file and stops at the limit. `GuardedRoute` has already authorized the caller. Use `pypdfium2` for inspection. Add a private `minio` service to Compose, bound to `127.0.0.1`.
- [ ] **Step 4: Run the full suite. Expected: PASS.**
- [ ] **Step 5: Commit** `feat: add private PDF upload and validation`

---

### Task 5: PaddleOCR provider and worker

**Files:**
- Create: `npa_api/ocr/__init__.py`, `npa_api/ocr/provider.py`, `npa_api/ocr/paddle.py`, `npa_api/ocr/worker.py`, `tests/fixtures/paddle_result.jsonl`
- Modify: `npa_api/routes/test_files.py`, `pyproject.toml`, `.env.example`, `tests/test_tenant_matrix.py`
- Test: `tests/test_ocr_provider.py`, `tests/test_ocr_worker.py`

**Interfaces:**
- Produces: `OcrProvider` protocol with `submit(path: Path) -> str`, `status(job_id: str) -> OcrStatus`, `pages(status: OcrStatus) -> list[OcrPage]`
- Produces: `OcrPage(page: int, text: str, lines: list[OcrLine])`, `OcrLine(text: str, bbox: tuple[float, float, float, float] | None, confidence: float | None)`
- Produces: `PaddleOcrProvider(token: str, client: httpx.Client)`
- Produces: `run_once(pool, store, provider, now) -> bool`, the `npa-worker` script, and route `POST /api/tests/{id}/ocr`

API contract from Baidu's docs, unverified until Step 1: `POST https://paddleocr.aistudio-app.com/api/v2/ocr/jobs` with `Authorization: Bearer <token>`, multipart `file`, and `model=PaddleOCR-VL-1.5`. Poll `GET /api/v2/ocr/jobs/{jobId}` for `pending`, `running`, `done` or `failed`. On `done`, download JSONL from `resultUrl.jsonUrl`.

- [ ] **Step 1: Verify the live contract with a synthetic one-page PDF.** Generate it in the scratchpad with invented text. Never use the exam. Record the real response shape, then commit only a sanitized `tests/fixtures/paddle_result.jsonl`.
- [ ] **Step 2: Write failing tests**

```python
def test_submit_sends_bearer_token_and_opaque_filename(): ...
def test_provider_errors_never_contain_token(): ...
def test_result_url_must_be_https_and_not_redirect(): ...
def test_result_download_stops_at_size_limit(): ...
def test_worker_stores_page_texts_on_success(h): ...
def test_expired_lease_is_reclaimed_without_second_submit(h): ...
def test_third_failure_marks_failed_and_publish_still_works(h): ...
def test_quota_429_is_retryable_with_error_code(h): ...
```

- [ ] **Step 3: Run them. Expected: FAIL.**
- [ ] **Step 4: Implement.** The worker uses the claim function, then writes under `with_identity(pool, Identity(teacher_id, "teacher", teacher_id))`, so RLS still applies. Maximum 3 attempts. Store only fixed error codes. Student PDF upload enqueues a job in the same transaction.
- [ ] **Step 5: Run the full suite. Expected: PASS.**
- [ ] **Step 6: Commit** `feat: add PaddleOCR worker for uploaded tests`

---

### Task 6: Key import from text layer and scan

**Files:**
- Create: `npa_api/exams/key_import.py`
- Modify: `npa_api/routes/test_keys.py`, `npa_api/ocr/worker.py`, `tests/test_tenant_matrix.py`
- Test: `tests/test_key_import.py`, `tests/test_pilot_key.py`

**Interfaces:**
- Consumes: `OcrPage`, `OcrProvider`, `inspect_pdf`, `upsert_items`
- Produces: `parse_key_lines(lines: list[KeyLine]) -> list[KeyDraft]`
- Produces: `KeyDraft(number: int, answers: list[str], flagged: bool, page: int | None, bbox: tuple[float, float, float, float] | None)`
- Produces routes: `POST /api/tests/{id}/key/import`, `POST /api/tests/{id}/key/scan`

A draft is flagged when confidence is below `0.90`, the number is duplicated or out of range, the answer is empty, or the value does not fit the question type.

- [ ] **Step 1: Write failing tests**

```python
def test_text_layer_import_never_calls_provider(h): ...
def test_scan_on_text_layer_pdf_returns_409_key_has_text_layer(h): ...
def test_scan_runs_only_on_explicit_request(h): ...
def test_low_confidence_answer_is_flagged(): ...
def test_duplicate_question_number_is_flagged(): ...
def test_tfng_value_outside_allowed_set_is_flagged(): ...
def test_rescan_keeps_reviewed_answers(h): ...
def test_imported_answers_stay_unreviewed(h): ...

@pytest.mark.pilot
def test_pilot_key_matches_ba_table(): ...  # skip when DEV/exam is absent
```

The pilot test compares all 40 answers with `BA/PILOT_EXAM_KEY.md`, except question 23's PO-added variants from D-20. It also prints the verified passage and question pages, closing the page-number check in `ARCHITECTURE.md`.

- [ ] **Step 2: Run them. Expected: FAIL.**
- [ ] **Step 3: Implement the parser and routes.** Register the `pilot` marker and exclude it from the default run.
- [ ] **Step 4: Run the full suite and `uv run pytest -m pilot`. Expected: PASS.**
- [ ] **Step 5: Commit** `feat: import answer keys from text and scans`

---

### Task 7: Backend acceptance for the pilot test

**Files:**
- Test: `tests/test_m2_acceptance.py`

- [ ] **Step 1: Write the acceptance test.** Using a synthetic 15-page PDF and the pilot structure from `BA/PILOT_EXAM_KEY.md`, create the test as Teacher A, upload, map 5 passages, enter 40 answers, review, and publish.
- [ ] **Step 2: Assert** 5 sections, 40 questions, groups 20/10/6/4, no band field in any response, Teacher B gets 404, and a student gets 403.
- [ ] **Step 3: Run the full suite. Expected: PASS.**
- [ ] **Step 4: Commit** `test: cover M2 pilot authoring flow`

---

### Task 8: Canvas review gate

**This task stops for PO approval. Do not change `DEV/frontend` before it passes.**

- [ ] **Step 1:** Add these screens to the Claude Design canvas in the approved PANXG style, in Vietnamese and English: test list, create and upload, page and structure mapping, key entry with source region, review and publish blockers, student-view preview.
- [ ] **Step 2:** Include empty, loading, upload error, OCR failed and publish-blocked states.
- [ ] **Step 3:** Send the canvas link to the PO and wait for approval. Record the approved page in this plan.

---

### Task 9: Teacher authoring UI

**Files:**
- Create: `frontend/src/pages/tests/{list,editor,structure,key,publish,preview}.tsx`, `frontend/src/lib/tests-api.ts`
- Modify: `frontend/src/main.tsx`, `frontend/src/app/shell.tsx`, `frontend/src/locales/{vi,en}.ts`, `frontend/package.json`
- Test: `tests/e2e/test_m2.py`

**Interfaces:**
- Consumes: all `/api/tests` routes from Tasks 2 to 6

- [ ] **Step 1: Write the failing browser test** for teacher login, create, upload, range assignment, pasted key, blocked publish, full review and publish.
- [ ] **Step 2: Run `uv run pytest -m e2e tests/e2e/test_m2.py`. Expected: FAIL.**
- [ ] **Step 3: Implement the approved screens.** Render PDFs with PDF.js with `isEvalSupported: false`. Show OCR text as plain text. Preview creates no attempt and loads no answer data.
- [ ] **Step 4: Run `npm run typecheck`, `npm run build` and the e2e test. Expected: PASS.**
- [ ] **Step 5: Commit** `feat: add teacher exam authoring UI`

---

### Task 10: Real pilot run

- [ ] **Step 1:** With the local stack, create the real pilot test from `DEV/exam` through the UI. This sends the student PDF to PaddleOCR, as approved by the PO on 2026-11-23.
- [ ] **Step 2:** Confirm 15 pages, 5 passages, 40 questions, groups 20/10/6/4, and that publish is blocked until all answers are reviewed.
- [ ] **Step 3:** Update `DEV/docs/ARCHITECTURE.md` with the storage, worker queue and OCR provider decisions. The simple PostgreSQL job table replaces Procrastinate for M2.
