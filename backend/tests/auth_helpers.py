def authenticate(client, email="fixture@example.test", name="Fixture account"):
    client.headers["X-Auth-Request"] = "travel-planner"
    response = client.post(
        "/api/v1/auth/register",
        json={"name": name, "email": email, "password": "fixture-only-password-2026"},
    )
    assert response.status_code == 201, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return response.json()["account"]
