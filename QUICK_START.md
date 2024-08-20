# 🚀 Quick Start Guide - Citation Metrics Collector

**5-Minute Setup & Test**

---

## Step 1: Setup (2 minutes)

```bash
# Navigate to project
cd /home/brtnfld/work/metrics

# Activate virtual environment
source venv/bin/activate

# Verify installation
python -c "import httpx; print('✓ Ready!')"
```

---

## Step 2: Test with NumPy (1 minute)

```bash
# Run basic test
python test_citation.py
```

**Expected output:**
```json
{
  "score": 60.0,
  "formal_citations": 18878,
  "informal_mentions": 100
}
```

---

## Step 3: Detailed API Test (2 minutes)

```bash
# Run detailed test
python test_citation_detailed.py
```

**Expected output:**
```
Testing Individual API Integrations
1. Semantic Scholar... ✓ 16,479 citations
2. OpenAlex...         ✓ 18,878 citations
3. Zenodo...           ✓ API working
```

---

## Step 4: Generate CORSA Data (Optional)

```bash
# Test with 3 repositories
python scripts/generate_corsa_citations.py \
  --catalog /tmp/corsa-dashboard/explore/github-data/intRepo_Metadata.json \
  --limit 3

# View results
cat output/citationSummary.json
```

---

## 📋 Common Commands

### Basic Testing
```bash
# Simple test
python test_citation.py

# Detailed test with API breakdown
python test_citation_detailed.py
```

### CORSA Integration
```bash
# Test with 3 repos
python scripts/generate_corsa_citations.py \
  --catalog /path/to/intRepo_Metadata.json \
  --limit 3

# Full catalog (all repos)
python scripts/generate_corsa_citations.py \
  --catalog /path/to/intRepo_Metadata.json \
  --output citationMetrics.json
```

### View Results
```bash
# Summary statistics
cat output/citationSummary.json

# Detailed metrics
cat output/citationMetrics.json | jq

# Specific repository
cat output/citationMetrics.json | jq '.["numpy/numpy"]'
```

---

## 🔑 Environment Variables (Optional)

For better results, set these before running:

```bash
# GitHub token (for dependent packages)
export GITHUB_TOKEN="ghp_xxxxxxxxxxxx"

# Semantic Scholar API key (for higher rate limits)
export SEMANTIC_SCHOLAR_KEY="your_key_here"

# OpenAlex email (for polite pool)
export OPENALEX_EMAIL="your-email@example.com"
```

---

## 📊 What Gets Collected

For each repository:

| Metric | Source | Weight |
|--------|--------|--------|
| **Formal Citations** | Semantic Scholar + OpenAlex | 40% |
| **Informal Mentions** | Semantic Scholar search | 20% |
| **Dependent Packages** | GitHub API | 30% |
| **DOI Resolutions** | Zenodo | 10% |

**Final Score:** 0-100 (weighted average)

---

## 🛠️ Troubleshooting

### Rate Limit Errors (429)

**Problem:** Too many API requests

**Solution:**
```bash
# Add delay between requests
python scripts/generate_corsa_citations.py --rate-limit 5.0
```

### Missing Citations

**Problem:** No citations found

**Solution:**
- Check if DOI exists in `catalog/doi_mapping.json`
- Verify repository name is correct
- Some repos may genuinely have no academic papers

### GitHub Authentication Failed

**Problem:** 401 error from GitHub

**Solution:**
```bash
# Set GitHub token
export GITHUB_TOKEN="your_token_here"

# Or edit test_citation.py and add your token
```

---

## 📁 Key Files

| File | Purpose |
|------|---------|
| `test_citation.py` | Basic test script |
| `test_citation_detailed.py` | Detailed API test |
| `scripts/generate_corsa_citations.py` | CORSA integration |
| `collectors/impact/citation.py` | Main collector |
| `catalog/doi_mapping.json` | DOI mappings |

---

## 📖 Full Documentation

- **[README.md](README.md)** - Complete documentation
- **[CORSA_INTEGRATION_PLAN.md](CORSA_INTEGRATION_PLAN.md)** - Integration guide
- **[SUMMARY.md](SUMMARY.md)** - Project summary

---

## ✅ Success Checklist

- [ ] Virtual environment activated
- [ ] Basic test runs successfully
- [ ] Detailed test shows API results
- [ ] CORSA integration script tested
- [ ] Results in `output/` directory

---

## 🎯 Next Steps

1. ✅ **You've completed basic testing!**

2. **To integrate with CORSA:**
   - Read [CORSA_INTEGRATION_PLAN.md](CORSA_INTEGRATION_PLAN.md)
   - Add DOI mappings for your repositories
   - Run full catalog collection
   - Copy output to CORSA dashboard

3. **To customize:**
   - Edit weights in `test_citation.py`
   - Add new API integrations in `integrations/`
   - Modify normalization in `citation.py`

---

**Status:** ✅ Ready to use!

*For questions: Read [README.md](README.md) or check [SUMMARY.md](SUMMARY.md)*
