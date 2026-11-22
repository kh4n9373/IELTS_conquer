# PANXG English: application code

Architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Open questions for BA and PO: [docs/BA_QUESTIONS.md](docs/BA_QUESTIONS.md).

## Where things live

```text
DEV/
  frontend/                 React and TypeScript UI
    src/
      main.tsx              entry point and route table
      app/                  app shell, role gate, signed-in session
      components/           shared UI components
      lib/                  API client, messages, motion helpers
      pages/                one file or folder per screen
      styles/               global styles
  backend/                  Python API
    npa_api/
      app.py                builds the FastAPI app
      server.py, cli.py     run the API, migrate, create an admin
      core/                 database, request guard, security, mail
      routes/               one file per API area
      migrations/           SQL schema changes, applied in order
      testing.py            throwaway test databases
    tests/                  API and database tests
    tests/e2e/              browser flow
  docs/                     architecture, design system, plans
  assets/                   brand files such as the logo
  exam/                     full exam and answer key, never committed
  docker-compose.yml        local PostgreSQL
```

To add a screen, create it in `frontend/src/pages/` and register it in `main.tsx`. To add an API area, create it in `backend/npa_api/routes/` and include it in `app.py`.

## Run locally

Requires Python 3.13+, [uv](https://docs.astral.sh/uv/), Node 24+ and Docker.

```bash
cp .env.example .env        # then set POSTGRES_PASSWORD and the matching URLs
npm install
(cd backend && uv sync)
docker compose --env-file .env up -d db
set -a; source .env; set +a

(cd backend && uv run npa-migrate)
psql "$DATABASE_OWNER_URL" -c "alter role npa_app password '$APP_DB_PASSWORD'"
(cd backend && ADMIN_EMAIL=you@example.com ADMIN_PASSWORD='at-least-12-chars' uv run npa-create-admin)

(cd backend && RELOAD=1 uv run npa-api)   # http://127.0.0.1:3000
npm run dev:web                           # http://localhost:5173
```

In development, password and activation links are printed in the API log. Set `APP_ENV=production` to turn on secure cookies and trust forwarded headers from `FORWARDED_ALLOW_IPS`.

## Test

Tests need a PostgreSQL server where the connecting user can create databases and roles. Set `TEST_DATABASE_URL`, which defaults to `postgres://npa_owner@127.0.0.1:54329/postgres`, and allow passwordless local login for `npa_app`.

```bash
cd backend
uv run pytest                                   # database and API
uv run pytest -o addopts='' -m e2e tests/e2e    # browser flow, uses installed Google Chrome
uv run ruff check . && uv run mypy              # lint and types
```
