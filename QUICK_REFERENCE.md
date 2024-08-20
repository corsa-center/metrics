# Quick Reference Guide

## What Was Built This Session

### 1. CASS Catalog Sync ✅
- **Script:** `collectors/catalog_sync.py`
- **Purpose:** Sync CORSA Dashboard with CASS software catalog
- **Status:** Complete, tested, working
- **Result:** Dashboard updated with all 36 CASS GitHub repos

### 2. Community Metrics ✅
- **Collector:** `collectors/community/community_health.py`
- **Test Script:** `test_community_metrics.py`
- **Purpose:** Check for CoC, Governance, Contributing docs
- **Status:** Complete, tested on HDF5
- **Result:** HDF5 scored 2/3 (66.67%)

---

## Quick Commands

### Run CASS Sync
```bash
cd /home/brtnfld/work/metrics
python3 collectors/catalog_sync.py
```

### Run Community Metrics
```bash
cd /home/brtnfld/work/metrics
export GITHUB_TOKEN="your_token"  # Optional
python3 test_community_metrics.py
```

### View Results
```bash
# Community metrics summary
cat output/community_metrics_summary.json

# Full community metrics
jq '.[17]' output/community_metrics.json  # HDF5 entry

# Dashboard repo count
jq '.["https://github.com"].repos | length' /home/brtnfld/work/dashboard/_explore/input_lists.json
```

---

## File Locations

### Metrics Collection
```
/home/brtnfld/work/metrics/
├── collectors/
│   ├── catalog_sync.py                    # CASS sync
│   └── community/community_health.py      # Community metrics
├── test_community_metrics.py              # Test script
└── output/
    ├── community_metrics.json             # Results
    └── community_metrics_summary.json     # Summary
```

### Dashboard
```
/home/brtnfld/work/dashboard/
└── _explore/
    ├── input_lists.json           # Updated (not committed)
    └── input_lists.json.backup    # Backup
```

### Documentation
```
CASS_SYNC_SUMMARY.md      # CASS sync details
SYNC_COMPLETE.md          # CASS completion report
COMMUNITY_METRICS.md      # Community metrics guide
SESSION_SUMMARY.md        # Complete session history
QUICK_REFERENCE.md        # This file
```

---

## Key Results

### CASS Sync
- ✅ 45 CASS packages extracted
- ✅ 36 unique GitHub repos mapped
- ✅ 100% CASS coverage in dashboard
- ✅ 50 total GitHub repos, 4 GitLab repos

### HDF5 Community Metrics
- ✅ Code of Conduct: FOUND
- ❌ Governance: NOT FOUND
- ✅ Contributing: FOUND
- **Score: 2/3 (66.67%)**

### Other 49 Packages
- Status: Placeholder only (loop demonstrated)
- Ready to expand by removing filter

---

## Expand to All Packages

To collect community metrics for ALL packages:

**Edit:** `test_community_metrics.py` line 51

**Change from:**
```python
if package['name'].lower() == 'hdf5':
```

**Change to:**
```python
if True:  # Collect for all
```

---

## Next Actions (Not Done)

1. Commit dashboard changes
2. Expand community metrics to all packages
3. Add more metrics (issue templates, security policy, etc.)
4. Integrate with dashboard visualization
5. Set up automated collection

---

## GitHub Token

Set for better rate limits:
```bash
export GITHUB_TOKEN="ghp_your_token_here"
```

**Limits:**
- Without token: 60 requests/hour
- With token: 5,000 requests/hour

---

## All 50 Software Packages

ascent, amrex, aml, caffeine, gasnet, upcxx, cass-community-new, libceed, chipstar, darshan, deephyper, diy, dyninst, e4s, empirical-roofline-toolkit, flang, ginkgo, **hdf5** (✓ collected), hpctoolkit, hypre, magma, papi, kokkos, kokkos-kernels, libensemble, sundials, zfp, llvm-project, mfem, ompi, openaccv-v, openmp_vv, adios2, pnetcdf, spack, strumpack, superlu_dist, tau2, trilinos, visit, viskores, superlu, superlu_mt, xsdk-project, xsdk-issues, mpich, petsc, paraview, albany

---

**For Complete Details:** See `SESSION_SUMMARY.md`
