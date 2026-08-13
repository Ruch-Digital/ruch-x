# Audit criteria

Every criterion, its weight, its threshold, and where the threshold comes from.

All of it lives in one function — `auditoria()` in `scripts/render.py` — on
purpose. In an audit the criterion is something the client argues with, not
something a black box hands down.

## How a grade is computed

Each criterion returns one of three states:

| State | Effect |
|---|---|
| met | its weight counts toward both the numerator and the denominator |
| not met | its weight counts toward the denominator only, and it produces a line in the action plan |
| **not audited** (`null`) | it is removed from the denominator entirely |

```
grade % = sum(weights met) / sum(weights of criteria that were audited)
```

| Grade | Range |
|---|---|
| A | ≥ 90 % |
| B | ≥ 75 % |
| C | ≥ 60 % |
| D | ≥ 40 % |
| F | below 40 % |

If **no** criterion in an axis could be measured, the denominator is zero and
the axis gets **no letter** — the card shows a dash and "não auditado". It is
never an `F`. A tool that reports failure because it could not look is worse
than a tool that says nothing.

Each unmet criterion becomes a row in the action plan with a priority (`P0`,
`P1`, `P2`), the finding, and the fix. P0 is reserved for a committed secret.

### The one deliberate exception

**Missing coverage counts as a failure, not as "not audited."** Every other
criterion drops out of the grade when it cannot be measured. Coverage does not:
if no coverage report exists anywhere in the repository, the criterion fails and
produces a P1.

Not measuring coverage is a choice with a consequence — nobody knows what the
suite protects. That is a finding about the project, not a limitation of the
tool.

---

## Delivery — DORA

Source: DORA / *Accelerate* / State of DevOps. Derived from GitHub Actions
history plus git; nothing is instrumented in the project.

A run counts as a deploy when its workflow name matches one of
`deploy_keywords`, it ran on the production branch, its event was `push` or
`workflow_dispatch`, and it concluded as success or failure.

Performance bands (`NIVEIS_DORA` in `render.py`):

| Metric | Elite | High | Medium | Low |
|---|---|---|---|---|
| Deploys per week | ≥ 7 | ≥ 1 | ≥ 0.25 | below |
| Lead time p50 (commit → production) | ≤ 24 h | ≤ 168 h | ≤ 720 h | above |
| Change failure rate | ≤ 15 % | ≤ 30 % | ≤ 45 % | above |
| Time to restore (MTTR) | ≤ 1 h | ≤ 24 h | ≤ 168 h | above |

A criterion is met at **elite or high**.

| Criterion | Weight | Met when | Not audited when |
|---|---|---|---|
| Deploy frequency | 3 | ≥ 1 deploy/week | no deploy run identified |
| Lead time p50 | 3 | ≤ 168 h | no successful deploy whose commit is in this clone |
| Change failure rate | 3 | ≤ 30 % | no deploy run identified |
| Time to restore | 2 | ≤ 24 h | no failure followed by a green run |

Lead time is measured from the commit date to the **end** of the run that
shipped it, and samples outside `0 ≤ h < 720` are discarded as clock noise.
MTTR is the median gap from a failed deploy to the next green deploy of the same
workflow.

Without `gh` the whole axis is unmeasurable, and it correctly gets no letter.

## Quality

| Criterion | Weight | Met when | Not audited when |
|---|---|---|---|
| Coverage | 4 | ≥ 70 % | never — see the exception above |
| Complexity | 3 | fewer than 5 % of analysed blocks score above 10 | `radon` did not run, or no block was analysed |
| Top friction file under control | 3 | the most-changed file scores below 150 | no hotspot (no git history, or no source file changed in the window) |

Cyclomatic complexity above 10 per function is the conventional line from
McCabe onward, and it is what `radon` ranks against. The 5 % is about
distribution, not about individual functions: a handful of complex functions in
a large codebase is normal, a fifth of them is a structural problem.

Coverage carries the largest weight in the axis because it is the only one that
says anything about whether a change is safe to make.

## Security

Sources: OWASP for the credential and configuration items, SLSA for supply
chain.

| Criterion | Weight | Priority | Met when | Not audited when |
|---|---|---|---|---|
| No committed secret | 5 | P0 | the scan found nothing | `git ls-files` failed — nothing was scanned |
| Actions pinned to a SHA | 3 | P1 | no `uses:` with a moving ref | the `governance` collector did not run |
| Workflows declare `permissions` | 2 | P2 | every workflow has a top-level `permissions:` | idem |
| Dependencies up to date | 2 | P2 | fewer than 25 % outdated | `pip`/`npm` could not produce both counts |
| Automated dependency updates | 2 | P2 | `.github/dependabot.yml` or `.github/renovate.json` exists | the `governance` collector raised |
| Framework security warnings | 3 | P1 | `check --deploy` reported no `security.*` | `check --deploy` did not run, **or** `[django] settings_module` is unset |

Three details that decide whether the report is believable:

- **The secret scanner only looks at tracked files** (`git ls-files`). A secret
  in a local `.env` never leaked; a committed one is in the history forever, even
  after deletion.
- **Test files and placeholders are filtered out.** Fixtures, `conftest.py`,
  factories and values matching `test`/`example`/`changeme`/`<...>`/`${VAR}` are
  not reported. One false alarm discredits every other finding in the report.
- **A `check --deploy` run against dev settings is not evidence about
  production.** `DEBUG=True` and missing HSTS are expected on a developer
  machine. Without `[django] settings_module` pointing at the production
  settings, the criterion reports as *não auditado* rather than failing a system
  that may well be configured correctly.

An action referenced by a moving tag (`@v4`) runs whatever its owner publishes
tomorrow, inside your CI, with your secrets. That is why pinning is weighted
above `permissions`.

## Reliability

Source: Google SRE for the operational items.

| Criterion | Weight | Met when | Not audited when |
|---|---|---|---|
| CI green | 3 | success rate ≥ 85 % | no GitHub Actions data |
| Operational runbook | 3 | `docs/runbooks/`, `runbooks/` or `docs/deploy/runbooks/` contains any `.md` | the `governance` collector raised — see the Process axis |
| Migrations applied | 2 | no pending migration | `showmigrations` failed, the project has no `manage.py`, or the toml's `manage_py` does not resolve inside the root |
| Observable infrastructure | 2 | container or database metrics were collected | nothing was collected — this criterion never fails, it only counts when present |

Below 85 % green, a team learns to ignore red. That is the reason the threshold
is where it is: an unstable pipeline is worse than no pipeline, because it
trains people to merge past it.

**On migrations:** the criterion used to be counted as met in any repository
without a `manage.py`, because "no Django" and "no pending migration" both
arrived as an empty list. They are now distinguished: with no `manage.py` the
field is `null` and the reason says so, and a `manage_py` configured in the toml
that does not resolve gets a different reason — a typo in the config must not
look like a project that simply is not Django.

## Process

| Criterion | Weight | Met when | Not audited when |
|---|---|---|---|
| Production branch protected | 4 | the GitHub API reports branch protection | `gh` is absent, the repo has no GitHub remote, or the API call failed for any reason other than 404 |
| README | 2 | `README.md`/`.rst`/`.txt` exists | the `governance` collector raised |
| Documented decisions | 2 | `docs/adr/`, `docs/decisions/`, `adr/`, `docs/decisoes/` or `docs/` contains `.md` | idem |
| License | 1 | `LICENSE` (any common extension) exists | idem |
| Pre-commit hooks | 1 | `.pre-commit-config.yaml` exists | absent — counts as not audited, never as a failure |
| Changelog | 2 | `CHANGELOG.md` or `docs/CHANGELOG.md` exists | absent — same |

Branch protection is the heaviest single criterion of the axis because it is the
only one that does not depend on anyone's discipline.

**The file-existence criteria separate "the file is missing" from "nobody
looked."** README, documented decisions, license and the operational runbook are
read out of the `governance` collector, where a missing field looks exactly like
a missing file. The grade therefore checks the collector first: when `governance`
is absent from the snapshot — it raised, and the error is in `errors.governance`
— those four report as *não auditado*, carry that error as the reason on the
card, and leave the denominator. When the collector ran, an absent file is a
finding, as it should be. Without the distinction, one exception in a collector
produced four accusations ("no README", "no license", "no documented decisions",
"no runbooks") about a project nobody had looked at.

A **404** from the protection endpoint is the answer that matters: the branch has
no protection. Any other failure — 403, rate limit, network — leaves the
criterion unaudited. Reporting "unprotected" because the request failed would be
an accusation without a look.

---

## The "what to look at first" list

Separate from the graded axes, `findings()` in `render.py` turns raw numbers
into ranked observations (alto / médio / baixo / info). Its thresholds:

- coverage below 50 % → alto; below 70 % → médio
- pending migrations → médio (the check ran against *this* environment's
  database, which on a dev machine usually means a forgotten `migrate`)
- `security.*` warnings with production settings → alto; with dev settings →
  info, explicitly labelled as normal
- library configuration errors from `check` (`E` codes outside `security.*`) →
  médio
- PostgreSQL cache hit below 95 % → alto (usually `shared_buffers`)
- table with more than 20 % dead rows and more than 10k of them → médio
- table over 5k rows whose `seq_scan` exceeds `idx_scan` tenfold → médio
- non-unique index over 512 KB with fewer than 50 scans → baixo
- CI green below 85 % → médio
- any function above complexity 10 → baixo
- more than 200 lint violations → baixo
- each collector that raised → info, with its error message

Adjust them if the project's context calls for it — but tell the reader you did.
