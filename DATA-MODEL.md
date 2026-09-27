# Data model

PostgreSQL 16, managed through SQLAlchemy 2 models (`src/app/models.py`) and Alembic migrations (`src/migrations/versions/`). This document explains every table, the constraints that protect the data, and how data gets in and out.

## Conventions

- **Integer surrogate keys** everywhere. Rows that came from an import keep the id they had in the file in an `external_id` column (`evt_01`, `prj_07`, `jdg_24`). That makes imports idempotent (a second import matches on `external_id` and changes nothing) and lets exports round-trip the original ids. `external_id` is unique per event, not globally, because two events imported from two files can both contain a `prj_01`.
- **Every timestamp is `timestamptz` and stored in UTC.** Forms label their inputs "UTC". There is no local-time guessing on the server.
- **Roles are per event** (`event_roles`). Being a judge at one hackathon says nothing about the next. The only global flags are `users.is_admin` and `users.can_create_events`.
- **The database enforces the rules that matter most**, not just the application: one team per person per event, one live project per team, one score sheet per assignment, score values in range, known statuses and roles.

## Diagram

```
users ──< event_roles >── events ──< tracks
  │                          │  ├──< prizes (→ tracks, optional)
  │                          │  ├──< rubric_criteria
  │                          │  ├──< judge_invites
  │                          │  └──< judge_tracks >── users, tracks
  │                          │
  ├──< team_members >── teams ──< projects ──< assignments ──1 scores ──< score_values >── rubric_criteria
  │                                  │  (duplicate_of → projects)  │
  │                                  └── tracks                    └── users (judge)
  ├──< sessions
  └──< audit_log (actor)  ── events (optional)
```

`──<` is one-to-many; `──1` is one-to-one.

## Tables

### `users`
One row per person, identified by lower-cased email (a `CHECK` enforces the lower-casing, so `Ana@x.org` and `ana@x.org` can never be two accounts).

| column | notes |
|---|---|
| `email` | unique, lower case |
| `name` | display name |
| `password_hash` | argon2id; nullable for accounts that exist only through an import and have not set a password |
| `is_admin`, `can_create_events` | the only global permissions |
| `external_id` | e.g. `jdg_24` for imported judges; used in URLs such as `/api/judges/jdg_24/scores` |

### `sessions`
Server-side sessions. The cookie (or `Authorization: Bearer`) holds a random 256-bit token. The table stores only its SHA-256 hash, so a database dump does not contain live sessions. Sessions expire (`expires_at`, 14 days by default) and are deleted on logout. `label` records where a session came from (`login`, `register`, `checker:judge_a`).

### `events`
| column | notes |
|---|---|
| `slug` | unique, used in every URL |
| `starts_at`, `submissions_close_at` | `CHECK (submissions_close_at > starts_at)`. The deadline rule reads these two columns and nothing else. |
| `judging_close_at` | optional; after it, scores lock |
| `results_published_at` | null = results sealed. Publishing is refused while submissions are open. |
| `max_team_size` | 1–20, default 4 |
| `reviews_per_project` | 1–20, default 3: the assignment target and the "under-reviewed" threshold |
| `shrinkage_k` | normalization prior strength, default 3 (see JUDGING.md) |
| `voting_mode`, `voting_open_at`, `voting_close_at` | community voting (T3) |

The event's *phase* (upcoming / open / judging / results) is **derived** from the timestamps, never stored, so it cannot drift out of sync with the clock.

### `event_roles`
`(event_id, user_id, role)`, unique, with `role` constrained to `organizer | judge | participant`. Joining a team grants `participant`, accepting a judge invitation grants `judge`, and creating an event grants `organizer`. `app/auth.py` loads a person's roles once per request.

### `tracks`, `prizes`
Tracks belong to an event and are ordered by `position`. A prize may be tied to a track or be event-wide. A track that projects are entered in cannot be deleted.

### `judge_tracks`
`(event_id, user_id, track_id)`: which tracks a judge covers in an event. Assignment prefers judges whose tracks include the project's track.

### `judge_invites`
An invitation for one email address with a random token, plus the tracks the judge will cover. Only a logged-in user whose email matches can accept it. `accepted_at` makes it single-use.

### `teams`, `team_members`
`teams.name` is **deliberately not unique**: the fixture has three teams called "StillTrail", which is realistic. `invite_code` is a random token; the lead can rotate it, which kills the old link.

`team_members` copies `event_id` from its team so the database can enforce **`UNIQUE (event_id, user_id)`: one team per person per event**, no matter which code path adds the row. `is_lead` marks who may rotate the invite and remove members. When the lead leaves, the longest-standing member becomes lead.

### `projects`
| column | notes |
|---|---|
| `status` | `draft | submitted | withdrawn` (`CHECK`) |
| `submitted_at` | set when (re)submitted |
| `duplicate_of_id` | set when this is a team's second project, pointing at the first |
| `duplicate_dismissed` | the organizer kept this copy and withdrew the original |

**`one_live_project_per_team`**: a *partial* unique index on `team_id` `WHERE status <> 'withdrawn' AND duplicate_of_id IS NULL`. A team can have at most one live project. A flagged duplicate does not count, which is exactly how the fixture's `prj_41` can exist next to `prj_07` while an organizer decides. Withdrawn projects stay in the table for the record.

"In the ranking" = `status = 'submitted' AND duplicate_of_id IS NULL`.

### `rubric_criteria`
`(event_id, key)` unique; `weight >= 0`; `max_value > min_value`. Weights are read at scoring time, so changing one recomputes every result without touching a score. A criterion with scores cannot be deleted, only weighted 0.

### `assignments`
`(judge_id, project_id)` unique: a judge reviews a project at most once. `status` is `pending | done`. `method` records how it was created (`import | auto | manual`), which the organizer sees and the audit trail explains.

### `scores`, `score_values`
One `scores` row per assignment (`UNIQUE assignment_id`) holds the comment and timestamps. `score_values` holds one integer per criterion: `(score_id, criterion_id)` is the primary key. The judge form validates each value against its criterion's range before saving.

The criteria are **normalized into rows rather than stored as a JSON blob**, which is the main schema decision worth defending. It lets the database guarantee a value refers to a real criterion of the same event, lets organizers add or reweight criteria mid-event, and makes per-criterion statistics a plain `GROUP BY`. The fixture's `"criteria": {"functionality": 4, ...}` object becomes three `score_values` rows.

Raw weighted and normalized scores are **not stored**. They are recomputed from `score_values`, the current weights and the current `shrinkage_k` (`services/scoring.py`). With hundreds of sheets this takes milliseconds, and it means a stored number can never disagree with its inputs.

### `audit_log`
Append-only: nothing in the application updates or deletes it. `action` (e.g. `score.updated`, `denied.peer_scores`), `actor_id`, `event_id`, `entity_type` / `entity_id`, a JSONB `detail` with before/after values where relevant, and the client IP. Indexed on `at` and `event_id`, because the organizer view reads it newest-first per event.

## From fixtures.json to tables

`src/app/services/importer.py`:

| fixtures.json | becomes |
|---|---|
| `event` | `events` row. `starts_at` is not in the file, so it is set to `submissions_close − 72h`, before every fixture submission. |
| `tracks[]` | `tracks` |
| every key seen in `scores[].criteria` | `rubric_criteria` (weight 1, scale 1–5) |
| `judges[]` | `users` (email, name, `external_id`) + `event_roles(judge)` + `judge_tracks` |
| `teams[].members[]` | `users` (name = the email's local part) + `team_members` (first member is the lead) + `event_roles(participant)` |
| `projects[]` | `projects` with `status = submitted`. Processed oldest first; a team's second project gets `duplicate_of_id` = its first. |
| `scores[]` | a finished `assignments` row + `scores` + `score_values` |
| (after import) | projects below `reviews_per_project` get *pending* assignments, so the progress view shows the unfinished review batches |

Unknown judge or project ids in `scores` are skipped and reported, not fatal.

## Getting data in

- **On boot (demo mode):** `docker compose up` runs `alembic upgrade head`, then `python -m app.seed`, which imports `data/fixtures.json` (idempotently) and creates the demo accounts.
- **Any time:** `docker compose exec app python -m app.cli import path/to/event.json` imports a fixtures-shaped file into a running instance. Re-running it is safe.
- **Coming from another platform:** convert its export to the fixtures shape (six arrays, documented above), then use the same command.

## Getting data out

| What | Where | Who |
|---|---|---|
| Ranking with raw and normalized scores, review counts, standard errors, rank change, excluded projects | `GET /api/events/<slug>/export/results.csv` | organizer |
| Every score sheet: judge, project, each criterion, weighted raw, normalized, comment | `GET /api/events/<slug>/export/scores.csv` | organizer |
| Projects with team members, links, status, duplicate flag | `GET /api/events/<slug>/export/projects.csv` | organizer |
| The whole event in the fixtures.json shape (re-importable) | `GET /api/events/<slug>/export.json` or `python -m app.cli export <slug>` | organizer |
| Everything | `docker compose exec db pg_dump -U dogfood dogfood > backup.sql` | operator |

`tests/test_roundtrip.py` exports the fixture event and imports it again as a new event, and checks that every project, team and score survives.

## Migrations

Schema changes are Alembic revisions in `src/migrations/versions/`. They are applied automatically on every boot (`alembic upgrade head` in `scripts/start.sh`) and by the test suite before it runs, so the tests always exercise the real migration path, not `create_all`. To add one:

```
docker compose run --rm -v "$PWD/src:/app/src" app alembic revision --autogenerate -m "describe the change"
```
