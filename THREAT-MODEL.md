# Broadsheet Threat Model

This document outlines the security architecture and threat model for the Broadsheet platform, focusing on submission integrity, judge isolation, community voting abuse, and automated attacks.

---

## Trust Boundaries & Assets

- **High-value assets:**
  - Submission integrity (deadlines, authentic timestamps, code/demo links).
  - Judge isolation (independent, unbiased score sheets; confidential rubric weights).
  - Community choice voting integrity (approval voting tallies and anti-fraud).
  - Audit trail (tamper-evident log of all administrative and scoring actions).

- **Actors:**
  - **Anonymous Visitors:** Can browse public gallery and published results.
  - **Participants:** Can form teams and draft/submit projects before deadline.
  - **Judges:** Can score only assigned projects; strictly blocked from peer score discovery.
  - **Organizers:** Can configure rubrics, manage assignments, monitor live progress, inspect abuse signals, and publish results.
  - **Admins:** Can provision events and configure global operator access.

---

## Threat Analysis

### 1. Sybil Accounts & Mass Voter Registration

| Dimension | Description |
|---|---|
| **Threat** | An adversary registers dozens or hundreds of throwaway accounts to mass-vote for a target project. |
| **What Broadsheet Does** | 1. **Authentication Requirement:** Community voting is restricted to authenticated accounts (`voting_mode == "authenticated"`).<br>2. **Account Age Signal:** Organizer dashboard specifically flags all votes cast by accounts created *after* the voting window opened (`User.created_at > Event.voting_open_at`).<br>3. **Shared IP Aggregation:** Signals IP addresses shared by 3 or more distinct voter accounts.<br>4. **Rapid Voter Flagging:** Identifies accounts casting their vote allowances in under 10 seconds.<br>5. **Organizer Voiding:** Organizers can audit and void suspicious votes with a logged reason. |
| **What Broadsheet Does Not Do** | Because the portal is self-hosted and offline-first, it does not mandate external CAPTCHA, phone SMS verification, or mandatory email verification loops. |

---

### 2. Ballot Stuffing & Duplicate Voting

| Dimension | Description |
|---|---|
| **Threat** | A user votes multiple times for the same project or exceeds the event's `max_votes` allowance. |
| **What Broadsheet Does** | 1. **Database Constraint:** `UNIQUE (user_id, project_id)` on the `votes` table guarantees at most one vote per user per project at the database level.<br>2. **Allowance Enforcement:** Backend counts existing votes and blocks casting beyond `event.max_votes` (default 3, configurable 1–20).<br>3. **Conflict of Interest Restrictions:** Team members are blocked from voting for their own team's project. Judges and organizers of the event are blocked from participating in community voting.<br>4. **Rate Limiting:** In-memory sliding-window rate limiter limits voting actions to 30/minute per user. |
| **What Broadsheet Does Not Do** | Does not restrict voting based on hardware fingerprints or third-party identity providers. |

---

### 3. Vote Buying, Collusion & Ballot Position Bias

| Dimension | Description |
|---|---|
| **Threat** | Projects at the top of the ballot receive an unfair advantage (position bias); voters collude or sell votes based on visible live standings. |
| **What Broadsheet Does** | 1. **Sealed Standings:** Public tallies and community results are strictly sealed (`403`) until `voting_close_at`. Non-organizers cannot observe intermediate counts.<br>2. **Deterministic Ballot Randomization:** Each voter receives an independently shuffled project list generated from `random.Random(f"{event.id}:{user.id}")`. This eliminates global ordering bias while remaining stable across refreshes for the same user. |
| **What Broadsheet Does Not Do** | Cannot stop off-platform financial incentives or private coordination between participants. |

---

### 4. Scraping, Enumeration & Information Leaks

| Dimension | Description |
|---|---|
| **Threat** | Competitors or malicious judges scrape unpublished scores, draft submissions, or peer reviews. |
| **What Broadsheet Does** | 1. **Backend Query Isolation:** Judge endpoints scope all database queries strictly to `WHERE assignment.judge_id = current_user.id`. Peer score endpoints return `403` and write a `denied.peer_scores` audit entry.<br>2. **Draft & Duplicate Concealment:** Draft projects and flagged duplicates are excluded from public gallery queries (`WHERE status = 'submitted' AND duplicate_of_id IS NULL`).<br>3. **Sealed Rubrics & Results:** Unpublished results return `403` to non-organizers. Publishing is blocked while submissions or community voting are active. |
| **What Broadsheet Does Not Do** | Publicly submitted projects (title, summary, repo URL) are open to public browsing in the gallery by design. |

---

### 5. Deadline Gaming & Race Conditions

| Dimension | Description |
|---|---|
| **Threat** | A participant submits or modifies a project after the deadline by manipulating client clocks or latency. |
| **What Broadsheet Does** | 1. **Server-Clock Enforcement:** Every mutating project route calls `ensure_submissions_open(event)` before inspecting input, referencing server UTC timestamps (`utcnow()`).<br>2. **Post-Deadline Freezing:** After `submissions_close_at`, all edit, submit, unsubmit, withdraw, and team-change requests return `403 submissions_closed`.<br>3. **One Live Project Constraint:** Partial unique index `one_live_project_per_team` ensures concurrent submissions cannot insert multiple active projects for a single team. |
| **What Broadsheet Does Not Do** | Does not accept offline timestamp claims generated by client hardware. |

---

## Summary Matrix

| Threat | Prevention Layer | Detection / Audit Mechanism |
|---|---|---|
| Sybil voting | Account requirement, rate limits | Organizer abuse dashboard (`abuse_signals`) |
| Duplicate voting | DB unique constraint, backend checks | `vote.cast` audit entries |
| Position bias | Deterministic per-user RNG ballot | Verified uniform shuffle distribution |
| Bandwagon effect | Sealed tallies until deadline | 403 access control |
| Peer score snooping | Scoped SQL queries, backend guards | `denied.peer_scores` audit log entries |
| Late submissions | Strict server UTC deadline check | `submissions_closed` 403 enforcement |
