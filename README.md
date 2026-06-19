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

## First-time setup: ingest your CV

Before scoring job matches, store your CV once:

```
You: "Set up my profile using my CV at /path/to/resume.pdf"
```

Supports `.pdf` and `.docx`. Claude will ask for sampling approval — this is expected.

## Usage

```
You: "Evaluate this role for me: https://jobs.example.com/swe-123"
Claude:
  → analyze_job(url) — fetches job + checks visa + scores CV match
  → Returns APPLY / CONSIDER / SKIP + reasoning
```

On first run, the server downloads USCIS H-1B data (~2MB) automatically.

## Optional: Playwright for JavaScript-heavy job boards

Some Greenhouse custom domains require a headless browser to parse. Install the optional extra:

```bash
pip install -e ".[dev,browser]"
playwright install chromium
```

Without it, canonical `boards.greenhouse.io` URLs always work. The server warns you at startup if Playwright is missing.

> **Note**: `setup_profile`, `analyze_match`, and `analyze_job` use MCP Sampling — Claude Code will ask for your approval the first time these tools make a sampling request. This is expected behavior.

---

## How it works

Claude Code launches this server over stdio and calls its tools when relevant. You don't invoke the tools directly — Claude decides when to call them based on the conversation.

**One-call flow (recommended):**

```
You: "Evaluate this role for me: https://jobs.example.com/swe-123"
Claude:
  1. analyze_job(url) → job details + visa verdict + match score + APPLY/CONSIDER/SKIP
  2. [reasons over the data] → context, red flags, application advice
```

**Or use the individual tools directly:**

```
Claude:
  1. fetch_job_posting(url)          → job title, company, country, full JD
  2. check_visa_sponsorship(company) → H-1B history, approval rate, verdict
  3. analyze_match(job)              → fit score vs your stored CV
```

The visa check only runs for US roles — Claude skips it for positions in other countries.

## Status

| Tool | Status |
|------|--------|
| `fetch_job_posting` | ✅ Working — Greenhouse, Ashby, Lever, generic fallback |
| `check_visa_sponsorship` | ✅ Working — real USCIS FY2024 data, auto-refreshes on startup |
| `setup_profile` | ✅ Working — CV ingestion via MCP Sampling |
| `update_profile` | ✅ Working — update stored CV |
| `analyze_match` | ✅ Working — job-vs-CV scoring |
| `analyze_job` | ✅ Working — one-call orchestrator |

## Tools

### `analyze_job(url: str) -> AnalyzeJobResult`

One-call orchestrator. Fetches the job, checks visa sponsorship, and scores the match against your stored CV. Returns a combined envelope:

```json
{
  "job":    { "title": "...", "company": "...", "url": "..." },
  "visa":   { "verdict": "GREEN", "filings": 42, "approval_rate": 0.91 },
  "match":  { "score": 84, "matched_skills": [...], "missing_skills": [...], "summary": "..." },
  "recommendation": "APPLY"
}
```

**Recommendation thresholds:**
- `APPLY` — visa GREEN and score ≥ 70
- `SKIP` — visa RED or score < 40 (SKIP takes precedence)
- `CONSIDER` — everything else

Requires a stored profile (run `setup_profile` first). If no profile exists, returns a clear error.

### `setup_profile(cv_path: str) -> ProfileSetupResult`

Reads a CV file (`.pdf` or `.docx`), sends it to Claude for structured extraction, and stores the result at `~/.config/runway-mcp/profile.json`. Required before `analyze_match` or `analyze_job`.

### `update_profile(cv_path: str) -> ProfileSetupResult`

Same as `setup_profile` but replaces an existing profile. Use when you update your CV.

### `analyze_match(job: JobPostingResult) -> MatchResult`

Scores a job posting against your stored CV using Claude. Returns a 0–100 score with matched skills, missing skills, and a summary. Requires a stored profile.

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
