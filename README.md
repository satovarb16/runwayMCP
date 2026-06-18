# runwayMCP

An MCP server that gives Claude Code real data for job search decisions.

Paste a job posting URL into Claude Code and it will automatically fetch the job, check the company's H-1B sponsorship history, and score how well it matches your CV — in a single call.

Built for international students (F-1/OPT) who need visa sponsorship for US roles.

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
| `check_visa_sponsorship` | ✅ Working — real USCIS FY2024 data |
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

Pass the full employer name as it appears in the job posting (e.g. `"Google LLC"`, `"Microsoft Corporation"`). Abbreviated names may match subsidiaries.

Returns: `company`, `total_filings`, `approval_rate` (0–1), `verdict` (green/yellow/red), `source`.

**Verdict thresholds** (calibrated against FY2024 data, ~36k employers):
- `green` — ≥ 5 filings AND approval rate ≥ 80% (active sponsor, top ~10%)
- `yellow` — ≥ 1 filing AND approval rate ≥ 50% (has sponsored before)
- `red` — no record or rate below threshold

Data is downloaded and cached at `~/.cache/runway-mcp/uscis_h1b.csv` on first call (~2MB).

### `fetch_job_posting(url: str) -> JobPostingResult`

Fetches and parses a job posting from a URL.

Returns: `title`, `company`, `country`, `location`, `description`, `posted_date`, `source_url`.

The `country` field is what Claude uses to decide whether to call `check_visa_sponsorship`.

**How parsing works**

1. **Dedicated parsers** — fast, API-backed, guaranteed results for known ATS platforms (Greenhouse, Ashby, Lever).
2. **Generic HTML fallback** — for any other URL, the tool attempts: JSON-LD (`schema.org/JobPosting`) → HTML microdata (`itemprop`) → `__NEXT_DATA__` (Next.js SSR). If all static extractors fail and the `[browser]` extra is installed, it renders with headless Chromium and retries. Bot-challenge pages (Cloudflare, Incapsula) are detected early and fail fast.

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

## Setup

### 1. Clone and install

```bash
git clone https://github.com/satovarb16/runwayMCP
cd runwayMCP
pip install -e ".[dev]"
```

### 2. Browser extra (Greenhouse custom domains)

```bash
pip install -e ".[dev,browser]"
playwright install chromium
```

Without this extra, custom-domain SPA pages will fail with an actionable error. Canonical `boards.greenhouse.io` URLs always work without it.

If Playwright is not installed, the server prints a warning to stderr on startup with install instructions.

### 3. Configure Claude Code

Create a `.mcp.json` file in your project root (or wherever you run Claude Code from):

```json
{
  "mcpServers": {
    "runway-mcp": {
      "command": "python",
      "args": ["-m", "server"],
      "cwd": "/absolute/path/to/runwayMCP"
    }
  }
}
```

Replace `cwd` with your actual path. On Windows use double backslashes: `"C:\\Users\\you\\runwayMCP"`.

> **Note**: `setup_profile`, `analyze_match`, and `analyze_job` use MCP Sampling — Claude Code will ask for your approval the first time these tools make a sampling request. This is expected behavior.

### 4. Ingest your CV

Before using `analyze_job` or `analyze_match`, store your CV:

```
You: "Set up my profile using my CV at /path/to/resume.pdf"
Claude: setup_profile("/path/to/resume.pdf")
```

### 5. Use it

```
You: "Evaluate this role for me: https://jobs.example.com/swe-123"
```

On first call, the server downloads the USCIS dataset (~2MB) to `~/.cache/runway-mcp/`.

## Updating USCIS data

The server downloads FY2024 data automatically on first run. For newer data:

1. Go to https://www.uscis.gov/tools/reports-and-studies/h-1b-employer-data-hub
2. Click **Crosstab View** → select fiscal year → **Download to Excel** → CSV
3. Replace `~/.cache/runway-mcp/uscis_h1b.csv` with the downloaded file

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
pytest                  # full suite (207 tests)
```

## Contributing

```bash
pip install -e ".[dev]"
pre-commit install       # runs ruff lint + format before every commit
```

Highest-value next features (in priority order):
1. **USCIS auto-refresh** — FY detection at startup, auto-download when new data is available
2. **Workday parser** — dedicated parser for better reliability on Workday boards
3. **SmartRecruiters** — public API, clean integration
4. **BambooHR** — public API, clean integration
5. **Lever custom domains** — same pattern as Greenhouse custom domains

PRs welcome.
