"""Local backend launcher: JSON logs from process startup; one worker, default port 8001."""

import argparse

import uvicorn

from app.core.logging import configure_logging


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    # With reload, only the serving child writes the rotating file (Windows locks).
    configure_logging(file_enabled=not args.reload)
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=args.port,
        reload=args.reload,
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    main()
