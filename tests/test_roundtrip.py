"""Export an event and import it again: the migration path out and back in."""

import json
import os

from sqlalchemy import func, select

from app.models import Event, Project, Score, Team
from app.services.exports import event_json
from app.services.importer import import_event

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "fixtures.json")


def test_export_reimports_to_the_same_counts(db):
    with open(FIXTURES, encoding="utf-8") as f:
        original = json.load(f)
    event = import_event(db, original).event
    db.commit()
    exported = event_json(db, event)
    assert len(exported["projects"]) == len(original["projects"])
    assert len(exported["scores"]) == len(original["scores"])
    assert exported["event"]["submissions_close"] == original["event"]["submissions_close"]

    # Pretend it is another instance: new external ids, same content.
    exported["event"]["id"] = "evt_copy"
    exported["event"]["name"] = "Copy"
    copy = import_event(db, json.loads(json.dumps(exported))).event
    db.commit()

    def count(model, ev):
        return db.scalar(select(func.count()).select_from(model).where(model.event_id == ev.id))

    assert count(Project, copy) == count(Project, event)
    assert count(Team, copy) == count(Team, event)
    scores = db.scalar(select(func.count()).select_from(Score))
    assert scores == 2 * len(original["scores"])
    assert db.scalar(select(func.count()).select_from(Event)) == 2
