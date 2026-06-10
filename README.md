# runwayMCP

An MCP server that gives Claude Code real data for job search decisions.

When you paste a job posting URL into Claude Code, it can automatically check whether the company has a history of H-1B sponsorship — so you know before you spend hours on an application.

Built for job seekers who need visa sponsorship for US roles.

## How it works

Claude Code launches this server over stdio and calls its tools when relevant. You don't invoke the tools directly — Claude decides when to call them based on the conversation.

```
You: "Evaluate this role for me: https://jobs.example.com/swe-123"
Claude:
  1. fetch_job_posting(url)          → job title, company, country, full JD
  2. check_visa_sponsorship(company) → H-1B history, approval rate, verdict
  3. [reasons over the data]         → fit analysis, red flags, application advice
```

The visa check only runs for US roles — Claude skips it for positions in other countries.

## Status

| Tool | Status |
|------|--------|
| `check_visa_sponsorship` | ✅ Working — real USCIS data |
| `fetch_job_posting` | ✅ Working — Ashby and Greenhouse |

## Tools

### `check_visa_sponsorship(company: str) -> VisaResult`

Looks up a company's H-1B petition history via the [USCIS H-1B Employer Data Hub](https://www.uscis.gov/tools/reports-and-studies/h-1b-employer-data-hub).

Pass the full employer name as it appears in the job posting (e.g. `"Google LLC"`, `"Microsoft Corporation"`). Abbreviated names may match subsidiaries.

Returns: `company`, `total_filings`, `approval_rate` (0–1), `verdict` (green/yellow/red), `source`.

**Verdict thresholds** (calibrated against FY2026 data, ~36k employers):
- `green` — ≥ 5 filings AND approval rate ≥ 80% (active sponsor, top ~10%)
- `yellow` — ≥ 1 filing AND approval rate ≥ 50% (has sponsored before)
- `red` — no record or rate below threshold

Data is downloaded and cached at `~/.cache/runway-mcp/uscis_h1b.csv` on first call (~2MB).

### `fetch_job_posting(url: str) -> JobPostingResult`

Fetches and parses a job posting from a URL.

Returns: `title`, `company`, `country`, `location`, `description`, `posted_date`, `source_url`.

The `country` field in the output is what Claude uses to decide whether to call `check_visa_sponsorship`.

**How parsing works**

The tool uses a two-tier strategy:

1. **Dedicated parsers** — fast, API-backed, guaranteed results for known ATS platforms (Greenhouse, Ashby, Lever).
2. **Generic HTML fallback** — for any URL not matched by a dedicated parser, the tool attempts standards-based extraction in order: JSON-LD (`schema.org/JobPosting`) → HTML microdata (`itemprop`) → `__NEXT_DATA__` (Next.js SSR payload). If all static extractors fail and the `[browser]` extra is installed, it renders the page with a headless Chromium and retries the cascade. Bot-challenge pages (Cloudflare, Incapsula) are detected early and fail fast with a clear error.

This means many boards beyond the table below work out of the box — anything that embeds `schema.org/JobPosting` markup (Workday, ADP, many custom career pages) will extract successfully via the generic path.

**Supported job boards**

The typical flow: you find a job on LinkedIn/Indeed/Handshake → click Apply → get redirected to the company's ATS page → paste that final URL here. This tool never touches the aggregator — it only needs the destination URL.

| ATS | Canonical domain | Company custom domain | Notes |
|-----|------------------|-----------------------|-------|
| Greenhouse | ✅ `boards.greenhouse.io`, `job-boards.greenhouse.io`, `job-boards.eu.greenhouse.io` | ✅ with `[browser]` extra | Custom domains require Playwright — see setup below |
| Ashby | ✅ `jobs.ashbyhq.com` | ❌ not yet | |
| Lever | ✅ `jobs.lever.co`, `lever.co` | ❌ not yet | |
| Any board with `schema.org/JobPosting` markup | ✅ generic fallback | ✅ generic fallback | No guarantees — quality depends on the site's markup |
| Workday, ADP, Jacobs, others | ⚠️ generic fallback (best-effort) | ⚠️ generic fallback (best-effort) | Works if the page embeds JSON-LD or microdata |
| SmartRecruiters | ❌ not yet | ❌ not yet | Has public API — dedicated parser planned |
| BambooHR | ❌ not yet | ❌ not yet | Has public API — dedicated parser planned |
| iCIMS | ❌ not yet | ❌ not yet | |

**Known gaps**

| Scenario | Behavior | Workaround |
|----------|----------|------------|
| Greenhouse custom domain (e.g. `cribl.io/jobs?gh_jid=123`) without Playwright installed | Fails with an actionable error | Install `[browser]` extra |
| Greenhouse custom domain behind bot protection (Incapsula, Cloudflare) | Fails — bot protection blocks even headless browsers | Use the canonical `boards.greenhouse.io` URL if available |
| Lever custom domain (e.g. `stripe.com/jobs/uuid`) | Unsupported — Lever token not detectable from HTML | Find the `jobs.lever.co/stripe/uuid` URL directly |
| Generic fallback page behind bot protection | Fails with a bot-challenge error | No workaround — the site blocks scrapers |
| Any aggregator URL (LinkedIn, Indeed, Handshake) | Unsupported — these are not ATS pages | Use the URL from the "Apply" redirect instead |

## Setup

### 1. Clone and install

```bash
git clone https://github.com/satovarb16/runwayMCP
cd runwayMCP
pip install -e ".[dev]"
```

### 2. Browser extra (Greenhouse custom domains)

Custom-domain Greenhouse job pages that use a JavaScript SPA (React, Next.js) do not include the board token in static HTML. To handle those, install the optional `browser` extra and the Chromium binary:

```bash
pip install -e ".[dev,browser]"
playwright install chromium
```

Without this extra, custom-domain SPA pages will fail with an actionable error message telling you to install it. Canonical `boards.greenhouse.io` URLs always work without it.

### 3. Configure Claude Code

Add to your Claude Code `settings.json` (`.claude/settings.json` in the project, or `~/.claude/settings.json` globally):

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

Replace the `cwd` with your actual path. On Windows use double backslashes: `"C:\\Users\\you\\runwayMCP"`.

### 3. Use it

Open Claude Code and ask it to evaluate a job posting. On first call, the server downloads the USCIS dataset (~2MB) to `~/.cache/runway-mcp/`.

## Updating USCIS data

The server downloads FY2023 data automatically on first run. For newer data:

1. Go to https://www.uscis.gov/tools/reports-and-studies/h-1b-employer-data-hub
2. Click **Crosstab View** → select fiscal year → **Download to Excel** → CSV
3. Replace `~/.cache/runway-mcp/uscis_h1b.csv` with the downloaded file

The parser auto-detects encoding and column format across fiscal year versions.

## Tool vs. reasoning boundary

These tools only **fetch and shape data**. Claude handles all reasoning:
- Whether to call `check_visa_sponsorship` (only for US roles)
- How to interpret the verdict in context
- Whether the role is a good fit overall

This is intentional — tools that encode judgment make Claude less useful, not more.

## Tests

```bash
pytest -m contract      # fast contract tests
pytest -m integration   # server tool registration
pytest                  # full suite
```

## Contributing

Highest-value next features (in priority order):
1. **SmartRecruiters** — public API, clean integration
2. **BambooHR** — public API, clean integration
3. **Workday** — large employer coverage, needs scraping
4. **Lever custom domains** — same pattern as Greenhouse custom domains
5. **Greenhouse/Lever/Ashby custom domains without Playwright** — lighter alternative to headless browser

PRs welcome.
