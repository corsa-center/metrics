# Metrics Catalog

Every metric the framework collects, organized by the section numbering of the
**CASS Sustainability Metrics Report v3**. Each section lists the sub-metrics
named by the report, whether the framework fills them today, and where the data
comes from.

The report defines three dimensions — **4.1 Impact**, **4.2 Sustainability**,
**4.3 Quality** — and 19 sections beneath them. Sub-metric names below are the
report's own; they are also the keys used by the `package_config/` override
files and by `SECTION_SUBMETRICS` in [`orchestrator.py`](orchestrator.py).

**Legend:** ✅ collected · 🔲 rendered as "Not yet collected" · ⬜ whole section
is a stub

---

## Coverage at a glance

| Section | Title | Filled |
|---|---|---|
| 4.1.1 | Software Citation and Adoption | 7/7 |
| 4.1.2 | Field Research Impact | ⬜ 0/3 |
| 4.2.1 | CoC, Governance, and Contributor Guidelines | 5/5 |
| 4.2.2 | Open-Source Licensing and FAIR Compliance | 5/5 |
| 4.2.3 | Active Maintenance | 4/6 |
| 4.2.4 | Engagement | 7/7 |
| 4.2.5 | Outreach | 5/8 |
| 4.2.6 | Welcomeness | 1/7 |
| 4.2.7 | Collaboration | 2/5 |
| 4.2.8 | Financial Sustainability | 4/5 |
| 4.2.9 | Institutional & Organizational Support | 1/5 |
| 4.2.10 | Project Longevity and Community Health | 5/5 |
| 4.3.1 | Reliability and Robustness | 2/5 |
| 4.3.2 | Development Practices | 5/5 |
| 4.3.3 | Reproducibility | 5/5 |
| 4.3.4 | Usability | 2/5 |
| 4.3.5 | Accessibility | 5/5 |
| 4.3.6 | Maintainability and Understandability | 5/5 |
| 4.3.7 | Performance and Efficiency | ⬜ 0/10 |

A stub section renders nothing unless the package's `package_config/` file
supplies overrides, in which case it renders those values against a 0/N score.

---

## 4.1 Impact

### 4.1.1 Software Citation and Adoption
**Collector:** [`collectors/impact/citation.py`](collectors/impact/citation.py)

This section renders its own labels rather than the report's five sub-metric
names, because the underlying sources return directly comparable counts.

| Rendered metric | Status | Source |
|---|---|---|
| Formal Citations | ✅ | Semantic Scholar, OpenAlex |
| Informal Mentions | ✅ | Semantic Scholar |
| Dependent Packages | ✅ | GitHub API |
| DOI Resolutions | ✅ | Zenodo |
| GitHub Stars | ✅ | GitHub API |
| GitHub Forks | ✅ | GitHub API |
| Citation Score | ✅ | computed |

### 4.1.2 Field Research Impact
**Collector:** none — ⬜ stub

AI-Enhanced Publication Analysis · Comprehensive Institutional Tracking ·
Impact Narrative Extraction. All three need LLM analysis of scientific
literature plus facility web scraping; see the "Hard" tier in
[METRICS_ROADMAP.md](METRICS_ROADMAP.md).

---

## 4.2 Sustainability

### 4.2.1 Codes of Conduct, Governance, and Contributor Guidelines
**Collectors:** [`community_health.py`](collectors/sustainability/community_health.py),
[`chaoss_governance.py`](collectors/sustainability/chaoss_governance.py),
[`openssf_badge.py`](collectors/sustainability/openssf_badge.py),
[`openssf_scorecard.py`](collectors/sustainability/openssf_scorecard.py)

| Sub-metric | Status | Source |
|---|---|---|
| Enhanced Document Detection | ✅ | CODE_OF_CONDUCT / GOVERNANCE / CONTRIBUTING file detection |
| Governance Keyword Analysis | ✅ | decision process / defined roles / membership lifecycle / conflict resolution, read from the full documents; passes at ≥2 |
| OpenSSF Badge Integration | ✅ | `bestpractices.dev`, level + percentage. **Substituted** by an *OpenSSF Scorecard* row (`api.securityscorecards.dev`, with a per-check breakdown of failing checks) whenever scorecard data exists, so the section is always 5 rows |
| CHAOSS Governance Metrics | ✅ | [`chaoss_governance.py`](collectors/sustainability/chaoss_governance.py) — weighted 0–100 health score, passing at ≥60, with a per-category breakdown (popularity, docs, time-to-close, issue age, PR closure ratio, release frequency, issue inclusivity) |
| Governance Effectiveness Assessment | ✅ | CODEOWNERS present **and** governance docs touched within three years |

> `community_health.py` is named for the report's older phrasing but is the
> **4.2.1** collector: it detects governance documents. It has nothing to do
> with 4.2.10.

**Document detection is case-insensitive.** GitHub's Contents API is
case-sensitive, so the old enumerated pattern list could only match spellings
somebody thought to write down. ADIOS2 names its guide `Contributing.md`, which
no list of upper/lower variants catches, and the file was invisible. The root,
`.github/` and `docs/` directories are now listed once and matched
case-insensitively — fewer requests as well as more hits.

### 4.2.2 Open-Source Licensing and FAIR Compliance
**Collector:** [`licensing.py`](collectors/sustainability/licensing.py)

| Sub-metric | Status | Source |
|---|---|---|
| Enhanced License Detection | ✅ | GitHub License API and SPDX identifier, falling back to the family named in the licence text |
| Automated FAIR4RS Assessment | ✅ | the four principles scored individually; passes at ≥3 |
| OSI License Validation | ✅ | SPDX / OSI approval list; a text-resolved family counts as approved |

**GitHub returns `NOASSERTION` for any licence it cannot match verbatim.** HDF5's
LICENSE states plainly that the software "is covered by the 3-clause BSD
License", but the extra copyright notices stop the classifier recognising it — so
the framework reported HDF5 as licence "Other", category "Unknown", OSI
"unknown". It now resolves to BSD-3-Clause, Permissive, OSI-approved. (HDF5 is
also being fixed at source so GitHub classifies it directly; the fallback stays
for every other project with a modified licence.)
| License Exception Handling | ✅ | license family recovered from the text when GitHub returns `NOASSERTION`, plus exception / extra-terms markers |
| FAIR Metadata Assessment | ✅ | CITATION.cff field completeness (title, authors, version, license, repository-code, DOI); passes at ≥4 |

### 4.2.3 Active Maintenance
**Collector:** [`active_maintenance.py`](collectors/sustainability/active_maintenance.py)

| Sub-metric | Status | Source |
|---|---|---|
| Commit Activity Pattern Analysis | ✅ | `/stats/participation`, 52-week series |
| Maintenance Mode Indicator Detection | ✅ | `archived` flag + description keywords |
| Activity Trend Monitoring | ✅ | last 13 weeks vs previous 13 |
| Release Pattern Assessment | ✅ | `/releases`, releases in last year |
| Multi-Channel Communication Activity | 🔲 | — |
| Contributor Abandonment Forecasting | 🔲 | — |

### 4.2.4 Engagement
**Collector:** [`engagement.py`](collectors/sustainability/engagement.py)

| Sub-metric | Status | Source |
|---|---|---|
| Response Time Tracking | ✅ | `/issues`, time to first response |
| Issue Resolution Analysis | ✅ | `/issues`, close rate |
| Pull Request Flow Assessment | ✅ | `/pulls`, median cycle time |
| Support Request Closure Analysis | ✅ | `/issues` |
| Engagement Quality Metrics | ✅ | median comments per issue; passes at ≥2 |
| Communication Pattern Analysis | ✅ | share of issues answered within a week; passes at ≥70% |
| Community Participation Assessment | ✅ | share of issues and PRs opened outside the maintainer group (`author_association`); passes at ≥15% |

**The issue sample was silently 4 items.** GitHub's `/issues` endpoint returns
pull requests too and offers no way to exclude them, so one page of 30 yielded
26 PRs and 4 real issues on HDF5 — every median in this section was computed
from those four. Pages of 100 are now pulled until 30 issues are in hand.
Fixing it moved HDF5's median first response from 301 hours to 26, its median
close time from 1463 to 928, and its backlog ratio from 3.00 to 2.00.

**Consistency is measured absolutely, not as a p90/median ratio.** The ratio is
scale-sensitive: a project that usually replies within minutes scores
thousands-to-one the moment one issue waits a fortnight, which says more about
the arithmetic than the project. ADIOS2 measured 3846×. The share answered
within a week separates the portfolio meaningfully instead — HDF5 43%,
ADIOS2 53%, zfp 93%.

### 4.2.5 Outreach
**Collector:** [`outreach.py`](collectors/sustainability/outreach.py)

| Sub-metric | Status | Source |
|---|---|---|
| New Contributor Tracking | ✅ | contributors whose all-time count is fully inside the last 365 days |
| Contributor Retention Analysis | ✅ | share of those newcomers with ≥2 commits; passes at ≥50% |
| Contributor Lifecycle Mapping | ✅ | one-time (1) / casual (2–4) / repeat (5+) buckets from `/contributors` |
| Contribution Type Diversity | 🔲 | non-code contributions aren't recorded in the repo |
| Good First Issue Effectiveness | ✅ | search API counts for `good first issue`, `help wanted`, `newcomer`; passes on ≥1 **open** |
| External Event Participation | 🔲 | needs conference programmes |
| Training Material Integration | 🔲 | needs course syllabi |
| Onboarding Infrastructure Assessment | ✅ | CONTRIBUTING, issue/PR templates, getting-started guide; passes at 3/4 |

> "New" is inferred by comparing each author's all-time contribution count
> against their commits in the window, rather than walking the whole log to find
> each first commit. Cheap and accurate for normal repos; it misses anyone whose
> recent commits exceed the 10-page pagination cap.

### 4.2.6 Welcomeness
**Collector:** [`welcomeness.py`](collectors/sustainability/welcomeness.py)

| Sub-metric | Status | Source |
|---|---|---|
| CHAOSS Community Experience Metrics | 🔲 | — |
| Response Quality and Tone Analysis | 🔲 | needs NL analysis of conversations |
| Communication Sentiment Analysis | 🔲 | needs NL analysis of conversations |
| Contributor Journey Mapping | 🔲 | — |
| Language and Communication Review | 🔲 | needs NL analysis of documentation |
| Leadership Role Representation | 🔲 | needs maintainer demographics |
| Decision-Making Visibility | ✅ | `has_discussions` / `has_wiki` / `has_pages` plus roadmap, meeting notes, decision records, governance doc; passes at ≥2 signals |

### 4.2.7 Collaboration
**Collector:** [`collaboration.py`](collectors/sustainability/collaboration.py)

| Sub-metric | Status | Source |
|---|---|---|
| Advanced Dependency Analysis | ✅ | distinct package ecosystems carrying the software; passes at ≥2 |
| Cross-project Reference Detection | 🔲 | the report specifies AI analysis of issues and PRs |
| Interoperability Assessment | 🔲 | needs domain-specific standards knowledge |
| Collaboration Network Analysis | ✅ | downstream dependents; passes at ≥10 dependent packages **or** ≥50 dependent repositories |
| Standards Compliance Tracking | 🔲 | needs domain-specific standards knowledge |

Data comes from the free, unauthenticated **ecosyste.ms** APIs, looked up by
**repository URL** rather than by guessing a package name — HDF5's PyPI
presence is `h5py`, a different project entirely.

Spack is additionally looked up by name, because Spack recipes usually record
the project's own homepage as their repository URL rather than the GitHub repo.
HDF5's Spack entry points at `support.hdfgroup.org`, so the repository-URL
lookup alone misses the single most relevant package manager for this portfolio
— and with it HDF5's 161 Spack dependents.

Duplicate entries for one package are collapsed keeping the highest count:
conda-forge and anaconda.org both index `hdf5`, and summing would double-count.

Either downstream signal passes on its own. A library can be depended on by many
packages (HDF5: 176 conda packages) or by many repositories (zfp: 111 repos from
only 9 packages); both are real evidence of ecosystem integration.

### 4.2.8 Financial Sustainability
**Collector:** [`funding.py`](collectors/sustainability/funding.py)

| Sub-metric | Status | Source |
|---|---|---|
| Enhanced Funding Documentation Analysis | ✅ | FUNDING.yml / funding.json, plus DOE/NSF/NIH award numbers in the README |
| Institutional Affiliation Tracking | ✅ | `company` field of the top 25 contributors; passes at ≥3 distinct orgs |
| NIH R50 Award Tracking | 🔲 | NIH RePORTER API is public and unauthenticated — a Tier 2 win, not yet wired |
| Corporate Sponsorship Detection | ✅ | declared funding platforms + organization-owned repository |
| Funding Portfolio Analysis | ✅ | count of distinct platforms + award references; passes at ≥2 |

> Contributor affiliations are folded onto a canonical key, so "The HDF Group",
> "HDFGroup" and "The HDFgroup" count as one organization. Without that the
> free-text `company` field inflates the diversity figure — HDF5 read as 5
> organizations instead of its actual 3.

### 4.2.9 Institutional & Organizational Support
**Collector:** [`funding.py`](collectors/sustainability/funding.py) — shares
4.2.8's contributor-affiliation pass rather than fetching it twice.

| Sub-metric | Status | Source |
|---|---|---|
| RSE Position Detection | 🔲 | needs LinkedIn / institutional directories |
| Institutional Support Tracking | ✅ | distinct organizations backing the top contributors |
| Career Development Indicators | 🔲 | not visible from the repository |
| NIH R50 Award Integration | 🔲 | NIH RePORTER, as for 4.2.8 |
| Institutional Policy Analysis | 🔲 | not visible from the repository |

### 4.2.10 Project Longevity and Community Health
**Collector:** none of its own — derived in `_transform_for_dashboard`
([`orchestrator.py`](orchestrator.py)) from data `active_maintenance.py`
already fetched for 4.2.3. Costs **zero additional API calls**.

| Sub-metric | Status | Derivation |
|---|---|---|
| Comprehensive Activity Analysis | ✅ | commits, releases and sustained activity as 3 dimensions; ≥2 passes |
| Contributor Viability Assessment | ✅ | bus factor ≥ 3 (same threshold as 4.2.3 and 4.3.6) |
| Maintenance Mode Detection | ✅ | archived flag, description keywords, >365 days without a push |
| Community Health Trends | ✅ | 52-week commit trend (increasing / stable passes) |
| Project Lifecycle Assessment | ✅ | project age × current release activity → Emerging / Growing / Mature / Legacy / Retired |

**Project age reports both dates**, because for a migrated project they differ
by decades and neither alone is honest:

- **First commit** — the age of the code history. Found via the `rel="last"`
  Link header on `/commits?per_page=1`, so it costs two requests regardless of
  history size. Reset by a history rewrite.
- **GitHub repository creation** — `created_at`. Understates any project that
  moved to GitHub from another VCS.

Lifecycle staging uses the longer of the two. HDF5 renders as
`first commit 1997-07-30 (29.1 yrs); GitHub repo created 2020-04-24 (6.3 yrs)`
— a 23-year gap. A repo created empty has its first commit land *after*
creation (zfp, by one day), so the repo date is sometimes the longer span.

---

## 4.3 Quality

### 4.3.1 Reliability and Robustness
**Collectors:** [`test_coverage.py`](collectors/quality/test_coverage.py),
[`static_analysis.py`](collectors/quality/static_analysis.py)

| Sub-metric | Status | Source |
|---|---|---|
| Advanced Static Analysis | 🔲 | — |
| Enhanced Security Analysis | ✅ | CodeQL workflow presence |
| CERT Guidelines Compliance | 🔲 | — |
| Test Coverage Excellence | ✅ | Codecov v2 public API; passes at ≥80% |
| Reliability Trend Analysis | 🔲 | — |

Codecov's `api.codecov.io/api/v2/github/{owner}/repos/{repo}/` is public and
unauthenticated for public repos. Repos with no active Codecov integration
render "No Codecov data found" rather than a number. Coveralls was evaluated
and rejected — its public JSON endpoint returns HTTP 403 to non-browser clients.

### 4.3.2 Development Practices
**Collectors:** [`ci_cd.py`](collectors/quality/development_practices/ci_cd.py),
[`dev_tooling.py`](collectors/quality/development_practices/dev_tooling.py),
[`openssf_badge.py`](collectors/sustainability/openssf_badge.py)

| Sub-metric | Status | Source |
|---|---|---|
| CI/CD Effectiveness Assessment | ✅ | `.github/workflows/` parsing + run status |
| Testing Framework Excellence | ✅ | test directories, CTest, pytest config, vendored frameworks; passes at 2/4 |
| Code Review Quality Analysis | ✅ | share of the last 50 merged PRs with ≥1 review; passes at ≥70% |
| Development Tool Integration | ✅ | pre-commit, formatter, linter, Dependabot/Renovate configs; passes at 2/4 |
| Community Contribution Facilitation | ✅ | OpenSSF Best Practices badge as proxy |

### 4.3.3 Reproducibility
**Collector:** [`reproducibility.py`](collectors/quality/reproducibility.py)

| Sub-metric | Status | Source |
|---|---|---|
| FAIR4RS Compliance Assessment | ✅ | CITATION.cff, codemeta.json, `.zenodo.json` |
| Containerization Excellence | ✅ | Dockerfile, Singularity/Apptainer definitions |
| Version Control Best Practices | ✅ | semantic versioning in `/tags` |
| Environment Management | ✅ | dependency pinning: `requirements.txt`, `poetry.lock`, `conda-lock.yml`, `package-lock.json`, `Cargo.lock`, `uv.lock` |
| Reproducibility Documentation | ✅ | install/build guide, release notes, environment spec (`environment.yml`, `spack.yaml`, devcontainer) |

### 4.3.4 Usability
**Collectors:** [`usability.py`](collectors/quality/usability.py),
[`collaboration.py`](collectors/sustainability/collaboration.py)

| Sub-metric | Status | Source |
|---|---|---|
| User Experience Assessment | 🔲 | the report specifies the UEQ instrument, which needs a survey |
| Documentation Completeness Analysis | ✅ | README headings (installation / usage / examples / support), `docs/` tree, published documentation site |
| Accessibility Feature Detection | 🔲 | — |
| Installation Success Tracking | ✅ | distinct package managers with an install command, from 4.2.7's registry data |
| Usage Analytics Integration | 🔲 | — |

README sections are matched against **heading text only**, so "you can install
it somehow" in a paragraph doesn't count as an installation section.

A thin README still passes when it is backed by both a `docs/` tree and a
published site — HDF5's README covers 2 of 4 sections but its real
documentation lives elsewhere.

Installation Success reuses the 4.2.7 registry lookup rather than querying
ecosyste.ms a second time for the same answer.

### 4.3.5 Accessibility
**Collectors:** [`accessibility.py`](collectors/quality/accessibility.py),
[`deployment_environments.py`](collectors/quality/deployment_environments.py)

| Sub-metric | Status | Source |
|---|---|---|
| Portable Build System Detection | ✅ | CMake, Autotools, Meson, Spack recipe, Conda |
| Container Availability Assessment | ✅ | Dockerfile, Singularity/Apptainer |
| Architecture Compatibility Analysis | ✅ | non-x86 CPU architectures in the CI workflows (ARM64, POWER, RISC-V, s390x); x86-64 alone doesn't count |
| Platform Documentation Evaluation | ✅ | platform families named in the README; passes at ≥2 |
| Deployment Environment Testing | ✅ | [`deployment_environments.py`](collectors/quality/deployment_environments.py) — CI runner labels folded to OS families; passes at ≥2 |

### 4.3.6 Maintainability and Understandability
**Collector:** [`maintainability.py`](collectors/quality/maintainability.py),
plus the bus factor reused from `active_maintenance.py`.

| Sub-metric | Status | Source |
|---|---|---|
| Advanced Complexity Analysis | ✅ | source-file size distribution and tree depth; passes under 5% of files over 100 KB and depth ≤10 |
| Code Quality Assessment | ✅ | test-to-source file ratio; passes at ≥0.20 |
| Documentation Quality Evaluation | ✅ | documentation coverage plus a doc generator (Doxygen / Sphinx / MkDocs) |
| Knowledge Distribution Analysis | ✅ | bus factor ≥ 3, plus top-contributor share |
| Refactoring and Evolution Tracking | ✅ | refactor-intent commits over a 300-commit sample; passes at ≥2% |

The whole section comes from three calls: the recursive git tree, the language
breakdown, and recent commit messages.

**Complexity is a structural proxy, not static analysis.** The report asks for
tools that measure computational complexity; that needs the source checked out.
What this reports is file size and nesting, and says so in the rendered row.

**Two counting rules that materially change the numbers.** Files under a `docs/`
tree only count as documentation if they are prose — HDF5's docs tree holds 165
`.gif`, 82 `.png` and 33 `.c` files, which had doubled its documentation figure.
And `.txt` counts only at the repo root or under a doc directory, since deeper in
the tree it is almost always test fixtures.

**Churn is measured from commit subjects, not `/stats/code_frequency`.** That
endpoint returns HTTP 422 for any repository over 10,000 commits, which rules out
most of this portfolio — HDF5 alone has ~24,500. Sampling 300 commits rather than
100 matters too: at 100, each commit is worth a full percentage point and a 2%
threshold is indistinguishable from noise.

### 4.3.7 Performance and Efficiency
**Collector:** none — ⬜ stub

Performance Benchmarking Integration · Environmental Impact Assessment ·
Resource Utilization Analysis · Scalability Assessment · Optimization Practice
Evaluation · Memory Efficiency Analysis · I/O Performance Profiling ·
Algorithmic Complexity Assessment · Power Measurement Integration ·
Performance Portability Assessment.

All ten require running benchmarks on target hardware, profiling
(Valgrind/Darshan/RAPL/NVML), or domain expertise to interpret.

---

## Scoring

Each section reports `Score: n/N`, where `N` is the number of sub-metrics the
report defines for it and `n` is how many currently pass. Section scores roll up
into three dimension scores, which combine into an overall score using the
weights in [`config/orchestrator.yaml`](config/orchestrator.yaml):

```yaml
metric_weights:
  impact: 0.33
  sustainability: 0.34
  quality: 0.33
```

Sections with no collector contribute 0 and are excluded from their dimension's
average rather than dragging it down.

---

## Configuration

Collectors are toggled per **dimension** in
[`config/orchestrator.yaml`](config/orchestrator.yaml):

```yaml
collectors:
  impact: true          # 4.1
  sustainability: true  # 4.2
  quality: true         # 4.3
```

Individual sub-collectors are toggled within a dimension:

```yaml
sustainability_collectors:
  community_health: true    # 4.2.1 governance docs
  chaoss_activity: true     # 4.2.1 CHAOSS Governance Metrics
  openssf_scorecard: true   # 4.2.1 OpenSSF Scorecard
  openssf_badge: true       # 4.2.1 + 4.3.2
  licensing: true           # 4.2.2
  active_maintenance: true  # 4.2.3, and 4.2.10 + 4.3.6 derive from it
  engagement: true          # 4.2.4
  outreach: true            # 4.2.5
  welcomeness: true         # 4.2.6
  collaboration: true       # 4.2.7 + 4.3.4 install paths
  funding: true             # 4.2.8 + 4.2.9

quality_collectors:
  test_coverage: true       # 4.3.1
  static_analysis: true     # 4.3.1
  ci_cd: true               # 4.3.2
  dev_tooling: true         # 4.3.2
  reproducibility: true     # 4.3.3
  usability: true           # 4.3.4
  maintainability: true     # 4.3.6
  accessibility: true       # 4.3.5
  deployment_environments: true  # 4.3.5
```

Omitted keys default to `true`, so deleting a block runs everything. Disabling
a sub-collector skips its API calls and drops it from the dimension average
rather than scoring it zero; the sections it feeds render "Not yet collected".
Turning off `active_maintenance` also empties 4.2.10 and 4.3.6, which are
derived from it.

Per-package overrides for sub-metrics that are genuinely N/A live in
`package_config/<owner>_<repo>.yaml`; keys are the exact sub-metric labels from
this catalog. See [PLACEHOLDER_GUIDE.md](PLACEHOLDER_GUIDE.md).

---

## API sources

| Service | Auth | Used for |
|---|---|---|
| GitHub REST API | token | most sections |
| Semantic Scholar | optional key | 4.1.1 citations, mentions |
| OpenAlex | polite-pool email | 4.1.1 citations |
| Zenodo | none | 4.1.1 DOI resolutions |
| OpenSSF Scorecard | none | 4.2.1 |
| OpenSSF Best Practices | none | 4.2.1, 4.3.2 |
| Codecov v2 | none | 4.3.1 test coverage |

---

## Contact

- CORSA Dashboard: info@corsa.center
- Issues: [corsa-center/metrics](https://github.com/corsa-center/metrics/issues)
