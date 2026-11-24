# Answer explanations and the "Chi tiết" review page

Status: approved, 2026-11-24

## Intent

After a test, a student should see where they went wrong *and why*. The results page already gives the
score, the proportional bar and a per-question table (answer vs. key). A new "Chi tiết" button, bottom
right on the same row as "Về bài của tôi", opens a review page that shows each question with its text,
the student's choice, the correct answer, and a short explanation written by an LLM.

## Goals

- Every question that has text gets an explanation of the correct answer. For questions with options
  (multiple choice, TFNG/YNNG, matching), every wrong option gets its own short reason.
- The student never waits on the AI. The review page renders answers at once; explanations fill in
  when ready.
- AI cost scales with *tests*, not with students or attempts.

## Non-goals

- Per-student, personalised feedback. Explanations belong to the question, not the submission.
- Re-grading. Scoring stays the existing key comparison; the LLM never decides right or wrong.
- Writing tasks or free-text feedback.

## Anti-goals (a failure even if it works)

- An explanation reaching a student before their result is released (it reveals the key).
- The review page blocking, spinning full-screen, or showing provider errors to students.
- Student data (names, answers, emails) sent to an LLM provider.

## Approaches considered

1. **Pre-generate per question when a test is assigned** (picked). One background job per test; stored
   per question and per option; reused by every student and every attempt.
2. Generate per submission, personalised. Rejected: student waits, cost × students, and for
   option-based questions the content would be identical anyway.
3. Generate lazily on first "Chi tiết" open, then cache. Rejected: no job queue needed, but the first
   student of every test waits.

The user picked approach 1 on 2026-11-24, after confirming that a student changing their answer or
retaking the test needs no new AI call (all options are pre-explained).

## Design

### Data

- New table `keys.question_explanations` in the `keys` schema (same protection as answer keys: no
  student role has a path to it). One row per question:
  `question_id`, `test_id`, `teacher_id`, `content_hash`, `correct` (text), `options` (jsonb map
  option letter → reason), `source` (`ai` | `teacher`), `provider`, `created_at`.
- `content_hash` = hash of section passage + prompt + options + accepted key. If it differs from the
  current question, the row is stale and the question is regenerated.
- New queue table `explain_jobs`, shaped like `ocr_jobs` (state, attempts, lease_until, error_code),
  one per test.

### When jobs are queued

- On `POST /api/assignments` (test assigned).
- When the teacher saves structure or key on a test that already has an assignment.
- Safety net: when a released result is read and any question lacks a fresh explanation, enqueue (no
  duplicate if a job for that test is already queued or running).

### Worker

- Runs in the existing `npa-worker` process, next to OCR, as a separate claim loop.
- Selects only questions with non-empty prompt whose hash is missing or stale, and whose source is not
  `teacher` (a teacher's edit is never overwritten unless the question itself changes).
- Batches about 20 questions per call, grouped by section so the passage is sent once per batch.
  Batches of one test run in parallel (small thread pool, e.g. 3).
- Context sent: passage (section body for typed tests; OCR page text of passage pages for PDF tests),
  prompt, options, accepted answer. Nothing about students or teacher.
- Reply is JSON against a fixed schema, validated; answers in the reply are ignored (the key comes from
  the DB). Invalid or missing items are retried once, then left empty.
- Explanations are written in Vietnamese, with English grammar terms kept, 1–3 sentences each.

### Providers

- Primary: Groq free tier (Llama 3.3 70B, OpenAI-compatible API), key `GROQ_API_KEY` (present in
  `DEV/.env`).
- Fallback: Gemini 2.5 Flash via the existing client pattern in `npa_api/ocr/gemini.py`,
  key `GEMINI_API_KEY` (already configured).
- Fallback triggers on 429, 5xx, timeout or invalid JSON. If `GROQ_API_KEY` is unset, go straight to
  Gemini. Provider interface is small so others can be swapped in later.

### API

- `GET /api/attempts/{id}/review`: same ownership and release rules as `/result` (students get 404 or
  pending until released). Returns per question: number, type, section title, prompt, options,
  student answer, accepted key, correct flag, and `explanation: {correct, chosen} | null` plus
  `explanationState: ready | pending | none`.
  - `pending` = a job for this test is queued or running.
  - `none` = no prompt text, or the job failed for that question.

### Student UI (`/results/:id/review`)

- Entry: "Chi tiết" button on the results page, right side of the "Về bài của tôi" row.
- Filter Sai / Bỏ trống / Tất cả, default **Sai**. 10 per page, same pager as the results table.
- Each question card: prompt, options with the student's choice marked ✗/✓ and the correct one ✓,
  then the explanation for the correct answer and, when the choice was wrong, the reason for their
  choice.
- `pending`: a small muted line "Đang soạn giải thích…" in the explanation slot; react-query polls
  every 5 s while any item is pending, and stops when none are.
- `none`: the explanation slot is simply absent.
- The UI goes through the Claude Design canvas for approval before it is coded.

### Teacher (deferred)

- Not in this round (user decision, 2026-11-24). Later: the test editor shows each question's
  explanation with an edit field; saving sets `source = teacher`. This round still creates the
  `source` column and the worker already skips `teacher` rows, so the later work needs no migration.

## Constraints

- Backend is Python (FastAPI + psycopg, raw SQL migrations). Next migration: `0008`.
- Times in +07:00; nothing time-sensitive here beyond release rules.
- Free-tier rate limits are low; the worker must back off on 429 before falling back.

## Left to the builder

- Exact hash inputs and encoding; thread-pool size; batch size tuning against token limits.
- Prompt wording, as long as it meets the language and length rules above.
- Whether the teacher edit lives in the question row or a side panel (canvas decides).
