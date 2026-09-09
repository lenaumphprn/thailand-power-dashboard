# Thailand Power & Renewables Pulse — pipeline v0.7

v0.7 is the GitHub-ready weekly refresh package for the Thailand Power & Renewables dashboard.

## What changed in v0.7

- **Latest-period-first:** recurring market metrics use the latest publicly available period as the primary view. Prior full-year values are shown only as clearly labelled comparators.
- **Generation mix is YTD:** EPPO monthly generation-by-fuel observations are aggregated from January through the latest available month; the dashboard no longer leads with a stale prior-year generation stack.
- **Single source of truth:** repeated instances of the same metric are rebuilt from the same database observation so the headline, chart, bar, legend, comparator, as-of date and explanatory context stay synchronized.
- **Source-clickable figures:** quantitative figures and charts link to the public source used for the figure. Derived comparisons link to the public source observations used as inputs.
- **Consistency publication gate:** `consistency_audit.py` runs before provenance QA. If an unexplained display mismatch is found, publication is blocked.
- **Public-source provenance gate:** every displayed market number must be direct-public or transparently derived from public observations. Unsupported / model-estimated market figures are not published.
- **EGAT peak refresh:** the live pipeline refreshes both the current-year EGAT system peak and the latest monthly peak context from EGAT's public statistics page.
- **Historical continuity:** failed source checks do not replace a previously verified number. The prior verified observation remains with its original as-of date.

## Weekly refresh flow

A normal live run:

```bash
./run_weekly.sh
```

The pipeline then:

1. Seeds/retains the governed historical database.
2. Pulls latest public EPPO demand and generation data.
3. Pulls EGAT system-peak data.
4. Pulls ERC Ft and tariff decisions.
5. Checks whitelisted policy and project/company sources.
6. Updates project milestones conservatively and does not add company MW to national totals a second time.
7. Selects the most material current developments.
8. Rebuilds the complete HTML dashboard from the database.
9. Runs the display consistency audit.
10. Runs the public-figure provenance audit.
11. Publishes only if the blocking checks pass.

For a deterministic/offline validation build:

```bash
./run_weekly.sh --offline --asof 2026-09-09
```

## Main outputs

Under `data/`:

- `thailand_power_renewables_pulse_live.html` — refreshed dashboard
- `dashboard_data.json` — data payload used to build/synchronize the dashboard
- `thailand_power.db` — persistent SQLite history
- `consistency_audit.csv` — display consistency checks
- `provenance_audit.csv` — public-source/provenance checks
- `market_events.csv` — normalized market event ledger
- `weekly_digest.csv` — developments selected for Pulse
- `project_registry.csv` — governed project registry
- `renewable_capacity_snapshot.csv` — capacity snapshots by scope / stage
- `procurement_pipeline.csv` and `procurement_scod_schedule.csv` — procurement cohorts / schedule

`public_figure_manifest.json` documents the lineage rules and key figure sources/formulas.

## Source / definition rules

- Primary official sources include EPPO, EGAT, ERC, DEDE and the Ministry of Energy.
- Public SET/company disclosures are used for company/project milestones.
- Company-level MW are evidence of project status and are **not** added again to national procurement totals.
- Contracted-sale MW and installed MW remain separate capacity bases.
- Procurement stages that are nested are not summed as if independent.
- Derived figures (e.g. MoM, YoY, percentage-point change) must use public-source input observations.
- If a source is unavailable during a refresh, the last verified value is retained with its original as-of date.
- Detailed draft PDP2026 scenario MW may use clearly labelled public consultation coverage where an official machine-readable table is not available.

## QA / tests

Run:

```bash
python -m unittest discover -s tests -v
python weekly_refresh.py --offline --asof 2026-09-09
```

The package includes parser/transform tests for EPPO data, ERC Ft, EGAT peak data, renewable funnel logic, project overlap guardrails and intelligence selection.

One known source-table issue is deliberately preserved as a warning: ERC's published 2024-round 2027 SCOD total differs by 1 MW from the sum of the technology rows. The source value is retained and flagged rather than silently altered.

## GitHub deployment

See `DEPLOY_GITHUB.md`.

**You can upgrade the same existing repository.** Keeping the repository means keeping the same GitHub Pages URL and, if you retain `data/thailand_power.db`, the historical database as well.
