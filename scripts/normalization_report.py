"""Reproduce the normalization proof in JUDGING.md from fixtures.json.

    docker compose run --rm app python scripts/normalization_report.py

Uses the same functions the portal uses (app.services.scoring), with the
fixture event's defaults: equal weights, a 1-5 scale, prior strength k = 3,
and the flagged duplicate (a team's second project) left out of the ranking
but still used to calibrate its judges.
"""

import json
import sys
from collections import defaultdict

from app.services.scoring import Criterion, Sheet, normalize, rank_projects, sheet_score


def main(path: str = "data/fixtures.json", k: float = 3.0) -> None:
    data = json.load(open(path, encoding="utf-8"))
    keys = []
    for s in data["scores"]:
        keys += [c for c in s["criteria"] if c not in keys]
    crit = [Criterion(i, 1.0, 1, 5) for i, _ in enumerate(keys)]
    judges = {j["id"]: j["name"] for j in data["judges"]}
    projects = {p["id"]: p for p in data["projects"]}

    first_by_team, duplicates = {}, set()
    for p in sorted(data["projects"], key=lambda p: p["submitted_at"]):
        if p["team"] in first_by_team:
            duplicates.add(p["id"])
        else:
            first_by_team[p["team"]] = p["id"]

    sheets = [Sheet(s["judge"], s["project"],
                    sheet_score({keys.index(c): v for c, v in s["criteria"].items()}, crit))
              for s in data["scores"]]
    norm = normalize(sheets, k)
    ranked_ids = [pid for pid in projects if pid not in duplicates]
    rows = rank_projects(ranked_ids, [s for s in sheets if s.project_id not in duplicates])

    print(f"sheets: {len(sheets)}  judges: {len(norm.judges)}  ranked projects: {len(rows)}"
          f"  excluded duplicates: {', '.join(sorted(duplicates))}")
    print(f"event mean M = {norm.global_mean:.2f}, event sd S = {norm.global_sd:.2f}, k = {k}\n")

    print("## Judges (sorted by leniency)\n")
    print("| judge | sheets | mean | sd | used mean | used sd | offset vs event |")
    print("|---|---:|---:|---:|---:|---:|---:|")
    for js in sorted(norm.judges.values(), key=lambda j: j.mean):
        print(f"| {js.judge_id} {judges[js.judge_id]} | {js.n} | {js.mean:.1f} | {js.sd:.1f} | "
              f"{js.shrunk_mean:.1f} | {js.shrunk_sd:.1f} | {js.shrunk_mean - norm.global_mean:+.1f} |")

    print("\n## Ranking, raw vs normalized\n")
    print("| rank | raw rank | move | project | reviews | raw | normalized | s.e. |")
    print("|---:|---:|---:|---|---:|---:|---:|---:|")
    for r in rows:
        p = projects[r.project_id]
        se = f"{r.stderr:.1f}" if r.stderr is not None else "–"
        print(f"| {r.rank} | {r.raw_rank} | {r.movement:+d} | {r.project_id} {p['title']} | {r.n} | "
              f"{r.raw_mean:.1f} | {r.normalized_mean:.1f} | {se} |")

    moved = [r for r in rows if r.movement]
    print(f"\n{len(moved)} of {len(rows)} projects changed rank; "
          f"largest move {max(abs(r.movement) for r in rows)} places.")
    top_raw = {r.project_id for r in rows if r.raw_rank <= 5}
    top_norm = {r.project_id for r in rows if r.rank <= 5}
    print(f"top five by raw: {', '.join(sorted(top_raw))}")
    print(f"top five normalized: {', '.join(sorted(top_norm))}")

    by_judge = defaultdict(list)
    for s in sheets:
        by_judge[s.judge_id].append(s)
    flat = [j for j, own in by_judge.items() if len(own) > 1 and len({s.raw for s in own}) == 1]
    print(f"zero-spread judges: {', '.join(flat) or 'none'}")


if __name__ == "__main__":
    main(*sys.argv[1:2])
