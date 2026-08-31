# Metrics Implementation Roadmap

Grouped by ease of data collection, based on
[corsa-center/metrics issues](https://github.com/corsa-center/metrics/issues)
and the CASS Sustainability Metrics Report v3.

---

## Infrastructure (prerequisite, not metric-specific)

| Issue | Title | Status |
|-------|-------|--------|
| [#1](https://github.com/corsa-center/metrics/issues/1) | Phase 1: Infrastructure & Setup | ✅ Done |
| [#2](https://github.com/corsa-center/metrics/issues/2) | Software Package Data Ingestion | ✅ Done |
| [#3](https://github.com/corsa-center/metrics/issues/3) | GitHub/GitLab API Integration Framework | ✅ Done |
| [#24](https://github.com/corsa-center/metrics/issues/24) | Metrics of interest file | 🔲 Todo |

---

## Easy — Direct GitHub/Repository API Queries

File presence checks or structured data returned directly by GitHub/GitLab APIs.
No significant post-processing required.

| Issue | Metric | Collector | Status |
|-------|--------|-----------|--------|
| [#8](https://github.com/corsa-center/metrics/issues/8) | 4.2.1 CoC, Governance & Contributor Guidelines | `collectors/sustainability/community_health.py` + `chaoss_governance.py` | ✅ Done (partial — docs, CHAOSS, OpenSSF badge/scorecard; keyword & effectiveness analysis TBD) |
| [#9](https://github.com/corsa-center/metrics/issues/9) | 4.2.2 Open-Source Licensing & FAIR Compliance | `collectors/sustainability/licensing.py` | ✅ Done |
| [#10](https://github.com/corsa-center/metrics/issues/10) | 4.2.3 Active Maintenance | `collectors/sustainability/active_maintenance.py` | ✅ Done |
| [#16](https://github.com/corsa-center/metrics/issues/16) | 4.2.10 Project Longevity & Community Health | `orchestrator.py` (derived from `active_maintenance.py`) | ✅ Done |
| [#18](https://github.com/corsa-center/metrics/issues/18) | 4.3.2 Development Practices | `ci_cd.py` + `dev_tooling.py` | ✅ Done (5/5) |
| [#21](https://github.com/corsa-center/metrics/issues/21) | 4.3.5 Accessibility | `accessibility.py` + `deployment_environments.py` | ✅ Done (5/5) |
| — | **OpenSSF Best Practices Badge** (Quality) | `collectors/sustainability/openssf_badge.py` | ✅ Done |
| — | **OpenSSF Scorecard** (Sustainability) | `collectors/sustainability/openssf_scorecard.py` | ✅ Done |
| — | **CI / GitHub Actions Status** (Quality) | covered by `ci_cd.py` | ✅ Done |
| — | **Test Coverage %** (Quality) | `collectors/quality/test_coverage.py` | ✅ Done (Codecov only — see note) |

### Why prioritised

- All data is a single API call or file-existence check — no ML, no scraping, no
  domain expertise required.
- **4.2.10** has no collector of its own. All five of its sub-metrics are
  re-derived in `_transform_for_dashboard` from data `ActiveMaintenanceCollector`
  already fetches for 4.2.3, so the section costs zero additional API calls.
  Note that `community_health.py`, despite its name, is the **4.2.1** collector
  (Code of Conduct / GOVERNANCE / CONTRIBUTING file detection) and is not
  involved in 4.2.10.
- The three recommended new metrics are the lightest of all: each returns a
  pre-computed score from a free public API.
  - **OpenSSF Scorecard**: 3 projects (ADIOS, Viskores, PnetCDF) already report
    real scores; free API at `api.securityscorecards.dev`.
  - **OpenSSF Best Practices Badge**: 4 projects track it; ADIOS and HDF5 both
    achieved "Passing" this quarter and cite it as their #1 quality metric.
  - **CI / GitHub Actions Status**: 4 projects explicitly cite their CI pass
    rate; GitHub API auth already wired in.
  - **Test Coverage %**: originally assessed as "Hard" (see below) on the
    assumption that CodeCov/Coveralls need auth. Re-tested during
    implementation — Codecov's `v2` API (`api.codecov.io/api/v2/github/{owner}/repos/{repo}/`)
    is public and unauthenticated for public repos and returns real coverage
    totals (confirmed live: zfp 94.8%, DeepHyper 52.4%). Coveralls' public
    JSON endpoint returns HTTP 403 for non-browser requests, so only Codecov
    is used; repos without an active Codecov integration report "No Codecov
    data found" rather than a number.

---

## Moderate — GitHub API + Analysis/Processing

Data is available via APIs but requires aggregation, timestamp arithmetic,
static analysis tool runs, or non-trivial content parsing.

| Issue | Metric | Collector | Status |
|-------|--------|-----------|--------|
| [#6](https://github.com/corsa-center/metrics/issues/6) | 4.1.1 Software Citation & Adoption | `collectors/impact/citation.py` | ✅ Done (partial — CITATION.cff/DOI; advanced deps TBD) |
| [#11](https://github.com/corsa-center/metrics/issues/11) | 4.2.4 Engagement | `collectors/sustainability/engagement.py` | ✅ Done (7/7) |
| [#12](https://github.com/corsa-center/metrics/issues/12) | 4.2.5 Outreach | `collectors/sustainability/outreach.py` | ✅ Done (partial — 5/8; event & training data not in the repo) |
| [#17](https://github.com/corsa-center/metrics/issues/17) | 4.3.1 Reliability & Robustness | `collectors/quality/test_coverage.py` | ✅ Done (partial — Test Coverage Excellence via Codecov; static analysis/CERT/trend TBD) |
| [#19](https://github.com/corsa-center/metrics/issues/19) | 4.3.3 Reproducibility | `collectors/quality/reproducibility.py` | ✅ Done (5/5) |
| [#20](https://github.com/corsa-center/metrics/issues/20) | 4.3.4 Usability | `collectors/quality/usability.py` | ✅ Done (partial — 2/5; UEQ needs a survey) |
| [#22](https://github.com/corsa-center/metrics/issues/22) | 4.3.6 Maintainability & Understandability | `collectors/quality/maintainability.py` | ✅ Done (5/5) |

---

## Hard — External Data, AI/NLP, or Manual Assessment Required

Data is not available from repository APIs alone; requires external services,
ML models, specialized runtime instrumentation, or qualitative judgment.

| Issue | Metric | Collector | Status |
|-------|--------|-----------|--------|
| [#7](https://github.com/corsa-center/metrics/issues/7) | 4.1.2 Field Research Impact | — | 🔲 Todo |
| [#13](https://github.com/corsa-center/metrics/issues/13) | 4.2.7 Collaboration | `collectors/sustainability/collaboration.py` | ✅ Done (partial — 2/5 via ecosyste.ms) |
| [#14](https://github.com/corsa-center/metrics/issues/14) | 4.2.8 Financial Sustainability | `collectors/sustainability/funding.py` | ✅ Done (partial — 4/5; NIH R50 via RePORTER still TBD) |
| [#15](https://github.com/corsa-center/metrics/issues/15) | 4.2.9 Institutional & Organizational Support | `collectors/sustainability/funding.py` | ✅ Done (partial — 1/5; RSE/policy detection needs directory data) |
| [#23](https://github.com/corsa-center/metrics/issues/23) | 4.3.7 Performance & Efficiency | — | 🔲 Todo |

### Why hard

- **4.1.2 Field Research Impact**: LLM-powered analysis of scientific literature
  + HPC facility web scraping + DOI cross-referencing.
- **4.2.7 Collaboration**: dependency mapping shipped via ecosyste.ms. What
  stays hard is cross-project PR/issue network analysis, which the report itself
  specifies as AI-powered.
- **4.2.8 / 4.2.9**: the automatable parts (funding manifests, README award
  numbers, contributor affiliations) shipped in `funding.py`. What stays hard is
  funding *amounts*, which are often confidential, and RSE position detection,
  which needs LinkedIn or institutional directory data.
- **4.3.7 Performance & Efficiency**: Requires running benchmarks on target
  hardware, GPU/CPU profiling (RAPL, NVML), and domain expertise to interpret.
