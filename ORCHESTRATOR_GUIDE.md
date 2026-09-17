# Orchestrator Guide: Coordinating Metrics and Dashboard

## Overview

The **orchestrator** is the coordination program that:
1. Reads the software catalog from the dashboard repository
2. Collects metrics for each software package (citations, community, licensing)
3. Transforms data to dashboard format
4. Exports to the dashboard repository

This eliminates manual coordination between the metrics and dashboard repositories.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    ORCHESTRATOR                         │
│          (orchestrator.py + config.yaml)                │
└────────────┬────────────────────────────┬───────────────┘
             │                            │
             ↓                            ↓
   ┌─────────────────┐          ┌─────────────────┐
   │ Metrics Repo    │          │ Dashboard Repo  │
   │ - Collectors    │          │ - Catalog       │
   │ - APIs          │←─reads───│ - Categories    │
   │ - Processing    │          │                 │
   └────────┬────────┘          └─────────┬───────┘
            │                              ↑
            └──────────writes──────────────┘
                 ecosystemMetrics.json
```

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

Edit `config/orchestrator.yaml`:

```yaml
# Set repository paths
dashboard_repo_path: "../dashboard"

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
```

### 3. Set Environment Variables

```bash
export GITHUB_TOKEN="your_github_token"
export SEMANTIC_SCHOLAR_KEY="your_ss_key"  # Optional
export OPENALEX_EMAIL="your@email.com"     # Optional but recommended
```

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

### 1. Dashboard Output
**Location:** `../dashboard/explore/github-data/ecosystemMetrics.json`

Format:
```json
{
  "HDFGroup/hdf5": {
    "overall_score": 75,
    "impact_metrics": { ... },
    "community_metrics": { ... },
    "licensing_metrics": { ... },
    "last_updated": "2025-10-10T21:00:00Z"
  }
}
```

### 2. Summary Report
**Location:** `./output/orchestrator_summary.json`

Contains:
- Total packages processed
- Average scores
- Score distribution
- Top performing packages

### 3. Log File
**Location:** `./orchestrator.log`

Detailed logs of the collection process

## Automation via GitHub Actions

The workflow `.github/workflows/collect-and-sync.yml` automates the orchestrator:

### Schedule
- Runs every Sunday at 00:00 UTC
- Automatically pushes updates to dashboard

### Manual Trigger
Go to GitHub Actions → "Collect Metrics and Sync to Dashboard" → Run workflow

Options:
- **Software Filter:** Run for specific software only
- **Dry Run:** Test without pushing changes

### Workflow Steps

1. **Checkout** both repositories
2. **Install** Python dependencies
3. **Run orchestrator** with configured collectors
4. **Commit changes** to dashboard repository
5. **Upload artifacts** (reports, logs)
6. **Create summary** in GitHub Actions UI

## Configuration Options

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
governed by a value in [`config/thresholds.yaml`](config/thresholds.yaml) --
that file **is** the full, current list of every changeable threshold and
its default, organized by CASS report section number and the exact
sub-metric label, with an inline comment explaining each one.
([METRICS_CATALOG.md](METRICS_CATALOG.md)'s "Meets threshold when" column
shows the same values in prose, for cross-reference against the report's
own language.)

To change a default without editing collector code, copy the relevant
section/label(/param) structure from `config/thresholds.yaml` into a
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
  `config/thresholds.yaml` doesn't already declare a default for raises an
  error when the orchestrator starts, rather than being quietly ignored.
- **Partial overrides merge.** For a sub-metric with several named params
  (like `CI/CD Effectiveness Assessment` above), overriding one param
  leaves its siblings at their default -- no need to repeat values you're
  not changing.
- **Not every sub-metric is configurable.** A pure presence/structural
  check (e.g. "a license was identified at all") has no entry in
  `config/thresholds.yaml` and can't be overridden here -- see that file's
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

### Example 1: Weekly Full Update

```bash
# Every Sunday, GitHub Actions runs:
python orchestrator.py --config config/orchestrator.yaml

# Results:
# - All CASS software metrics collected
# - Dashboard updated automatically
# - Summary report generated
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
**Solution:** Check `dashboard_repo_path` in config points to correct location

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

# Verify dashboard output
ls -lh ../dashboard/explore/github-data/ecosystemMetrics.json
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
