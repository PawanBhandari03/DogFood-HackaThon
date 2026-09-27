# Judging in Broadsheet

This document covers how judges are assigned, how a score sheet becomes a number, how scores from different judges are made comparable (normalization), how the result is ranked, and who may see what along the way. Every number in the "Proof on the fixture data" section can be reproduced with:

```
docker compose run --rm app python scripts/normalization_report.py
```

The code lives in `src/app/services/assignment.py` and `src/app/services/scoring.py`. The tests are `tests/test_normalization.py`, `tests/test_judging_flow.py` and `tests/test_fixture_import.py`.

---

## 1. Assignment

**Goal.** Every submitted project gets `reviews_per_project` independent reviews (default 3, set per event). Each review comes from a judge who knows the project's track, never from a judge on the project's own team, and the work is spread as evenly as possible.

**Algorithm** (`plan_assignments`), run by the organizer from *Assignments → Assign automatically*:

1. Take the submitted projects that are not flagged as duplicates. Sort them by how many reviews they already have (fewest first), then by id. The neediest projects pick first, so a shortage of judges is spread across projects rather than piling up on the last few.
2. For each project, the eligible judges are those with the judge role in the event who are not already assigned to it and are **not members of its team** (conflict of interest).
3. Eligible judges who cover the project's track come first; within that group, the judge with the fewest current assignments wins. Ties are broken by a random number drawn from a generator seeded with the organizer-supplied `seed`, so the same seed on the same data produces the same plan. The seed is written to the audit log with the run.
4. Only when a track has run out of eligible judges does the algorithm take judges from other tracks. Those assignments are counted and reported to the organizer ("N went outside the judge's tracks"). Projects that still cannot reach the target are listed as unfilled.
5. Existing assignments are never moved or deleted. The algorithm only adds. Rerunning it after inviting more judges tops projects up.

Organizers can also assign or unassign by hand. A finished review cannot be unassigned, because that would silently delete a judge's work.

**Structural conflict-of-interest rules**, enforced in the backend (`routers/teams.py`, `routers/judge.py`):

- A judge or organizer of an event cannot create or join a team in it.
- A team member cannot accept a judge invitation for the same event.
- An invitation can only be accepted by the email address it was sent to, so a forwarded link is useless.

**Why not something cleverer** (such as a max-flow or ILP balanced assignment)? With 30 judges and 40 projects, greedy least-loaded is within one review of perfectly balanced; the test `test_auto_assignment_respects_tracks_conflicts_and_balance` checks max − min load ≤ 1. It is also something an organizer can predict and explain. Pairwise judging (Gavel-style) is a different model and is out of scope for this version.

---

## 2. From a score sheet to one number

A rubric is a list of criteria. Each criterion has a label, a weight *wᵢ ≥ 0* and an integer scale *[minᵢ, maxᵢ]*, all editable by the organizer. A judge's sheet gives one value *vᵢ* per criterion. The sheet score is

```
            Σ wᵢ · (vᵢ − minᵢ) / (maxᵢ − minᵢ)
  x = 100 · ─────────────────────────────────
                        Σ wᵢ
```

- **Rescaling each criterion to 0..1 before weighting** means criteria on different scales (1–5 next to 0–10) mix correctly, and a weight means exactly "share of the final score". The rubric page shows each weight as a percentage.
- **Weights are applied at read time,** not stored on the sheet. When an organizer changes a weight, every result is recomputed immediately and nobody has to rescore. The change is audited with before and after values.
- **A criterion added after judging started** is missing from older sheets. Those sheets are scored on the criteria they have, with the weights renormalized. The alternative, treating the missing value as zero, would punish projects for being judged early.
- **All weights zero** is refused by the rubric form. If it ever occurs anyway, the criteria count equally.
- A criterion that judges have already scored cannot be deleted, only set to weight 0, so no judge's input disappears.

The fixture uses three criteria (functionality, quality, innovation), which we import with equal weight on a 1–5 scale.

---

## 3. Normalization

### The problem

Judges differ in two ways that have nothing to do with the projects:

- **Leniency:** a generous judge's "average" is a harsh judge's "excellent". In the fixture, the most generous judge averages 80.6/100 and the harshest (with more than one sheet) 50.0: a 30-point gap.
- **Spread:** one judge uses the whole scale, another only ever gives 3s and 4s. The fixture's widest judge has a standard deviation of 18.9, and one judge has 0.

Each project sees only 2–5 of the 30 judges. So a project's raw average depends heavily on *which* judges it drew. That is the unfairness normalization removes.

### The model

We assume each judge *j* reports roughly an affine transformation of a latent project quality *q*:

```
  x = a_j + b_j · q + noise
```

where *a_j* is the judge's leniency and *b_j* their spread. If we knew *a_j* and *b_j*, we could invert this for every sheet. Under the usual assumption that assignment is not systematically biased (see "Limitations"), a judge's sheet mean estimates *a_j* and the standard deviation of their sheets estimates *b_j*, up to a shared constant. The classic estimator is then the per-judge z-score:

```
  z = (x − m_j) / s_j
```

### Why plain z-scores are not enough

Two things in the fixture break plain z-scores, and they happen at every real event:

1. **Judges with one or two sheets.** `jdg_01` Tomas Varga scored one project; `jdg_23` scored one; six more scored two. With one sheet, *m_j* is that sheet and *s_j* is 0: the z-score is 0/0. With two sheets, *m_j* and *s_j* are extremely noisy, and a plain z-score would make a judge's lower-scored project look terrible and their higher one excellent, whatever their actual level.
2. **Judges with no spread.** `jdg_07` Iva Petrova gave 4/4/4 to all three of their projects. *s_j* = 0 and the z-score divides by zero.

### Shrinkage: the method we use

We treat the event as a whole as prior knowledge about any one judge. Before seeing a judge's sheets, the best guess for their mean and spread is the event's: *M* and *S*, computed over every sheet in the event. Each judge's statistics are then pulled toward the event values, with the strength of the pull set by a constant *k*, the number of "virtual average sheets" each judge is assumed to have:

```
  m̂_j = (n_j · m_j  + k · M ) / (n_j + k)

  ŝ_j = sqrt( (n_j · s_j² + k · S²) / (n_j + k) )
```

where *n_j* is the judge's number of finished sheets and *m_j*, *s_j* their own (population) mean and standard deviation. This is the standard empirical-Bayes / pseudo-count form: the posterior mean under a normal prior on the judge's mean, with the prior's weight expressed as *k* observations, applied the same way to the variance.

The normalized score of a sheet is its z-score against the *shrunk* statistics, mapped back onto the event scale so it stays readable as a 0–100 number:

```
  z  = (x − m̂_j) / ŝ_j
  x' = M + z · S
```

A project's score is the mean of its normalized sheets.

**What this does in the edge cases:**

| Situation | Plain z-score | With shrinkage (k = 3) |
|---|---|---|
| 1 sheet (`jdg_01`) | 0/0, undefined | the used mean moves ¼ of the way to the sheet (1 real vs 3 virtual). A 25/100 from this judge is read as harsh-ish rather than as either "average" or "catastrophic". |
| zero spread (`jdg_07`) | division by zero | ŝ_j = S·√(3/6) > 0. All of the judge's sheets get the *same* normalized value, which is correct: they carry no information about how those projects compare to each other. The judge's leniency is still partially corrected. |
| 10+ sheets (`jdg_24`, `jdg_26`) | reliable | nearly unchanged: n/(n+k) ≥ 0.77 of the weight is their own data. |
| every sheet identical (S = 0) | division by zero | nothing to correct; normalized = raw. |

### Choosing k

*k* is an organizer setting per event (*Event → Normalization prior k*), default **3**. The reasoning: 3 is the target number of reviews per project, and a judge with 3 sheets should count as "half known". Sensitivity on the fixture (Spearman rank correlation of the final ranking):

| k | vs raw ranking | vs k = 3 | top five |
|---:|---:|---:|---|
| 0 (plain z-scores) | 0.820 | 0.869 | prj_34, prj_33, prj_11, prj_37, prj_16 |
| 1 | 0.943 | 0.989 | prj_34, prj_11, prj_33, prj_37, prj_25 |
| **3** | **0.970** | **1.000** | **prj_34, prj_11, prj_33, prj_37, prj_25** |
| 5 | 0.978 | 0.998 | prj_34, prj_11, prj_33, prj_37, prj_25 |
| 10 | 0.986 | 0.994 | prj_34, prj_11, prj_37, prj_10, prj_25 |
| 50 | 0.994 | 0.985 | prj_34, prj_11, prj_10, prj_25, prj_37 |

Unshrunk z-scores (k = 0) reshuffle the ranking the most, driven by the one- and two-sheet judges. From k = 1 to k = 5 the result is essentially the same (ρ ≥ 0.989, identical top five). As k grows very large the correction fades away and the ranking returns to raw averages. The default sits in the stable middle.

### Uncertainty

Next to each score the organizer sees the number of finished reviews and a standard error (sample standard deviation of the normalized sheets ÷ √n). Projects below the event's review target are marked ▲. Two projects 0.5 apart with standard errors of 10 are not meaningfully different, and the results page says so rather than implying false precision. Ties are broken by more reviews, then by id.

### Limitations, stated plainly

- **Leniency and project quality are confounded** when a judge happens to get a batch of unusually strong or weak projects: z-scoring will read that judge as generous or harsh. Two things reduce this. Assignment mixes projects across teams and uses least-loaded selection with a random tiebreak, so batches are not hand-picked. And shrinkage limits how far a small batch can move a judge's estimate. A full fix needs a connected judge–project design and a joint model (additive fixed effects fitted by least squares, or pairwise comparison). That is the natural next step and is not implemented.
- **Normalization is linear.** A judge who compresses the top of the scale but not the bottom is only partially corrected.
- The fixture's scores carry no timestamps, so we cannot detect judges drifting harsher over a long session.

---

## 4. Proof on the fixture data

Settings: equal weights, 1–5 scale, k = 3. 126 sheets from 30 judges on 41 projects. `prj_41` is flagged as a duplicate and left out of the ranking (see section 6), but its sheets still calibrate the judges who wrote them, because they are real evidence of how those judges score.

Event mean **M = 64.15**, event standard deviation **S = 16.21**.

### What the judges look like

"Offset" is how far the judge's used mean sits from the event mean: negative means harsh, positive means generous.

| judge | sheets | mean | sd | used mean | used sd | offset |
|---|---:|---:|---:|---:|---:|---:|
| jdg_01 Tomas Varga | 1 | 25.0 | 0.0 | 54.4 | 14.0 | −9.8 |
| jdg_27 Leila Nasser | 2 | 50.0 | 8.3 | 58.5 | 13.6 | −5.7 |
| jdg_14 Emeka Adeyemi | 3 | 50.0 | 13.6 | 57.1 | 15.0 | −7.1 |
| jdg_20 Otto Brandt | 6 | 52.8 | 18.4 | 56.6 | 17.7 | −7.6 |
| jdg_28 Pavel Ivanov | 2 | 54.2 | 4.2 | 60.2 | 12.8 | −4.0 |
| jdg_10 Hiro Tanaka | 3 | 55.6 | 10.4 | 59.9 | 13.6 | −4.3 |
| jdg_19 Mira Kaur | 4 | 58.3 | 14.4 | 60.8 | 15.2 | −3.3 |
| jdg_23 Anya Sokolova | 1 | 58.3 | 0.0 | 62.7 | 14.0 | −1.5 |
| jdg_24 Diego Herrera | 11 | 59.1 | 14.8 | 60.2 | 15.1 | −4.0 |
| jdg_08 Marek Nowak | 3 | 61.1 | 17.1 | 62.6 | 16.7 | −1.5 |
| jdg_06 Lena Kovac | 3 | 61.1 | 10.4 | 62.6 | 13.6 | −1.5 |
| jdg_18 Lars Berg | 3 | 61.1 | 3.9 | 62.6 | 11.8 | −1.5 |
| jdg_09 Sofia Duarte | 5 | 61.7 | 12.5 | 62.6 | 14.0 | −1.6 |
| jdg_25 Thandi Dlamini | 5 | 61.7 | 10.0 | 62.6 | 12.7 | −1.6 |
| jdg_12 Dilan Yilmaz | 2 | 62.5 | 12.5 | 63.5 | 14.8 | −0.7 |
| jdg_05 Kofi Mensah | 2 | 62.5 | 4.2 | 63.5 | 12.8 | −0.7 |
| jdg_17 Bruno Costa | 2 | 62.5 | 4.2 | 63.5 | 12.8 | −0.7 |
| jdg_29 Ines Rocha | 9 | 63.0 | 9.7 | 63.3 | 11.7 | −0.9 |
| jdg_04 Noor Haddad | 4 | 64.6 | 16.0 | 64.4 | 16.1 | +0.2 |
| jdg_11 Amara Silva | 6 | 65.3 | 17.6 | 64.9 | 17.2 | +0.7 |
| jdg_16 Nadia Rahman | 6 | 65.3 | 17.6 | 64.9 | 17.2 | +0.7 |
| jdg_21 Sana Aziz | 4 | 66.7 | 17.7 | 65.6 | 17.1 | +1.4 |
| jdg_26 Jonas Vogel | 10 | 67.5 | 11.5 | 66.7 | 12.7 | +2.6 |
| jdg_22 Felix Roth | 5 | 68.3 | 17.0 | 66.8 | 16.7 | +2.6 |
| jdg_03 Priya Nair | 2 | 70.8 | 12.5 | 66.8 | 14.8 | +2.7 |
| jdg_13 Rosa Moreau | 3 | 75.0 | 13.6 | 69.6 | 15.0 | +5.4 |
| jdg_07 Iva Petrova | 3 | 75.0 | 0.0 | 69.6 | 11.5 | +5.4 |
| jdg_15 Yuki Sato | 6 | 76.4 | 18.9 | 72.3 | 18.0 | +8.2 |
| jdg_30 Rafa Okonkwo | 4 | 77.1 | 12.3 | 71.5 | 14.1 | +7.4 |
| jdg_02 Wei Lindqvist | 6 | 80.6 | 18.4 | 75.1 | 17.7 | +10.9 |

Worth noticing:

- `jdg_01` gave the lowest single score in the event (25/100). With one sheet we cannot tell a harsh judge from a weak project, so shrinkage treats it as a bit of both (−9.8 offset, not −39).
- `jdg_07` is the "rates everything the same" judge. The spread they get to use (11.5) comes almost entirely from the prior.
- The three most generous judges (`jdg_02`, `jdg_15`, `jdg_30`) all have four or more sheets, so their correction is mostly driven by their own data.

### What happens to the ranking

"Move" = raw rank − normalized rank (positive = moved up). Raw ties are broken by id.

| rank | raw rank | move | project | reviews | raw | normalized | s.e. |
|---:|---:|---:|---|---:|---:|---:|---:|
| 1 | 2 | +1 | prj_34 Iron Switch | 3 | 83.3 | 83.5 | 2.8 |
| 2 | 1 | −1 | prj_11 Salt Ledger | 4 | 83.3 | 79.6 | 0.8 |
| 3 | 7 | +4 | prj_33 Slow Trail | 3 | 75.0 | 77.7 | 1.6 |
| 4 | 5 | +1 | prj_37 Salt Loom | 4 | 77.1 | 77.0 | 11.9 |
| 5 | 4 | −1 | prj_25 Dry Relay | 3 | 77.8 | 75.6 | 6.9 |
| 6 | 3 | −3 | prj_10 Still Beacon | 2 | 79.2 | 75.2 | 6.3 |
| 7 | 6 | −1 | prj_16 Salt Kiln | 3 | 75.0 | 73.5 | 5.9 |
| 8 | 8 | 0 | prj_21 Copper Kiln | 3 | 72.2 | 68.8 | 10.6 |
| 9 | 10 | +1 | prj_04 Green Switch | 3 | 69.4 | 67.9 | 7.1 |
| 10 | 9 | −1 | prj_08 North Drift | 5 | 70.0 | 67.3 | 8.8 |
| 11 | 11 | 0 | prj_38 Deep Beacon | 3 | 69.4 | 67.0 | 11.3 |
| 12 | 12 | 0 | prj_15 Copper Orbit | 2 | 66.7 | 66.2 | 3.8 |
| 13 | 14 | +1 | prj_36 Salt Drift | 3 | 66.7 | 65.8 | 4.7 |
| 14 | 18 | +4 | prj_09 Hollow Signal | 3 | 63.9 | 65.5 | 11.0 |
| 15 | 17 | +2 | prj_31 Salt Ferry | 3 | 63.9 | 65.4 | 4.7 |
| 16 | 13 | −3 | prj_19 Small Relay | 2 | 66.7 | 64.6 | 7.3 |
| 17 | 16 | −1 | prj_17 Small Loom | 3 | 63.9 | 64.4 | 4.2 |
| 18 | 20 | +2 | prj_24 Glass Beacon | 2 | 62.5 | 64.3 | 6.1 |
| 19 | 19 | 0 | prj_18 Open Kiln | 2 | 62.5 | 63.8 | 19.4 |
| 20 | 24 | +4 | prj_27 Flat Thread | 3 | 61.1 | 63.5 | 13.6 |
| 21 | 23 | +2 | prj_12 Open Beacon | 3 | 61.1 | 63.3 | 12.7 |
| 22 | 15 | −7 | prj_02 Small Meadow | 3 | 63.9 | 63.1 | 7.4 |
| 23 | 27 | +4 | prj_01 Glass Signal | 3 | 61.1 | 62.6 | 11.1 |
| 24 | 22 | −2 | prj_35 Warm Beacon | 5 | 61.7 | 61.9 | 5.2 |
| 25 | 28 | +3 | prj_14 Green Lantern | 5 | 60.0 | 61.7 | 5.0 |
| 26 | 21 | −5 | prj_39 Paper Anchor | 2 | 62.5 | 61.6 | 8.1 |
| 27 | 32 | +5 | prj_29 Flat Relay | 2 | 58.3 | 61.4 | 17.1 |
| 28 | 26 | −2 | prj_32 Loud Ledger | 3 | 61.1 | 60.7 | 10.8 |
| 29 | 30 | +1 | prj_07 Dry Harbour | 5 | 58.3 | 60.6 | 12.5 |
| 30 | 29 | −1 | prj_03 Deep Compass | 3 | 58.3 | 58.6 | 8.8 |
| 31 | 31 | 0 | prj_13 Quiet Anchor | 3 | 58.3 | 57.5 | 4.8 |
| 32 | 33 | +1 | prj_26 Amber Hours | 3 | 55.6 | 56.5 | 7.6 |
| 33 | 37 | +4 | prj_30 Paper Harbour | 3 | 52.8 | 55.6 | 9.5 |
| 34 | 25 | −9 | prj_28 Flat Meadow | 3 | 61.1 | 54.5 | 2.8 |
| 35 | 34 | −1 | prj_20 Paper Thread | 3 | 55.6 | 54.2 | 9.9 |
| 36 | 36 | 0 | prj_22 Dry Bridge | 3 | 52.8 | 53.8 | 2.2 |
| 37 | 35 | −2 | prj_06 Dry Compass | 3 | 52.8 | 51.8 | 6.5 |
| 38 | 38 | 0 | prj_40 Slow Loom | 2 | 50.0 | 48.9 | 4.6 |
| 39 | 39 | 0 | prj_23 Slow Quarry | 3 | 47.2 | 46.7 | 6.9 |
| 40 | 40 | 0 | prj_05 North Compass | 3 | 47.2 | 43.8 | 2.1 |

31 of 40 projects change rank. The top five changes by one project. Three examples, traced by hand:

- **prj_28 Flat Meadow falls 9 places** (25th → 34th). Two of its three judges are the event's most and third-most generous (`jdg_02` +10.9, `jdg_30` +7.4). Its raw 61.1 is a middling score *from generous judges*; relative to what those judges gave everyone else, it is weak. Its small standard error (2.8) shows the judges agree, so this is a confident drop.
- **prj_33 Slow Trail rises 4 places into the top three.** All three of its judges gave it 75/100: `jdg_24` (harsh, own mean 59.1), `jdg_09` (own mean 61.7) and `jdg_26` (own mean 67.5). For the first two, that is 13–16 points above what they usually give, and both have five or more sheets, so the portal trusts their scale. A 75 from a harsh judge counts for more than a 75 from a generous one.
- **prj_10 Still Beacon leaves the top five** (3rd → 6th). It has only two reviews, one from `jdg_15` (+8.2, generous). The ▲ marker and the 6.3 standard error on the results page tell the organizer this is a thin result.

This is also the answer to "does normalization matter?": on this data the event's podium (top three) contains a different project depending on whether you correct for judges, and 9 projects move four or more places.

---

## 5. Isolation: who sees which scores

These rules are enforced by the backend, in `src/app/auth.py` and the routers. The UI simply has nothing to show otherwise.

| | own scores | another judge's scores | all scores and results | publish |
|---|---|---|---|---|
| visitor | – | – | published ranking only | – |
| participant | – | 403 | published ranking only | – |
| judge | ✔ | **403, and audited** | published ranking only | – |
| organizer of the event | – | ✔ (their events only) | ✔ | ✔ |
| organizer of a different event | – | empty (not their event) | 403 | 403 |
| admin | – | ✔ | ✔ | ✔ |

How it is enforced:

- **Query scoping, not filtering after the fact.** Every judge query has `WHERE assignment.judge_id = <caller>` built in (`_my_assignments`, `_assignments`). There is no code path that loads another judge's sheet and then hides it.
- **An assignment id belonging to someone else** (`/judge/assignments/<id>`) returns 403, both for reading and for writing, and the attempt is written to the audit log (`denied.not_your_assignment`).
- **`/api/judges/<id>/scores`** answers only the judge themselves, an admin, or an organizer (scoped to the events they organize). A judge asking about a peer gets 403 and an audit row (`denied.peer_scores`). A judge asking about a judge who does not exist also gets 403, so the endpoint cannot be used to enumerate judges.
- **Judges never see aggregates** until results are published: no averages, no other sheets on the project, no ranking.
- **Scores lock** when results are published or when the optional judging deadline passes.

`tests/test_isolation.py` exercises every one of these as plain HTTP requests, which is exactly how the acceptance checker and an attacker would try.

---

## 6. The fixture's awkward cases

| Case | What Broadsheet does |
|---|---|
| **Duplicate submission**: `prj_41` has the same team (`tm_07`), title and repo as `prj_07`, filed 3 minutes before the deadline | Imported and flagged (`duplicate_of = prj_07`). Kept out of the public gallery and the ranking, shown in a red "needs a decision" panel on the organizer overview with two actions: withdraw the later copy, or keep it and withdraw the original. The database enforces one live project per team through a partial unique index, so a new duplicate cannot be created at all. |
| **Judge who rates everything the same**: `jdg_07` Iva Petrova, 4/4/4 on all three projects | Detected on the progress view ("same score everywhere") and in judge calibration. Normalization handles it without dividing by zero (section 3). |
| **Unfinished batches**: 8 projects have only 2 reviews, others up to 5 | On import, under-reviewed projects are topped up to the event target with *pending* assignments. The live progress view lists them first. The results page marks them ▲ with their standard error. |
| **Judges with one sheet**: `jdg_01`, `jdg_23` | Shrinkage (section 3): the portal neither ignores them nor lets one sheet swing a ranking. |
| **Team names are not unique**: three teams are called "StillTrail" | Team names are not a key anywhere. Teams are identified by id. |

---

## 7. Audit trail

Every state change writes an append-only row to `audit_log` with who, when, what, a before/after detail and the client IP. That covers events, rubric weights, invitations, assignment runs (with seed), scores filed and changed, duplicate decisions, results published or unpublished, and logins. Refused access attempts that matter for integrity are logged too (`denied.*`). Organizers read it on *Organizer desk → Audit log*, filterable by action. Nothing in the application updates or deletes audit rows.
