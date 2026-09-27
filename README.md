# Broadsheet

**Self-hosted hackathon submission and judging platform — DOGFOOD 2026 · Binary Builders**

> Hackathon submissions & judging, printed fresh and run by you.

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

---

## Quick start

```bash
git clone https://github.com/PawanBhandari03/DogFood-HackaThon.git
cd DogFood-HackaThon
docker compose up
```

Open **http://localhost:8080** — the platform boots, runs migrations, and seeds full fixture data automatically. No cloud account, no API keys, works fully offline.

---

## Demo logins

All accounts share the password **`dogfood-demo`**. They are printed in the terminal when the container starts, and also shown on the `/login` page in demo mode.

| Role | Email | What they can do |
|---|---|---|
| **Admin** | `admin@dogfood.local` | Manage users, grant event-creation rights |
| **Organizer** | `organizer@dogfood.local` | Create events, invite judges, assign, publish results |
| **Judge A** | `diego.herrera@example.org` | Score assigned projects, view own record |
| **Judge B** | `jonas.vogel@example.org` | Score assigned projects (use for the isolation demo) |
| **Participant** | `priya1@example.org` | Team member, project submitter, community voter |

### Checker session tokens (for `run.py`)

```
organizer   →  Cookie: session=org_binarybuilders_demo
judge_a     →  Cookie: session=jdg_a_binarybuilders_demo
judge_b     →  Cookie: session=jdg_b_binarybuilders_demo
participant →  Cookie: session=prt_binarybuilders_demo
```

---

## Run the official checker

```bash
python run.py .dogfood.toml --fixtures data/fixtures.json
```

Expected output:

```
claimed T1 T2 T3 T4, verified T1 T2
note: claimed but not verified: T3 T4
```

T3 and T4 have no automated checks in `run.py` by design — they are verified by a judge reading the repo, tests and docs (confirmed on Discord).

---

## Run the tests

```bash
docker compose run --rm app pytest
# 78 tests, all pass
```

---

## How the platform works

### Full event lifecycle

```mermaid
flowchart LR
    A([Organizer\ncreates event]) --> B([Participants\nform teams])
    B --> C([Teams submit\nprojects])
    C --> D{Deadline\npasses}
    D --> E([Judges score\nassigned projects])
    E --> F([Organizer\npublishes results])
    F --> G([Public sees\nfinal ranking])
    B --> V([Community votes\nduring voting window])
    V --> G
```

### System architecture

```mermaid
flowchart TD
    Browser -->|HTTP| FastAPI
    FastAPI -->|SQLAlchemy 2| PostgreSQL
    FastAPI -->|Jinja2 + htmx| Browser
    FastAPI -->|Webhooks| ExternalSite[External\nwebhook URLs]
    FastAPI -->|CORS-enabled API| Widget[Embeddable\ngallery widget]

    subgraph FastAPI["FastAPI app (src/app/)"]
        Routers["Routers\npublic · organizer · judge\nteams · api · voting · account"]
        Services["Services\nscoring · assignment · voting\nwebhooks · journey · audit"]
        Auth["auth.py\nViewer + role enforcement"]
        Routers --> Auth
        Auth --> Services
    end
```

### Role model

```mermaid
flowchart LR
    subgraph Global
        Admin["is_admin\n(instance-wide)"]
        Creator["can_create_events\n(instance-wide)"]
    end
    subgraph PerEvent["Per-event roles"]
        Organizer
        Judge
        Participant
        Visitor
    end
    Admin -->|"can act as"| Organizer
    Creator -->|"may create events"| Organizer
```

Roles are **per event**, not global. A user can be a judge in one event and a participant in another. Only `is_admin` and `can_create_events` are instance-wide flags. All access decisions are enforced in `src/app/auth.py` — never just hidden in a template.

### Submission deadline enforcement

```mermaid
sequenceDiagram
    participant Browser
    participant Server
    participant DB

    Browser->>Server: POST /events/{slug}/team (submit project)
    Server->>DB: load event
    Server->>Server: ensure_submissions_open(event)
    alt Deadline has passed
        Server-->>Browser: 403 submissions_closed (audited)
    else Open
        Server->>DB: save project
        Server-->>Browser: 302 redirect
    end
```

The deadline is checked **server-side on every write**. It is not just a disabled button.

### Judge isolation (T2 core requirement)

```mermaid
sequenceDiagram
    participant JudgeB as Judge B (browser)
    participant Server
    participant AuditLog

    JudgeB->>Server: GET /api/judges/judge_a_id/scores
    Server->>Server: viewer is not admin, not organizer,\nnot the target judge
    Server->>AuditLog: write denied.peer_scores row
    Server-->>JudgeB: 403 Forbidden
```

A judge can only ever query their own score sheets. Any peer-score request is blocked by the backend and logged in the audit trail. This is the single most important safety invariant in the platform.

### Score normalization

```mermaid
flowchart TD
    Raw["Raw weighted\nscores per judge"] --> Z["Per-judge z-score\nnormalization"]
    Z --> Shrink["Empirical-Bayes\nshrinkage (k=3)"]
    Shrink --> Rank["Final ranked\nleaderboard"]

    subgraph Shrink
        formula["m̂_j = (n_j·m_j + k·M) / (n_j + k)\nŝ_j = sqrt((n_j·s_j² + k·S²) / (n_j + k))"]
    end
```

Judges who are harsher or more generous than average are corrected. A judge with very few reviews is pulled toward the event mean (shrinkage), so they cannot distort the ranking. The full proof with fixture numbers is in [JUDGING.md](JUDGING.md).

---

## Tier ladder

### T1 — Core (verified by `run.py`)

- Accounts, login, logout, server-side sessions, argon2 password hashing
- Per-event roles: visitor, participant, judge, organizer; global: admin, can_create_events
- Event creation: dates, tracks, prizes, rubric weights (all editable after creation)
- Team formation by invite link; size limit; lead-only link rotation; one team per person per event
- Projects: draft → submit → edit until deadline → withdraw
- Duplicate detection: if a team submits twice, the later submission is flagged `duplicate_of_id` and excluded from ranking
- **Server-enforced deadline** on every write path (`services/deadline.py`)
- Public gallery with full-text search and filters — no login required

### T2 — Judging (verified by `run.py`)

- Judge invitations bound to the invited email; judges declare track coverage
- Assignment engine: track-matched, least-loaded, never a judge's own team, reproducible by seed; also manual override
- Rubric with organizer-set weights and per-criterion min/max scales; changing weights recomputes results instantly without re-scoring
- **Backend role isolation:** judges can only query their own sheets; refused attempts are audited (see diagram above)
- Live organizer progress view (auto-refreshes every 10 s): projects below target, judges behind, judges who score everything the same
- Cross-judge normalization with shrinkage (see above); raw vs. normalized side by side; per-project standard error
- CSV exports (results, score sheets, projects, votes) + re-importable JSON export

### T3 — Community voting (built & tested, verified manually)

- Authenticated approval voting with configurable `max_votes` per event
- Strict voting window: votes are accepted only between `voting_open_at` and `voting_close_at`
- Deterministic per-voter ballot order (`random.Random(f"{event.id}:{user.id}")`) — eliminates position bias
- **Sealed tallies:** live counts return 403 until voting closes; organizers can preview at any time
- Public project comments (1–2000 chars) with rate limiting and organizer soft-delete moderation
- Anti-abuse: 30 votes/min rate limit, DB-constraint duplicate detection, organizer abuse signals (new accounts, shared IPs, rapid voters)
- Threat analysis: [THREAT-MODEL.md](THREAT-MODEL.md)

### T4 — Stretch (built & tested, verified manually)

- **REST API** with interactive OpenAPI UI at `/api/docs`, spec at `/api/openapi.json`
- **Bulk import/export:** `python -m app.cli import/export` + `/api/events/{slug}/export.json`
- **Webhooks:** organizers register per-event URLs for `project.submitted`, `score.submitted`, `results.published`; HMAC-SHA256 signed payloads; delivery log; a failed webhook never blocks the real action; score payloads never include score values or comments
- **Certificates:** printable public page per submitted project at `/events/{slug}/certificates/{project_id}`; shows final rank once published
- **Signed judge records:** `/judges/{id}/record` + `/record/verify` — HMAC-signed count of reviews per event, publicly verifiable without database access; no project data exposed
- **Embeddable gallery widget:** one `<script>` tag, no build step; preview + copy snippet at `/embed/{slug}/preview`; only the public read-only projects endpoint gets `Access-Control-Allow-Origin: *`
- **Participant journey dashboard:** `/me` shows a 4-stage pipeline card per team (Submit → Screen → Judge → Select)

Full API docs, payload shapes, signature verification code samples: [API.md](API.md)

---

## Repository layout

```
broadsheet/
├── src/app/
│   ├── main.py              # FastAPI app, router registration
│   ├── models.py            # SQLAlchemy models (single source of truth)
│   ├── auth.py              # Viewer dataclass + all role checks
│   ├── security.py          # Argon2, session tokens, Forbidden/NotAuthenticated
│   ├── config.py            # Settings from environment variables
│   ├── seed.py              # Demo mode: import fixtures, set checker tokens
│   ├── routers/
│   │   ├── public.py        # Gallery, events, projects, embed widget
│   │   ├── organizer.py     # Organizer desk (all manage/ routes)
│   │   ├── judge.py         # Judge desk, scoring, participation records
│   │   ├── api.py           # REST API (/api/...)
│   │   ├── voting.py        # T3 community voting & comments
│   │   ├── teams.py         # Team formation, invites
│   │   └── account.py       # Login, register, /me dashboard
│   ├── services/
│   │   ├── scoring.py       # Normalization, ranking, event_results()
│   │   ├── assignment.py    # Judge assignment engine
│   │   ├── voting.py        # T3 ballot, cast/retract, tallies, abuse signals
│   │   ├── webhooks.py      # T4 webhook delivery (fire-and-forget)
│   │   ├── journey.py       # T4 participant pipeline card data
│   │   ├── judge_records.py # T4 signed participation records
│   │   ├── exports.py       # CSV + JSON exports
│   │   ├── importer.py      # Idempotent fixture importer
│   │   ├── audit.py         # Append-only audit log writer
│   │   ├── deadline.py      # ensure_submissions_open / ensure_judging_open
│   │   └── progress.py      # Live organizer progress numbers
│   ├── templates/           # Jinja2 HTML templates
│   └── static/              # CSS, vendored htmx, embed.js, hero image
├── src/migrations/          # Alembic migrations (never edit existing ones)
├── tests/                   # pytest suite (78 tests)
├── data/
│   └── fixtures.json        # 40 teams, 41 projects, 30 judges, 126 scores
├── vendor/wheels/           # 51 pre-built Linux wheels for offline install
├── scripts/
│   ├── start.sh             # Container entrypoint
│   ├── normalization_report.py  # Reproduce JUDGING.md proof from fixtures
│   └── dev/                 # Development helpers (not needed to run the app)
├── docker-compose.yml
├── Dockerfile
├── .dogfood.toml            # Tier claims + checker auth tokens
├── acceptance-report.txt    # Output of python run.py .dogfood.toml
├── JUDGING.md               # Full normalization proof with fixture numbers
├── THREAT-MODEL.md          # T3 security analysis
├── API.md                   # REST API, webhooks, certificates, embed widget docs
├── ARCHITECTURE.md          # Architecture decisions and trade-offs
└── DATA-MODEL.md            # Full schema, every table and key decision explained
```

---

## Environment variables

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://dogfood:dogfood@localhost:5432/dogfood` | Set by docker-compose.yml |
| `DOGFOOD_MODE` | `demo` | `demo` seeds fixtures and uses known tokens; `production` randomizes everything |
| `FIXTURES_PATH` | `data/fixtures.json` | Path to the fixtures file to seed on first boot |
| `SESSION_DAYS` | `14` | Session cookie lifetime |
| `COOKIE_SECURE` | `0` | Set to `1` in production (HTTPS only) |
| `INSTANCE_SECRET` | *(random on each restart)* | **Set this in production.** Used to sign judge participation records. If unset, signatures become invalid on container restart. |
| `DEMO_PASSWORD` | `dogfood-demo` | Password for all demo accounts seeded from fixtures |

---

## Production checklist

1. Set `DOGFOOD_MODE=production` — disables fixture seeding and fixed checker tokens
2. Set `INSTANCE_SECRET` to a stable random value (`python -c "import secrets; print(secrets.token_hex(32))"`)
3. Set `COOKIE_SECURE=1` (requires HTTPS)
4. Set `DATABASE_URL` to your Postgres connection string
5. Put a reverse proxy (nginx, Caddy) in front — the app listens on port 8080

---

## Key design decisions

**Why server-side sessions instead of JWTs?** Sessions can be revoked immediately. JWTs can't be invalidated without a blocklist, which is essentially a session store anyway.

**Why Postgres-only and no SQLite fallback?** The partial unique index (`one_live_project_per_team WHERE status <> 'withdrawn'`) and JSONB audit detail require Postgres features. Supporting both databases would mean not using either properly.

**Why store `score_values` as rows instead of a JSON blob?** The DB can enforce that each value references a real criterion of the same event. Organizers can add or reweight criteria mid-event. Per-criterion statistics are a plain `GROUP BY`. A stored number can never disagree with its inputs — raw/normalized scores are always recomputed.

**Why shrinkage normalization instead of a plain z-score?** A judge with one review would otherwise have enormous influence. Shrinkage pulls their estimated mean and spread toward the event-wide values, proportional to how few reviews they have. A judge with 10+ reviews is barely affected; a judge with 1 review is pulled about 75% toward the event mean.

---

## Built by

[Binary Builders](https://github.com/PawanBhandari03/DogFood-HackaThon) for DOGFOOD 2026 · MIT licensed
