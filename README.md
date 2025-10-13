# CASS Metrics Collection Framework

A system for collecting and analyzing software sustainability metrics for scientific open-source software.

## Overview

This framework collects metrics from multiple sources and integrates with the [CORSA Sustainability Dashboard](https://corsa.center/dashboard/).

### Key Features

- **Multi-Source Data Collection**: GitHub, Semantic Scholar, OpenAlex, Zenodo
- **Orchestrated Workflows**: Configurable collection pipelines
- **CASS Framework**: Four dimensions - Impact, Community, Viability, Quality
- **Dashboard Integration**: Generate JSON data for CORSA dashboard
- **Automated Collection**: GitHub Actions workflows
- **Extensible Framework**: Placeholder metrics for incremental implementation

## Quick Start

### Prerequisites

- Python 3.11+
- Git

### Installation

```bash
# Clone repository
git clone https://github.com/brtnfld/metrics
cd metrics

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### Basic Usage

#### Using the Orchestrator

```bash
# Configure your workflow
cp config/api_credentials.yaml.example config/api_credentials.yaml
# Edit config/api_credentials.yaml with your API keys

# Run orchestrator
python orchestrator.py config/orchestrator.yaml
```

#### Generate Citation Metrics

```bash
# Set environment variables (optional but recommended)
export GITHUB_TOKEN="your_github_token"
export SEMANTIC_SCHOLAR_KEY="your_api_key"
export OPENALEX_EMAIL="your-email@example.com"

# Generate metrics
python scripts/generate_corsa_citations.py \
  --catalog config/software_catalog.yaml \
  --output output/citationMetrics.json
```

## CASS Dimensions

The framework follows the CASS (Consortium for Advancement of Scientific Software) sustainability model with four main dimensions:

| Dimension | Status | Description |
|----------|--------|-------------|
| **Impact** | ✅ Implemented | Software citation, adoption, and field research impact |
| **Community** | 🔄 Placeholder | Community health, engagement, and diversity |
| **Viability** | ✅ Implemented | Long-term sustainability, security, and licensing |
| **Quality** | 🔄 Placeholder | Documentation, code quality, testing, and usability |

Each dimension contains multiple sub-categories and metrics that contribute to an overall sustainability score.

## Project Structure

```
metrics/
├── collectors/              # CASS dimension collectors
│   ├── impact/
│   │   ├── citation.py     # Citation metrics (✅ implemented)
│   │   └── dimension.py    # Impact dimension (🔄 placeholder)
│   ├── community/
│   │   ├── community_health.py  # Legacy community health collector
│   │   └── dimension.py         # Community dimension (🔄 placeholder)
│   ├── viability/
│   │   ├── licensing.py         # License analysis (✅ implemented)
│   │   └── dimension.py         # Viability dimension (🔄 placeholder)
│   ├── quality/
│   │   └── dimension.py         # Quality dimension (🔄 placeholder)
│   └── catalog_sync.py          # Catalog synchronization
│
├── integrations/            # API integrations
│   ├── base.py             # Base API client
│   ├── github_api.py       # GitHub API
│   ├── semantic_scholar.py # Semantic Scholar API
│   ├── openalex.py         # OpenAlex API
│   └── zenodo.py           # Zenodo API
│
├── scripts/
│   └── generate_corsa_citations.py  # CORSA integration
│
├── config/
│   ├── orchestrator.yaml          # Workflow configuration
│   ├── software_catalog.yaml      # Software catalog
│   └── api_credentials.yaml.example
│
├── .github/workflows/
│   └── collect-and-sync.yml      # Automated collection
│
└── orchestrator.py          # Main orchestrator
```

## Configuration

Environment variables (all optional for better rate limits):

| Variable | Description |
|----------|-------------|
| `GITHUB_TOKEN` | GitHub personal access token |
| `SEMANTIC_SCHOLAR_KEY` | Semantic Scholar API key |
| `OPENALEX_EMAIL` | Email for OpenAlex polite pool |
| `ZENODO_TOKEN` | Zenodo access token |

See [CONFIGURATION.md](CONFIGURATION.md) for detailed setup.

## Documentation

- [CONFIGURATION.md](CONFIGURATION.md) - Configuration details
- [ORCHESTRATOR_GUIDE.md](ORCHESTRATOR_GUIDE.md) - Orchestrator usage
- [QUICK_START.md](QUICK_START.md) - Getting started guide
- [QUICK_REFERENCE.md](QUICK_REFERENCE.md) - Command reference
- [CASS-Sustainability-Metrics-Report.pdf](CASS-Sustainability-Metrics-Report.pdf) - CASS framework specification

## API Integrations

- **Semantic Scholar**: Academic citations
- **OpenAlex**: Citation database
- **Zenodo**: DOI resolution and downloads
- **GitHub**: Repository metadata and dependents

## License

MIT License - See [LICENSE](LICENSE) for details.

## Contact

- **CORSA Dashboard**: info@corsa.center
- **Issues**: [GitHub Issues](https://github.com/brtnfld/metrics/issues)
