import json
from datetime import date

import httpx
import pytest

from app.evaluation import hotel_providers as pilot

HOTEL = {
    "name": "Hotel Da Baixa",
    "latitude": 38.712166,
    "longitude": -9.137548,
    "xotelo_key": "g189158-d13402042",
}
ADULTS = {"id": "adults", "adults": 2, "child_ages": [], "rooms": 1}
FAMILY = {"id": "family", "adults": 2, "child_ages": [8], "rooms": 1}
GROUP = {"id": "group", "adults": 4, "child_ages": [], "rooms": 2}
FIRST, LAST = date(2026, 11, 2), date(2026, 11, 4)


def test_provider_parameters_preserve_children_and_never_convert_two_rooms_to_one():
    assert pilot.parameters("xotelo", HOTEL, FAMILY, FIRST, LAST)["age_of_children"] == "8"
    assert pilot.parameters("serpapi", HOTEL, FAMILY, FIRST, LAST)["children_ages"] == "8"
    assert pilot.parameters("xotelo", HOTEL, GROUP, FIRST, LAST)["rooms"] == 2
    with pytest.raises(ValueError, match="unsupported_room_count"):
        pilot.parameters("serpapi", HOTEL, GROUP, FIRST, LAST)
    infant = {**FAMILY, "child_ages": [0, 1]}
    assert pilot.parameters("xotelo", HOTEL, infant, FIRST, LAST)["age_of_children"] == "0,0"
    assert pilot.parameters("serpapi", HOTEL, infant, FIRST, LAST)["children_ages"] == "1,1"


def test_null_tax_is_unknown_and_a_nightly_quote_does_not_become_a_verified_trip_total():
    case = pilot.parameters("xotelo", HOTEL, FAMILY, FIRST, LAST)
    body = {
        "error": None,
        "result": {
            "chk_in": str(FIRST),
            "chk_out": str(LAST),
            "currency": "EUR",
            "rates": [{"name": "OTA", "rate": 100, "tax": None}],
        },
    }
    result = pilot.xotelo_result(body, case)
    assert result["status"] == "quotes" and result["quotes"][0]["reported_tax_cents"] is None
    assert result["verified_stay_total_cents"] is None
    body["result"]["rates"] = []
    assert pilot.xotelo_result(body, case)["status"] == "empty"
    body["result"]["currency"] = "USD"
    assert pilot.xotelo_result(body, case)["status"] == "query_mismatch"


def test_matching_requires_nearby_coordinates_and_unique_identity():
    correct = {
        "name": "Hotel da Baixa",
        "gps_coordinates": {"latitude": 38.7122, "longitude": -9.1375},
    }
    assert pilot.same_property(HOTEL, correct)
    assert not pilot.same_property(
        HOTEL, {**correct, "gps_coordinates": {"latitude": 41, "longitude": -8}}
    )
    case = pilot.parameters("serpapi", HOTEL, FAMILY, FIRST, LAST)
    body = {"search_parameters": case, "properties": [correct, correct]}
    assert pilot.serp_result(body, HOTEL, case)["status"] == "ambiguous_identity"
    body["properties"] = [
        {
            **correct,
            "total_rate": {"extracted_lowest": 200},
            "rate_per_night": {"extracted_lowest": 100},
        }
    ]
    result = pilot.serp_result(body, HOTEL, case)
    assert result["status"] == "quotes"
    assert result["quotes"][0]["total_after_taxes_cents"] is None
    assert result["verified_stay_total_cents"] is None
    for patch in [{"children_ages": "9"}, {"adults": 3}, {"check_out_date": "2026-11-05"}]:
        changed = {**body, "search_parameters": {**case, **patch}}
        assert pilot.serp_result(changed, HOTEL, case)["status"] == "query_mismatch"


@pytest.mark.parametrize(
    "body,status",
    [
        ({"plan_id": "free", "total_searches_left": 8}, "ready"),
        ({"plan_id": "free", "total_searches_left": 7}, "insufficient_verified_free_quota"),
        ({"plan_id": "paid", "total_searches_left": 1000}, "free_plan_not_confirmed"),
    ],
)
def test_account_gate_requires_a_confirmed_free_plan_and_sufficient_quota(body, status):
    with httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=body))
    ) as client:
        assert pilot.free_account(client, "private-test-key", 8)["status"] == status


def test_credentials_and_exception_urls_are_not_written_to_reports():
    secret = "PRIVATE_TEST_SECRET"
    text = json.dumps(
        pilot.redacted(
            {"api_key": secret, "nested": {"url": "https://example.org/?api_key=" + secret}}, secret
        )
    )
    assert secret not in text

    def fail(request):
        raise httpx.ConnectError("Could not fetch " + str(request.url), request=request)

    with httpx.Client(transport=httpx.MockTransport(fail)) as client:
        meta, body = pilot.request_json(
            client, "https://serpapi.com/search.json", {"api_key": secret}
        )
        assert body is None and secret not in json.dumps(meta)


def test_malformed_provider_payloads_are_not_treated_as_free_or_empty_quotes():
    xcase = pilot.parameters("xotelo", HOTEL, ADULTS, FIRST, LAST)
    assert pilot.xotelo_result({"result": "bad"}, xcase)["status"] == "invalid_response"
    scase = pilot.parameters("serpapi", HOTEL, ADULTS, FIRST, LAST)
    assert (
        pilot.serp_result({"search_parameters": ["bad"]}, HOTEL, scase)["status"]
        == "invalid_response"
    )
    assert not pilot.same_property(HOTEL, {"name": None})


def test_missing_key_skips_serpapi_without_sending_authenticated_requests(tmp_path, monkeypatch):
    requests = []

    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "error": None,
                "result": {
                    "chk_in": request.url.params["chk_in"],
                    "chk_out": request.url.params["chk_out"],
                    "currency": "EUR",
                    "rates": [],
                },
            },
        )

    real_client = httpx.Client
    monkeypatch.setattr(
        pilot.httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    monkeypatch.setattr(pilot.time, "sleep", lambda _seconds: None)
    cases = {
        "version": "fixture",
        "city": "Lisbon",
        "currency": "EUR",
        "hotels": [HOTEL],
        "nights": [2],
        "parties": [ADULTS, GROUP],
    }
    future = pilot.datetime.now(pilot.timezone.utc).date() + pilot.timedelta(days=10)
    report = pilot.run(cases, tmp_path / "report.json", future, secret="")
    assert len(requests) == 2 and all("data.xotelo.com" in url for url in requests)
    assert report["serpapi_searches_sent"] == 0
    assert report["summary"]["serpapi"] == {"missing_api_key": 1, "unsupported_room_count": 1}
    assert report["application_changed"] is False and report["booking_performed"] is False


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), -1, "€100"])
def test_invalid_money_does_not_become_zero(value):
    assert pilot.cents(value) is None
