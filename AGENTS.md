# Project workflow

- The user wants each new development stage in a separate descriptive Git branch.
- Commit completed, verified changes at logical checkpoints so the user can review and restore them.
- Keep main as the stable baseline. Do not merge feature branches or force-push without an explicit request.
- When work depends on a previous unmerged feature, branch from it and state that dependency.
- Push completed feature branches to origin so their diffs are available for review on GitHub.
- Keep secrets, virtual environments, dependencies, build artifacts and caches out of Git.
  Track example configuration and dependency lock files.
- Run checks appropriate to the change; integration tests require migrated PostgreSQL and RUN_DB_TESTS=1.
- Frontend-specific Next.js instructions are in frontend/AGENTS.md.
