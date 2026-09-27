"""Webhook delivery service (T4).

Signs payloads using HMAC-SHA256 (GitHub-compatible header format), dispatches HTTP POST
requests to active subscribed endpoints, and logs delivery results and audit events.
"""

import hashlib
import hmac
import json
import urllib.error
import urllib.request
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Event, Webhook, WebhookDelivery, utcnow
from app.services import audit


def sign(secret: str, body: bytes) -> str:
    """Computes HMAC-SHA256 signature in GitHub-style `sha256=<hexdigest>` format."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def fire(db: Session, event: Event, event_type: str, payload: dict[str, Any]) -> None:
    """Dispatches webhook notifications to all active subscribed endpoints for the event."""
    webhooks = list(
        db.scalars(
            select(Webhook).where(
                Webhook.event_id == event.id,
                Webhook.is_active.is_(True),
            )
        ).all()
    )

    subscribed = [w for w in webhooks if event_type in (w.subscribed_events or [])]
    if not subscribed:
        return

    now_iso = utcnow().isoformat()
    envelope = {
        "event": event_type,
        "data": payload,
        "sent_at": now_iso,
    }
    body_bytes = json.dumps(envelope).encode("utf-8")

    for w in subscribed:
        signature = sign(w.secret, body_bytes)
        req = urllib.request.Request(
            w.url,
            data=body_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Broadsheet-Signature": signature,
                "User-Agent": "Broadsheet-Webhooks/1.0",
            },
            method="POST",
        )

        status_code = None
        error_msg = ""
        succeeded = False

        try:
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                status_code = resp.status
                succeeded = 200 <= status_code < 300
        except urllib.error.HTTPError as e:
            status_code = e.code
            error_msg = str(e)
            succeeded = False
        except Exception as e:
            error_msg = str(e)
            succeeded = False

        delivery = WebhookDelivery(
            webhook_id=w.id,
            event_type=event_type,
            payload=envelope,
            status_code=status_code,
            error=error_msg[:2000] if error_msg else "",
            attempted_at=utcnow(),
            succeeded=succeeded,
        )
        db.add(delivery)
        audit.record(
            db,
            "webhook.delivered" if succeeded else "webhook.delivery_failed",
            event_id=event.id,
            entity_type="webhook",
            entity_id=w.id,
            detail={
                "event_type": event_type,
                "url": w.url,
                "status_code": status_code,
                "succeeded": succeeded,
            },
        )

    try:
        db.commit()
    except Exception:
        db.rollback()


def safe_fire(db: Session, event: Event, event_type: str, payload: dict[str, Any]) -> None:
    """Safe wrapper that catches any exception during webhook delivery to protect caller flow."""
    try:
        fire(db, event, event_type, payload)
    except Exception:
        pass
