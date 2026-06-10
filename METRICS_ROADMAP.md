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
| [#8](https://github.com/corsa-center/metrics/issues/8) | 4.2.1 CoC, Governance & Contributor Guidelines | `collectors/sustainability/chaoss_governance.py` | ✅ Done |
| [#9](https://github.com/corsa-center/metrics/issues/9) | 4.2.2 Open-Source Licensing & FAIR Compliance | `collectors/sustainability/licensing.py` | ✅ Done |
| [#10](https://github.com/corsa-center/metrics/issues/10) | 4.2.3 Active Maintenance | `collectors/sustainability/active_maintenance.py` | ✅ Done |
| [#16](https://github.com/corsa-center/metrics/issues/16) | 4.2.10 Project Longevity & Community Health | `collectors/sustainability/community_health.py` | ✅ Done |
| [#18](https://github.com/corsa-center/metrics/issues/18) | 4.3.2 Development Practices (CI/CD) | `collectors/quality/development_practices/ci_cd.py` | ✅ Done |
| [#21](https://github.com/corsa-center/metrics/issues/21) | 4.3.5 Accessibility (portable build systems) | `collectors/quality/accessibility.py` | ✅ Done |
| — | **OpenSSF Best Practices Badge** (Quality) | `collectors/sustainability/openssf_badge.py` | ✅ Done |
| — | **OpenSSF Scorecard** (Sustainability) | `collectors/sustainability/openssf_scorecard.py` | ✅ Done |
| — | **CI / GitHub Actions Status** (Quality) | covered by `ci_cd.py` | ✅ Done |

### Why prioritised

- All data is a single API call or file-existence check — no ML, no scraping, no
  domain expertise required.
- The three recommended new metrics are the lightest of all: each returns a
  pre-computed score from a free public API.
  - **OpenSSF Scorecard**: 3 projects (ADIOS, Viskores, PnetCDF) already report
    real scores; free API at `api.securityscorecards.dev`.
  - **OpenSSF Best Practices Badge**: 4 projects track it; ADIOS and HDF5 both
    achieved "Passing" this quarter and cite it as their #1 quality metric.
  - **CI / GitHub Actions Status**: 4 projects explicitly cite their CI pass
    rate; GitHub API auth already wired in.

---

## Moderate — GitHub API + Analysis/Processing

Data is available via APIs but requires aggregation, timestamp arithmetic,
static analysis tool runs, or non-trivial content parsing.

| Issue | Metric | Collector | Status |
|-------|--------|-----------|--------|
| [#6](https://github.com/corsa-center/metrics/issues/6) | 4.1.1 Software Citation & Adoption | `collectors/impact/citation.py` | ✅ Done (partial — CITATION.cff/DOI; advanced deps TBD) |
| [#11](https://github.com/corsa-center/metrics/issues/11) | 4.2.4 Engagement | `collectors/sustainability/engagement.py` | ✅ Done |
| [#12](https://github.com/corsa-center/metrics/issues/12) | 4.2.5 Outreach | — | 🔲 Todo |
| [#17](https://github.com/corsa-center/metrics/issues/17) | 4.3.1 Reliability & Robustness | — | 🔲 Todo |
| [#19](https://github.com/corsa-center/metrics/issues/19) | 4.3.3 Reproducibility | `collectors/quality/reproducibility.py` | ✅ Done |
| [#20](https://github.com/corsa-center/metrics/issues/20) | 4.3.4 Usability | — | 🔲 Todo |
| [#22](https://github.com/corsa-center/metrics/issues/22) | 4.3.6 Maintainability & Understandability | — | 🔲 Todo |

---

## Hard — External Data, AI/NLP, or Manual Assessment Required

Data is not available from repository APIs alone; requires external services,
ML models, specialized runtime instrumentation, or qualitative judgment.

| Issue | Metric | Collector | Status |
|-------|--------|-----------|--------|
| [#7](https://github.com/corsa-center/metrics/issues/7) | 4.1.2 Field Research Impact | — | 🔲 Todo |
| [#13](https://github.com/corsa-center/metrics/issues/13) | 4.2.7 Collaboration | — | 🔲 Todo |
| [#14](https://github.com/corsa-center/metrics/issues/14) | 4.2.8 Financial Sustainability | — | 🔲 Todo |
| [#15](https://github.com/corsa-center/metrics/issues/15) | 4.2.9 Institutional & Organizational Support | — | 🔲 Todo |
| [#23](https://github.com/corsa-center/metrics/issues/23) | 4.3.7 Performance & Efficiency | — | 🔲 Todo |

### Why hard

- **4.1.2 Field Research Impact**: LLM-powered analysis of scientific literature
  + HPC facility web scraping + DOI cross-referencing.
- **4.2.7 Collaboration**: Multi-platform dependency mapping and cross-project
  PR/issue network analysis.
- **4.2.8 Financial Sustainability**: Funding amounts are often confidential;
  requires `FUNDING.yml` parsing, NIH award DB lookups, and manual research.
- **4.2.9 Institutional Support**: RSE position detection requires LinkedIn API
  and institutional directory scraping; cannot be reliably automated.
- **4.3.7 Performance & Efficiency**: Requires running benchmarks on target
  hardware, GPU/CPU profiling (RAPL, NVML), and domain expertise to interpret.
