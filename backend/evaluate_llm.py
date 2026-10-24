"""Opt-in evaluation of the local assistant; default reports stay outside Git."""

from app.evaluation.runner import main

if __name__ == "__main__":
    raise SystemExit(main())
