# HACS default store checklist

For later. Not needed for installing from a custom repository.

## Store requirements

* [ ] The repository is public at https://github.com/gielk/ha-anova.
* [ ] The repository has a description.
* [ ] The repository has topics, for example `home-assistant`, `hacs`, `anova`, `sous-vide` and `bluetooth`.
* [ ] README.md explains installation and setup.
* [ ] hacs.json has `name`, `render_readme` and `homeassistant`.
* [ ] At least one GitHub release exists. Its tag is `vX.Y.Z` and matches `version` in `manifest.json`.
* [ ] A brand icon ships with the integration. Since Home Assistant 2026.x a custom integration can serve its own images from `custom_components/anova_ble/brand/` (see `homeassistant/components/brands/const.py` for the allowed names):
  * `icon.png` at 256x256 px and `icon@2x.png` at 512x512 px
  * optional `logo.png`, `logo@2x.png` and `dark_` variants
  * Use an original icon. Never the Anova logo.
* [ ] The hassfest and HACS jobs in `.github/workflows/validate.yml` are green.
* [ ] A pull request to `hacs/default` adds gielk/ha-anova to the integration list.

## Release checklist

1. Set the new version in `custom_components/anova_ble/manifest.json` and in `CARD_VERSION` in `custom_components/anova_ble/frontend/anova-cooker-card.js`. A test fails when they differ. Browsers cache the card by this version.
2. In `CHANGELOG.md`, move the Unreleased entries under a new version heading with the release date. Update the compare links at the bottom.
3. Check that the Tests and Validate workflows pass.
4. Commit the changes and push them to the default branch.
5. Create the tag `vX.Y.Z`, for example `v0.1.0`, and push it.
6. The Release workflow then creates the GitHub release from the changelog entry. Check that it ran.
