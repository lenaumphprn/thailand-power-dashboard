# Deploy Thailand Power & Renewables Pulse on GitHub

This package is ready for a GitHub Actions + GitHub Pages pilot.

## What the workflow does

Every Monday at 08:10 Asia/Bangkok, and whenever you trigger it manually, `.github/workflows/refresh-and-deploy.yml` will:

1. Install Python dependencies.
2. Run the automated tests.
3. Run `./run_weekly.sh` in live mode.
4. Persist refreshed `data/` outputs back to the repository so SQLite/history survive the next runner.
5. Copy the refreshed dashboard to `site/index.html`.
6. Deploy the static site to GitHub Pages.

## One-time setup in GitHub

1. Create a new repository, e.g. `thailand-power-pulse`.
2. For work use, choose Private/Internal and follow your organization's hosting policy.
3. Upload all files from this package to the repository root. Make sure the hidden `.github/workflows/refresh-and-deploy.yml` file is included.
4. Go to **Settings > Pages** and choose **GitHub Actions** as the source.
5. If available for your enterprise, set the Pages site's visibility to **Private**.
6. Go to **Settings > Actions > General > Workflow permissions** and allow **Read and write permissions** if your organization permits it. The workflow needs this to persist the refreshed SQLite/history files.
7. Go to **Actions > Refresh and deploy Thailand Power Pulse > Run workflow**.
8. Confirm the refresh job and deploy job both complete successfully.
9. Go to **Settings > Pages > Visit site**. Bookmark that URL; the same URL is redeployed after each weekly run.

## Schedule

The workflow currently uses:

```yaml
schedule:
  - cron: '10 8 * * 1'
    timezone: 'Asia/Bangkok'
```

Change the hour/minute/timezone if desired. The 10-minute offset avoids scheduling exactly at the top of the hour.

## If the first live run fails

The likely causes are organizational GitHub permissions or a public source blocking automated requests. Read the failed Action step. The pipeline is designed to retain prior verified values when an individual source watcher fails, but a configuration/permission failure can stop the workflow itself.

If your organization blocks GitHub Pages or direct writes to `main`, keep the same Python pipeline but deploy it using your approved internal CI/static-hosting platform instead.

## Pilot storage note

For the pilot, the SQLite database and audit CSVs are committed back to the repository after each run. This is simple and auditable. If the dashboard becomes long-lived or much larger, move persistent history to a managed database/object store and keep Pages only as the static front end.
