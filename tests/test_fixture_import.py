"""fixtures.json imports cleanly, twice, and its awkward cases are handled."""

import json
import os

from sqlalchemy import func, select

from app.models import Assignment, Project, Score, Team, User
from app.services.importer import import_event
from app.services.progress import event_progress
from app.services.scoring import event_results

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "fixtures.json")


def _load():
    with open(FIXTURES, encoding="utf-8") as f:
        return json.load(f)


def _count(db, model):
    return db.scalar(select(func.count()).select_from(model))


def test_import_counts_match_the_file(db):
    data = _load()
    report = import_event(db, data)
    db.commit()
    assert _count(db, Project) == len(data["projects"])
    assert _count(db, Team) == len(data["teams"])
    assert _count(db, Score) == len(data["scores"])
    assert report.event.submissions_close_at.isoformat().startswith("2026-03-01T18:00")


def test_import_is_idempotent(db):
    data = _load()
    import_event(db, data)
    db.commit()
    before = [_count(db, m) for m in (User, Project, Score, Assignment)]
    report = import_event(db, data)
    db.commit()
    assert [_count(db, m) for m in (User, Project, Score, Assignment)] == before
    assert report.created == {}


def test_duplicate_submission_is_flagged_and_left_out_of_ranking(db):
    import_event(db, _load())
    db.commit()
    dup = db.scalar(select(Project).where(Project.external_id == "prj_41"))
    assert dup.duplicate_of.external_id == "prj_07"
    res = event_results(db, dup.event)
    assert dup.id not in {r.project_id for r in res.rows}
    assert dup in res.excluded


def test_flat_judge_is_detected(db):
    report = import_event(db, _load())
    db.commit()
    progress = event_progress(db, report.event)
    flat = {j.judge.external_id for j in progress.judges if j.flat}
    assert "jdg_07" in flat


def test_every_ranked_project_has_a_finite_score(db):
    report = import_event(db, _load())
    db.commit()
    res = event_results(db, report.event)
    assert len(res.rows) == 40
    assert all(r.normalized_mean is not None for r in res.rows)
    assert [r.rank for r in res.rows] == list(range(1, 41))


def test_under_reviewed_projects_get_pending_assignments(db):
    report = import_event(db, _load())
    db.commit()
    progress = event_progress(db, report.event)
    for row in progress.projects:
        if row.project.duplicate_of_id is None:
            assert row.assigned >= report.event.reviews_per_project
