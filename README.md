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

Supports Ashby (`jobs.ashbyhq.com`) and Greenhouse (`boards.greenhouse.io`, `job-boards.greenhouse.io`). The `country` field in the output is what Claude uses to decide whether to call `check_visa_sponsorship`.

## Setup

### 1. Clone and install

```bash
git clone https://github.com/satovarb16/runwayMCP
cd runwayMCP
pip install -e ".[dev]"
```

### 2. Configure Claude Code

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
pytest                  # full suite (50 tests)
```

## Contributing

Next useful changes: LinkedIn support in `fetch_job_posting`, and additional job boards. PRs welcome.
