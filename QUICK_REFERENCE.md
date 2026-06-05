# Quick Reference Guide

## Implemented Collectors

| Collector | Metric | File |
|-----------|--------|------|
| Citation | 4.1.1 Software Citation & Adoption | `collectors/impact/citation.py` |
| CoC / Governance | 4.2.1 CoC, Governance & Contributor Guidelines | `collectors/sustainability/chaoss_governance.py` |
| Licensing | 4.2.2 Open-Source Licensing & FAIR Compliance | `collectors/sustainability/licensing.py` |
| Active Maintenance | 4.2.3 Active Maintenance | `collectors/sustainability/active_maintenance.py` |
| Engagement | 4.2.4 Community Engagement | `collectors/sustainability/engagement.py` |
| Community Health | 4.2.10 Project Longevity & Community Health | `collectors/sustainability/community_health.py` |
| OpenSSF Badge | OpenSSF Best Practices Badge | `collectors/sustainability/openssf_badge.py` |
| OpenSSF Scorecard | OpenSSF Scorecard | `collectors/sustainability/openssf_scorecard.py` |
| CI/CD | 4.3.2 Development Practices (CI/CD) | `collectors/quality/development_practices/ci_cd.py` |
| Reproducibility | 4.3.3 Reproducibility | `collectors/quality/reproducibility.py` |
| Accessibility | 4.3.5 Accessibility (portable build systems) | `collectors/quality/accessibility.py` |

---

## Quick Commands

### Run All Collectors (via Orchestrator)
```bash
cd ~/work/metrics
source venv/bin/activate
python orchestrator.py --config config/orchestrator.yaml
```

### Run for a Single Package
```bash
python orchestrator.py --config config/orchestrator.yaml --software HDF5
```

### Dry Run (no file writes)
```bash
python orchestrator.py --config config/orchestrator.yaml --dry-run
```

### Generate Citation Metrics Only
```bash
export GITHUB_TOKEN="your_github_token"
python scripts/generate_corsa_citations.py \
  --catalog config/software_catalog.yaml \
  --output output/citationMetrics.json
```

---

## Environment Variables

| Variable | Purpose | Required? |
|----------|---------|-----------|
| `GITHUB_TOKEN` | GitHub API — raises limit from 60 to 5,000 req/hr | Strongly recommended |
| `SEMANTIC_SCHOLAR_KEY` | Higher rate limits for citation lookups | Optional |
| `OPENALEX_EMAIL` | Polite-pool access for OpenAlex | Optional |
| `ZENODO_TOKEN` | Zenodo DOI resolution | Optional |

---

## Output Files

| File | Description |
|------|-------------|
| `output/sustainabilityMetrics.json` | Dashboard-ready metrics JSON |
| `output/orchestrator_summary.json` | Run summary (scores, counts, top packages) |
| `orchestrator.log` | Detailed collection log |

---

## Documentation

| File | Purpose |
|------|---------|
| [README.md](README.md) | Project overview and setup |
| [CONFIGURATION.md](CONFIGURATION.md) | API credentials and config details |
| [ORCHESTRATOR_GUIDE.md](ORCHESTRATOR_GUIDE.md) | Orchestrator usage and options |
| [QUICK_START.md](QUICK_START.md) | Getting started quickly |
| [METRICS_ROADMAP.md](METRICS_ROADMAP.md) | Implementation status of all metrics |
