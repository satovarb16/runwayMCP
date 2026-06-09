# runwayMCP

MCP server for job search automation. Exposes two tools to Claude Code; reasoning stays in the model.

## Tools

### `check_visa_sponsorship(company: str) -> VisaResult`

Looks up a company's H-1B sponsorship history via the USCIS H-1B Employer Data Hub.

Pass the full employer name as it appears in the job posting (e.g. `"Google LLC"`, `"Microsoft Corporation"`). Abbreviated names may match subsidiaries.

Returns: `company`, `total_filings`, `approval_rate` (0–1), `verdict` (green/yellow/red), `source`.

**Verdict thresholds** (calibrated against FY2026 data):
- `green` — ≥ 5 filings AND approval rate ≥ 80% (top ~10% of sponsors)
- `yellow` — ≥ 1 filing AND approval rate ≥ 50%
- `red` — no record or rate below threshold

Data is cached locally at `~/.cache/runway-mcp/uscis_h1b.csv` on first call. Only call this tool for roles in the USA.

### `fetch_job_posting(url: str) -> JobPostingResult`

Fetches and parses a job posting from a URL.

Returns: `title`, `company`, `country`, `location`, `description`, `posted_date`, `source_url`.

The `country` field drives Claude's decision on whether to call `check_visa_sponsorship` — only relevant for US roles.

> Currently a stub. Real scraping is a future change.

## Tool vs. reasoning boundary

These tools only **shape data**. Claude decides:
- Whether to call `check_visa_sponsorship` (only for US roles)
- How to interpret the verdict and filing volume
- Whether a role is a good fit given the full context

## Setup

```bash
pip install -e ".[dev]"
```

Claude Code reads `.claude/settings.json` to launch the server automatically over stdio.

## Updating USCIS data

The server downloads FY2023 data automatically on first run. For newer data:

1. Go to https://www.uscis.gov/tools/reports-and-studies/h-1b-employer-data-hub
2. Click **Crosstab View** → select fiscal year → **Download to Excel** → CSV
3. Replace `~/.cache/runway-mcp/uscis_h1b.csv` with the downloaded file

The parser auto-detects encoding and column format across fiscal year versions.

## Tests

```bash
pytest -m contract      # fast contract tests (24 tests)
pytest -m integration   # server tool registration (1 test)
pytest                  # full suite (27 tests)
```
