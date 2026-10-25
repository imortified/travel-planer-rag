import httpx

from app.evaluation.mvp_http import latency, measured, route_metadata


def test_p95_keeps_failed_requests_and_small_sample_is_not_an_sla():
    rows = [{"elapsed_seconds": i, "ok": i < 20} for i in range(1, 21)]
    report = latency(rows)
    assert report["attempts"] == 20 and report["errors"] == 1
    assert report["p50_seconds"] == 10.5 and report["p95_seconds"] == 19
    assert report["max_seconds"] == 20
    assert latency([])["p95_seconds"] is None


def test_http_failure_and_malformed_json_do_not_become_successes():
    def handle(request):
        return (
            httpx.Response(503, json={"detail": "no source"})
            if request.url.path == "/failure"
            else httpx.Response(200, text="not json")
        )

    with httpx.Client(base_url="http://test", transport=httpx.MockTransport(handle)) as client:
        row, _ = measured(client, "GET", "/failure")
        assert not row["ok"] and row["http_status"] == 503
        row, _ = measured(client, "GET", "/invalid")
        assert not row["ok"] and row["error_kind"] == "JSONDecodeError"
    assert not route_metadata({"options": []})["feasible"]
