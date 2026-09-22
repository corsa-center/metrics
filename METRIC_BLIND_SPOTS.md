# Metric Blind Spots and Remediation Plan

False-negative analysis of the metrics framework, measured against the real file
trees of the tracked portfolio, plus the plan to close what it found.

Seven false-negative reports arrived from AMReX on 2026-09-21
([#48](https://github.com/corsa-center/metrics/issues/48)–[#54](https://github.com/corsa-center/metrics/issues/54)).
They are not seven bugs. They are six recurring defect classes, and the same
code paths are already mis-scoring most of the portfolio.

> [!IMPORTANT]
> Every one of these defects is the same shape: **the framework asks "is the
> file at this exact path?" when it should ask "what is actually here?"** A
> literal path list can only match spellings somebody thought to write down, and
> there are ~112 such literals across 14 collectors.
>
> One collector already does it right. [`maintainability.py`](collectors/quality/maintainability.py)
> pulls the recursive git tree once and counts what it finds — it cannot suffer
> F1, F2 or F3, at a cost of one API call. That is the fix, and it is
> architectural, not a longer list of strings.

---

## How this was measured

A probe fetched the recursive git tree, default branch and release tags for
every repository in the production catalog
(`{dashboard}/explore/github-data/intReposInfo.json`), then ran every
path- and format-matching heuristic in the collectors against what is actually
in those repositories.

| | |
|---|---|
| Repositories in catalog | 71 |
| Successfully probed | 65 |
| Excluded — LLNL org blocks classic PATs >30 days | `llnl/libROM`, `llnl/pylibROM`, `llnl/sundials`, `llnl/zfp` |
| Excluded — URL returns HTTP 404 | `vtk/vtk`, `paraview/paraview` |
| Probe date | 2026-09-22 |

The LLNL exclusion affects **local auditing only** — production uses the
ephemeral `secrets.GITHUB_TOKEN` from GitHub Actions, which is an installation
token and not subject to that organization policy.

---

## Measured blast radius

"Affected" means the project has the thing the metric is looking for, in a form
the metric cannot see — a false negative waiting to be reported.

| Class | Defect | Affected | Share | Worst cases |
|---|---|---|---|---|
| **F5** | Hard-coded default branch `main` | 46 / 65 | 71% | spack, Trilinos, HDF5, kokkos, mfem |
| **F4** | CI workflows skipped by filename gate | 40 / 65 | 62% | HDF5 reads 2/76, llvm 4/62, papi 0/19 |
| **F2** | Dependency-pinning file outside path list | 23 / 65 | 35% | petsc, llvm, spack, AMReX, ADIOS2 |
| **F2** | Container definition outside path list | 20 / 65 | 31% | kokkos, ADIOS2, ascent, E4S, visit |
| **F6** | Tag scheme fails the semver regex | 19 / 65 | 29% | llvm, Trilinos, papi, Legion, GASNet |
| **F2** | Getting-started guide in a doc tree, unmatched | 16 / 65 | 25% | AMReX, HDF5, ompi, petsc, libCEED |
| **F3** | Compiler-flag file outside scanned directories | 8 / 65 | 12% | AMReX, HDF5, Trilinos, ADIOS2, llvm |
| **F1** | Directory exists under a different case | 6 / 65 | 9% | superlu `DOC/` `TESTING/`, AMReX `Docs/` |
| **F13** | Catalog points at a repo that does not exist | 5 / 71 | 7% | vtk/vtk, paraview/paraview, RAJA, Umpire, xsdk |

---

## Defect classes

### F1 — Case-sensitive path lookup

GitHub's Contents API is case-sensitive. A literal `docs` never matches `Docs`;
`tests` never matches `TESTING`. Diagnosed and fixed once — in
`community_health.py`, which lists each directory and matches
case-insensitively — but never propagated.

```
community_health.py   →  _build_file_index()    case-insensitive   ✅
14 other collectors   →  _check_file_exists()   literal            ✗
```

Reported: [#54](https://github.com/corsa-center/metrics/issues/54) AMReX `Docs/`.
Predicted: `superlu`, `superlu_dist`, `superlu_mt` ship `DOC/` and `TESTING/`.

### F2 — Finite path enumeration

Even case-corrected, a fixed candidate list cannot anticipate real layouts.

```
getting-started, code knows:  docs/getting-started.md  docs/getting_started.md
                              docs/quickstart.md       doc/getting-started.md
                              GETTING_STARTED.md       docs/source/getting_started.rst

portfolio actually has:       AMReX     Docs/sphinx_documentation/source/GettingStarted.rst
                              HDF5      docs/doxygen/dox/GettingStarted.dox
                              open-mpi  docs/building-apps/quickstart.rst
                              petsc     doc/install/install_tutorial.md
                              …12 more
```

Reported: [#49](https://github.com/corsa-center/metrics/issues/49). The same
list-shaped assumption governs containers, dependency locks, citation metadata
and analysis configs.

### F3 — Shallow scan depth

Hardening flags are read from four root build files plus four named directories
(`cmake/`, `config/cmake/`, `config/sanitizer/`, `CMake/`). Large projects keep
compiler flags in their own build tree.

```
AMReX     Tools/CMake/AMReXFlagsTargets.cmake                        (#51)
HDF5      config/flags/HDFClangCXXFlags.cmake
Trilinos  packages/kokkos-kernels/cmake/kokkoskernels_warnings.cmake
ADIOS2    scripts/ci/cmake/ci-uo-sanitizer-asan.cmake
```

### F4 — Keyword-gated reads with a hard cap

Only workflows whose *filename* matches an analysis-shaped regex are read,
capped at eight. HPC projects name workflows after compilers and platforms —
`gcc.yml`, `cuda.yml`, `macos.yml` — and set hardening flags there. The gate was
added to bound request volume; it bounds the evidence instead.

```
HDF5     2 of 76 workflows read        papi      0 of 19
llvm     4 of 62                       dyninst   0 of 16
AMReX    2 of 30          (#51)        ginkgo    0 of 14
```

### F5 — Hard-coded default branch

A branch filter naming a branch that does not exist returns zero rows and no
error, so the metric reads "no CI data" for projects with tens of thousands of
runs.

```
main 19 · master 29 · develop 11 · development 2 · migration-notice 2 · stable 1 · devel 1

AMReX:  0 runs on "main"  vs  63,598 on "development"
```

Fixed 2026-09-19 — `ci_cd.py` now resolves the repository's real default branch.

### F6 — Format and scheme rigidity

A strict three-component semver regex rejects the versioning conventions this
community uses. Calendar versioning is now accepted; the dominant HPC pattern —
a project-name-prefixed tag — still fails and accounts for most of the
remaining 16.

```
llvm       llvmorg-23.1.1              Legion   legion-26.06.0
Trilinos   trilinos-release-17-2-1     GASNet   gex-2025.8.0
papi       papi-7-2-0-t                dakota   vstable_2026_09_04
AMReX      26.09     ✅ fixed — CalVer now accepted   (#53)
```

### F7 — Fallback suppression

A primary source returning a non-zero but insufficient result blocks the
fallback that would have found the real signal.

```
was:  if recent + previous == 0:                 try labels
now:  if recent + previous < min_trend_volume:   try labels, keep the larger

AMReX: 1 typed issue suppressed 35 bug-labelled ones   (#52)
```

Look for any fallback guarded by `== 0` rather than by "is this enough to answer
the question?".

### F8 — Domain-inappropriate proxy

The hardest class, because the code works as written. Registry dependents
measure adoption in ecosystems that publish packages; HPC libraries are consumed
at source level through submodules and `AMREX_HOME`, which no registry records.

- [#50](https://github.com/corsa-center/metrics/issues/50) — AMReX downstream
  (WarpX, Castro, PeleC, MFiX-Exa) invisible to ecosyste.ms; its conda-forge
  package missing from the index entirely.
- A spurious `go` registry entry is returned for a C++ library.

### F9 — Practice-shape penalty

The metric penalises a deliberate, effective workflow.
[#48](https://github.com/corsa-center/metrics/issues/48): AMReX uses issues as a
bug tracker; maintainers filed a batch of audit defects, each closed by a PR that
fixes it. Engagement Quality reads median 0 comments against a minimum of 2, and
Community Participation reads 3% from outside — both describing effective triage
as disengagement.

### F10–F14 — Supporting classes

| Class | Defect |
|---|---|
| **F10** | Sample-window and pagination bias — 30-issue samples, 5-page contributor cap, 10-page commit cap, last-50-PR windows |
| **F11** | Gap vs. confirmed-absent conflation — a 403 or timeout read as "does not exist" |
| **F12** | Third-party index gaps — ecosyste.ms coverage, Codecov inactivity, bestpractices.dev tier confusion |
| **F13** | Catalog identity drift — entries pointing at renamed or deleted repositories |
| **F14** | Multi-repo projects — governance, docs or tests in a sibling repository |

---

## Per-metric exposure

**Legend:** 🔴 confirmed (reported or verified) · 🟡 measured (probe found
affected repos) · ⚪ latent (same code path, no portfolio hit yet)

| § | Sub-metric | Exposure | Why |
|---|---|---|---|
| 4.1.1 | Formal Citations / Informal Mentions | ⚪ F8, F12 | literature-DB name matching |
| 4.1.1 | Dependent Packages | 🟡 F8, F12 | registry proxy; same ecosyste.ms gap as 4.2.7 |
| 4.2.1 | Enhanced Document Detection | ⚪ F2, F14 | only collector immune to F1; `.rst` gap fixed |
| 4.2.1 | Governance Keyword Analysis | ⚪ F6 | fixed keyword vocabulary |
| 4.2.1 | Governance Effectiveness | 🟡 F1, ⚪ F2 | CODEOWNERS at three literal paths |
| 4.2.1 | CHAOSS Governance Metrics | ⚪ F10, F8 | 30–50 item samples; popularity weighted on stars |
| 4.2.2 | FAIR Metadata Assessment | 🟡 F1, F2 | only `CITATION.cff`; CodeScribe ships `citation.cff` |
| 4.2.2 | Enhanced License Detection | ⚪ F12 | NOASSERTION on modified licenses — text fallback mitigates |
| 4.2.3 | Release Pattern Assessment | 🟡 F6, ⚪ F10 | tag-name parsing; `per_page=20` |
| 4.2.3 | Multi-Channel Communication | ⚪ F2, F6 | README link detection by pattern |
| 4.2.3 | Contributor Abandonment | ⚪ F10, F11 | `/stats/` returns 202 while computing |
| 4.2.4 | **Engagement Quality Metrics** | 🔴 F9, ⚪ F10 | **#48** — deliberate triage reads as disengagement |
| 4.2.4 | **Community Participation** | 🔴 F9 | **#48** — maintainer-authored work penalised |
| 4.2.4 | Response Time / Issue Resolution | ⚪ F10, F9 | 30-issue sample; bot-closed issues counted |
| 4.2.5 | **Onboarding Infrastructure** | ✅ fixed | **#49** — fixed via RepoTree; getting-started guide now found by regex, not 6 literal paths |
| 4.2.5 | New Contributor / Retention / Lifecycle | ⚪ F10 | 5-page contributor cap, 10-page commit cap |
| 4.2.5 | Good First Issue Effectiveness | ⚪ F6, F9 | label vocabulary; penalises promptly-fixed issues |
| 4.2.6 | Decision-Making Visibility | 🟡 F2, ⚪ F1 | roadmap / meeting-notes paths — 4 repos unmatched |
| 4.2.7 | **Collaboration Network Analysis** | 🔴 F8, F12 | **#50** — source-level coupling invisible to registries |
| 4.2.7 | Advanced Dependency Analysis | 🔴 F12 | spurious `go` entries; conda-forge missing |
| 4.2.8 | Funding Documentation Analysis | ⚪ F2, F6 | award-number regex is DOE/NSF/NIH-shaped only |
| 4.2.8 | Institutional Affiliation | ⚪ F10, F12 | top-25 sample; free-text `company` often blank |
| 4.3.1 | **CERT Guidelines Compliance** | ◐ F3 fixed, F4 open | **#51** — flag-file scan now whole-tree (`.cmake`); workflow filename gate is Phase 2 |
| 4.3.1 | **Reliability Trend Analysis** | 🔴 F7 | **#52** — fallback suppressed by one typed issue |
| 4.3.1 | Advanced Static Analysis | 🟡 F2, F4 | config paths + the same workflow gate |
| 4.3.1 | Test Coverage Excellence | ⚪ F12 | Codecov-only; near-universally unmeasurable for HPC |
| 4.3.2 | CI/CD Effectiveness | 🔴 F5, ⚪ F10 | fixed — was zeroing 71% of the portfolio |
| 4.3.2 | Testing Framework Excellence | ✅ fixed | `Tests/`, `TESTING/`, `TEST/`, vendored frameworks all case-insensitive now |
| 4.3.2 | Development Tool Integration | ⚪ F1, F2 | literal config paths at repo root |
| 4.3.2 | Code Review Quality | ⚪ F10, F9 | last 50 PRs; self-merge conventions differ |
| 4.3.3 | **Version Control Best Practices** | 🔴 F6 | **#53** — CalVer fixed; 16 prefixed-tag repos still fail |
| 4.3.3 | Environment Management | ◐ F1 fixed, F2 open | case-insensitive now; 23 repos still pin deps at an unenumerated nested path (needs a scope decision, see Phase 1 footnote) |
| 4.3.3 | Containerization Excellence | ◐ F1 fixed, F2 open | case-insensitive now; CI-only Dockerfiles deliberately still excluded (needs a scope decision, see Phase 1 footnote) |
| 4.3.3 | FAIR4RS / Reproducibility Docs | ⚪ F1, F2 | literal path lists |
| 4.3.4 | **Documentation Completeness** | 🔴 F1, ⚪ F6 | **#54** — `Docs` fixed; `DOC/` still misses |
| 4.3.5 | Portable Build System | ✅ fixed | `GNUmakefile.in`, `Makefile.am` (6 autotools repos) both now detected |
| 4.3.5 | Platform Documentation | ⚪ F6 | README platform-name regex |
| 4.3.5 | Deployment Environment Testing | ⚪ F10 | workflow scan capped — AMReX scanned 25 of 26 |
| 4.3.6 | Complexity / Code Quality / Docs Quality | ⚪ F8 | **immune to F1–F3** — reads the recursive tree |
| 4.3.8 | SBOM / Build Provenance | ⚪ F2, F6, F10 | filename matching only; last 5 releases |
| — | Catalog repository identity | 🔴 F13 | 5 entries 404 — those projects score nothing |

---

## Remediation plan

Ordered by how much of the defect surface each phase removes, not by effort.
Phases 1–5 are mechanical; phase 6 needs a product decision.

### Phase 0 — Landed

| Fix | Class | Closes | Commit |
|---|---|---|---|
| `.rst` governance/CoC/contributing docs | F2 | — | `ec30acd` |
| `ci_cd.py` resolves real default branch | F5 | — | `8cb02a8` |
| `Tests/` capitalisation, `GNUmakefile.in` | F1, F2 | — | `8cb02a8` |
| `Docs`/`Doc` doc-directory waiver | F1 | [#54](https://github.com/corsa-center/metrics/issues/54) | `2e89d9f` |
| CalVer release tags | F6 | [#53](https://github.com/corsa-center/metrics/issues/53) | `2e89d9f` |
| Defect-trend fallback threshold | F7 | [#52](https://github.com/corsa-center/metrics/issues/52) | `2e89d9f` |

### Phase 1 — Tree-based file detection ⭐ highest value — 6/9 landed

**Removes F1 and F3 outright, and F2 wherever a candidate is genuinely
locatable by regex.**

Added `RepoTree` to [`collectors/ecosystem/base.py`](collectors/ecosystem/base.py):
one `GET /repos/{owner}/{repo}/git/trees/HEAD?recursive=1` per repository,
indexing both files and (inferred) directories case-insensitively.

- `match(candidates)` / `match_url(candidates)` — first candidate present as a
  file **or** directory, case-insensitive, real casing returned;
- `has_dir(path)` — case-insensitive directory presence;
- `find(pattern)` / `find_url(pattern)` — regex over the whole tree, for "this
  concept, any spelling" checks a literal list can't express;
- `fetch()` returns `COLLECTION_GAP` on failure, preserving existing gap
  semantics; `.truncated` is carried through for callers that want to know.

**Feasibility, measured:** 64 of 65 repositories return a complete tree.
Only `llvm/llvm-project` truncates (64,859 paths); Trilinos at 51,729 does not.
`_check_file_exists` remains available as a fallback for a truncated tree.

**This reduces request volume.** Up to ~112 per-path probes per repository
collapse into one tree call.

Migration order, by measured exposure:

| Order | Collector | Metrics fixed | Status |
|---|---|---|---|
| 1 | `usability.py` | 4.3.4 Documentation Completeness (`DOC/`) | ✅ landed |
| 2 | `outreach.py` | 4.2.5 Onboarding — 16 repos, incl. AMReX's getting-started guide (#49) | ✅ landed |
| 3 | `reproducibility.py` | 4.3.3 Environment, Containers — case-insensitive + single-fetch | ✅ landed¹ |
| 4 | `dev_tooling.py` | 4.3.2 Testing (`TESTING/`, `TEST/`, vendored frameworks) | ✅ landed |
| 5 | `accessibility.py` | 4.3.5 Portable Build (`Makefile.am`, 6 repos) | ✅ landed |
| 6 | `reliability.py` | 4.3.1 Static Analysis configs; CERT flag-file scan now whole-tree (#51) | ✅ landed |
| 7 | `fair_licensing.py` | 4.2.2 `citation.cff` | 🔲 todo |
| 8 | `welcomeness.py` | 4.2.6 roadmap / meeting notes | 🔲 todo |
| 9 | remaining 6 collectors | latent exposure | 🔲 todo |

¹ **Containers/Environment Management are case-insensitive and single-fetch
now, but still exact-path matching, not regex.** Broadening them to "found
anywhere in the tree" was deliberately *not* done: the probe's F2 counts for
these two categories included CI-only artifacts (e.g. kokkos's
`scripts/docker/Dockerfile.gcc-10`, built to test compilers, not shipped for
users) that arguably shouldn't count toward a *reproducibility* signal the
way a root `Dockerfile` does. Broadening needs a product decision about which
nested locations legitimately count; case-insensitivity and the tree-based
fetch were the parts safe to land without one.

**Also found while migrating `reliability.py`'s CERT flag-file scan:**
broadening `_find_flag_files` to search the whole tree (instead of four fixed
directories) initially let `SECURITY.md` ("secur") and a CI workflow named
`flag_prs_to_master.yml` ("flag") match the keyword regex by name alone,
crowding out genuine `.cmake` files within the small 4-file result cap on
Trilinos. Fixed by restricting the whole-tree search to `.cmake` files
specifically, which is what the four original directories implied in the
first place. Worth remembering as a general risk when Phase 2 broadens the
CI-workflow read the same way: a wider net needs a correspondingly tighter
filter, or a small result cap silently fills with noise.

### Phase 2 — Read every CI workflow

**Removes F4 — 62% of the portfolio.**

- Drop `_ANALYSIS_WORKFLOW_HINT`; enumerate `.github/workflows/*` from the tree
  (already available via `RepoTree.find(r"^\.github/workflows/.*\.ya?ml$")`).
- Spend the read budget on the largest or most recently modified workflows
  rather than name-matched ones.
- Where a cap still binds, report a **partial scan** rather than a confident
  absence — an `F11`-correct result.

### Phase 3 — Scheme detection instead of exact formats

**Removes the remaining 16 F6 repos.**

In `_versioning_scheme`, strip a leading non-numeric prefix and normalise
separators before parsing:

```
llvmorg-23.1.1            → 23.1.1
trilinos-release-17-2-1   → 17.2.1
papi-7-2-0-t              → 7.2.0
gex-2025.8.0              → 2025.8.0
vstable_2026_09_04        → 2026.09.04
```

Then audit the other exact-format regexes for the same rigidity: award numbers
(4.2.8), defect labels (4.3.1), README headings (4.3.4), platform names (4.3.5).

### Phase 4 — Audit every `== 0` fallback guard

**Removes latent F7.** `grep -rn "== 0" collectors/` and check each one against
"is this enough to answer the question?" rather than "is this empty?".

### Phase 5 — Validate the catalog before every run

**Removes F13.** Add a preflight to `orchestrator.prepare_software_list()`:

- resolve every repository URL; GitHub returns `301` with the new location on
  rename, so renames can be followed automatically and logged;
- fail loudly on `404` rather than collecting zeros.

Known corrections to apply now:

| Catalog entry | Correct location |
|---|---|
| `vtk/vtk` | `Kitware/VTK` (3,211★) |
| `paraview/paraview` | `Kitware/ParaView` (1,702★) |
| `RAJA-llnl/RAJA` | `LLNL/RAJA` (599★) |
| `Umpire-project/Umpire` | `LLNL/Umpire` (422★) |
| `xsdk-project/xsdk` | no such repository — needs a product decision |

> A project whose URL is wrong is not scored badly. It is scored on nothing,
> which is the version of this that maintainers notice fastest.

### Phase 6 — For F8/F9, change the claim, not the threshold

Where the available signal genuinely does not measure the thing, the honest move
is to mark it **not measurable** rather than publish a low score. The framework
already has that convention for gaps; these are gaps.

| Issue | Metric | Proposal |
|---|---|---|
| [#48](https://github.com/corsa-center/metrics/issues/48) | Engagement Quality | Exclude issues whose author *and* closer are both inside the maintainer group from the comment-count median — they are triage records, not conversations. |
| [#48](https://github.com/corsa-center/metrics/issues/48) | Community Participation | Same exclusion applied to the denominator, so an internal tracker is not read as absent community. |
| [#50](https://github.com/corsa-center/metrics/issues/50) | Collaboration Network | Add a source-level signal (GitHub code search for `AMREX_HOME`-style includes or submodule references), or mark registry-only measurement not applicable for source-included libraries. |
| [#50](https://github.com/corsa-center/metrics/issues/50) | Advanced Dependency Analysis | Drop `go` ecosystem entries carrying zero dependents for non-Go projects; query conda-forge directly rather than relying on the ecosyste.ms index. |

### Phase 7 — Keep the probe, run it continuously

This analysis found more than the maintainers reported, because it tested the
heuristics against reality instead of waiting for complaints. Make that
permanent:

- promote the probe to `tools/portfolio_probe.py`;
- run weekly in CI over the full catalog;
- for every path- or format-matching heuristic, assert that the count of
  repositories where *the concept is present but unmatched* stays at zero;
- a new false-negative class then shows up as a failing check rather than as a
  maintainer's issue.

---

## Issue mapping

| Issue | Metric | Class | Phase | Status |
|---|---|---|---|---|
| [#48](https://github.com/corsa-center/metrics/issues/48) | Engagement Quality / Community Participation | F9 | 6 | 🔲 Todo |
| [#49](https://github.com/corsa-center/metrics/issues/49) | Onboarding Infrastructure | F1, F2 | 1 | ✅ Fixed |
| [#50](https://github.com/corsa-center/metrics/issues/50) | Collaboration Network Analysis | F8, F12 | 6 | 🔲 Todo |
| [#51](https://github.com/corsa-center/metrics/issues/51) | CERT Guidelines Compliance | F3, F4 | 1 + 2 | ◐ flag-file scan fixed (F3); workflow gate pending (F4) |
| [#52](https://github.com/corsa-center/metrics/issues/52) | Reliability Trend Analysis | F7 | 0 | ✅ Fixed |
| [#53](https://github.com/corsa-center/metrics/issues/53) | Version Control Best Practices | F6 | 0 + 3 | ◐ CalVer fixed; prefixed tags pending |
| [#54](https://github.com/corsa-center/metrics/issues/54) | Documentation Completeness | F1 | 0 + 1 | ◐ `Docs`/`DOC/` fixed; heading-regex rigidity (F6) pending |
