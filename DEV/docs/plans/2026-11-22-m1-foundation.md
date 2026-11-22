# M1 Foundation Implementation Plan

> **Stack changed after this plan was written.** The backend is now Python (FastAPI, Pydantic, psycopg, pytest) in `backend/`. Scope and behavior are unchanged. TypeScript names and paths below are historical. See `docs/ARCHITECTURE.md`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A teacher can sign in, create a class and invite a student, and automated tests prove another teacher cannot read that data.

**Architecture:** React SPA calls a Fastify API. The API runs every data query inside a transaction that assumes a PostgreSQL role and identity taken from the session, so Row-Level Security enforces tenancy. Pre-login operations go through narrow `SECURITY DEFINER` functions.

**Tech Stack:** Node 26, TypeScript strict, npm workspaces, Fastify 5, Zod, `pg`, PostgreSQL, React, Vite, TanStack Query, React Router, Vitest, Playwright.

**Spec:** `DEV/docs/ARCHITECTURE.md`, `BA/PRODUCT_SPEC.md`, `BA/MVP_PLAN.md`

## Global Constraints

- Code lives under `DEV/`. `DEV/exam/` is never committed or copied into an image.
- Authorization is server-side on every request (Spec section 3). Other tenants' objects return 404.
- Pilot accepts users aged 18+ only (A-04). A student belongs to one teacher (A-01).
- UI copy is Vietnamese, held in one message file (NFR-09).
- Passwords use `node:crypto` scrypt. Session and reset tokens are stored only as SHA-256 hashes.
- Cookies are `httpOnly`, `sameSite=lax`, and `secure` outside development.
- Consent text is a marked placeholder until PO supplies it (BA question 6).
- Migrations are plain SQL. This replaces Drizzle from the architecture draft, because RLS, grants and definer functions are all hand-written SQL.

## Review Focus

- Same email in different letter case must be one account (Task 3).
- A locked user holding a live session is rejected on the next request (Task 3).
- A student of Teacher A using Teacher B's invite is rejected, per A-01 (Task 6).
- A removed student loses class access, keeps history, and can rejoin only through a valid invite (Task 6).
- Reset tokens work once, and a reset revokes all existing sessions (Task 3).

---

### Task 1: Workspace and database foundation

**Files:** `DEV/package.json`, `DEV/tsconfig.base.json`, `DEV/docker-compose.yml`, `DEV/.gitignore`, `DEV/packages/db/migrations/0001_foundation.sql`, `DEV/packages/db/src/{migrate,identity,index}.ts`, `DEV/packages/db/test/rls.test.ts`

**Interfaces:**
- Produces: `withIdentity<T>(pool, identity: Identity, fn: (tx: PoolClient) => Promise<T>): Promise<T>`
- Produces: `type Identity = { userId: string; role: 'student'|'teacher'|'admin'; teacherId: string | null }`
- Produces: `migrate(databaseUrl: string): Promise<void>`

- [ ] Write failing tests: a query with no identity returns 0 rows, and the app role cannot read `users.password_hash`.
- [ ] Implement tables `users`, `sessions`, `password_resets`, `consents`, `classes`, `class_members`, `invites`, `audit_log`, roles and RLS policies.
- [ ] Run `npm test -w @npa/db`. Expected: PASS.
- [ ] Commit.

### Task 2: Audit log

**Files:** `DEV/packages/db/src/audit.ts`, `DEV/packages/db/test/audit.test.ts`

**Interfaces:** Produces `writeAudit(tx, entry: { action: string; entity: string; entityId: string; before?: unknown; after?: unknown }): Promise<void>`

- [ ] Write failing tests: app roles cannot `UPDATE` or `DELETE` audit rows, and the actor comes from identity rather than the caller.
- [ ] Implement, run, commit.

### Task 3: Authentication

**Files:** `DEV/apps/api/src/{app,config}.ts`, `DEV/apps/api/src/auth/*`, `DEV/apps/api/test/auth.test.ts`

**Interfaces:**
- Produces: `buildApp(deps: { pool: Pool; mailer: Mailer; now?: () => Date }): FastifyInstance`
- Produces: `request.identity: Identity`, `requireRole(...roles)` preHandler
- Routes: `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/me`, `POST /api/auth/forgot`, `POST /api/auth/reset`

- [ ] Write failing tests for login, wrong password, mixed-case email, locked user on a live session, single-use reset, and reset revoking sessions. `forgot` returns 204 for unknown emails.
- [ ] Implement, run, commit.

### Task 4: Admin manages teachers

**Files:** `DEV/apps/api/src/admin/routes.ts`, `DEV/apps/api/src/cli/create-admin.ts`, `DEV/apps/api/test/admin.test.ts`

Routes: `GET/POST /api/admin/teachers`, `POST /api/admin/teachers/:id/lock`, `POST /api/admin/teachers/:id/unlock`

- [ ] Write failing tests: only admin can call, lock takes effect immediately, and both actions are audited.
- [ ] Implement, run, commit.

### Task 5: Classes

**Files:** `DEV/apps/api/src/classes/routes.ts`, `DEV/apps/api/test/classes.test.ts`

Routes: `GET/POST /api/classes`, `GET/PATCH /api/classes/:id`, `POST /api/classes/:id/archive`

- [ ] Write failing tests: CRUD, archived class stays readable and rejects edits, other teacher gets 404.
- [ ] Implement, run, commit.

### Task 6: Invites, joining and membership

**Files:** `DEV/apps/api/src/invites/routes.ts`, `DEV/apps/api/test/invites.test.ts`

Routes: `POST /api/classes/:id/invites`, `POST /api/invites/:id/disable`, `GET /api/invites/:code`, `POST /api/invites/:code/register`, `POST /api/invites/:code/join`, `DELETE /api/classes/:id/members/:studentId`

- [ ] Write failing tests: register into the right class, expired or disabled code rejected, under-18 rejected, other teacher's student rejected, removal keeps history, rejoin works.
- [ ] Implement, run, commit.

### Task 7: Consent

**Files:** `DEV/apps/api/src/consent/routes.ts`, `DEV/apps/api/test/consent.test.ts`

**Interfaces:** Produces `hasConsent(tx, purpose: 'core_learning'|'ai_processing'): Promise<boolean>`

- [ ] Write failing tests: latest decision wins, history is append-only, AI consent defaults to false, terms version is stored.
- [ ] Implement, run, commit.

### Task 8: Web shell

**Files:** `DEV/apps/web/src/{main,router,api,messages}.ts*`, `DEV/apps/web/src/pages/*`

Pages: login, forgot/reset, join by invite, consent, admin teachers, teacher classes, class detail, student home.

- [ ] Build pages against the API and run `npm run build -w @npa/web`. Expected: success.
- [ ] Commit.

### Task 9: Cross-tenant suite and M1 end-to-end

**Files:** `DEV/apps/api/test/tenant-matrix.test.ts`, `DEV/e2e/m1.spec.ts`

- [ ] Write a matrix that enumerates every registered route and asserts each is either covered for Teacher B and student access or explicitly public. A new uncovered route fails the test.
- [ ] Write the browser flow: admin → teacher → class → invite → student join.
- [ ] Run `npm test` and `npm run e2e`. Expected: PASS.
- [ ] Commit.
