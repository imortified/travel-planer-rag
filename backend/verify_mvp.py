"""Verify the local MVP and measure full HTTP planning/read latency on real catalog snapshots."""

from app.evaluation.mvp_http import main

if __name__ == "__main__":
    raise SystemExit(main())
