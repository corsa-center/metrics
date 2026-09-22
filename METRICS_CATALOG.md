# Metrics Catalog

Every metric the framework collects, organized by the section numbering of the
**CASS Sustainability Metrics Report v3**. Each section lists the sub-metrics
named by the report, whether the framework fills them today, and where the data comes from.

The report defines three dimensions — **4.1 Impact**, **4.2 Sustainability**,
**4.3 Quality** — and 19 sections beneath them. Sub-metric names below are the
report's own; they are also the keys used by the `package_config/` override
files and by `SECTION_SUBMETRICS` in [`orchestrator.py`](orchestrator.py).

> [!NOTE]
> Throughout the metrics collection framework and sustainability dashboard, we refer to 'Ecosystem' in place of the CASS 'Sustainability' dimension.

**Legend:** ✅ collected · 🔲 rendered as "Not yet collected" · ⬜ whole section
is a stub

Each collected sub-metric below now lists a **Meets threshold when** column —
the value it's checked against today. Framed as a threshold rather than
"pass"/"fail": a sub-metric below its threshold isn't a defect, and the report
itself (§3.5) treats an unmeasured indicator as excluded from scoring, not as a
failure.

The values shown are current defaults, not fixed constants —
[`config/thresholds.yaml`](config/thresholds.yaml) is the authoritative,
configurable source (see [`ORCHESTRATOR_GUIDE.md`](ORCHESTRATOR_GUIDE.md#configurable-passfail-thresholds)
for how to override one). If a deployment has overridden a value, what
actually runs may differ from what's written here; this table isn't
regenerated from that file automatically. Per-project/per-package overrides
(letting one project use a different value than everyone else) aren't wired
up yet.

---

## Coverage at a glance

| Section | Title | Filled |
|---|---|---|
| 4.1.1 | Software Citation and Adoption | 7/7 |
| 4.1.2 | Field Research Impact | ⬜ 0/3 |
| 4.2.1 | CoC, Governance, and Contributor Guidelines | 5/5 |
| 4.2.2 | Open-Source Licensing and FAIR Compliance | 5/5 |
| 4.2.3 | Active Maintenance | 6/6 |
| 4.2.4 | Engagement | 7/7 |
| 4.2.5 | Outreach | 5/8 |
| 4.2.6 | Welcomeness | 1/7 |
| 4.2.7 | Collaboration | 2/5 |
| 4.2.8 | Financial Sustainability | 4/5 |
| 4.2.9 | Institutional & Organizational Support | 1/5 |
| 4.2.10 | Project Longevity and Community Health | 5/5 |
| 4.3.1 | Reliability and Robustness | 5/5 |
| 4.3.2 | Development Practices | 5/5 |
| 4.3.3 | Reproducibility | 5/5 |
| 4.3.4 | Usability | 2/5 |
| 4.3.5 | Accessibility | 5/5 |
| 4.3.6 | Maintainability and Understandability | 5/5 |
| 4.3.7 | Performance and Efficiency | ⬜ 0/10 |
| 4.3.8 | Software Supply Chain Integrity | 4/5 |

A stub section renders nothing unless the package's `package_config/` file
supplies overrides, in which case it renders those values against a 0/N score.

---

## 4.1 Impact

### 4.1.1 Software Citation and Adoption
**Collector:** [`collectors/impact/citation.py`](collectors/impact/citation.py)

This section renders its own labels rather than the report's five sub-metric
names, because the underlying sources return directly comparable counts. None
of these rows are evaluated against a threshold — they're reported as raw
counts (or, for Citation Score, a computed composite), not a pass/fail bar.

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

## 4.2 Ecosystem

### 4.2.1 Codes of Conduct, Governance, and Contributor Guidelines
**Collectors:** [`community_health.py`](collectors/ecosystem/community_health.py),
[`chaoss_governance.py`](collectors/ecosystem/chaoss_governance.py),
[`openssf_badge.py`](collectors/ecosystem/openssf_badge.py),
[`openssf_scorecard.py`](collectors/ecosystem/openssf_scorecard.py)

| Sub-metric | Status | Meets threshold when | Source |
|---|---|---|---|
| Enhanced Document Detection | ✅ | ≥2 of CoC / Governance / Contributing found | CODE_OF_CONDUCT / GOVERNANCE / CONTRIBUTING file detection |
| Governance Keyword Analysis | ✅ | ≥2 of 4 concept groups (decision process, defined roles, membership lifecycle, conflict resolution) found in the text | read from the full governance/CoC/contributing documents |
| OpenSSF Badge Integration | ✅ | badge progress ≥100%; or, when **substituted** by an *OpenSSF Scorecard* row, each individual check scores ≥7/10 | `bestpractices.dev`, level + percentage. Scorecard (`api.securityscorecards.dev`) substitutes in with a per-check breakdown of failing checks whenever scorecard data exists, so the section is always 5 rows |
| CHAOSS Governance Metrics | ✅ | weighted score ≥60/100 | [`chaoss_governance.py`](collectors/ecosystem/chaoss_governance.py) — weighted 0–100 health score, with a per-category breakdown (popularity 15%, docs 20%, time-to-close 15%, issue age 10%, PR closure ratio 15%, release frequency 15%, issue inclusivity 10%). A category that couldn't be measured is dropped from both the score and its weight, not counted as 0 |
| Governance Effectiveness Assessment | ✅ | CODEOWNERS present **and** governance docs touched within 1,095 days (3 years) | |

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
**Collectors:** [`licensing.py`](collectors/ecosystem/licensing.py) (Enhanced
License Detection, OSI License Validation),
[`fair_licensing.py`](collectors/ecosystem/fair_licensing.py) (the other
three)

| Sub-metric | Status | Meets threshold when | Source |
|---|---|---|---|
| Enhanced License Detection | ✅ | a license is identified, from the API or the text fallback | GitHub License API and SPDX identifier, falling back to the family named in the licence text |
| Automated FAIR4RS Assessment | ✅ | ≥3 of the 4 FAIR4RS principles satisfied: **Findable** (a DOI in CITATION.cff, or `.zenodo.json`), **Accessible** (a license identified), **Interoperable** (a CITATION.cff exists, or `codemeta.json`), **Reusable** (a license identified **and** ≥1 release exists) | each principle is an AND/OR of independently-fetched signals; one that couldn't be fully checked (a gap on one signal, with the others not yet enough to decide it either way) is excluded rather than counted against the total |
| OSI License Validation | ✅ | the identified license is on the SPDX/OSI-approved list | a text-resolved family counts as approved too |
| License Exception Handling | ✅ | a license family is identified — from the API, or recovered from the text when GitHub returns `NOASSERTION` | plus exception / extra-terms markers surfaced as detail |
| FAIR Metadata Assessment | ✅ | ≥4 of 6 CITATION.cff fields present (title, authors, version, license, repository-code, DOI) | CITATION.cff field completeness |

**GitHub returns `NOASSERTION` for any licence it cannot match verbatim.** HDF5's
LICENSE states plainly that the software "is covered by the 3-clause BSD
License", but the extra copyright notices stop the classifier recognising it — so
the framework reported HDF5 as licence "Other", category "Unknown", OSI
"unknown". It now resolves to BSD-3-Clause, Permissive, OSI-approved. (HDF5 is
also being fixed at source so GitHub classifies it directly; the fallback stays
for every other project with a modified licence.)

### 4.2.3 Active Maintenance
**Collector:** [`active_maintenance.py`](collectors/ecosystem/active_maintenance.py)

| Sub-metric | Status | Meets threshold when | Source |
|---|---|---|---|
| Commit Activity Pattern Analysis | ✅ | >0 commits in the last 52 weeks | `/stats/participation`, 52-week series |
| Maintenance Mode Indicator Detection | ✅ | not archived, and no maintenance-mode keywords in the description | `archived` flag + description keywords |
| Activity Trend Monitoring | ✅ | last 13 weeks' commit volume is stable or increasing vs. the previous 13 | `/stats/participation` |
| Release Pattern Assessment | ✅ | ≥1 release in the last year | `/releases` |
| Multi-Channel Communication Activity | ✅ | ≥2 of: Discussions, wiki, mailing list, chat, forum, help-desk link in the README | Discussions / wiki flags plus links detected in the README |
| Contributor Abandonment Forecasting | ✅ | departure rate ≤50% (unmeasurable if there's no prior-year contributor history to compare against) | contributors active in the prior 52 weeks who committed nothing in the last 52, from `/stats/contributors` |

### 4.2.4 Engagement
**Collector:** [`engagement.py`](collectors/ecosystem/engagement.py)

| Sub-metric | Status | Meets threshold when | Source |
|---|---|---|---|
| Response Time Tracking | ✅ | median time to first response < 168 hours (1 week) | `/issues`, time to first response |
| Issue Resolution Analysis | ✅ | median time to close < 720 hours (30 days) | `/issues`, close rate |
| Pull Request Flow Assessment | ✅ | merge rate > 50% | `/pulls`, median cycle time |
| Support Request Closure Analysis | ✅ | open/closed issue ratio < 2.0 | `/issues` |
| Engagement Quality Metrics | ✅ | median comments per issue ≥2 | |
| Communication Pattern Analysis | ✅ | ≥70% of issues answered within a week | |
| Community Participation Assessment | ✅ | ≥15% of issues and PRs opened by non-maintainers (`author_association`) | |

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
**Collector:** [`outreach.py`](collectors/ecosystem/outreach.py)

| Sub-metric | Status | Meets threshold when | Source |
|---|---|---|---|
| New Contributor Tracking | ✅ | >0 new contributors found | contributors whose all-time count is fully inside the last 365 days |
| Contributor Retention Analysis | ✅ | ≥50% of new contributors made ≥2 commits | share of newcomers with ≥2 commits |
| Contributor Lifecycle Mapping | ✅ | ≥3 repeat contributors (5+ commits) | one-time (1) / casual (2–4) / repeat (5+) buckets from `/contributors` |
| Contribution Type Diversity | 🔲 | — | non-code contributions aren't recorded in the repo |
| Good First Issue Effectiveness | ✅ | ≥1 **open** issue labelled `good first issue`, `help wanted`, or `newcomer` | search API counts |
| External Event Participation | 🔲 | — | needs conference programmes |
| Training Material Integration | 🔲 | — | needs course syllabi |
| Onboarding Infrastructure Assessment | ✅ | ≥3 of 4: Contributing guide, issue template, PR template, getting-started guide | |

> "New" is inferred by comparing each author's all-time contribution count
> against their commits in the window, rather than walking the whole log to find
> each first commit. Cheap and accurate for normal repos; it misses anyone whose
> recent commits exceed the 10-page pagination cap.

### 4.2.6 Welcomeness
**Collector:** [`welcomeness.py`](collectors/ecosystem/welcomeness.py)

| Sub-metric | Status | Meets threshold when | Source |
|---|---|---|---|
| CHAOSS Community Experience Metrics | 🔲 | — | — |
| Response Quality and Tone Analysis | 🔲 | — | needs NL analysis of conversations |
| Communication Sentiment Analysis | 🔲 | — | needs NL analysis of conversations |
| Contributor Journey Mapping | 🔲 | — | — |
| Language and Communication Review | 🔲 | — | needs NL analysis of documentation |
| Leadership Role Representation | 🔲 | — | needs maintainer demographics |
| Decision-Making Visibility | ✅ | ≥2 of: Discussions, wiki, Pages, roadmap doc, meeting notes, decision records, governance doc | `has_discussions` / `has_wiki` / `has_pages` plus roadmap, meeting notes, decision records, governance doc |

### 4.2.7 Collaboration
**Collector:** [`collaboration.py`](collectors/ecosystem/collaboration.py)

| Sub-metric | Status | Meets threshold when | Source |
|---|---|---|---|
| Advanced Dependency Analysis | ✅ | ≥2 distinct package ecosystems carrying the software | |
| Cross-project Reference Detection | 🔲 | — | the report specifies AI analysis of issues and PRs |
| Interoperability Assessment | 🔲 | — | needs domain-specific standards knowledge |
| Collaboration Network Analysis | ✅ | ≥10 dependent packages **or** ≥50 dependent repositories | downstream dependents |
| Standards Compliance Tracking | 🔲 | — | needs domain-specific standards knowledge |

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

Either downstream signal meets the threshold on its own. A library can be
depended on by many packages (HDF5: 176 conda packages) or by many
repositories (zfp: 111 repos from
only 9 packages); both are real evidence of ecosystem integration.

### 4.2.8 Financial Sustainability
**Collector:** [`funding.py`](collectors/ecosystem/funding.py)

| Sub-metric | Status | Meets threshold when | Source |
|---|---|---|---|
| Enhanced Funding Documentation Analysis | ✅ | a funding file exists, **or** ≥1 award reference found in the README | FUNDING.yml / funding.json, plus DOE/NSF/NIH award numbers in the README |
| Institutional Affiliation Tracking | ✅ | ≥3 distinct organizations found | `company` field of the top 25 contributors |
| NIH R50 Award Tracking | 🔲 | — | NIH RePORTER API is public and unauthenticated — a Tier 2 win, not yet wired |
| Corporate Sponsorship Detection | ✅ | ≥1 declared funding platform, **or** the repository is organization-owned | |
| Funding Portfolio Analysis | ✅ | ≥2 distinct sources (funding platforms + award references, combined) | |

> Contributor affiliations are folded onto a canonical key, so "The HDF Group",
> "HDFGroup" and "The HDFgroup" count as one organization. Without that the
> free-text `company` field inflates the diversity figure — HDF5 read as 5
> organizations instead of its actual 3.

### 4.2.9 Institutional & Organizational Support
**Collector:** [`funding.py`](collectors/ecosystem/funding.py) — shares
4.2.8's contributor-affiliation pass rather than fetching it twice.

| Sub-metric | Status | Meets threshold when | Source |
|---|---|---|---|
| RSE Position Detection | 🔲 | — | needs LinkedIn / institutional directories |
| Institutional Support Tracking | ✅ | ≥3 distinct organizations found (same threshold and underlying data as 4.2.8's Institutional Affiliation Tracking) | distinct organizations backing the top contributors |
| Career Development Indicators | 🔲 | — | not visible from the repository |
| NIH R50 Award Integration | 🔲 | — | NIH RePORTER, as for 4.2.8 |
| Institutional Policy Analysis | 🔲 | — | not visible from the repository |

### 4.2.10 Project Longevity and Community Health
**Collector:** none of its own — derived in `_transform_for_dashboard`
([`orchestrator.py`](orchestrator.py)) from data `active_maintenance.py`
already fetched for 4.2.3. Costs **zero additional API calls**.

| Sub-metric | Status | Meets threshold when | Derivation |
|---|---|---|---|
| Comprehensive Activity Analysis | ✅ | ≥2 of 3 dimensions active: >0 commits in 52w, ≥1 release/yr, active in ≥26 of the last 52 weeks | commits, releases and sustained activity |
| Contributor Viability Assessment | ✅ | bus factor ≥3 (same threshold as 4.2.3 and 4.3.6, so the same number can't read healthy in one section and at-risk in another) | |
| Maintenance Mode Detection | ✅ | no warnings: not archived, no maintenance-mode keywords, pushed within the last 365 days | archived flag, description keywords, days since last push |
| Community Health Trends | ✅ | 52-week commit trend is stable or increasing | |
| Project Lifecycle Assessment | ✅ | lifecycle stage is Growing (2–5 yrs) or Mature (≥5 yrs with a release or commit in the last year) — Emerging, Legacy and Retired don't meet it | project age (longer of first-commit and repo-creation dates) × current release activity |

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
[`static_analysis.py`](collectors/quality/static_analysis.py),
[`reliability.py`](collectors/quality/reliability.py)

**Defects are counted by GitHub issue *type* first, then by label.** Issue types
are a native field, separate from labels, and are what several of these projects
use — HDF5 carries no `bug` label at all but has 479 Bug-typed issues, so a
label-only query reported it as unmeasurable. A project that records defects
neither way is reported as unmeasurable rather than scored 0-vs-0 "stable",
which would falsely claim the threshold was met. ADIOS2 is genuinely in that
position: its last `bug`-labelled issue was October 2024.

**Hardening flags are looked for beyond the root build file.** Large projects
keep them elsewhere — HDF5's sanitizer setup lives in
`config/sanitizer/sanitizers.cmake` — so the conventional CMake config
directories are listed and any file whose name suggests flags is read, alongside
the CI workflow definitions.

| Sub-metric | Status | Meets threshold when | Source |
|---|---|---|---|
| Advanced Static Analysis | ✅ | ≥1 defect-finding tool found (Sonar, Coverity, cppcheck, Semgrep, clang-tidy, sanitizers) | configs and analysis workflows |
| Enhanced Security Analysis | ✅ | a CodeQL workflow is present | |
| CERT Guidelines Compliance | ✅ | ≥1 hardening indicator found (warnings-as-errors, fortify source, stack protector, sanitizers, explicit CERT/MISRA reference) | hardening flags, sanitizers and explicit CERT/MISRA references — **practice indicators, not audited conformance** |
| Test Coverage Excellence | ✅ | ≥80% line coverage | Codecov v2 public API |
| Reliability Trend Analysis | ✅ | defect volume over the last 52 weeks is ≤1.25× the prior 52 weeks (falling counts too); unmeasurable below 5 total defects across both windows | defect reports over two 52-week windows, by issue type first then label |

Codecov's `api.codecov.io/api/v2/github/{owner}/repos/{repo}/` is public and
unauthenticated for public repos. Repos with no active Codecov integration
render "No Codecov data found" rather than a number. Coveralls was evaluated
and rejected — its public JSON endpoint returns HTTP 403 to non-browser clients.

### 4.3.2 Development Practices
**Collectors:** [`ci_cd.py`](collectors/quality/development_practices/ci_cd.py),
[`dev_tooling.py`](collectors/quality/development_practices/dev_tooling.py),
[`openssf_badge.py`](collectors/ecosystem/openssf_badge.py)

| Sub-metric | Status | Meets threshold when | Source |
|---|---|---|---|
| CI/CD Effectiveness Assessment | ✅ | ≥1 of 6 internal checks met (workflow present, a recent successful run, deploy/release cadence at or above 1/year, elite-tier <24h cycle time, etc.) | `.github/workflows/` parsing + run status |
| Testing Framework Excellence | ✅ | ≥2 of 4: test directory, CTest/CMake config, pytest config, vendored test framework | |
| Code Review Quality Analysis | ✅ | ≥70% of the last 50 merged PRs had ≥1 review | |
| Development Tool Integration | ✅ | ≥2 of 4: pre-commit hooks, formatter config, linter config, Dependabot/Renovate config | |
| Community Contribution Facilitation | ✅ | OpenSSF Best Practices badge progress = 100% (the *passing* level) | proxy — the report's own metric needs data this framework doesn't have another source for |

### 4.3.3 Reproducibility
**Collector:** [`reproducibility.py`](collectors/quality/reproducibility.py)

| Sub-metric | Status | Meets threshold when | Source |
|---|---|---|---|
| FAIR4RS Compliance Assessment | ✅ | any of CITATION.cff, codemeta.json, `.zenodo.json` found | |
| Containerization Excellence | ✅ | any of Dockerfile, Singularity/Apptainer definition found | |
| Version Control Best Practices | ✅ | ≥1 of the last 5 releases (or tags, if no releases exist) follows semantic versioning | `/releases`, falling back to `/tags` |
| Environment Management | ✅ | any dependency-pinning file found (`requirements.txt`, `poetry.lock`, `conda-lock.yml`, `package-lock.json`, `Cargo.lock`, `uv.lock`, etc.) | |
| Reproducibility Documentation | ✅ | any of an install/build guide, release notes, or environment spec (`environment.yml`, `spack.yaml`, devcontainer) found | |

Each of the 5 rows is itself a weighted blend (containers 20%, dependency
pinning 30%, FAIR4RS metadata 20%, docs 15%, semantic versioning 15% of the
section's overall percentage) rather than a single file check — a category
that couldn't be measured at all (every candidate path gapped) is dropped from
both the score and its weight, not counted as 0.

### 4.3.4 Usability
**Collectors:** [`usability.py`](collectors/quality/usability.py),
[`collaboration.py`](collectors/ecosystem/collaboration.py)

| Sub-metric | Status | Meets threshold when | Source |
|---|---|---|---|
| User Experience Assessment | 🔲 | — | the report specifies the UEQ instrument, which needs a survey |
| Documentation Completeness Analysis | ✅ | README covers ≥3 of 4 core sections (installation / usage / examples / support); or, with ≥1 section, a `docs/` tree **and** a published site | README headings, `docs/` tree, published documentation site |
| Accessibility Feature Detection | 🔲 | — | — |
| Installation Success Tracking | ✅ | ≥1 package manager with a documented install command | from 4.2.7's registry data |
| Usage Analytics Integration | 🔲 | — | — |

README sections are matched against **heading text only**, so "you can install
it somehow" in a paragraph doesn't count as an installation section.

A thin README still meets the threshold when it is backed by both a `docs/` tree and a
published site — HDF5's README covers 2 of 4 sections but its real
documentation lives elsewhere.

Installation Success reuses the 4.2.7 registry lookup rather than querying
ecosyste.ms a second time for the same answer.

### 4.3.5 Accessibility
**Collectors:** [`accessibility.py`](collectors/quality/accessibility.py),
[`deployment_environments.py`](collectors/quality/deployment_environments.py)

| Sub-metric | Status | Meets threshold when | Source |
|---|---|---|---|
| Portable Build System Detection | ✅ | any of CMake, Spack recipe, Conda recipe, Autoconf, Makefile found | |
| Container Availability Assessment | ✅ | any of Dockerfile, Singularity/Apptainer definition found | |
| Architecture Compatibility Analysis | ✅ | ≥1 non-x86 CPU architecture named in the CI workflows (ARM64, POWER, RISC-V, s390x) — x86-64 alone doesn't count | |
| Platform Documentation Evaluation | ✅ | ≥2 platform families named in the README | |
| Deployment Environment Testing | ✅ | ≥2 distinct OS families across CI runner labels | [`deployment_environments.py`](collectors/quality/deployment_environments.py) |

### 4.3.6 Maintainability and Understandability
**Collector:** [`maintainability.py`](collectors/quality/maintainability.py),
plus the bus factor reused from `active_maintenance.py`.

| Sub-metric | Status | Meets threshold when | Source |
|---|---|---|---|
| Advanced Complexity Analysis | ✅ | <5% of source files over 100 KB, **and** directory tree depth ≤10 | source-file size distribution and tree depth |
| Code Quality Assessment | ✅ | test-file-to-source-file ratio ≥0.20 | |
| Documentation Quality Evaluation | ✅ | a doc generator (Doxygen / Sphinx / MkDocs) is configured, **or** doc-file-to-source-file ratio ≥5% | |
| Knowledge Distribution Analysis | ✅ | bus factor ≥3 (same threshold as 4.2.3 and 4.2.10) | top-contributor share shown as context, not scored separately |
| Refactoring and Evolution Tracking | ✅ | ≥2% of a 300-commit sample are refactor-intent commits | |

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

### 4.3.8 Software Supply Chain Integrity
**Collector:** [`supply_chain.py`](collectors/quality/supply_chain.py)

| Sub-metric | Status | Meets threshold when | Source |
|---|---|---|---|
| SBOM Detection and Validation | ✅ | an SPDX/CycloneDX-named file found at the repo root, or among the last 5 releases' assets | filename matching only — no SBOM content is parsed or validated |
| Build Provenance Assessment | ✅ | a SLSA/in-toto-attestation-named file found among the last 5 releases' assets | filename matching only |
| Dependency Vulnerability Posture | ✅ | no exactly-`==`-pinned dependency in `requirements.txt` has a known OSV.dev vulnerability | [OSV.dev](https://osv.dev) batch query API — free, unauthenticated, no Dependabot alert access needed on the target repo |
| Dependency Freshness (libyears) | 🔲 | — | needs a machine-readable dependency manifest with enough history to compute a lag, not just pinned versions |
| Badge and Scorecard Level | ✅ | not independently scored — display-only, read from the 4.2 OpenSSF Badge / Scorecard collectors | passthrough, not re-fetched |

Score is out of 3 (SBOM + Build Provenance + Dependency Vulnerability
Posture): the one still-not-collected row is excluded from the denominator
per §3.5 rather than scored as failure, and Badge/Scorecard is display-only
context rather than an independently scored row, since this section
explicitly "does not restate practices already assessed by the OpenSSF Best
Practices Badge."

SBOM/Build Provenance remain file-presence and filename matching against a
small hint list (`sbom`, `spdx`, `cyclonedx`, `intoto`, `slsa`, `.sigstore`,
…) — no SBOM/attestation content is parsed or validated against the
SPDX/CycloneDX/SLSA specs.

**Dependency Vulnerability Posture's coverage is real but narrow.** Only a
root-level `requirements.txt` is read, and only lines pinned with an exact
`==` are checked — OSV.dev's query API takes a single version, not a range,
so `numpy>=1.20` names a real dependency the check can't evaluate. Nested
`requirements.txt` files (`docs/requirements.txt`, CI-tooling pins,
test-only pins) are deliberately not matched, since a stale Sphinx theme
version isn't a supply-chain finding the way a stale runtime dependency is.
Lockfiles for other ecosystems (`Cargo.lock`, `poetry.lock`, `go.sum`, …)
aren't parsed yet. Of the 71 tracked repositories, 25 have a
`requirements.txt` somewhere and 9 pin it at the repository root — this
check found a real, currently unpatched **CRITICAL** remote-code-execution
vulnerability
([GHSA-53q9-r3pm-6pq6](https://github.com/advisories/GHSA-53q9-r3pm-6pq6))
in one of those 9 during development.

### 4.1.1 / 4.2.7 additions: downloads and reverse dependencies

Two more automated signals ride on data `collaboration.py` (4.2.7) already
fetches from ecosyste.ms and are surfaced in the 4.1.1 Citation and Adoption
section as unweighted evidence, the same way GitHub stars/forks already are:

- **Reverse-Dependency Analysis** — the same `dependent_packages` /
  `dependent_repos` counts 4.2.7 scores against, rendered under 4.1.1 too.
- **Package-Manager Download Telemetry** — `downloads` / `downloads_period`
  per registry. Not scored: `downloads_period` differs by registry ("total"
  for conda, "last-month" for PyPI), so raw totals aren't comparable across
  ecosystems and the report's own 4.1.1 Considerations says as much.

---

## Scoring

Each section reports `Score: n/N`, where `N` is the number of sub-metrics the
report defines for it and `n` is how many currently meet their threshold.
Section scores roll up into three dimension scores, which combine into an
overall score using the weights in
[`config/orchestrator.yaml`](config/orchestrator.yaml):

```yaml
metric_weights:
  impact: 0.33
  ecosystem: 0.34
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
  ecosystem: true        # 4.2
  quality: true         # 4.3
```

Individual sub-collectors are toggled within a dimension:

```yaml
ecosystem_collectors:
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

A project can also narrow its *own* collection by adding `.corsa/metrics.yaml`
to its own repo -- same `collectors:` / `overrides:` shape, fetched at
collection time, and unable to re-enable anything the global config or a
`package_config/` file already turned off. See
[docs/PROJECT_CONFIG.md](docs/PROJECT_CONFIG.md).

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
