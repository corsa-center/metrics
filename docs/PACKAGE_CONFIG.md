# Package configuration

Tracked packages are obtained from yaml files, one file per package. Currently
packages files are obtained from the [`metrics_data`](https://github.com/corsa-center/metrics_data) repository.

Package files contain mandatory fields and optional fields. Additionally, a package file can override
collectors and metrics output. The contents of the file are included as metadata in the `metrics.json`
file that is consumed by the dashboard. This allows the dashboard to annotate the package entry with 
required user interface elements, etc.

A tracked package must supply the following information:

```yaml
schema: 1
published: true
name: HDF5  # must be a unique name
description: "Official HDF5 \u00ae Library Repository"
repo_type: github
repo_url: "https://github.com/HDFGroup/hdf5"
default_branch: develop
primary_language: C
```

`repo_type` selects the code-hosting platform the metrics are collected
from:

- `github` -- repositories on github.com (GitHub Enterprise is not
  supported).
- `gitlab` -- gitlab.com or any self-hosted GitLab instance; the host is
  taken from `repo_url`. Add a token for a self-hosted host under
  `api_credentials.gitlab.<host>` in `config/orchestrator.yaml` to raise
  its rate limit or reach a private project.

Any other value is logged and the package is skipped, so it shows as "not
yet collected" rather than with false negatives.

It is expected that some of these fields will be automatically generated when the package file is created (TBD),
but some will, by necessity, be manually entered. e.g. `description` and `default_branch` can probably be obtained
from the GitHub repository, however the `name`, `published` and `repo_url` fields will need to be supplied manually.

The package file also allows additional information to be specified that would normally be difficult
to obtain. e.g. a package may have a different location for documentation or governance information. These
fields are optional.

```yaml
cdash: "https://my.cdash.org/index.php?project=HDF5"
documentation: "https://support.hdfgroup.org/documentation/"
homepage: "https://www.hdfgroup.org"
```

A tracked package can also narrow which sub-collectors run against its own repo using
a `collectors` and/or `overrides` sections.

```yaml
collectors:
  quality:
    supply_chain: false      # stop the 4.3.8 collector from running for this repo

overrides:
  "4.2.8":
    NIH R50 Award Tracking: "N/A — not US-funded"
```

- **`collectors`** stops a sub-collector from running at all for this
  package: no API calls, and the section it feeds drops out of the dimension
  average rather than scoring zero -- the same effect as the
  global `ecosystem_collectors:` / `quality_collectors:` toggles in
  `config/orchestrator.yaml`, just scoped to one repo. It is only possible to
  to turn a sub-collector off, if it is off at the configuration level it cannot
  be re-enabled here. Boolean only.
- **`overrides`** rewrites the displayed text of a specific sub-metric row,
  whether that row was actually collected or is part of a "not yet collected"
  stub. Keys are section numbers; values map the exact sub-metric label (as
  it appears in the rendered output, or the canonical label from
  `SECTION_SUBMETRICS` in `orchestrator.py` for a fully-stubbed section) to
  replacement text. 
- These don't imply each other. Disabling a collector via `collectors` does not
  by itself produce a visible reason -- the section (or the rows only that
  collector fed) simply goes quiet, exactly like a global disable. If you want
  a labeled "N/A — reason" instead, set it explicitly via `overrides`; you can
  do either independently of the other.

A package whose work is split across repositories can list the companions in
`related_repositories` (`owner/repo` entries). Contributor Abandonment
Forecasting then merges contributor activity across all of them, so work moving
to a companion repository isn't read as contributors leaving.

```yaml
related_repositories:
  - spack/spack-packages
```

A package that isn't ready to be shown publicly can set `published: false`. The
dashboard then asks for a password before showing that package's page, checked
against `password_sha256`, the hex SHA-256 of the password:

```yaml
published: false
password_sha256: "<output of: printf %s 'the-password' | sha256sum>"
```

This is a courtesy gate, not access control: the dashboard is a static site, so
the package's `metrics.json` (hash included) can still be fetched directly.

## Provenance

Every package's output includes `config_exclusions`, listing which toggle
keys were turned off by `package_config` specifically -- as opposed to a collector 
that crashed, which leaves the same gap in `sub_results` but isn't a deliberate 
exclusion. Use this to tell the  two apart when a section is unexpectedly empty.

