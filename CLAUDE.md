# Notes for working on this repository

* Every change that reaches `main` gets a release with a real version number.
  Follow the release checklist in `docs/hacs-default-checklist.md`: bump
  `version` in `custom_components/anova_ble/manifest.json` and `CARD_VERSION`
  in `custom_components/anova_ble/frontend/anova-cooker-card.js` (a test checks
  they match), move the changelog entries under the new version, push, then
  push the tag `vX.Y.Z`. The Release workflow creates the GitHub release from
  the changelog.
* Design contract: `docs/architecture.md`. BLE protocol: `docs/protocol.md`.
* The protocol package `custom_components/anova_ble/protocol/` must not import
  Home Assistant.
* Checks: `ruff check . && ruff format --check .`, `pytest tests -q`, and the
  card browser tests `node tests/frontend/render.mjs` (needs Playwright).
