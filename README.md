# Broadsheet

**A self-hosted hackathon submission and judging platform.** Teams form by invite link and submit projects until a deadline that actually holds. Judges score on a weighted rubric the organizer controls, and never see each other's scores. The organizer watches progress live, corrects for harsh and generous judges with a documented method, and publishes results. Everything runs from one command, offline.

Built by **Binary Builders** (Pawan Bhandari, Rahul) for [DOGFOOD 2026](https://dogfoodhack.com). MIT licensed.

---

## Run it

You need Docker (Docker Desktop on Windows or macOS). Then:

```
docker compose up
```

The first run pulls the Python and Postgres base images. Python packages are vendored in the repo, so after that everything, including rebuilds, works with the network off. When it is ready the log prints:

```
seeded. fixtures: 1 events, 8 tracks, 3 criteria, 121 users, 30 judge roles, 40 teams, 41 projects, 126 scores, 8 pending assignments
  flagged duplicate: prj_41 is a second submission by team tm_07 (first: prj_07, same repo)

web logins (password for every seeded account: dogfood-demo)
  admin        admin@dogfood.local
  organizer    organizer@dogfood.local
  judge_a      diego.herrera@example.org
  judge_b      jonas.vogel@example.org
  participant  priya1@example.org

test logins for .dogfood.toml:
  organizer    Cookie: session=org_binarybuilders_demo
  ...
```

Open **http://localhost:8080**. Log in as any account above with the password `dogfood-demo`. Every fixture judge and team member can log in too, with the same password.

Two events are seeded:

- **Sample Hack 2026**: the whole of `fixtures.json`. Its submissions closed on 1 March 2026, so it is in the judging phase and refuses new submissions.
- **Practice Jam**: open for 30 days from first boot, so you can try the participant flow (start a team, invite, draft, submit).

Stop with `Ctrl+C`. `docker compose down -v` also deletes the database.

## A tour, by role

| Role | Try this |
|---|---|
| **Visitor** | `/projects`: the gallery, with search and track/event filters. Open any project. Results pages stay sealed until published. |
| **Participant** (`priya1@example.org`) | Open *Practice Jam* → start a team → copy the invite link → open it logged in as someone else to join → draft the project → submit → edit → withdraw. After the deadline every one of those buttons is refused by the server. |
| **Judge** (`diego.herrera@example.org`) | *Judge desk*: only your own assignments, a keyboard-friendly score form, *Save & next*. Try `/api/judges/jdg_26/scores`: another judge's scores → **403**. |
| **Organizer** (`organizer@dogfood.local`) | *Organizer desk* for Sample Hack 2026: live progress; the flagged duplicate; *Rubric* weights; *Judges* invitations; *Assignments* (automatic or by hand); *Results* raw vs normalized with judge calibration; *Audit log*; CSV and JSON exports. |
| **Admin** (`admin@dogfood.local`) | `/admin`: who is an admin and who may create events. |

## Check it

```
python3 run.py .dogfood.toml > acceptance-report.txt   # the organizers' acceptance checker
docker compose run --rm app pytest                     # our own test suite (44 tests)
docker compose run --rm app python scripts/normalization_report.py   # the normalization proof
```

The committed [acceptance-report.txt](acceptance-report.txt) is the checker's output against this repository.

## What is built

**T1, core: done**
- Accounts, login, logout and server-side sessions. Roles: visitor, participant, judge, organizer (per event) and admin (instance).
- Event creation with dates, tracks and prizes, all editable afterwards.
- Team formation by invite link, with a team size limit, one team per person per event, and lead-only link rotation and removal.
- Projects: draft, submit, edit until the deadline, withdraw.
- A deadline enforced by the server on every write path, checked before anything else. `403 submissions_closed`.
- Public gallery with search and filters. Drafts and flagged duplicates are hidden.

**T2, judging: done**
- Judge invitations bound to the invited email; judges declare the tracks they cover.
- Assignment: automatic (track-matched, least-loaded, never a judge's own team, reproducible by seed) and manual.
- A rubric with organizer-set weights and scales; changing weights recomputes results instantly.
- Backend role isolation: judges can only ever query their own sheets. Refused attempts are audited.
- A live organizer progress view (refreshes every 10 s): projects below target, judges behind, and judges who score everything the same.
- Cross-judge normalization: per-judge z-scores with shrinkage, raw vs normalized side by side, per-project standard error. Method and proof in [JUDGING.md](JUDGING.md).
- CSV exports of results, score sheets and projects, plus a re-importable JSON export.

**T3, community voting & comments: built & tested**
- Authenticated approval voting with configurable `max_votes` and strict window enforcement.
- Deterministic per-voter randomized ballot order (`random.Random(f"{event.id}:{user.id}")`) to eliminate position bias.
- Sealed tallies: live counts return 403 until the voting window closes (organizers can preview).
- Public project comments with 1–2000 character validation, rate limiting, and organizer soft-delete moderation.
- Comprehensive anti-abuse protections: rate limits (30 votes/min, 5 comments/min), duplicate detection via DB constraints, and organizer abuse signals (new accounts, shared IPs, rapid voters).
- Full threat analysis documented in [THREAT-MODEL.md](THREAT-MODEL.md).

**T4, stretch: built & tested**
- REST API documented with an interactive OpenAPI UI at `/api/docs` (spec at `/api/openapi.json`), covering events, projects, judge scores, results, and every export.
- Bulk import/export: `python -m app.cli import/export`, plus `/api/events/{slug}/export.json` (round-trips through the fixtures.json shape).
- Webhooks: organizers can register a URL per event for `project.submitted`, `score.submitted` and `results.published`, delivered with an HMAC-SHA256 signature (`X-Broadsheet-Signature`), a delivery log, and a guarantee that a failing webhook never blocks the real action. Score-submitted payloads never include score values or comments.
- Certificates: a printable, publicly shareable page per submitted project (`/events/{slug}/certificates/{project_id}`), showing the final rank once published.
- Signed, publicly verifiable judge participation records (`/judges/{judge_id}/record` + `/record/verify`): an HMAC-signed count of reviews per event, verifiable by anyone, with no project titles or scores exposed.
- An embeddable gallery widget: one `<script>` tag, no build step, pulling live data from the one CORS-enabled public endpoint. Preview and copyable snippet at `/embed/{slug}/preview`, linked from the organizer overview.
- Full details, payload shapes and verification code samples in [API.md](API.md).

**Tier claim note:** `run.py` only checks T1 and T2 (confirmed with the organizers on Discord: T3/T4 are verified by a judge reading the repo, tests and docs, not by the script). `acceptance-report.txt` will always show `note: claimed but not verified: T3` and, once claimed, the same for T4 — that is expected, not a red flag.

## Known gaps, honestly

- **No emails.** The portal runs offline, so invitations and invite links are copied and sent by hand. There is no password-reset flow; an operator can reset an admin with `python -m app.cli create-admin`.
- **No file uploads.** Projects link to their repository and demo; there are no image galleries.
- **The fixture event has no start date.** We use submissions_close − 72 hours, which is before every fixture submission.
- **Normalization cannot fully separate a judge's leniency from the quality of the batch they drew.** JUDGING.md explains the limitation and what would fix it.
- **The login and voting rate limiters are in memory.** That is correct for the single app container we ship, but would need Postgres or Redis behind several replicas.

## Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md): system design and the decisions behind it.
- [DATA-MODEL.md](DATA-MODEL.md): every table, its constraints, and the import and export paths.
- [JUDGING.md](JUDGING.md): assignment, scoring, normalization with a worked proof on the fixture data, and isolation.
- [THREAT-MODEL.md](THREAT-MODEL.md): analysis of Sybil attacks, ballot stuffing, collusion, scraping, and deadline gaming.

## Operating it for real

- Set `DOGFOOD_MODE=production` in `docker-compose.yml`. That turns off the demo data, the known password and the fixed tokens.
- Create the first admin: `docker compose exec app python -m app.cli create-admin you@example.org`.
- Put it behind a TLS reverse proxy and set `COOKIE_SECURE=1`.
- Import an existing event: `docker compose exec app python -m app.cli import event.json` (fixtures.json shape).
- Back up: `docker compose exec db pg_dump -U dogfood dogfood > backup.sql`.

**Offline build:** Linux wheels for x86_64 and arm64 are vendored in `vendor/wheels/`, so once the `python:3.12-slim` and `postgres:16-alpine` images are on the machine, `docker compose up --build` works with the network off (tested with `docker build --network=none`). If the wheels ever do not match, the Dockerfile falls back to PyPI.

## License

[MIT](LICENSE). Fonts are self-hosted under the SIL Open Font License (Fraunces, Atkinson Hyperlegible, Martian Mono); their licenses are in `src/app/static/fonts/`. htmx is 0BSD.
