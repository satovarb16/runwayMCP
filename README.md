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

## Why runwayMCP no longer fetches job postings or checks visa sponsorship

Earlier versions of this server fetched job postings from Greenhouse/Ashby/Lever
(`fetch_job_posting`) and checked H-1B sponsorship history against USCIS data
(`check_visa_sponsorship`). Both tools, along with the read-only `get_profile`
migration hatch, are **removed as of this release** — not deprecated, deleted.

The fetch/scrape approach was fragile by construction: every job board changes its
markup on its own schedule, and a server that parses HTML is permanently one layout
change away from silently returning garbage. Claude, on the other hand, already reads
the job posting you paste into the conversation — it does not need the server to fetch
a second copy of the same text over HTTP. The server's job is to **persist and shape
data**, not to scrape it.

This also means the H-1B visa check goes away in its current form: the country
comparison it enabled will return as a lighter-weight, declarative check against a
country you tell the server yourself, once that lands in a follow-up release. There is
no server-side URL fetch involved in that either.

## Step 0 (required): save your resume

**Do this once before anything else.** `analyze_job` needs a stored resume — without one
it returns a `no_resume` error asking you to save one first.

```
You: "Here's my CV, save it as my general resume: /path/to/resume.pdf"
```

Claude reads your CV, drafts the resume as plain text, and saves it with
`save_resume_version` (see [Tools](#tools) below for the full reference).
Resume versions are **raw text, versioned, and append-only** — nothing is ever
overwritten or deleted. The first version you save (`parent_id=None`) establishes the
base of the tree. Later, when you decide to apply somewhere, Claude can save a version
*tailored* for that job pointing back at an existing one (`parent_id=<some version's
id>`, `job_url=<the job>`) without touching it — `analyze_job` never scores a job
against a resume already rewritten for that same job, since that would just be scoring
the resume against itself.

**Your "general" resume is the most recent version with no `job_url`** — not
necessarily the first one. Those are the same thing until you update your CV, and
different afterwards.

Updated your CV? Save it as a new version with `parent_id` set to any existing version's
id and **no `job_url`**. It becomes your general resume by being the most recent
untailored one. Passing `parent_id=None` a second time is rejected — there is only ever
one root.

### Migrating from a stored profile (pre-0.2.0)

If you used runwayMCP before this release, you may have a legacy structured profile at
`~/.config/runway-mcp/profile.json`. `setup_profile` and `update_profile` are gone — the
server no longer writes structured profiles, only versioned resume text. `get_profile()`
still works, read-only, for exactly **one release** (removed in 0.3.0), so you can migrate:

```
You: "Read my old profile and save it as my general resume."
Claude:
  1. get_profile()            → your old structured profile
  2. save_resume_version(...) → the same info, rewritten as resume text, parent_id=None
```

The server never writes to or deletes `profile.json` — once you have migrated, delete
`~/.config/runway-mcp/profile.json` yourself.

## Usage

```
You: "Evaluate this role for me: https://jobs.example.com/swe-123"
Claude:
  → analyze_job(url) — fetches job + checks visa + loads your general resume
  → scores the match and returns APPLY / CONSIDER / SKIP + reasoning
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
which does not support sampling. Claude drafts and tailors your resume text itself and
scores the job-vs-resume match using the rubric the tools return; the server only
persists what Claude gives it.

**One-call flow (recommended):**

```
You: "Evaluate this role for me: https://jobs.example.com/swe-123"
Claude:
  1. analyze_job(url) → job details + visa verdict + your general resume + scoring guide
  2. [scores the match + applies the rubric] → APPLY/CONSIDER/SKIP, red flags, advice
```

**Or use the individual tools directly:**

```
Claude:
  1. fetch_job_posting(url)          → job title, company, country, full JD
  2. check_visa_sponsorship(company) → H-1B history, approval rate, verdict
  3. list_resume_versions()          → pick the newest entry with job_url: null
  4. get_resume_version(id=<that id>) → that resume's text, to score against
```

Steps 3–4 are what `analyze_job` does internally. Reaching for
`get_resume_version(id="latest")` instead is the tempting shortcut and usually the wrong
one: `"latest"` means most recently *created*, and since Claude is told to save a
tailored version after every APPLY or CONSIDER, the newest version is typically written
for some other job. Scoring against it skews the result.

The visa check only runs for US roles — Claude skips it for positions in other countries.

## Status

| Tool | Status |
|------|--------|
| `fetch_job_posting` | ✅ Working — Greenhouse, Ashby, Lever, generic fallback |
| `check_visa_sponsorship` | ✅ Working — real USCIS FY2024 data, auto-refreshes on startup |
| `analyze_job` | ✅ Working — one-call data gatherer (Claude scores the match) |
| `save_resume_version` | ✅ Working — saves a resume version (raw text, append-only) |
| `get_resume_version` | ✅ Working — retrieves a resume version by id or "latest" |
| `list_resume_versions` | ✅ Working — lists saved resume versions, newest first |
| `get_profile` | ⚠️ Deprecated — read-only legacy migration hatch, removed in 0.3.0 |
| `save_job_analysis` | ✅ Working — persists an analyzed job record (upserts by URL) |
| `list_jobs` | ✅ Working — lists stored jobs, filterable by status, score, date |
| `set_application_status` | ✅ Working — sets a stored job's application status |

## Tools

### `analyze_job(url: str) -> AnalyzeJobResult`

One-call data gatherer. Fetches the job, checks visa sponsorship, and loads your **general resume**, then returns a combined envelope plus a scoring guide. **Claude** scores the match and applies the recommendation rules — the server does not (no MCP sampling).

The general resume is selected so that it was never written for the job being analyzed:
1. The most recently saved version with no `job_url`.
2. If every saved version has one, the most recent root version — **excluding any tailored to this exact job**.
3. If that leaves nothing, no resume is returned at all (see `no_resume` below).

Returns:

```json
{
  "job":     { "title": "...", "company": "...", "url": "..." },
  "visa":    { "verdict": "GREEN", "filings": 42, "approval_rate": 0.91 },
  "resume":  { "id": "...", "label": "...", "content": "...", "parent_id": null, "job_url": null, "created_at": "..." },
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

Requires a usable stored resume. Both error cases are checked **before** the job posting is fetched, so a request that cannot be scored anyway never pays for the network call:

- `error="no_resume"` — no resume was selected. Usually means you have not saved one yet, but it also fires when every stored version is tailored to this exact job, since scoring against those would inflate the match. Save an untailored version (no `job_url`) to fix it.
- `error="corrupt"` — the resume store exists but could not be read (malformed JSON, or a permissions/path problem). The file itself is the problem; saving another version will not help.

### `save_resume_version(content: str, label: str, parent_id: str | None = None, job_url: str | None = None) -> SaveResumeVersionResult`

Saves a new resume version as raw text. **Append-only** — no version is ever mutated or deleted, so your history is always intact. The store enforces a single-root tree:
- The **first** version you ever save must have `parent_id=None` — this establishes your general resume.
- Every version after that **must** set `parent_id` to an existing version's id. Passing `parent_id=None` again (or an unknown id) is rejected.
- `job_url` is optional — set it when a version is tailored for a specific job, so `list_resume_versions(job_url=...)` and `analyze_job`'s general-resume selection can tell tailored versions apart from your general one.

Returns `error="invalid_parent"` (empty store expects `parent_id=None`, non-empty store requires it) or `error="parent_not_found"` (unknown `parent_id`) on failure — no version is written in either case.

### `get_resume_version(id: str) -> GetResumeVersionResult`

Retrieves one resume version by its exact `id`, or the most recently *created* version via `id="latest"` (not necessarily the general one). Returns `error="not_found"` if no such version exists.

### `list_resume_versions(job_url: str | None = None, limit: int | None = None) -> ListResumeVersionsResult`

Lists saved resume versions **newest first**, as summaries — no `content` field, so listing 20 versions doesn't dump 20 full resumes into context. Call `get_resume_version` once you know which id you want. Filter to versions tailored for one job with `job_url`.

### `get_profile() -> GetProfileResult`

**Deprecated, removed in 0.3.0.** Read-only migration hatch: returns the legacy structured profile from `~/.config/runway-mcp/profile.json` if one exists, unchanged from before this release, so Claude can re-save it as resume text via `save_resume_version`. The server never writes to or deletes `profile.json` — delete it yourself once you've migrated. Returns `error="no_profile"` if none is stored.

### `save_job_analysis(url: str, title: str, company: str, visa_verdict: str, score: int | None = None, recommendation: str | None = None, notes: str | None = None) -> SaveJobResult`

Persists an analyzed job record, stamped with the current time. **Upserts by `url`** — saving the same URL again updates the existing record. Any argument you omit (`score`, `recommendation`, `notes`) is left as-is on an existing record rather than cleared, so a bare re-save of a reposted listing doesn't wipe your notes. `status` defaults to `not_applied` for new records and is preserved on upsert — set it separately with `set_application_status`.

### `list_jobs(since: str | None = None, status: str | list[str] | None = None, min_score: int | None = None, limit: int | None = None, sort_by: str = "analyzed_at") -> ListJobsResult`

Lists stored jobs, filtered → sorted → limited. `status` takes **one status or a list of statuses** — so "what have I applied to" (`applied`, `interviewing`, `offer`, and any other status you consider "in progress") is a single call; the server doesn't define what "applied" or "active" means, the caller passes the group it means. `sort_by` is `"analyzed_at"` (default, newest first) or `"score"` (descending, unscored jobs last).

### `set_application_status(url: str, status: str, notes: str | None = None) -> SetStatusResult`

Sets a stored job's application status to one of the 7 values: `not_applied`, `applied`, `interviewing`, `offer`, `rejected`, `withdrawn`, `ghosted`. **Transitions are deliberately unvalidated** — any status can move to any other (e.g. `rejected` → `interviewing` succeeds), because reopened hiring processes are real and the server doesn't get to say otherwise. Returns `error="not_found"` for an unknown URL, `error="invalid_status"` for an unrecognized value (record left unchanged).

**Upgrading from a pre-status `jobs.json`:** older versions stored `"applied": true/false` instead of `status`. The next time any job tool reads `~/.config/runway-mcp/jobs.json`, the server detects the old shape, writes a one-time `jobs.json.bak` backup, and migrates the file in memory (`applied: true` → `status: "applied"`, `applied: false` → `status: "not_applied"`) — no action needed on your part. The migration is only persisted to disk on the next write; a read alone never touches the file.

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
pytest                  # full suite (286 tests)
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
