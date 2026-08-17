# Releasing

`runway-mcp` ships in two layers that must stay in lockstep:

1. **PyPI package** — the server code (`uvx runway-mcp` runs it).
2. **Claude Code plugin** — pins an exact package version and is what users install/update.

The plugin's `.mcp.json` pins `runway-mcp==X.Y.Z`, so updating the plugin pulls the
exact matching package version (no stale `uvx` cache). For that to work, **every release
bumps the same version in all four places below.**

## Release checklist

1. Bump the version to `X.Y.Z` in all of:
   - `pyproject.toml` → `version`
   - `manifest.json` → `version` (Desktop Extension)
   - `plugins/runway-mcp/.claude-plugin/plugin.json` → `version` (plugin update signal)
   - `plugins/runway-mcp/.mcp.json` → `--from runway-mcp==X.Y.Z` (package pin)
2. Verify they all match:
   ```bash
   grep -h '"version"\|^version' pyproject.toml manifest.json \
     plugins/runway-mcp/.claude-plugin/plugin.json
   grep 'runway-mcp==' plugins/runway-mcp/.mcp.json
   ```
3. Sanity-check locally (CI runs all of this again before it uploads):
   ```bash
   uv run --extra dev pytest -q
   claude plugin validate .
   ```
4. Merge to `master`, then push the tag — **this is what publishes**:
   ```bash
   git tag vX.Y.Z && git push origin vX.Y.Z
   ```
   `.github/workflows/release.yml` re-checks that the tag agrees with all four version
   sites, lints, tests on Python 3.11/3.12/3.13, builds, runs `twine check`, and only
   then uploads to PyPI via Trusted Publishing. There is no token to set — do **not**
   `uv publish` by hand.
5. Merging the bumped `plugin.json` to `master` is the signal Claude Code uses to
   offer/apply the plugin update — users get the new package version automatically via
   the pin, without touching PyPI themselves. Publish the package *before* users can pull
   the pin, or the plugin will point at a version PyPI does not have yet.

## Versioning

- A published version on PyPI is immutable — never reuse a number; always bump.
- Bump patch (`Z`) for fixes, minor (`Y`) for features, major (`X`) for breaking changes.
