# Deploy / upgrade Thailand Power & Renewables Pulse on GitHub

This v0.7 package works with GitHub Actions + GitHub Pages and can be used in the **same repository** as the earlier package.

## Recommended: upgrade the existing repository

Using the same repository preserves the repo history and the existing GitHub Pages URL.

### If you already use GitHub Desktop

1. Open the existing `thailand-power-pulse` repository in GitHub Desktop.
2. In File Explorer/Finder, open that repository's local folder.
3. Unzip the v0.7 package separately.
4. Copy **all files and folders from inside the v0.7 package** into the existing repository folder and choose **Replace** when prompted.
5. **Do not delete the `.git` folder.** That is what makes it the same repository.
6. If you want to preserve the dashboard history, keep the existing `data/thailand_power.db`. The v0.7 code can upgrade around the same historical database. If the package copy overwrites it, restore the repository's existing DB before committing.
7. Confirm this path exists at the repository root:

   `.github/workflows/refresh-and-deploy.yml`

8. In GitHub Desktop, review the changed files.
9. Commit with a message such as `Upgrade Thailand Power Pulse to v0.7`.
10. Click **Push origin**.
11. On GitHub, go to **Actions > Refresh and deploy Thailand Power Pulse > Run workflow**.
12. Confirm the refresh and deploy jobs both finish successfully.
13. Open the existing Pages URL. It should update in place; no new link is required.

### Important when copying the package

The repository root should contain `.github/`, `data/`, `tests/`, `README.md`, `run_weekly.sh`, `weekly_refresh.py`, etc. Do not upload one outer folder that contains all of these underneath it.

## First-time repository setup

If you are starting from scratch instead:

1. Create a new repository, e.g. `thailand-power-pulse`.
2. For work use, choose **Private/Internal** and follow your organization's hosting policy.
3. Preserve the package folder structure when adding the files. GitHub Desktop is the easiest option.
4. Go to **Settings > Pages** and choose **GitHub Actions** as the source.
5. If supported by your enterprise, make the Pages site **Private** before sharing it internally.
6. Go to **Settings > Actions > General > Workflow permissions** and allow **Read and write permissions** if your organization permits it. The workflow needs this to persist refreshed history.
7. Run **Actions > Refresh and deploy Thailand Power Pulse > Run workflow** once manually.
8. Go to **Settings > Pages > Visit site**.

## What the workflow does

Every Monday at 08:10 Asia/Bangkok, and whenever you run it manually, `.github/workflows/refresh-and-deploy.yml` will:

1. Install Python dependencies.
2. Run the automated tests.
3. Run the live weekly refresh.
4. Rebuild the dashboard using the latest verified public observations.
5. Run the consistency publication gate.
6. Run the public-source provenance gate.
7. Commit refreshed `data/` history back to the repository.
8. Deploy the refreshed dashboard to the same GitHub Pages URL.

The Pages artifact also exposes `dashboard_data.json`, `consistency_audit.csv`, `provenance_audit.csv` and `public_figure_manifest.json` for auditability.

## Schedule

The workflow uses:

```yaml
schedule:
  - cron: '10 8 * * 1'
    timezone: 'Asia/Bangkok'
```

## If a live run fails

Open the failed step under **Actions**. Typical causes are enterprise GitHub permissions or a public website temporarily blocking an automated request.

The data pipeline is intentionally conservative: an individual source failure should not manufacture a newer value. The last verified observation remains in the database with its original as-of date. A configuration, consistency or provenance block can stop publication entirely.

If your organization blocks GitHub Pages or direct writes to the default branch, the same Python pipeline can be used with an approved internal CI/static-hosting platform instead.
