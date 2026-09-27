"""Webhooks registration, dispatch, signature verification, and delivery logging tests (T4)."""

import json
from unittest.mock import MagicMock, patch

from sqlalchemy import select

from app.models import Assignment, AssignmentStatus, Project, ProjectStatus, Role, Webhook, WebhookDelivery
from app.services.webhooks import sign
from conftest import as_user


def test_registering_webhook_requires_organizer(client, make, db):
    event = make.event("hook-auth", open_=True)
    user = make.user("participant_h@x.org")
    c = as_user(client, make, user)

    # Participant cannot register webhook -> 403
    r = c.post(f"/events/{event.slug}/manage/webhooks", data={"url": "https://example.com/hook"})
    assert r.status_code == 403

    # Organizer can register webhook -> 303
    org = make.user("organizer_h@x.org")
    make.role(org, event, Role.ORGANIZER)
    c_org = as_user(client, make, org)
    r_org = c_org.post(
        f"/events/{event.slug}/manage/webhooks",
        data={"url": "https://example.com/hook", "events": "project.submitted"},
        follow_redirects=False,
    )
    assert r_org.status_code == 303

    hook = db.scalar(select(Webhook).where(Webhook.event_id == event.id))
    assert hook is not None
    assert hook.url == "https://example.com/hook"
    assert "project.submitted" in hook.subscribed_events


def test_project_submission_fires_webhook_with_valid_signature(client, make, db):
    event = make.event("hook-submit", open_=True)
    org = make.user("org_sub@x.org")
    make.role(org, event, Role.ORGANIZER)

    hook = Webhook(
        event_id=event.id,
        url="https://example.com/hook",
        secret="test-secret-key-12345",
        subscribed_events=["project.submitted"],
        is_active=True,
    )
    db.add(hook)
    db.commit()

    participant = make.user("dev@x.org")
    team = make.team(event, participant, name="AlphaDevs")
    c = as_user(client, make, participant)

    track_id = event.tracks[0].id
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        r = c.post(
            f"/api/events/{event.slug}/projects",
            json={"title": "SmartBot", "summary": "An AI bot", "track_id": track_id, "submit": True},
        )
        assert r.status_code == 201

        # Check mock call
        assert mock_urlopen.called
        req = mock_urlopen.call_args[0][0]
        body = req.data
        data = json.loads(body.decode("utf-8"))
        assert data["event"] == "project.submitted"
        assert data["data"]["title"] == "SmartBot"
        assert data["data"]["team"] == "AlphaDevs"

        # Check signature header
        expected_sig = sign("test-secret-key-12345", body)
        assert req.headers.get("X-broadsheet-signature") == expected_sig

        # Delivery log written
        delivery = db.scalar(select(WebhookDelivery).where(WebhookDelivery.webhook_id == hook.id))
        assert delivery is not None
        assert delivery.succeeded is True
        assert delivery.status_code == 200


def test_failing_webhook_does_not_break_user_action(client, make, db):
    event = make.event("hook-fail", open_=True)
    hook = Webhook(
        event_id=event.id,
        url="https://unreachable.example.com/hook",
        secret="secret",
        subscribed_events=["project.submitted"],
        is_active=True,
    )
    db.add(hook)
    db.commit()

    participant = make.user("dev_fail@x.org")
    make.team(event, participant, name="FailSafe")
    c = as_user(client, make, participant)

    with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
        r = c.post(
            f"/api/events/{event.slug}/projects",
            json={"title": "ResilientProject", "summary": "Still works", "track_id": event.tracks[0].id, "submit": True},
        )
        # Main action succeeds despite webhook failure
        assert r.status_code == 201

        delivery = db.scalar(select(WebhookDelivery).where(WebhookDelivery.webhook_id == hook.id))
        assert delivery is not None
        assert delivery.succeeded is False
        assert "Connection refused" in delivery.error


def test_score_submitted_webhook_does_not_leak_score_values_or_comment(client, make, db):
    event = make.event("hook-judge", open_=True)
    hook = Webhook(
        event_id=event.id,
        url="https://example.com/score-hook",
        secret="secret",
        subscribed_events=["score.submitted"],
        is_active=True,
    )
    db.add(hook)
    db.commit()

    judge = make.user("judge_hook@x.org")
    make.role(judge, event, Role.JUDGE)

    owner = make.user("proj_owner@x.org")
    team = make.team(event, owner, name="TeamA")
    project = Project(
        event_id=event.id,
        team_id=team.id,
        title="JudgeSecret",
        status=ProjectStatus.SUBMITTED,
    )
    db.add(project)
    db.flush()

    assignment = Assignment(event_id=event.id, judge_id=judge.id, project_id=project.id, status=AssignmentStatus.PENDING)
    db.add(assignment)
    db.commit()

    c = as_user(client, make, judge)

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        crit = event.criteria[0]
        r = c.post(
            f"/judge/assignments/{assignment.id}",
            data={f"c{crit.id}": "5", "comment": "Super top secret private judge thoughts", "then": "stay"},
            follow_redirects=False,
        )
        assert r.status_code == 303

        assert mock_urlopen.called
        req = mock_urlopen.call_args[0][0]
        data = json.loads(req.data.decode("utf-8"))
        assert data["event"] == "score.submitted"
        assert "Super top secret" not in str(data)
        assert "5" not in str(data["data"].values())
        assert data["data"]["project_id"] == str(project.id)
        assert data["data"]["judge_id"] == judge.handle


def test_deleting_webhook_stops_deliveries(client, make, db):
    event = make.event("hook-del", open_=True)
    org = make.user("org_del@x.org")
    make.role(org, event, Role.ORGANIZER)

    hook = Webhook(
        event_id=event.id,
        url="https://example.com/delete-me",
        secret="secret",
        subscribed_events=["project.submitted"],
        is_active=True,
    )
    db.add(hook)
    db.commit()

    c_org = as_user(client, make, org)
    r = c_org.post(f"/events/{event.slug}/manage/webhooks/{hook.id}/delete", follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    assert db.scalar(select(Webhook).where(Webhook.id == hook.id)) is None

    # Submission now should not call urlopen
    participant = make.user("dev_del@x.org")
    make.team(event, participant, name="NoHookTeam")
    c_part = as_user(client, make, participant)

    with patch("urllib.request.urlopen") as mock_urlopen:
        r_sub = c_part.post(
            f"/api/events/{event.slug}/projects",
            json={"title": "NoHook", "summary": "s", "track_id": event.tracks[0].id, "submit": True},
        )
        assert r_sub.status_code == 201
        assert not mock_urlopen.called
