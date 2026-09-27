# Broadsheet API & Webhooks Reference

Broadsheet provides a complete REST API alongside an event-driven Webhook notification system to automate hackathon operations, integrate custom dashboards, and sync results.

---

## 1. Webhooks

Broadsheet can dispatch real-time HTTP POST notifications whenever key hackathon events occur.

### Supported Events

| Event | Trigger | Payload |
|---|---|---|
| `project.submitted` | A participant team submits their project before the deadline | `{"project_id": "...", "title": "...", "team": "...", "event": "..."}` |
| `score.submitted` | A judge completes and saves a score sheet | `{"project_id": "...", "judge_id": "..."}` *(Strictly preserves judge privacy — does not leak scores or comments)* |
| `results.published` | An organizer publishes final standings | `{"event": "..."}` |

### Delivery Envelope

Every webhook is sent as a `POST` request with `Content-Type: application/json` and `User-Agent: Broadsheet-Webhooks/1.0`:

```json
{
  "event": "project.submitted",
  "data": {
    "project_id": "prj_01",
    "title": "Quantum Leap",
    "team": "CyberPioneers",
    "event": "sample-hack-2026"
  },
  "sent_at": "2026-09-27T09:45:00.000000Z"
}
```

### Signature Verification

Every request includes the `X-Broadsheet-Signature` header containing the HMAC-SHA256 digest of the raw request payload body signed with the webhook's shared secret:

$$\text{X-Broadsheet-Signature: sha256=abcdef...}$$

#### Verifying Signatures in Python:

```python
import hashlib
import hmac

def verify_signature(secret: str, raw_body: bytes, header_signature: str) -> bool:
    if not header_signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header_signature)
```

#### Verifying Signatures in Node.js:

```javascript
const crypto = require('crypto');

function verifySignature(secret, rawBody, headerSignature) {
    const hmac = crypto.createHmac('sha256', secret);
    const expected = 'sha256=' + hmac.update(rawBody).digest('hex');
    return crypto.timingSafeEqual(Buffer.from(expected), Buffer.from(headerSignature));
}
```

---

## 2. REST API

The interactive OpenAPI / Swagger UI is available at `/api/docs`, and the OpenAPI 3.0 specification is served at `/api/openapi.json`.

### Authentication
Include session cookie `session=<token>` or HTTP header `Authorization: Bearer <token>`.

### Key Endpoints

| Method | Endpoint | Access | Purpose |
|---|---|---|---|
| `GET` | `/api/events` | Public | List all events and their phases |
| `GET` | `/api/events/{slug}/projects` | Public (CORS enabled) | List submitted projects for an event |
| `POST` | `/api/events/{slug}/projects` | Participant | Create or update team project |
| `GET` | `/api/judge/scores` | Judge | Get caller's own assigned score sheets |
| `GET` | `/api/judges/{id}/scores` | Judge / Organizer | Scoped judge scores (peer access returns 403) |
| `GET` | `/api/events/{slug}/results` | Public (if published) / Organizer | Normalization ranking and statistical metrics |
| `GET` | `/api/events/{slug}/export/results.csv` | Organizer | CSV export of final standings and raw/normalized scores |
| `GET` | `/api/events/{slug}/export/scores.csv` | Organizer | CSV export of every score sheet and criterion value |
| `GET` | `/api/events/{slug}/export/projects.csv` | Organizer | CSV export of projects and team member metadata |
| `GET` | `/api/events/{slug}/export/votes.csv` | Organizer | CSV export of community approval votes |
| `GET` | `/api/events/{slug}/export.json` | Organizer | Full event JSON export compatible with CLI re-import |

---

## 3. Certificates

Every submitted project has a printable, publicly shareable certificate at:

```
GET /events/{slug}/certificates/{project_id}
```

It shows the event, team, project title and members, and — once the organizer has published results — the project's final rank. No PDF library is used: the browser's own "Print to PDF" is enough. Before results are published, the rank is simply omitted rather than the page erroring.

---

## 4. Signed judge participation records

A judge's record of how many reviews they completed, per event, is public and cryptographically signed so it can be verified by anyone without needing access to the database:

```
GET /judges/{judge_id}/record          human-readable page with the signature
GET /judges/{judge_id}/record/verify?sig=...   returns {"valid": true|false}
```

The signature is an HMAC-SHA256 of `judge_id|event_slug|reviews_done`, keyed with the server's `INSTANCE_SECRET` (see README.md's production section — **set this environment variable**, or signatures stop verifying every time the container restarts). The record intentionally contains only counts and event names: no project titles, no score values, no comments. That is enforced in `src/app/services/judge_records.py` and checked in `tests/test_judge_records.py`.

---

## 5. Embeddable gallery widget

Any external site can embed a live list of an event's submitted projects with one script tag and no build step:

```html
<div id="broadsheet-gallery"></div>
<script src="https://<your-broadsheet-host>/embed/{slug}/gallery.js"
        data-target="#broadsheet-gallery"></script>
```

The script (`src/app/static/js/embed.js`, vendored, ~70 lines of plain JS, no dependencies) fetches `GET /api/events/{slug}/projects` — the one endpoint on this API that sends `Access-Control-Allow-Origin: *`, since it is public, read-only and non-sensitive — and renders a simple card grid into the target element. A live preview with the exact snippet to copy is at:

```
GET /embed/{slug}/preview
```

also linked from the organizer's "Overview" tab.
