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
