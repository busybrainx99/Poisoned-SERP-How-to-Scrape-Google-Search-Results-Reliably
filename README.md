# Poisoned SERP? How to Scrape Google Search Results Reliably

Collect Google Search pages with Zenrows Fetch, preserve the responses, and evaluate extracted organic results against a browser baseline and repeated collection rounds. This project produces JSON captures, domain-overlap reports, and CSV files for reviewing query relevance.
## Features

- Build localized Google Search URLs from a CSV input.
- Collect pages using Zenrows Fetch with `mode=auto` and `extract=auto`.
- Preserve returned HTML, automatic extraction, request settings, timing, and selected response headers.
- Extract organic titles, links, displayed URLs, and snippets with a separate HTML parser.
- Compare extracted counts with requested counts and identified domains with a browser baseline.
- Calculate pairwise Jaccard domain overlap across collection rounds.
- Export review rows, import relevance labels, and summarize review coverage.
- Preview requests without API calls and enforce a preflight credit-budget estimate for live collection.

## Prerequisites

- Python 3.10 or later. The included capture metadata records Python 3.14.6; compatibility across other versions has not been independently tested.
- A terminal and `pip`; Git is needed to clone the repository.
- A [Zenrows account](https://www.zenrows.com/register) and API key for live collection.
- Available Zenrows credits for live requests.

An API key is not required to evaluate the included captures or preview the input with `--dry-run`.

## Installation

### 1. Clone the repository

Replace `REPOSITORY_URL` with this repository's clone URL before running:

```bash
git clone REPOSITORY_URL
cd zenrows-serp-validation
```

### 2. Create a virtual environment

```bash
python -m venv .venv
```

### 3. Activate the environment

macOS or Linux:

```bash
source .venv/bin/activate
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

### 4. Install dependencies

```bash
python -m pip install -r requirements.txt
```

## Configuration

Run commands from the project root.

### API key

Create a `.env` file in the project root using `.env.example` as a template:

```dotenv
ZENROWS_API_KEY=your_api_key
```

Obtain your key from your Zenrows account. The collector loads `.env` with `python-dotenv`; you can also set the variable in your environment. The supplied `.gitignore` excludes `.env` and other `.env.*` files while allowing `.env.example`.

### Query input

`queries.csv` specifies one request per row:

```csv
query,language,country,proxy_country,result_count,round,route
best running shoes,en,us,us,10,round-1,zenrows_fetch
coffee grinder reviews,en,us,us,10,round-1,zenrows_fetch
weather forecast london,en,gb,gb,10,round-1,zenrows_fetch
```

| Field | Purpose |
| --- | --- |
| `query` | Search text; required and nonempty. |
| `language` | Google interface language (`hl`); required column. |
| `country` | Google search country (`gl`); required column. |
| `proxy_country` | Country supplied to Fetch for proxy routing; required column. |
| `result_count` | Requested Google result count (`num`); required integer from 1 to 100. This is not a guarantee of the extracted count. |
| `round` | Collection label used by the evaluator. Defaults to `unspecified` when omitted. |
| `route` | Recorded metadata, defaulting to `zenrows_fetch`. Changing it does not switch the collector to another provider. |

Use explicit round labels for comparisons. Keep the query and request settings unchanged when comparing repeated collections.

### Browser baseline

`baseline.json` maps each exact query string to a list of reference domains. `baseline.example.json` illustrates the format; replace its example domains before using it for your own evaluation.

```json
{
  "your search query": [
    "example.com",
    "example.org"
  ]
}
```

The evaluator requires a baseline file. A query missing from that file is reported as having no baseline. Read `baseline-notes.txt` for the provenance and limitations of the included references.

### Relevance labels

A review CSV must include `result_id` and `manual_relevance_label`. Accepted nonempty labels are `relevant`, `irrelevant`, and `unclear`; blank or missing labels count as unreviewed.

`relevance_review_simplified.csv` contains the labels for the included captures. Labels from that experiment do not automatically apply to newly collected pages.

## Project structure

```text
zenrows-serp-validation/
├── data/
│   ├── evaluations/
│   │   └── multi-round/
│   │       ├── evaluation-20261022T100357299816Z.json
│   │       └── relevance-review-20261022T100357299816Z.csv
│   └── raw/
│       ├── .gitkeep
│       └── ... nine saved SERP capture JSON files
├── scripts/
│   ├── collect_serps.py
│   └── evaluate_serps.py
├── .env                 # Local configuration; not for version control
├── .env.example
├── .gitignore
├── baseline-notes.txt
├── baseline.example.json
├── baseline.json
├── queries.csv
├── README.md
├── relevance_review_simplified.csv
└── requirements.txt
```

`data/raw/` preserves collection evidence. `data/evaluations/` stores generated reports and review exports. The scripts are separate entry points for collection and offline evaluation.

## How it works

```text
queries.csv + API key
        ↓
Build localized Google Search URLs
        ↓
Request pages through Zenrows Fetch
        ↓
Preserve automatic extraction and returned HTML
        ↓
Parse organic entries from HTML → data/raw/*.json
        ↓
Evaluate counts, baseline domains, and cross-round overlap
        ↓
Export relevance-review CSV → review and label entries
        ↓
Import labels and summarize → evaluation JSON + review CSV
```

The custom parser selects `div.tF2Cxc` result blocks, finds a linked `h3`, extracts a displayed URL from a qualifying `cite`, and reads snippets from `.VwiC3b`. It deduplicates identical title-and-link pairs. These selectors describe this implementation, not a guarantee of compatibility with every Google layout.

The evaluator reads `html_parsed_organic_results`, not the vendor's `parsed.organic_results`. Domain identification uses `displayed_url` only: it normalizes hostnames, removes a leading `www.`, retains other subdomains, and excludes Google domains.

Evaluation combines:

- Count comparison: extracted entries versus requested entries.
- Baseline overlap: matched baseline domains divided by the number of unique normalized baseline domains.
- Cross-round overlap: shared domains divided by the union of domains in each pair of captures.
- Query-term coverage: the proportion of query tokens appearing in the title or snippet, ignoring case. This is lexical matching, not semantic relevance.
- Imported relevance labels: reviewed judgments summarized separately from token coverage.

Cross-round comparisons expect exactly one capture per query per selected round. They check consistency of recorded language, country, proxy country, requested count, and route. Missing settings are reported; conflicting known settings or ineligible captures prevent comparison.

## Running the project

### 1. Evaluate the included experiment without live requests

```bash
python scripts/evaluate_serps.py   --input data/raw   --baseline baseline.json   --rounds round-1 round-2 round-3   --reviews relevance_review_simplified.csv
```

This creates new timestamped outputs under `data/evaluations/multi-round/`. It does not contact Fetch.

### 2. Preview collection

```bash
python scripts/collect_serps.py --input queries.csv --dry-run
```

The preview prints the selected input rows, planned request count, and the script's worst-case credit estimate. It does not validate a live response or use API credits.

### 3. Collect a round

The supplied CSV contains three rows labeled `round-1`:

```bash
python scripts/collect_serps.py   --input queries.csv   --output data/raw   --max-credit-budget 75
```

The script estimates 25 credits per planned request, so three rows require a budget of at least 75. This is a preflight calculation, not a provider-side spending limit or a measurement of the final charge. The collector does not track cumulative spending across separate runs.

Optional collection controls:

```bash
python scripts/collect_serps.py   --input queries.csv   --output data/raw   --limit 1   --min-delay-seconds 2   --max-credit-budget 25
```

`--limit` selects the first rows. `--min-delay-seconds` inserts a delay between requests. Requests run sequentially with a 180-second request timeout; the script does not implement its own retry loop.

### 4. Collect additional rounds

Update the CSV's `round` values to `round-2` and then `round-3`, running the collector once for each round. Preserve the other settings.

Avoid mixing a new experiment with the included captures under the same query and round labels. Repeated live collection appends new captures rather than replacing earlier ones, and duplicate query-round captures prevent cross-round comparison. Use a separate input directory for each experiment or distinct round labels.

### 5. Review newly collected results

First evaluate without `--reviews` to generate an unlabeled review CSV:

```bash
python scripts/evaluate_serps.py   --input data/raw   --baseline baseline.json   --rounds round-1 round-2 round-3
```

Open the generated `relevance-review-<timestamp>.csv`, review titles and snippets, and fill in `manual_relevance_label`. Optional fields include `review_reason`, `reviewer`, and `reviewed_at`.

Then run the evaluator again with the completed file:

```bash
python scripts/evaluate_serps.py   --input data/raw   --baseline baseline.json   --rounds round-1 round-2 round-3   --reviews data/evaluations/multi-round/relevance-review-<timestamp>.csv
```

Replace `<timestamp>` with the actual generated timestamp. For a single-round evaluation, use `--round round-1` instead of `--rounds ...`; outputs go under `data/evaluations/round-1/`.

Do not rename or modify captures after creating their review IDs. IDs incorporate the capture filename, SHA-256 file hash, and entry position. Renamed or modified files require new review IDs; existing labels will otherwise be unmatched.

For all available arguments:

```bash
python scripts/collect_serps.py --help
python scripts/evaluate_serps.py --help
```

## Output

### Collection records

By default, each request produces a timestamped JSON capture under `data/raw/`, named using a sanitized query, settings hash, and collection timestamp.

| Field | Contents |
| --- | --- |
| `query`, `google_url`, `settings` | Query, target URL, localization, round, and route metadata. |
| `collected_at`, `elapsed_seconds`, `http_status` | Collection start time, response timing, and HTTP status when a response is available. |
| `runtime` | Python, Requests, and collector versions. |
| `response_headers` | Available allowlisted request-ID, cost, and credit headers. |
| `parsed` | Unchanged automatic extraction returned by Fetch. |
| `html` | Returned HTML. |
| `html_parsed_organic_results` | Custom-parsed titles, links, displayed URLs, and snippets. |

Failure records may omit successful-response fields and include an `error`. The collector exits nonzero when it counts failures; inspect saved records and terminal messages for details.

### Evaluation files

The evaluator writes:

- `evaluation-<timestamp>.json`: capture hashes, statuses, counts, identified and unresolved domains, baseline comparisons, lexical coverage, cross-round comparisons, and relevance-review summaries.
- `relevance-review-<timestamp>.csv`: result-level review rows, lexical metrics, and any imported review labels.

The default directory is `data/evaluations/multi-round/` for multiple selected rounds, or `data/evaluations/<round>/` for one round. `--output` changes the parent directory.

The report records the baseline snapshot and unmatched review IDs. Duplicate review IDs and invalid nonempty labels cause an input error.

### Included experiment

The included nine captures were collected on October 21, 2026, across three queries and three rounds. All returned HTTP 200.

| Query | Entries per round | Baseline overlap, rounds 1 / 2 / 3 | Pairwise Jaccard, 1–2 / 1–3 / 2–3 |
| --- | --- | --- | --- |
| Best running shoes | 8 / 8 / 8 | 66.7% / 55.6% / 66.7% | 40.0% / 75.0% / 40.0% |
| Coffee grinder reviews | 9 / 9 / 9 | 77.8% / 77.8% / 88.9% | 60.0% / 77.8% / 77.8% |
| Weather forecast London | 8 / 8 / 8 | 100% / 100% / 100% | 100% / 100% / 100% |

All 75 result appearances were labeled relevant in the supplied review. These include repeats across rounds, not 75 unique pages. The experiment did not provide sufficient evidence to classify the captures as poisoned; the evaluator itself does not assign that classification.

## Technologies

- Python
- Zenrows Fetch
- Requests
- Beautiful Soup 4
- python-dotenv
- Python standard-library CSV, JSON, hashing, and argument-parsing modules
- pytest, included as a test dependency

## Data sources

- Google Search pages collected through Zenrows Fetch.
- Manually reviewed browser references stored in `baseline.json` and described in `baseline-notes.txt`.
- Result-level relevance labels in `relevance_review_simplified.csv`.

The browser references are single observations, not historical baselines. The notes record unconfirmed browser connection locations and an unresolved result-count URL detail for the coffee-grinder reference. Do not interpret the baseline as a fully controlled location-matched ground truth.

The evaluation covers custom-parsed organic entries. It does not evaluate ads, shopping blocks, People Also Ask, AI Overviews, destination-page accuracy, or ranking-position changes.

## Troubleshooting

### `ZENROWS_API_KEY` is missing

Create `.env` in the project root or export the environment variable. Confirm the variable name matches `.env.example`.

### Live collection requires a credit budget

Supply `--max-credit-budget` large enough for the script's estimate: selected CSV rows multiplied by 25. Use `--dry-run` first to inspect the plan.

### No captures found for the selected rounds

Check `--input` and the `settings.round` values inside capture files. The evaluator reads JSON files directly inside the specified directory, not nested directories.

### Cross-round comparison reports `not_compared`

Inspect its `reasons`. Common causes include duplicate or missing query-round captures, conflicting request settings, a failed request, or missing parser output.

### A known result has no identified domain

Domain matching uses `displayed_url` only. It does not follow links or decode `/goto?url=...` destinations. The parser only accepts displayed citations beginning with `http://` or `https://`, so some visible site labels are not retained as usable domains. Unknown-domain entries remain in result counts and relevance review but do not contribute to domain overlap.

### Automatic extraction has no organic results

Inspect `html_parsed_organic_results` and the saved HTML separately. The two extraction paths are independent; an empty or absent automatic organic group does not establish that the HTML contains no organic entries.

### Fewer entries were extracted than requested

Inspect the returned HTML and parser selectors before assigning a cause. The requested count, returned page content, and successfully extracted entry count are different things.

### Review labels are unexpectedly unreviewed

Inspect `unmatched_review_ids` and confirm that capture filenames and contents have not changed. New captures require their own review rows and labels.

### Relevance coverage looks low despite useful results

`query_term_coverage` checks exact tokens, ignoring case. It does not stem words or recognize synonyms; `grinder` and `grinders` are different tokens. Use the separate review labels for judgments of relevance.

## Related article

This repository accompanies the Zenrows article:

Poisoned SERP? How to Scrape Google Search Results Reliably

ARTICLE_URL — 