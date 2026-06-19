# runwayMCP

[![PyPI](https://img.shields.io/pypi/v/runway-mcp.svg)](https://pypi.org/project/runway-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/runway-mcp.svg)](https://pypi.org/project/runway-mcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

An MCP server that helps international students (F-1/OPT) filter US job postings by technical fit AND visa sponsorship history — in a single call.

## Quick install

### Option A: Claude Code plugin (recommended — two commands)

```
/plugin marketplace add satovarb16/runwayMCP
/plugin install runway-mcp@satovarb
```

Claude Code wires up the MCP server for you — no JSON to edit.

**Updating:** new releases arrive through the normal plugin flow — no need to touch PyPI.

```
/plugin marketplace update satovarb
/plugin update runway-mcp@satovarb
```

Then run `/reload-plugins` (or restart Claude Code) to load the new version. The plugin
pins an exact package version, so updating it pulls the matching server release.

### Option B: manual `.mcp.json`

Create a `.mcp.json` file in the directory where you run Claude Code:

```json
{
  "mcpServers": {
    "runway-mcp": {
      "command": "uvx",
      "args": ["runway-mcp"]
    }
  }
}
```

That's it. Open Claude Code — `uvx` downloads and runs the server automatically.

> **Don't have `uv`?** Install it: `pip install uv` (or see [uv docs](https://docs.astral.sh/uv/getting-started/installation/))

### Alternative: install from source

```bash
git clone https://github.com/satovarb16/runwayMCP
cd runwayMCP
pip install -e ".[dev]"
```

Then use `python -m server` instead of `uvx runway-mcp` in your `.mcp.json`, and add `"cwd": "/path/to/runwayMCP"`.

> **Optional extra:** parsing Greenhouse *custom domains* needs Playwright. Most users can skip it — see [Optional: Playwright](#optional-playwright-for-javascript-heavy-job-boards).

## Step 0 (required): ingest your CV

**Do this once before anything else.** `analyze_job` needs a stored profile — without it
it returns an error asking you to run this first.

```
You: "Set up my profile using my CV at /path/to/resume.pdf"
```

**Accepted CV formats:** `.pdf` and `.docx` only.

Claude reads your CV, extracts a structured profile, and saves it locally at
`~/.config/runway-mcp/profile.json`. Updated your CV later? Just say "Update my profile
with my new CV at ..." to replace it.

## Usage

```
You: "Evaluate this role for me: https://jobs.example.com/swe-123"
Claude:
  → analyze_job(url) — fetches job + checks visa + loads your profile
  → scores the CV match and returns APPLY / CONSIDER / SKIP + reasoning
```

On first run, the server downloads USCIS H-1B data (~2MB) automatically.

## Optional: Playwright for JavaScript-heavy job boards

**You almost certainly don't need this.** It's only for parsing **Greenhouse custom
domains** (a rare edge case). Canonical `boards.greenhouse.io`, Ashby, and Lever URLs
always work without it. The server prints a harmless warning at startup if Playwright is
missing — you can ignore it unless you hit a custom-domain Greenhouse URL.

Because `uvx` runs the server in an isolated environment, installing Playwright globally
won't reach it — you must pull in the `browser` extra so it lands in the server's env.

**If you installed via `uvx` / the plugin**, switch to a manual `.mcp.json` that requests
the extra:

```json
{
  "mcpServers": {
    "runway-mcp": {
      "command": "uvx",
      "args": ["--from", "runway-mcp[browser]", "runway-mcp"]
    }
  }
}
```

**If you installed from source:**

```bash
pip install -e ".[browser]"
```

Then, either way, download the browser binary once:

```bash
playwright install chromium
```

---

## How it works

Claude Code launches this server over stdio and calls its tools when relevant. You don't invoke the tools directly — Claude decides when to call them based on the conversation.

The tools **fetch and shape data**; Claude does the reasoning. The server never calls
back to the model (no MCP sampling), so it works on any MCP host — including Claude Code,
which does not support sampling. Claude extracts your profile from the CV and scores the
job-vs-profile match itself, using the rubric the tools return.

**One-call flow (recommended):**

```
You: "Evaluate this role for me: https://jobs.example.com/swe-123"
Claude:
  1. analyze_job(url) → job details + visa verdict + your profile + scoring guide
  2. [scores the match + applies the rubric] → APPLY/CONSIDER/SKIP, red flags, advice
```

**Or use the individual tools directly:**

```
Claude:
  1. fetch_job_posting(url)          → job title, company, country, full JD
  2. check_visa_sponsorship(company) → H-1B history, approval rate, verdict
  3. get_profile()                   → your stored CV, to score the fit against
```

The visa check only runs for US roles — Claude skips it for positions in other countries.

## Status

| Tool | Status |
|------|--------|
| `fetch_job_posting` | ✅ Working — Greenhouse, Ashby, Lever, generic fallback |
| `check_visa_sponsorship` | ✅ Working — real USCIS FY2024 data, auto-refreshes on startup |
| `setup_profile` | ✅ Working — saves the profile Claude extracts from your CV |
| `update_profile` | ✅ Working — update stored CV |
| `get_profile` | ✅ Working — returns the stored profile to score against |
| `analyze_job` | ✅ Working — one-call data gatherer (Claude scores the match) |

## Tools

### `analyze_job(url: str) -> AnalyzeJobResult`

One-call data gatherer. Fetches the job, checks visa sponsorship, and loads your stored profile, then returns a combined envelope plus a scoring guide. **Claude** scores the match and applies the recommendation rules — the server does not (no MCP sampling). Returns:

```json
{
  "job":     { "title": "...", "company": "...", "url": "..." },
  "visa":    { "verdict": "GREEN", "filings": 42, "approval_rate": 0.91 },
  "profile": { "name": "...", "skills": [...], "experience": [...] },
  "scoring_guide": {
    "instructions": "Score the match 0-100 and apply the rules...",
    "recommendation_rules": ["SKIP if visa RED or score < 40 ...", "..."]
  }
}
```

**Recommendation thresholds** (Claude applies these from the scoring guide):
- `APPLY` — visa GREEN and score ≥ 70
- `SKIP` — visa RED or score < 40 (SKIP takes precedence)
- `CONSIDER` — everything else

Requires a stored profile (run `setup_profile` first). If no profile exists, returns a clear error.

### `setup_profile(profile: ProfileData) -> ProfileSetupResult`

Persists a structured profile to `~/.config/runway-mcp/profile.json`. Claude reads your CV (`.pdf` or `.docx`) and extracts the `ProfileData` (name, skills, experience, education, …), then calls this tool to save it. Fails if a profile already exists — use `update_profile` to replace it. Required before `analyze_job`.

### `update_profile(profile: ProfileData) -> ProfileSetupResult`

Same as `setup_profile` but overwrites an existing profile. Use when you update your CV.

### `get_profile() -> GetProfileResult`

Returns the stored profile so Claude can score a job against it (e.g. in the individual-tools flow). Returns a structured `no_profile` error if none is stored yet.

### `check_visa_sponsorship(company: str) -> VisaResult`

Looks up a company's H-1B petition history via the [USCIS H-1B Employer Data Hub](https://www.uscis.gov/tools/reports-and-studies/h-1b-employer-data-hub).

Returns: `company`, `total_filings`, `approval_rate` (0–1), `verdict` (green/yellow/red), `source`.

**Verdict thresholds** (calibrated against FY2024 data, ~36k employers):
- `green` — ≥ 5 filings AND approval rate ≥ 80% (active sponsor, top ~10%)
- `yellow` — ≥ 1 filing AND approval rate ≥ 50% (has sponsored before)
- `red` — no record or rate below threshold

Data is downloaded and cached at `~/.cache/runway-mcp/uscis_h1b.csv` on first call (~2MB) and auto-refreshes to the latest FY on every server startup.

### `fetch_job_posting(url: str) -> JobPostingResult`

Fetches and parses a job posting from a URL.

Returns: `title`, `company`, `country`, `location`, `description`, `posted_date`, `source_url`.

**Supported job boards**

| ATS | Canonical domain | Company custom domain | Notes |
|-----|------------------|-----------------------|-------|
| Greenhouse | ✅ `boards.greenhouse.io`, `job-boards.greenhouse.io` | ✅ with `[browser]` extra | Custom domains require Playwright |
| Ashby | ✅ `jobs.ashbyhq.com` | ❌ not yet | |
| Lever | ✅ `jobs.lever.co`, `lever.co` | ❌ not yet | |
| Any board with `schema.org/JobPosting` markup | ✅ generic fallback | ✅ generic fallback | Quality depends on the site's markup |
| Workday, ADP, others | ⚠️ generic fallback (best-effort) | ⚠️ generic fallback (best-effort) | Works if the page embeds JSON-LD or microdata |
| SmartRecruiters | ❌ not yet | ❌ not yet | Has public API — planned |
| BambooHR | ❌ not yet | ❌ not yet | Has public API — planned |

**Known gaps**

| Scenario | Behavior | Workaround |
|----------|----------|------------|
| Greenhouse custom domain without Playwright installed | Fails with an actionable error | Install `[browser]` extra |
| Greenhouse custom domain behind bot protection | Fails — bot protection blocks even headless browsers | Use the canonical `boards.greenhouse.io` URL |
| Lever custom domain | Unsupported | Find the `jobs.lever.co/company/uuid` URL directly |
| Any aggregator URL (LinkedIn, Indeed, Handshake) | Unsupported | Use the URL from the "Apply" redirect |

## Tool vs. reasoning boundary

These tools only **fetch and shape data**. Claude handles all reasoning:
- Whether to call `check_visa_sponsorship` (only for US roles)
- How to interpret the verdict and score in context
- Whether the role is a good fit overall

This is intentional — tools that encode judgment make Claude less useful, not more.

## Tests

```bash
pytest -m contract      # fast contract tests
pytest -m integration   # server tool registration
pytest                  # full suite (214 tests)
```

## Contributing

```bash
pip install -e ".[dev]"
pre-commit install       # runs ruff lint + format before every commit
```

Highest-value next features (in priority order):
1. **Workday parser** — dedicated parser for better reliability on Workday boards
2. **SmartRecruiters** — public API, clean integration
3. **BambooHR** — public API, clean integration
4. **Lever custom domains** — same pattern as Greenhouse custom domains

PRs welcome.

## License

[MIT](LICENSE) © satovarb
