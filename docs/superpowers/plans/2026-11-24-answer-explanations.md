# Answer Explanations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pre-generate an LLM explanation for every question (and every wrong option) when a test is assigned, and show them to students on a new "Chi tiết" review page once their result is released.

**Architecture:** A new `explain_jobs` queue (one job per test) is filled on assignment create and drained by a second loop inside the existing `npa-worker` process. The loop batches ~20 questions per LLM call (Groq first, Gemini fallback), runs a test's batches in parallel, and writes `keys.question_explanations` under the teacher's identity. A new `GET /api/attempts/{id}/review` reuses the result endpoint's ownership + release gate and joins question text, the student's answers and explanations; the React review page polls while any explanation is pending.

**Tech Stack:** Python 3.13, FastAPI, psycopg 3, raw SQL migrations, httpx; React + react-query + react-router; pytest against a real Postgres (`TEST_DATABASE_URL`).

**Spec:** `docs/superpowers/specs/2026-11-24-answer-explanations-design.md`

## Global Constraints

- Backend is Python. Next migration is `DEV/backend/npa_api/migrations/0008_explanations.sql`.
- Explanations live in the `keys` schema; no student role gets a grant or a definer path to them except through the release-gated review endpoint.
- Only question/passage/option/key text goes to an LLM. Never titles, teacher, student, answers given.
- The LLM never decides correctness; grading is untouched. Answers in a reply are ignored.
- Explanations: Vietnamese, English grammar terms kept, 1–3 sentences each.
- Primary provider: Groq `llama-3.3-70b-versatile` (`GROQ_API_KEY`, override `GROQ_MODEL`). Fallback: Gemini `gemini-2.5-flash` (`GEMINI_API_KEY`, override `GEMINI_MODEL`). Fallback on 429, 5xx, timeout or invalid JSON. No `GROQ_API_KEY` → Gemini only.
- Batch size 20 questions; at most 3 concurrent calls per test.
- Review page: filter Sai / Bỏ trống / Tất cả, default Sai; 10 per page; polls every 5 s only while an item is `pending`.
- Teacher editing is out of scope, but `source in ('ai','teacher')` exists and the worker never overwrites `teacher` rows.
- Commit only when the user has asked for commits. Never add Co-Authored-By / generated-by lines. Never stage `BA/`, `PM/`, `DEV/frontend/tsconfig.tsbuildinfo`, or `.env`.
- UI must be approved on the Claude Design canvas before Task 6 code is written.

**Deviation from spec (deliberate):** the spec queues a job when a teacher saves structure/key on an assigned test. Structure and key edits are draft-only (`lock_test(..., draft=True)` → `test_locked`), and only published tests can be assigned, so that trigger can never fire. It is dropped. `content_hash` stays, so a future unlock still regenerates correctly.

## Review Focus

1. **Result not released yet** — `/review` must answer exactly like `/result` does for a pending student (no prompt, no key, no explanation). Test in Task 5.
2. **Student's answer in a different form than the stored option key** (`"t"` vs `"TRUE"`, `"b"` vs `"B"`, blank) — the chosen reason must still be found, and blank gets no chosen reason. Test in Task 3 (`chosen_reason`).
3. **LLM returns partial or wrong numbers** (missing items, extra numbers, numbers from another batch) — keep valid items, drop the rest, never crash the job. Test in Task 3.
4. **Both providers down** — job retries per OCR schedule then `failed`; review shows `none`, not `pending` forever. Test in Task 4 and Task 5.
5. **Two assignments of the same test** — no duplicate job, no duplicate rows. Test in Task 5 (`enqueue` idempotent).

---

### Task 1: Migration 0008 — tables, RLS, claim/finish, review row

**Files:**
- Create: `DEV/backend/npa_api/migrations/0008_explanations.sql`
- Test: `DEV/backend/tests/test_explain_db.py`

**Interfaces:**
- Produces tables:
  - `keys.question_explanations(question_id uuid pk → questions on delete cascade, test_id uuid, teacher_id uuid, content_hash text not null, correct text not null, options jsonb not null default '{}', source text check in ('ai','teacher'), provider text, created_at timestamptz default now())`, composite FK `(test_id, teacher_id) → tests`.
  - `explain_jobs(id, test_id, teacher_id, state, attempts, lease_until, error_code, created_at, finished_at)` — same columns/checks as `ocr_jobs` minus `asset_id`/`provider_job_id`; partial unique index `on explain_jobs (test_id) where state in ('queued','running')`.
- RLS `teacher_id = app.user_id()` + grants to `npa_teacher` on both tables (mirror `0003` lines 180–209). No grants to `npa_student`.
- `app.explain_claim(p_now timestamptz, p_lease_until timestamptz) returns table (id uuid, test_id uuid, teacher_id uuid, attempts int)` — body copied from `app.ocr_claim` with table/columns swapped.
- `app.explain_finish(p_job uuid, p_attempt int, p_state text, p_error text, p_retry_at timestamptz, p_now timestamptz) returns boolean` — copy of `app.ocr_finish`.
- `app.explain_enqueue(p_test uuid) returns void` security definer: insert a queued job for the test unless one is queued/running (`on conflict do nothing` against the partial index).
- `app.attempt_review_row(p_attempt uuid) returns jsonb` security definer: `{ questions: [{number, type, sectionTitle, prompt, options, explanation: {correct, options} | null, fresh: bool}], jobActive: bool }`. `fresh` is computed in Python (Task 5) — the SQL returns `contentHash` instead of `fresh`. All three new functions: revoke from public, grant to `npa_app`.

- [ ] **Step 1: Write failing tests** in `test_explain_db.py`:
  - `test_student_cannot_read_explanations`: as a student identity, `select * from keys.question_explanations` raises `psycopg.errors.InsufficientPrivilege`.
  - `test_enqueue_is_idempotent`: call `app.explain_enqueue(tid)` twice → exactly one row in `explain_jobs` with `state = 'queued'`.
  - `test_claim_then_finish_queued_again`: claim returns the job with `attempts = 1`; `explain_finish(..., 'queued', 'provider_error', now+60s, now)` returns `true`; a claim at `now` returns nothing, at `now+61s` returns it with `attempts = 2`.
  - `test_review_row_returns_prompt_and_options`: for a manual test with one MCQ (`prompt="Pick one"`, `options=["a1","b1"]`), `attempt_review_row` returns that prompt/options and `explanation: null`, `jobActive: false`.
- [ ] **Step 2:** `cd DEV/backend && uv run pytest tests/test_explain_db.py -q` → FAIL (function/table missing).
- [ ] **Step 3:** Write `0008_explanations.sql` per Interfaces.
- [ ] **Step 4:** Same command → 4 passed. Then `uv run pytest tests/test_rls.py tests/test_tenant_matrix.py -q` → still green (add the two new tables to the tenant matrix's table list if it enumerates tables).
- [ ] **Step 5:** Checkpoint commit `feat: add explanation tables and job queue` (if commits authorised).

### Task 2: LLM providers with fallback

**Files:**
- Create: `DEV/backend/npa_api/explain/__init__.py` (empty), `DEV/backend/npa_api/explain/llm.py`
- Test: `DEV/backend/tests/test_explain_llm.py`

**Interfaces:**
- Produces:
  - `class LlmError(Exception)` with `code: Literal["rate_limited", "provider_error", "bad_reply", "quota_exceeded"]`.
  - `Llm = Callable[[str, dict[str, Any]], dict[str, Any]]` — `(prompt, json_schema) -> parsed JSON object`.
  - `groq(api_key: str, client: httpx.Client, model: str = "llama-3.3-70b-versatile") -> Llm` — POST `https://api.groq.com/openai/v1/chat/completions`, `response_format={"type":"json_object"}`, schema embedded in the system message, timeout 60 s.
  - `gemini(api_key: str, client: httpx.Client, model: str = "gemini-2.5-flash") -> Llm` — same endpoint/`responseSchema` style as `npa_api/ocr/gemini.py` (reuse its `URL`), timeout 120 s.
  - `with_fallback(*providers: Llm) -> Llm` — tries in order; moves to the next on any `LlmError`; re-raises the last error. Logs provider name and error code only (never prompt/reply text).
  - `from_env(client: httpx.Client) -> Llm` — Groq+Gemini if `GROQ_API_KEY` set, else Gemini only.

- [ ] **Step 1: Write failing tests** using `httpx.MockTransport`:
  - `test_groq_parses_json_content`: reply `{"choices":[{"message":{"content":"{\"items\":[]}"}}]}` → `{"items": []}`; request has header `Authorization: Bearer k`.
  - `test_groq_429_is_rate_limited`: 429 → `LlmError` with `code == "rate_limited"`.
  - `test_invalid_json_is_bad_reply`: content `"not json"` → `code == "bad_reply"`.
  - `test_fallback_uses_second_on_error`: first raises `LlmError("rate_limited")`, second returns `{"ok": 1}` → `{"ok": 1}`.
  - `test_fallback_raises_last_error`: both fail → raises with the second's code.
  - `test_from_env_without_groq_uses_gemini_only` (monkeypatch env; assert only the Gemini URL is hit).
- [ ] **Step 2:** `uv run pytest tests/test_explain_llm.py -q` → FAIL (module missing).
- [ ] **Step 3:** Implement `llm.py`.
- [ ] **Step 4:** Same command → 6 passed; `uv run mypy npa_api/explain && uv run ruff check npa_api/explain` clean.
- [ ] **Step 5:** Checkpoint commit `feat: add Groq and Gemini explanation providers`.

### Task 3: Batches, prompt, hash and reply parsing

**Files:**
- Create: `DEV/backend/npa_api/explain/batch.py`
- Modify: `DEV/backend/npa_api/grading/rules.py` — rename `_canonical` to `canonical` (keep callers working).
- Test: `DEV/backend/tests/test_explain_batch.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Item: question_id: UUID; number: int; question_type: str; prompt: str; options: list[str]; accepted: list[str]; passage: str`
  - `@dataclass(frozen=True) class Explanation: question_id: UUID; correct: str; options: dict[str, str]` — option keys are canonical answer values (`"a"`, `"true"`, …).
  - `content_hash(item: Item) -> str` — sha256 hex of a JSON dump of `[passage, prompt, options, sorted(accepted), question_type]`.
  - `batches(items: list[Item], size: int = 20) -> list[list[Item]]` — groups by `passage` first, then chunks; never mixes passages in one batch.
  - `REPLY_SCHEMA: dict[str, Any]` — `{items: [{number: int, correct: str, options: [{answer: str, reason: str}]}]}` (array of pairs, because Gemini rejects free-form maps).
  - `build_prompt(batch: list[Item]) -> str` — Vietnamese instructions (language/length rule from Global Constraints), passage once, then each question with number, prompt, options lettered A.., and the accepted answer.
  - `parse_reply(batch: list[Item], reply: dict[str, Any]) -> list[Explanation]` — keeps only numbers present in the batch, first occurrence wins; trims each text to 600 chars; drops empty `correct`; canonicalises option answers with `canonical`; drops option reasons for the accepted answer.
  - `chosen_reason(e: Explanation, question_type: str, given: str | None) -> str | None` — `None` for blank or correct-by-canonical; otherwise `e.options.get(canonical(given))`.

- [ ] **Step 1: Write failing tests:**
  - `test_hash_changes_with_options` and `test_hash_ignores_question_id`.
  - `test_batches_split_at_20_and_by_passage`: 25 items passage P + 3 items passage Q → batch sizes `[20, 5, 3]`, no batch has two passages.
  - `test_parse_keeps_only_batch_numbers`: reply with numbers `[1, 2, 99, 1]` for batch `[1, 2]` → 2 explanations, first `1` kept.
  - `test_parse_drops_empty_and_trims`: `correct=""` dropped; 1000-char text trimmed to 600.
  - `test_parse_canonicalises_tfng`: option answer `"F"` on a `tfng` item stored under key `"false"`.
  - `test_chosen_reason_forms`: `tfng`, options `{"false": "x"}`, given `"F"` → `"x"`; given `None` → `None`; given equal to accepted → `None`.
- [ ] **Step 2:** `uv run pytest tests/test_explain_batch.py -q` → FAIL.
- [ ] **Step 3:** Implement `batch.py`; rename `_canonical` → `canonical` in `rules.py`.
- [ ] **Step 4:** `uv run pytest tests/test_explain_batch.py tests/test_grading.py -q` → all pass.
- [ ] **Step 5:** Checkpoint commit `feat: build explanation batches and parse replies`.

### Task 4: Explanation worker loop

**Files:**
- Create: `DEV/backend/npa_api/explain/worker.py`
- Modify: `DEV/backend/npa_api/ocr/worker.py` (`main()` only)
- Test: `DEV/backend/tests/test_explain_worker.py`

**Interfaces:**
- Consumes: `app.explain_claim`, `app.explain_finish` (Task 1); `Llm`, `LlmError`, `from_env` (Task 2); `Item`, `batches`, `build_prompt`, `parse_reply`, `content_hash`, `REPLY_SCHEMA` (Task 3); `with_identity`, `Identity` from `npa_api.core.db`.
- Produces:
  - `load_items(tx: Conn, test_id: UUID) -> list[Item]` — questions with non-empty `prompt`, passage = section `body` if non-empty else joined `page_texts` of the section's `passage_pages`; `accepted` from the test's approved `keys.answer_key_items`.
  - `stale(tx: Conn, items: list[Item]) -> list[Item]` — items with no row, or row with different hash and `source = 'ai'`.
  - `run_once(pool: Pool, llm: Llm, now: Clock) -> bool` — claim (lease 10 min); load/stale under the teacher identity; run batches through `ThreadPoolExecutor(max_workers=3)`; upsert each explanation with its hash (`on conflict (question_id) do update ... where source = 'ai'`); then set the job `done`/`finished_at` under the teacher identity. A batch that raises `LlmError` is skipped; if every batch failed, settle via `explain_finish` with the OCR retry schedule (`RETRY_SECONDS`, `QUOTA_RETRY_SECONDS`, `MAX_ATTEMPTS` imported from `ocr.worker`).
  - `run_loop(pool: Pool, llm: Llm, stop: threading.Event, now: Clock) -> None` — same shape as `ocr.worker.run_loop`.
- `ocr.worker.main()`: build `llm = from_env(client)` and start `threading.Thread(target=explain.worker.run_loop, args=(pool, llm, stop, clock), daemon=True)` before `run_loop`; raise `open_pool` `max_size` to 4. `GROQ_API_KEY` is not added to `REQUIRED`.

- [ ] **Step 1: Write failing tests** (fake `Llm` returning canned replies keyed by question numbers in the prompt):
  - `test_job_writes_explanations_and_finishes`: 25-question manual test → 2 LLM calls, 25 rows, job `done`.
  - `test_second_run_skips_fresh_rows`: re-enqueue, run → 0 LLM calls.
  - `test_teacher_row_not_overwritten`: set one row `source='teacher'` with an old hash → after run its `correct` is unchanged.
  - `test_partial_batch_failure_keeps_others`: fake fails batch 2 only → 20 rows, job `done`.
  - `test_all_batches_fail_requeues_then_fails`: always `LlmError("provider_error")` → after 3 claims (advancing clock past retries) job `failed`.
  - `test_prompt_has_no_student_or_title`: captured prompts contain neither the test title nor any student email.
- [ ] **Step 2:** `uv run pytest tests/test_explain_worker.py -q` → FAIL.
- [ ] **Step 3:** Implement `explain/worker.py`; wire into `ocr.worker.main()`.
- [ ] **Step 4:** `uv run pytest tests/test_explain_worker.py tests/test_ocr_worker.py -q` → all pass.
- [ ] **Step 5:** Checkpoint commit `feat: generate explanations in the worker`.

### Task 5: Enqueue on assign + review endpoint

**Files:**
- Modify: `DEV/backend/npa_api/routes/assignments.py` (`create_assignment`, after recipients insert)
- Modify: `DEV/backend/npa_api/routes/attempts.py` (new route under `result`)
- Modify: `DEV/backend/npa_api/grading/release.py` (add `build_review`)
- Test: `DEV/backend/tests/test_explain_review.py`

**Interfaces:**
- Consumes: `app.explain_enqueue`, `app.attempt_review_row` (Task 1); `content_hash`, `chosen_reason`, `Item` (Task 3); existing `_result_row`, `settle_if_due`, `is_released`, `facts_of`, `pending`.
- Produces:
  - `create_assignment` calls `select app.explain_enqueue(%s)` with the test id inside the same transaction.
  - `build_review(row: dict, review: dict) -> dict` in `release.py`: `{released: true, testTitle, items: [{number, type, sectionTitle, prompt, options, answer, correct, key, explanation: {correct: str, chosen: str | None} | null, explanationState: "ready"|"pending"|"none"}]}`. State: row present and hash matches → `ready`; else `jobActive` → `pending`; else `none`.
  - `GET /api/attempts/{id}/review` (`@access(BOTH)`): identical gate to `result` — if `row["run"] is None` or (student and not released) return `pending(row, student=...)`. Otherwise `build_review`. If any item is `none` because of a stale/missing row and no job is active, call `app.explain_enqueue` (safety net) and report those items as `pending`. Header `Cache-Control: no-store`.

- [ ] **Step 1: Write failing tests** (reuse `m3_helpers.publish_test` and `make_class_with_students`):
  - `test_assigning_enqueues_once`: two assignments of one test → one active job.
  - `test_review_hidden_until_released`: policy `after_last`, student submitted, others pending → body has `released: false` and no `items` key.
  - `test_review_ready_with_chosen_reason`: insert explanation for Q1 (`options={"a": "vì A..."}`), student answered `"A"` wrongly → `explanationState == "ready"`, `explanation.chosen == "vì A..."`.
  - `test_review_pending_while_job_active`: no row, job queued → `pending`.
  - `test_review_none_after_failed_job_requeues`: job `failed`, no row → response `pending` and a new queued job exists.
  - `test_question_without_prompt_is_none`: prompt `""` → `none`, and no job enqueued for it.
  - `test_other_student_gets_404`.
- [ ] **Step 2:** `uv run pytest tests/test_explain_review.py -q` → FAIL.
- [ ] **Step 3:** Implement per Interfaces.
- [ ] **Step 4:** `uv run pytest -q` (whole backend suite) → all pass.
- [ ] **Step 5:** Checkpoint commit `feat: serve explanations on the review endpoint`.

### Task 6: "Chi tiết" review page

**Files:**
- Modify: `DEV/frontend/src/lib/exam-api.ts` (type + `review(attemptId)`)
- Create: `DEV/frontend/src/pages/exam/review.tsx`, `DEV/frontend/src/styles/review.css`
- Modify: `DEV/frontend/src/main.tsx` (route `/results/:attemptId/review` → `ReviewPage`)
- Modify: `DEV/frontend/src/pages/exam/result.tsx` (button in the "Về bài của tôi" row, right-aligned, `Link` to `review`)
- Modify: `DEV/frontend/src/locales/vi.ts`, `en.ts` (`review` block)

**Interfaces:**
- Consumes: `GET /api/attempts/{id}/review` shape from Task 5.
- Produces: `examApi.review(attemptId: string): Promise<ReviewView>`; `ReviewView = ResultPending | { released: true; testTitle: string; items: ReviewItem[] }`.
- Copy (vi): `details: 'Chi tiết'`, `title: 'Xem lại bài làm'`, `filters: { wrong: 'Sai', blank: 'Bỏ trống', all: 'Tất cả' }`, `yourChoice: 'Bạn chọn'`, `answer: 'Đáp án'`, `pending: 'Đang soạn giải thích…'`, `emptyWrong: 'Không có câu sai nào.'`, `back: 'Về kết quả'`. (en equivalents.)

- [ ] **Step 1 (gate):** Mock the review page and the results-row button on the Claude Design canvas; get the user's approval. Do not write Step 3 code before approval.
- [ ] **Step 2:** Add `review` locale block in both files; run `npx tsc -b` → FAIL until the page exists (missing keys are a type error since `en` is typed from `vi`).
- [ ] **Step 3:** Implement `ReviewPage`: `useQuery(['review', id], …, { refetchInterval: (q) => q.state.data?.released && q.state.data.items.some(i => i.explanationState === 'pending') ? 5000 : false })`; pending results render the same pending view as `ResultPage`; filter default `wrong`; 10 per page reusing the `.res-pager` markup/styles from `result.css`; per card: prompt, options with ✗ on the student's choice and ✓ on the key, `explanation.correct`, `explanation.chosen` when present, muted `pending` line, nothing for `none`. Non-option questions show "Bạn chọn" / "Đáp án" as text.
- [ ] **Step 4:** `cd DEV/frontend && npx tsc -b && npm run lint` → clean. Then in the browser (dev servers at :5173/:3000) log in as the test student, open a released result → "Chi tiết" → see wrong items with explanations; with the worker stopped, see the pending line and that it disappears within ~5 s after starting the worker.
- [ ] **Step 5:** Checkpoint commit `feat: add Chi tiết review page`.
