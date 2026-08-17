# Releasing

`runway-mcp` ships in two layers that must stay in lockstep:

1. **PyPI package** — the server code (`uvx runway-mcp` runs it).
2. **Claude Code plugin** — pins an exact package version and is what users install/update.

The plugin's `.mcp.json` pins an exact version, so updating the plugin pulls exactly that
server build (no stale `uvx` cache). For that to work, **every release bumps the same
version in all four places below.**

> **PyPI publishing is currently blocked.** The `pypi` Trusted Publishing publisher was
> never configured, and the account's 2FA is lost, so the `publish` job fails with
> `invalid-publisher` on every tag. Until the account is recovered, the plugin pins the
> **git tag** (`git+https://github.com/satovarb16/runwayMCP@vX.Y.Z`) instead of the PyPI
> spec (`runway-mcp==X.Y.Z`), and the latest release on PyPI stays at `0.1.2`. The
> version gate accepts either pin form, so switching back is a one-line change to
> `plugins/runway-mcp/.mcp.json`.

## Release checklist

1. Bump the version to `X.Y.Z` in all of:
   - `pyproject.toml` → `version`
   - `manifest.json` → `version` (Desktop Extension)
   - `plugins/runway-mcp/.claude-plugin/plugin.json` → `version` (plugin update signal)
   - `plugins/runway-mcp/.mcp.json` → the `--from` pin (currently
     `git+https://github.com/satovarb16/runwayMCP@vX.Y.Z`; `runway-mcp==X.Y.Z` once PyPI
     works again)
2. Verify they all match:
   ```bash
   rg -I '"version"|^version' pyproject.toml manifest.json \
     plugins/runway-mcp/.claude-plugin/plugin.json
   rg -o '(==|@v)[0-9]+\.[0-9]+\.[0-9]+' plugins/runway-mcp/.mcp.json
   ```
   CI re-checks this on every tag and refuses to publish on a mismatch, so this step is
   a convenience, not the safety net.
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
