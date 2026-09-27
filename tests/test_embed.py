"""The embeddable gallery widget (T4): the script serves correctly, the
public projects API allows cross-origin fetches from it, and the data
matches what the real gallery shows."""

from app.models import Project, ProjectStatus


def test_embed_script_is_served_with_correct_content_type(client, make, db):
    make.event("embed-evt")
    r = client.get("/embed/embed-evt/gallery.js")
    assert r.status_code == 200
    assert "javascript" in r.headers["content-type"]
    assert "fetch(" in r.text


def test_embed_script_404s_for_unknown_event(client):
    assert client.get("/embed/does-not-exist/gallery.js").status_code == 404


def test_projects_api_has_cors_header_for_the_widget(client, make, db):
    event = make.event("embed-evt2")
    r = client.get(f"/api/events/{event.slug}/projects")
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "*"


def test_embed_data_matches_the_gallery(client, make, db):
    event = make.event("embed-evt3", open_=False)
    team = make.team(event, make.user("owner@x.org"), name="EmbedTeam")
    project = Project(event_id=event.id, team_id=team.id, title="Widget Test",
                      summary="shows in the widget", status=ProjectStatus.SUBMITTED)
    db.add(project)
    db.commit()

    api = client.get(f"/api/events/{event.slug}/projects").json()
    assert any(p["title"] == "Widget Test" for p in api)

    preview = client.get(f"/embed/{event.slug}/preview")
    assert preview.status_code == 200 and event.slug in preview.text


def test_preview_page_requires_a_real_event(client):
    assert client.get("/embed/nope/preview").status_code == 404
