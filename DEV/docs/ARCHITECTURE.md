# Architecture Plan: IELTS Reading MVP

## Context

The BA package (Spec 1.1, MVP Plan 1.1, approved 2026-11-21) asks the Coding Agent to propose the architecture and split M1 into tasks. The MVP serves 1 teacher and 1 student on a monthly Reading test, while keeping data separated by teacher so it can grow.

The core loop is: upload PDF → enter/scan key → approve → assign → timed exam → automatic grading → AI and teacher review → insight → next assignment.

The repository has no application code yet. Checked against the real files:

- The student PDF has 15 pages and **no text layer on any page**, so OCR is required for AI (CNT-13).
- The teacher key PDF has a **real text layer**. All 40 answers and the Unit list match `BA/PILOT_EXAM_KEY.md`.
- The group totals in S12 are correct: Matching 20, Completion 10, Multiple Choice 6, TFNG 4.
- **Not verified:** the passage and question page numbers, because no PDF renderer is installed. This is the first check in M2.

## 1. Architecture decisions

| Area | Decision | Why |
|---|---|---|
| Shape | Modular monolith plus a separate AI service | Simple to run for a pilot, and D-07 becomes a physical boundary |
| Language | Python 3.13 for all backend code (uv). TypeScript only for the browser UI | Python is a project requirement. The browser still needs JavaScript |
| Web | React + Vite SPA, PDF.js, TanStack Query | One server data path makes "no answers in the browser" easy to test |
| API | FastAPI + Pydantic contracts | Schema-validated requests and, importantly, responses |
| Database | PostgreSQL with Row-Level Security, plain SQL migrations and the `psycopg` driver | Tenant isolation is enforced below application code. Policies, grants and definer functions are hand-written SQL, so an ORM adds little |
| Jobs | Procrastinate on PostgreSQL | OCR, regrade and auto-submit without adding Redis |
| Files | S3-compatible private storage, short-lived signed URLs | NFR-07 |
| AI | Anthropic Python SDK, Messages API, our own loop | Full control of hooks, permissions and budgets |
| Model | `claude-opus-5-5`, set per task in versioned config | Moving a task to a cheaper model is a PO decision backed by eval results |
| Deploy | Docker Compose, Caddy for TLS, region pending (issue 1) | Enough for the pilot and portable between hosts |

**Not used for the AI:** Claude Agent SDK, Managed Agents, Files API, hosted Skills API. The tutor needs no shell or filesystem, and these either add a per-session runtime or fall outside Zero Data Retention eligibility according to the BA research. We keep the Claude Code *concepts* and implement them on the Messages API.

## 2. System shape

```text
Browser (React SPA)
   │ HTTPS, httpOnly session cookie
   ▼
Caddy ──► API (FastAPI) ───────────► PostgreSQL (RLS)
            │    ▲                        ▲
            │    │ scoped tool calls      │
            │    │                     Worker (Procrastinate)
            ▼    │                     OCR, key scan, regrade,
         AI service                    auto-submit, explanations
         no DB credentials                │
            │                             ▼
            ▼                       Private file storage
       Claude API
```

Hard boundaries, enforced by lint rules and separate credentials:

- Exam, grading and results code cannot import or call the AI service.
- The AI service has no database or storage credentials. It reaches data only through read-only API tools, using a short-lived grant.
- `npa_grading` is a pure library with no I/O.

## 3. How the seven invariants are enforced

| # | Invariant | Enforcement | Proof |
|---|---|---|---|
| 1 | No answers or results before release | The student DB role has **no grant** on key tables. Review data comes only from a release-gated DB function. Response schemas are allowlists | S3 test scans every student response during an exam |
| 2 | Backend owns scores | AI service has no write path. Only the grading module writes results | Permission test plus S10 |
| 3 | Server-side authorization | RLS on every tenant table, with identity set per transaction from the session | Cross-tenant test matrix on every endpoint |
| 4 | Never lose saved answers | Idempotent upserts plus an append-only answer event log. Browser queue in IndexedDB | S1, plus kill-and-restart test during autosave |
| 5 | PDF content is untrusted | OCR runs with no tools. Text reaches the tutor only as labeled tool data, and the tutor has no write tools | S10 red-team set |
| 6 | AI failure cannot affect exams | Separate process, no dependency from exam routes | Full exam E2E with the AI service stopped |
| 7 | No band on course tests | Band rows are constrained by DB to `ielts_standard` tests. Band data is never given to AI for course tests. Output guard blocks band claims | S11 across results, dashboard and AI |

## 4. Core backend design

**Data domains**

- Identity: `users`, `sessions`, `consents`, `audit_log` (append-only)
- Classes: `classes`, `class_members`, `invites`
- Content: `tests`, `test_assets`, `sections`, `questions`, `tags`, `page_texts`
- Keys: `answer_key_versions`, `answer_key_items`, kept in a restricted schema
- Delivery: `assignments`, `attempts`, `responses`, `response_events`
- Grading: `grading_runs`, `graded_responses`, `manual_overrides`, `scores`, `band_estimates`
- Review: `feedback`, `error_causes`
- AI: `conversations`, `ai_messages`, `ai_turns`, `explanations`, `quota_ledger`

**Exam room**

- The server stores `deadline_at` when the attempt starts. The browser only displays a countdown from server time.
- Autosave runs on each answer change (debounced) and at most every 10 seconds, with a per-question sequence number.
- The newest device takes the write lease and the older one is told. This prevents a crashed tab from locking a student out.
- A sweeper job plus a check on every request auto-submits expired attempts, using a row lock so submission happens once.
- PDF.js keeps passage and question pages mounted, so switching (EXM-09) loses neither scroll position nor typed input.

**Grading**

- `grade(responses, keyVersion, rulesVersion)` is a pure, deterministic function implementing BR-02, 03, 04, 19 and D-15, D-20.
- Rules are per-test configuration. The NG and article rules are seeded for the pilot, not hardcoded globally.
- UK/US spelling is expanded into **explicit variants the teacher sees and approves**, so grading stays exact-match and BR-03 holds.
- Each regrade creates a new immutable `grading_run`. Teacher overrides and comments live outside runs, so they survive (S6).
- Grading runs in the submit transaction to meet the 5-second target.

**Key entry:** a PDF with a text layer is parsed without AI, which covers the pilot key. Image keys use two independent AI extractions, and any disagreement is flagged. Either way, the teacher approves 100% before publishing.

## 5. AI harness (M5)

| Claude Code concept | Our implementation |
|---|---|
| Agent loop | Manual Messages API loop with step, token and time limits per turn |
| Tools | Small read-only registry, deny by default, allowlisted per assistant and role |
| Skills | Versioned `SKILL.md` folders in the repo. Metadata always loaded, body loaded on demand. Students cannot invoke them |
| Hooks | `PreToolUse`, `PostToolUse`, `PreOutput`, `Stop`. A hook failure **blocks** the action |
| Permissions | Signed grant from the login session, bound to user, attempt, tools and expiry. The model never supplies an ID |
| Sessions | Conversations stored in PostgreSQL, tied to an attempt, append-only |
| Context | Stable prefix for prompt caching. Constraints rebuilt from the DB every turn, so compaction cannot remove them |
| Streaming | SSE from AI service through API to browser, with cancel |
| Observability | `ai_turns` records skill, tools, tokens, cost, latency, outcome and versions |

**MVP tools:** `get_attempt_summary`, `get_question_context`, `get_approved_answer`, `get_teacher_feedback`, `search_passage`, `load_skill`, `cite_passage`.

**Grounding**

- Quotes go through `cite_passage`. The backend checks the quote exists verbatim in the OCR text before showing it (AIB-02).
- Teacher feedback is injected as higher-priority context (AIB-11, S9).
- The backend refuses to issue a grant until results are released, so AI cannot run during an exam (AIB-07).
- Student names and emails are never sent. Emails and phone numbers typed into chat are masked.

**Explanations are generated once per question and stored.** This makes the 40-question gate a real review step. After PO approval, students read approved text with no AI latency, cost or failure risk. Live AI is used only for follow-up questions. If the gate fails, explanations stay as teacher-only drafts.

**Limits:** per-turn, per-user daily and global kill switch. Quota is reserved before the call and reconciled from actual usage. Values are set from M5 measurements.

## 6. Repository layout

```text
DEV/
  frontend/                # React UI
  backend/npa_api/         # API: core/, routes/, migrations/
  backend/tests/           # API, database and e2e tests
  docs/                    # architecture, design system, ADRs, plans
  assets/                  # brand files
  docker-compose.yml
```

Planned for later milestones: `backend/{npa_grading,npa_worker,npa_ai}` and `fixtures/pilot-exam/` for the machine-readable key and S14 cases.

`DEV/exam/` stays out of git and Docker images, because it holds a full exam and its answer key.

## 7. Delivery plan

**M1 tasks (current milestone)**

| # | Task | Requirements | Done when |
|---|---|---|---|
| 1 | Python backend project, lint and type checks, test runner, Compose | | One command boots and tests the stack |
| 2 | DB foundation: migrations, roles, RLS, per-transaction identity | NFR-06 | A query without identity returns no rows |
| 3 | Audit log | ADM-04 | App role cannot update or delete entries |
| 4 | Login, logout, password reset, revocable sessions | ACC-01 | A locked user is rejected on the next request |
| 5 | Admin creates and locks teachers | ACC-02, ADM-01 | New teacher sees only their own space |
| 6 | Classes: create, edit, archive | CLS-01 | Archived class rejects new assignments |
| 7 | Invite link/code, join, remove student | ACC-03, CLS-02, CLS-03 | Expired or disabled code is rejected |
| 8 | 18+ gate and per-purpose consent | ACC-04, LGL-03, LGL-08 | AI purpose stays off without consent, with evidence stored |
| 9 | Web shell in Vietnamese: auth, teacher classes, student join | NFR-08, NFR-09 | Full invite flow works in a browser |
| 10 | Cross-tenant test suite | M1 exit | Teacher B gets no data from Teacher A on any endpoint |

**Later milestones**

- **M2:** upload pipeline, page mapping, structure, key entry and scan, OCR, approval, publish.
- **M3:** assignments, exam room, autosave, auto-submit, grading, results (S1–S5, S11, S13, S14).
- **M4:** teacher review, error causes, statistics, backups with a restore drill (S6, S7, S12, S15). First real exam possible.
- **M5:** harness, Reading skill, explanations, 40-question gate, red-team set (S8–S10).
- **M6:** pilot, after the legal check.

## 8. Issues for BA and PO

| # | Issue | Blocks | Recommendation |
|---|---|---|---|
| 1 | **Hosting abroad also moves personal data abroad.** The handoff treats cross-border transfer as an AI issue at M6, but a foreign database would trigger it at the first real exam (M4) | M4 | Host DB and files in Vietnam, or get the lawyer's answer before M4 |
| 2 | **OQ-09 is still open:** answers not synced when time runs out | M3 | Accept for 30 seconds after deadline. Keep later arrivals ungraded and visible to the teacher |
| 3 | **"AI reviews immediately" (D-14) can conflict with a later release time (ASG-04) and with retakes**, since it would reveal answers early | M3 | AI unlocks only at release. With retakes, release after the last attempt or the deadline |
| 4 | **Starting close to the due time is undefined:** full duration, or cut off at the due time? | M3 | Deadline is the earlier of start + duration and the due time |
| 5 | **Quote checks trust the OCR.** An OCR error can make a false quote pass or a true quote fail | M5 | Teacher proofreads OCR per passage before AI goes live |
| 6 | **Consent and upload terms text** is needed from PO or lawyer | Real users (M4) | Use clearly marked placeholders in development |
| 7 | `Y`/`N` shorthand for Yes/No/Not Given is unspecified | M3 | Treat it the same as `T`/`F` |
| 8 | Docs cite `DEV/exam/Reading_...pdf`, but the file is in `DEV/exam/exam/` | None | BA corrects the path |

Under the handoff rules, I will not decide items 1–7 myself. M1 can proceed without them.

## 9. Verification

- **Every task:** typecheck, lint, unit and integration tests against real PostgreSQL.
- **M1 exit:** browser E2E of admin → teacher → class → invite → student join, plus the cross-tenant suite.
- **Grading:** all 24 cases from `PILOT_EXAM_KEY.md` section 3, a 40/40 submission, and determinism tests.
- **Invariants:** each row in section 3 becomes an automated test that stays in CI.
- **AI (M5):** PO scores all 40 explanations, and the S8–S10 red-team set must pass before students see AI output.

## On approval

1. Save this as `DEV/docs/ARCHITECTURE.md` and send section 8 to the BA as `DEV/docs/BA_QUESTIONS.md`.
2. Write the detailed M1 implementation plan.
3. Build M1 task by task with tests, on a feature branch.
