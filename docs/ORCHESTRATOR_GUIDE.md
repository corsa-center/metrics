# Orchestrator Guide: Coordinating Metrics and Dashboard

## Overview

The **orchestrator** is the collection program. It:
1. Reads the package catalog from the
   [metrics_packages](https://github.com/corsa-center/metrics_packages)
   repository (`package_config/*.yaml`, see [PACKAGE_CONFIG.md](PACKAGE_CONFIG.md))
2. Collects metrics for each package across the three CASS dimensions
3. Writes one `{repo}-metrics/metrics.json` (and `report.html`) per package

## Architecture

```
metrics_packages            metrics (this repo)             metrics_data (private)
package_config/*.yaml ──→  orchestrator.py, collectors ──→  data/YYYYMMDD/{repo}-metrics/
                           tools/dashboard_data.py     ──→  data/YYYYMMDD/intReposInfo.json, ...
                           tools/spack_dependencies.py            │
                                                                  ↓
                                                    dashboard: pages.yml copies the
                                                    newest data in at each deploy
```

The workflows that run the code live in metrics_data and check out this
repository's `main`; see [Automation](#automation-via-github-actions).

## Installation

### 1. Prerequisites

```bash
cd ~/work/metrics
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install pyyaml  # For config handling
```

### 2. Configuration

Edit `../config/orchestrator.yaml`:

```yaml
# Enable collectors you want to use
collectors:
  citation: true
  community: true
  licensing: true
  openssf_badge: true
  openssf_scorecard: true
  ci_cd: true
  reproducibility: true
  accessibility: true

# Configure API credentials via environment variables
api_credentials:
  github:
    token: ${GITHUB_TOKEN}
  # GitLab, keyed by host. gitlab.com is recognized even without an entry
  # here -- an absent token still works for public repos, just at GitLab's
  # lower unauthenticated rate limit. A self-hosted instance (e.g.
  # gitlab.kitware.com) has no way to self-identify as GitLab from its
  # hostname alone, so it must be listed here explicitly: being listed is
  # itself what makes it recognized, not a separate allowlist.
  gitlab:
    gitlab.com:
      token: ${GITLAB_TOKEN}
    gitlab.kitware.com:
      token: ${GITLAB_KITWARE_TOKEN}
```

### 3. Set Environment Variables

```bash
export GITHUB_TOKEN="your_github_token"
export GITLAB_TOKEN="your_gitlab_com_token"           # Optional -- public repos work without it
export GITLAB_KITWARE_TOKEN="your_kitware_token"      # Optional, only needed for gitlab.kitware.com repos
export SEMANTIC_SCHOLAR_KEY="your_ss_key"  # Optional
export OPENALEX_EMAIL="your@email.com"     # Optional but recommended
```

### Supported hosts

Each package file's `repo_type` (see `docs/PACKAGE_CONFIG.md`) chooses the
forge that collects it:

- `github` -- GitHub, for repositories on github.com. GitHub Enterprise
  hosts are skipped: the GitHub forge only talks to api.github.com.
- `gitlab` -- GitLab, on whatever host `repo_url` names: gitlab.com or any
  self-hosted instance (e.g. `gitlab.kitware.com` for ParaView, VTK,
  Viskores). A host listed under `api_credentials.gitlab.<host>` in
  `config/orchestrator.yaml` gets that token; any other host is queried
  without one, which works for public projects at a lower rate limit.
- Anything else -- skipped entirely: all three CASS dimensions render as
  "not yet collected" for that package, rather than a false negative from
  querying the wrong API.

A package without `repo_type` falls back to the hostname: github.com is
GitHub; gitlab.com and hosts listed under `api_credentials.gitlab` are
GitLab; anything else is skipped.

Collectors only ever see the platform-neutral `Forge` interface
(`forge/interface.py`), implemented by `forge/github.py` and
`forge/gitlab.py`. `Forge.for_repo` is the one place a
concrete forge is chosen.

GitLab-hosted packages get the same 3-dimension coverage as GitHub ones,
with a few metrics GitLab genuinely cannot supply -- these render as "not
yet collected", never as a zero or a failing result, so don't mistake them
for bugs:

- **Large issue counts** -- GitLab can't OR labels in one query, so label
  counts (newcomer issues, defect trend) list matching issues per label
  and combine them. Past 2,000 issues for one label or date window the
  count is reported as not collected rather than undercounted. GitLab has
  no "Bug" issue type, so the defect trend always uses labels there.
- **Per-workflow CI breakdown** -- GitHub Actions has named workflows as a
  first-class concept; GitLab Pipelines don't have an equivalent grouping.
  `ci_workflows`/`ci_workflow_runs` always return empty for GitLab repos.
- **Token-gated lookups** -- some GitLab instances (gitlab.kitware.com
  among them) require a token to list a project's members or labels.
  Without one, every author counts as an outside contributor (GitLab has no
  author association field, so membership is the only signal), and label
  counts fall back to conventional names (`good first issue`, `bug`) instead
  of the project's own (ParaView's `triage:easy`). Set the host's token
  under `api_credentials.gitlab.<host>`.
- **Community profile** (`/community/profile`) -- a GitHub-only aggregate
  endpoint. GitLab repos rely entirely on `community_health.py`'s own
  direct file checks (CODE_OF_CONDUCT, GOVERNANCE, CONTRIBUTING), which run
  the same on both platforms.
- **OpenSSF Scorecard** -- its published dataset skews heavily toward
  GitHub-hosted projects; a GitLab repo not in that dataset reads as "not
  yet collected", not a failure.
- **Weekly commit/contributor stats** -- GitHub exposes these via
  dedicated `/stats/participation` and `/stats/contributors` endpoints;
  GitLab has no equivalent, so they're derived from paged commit history
  instead (bounded to a fixed number of pages), which is close but not
  identical in methodology.

## Usage

### Basic Usage

```bash
# Collect metrics for all software
python orchestrator.py --config config/orchestrator.yaml
```

### Filter Specific Software

```bash
# Test with HDF5 only
python orchestrator.py --config config/orchestrator.yaml --software HDF5

# Or test with multiple packages
python orchestrator.py --config config/orchestrator.yaml --software "hdf5|amrex|petsc"
```

### Dry Run Mode

```bash
# Test without writing files
python orchestrator.py --config config/orchestrator.yaml --dry-run
```

## Outputs

The orchestrator creates several files:

### 1. Per-Package Metrics
**Location:** `{output_path}/{repo}-metrics/metrics.json` (`./output` by default)

The package's catalog entry (`metadata`), `stars`, `forks`, `generated_at`,
each CASS section keyed by number under `impact`, `ecosystem` and `quality`
(a `title` and an HTML `data` string), and the dashboard data described in
[Dashboard Data Files](#5-dashboard-data-files).

### 2. Summary Report
**Location:** `../output/orchestrator_summary.json`

Contains:
- Total packages processed
- Average scores
- Score distribution
- Top performing packages

### 3. Log File
**Location:** `../orchestrator.log`

Detailed logs of the collection process

### 4. Per-Project Report
**Location:** `./output/{repo}-metrics/report.html` (beside that package's `metrics.json`)

A standalone page showing how one package's results were reached, meant to
be linked from the package's dashboard page:

- every dashboard row with its value, met / not met / not scored, and the
  evidence behind it (file links, counts, failing Scorecard checks)
- under each row, the threshold from `../config/thresholds.yaml` it was judged
  against, with any `thresholds:` overrides applied
- rows whose text was set by `overrides` in `../package_config` or the
  project's own metrics file, flagged as configured rather than measured
- how the dimension scores are computed (the average of each collector's
  percentage, listed per collector) and weighted into the overall score, and
  which collectors the project's config turned off

It is rendered from the same per-section data as `metrics.json`, so it
always matches the dashboard. See `../package_report.py`.

### 5. Dashboard Data Files

Besides the catalog entry, `metadata` records what the forge reports about
the repository: description, homepage, language, license, dates, `topics`,
`owner_avatar_url`, `parent` (the upstream of a fork), and the counts
`stars`, `forks`, `pull_requests` (`open`/`closed`/`merged`), `issues`
(`open`/`closed`) and `mentionable_users`. Each `metrics.json` also has:

- `dependencies` -- the GitHub dependency graph (`null` on GitLab, which
  has none), with `repositories`: the primary language and owner of each
  repository a dependency points at
- `spack` -- the package's Spack dependencies and dependents (`{name,
  dependencies, dependents}`; `null` when Spack has no package named after
  the repository). Collected only when `spack` is on `PATH`: one `spack
  python tools/spack_dependencies.py` run per batch covers every package in
  it.

A key is left out when its lookup failed, rather than written empty.

The metrics_data repository's daily "Generate dashboard data" workflow turns
those into the dashboard's `intReposInfo.json`, `intRepos_Topics.json`,
`intRepos_Dependencies.json`, `dependencyInfo.json`,
`spackPackageDependencies.json` and `spackPackageDependents.json`, beside the
day's metrics in `data/YYYYMMDD/`. No API calls; it uses each package's
newest `metrics.json` from the last 7 days, and all the dashboard-specific
shaping lives in this one script:

```bash
python tools/dashboard_data.py --data-dir data --output data/20261026
```

Unpublished packages are kept, with `"published": false` on their
`intReposInfo.json` entry.

## Automation via GitHub Actions

Collection and generation run in the
[metrics_data](https://github.com/corsa-center/metrics_data) repository,
each checking out this repository's `main`:

- **Collect Metrics** (`collect-and-sync.yml`, every 4 hours) runs the
  orchestrator with `--use-summary-as-filter --max-packages 20`, so each day's
  first runs cover every package once, and commits the results to
  `data/YYYYMMDD/`.
- **Generate Dashboard Data** (`generate-dashboard-data.yml`, daily at 12:30
  UTC) writes the dashboard files beside them (see
  [Dashboard Data Files](#5-dashboard-data-files)).

The dashboard's daily Pages deploy (13:30 UTC) copies the newest of both into
its `explore/github-data/`; nothing it shows is committed to the dashboard
repository.

## Configuration Options

The following sections describe the options available in the config/orchestrator.yaml file to control 
the operation of the orchestrator.

### Enable/Disable Collectors

```yaml
collectors:
  citation: true          # Academic impact (4.1.1)
  community: true         # CoC, governance, maintenance, engagement (4.2.x)
  licensing: true         # License analysis (4.2.2)
  openssf_badge: true     # OpenSSF best practices badge
  openssf_scorecard: true # OpenSSF scorecard
  ci_cd: true             # CI/CD development practices (4.3.2)
  reproducibility: true   # Reproducibility (4.3.3)
  accessibility: true     # Accessible build systems (4.3.5)
```

### Adjust Metric Weights

```yaml
metric_weights:
  citation: 0.4    # 40% of overall score
  community: 0.4   # 40% of overall score
  licensing: 0.2   # 20% of overall score
```

### Configurable Pass/Fail Thresholds

Every sub-metric's pass/fail decision (the ✓/✗ shown on the dashboard) is
governed by a value in [`../config/thresholds.yaml`](../config/thresholds.yaml) --
that file **is** the full, current list of every changeable threshold and
its default, organized by CASS report section number and the exact
sub-metric label, with an inline comment explaining each one.
([METRICS_CATALOG.md](METRICS_CATALOG.md)'s "Meets threshold when" column
shows the same values in prose, for cross-reference against the report's
own language.)

To change a default without editing collector code, copy the relevant
section/label(/param) structure from `../config/thresholds.yaml` into a
`thresholds:` block in `config/orchestrator.yaml`, e.g.:

```yaml
thresholds:
  "4.3.1":
    Test Coverage Excellence: 70       # was 80 -- override a plain number
  "4.3.2":
    CI/CD Effectiveness Assessment:
      max_cycle_time_hours: 72         # override one param, others keep their default
  "4.2.10":
    Community Health Trends:
      - stable                         # override a set-membership list
      - increasing
      - improving
```

A few things worth knowing before overriding one:

- **Fail-fast, not silent.** An override for a section, label, or param that
  `../config/thresholds.yaml` doesn't already declare a default for raises an
  error when the orchestrator starts, rather than being quietly ignored.
- **Partial overrides merge.** For a sub-metric with several named params
  (like `CI/CD Effectiveness Assessment` above), overriding one param
  leaves its siblings at their default -- no need to repeat values you're
  not changing.
- **Not every sub-metric is configurable.** A pure presence/structural
  check (e.g. "a license was identified at all") has no entry in
  `../config/thresholds.yaml` and can't be overridden here -- see that file's
  header comment for what's deliberately excluded and why.
- **Thresholds never override a collection gap.** If a sub-metric's data
  couldn't be collected at all (rate limit, API error, etc.), it's excluded
  from scoring regardless of what any threshold says -- an override can
  change where the bar is, not turn "we don't know" into "it passed."
- **Per-project overrides aren't wired up yet.** This `thresholds:` block
  is global, applying to every project the orchestrator scores. Overriding
  a threshold for one project only is planned but not yet built.

### Rate Limiting

```yaml
rate_limiting:
  delay_between_packages: 2  # Seconds between API calls
  api_retry_attempts: 3      # Retry failed calls
  api_retry_delay: 5         # Seconds between retries
```

### Filters

```yaml
filters:
  categories: ["Mathematical Libraries"]  # Only these categories
  exclude_repos: ["test/repo"]            # Skip these repos
  min_stars: 10                           # Minimum GitHub stars
```

## Workflow Examples

### Example 1: Full Update

```bash
# Collect every package in the catalog:
python orchestrator.py --config config/orchestrator.yaml

# Results:
# - output/{repo}-metrics/metrics.json and report.html per package
# - output/orchestrator_summary.json
```

### Example 2: Test New Software

```bash
# Before adding to CASS catalog, test metrics collection:
python orchestrator.py --software "newproject/repo" --dry-run

# Review output in terminal
# Check output/orchestrator_summary.json
```

### Example 3: Update Single Package

```bash
# HDF5 just released new version, update its metrics:
python orchestrator.py --software HDF5

# Only HDF5 metrics updated in dashboard
```

### Example 4: Bulk Collection with Filters

```bash
# Collect only Mathematical Libraries category:
# Edit config/orchestrator.yaml:
filters:
  categories: ["MATHEMATICAL LIBRARIES"]

python orchestrator.py --config config/orchestrator.yaml
```

## Troubleshooting

### Issue: "Catalog not found"
**Solution:** Check that `catalog_url` in config points to the
`package_config` directory of corsa-center/metrics_packages, and that
`GITHUB_TOKEN` is set if you are rate limited

### Issue: API rate limits exceeded
**Solution:**
- Add `SEMANTIC_SCHOLAR_KEY` for higher limits
- Increase `delay_between_packages` in config
- Use `--software` filter to process fewer packages

### Issue: Import errors for collectors
**Solution:**
- Ensure collectors are properly implemented
- Set collector to `false` in config if not ready
- Check Python path and virtual environment

### Issue: GitHub Actions fails to push
**Solution:**
- Verify `DASHBOARD_PUSH_TOKEN` secret is set
- Check token has write permissions
- Ensure dashboard repository exists and is accessible

## Monitoring

### Check Collection Status

```bash
# View logs
tail -f orchestrator.log

# Check summary
cat output/orchestrator_summary.json | jq '.'

# Verify per-package output
ls output/*-metrics/
```

### GitHub Actions Monitoring

1. Go to metrics repository → Actions tab
2. Click on latest "Collect Metrics and Sync to Dashboard" run
3. Review:
   - Step logs
   - Summary report
   - Artifacts (detailed reports)

## Best Practices

1. **Start Small:** Test with 1-2 packages before full run
2. **Use Dry Run:** Always test configuration changes with `--dry-run`
3. **Monitor Logs:** Check logs for API errors or failures
4. **Set Secrets:** Never commit API keys, use environment variables
5. **Rate Limiting:** Be respectful of API rate limits
6. **Regular Updates:** Weekly schedule is good balance between freshness and load
7. **Review Changes:** Check git diff before pushing to dashboard

## Next Steps

1. **Remaining Metrics:** Implement 4.2.5 Outreach, 4.3.1 Reliability, 4.3.4 Usability, 4.3.6 Maintainability
2. **Enhanced Scoring:** Refine overall score calculation and dimension weights
3. **Trend Analysis:** Track metrics over time
4. **Alerting:** Notify on significant metric changes
5. **Caching:** Cache API responses to reduce calls
6. **Parallelization:** Process packages in parallel for speed

## Support

For issues or questions:
- **Metrics Repo:** https://github.com/corsa-center/metrics/issues
- **Dashboard Repo:** https://github.com/corsa-center/dashboard/issues
