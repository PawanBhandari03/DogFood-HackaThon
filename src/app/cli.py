"""Operator commands. Run inside the app container:

    docker compose exec app python -m app.cli import  data/fixtures.json
    docker compose exec app python -m app.cli export  sample-hack-2026 > event.json
    docker compose exec app python -m app.cli create-admin you@example.org
    docker compose exec app python -m app.cli normalize sample-hack-2026

`import` reads the fixtures.json shape (the same shape `export` writes), so an
event can move between two Broadsheet instances, or in from another platform
after a small conversion script.
"""

import argparse
import getpass
import json
import sys

from sqlalchemy import select

from app.db import SessionLocal
from app.models import User
from app.security import hash_password
from app.services import audit
from app.services.exports import event_json
from app.services.importer import import_event
from app.services.scoring import event_results
from app.web import event_or_404


def cmd_import(args) -> int:
    with open(args.path, encoding="utf-8") as f:
        data = json.load(f)
    with SessionLocal() as db:
        report = import_event(db, data)
        audit.record(db, "event.imported", event_id=report.event.id, entity_type="event",
                     entity_id=report.event.slug, detail={"file": args.path, "created": report.created})
        db.commit()
        print(f"imported into /events/{report.event.slug}: "
              + (", ".join(f"{v} {k}" for k, v in report.created.items()) or "nothing new"))
        for line in report.duplicates + report.notes:
            print("  " + line)
    return 0


def cmd_export(args) -> int:
    with SessionLocal() as db:
        json.dump(event_json(db, event_or_404(db, args.slug)), sys.stdout, indent=2)
        print()
    return 0


def cmd_create_admin(args) -> int:
    password = args.password or getpass.getpass("password: ")
    if len(password) < 8:
        print("use at least 8 characters", file=sys.stderr)
        return 1
    with SessionLocal() as db:
        email = args.email.lower()
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(email=email, name=args.name or email.split("@")[0])
            db.add(user)
        user.password_hash = hash_password(password)
        user.is_admin = True
        user.can_create_events = True
        db.flush()
        audit.record(db, "admin.created_by_cli", actor=user, entity_type="user", entity_id=user.id)
        db.commit()
        print(f"{email} is an admin")
    return 0


def cmd_normalize(args) -> int:
    with SessionLocal() as db:
        res = event_results(db, event_or_404(db, args.slug))
        n = res.normalization
        print(f"M={n.global_mean:.2f} S={n.global_sd:.2f} k={n.k}")
        for r in res.rows:
            p = res.projects[r.project_id]
            print(f"{r.rank or '-':>3}  {r.normalized_mean or 0:6.2f}  raw {r.raw_mean or 0:6.2f}  "
                  f"n={r.n}  {p.external_id or p.id}  {p.title}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("import", help="import an event from a fixtures.json-shaped file")
    p.add_argument("path")
    p.set_defaults(fn=cmd_import)
    p = sub.add_parser("export", help="print an event as fixtures.json-shaped JSON")
    p.add_argument("slug")
    p.set_defaults(fn=cmd_export)
    p = sub.add_parser("create-admin", help="create or promote an admin account")
    p.add_argument("email")
    p.add_argument("--name")
    p.add_argument("--password", help="prompted for when omitted")
    p.set_defaults(fn=cmd_create_admin)
    p = sub.add_parser("normalize", help="print the normalized ranking of an event")
    p.add_argument("slug")
    p.set_defaults(fn=cmd_normalize)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
