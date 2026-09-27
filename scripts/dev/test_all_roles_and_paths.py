"""Comprehensive verification script for all logins, roles, and paths."""

import sys
import httpx

BASE_URL = "http://localhost:8080"
PASSWORD = "dogfood-demo"

ACCOUNTS = {
    "admin": ("admin@dogfood.local", PASSWORD),
    "organizer": ("organizer@dogfood.local", PASSWORD),
    "judge_a": ("diego.herrera@example.org", PASSWORD),
    "judge_b": ("jonas.vogel@example.org", PASSWORD),
    "participant": ("priya1@example.org", PASSWORD),
}

def login_client(email, password):
    client = httpx.Client(base_url=BASE_URL, follow_redirects=True, timeout=10.0)
    resp = client.post("/login", data={"email": email, "password": password, "next": "/me"})
    assert resp.status_code == 200, f"Login failed for {email}: {resp.status_code}"
    assert "Logged in" in resp.text or "/me" in str(resp.url), f"Redirect failed for {email}"
    return client

def test_all():
    results = []
    errors = []

    def check(role, path, expected_status=200, client=None, method="GET", data=None):
        c = client or httpx.Client(base_url=BASE_URL, follow_redirects=False, timeout=10.0)
        try:
            if method == "GET":
                resp = c.get(path)
            elif method == "POST":
                resp = c.post(path, data=data)
            
            ok = resp.status_code == expected_status
            results.append((role, method, path, resp.status_code, expected_status, ok))
            if not ok:
                errors.append(f"[{role}] {method} {path} -> got {resp.status_code}, expected {expected_status}")
            return resp
        except Exception as e:
            results.append((role, method, path, "ERROR", expected_status, False))
            errors.append(f"[{role}] {method} {path} -> Exception: {e}")
            return None

    print("\n--- Testing Anonymous / Visitor Paths ---")
    visitor = httpx.Client(base_url=BASE_URL, follow_redirects=False, timeout=10.0)
    check("visitor", "/", 200, visitor)
    check("visitor", "/projects", 200, visitor)
    check("visitor", "/events/sample-hack-2026", 200, visitor)
    check("visitor", "/events/practice-jam", 200, visitor)
    check("visitor", "/events/sample-hack-2026/certificates/tm_01", 200, visitor)
    check("visitor", "/embed/sample-hack-2026/preview", 200, visitor)
    check("visitor", "/embed/sample-hack-2026/gallery.js", 200, visitor)
    check("visitor", "/api/docs", 200, visitor)
    check("visitor", "/me", 303, visitor)  # Should redirect to login

    print("\n--- Testing Participant (Priya) ---")
    p_client = login_client(*ACCOUNTS["participant"])
    check("participant", "/me", 200, p_client)
    check("participant", "/events/sample-hack-2026/team", 200, p_client)
    check("participant", "/events/practice-jam/team", 200, p_client)
    check("participant", "/events/sample-hack-2026/vote", 200, p_client)
    check("participant", "/events/practice-jam/vote", 200, p_client)
    check("participant", "/api/judge/scores", 403, p_client)  # Participant cannot view judge scores

    print("\n--- Testing Judge A (Diego) ---")
    ja_client = login_client(*ACCOUNTS["judge_a"])
    check("judge_a", "/me", 200, ja_client)
    check("judge_a", "/judge", 200, ja_client)
    check("judge_a", "/judge/record", 200, ja_client)
    check("judge_a", "/api/judge/scores", 200, ja_client)
    check("judge_a", "/api/judges/jdg_26/scores", 403, ja_client)  # Peer isolation check

    print("\n--- Testing Judge B (Jonas) ---")
    jb_client = login_client(*ACCOUNTS["judge_b"])
    check("judge_b", "/me", 200, jb_client)
    check("judge_b", "/judge", 200, jb_client)
    check("judge_b", "/api/judge/scores", 200, jb_client)
    check("judge_b", "/api/judges/jdg_24/scores", 403, jb_client)  # Peer isolation check

    print("\n--- Testing Organizer ---")
    org_client = login_client(*ACCOUNTS["organizer"])
    check("organizer", "/me", 200, org_client)
    check("organizer", "/events/sample-hack-2026/manage", 200, org_client)
    check("organizer", "/events/sample-hack-2026/manage/progress", 200, org_client)
    check("organizer", "/events/sample-hack-2026/manage/settings", 200, org_client)
    check("organizer", "/events/sample-hack-2026/manage/rubric", 200, org_client)
    check("organizer", "/events/sample-hack-2026/manage/judges", 200, org_client)
    check("organizer", "/events/sample-hack-2026/manage/assignments", 200, org_client)
    check("organizer", "/events/sample-hack-2026/manage/results", 200, org_client)
    check("organizer", "/events/sample-hack-2026/manage/audit", 200, org_client)
    check("organizer", "/events/sample-hack-2026/manage/voting", 200, org_client)
    check("organizer", "/events/sample-hack-2026/manage/webhooks", 200, org_client)
    check("organizer", "/api/events/sample-hack-2026/export/results.csv", 200, org_client)
    check("organizer", "/api/events/sample-hack-2026/export.json", 200, org_client)

    print("\n--- Testing Admin ---")
    admin_client = login_client(*ACCOUNTS["admin"])
    check("admin", "/me", 200, admin_client)
    check("admin", "/admin", 200, admin_client)
    check("admin", "/events/new", 200, admin_client)

    print("\n" + "=" * 60)
    print(f"TOTAL CHECKS: {len(results)} | PASSED: {len([r for r in results if r[5]])} | FAILED: {len(errors)}")
    print("=" * 60)
    if errors:
        print("\nFAILURES:")
        for err in errors:
            print("  [FAIL]", err)
        return False
    else:
        print("\n[SUCCESS] ALL ROLES, LOGINS, AND PATHS VERIFIED WITH ZERO ERRORS!")
        return True

if __name__ == "__main__":
    success = test_all()
    sys.exit(0 if success else 1)
