# Thailand Power & Renewables Pulse — pipeline v0.5

v0.5 combines the governed quantitative pipeline, self-updating policy/project intelligence layer, and contextual price/cost comparisons.


## Public-source publication rule (v0.6)

The dashboard now has a hard publication gate:

- Every displayed quantitative market figure must resolve to a publicly accessible URL.
- Direct values are classified as `DIRECT_PUBLIC`.
- Calculated comparisons are classified as `DERIVED_PUBLIC` and must declare the formula and public input figures.
- No model-estimated, assumed or illustrative market values are allowed in the published dashboard.
- Official agency / regulator sources are preferred. Public SET/company disclosures are accepted for company/project milestones. Draft PDP2026 scenario MW values are explicitly tagged as public secondary consultation coverage until an official machine-readable table is available.
- `provenance_audit.py` runs after every dashboard build. Any missing public lineage is a BLOCK and the weekly run is not marked published.
- Audit output: `data/provenance_audit.csv`.

## What is automated now

**v0.5 display upgrade:** price/cost indicators now show period comparators and drivers (e.g., Pool Gas MoM change and LNG driver; Ft vs prior billing periods) rather than standalone levels.


A weekly run can:

1. Refresh EPPO demand / generation data and ERC tariff / Ft data.
2. Check whitelisted policy and company sources.
3. Filter out routine corporate noise such as earnings, dividends, board changes and AGM notices.
4. Normalize relevant items into a `market_event` ledger with event date, category, topic, company, technology, MW mentions, status signal, source and confidence.
5. Deduplicate previously seen items by source URL/title fingerprint.
6. Match project aliases conservatively and update project status **only when confidence is high**. It never adds company-project MW to national totals.
7. Rank recent developments by materiality and freshness, enforcing topic/category diversity.
8. Populate the three **Developments shaping the market** cards automatically.
9. Export the database, CSV audit files, dashboard JSON and refreshed HTML.

## Watched sources

### Official / regulatory
- ERC news releases
- ERC consultations
- EPPO news
- Ministry of Energy minister news

### Major utilities / developers
- GULF SET announcements
- GUNKUL SET announcements
- EGCO SET announcements
- B.Grimm Power SET announcements
- GPSC SET announcements
- RATCH SET announcements

The source list is editable in `watch_sources.json`.

## Key safety / governance rules

- Market totals remain sourced from governed national snapshots/cohorts. Company announcements are **milestone evidence**, not additive capacity.
- A project status update is automatic only when one unambiguous project alias is found and confidence is >=0.90.
- Unknown or ambiguous items can still be stored/scored without changing the project registry.
- Failed source checks do not erase or overwrite verified values. The prior verified state is retained and the source failure is logged.
- Procurement stages remain nested rather than additive.
- Contracted-sale MW and installed MW remain separate capacity bases.

## Main command

In an internet-enabled environment:

```bash
./run_weekly.sh
```

For the deterministic/offline build used in this package:

```bash
./run_weekly.sh --offline --asof 2026-09-08
```

## Outputs

Under `data/`:

- `thailand_power.db` — persistent SQLite database
- `dashboard_data.json` — dashboard payload
- `thailand_power_renewables_pulse_live.html` — refreshed dashboard
- `market_events.csv` — normalized event audit trail
- `weekly_digest.csv` — selected top developments by refresh date
- `source_watch_state.csv` — source health / last-success state
- renewable capacity / procurement / project CSVs from v0.2

## Intelligence tables

- `market_event` — normalized policy/project/market developments
- `project_alias` — conservative alias-to-project matching
- `source_watch_state` — success/failure state for each watcher
- `weekly_digest` — the three developments surfaced on the Pulse page

## Selection logic

Materiality is deterministic rather than an opaque LLM score. PDP / major policy, market-design and tariff changes receive the highest base weight. Project milestones are weighted by stage and MW scale. Freshness adds a small selection bonus. The digest also prevents duplicate topics/companies and aims to include a concrete project/investment signal when one is sufficiently material.

## Current seeded digest — 8 Sep 2026

1. EPPO public hearing on draft PDP2026.
2. Direct PPA direction broadening industrial clean-power access.
3. GULF commercial operation of two solar projects on 1 Sep 2026.

These are verified seed events so the package remains testable without network access. In live mode, newly discovered events are appended rather than replacing history.

## Tests / QA

```bash
python -m unittest discover -s tests -v
python weekly_refresh.py --offline --asof 2026-09-08
```

The known 1 MW inconsistency in ERC's 2024-round SCOD table remains an intentional warning; official source values are preserved.
