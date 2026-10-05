# Repository contribution rules

## Branches and pull requests

- Start every feature, bug fix, refactor, or documentation change on a new branch. Use the `left-try/` prefix with Gitflow-style names: `left-try/feature/<short-name>`, `left-try/bugfix/<short-name>`, `left-try/hotfix/<short-name>`, or `left-try/docs/<short-name>` as appropriate. Never make project changes directly on `main`.
- Keep each branch and pull request focused on one feature or fix. Prefer small pull requests that are easy to review; split independent work into separate branches and pull requests.
- Open a pull request for every feature, fix, refactor, or documentation change. Review the diff and relevant checks before merging. Merge only after the review is clean and required checks pass; keep `main` as the integration branch.
- Do not push directly to `main`. Push the feature branch and create its pull request against `main`.

## Commit messages

- Make small, focused commits that each represent one logical change.
- Use the repository's bracketed subject style: `[scope] concise description`, for example `[fix] record every voice answer` or `[docs] define branch workflow`.
- Keep unrelated changes out of the same commit. Use additional commits when they make review clearer.

## Tests and contracts

- Treat existing tests as behavior contracts. Do not modify or weaken them to make an implementation pass; resolve a genuine conflict explicitly.
- Add focused regression tests for bug fixes and new behavior. Run the relevant tests before opening a pull request and report any known unrelated failures clearly.
