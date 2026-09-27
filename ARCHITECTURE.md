# Architecture

Broadsheet is one Python web application in front of one PostgreSQL database, shipped as two containers. That is deliberate: a volunteer organizer should be able to run it on a laptop on Monday and still understand it on Friday.

```
 browser ── HTML forms + a little htmx ─┐
                                         ├──▶  app  (FastAPI, Jinja2, SQLAlchemy)  ──▶  db  (PostgreSQL 16)
 scripts / curl / run.py ── JSON, CSV ───┘         port 8080                             no published port
```

## Decisions and why

| Decision | Why | What we gave up |
|---|---|---|
| **FastAPI + server-rendered Jinja2 pages** | One process serves both the pages and the JSON API, with the same access rules. No separate frontend build, no Node in the container, nothing to download at runtime. FastAPI also generates the OpenAPI document at `/api/openapi.json` (browsable at `/api/docs`). | A single-page-app feel. We use htmx (vendored, 50 KB) only where live updates matter: the organizer's progress view polls every 10 seconds. |
| **PostgreSQL** | The data is relational and the rules are relational: one team per person per event, one live project per team, one sheet per assignment. Postgres enforces those itself (unique and partial unique indexes, check constraints), so a bug in one route cannot break them. | SQLite's zero-config. Compose makes Postgres a single line anyway. |
| **Server-side sessions, token hashed at rest** | Revocable (logout deletes the row), inspectable, and the acceptance checker can be handed a fixed token without a login flow. Cookies are `HttpOnly` and `SameSite=Lax`, which also blocks cross-site form posts. `Authorization: Bearer <token>` works too, for scripts. | Stateless JWTs, which cannot be revoked without a server-side list anyway. |
| **Roles per event** (`event_roles`) | Someone judges one hackathon and competes in the next. A global "judge" flag would be wrong the first time that happens. | Slightly more joins. |
| **Scores computed on read** | Raw and normalized scores are derived from the stored values, the current weights and the current prior. An organizer who changes a weight sees the new ranking immediately, and a stored number can never disagree with its inputs. | A few milliseconds per results page. |
| **Access control in one module** | `app/auth.py` builds a `Viewer` (user + roles per event) once per request. Routes call `require_organizer`, `require_judge`, `require_admin`, and judge data is always *queried* by the caller's id. There is no role check in any template. | Nothing. |
| **Self-hosted everything** | Fonts (OFL), htmx and CSS are files in `src/app/static/`. No CDN, no analytics, no email service. Judge invitations are links the organizer copies. | Automatic invitation emails. |

## Request lifecycle

1. The request arrives at uvicorn → FastAPI.
2. `get_viewer` (a dependency) reads the `session` cookie or bearer token, looks up the hashed token, and loads the user's roles per event. Missing or expired means anonymous.
3. The route loads the event by slug and calls the relevant `require_*`. Failures raise `NotAuthenticated` or `Forbidden`.
4. Write paths call their rule functions **before looking at input**. For example, every project and team write calls `ensure_submissions_open(event)` first, so a late request is refused as late (`403 submissions_closed`), not as malformed.
5. Changes and their `audit_log` row are committed in the same transaction.
6. Exception handlers turn `NotAuthenticated` into `401` JSON for `/api/*` or a redirect to `/login` for pages, and `Forbidden` into `403` JSON or an HTML page. `Forbidden(audit=True)` also writes a `denied.*` audit row in its own transaction, so probing for other judges' scores leaves a trace.

## Source layout

```
src/app/
  main.py          app assembly, error handlers, security headers
  config.py        settings from environment variables
  db.py            engine and session
  models.py        the schema (see DATA-MODEL.md)
  auth.py          Viewer, require_* guards: every access decision
  security.py      argon2 passwords, session tokens, NotAuthenticated / Forbidden
  web.py           template rendering, flash messages
  seed.py          boot-time seeding (demo mode) and first admin (production mode)
  cli.py           import / export / create-admin / normalize
  services/
    deadline.py    the submission and judging deadlines
    projects.py    create / edit / submit / withdraw a team's project
    assignment.py  judge assignment algorithm
    scoring.py     weighted sheet scores, shrinkage normalization, ranking
    progress.py    numbers for the live organizer view
    importer.py    fixtures.json-shaped import (idempotent)
    exports.py     CSV and JSON exports
    audit.py       audit rows
    ratelimit.py   in-process sliding-window limiter (login)
  routers/
    public.py      home, gallery, project, event, published results
    account.py     login, logout, register, personal desk
    teams.py       teams, invite links, project editor
    judge.py       judge desk, score form, invitations
    organizer.py   event setup, rubric, judges, assignments, results, audit
    admin.py       instance admins and event creators
    api.py         JSON and CSV API
  templates/       Jinja2 pages (Broadsheet design)
  static/          CSS, self-hosted fonts, htmx
src/migrations/    Alembic
tests/             pytest suite against a real Postgres
scripts/           start.sh (migrate, seed, serve), normalization_report.py
```

Services hold the rules and the math and know nothing about HTTP (except raising the two access errors). Routers translate HTTP to services. The scoring math is pure functions over plain data, which is why it can be unit-tested and reproduced by `scripts/normalization_report.py` without a database.

## Modes

`DOGFOOD_MODE` controls seeding:

- **`demo`** (the default in `docker-compose.yml`): imports `data/fixtures.json`, creates demo accounts with one known password, adds an open "Practice Jam" event, and creates the four fixed session tokens the acceptance checker uses. It then prints them.
- **`production`**: seeds nothing. Set `ADMIN_EMAIL` and `ADMIN_PASSWORD` for a first admin, or run `python -m app.cli create-admin`. Put it behind TLS and set `COOKIE_SECURE=1`.

## Security notes

- Passwords: argon2id. Login is rate-limited per IP and email (10 per minute), and failed logins are audited.
- Sessions: 256-bit random tokens, stored hashed, `HttpOnly`, `SameSite=Lax`, optional `Secure`.
- CSRF: `SameSite=Lax` keeps the session cookie off cross-site POSTs. All state changes are POSTs.
- Headers: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: same-origin`.
- Templates auto-escape. User-supplied links get `rel="noopener nofollow"`, and project and demo URLs must be `http(s)://`.
- Open redirects: `next=` accepts only same-site paths.
- The database has no published port.
- The container runs as an unprivileged user.

## Scaling and limits

- One app container handles a hackathon of thousands of participants comfortably. The heaviest page (results) is a few indexed queries plus arithmetic over the event's sheets.
- The login rate limiter lives in process memory. Behind several app replicas it would move to Postgres or Redis; that is the one piece of state that is not in the database.
- Files (images, videos) are links, not uploads. Adding uploads would mean a volume or object storage.

## Testing

`docker compose run --rm app pytest` runs the suite against a separate `dogfood_test` database, migrated with the real Alembic migrations. Every test starts from truncated tables. Coverage priorities, in order: role isolation as raw HTTP (`test_isolation.py`), the deadline on every write path, the fixture import and its awkward cases, the normalization math, export and re-import, and one full lifecycle through the real pages (`test_lifecycle.py`).
