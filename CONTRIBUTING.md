# Contributing

1. Open an issue first for anything bigger than a typo, so we agree on the change.
2. Branch, change, and run the linter locally:
   ```bash
   bash scripts/lint_skill.sh .
   ```
3. New or changed behavior needs a test: add or update a case in `evals/` (and the CI self-test if you change
   `action.yml`).
4. Open a PR. CI must be green. Mauricio Alonso Moya Tapia (@maumoya) reviews and merges.

Keep SKILL.md under 500 lines; put detail in `references/`. Plain words. Commits name only their human authors.
