# Agent rules

These rules apply to every change in this repository.

## Branching and pull requests

- **Never make changes on `main`.** Do not commit or push to `main`.
- Always create a **new branch** from the latest `main`, do the work there, and open a **pull request** into `main`.
- **Never force-push** to any branch (`git push --force`, `git push --force-with-lease`, or rewriting published history).

## Development practice

- **Always follow test-driven development (TDD):** add or update a failing test that expresses the desired behavior, implement until the test passes, then refactor while keeping tests green.
- **Always run formatting and linting** for this repo’s standard toolchain **before committing**, and fix every issue they report. Do not commit with known format/lint failures.
