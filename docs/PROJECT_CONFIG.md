# Project metric configuration

A tracked project can narrow which sub-collectors run against its own repo by
adding `.corsa/metrics.yaml` at the root of that repo. This closes
[#24](https://github.com/corsa-center/metrics/issues/24).

```yaml
schema: 1
repo: HDFGroup/hdf5          # must match the repo being collected, case-insensitively

collectors:
  quality:
    supply_chain: false      # stop the 4.3.8 collector from running for this repo

overrides:
  "4.2.8":
    NIH R50 Award Tracking: "N/A — not US-funded"
```

## Two independent knobs

- **`collectors`** stops a sub-collector from running at all for this
  package: no API calls, and the section it feeds drops out of the dimension
  average rather than scoring zero -- the same effect as the operator's
  global `ecosystem_collectors:` / `quality_collectors:` toggles in
  `config/orchestrator.yaml`, just scoped to one repo. Boolean only.
- **`overrides`** rewrites the displayed text of a specific sub-metric row,
  whether that row was actually collected or is part of a "not yet collected"
  stub. Keys are section numbers; values map the exact sub-metric label (as
  it appears in the rendered output, or the canonical label from
  `SECTION_SUBMETRICS` in `orchestrator.py` for a fully-stubbed section) to
  replacement text.

These don't imply each other. Disabling a collector via `collectors` does not
by itself produce a visible reason -- the section (or the rows only that
collector fed) simply goes quiet, exactly like a global disable. If you want
a labeled "N/A — reason" instead, set it explicitly via `overrides`; you can
do either independently of the other.

## Precedence

Three layers, each only able to narrow the one before it -- none can
re-enable a collector a higher layer turned off:

1. **Global** `config/orchestrator.yaml` -- applies to every package.
2. **Maintainer-authored** `package_config/<owner>_<repo>.yaml` (this repo).
   Same `collectors:` / `overrides:` shape as above.
3. **Project-authored** `.corsa/metrics.yaml`, fetched from the project's own
   repo at collection time.

For `overrides`, layer 2 (central) wins over layer 3 (project) on a
conflicting section/label -- a maintainer's text replaces a project's for
the same row.

For `collectors`, there is no "wins" direction: every layer can only turn a
sub-collector *off*, never back on, so layers 2 and 3 are two independent
ways to narrow further, not an override relationship. Nothing in this
mechanism lets a maintainer force a sub-collector to keep running once the
project's own file has disabled it -- disable it centrally in
`package_config/`? No, that also can't re-enable it. The only lever an
operator has over a project's `collectors:` choice is the global kill switch
below (`project_config.enabled: false`), which stops reading the project's
file at all.

## Code-hosting platform (`repo_type`)

By default the platform is inferred from the repo URL's hostname: github.com
is GitHub, gitlab.com and every host listed under `api_credentials.gitlab` in
`config/orchestrator.yaml` are GitLab, and anything else is skipped.

A maintainer can set it explicitly in `package_config/<owner>_<repo>.yaml`:

```yaml
package: vtk/vtk-m
repo_type: gitlab   # github | gitlab
```

- **`gitlab`** uses the GitLab API on the repo URL's own host. This works for
  a self-hosted instance even when it isn't listed under
  `api_credentials.gitlab`. An unlisted host is queried without a token.
- **`github`** is accepted only for github.com. GitHub Enterprise hosts are
  skipped because the GitHub forge talks to api.github.com only.
- Any other value is logged and the package is skipped.

`repo_type` is read only from `package_config/`, never from the project's
own `.corsa/metrics.yaml`. That file is fetched *through* the forge, so it
can't be the thing that chooses the forge.

## Fails open

If `.corsa/metrics.yaml` is missing, unreachable, not valid YAML, declares an
unsupported `schema`, its `repo:` field doesn't match the package being
collected, or the package's repo isn't on a recognized forge (see
`ORCHESTRATOR_GUIDE.md`'s "Supported hosts" section), it is ignored entirely
and every collector runs as if the file didn't exist. A project cannot break
its own metrics collection by getting this file wrong.

## Provenance

Every package's output includes `config_exclusions`, listing which toggle
keys were turned off by `package_config/` or `.corsa/metrics.yaml`
specifically -- as opposed to a collector that crashed, which leaves the same
gap in `sub_results` but isn't a deliberate exclusion. Use this to tell the
two apart when a section is unexpectedly empty.

## Operator kill switch

Set `project_config.enabled: false` in `config/orchestrator.yaml` to stop
fetching every project's `.corsa/metrics.yaml` (e.g. to pause the mechanism
ecosystem-wide). This does not affect `package_config/`, which is
operator-authored regardless.
