"""Real cookies and PostgreSQL: account isolation, CSRF, expiry and immutable history."""

from contextlib import contextmanager
from datetime import timedelta

import pytest
from auth_helpers import authenticate
from sqlalchemy import select
from test_groups_db import client as client
from test_groups_db import create_group
from test_groups_db import pytestmark as pytestmark

from app.core.auth import COOKIE, now, token_hash
from app.db.models import Account, AuthSession, Group, User
from app.db.session import get_session
from app.main import app


@contextmanager
def db_session():
    generator = app.dependency_overrides[get_session]()
    session = next(generator)
    try:
        yield session
    finally:
        generator.close()


def test_registration_cookie_password_hash_normalization_and_logout_revocation(client):
    token = client.cookies.get(COOKIE)
    headers = dict(client.headers)
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200 and "no-store" in me.headers["cache-control"]
    with db_session() as db:
        account = db.scalar(select(Account).where(Account.email == "fixture@example.test"))
        assert account.password_hash.startswith("$argon2id$")
        assert "fixture-only-password-2026" not in account.password_hash
        assert db.get(AuthSession, token_hash(token)) is not None
        assert db.get(AuthSession, token) is None
    assert client.post("/api/v1/auth/logout").status_code == 204
    client.cookies.set(COOKIE, token)
    client.headers.update(headers)
    assert client.get("/api/v1/auth/me").status_code == 401
    client.cookies.clear()
    login = client.post(
        "/api/v1/auth/login",
        json={"email": " FIXTURE@EXAMPLE.TEST ", "password": "fixture-only-password-2026"},
    )
    assert login.status_code == 200
    assert client.cookies.get(COOKIE) != token
    assert "httponly" in login.headers["set-cookie"].lower()
    assert "samesite=lax" in login.headers["set-cookie"].lower()
    assert "password" not in login.text


@pytest.mark.parametrize("kind", ["missing_csrf", "wrong_csrf", "cross_origin", "cross_site"])
def test_mutations_require_session_csrf_and_trusted_origin(client, kind):
    headers = {}
    if kind == "missing_csrf":
        client.headers.pop("X-CSRF-Token")
    elif kind == "wrong_csrf":
        headers["X-CSRF-Token"] = "wrong"
    elif kind == "cross_origin":
        headers["Origin"] = "https://attacker.example"
    else:
        headers["Sec-Fetch-Site"] = "cross-site"
    assert (
        client.post("/api/v1/groups", json={"name": "Denied"}, headers=headers).status_code == 403
    )
    assert client.get("/api/v1/groups").json() == []


def test_session_expiry_denies_reads_and_writes(client):
    with db_session() as db:
        stored = db.get(AuthSession, token_hash(client.cookies.get(COOKIE)))
        stored.expires_at = now() - timedelta(seconds=1)
        db.commit()
    assert client.get("/api/v1/trips").status_code == 401
    assert client.post("/api/v1/groups", json={"name": "Denied"}).status_code == 401


def test_wrong_password_duplicate_registration_and_password_redaction(client):
    invalid = client.post(
        "/api/v1/auth/register", json={"email": "invalid", "name": "x", "password": "shortsecret"}
    )
    assert invalid.status_code == 422 and "shortsecret" not in invalid.text
    payload = {"email": "fixture@example.test", "password": "incorrect-password-2026"}
    assert client.post("/api/v1/auth/login", json=payload).status_code == 401
    assert (
        client.post("/api/v1/auth/register", json={**payload, "name": "Duplicate"}).status_code
        == 409
    )
    client.headers.pop("X-Auth-Request")
    assert client.post("/api/v1/auth/login", json=payload).status_code == 403


def test_login_rate_limit_counts_failed_attempts(client):
    payload = {"email": "absent@example.test", "password": "incorrect-password-2026"}
    for _ in range(15):
        assert client.post("/api/v1/auth/login", json=payload).status_code == 401
    response = client.post("/api/v1/auth/login", json=payload)
    assert response.status_code == 429 and "Retry-After" in response.headers


def test_owner_isolation_for_all_private_route_and_provider_paths(client):
    gid, users = create_group(client, 1)
    trip = client.post(f"/api/v1/groups/{gid}/trips", json={"budget": 10000}).json()
    route_set = client.post(f"/api/v1/trips/{trip['id']}/route-sets").json()
    rid = route_set["options"][0]["id"]
    profile = client.post(
        "/api/v1/travellers", json={"name": "Private", "interests": {"history": 1}}
    ).json()
    authenticate(client, "other@example.test")
    assert (
        client.post(
            f"/api/v1/groups/{gid}/flight-searches",
            json={"destination": "Lisbon", "origin": "MSQ", "departure": "2026-11-02"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/groups/{gid}/lodging-quotes",
            json={
                "destination": "Lisbon",
                "hotel_key": "g189158-d1",
                "checkin": "2026-11-02",
                "checkout": "2026-11-03",
            },
        ).status_code
        == 404
    )
    for path in [
        f"/users/{users[0]}",
        f"/groups/{gid}",
        f"/groups/{gid}/trips",
        f"/trips/{trip['id']}",
        f"/trips/{trip['id']}/routes",
        f"/trips/{trip['id']}/route-sets",
        f"/routes/{rid}",
        f"/route-sets/{route_set['id']}",
    ]:
        assert client.get("/api/v1" + path).status_code == 404, path
    for collection in ("groups", "trips", "travellers"):
        assert client.get("/api/v1/" + collection).json() == []
    for path in [
        f"/trips/{trip['id']}/route-sets",
        f"/trips/{trip['id']}/routes",
        f"/routes/{rid}/explanation",
    ]:
        assert client.post("/api/v1" + path).status_code == 404
    assert (
        client.post(f"/api/v1/groups/{gid}/members", json={"user_id": users[0]}).status_code == 404
    )
    assert client.post(f"/api/v1/groups/{gid}/trips", json={"budget": 10000}).status_code == 404
    assert (
        client.put(
            f"/api/v1/groups/{gid}/members/{users[0]}/preferences", json={"interests": {}}
        ).status_code
        == 404
    )
    assert client.delete(f"/api/v1/travellers/{profile['id']}").status_code == 404
    assert (
        client.put(
            f"/api/v1/travellers/{profile['id']}", json={"name": "Stolen", "interests": {}}
        ).status_code
        == 404
    )
    own = client.post("/api/v1/groups", json={"name": "Other"}).json()
    assert (
        client.post(f"/api/v1/groups/{own['id']}/members", json={"user_id": users[0]}).status_code
        == 404
    )
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "fixture@example.test", "password": "fixture-only-password-2026"},
    )
    client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    assert client.get(f"/api/v1/routes/{rid}").status_code == 200
    assert client.get("/api/v1/trips").json()[0]["id"] == trip["id"]
    client.cookies.clear()
    assert client.get(f"/api/v1/routes/{rid}").status_code == 401


def test_profile_update_and_deletion_leave_saved_trip_unchanged(client):
    gid, users = create_group(client, 1)
    trip = client.post(f"/api/v1/groups/{gid}/trips", json={"budget": 10000}).json()
    profile = client.post(
        "/api/v1/travellers",
        json={
            "name": "Анна",
            "interests": {"history": 1},
            "age_profile": {"birth_date": "2000-01-01"},
        },
    ).json()
    assert (
        client.put(
            f"/api/v1/users/{users[0]}/saved-profile", json={"profile_id": profile["id"]}
        ).status_code
        == 204
    )
    assert (
        client.get(f"/api/v1/groups/{gid}").json()["members"][0]["saved_traveller_id"]
        == profile["id"]
    )
    assert client.get("/api/v1/travellers?q=Ан").json()[0]["id"] == profile["id"]
    updated = client.put(
        f"/api/v1/travellers/{profile['id']}",
        json={"name": "Анна", "interests": {"nature": 1}, "age_profile": {"age_on_start": 40}},
    )
    assert updated.status_code == 200
    assert client.get(f"/api/v1/trips/{trip['id']}").json() == trip
    assert client.delete(f"/api/v1/travellers/{profile['id']}").status_code == 204
    assert client.get(f"/api/v1/groups/{gid}").json()["members"][0]["saved_traveller_id"] is None
    assert client.get(f"/api/v1/trips/{trip['id']}").json() == trip


def test_legacy_records_stay_private_until_explicit_local_claim(client):
    from app.accounts_legacy import claim_legacy

    with db_session() as db:
        group = Group(name="Legacy")
        user = User(name="Legacy participant")
        db.add_all([group, user])
        db.commit()
        gid = group.id
        preview = claim_legacy(db, "fixture@example.test")
        assert preview["groups"] >= 1 and not preview["applied"]
    assert client.get(f"/api/v1/groups/{gid}").status_code == 404
    with db_session() as db:
        claim_legacy(db, "fixture@example.test", apply=True)
        db.commit()
    assert client.get(f"/api/v1/groups/{gid}").status_code == 200


def test_login_rotates_session_and_csrf_while_secure_cookie_is_configurable(client, monkeypatch):
    from app.core.config import settings

    old_token = client.cookies.get(COOKIE)
    old_csrf = client.headers["X-CSRF-Token"]
    monkeypatch.setattr(settings, "auth_cookie_secure", True)
    signed_in = client.post(
        "/api/v1/auth/login",
        json={"email": "fixture@example.test", "password": "fixture-only-password-2026"},
    )
    assert signed_in.status_code == 200
    assert "; Secure" in signed_in.headers["set-cookie"]
    assert signed_in.json()["csrf_token"] != old_csrf
    with db_session() as db:
        assert db.get(AuthSession, token_hash(old_token)) is None
    # Secure cookies are deliberately not sent by a client over HTTP.
    assert client.get("/api/v1/auth/me").status_code == 401
