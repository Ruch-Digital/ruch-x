# Audit criteria

Every criterion, its weight, its threshold, and where the threshold comes from.

All of it lives in one function — `auditoria()` in `scripts/render.py` — on
purpose. In an audit the criterion is something the client argues with, not
something a black box hands down.

## How a grade is computed

Each criterion returns one of four states:

| State | Effect |
|---|---|
| met | its weight counts toward both the numerator and the denominator |
| not met | its weight counts toward the denominator only, and it produces a line in the action plan |
| **environment-blocked** (`NAO_MEDIDO`) | the *collection environment* prevented the measurement — database down, `gh` silent, `radon` missing, a timeout, a collector that raised. Stays in the denominator; see "Uncertainty band" below |
| **nothing to audit** (`null`) | the project genuinely has nothing there — a library with no deploy workflow, a private repo's license. Removed from the denominator entirely |

```
worst % = sum(weights met) / sum(weights of {met, not met, environment-blocked})
best  % = sum(weights met + environment-blocked) / sum(weights of {met, not met, environment-blocked})
```

An axis with nothing environment-blocked has `worst == best`: one number,
exactly as it has always been.

| Grade | Range |
|---|---|
| A | ≥ 90 % |
| B | ≥ 75 % |
| C | ≥ 60 % |
| D | ≥ 40 % |
| F | below 40 % |

The letter is computed independently at each end. If **no** criterion in an
axis was met or not met — everything in it is either environment-blocked or
nothing to audit — both ends are zero over zero, the axis gets **no letter**,
and the card shows a dash and "não auditado". It is never an `F`. A tool that
reports failure because it could not look is worse than a tool that says
nothing.

Each unmet criterion becomes a row in the action plan with a priority (`P0`,
`P1`, `P2`), the finding, and the fix. P0 is reserved for a committed secret.
An environment-blocked criterion produces its own `P2` row too — see below.

### Uncertainty band

An axis with at least one environment-blocked criterion does not report a
single measured percentage — it computes a range, worst case to best case.
Worst case treats every environment-blocked criterion as failed; best case
treats it as passed. **The card's big letter shows only the worst case** (a
letter pair read as a broken grade, and averaging the two ends would let a
degraded environment raise the grade again); the range and the ceiling are
spelled out on the card's base line — "worst-case grade: an unmeasured
criterion counts as failed — measured in full, this reaches A (60–100%)".

**The card's letter and color anchor on the worst case.** So does every trend
arrow and every comparison between snapshots — they read the pessimistic end,
never the midpoint or the optimistic one. A degraded collection environment
can therefore never raise a grade or an arrow. The card also states how many
criteria actually held up the number ("N of M criteria audited") and, when the
axis is in a band, an explicit notice that the collection environment limited
the measurement.

This closed a real defect: before the split, an environment-blocked criterion
left the denominator the same way "nothing to audit" does, and removing a
*failing* criterion from the denominator inflates the ones that remain. This
tool's own audit of a Django project scored Reliability B/75% with the
database up, A/100% with the database down (the pending-migrations criterion
could no longer be measured, and used to simply drop out), and B/80% once the
database was back up — a project could raise its grade by turning a database
off. With the band, a collection window like that no longer jumps to a full
letter step; it renders as the pessimistic `B` with an explicit "reaches A
(80–100%)" note, instead of quietly becoming an A.

**A band requires at least one measured criterion.** An axis where every
criterion is either environment-blocked or nothing to audit stays **NA** —
`F–A · 0–100%` would be technically true and useless, and would break the
existing meaning of "não auditado" (no letter, never an `F`).

**Each environment-blocked criterion also produces its own `P2` row in the
action plan** — what could not be measured, why, and how to restore it
(bring the service up, install the tool, authenticate `gh`…). Without that
row the collection gap is invisible, which is how the defect above went
unnoticed for days.

**The limit, stated plainly, the same way the Delivery axis states its own
limits below: the band describes what the *auditor's* collection environment
failed to see. It does not audit anyone's production environment** — a
database unreachable from the machine running the audit is not evidence the
production database is down, any more than it used to be evidence of health.

### The one deliberate exception

**Missing coverage counts as a failure, not as environment-blocked or nothing
to audit.** Every other criterion that cannot be measured either opens the
axis's uncertainty band (the environment failed to look) or drops out of the
grade entirely (there was nothing there to look at). Coverage does neither: if
no coverage report exists anywhere in the repository, the criterion fails
outright and produces a P1.

Not measuring coverage is a choice with a consequence — nobody knows what the
suite protects. That is a finding about the project, not a limitation of the
tool.

---

## Delivery — DORA

Source: DORA / *Accelerate* / State of DevOps. Derived from GitHub Actions
history plus git; nothing is instrumented in the project.

A run is a *deploy candidate* when its workflow name matches one of
`deploy_keywords`, it ran on the production branch, its event was `push` or
`workflow_dispatch`, and it concluded as success or failure.

**A run's conclusion is not the deploy's conclusion.** In a pipeline with
several jobs, one red `pip-audit` beside a green `Deploy staging` turns the
whole run red while the deploy shipped normally — and counting that as a
failed change measures pipeline health, not delivery. Candidates that
concluded red are therefore opened one level down (`gh run view --json
jobs`) and judged by the deploy job itself:

| Deploy job inside a red run | Counted as |
|---|---|
| concluded `success` | a successful deploy — something else is what failed |
| concluded `failure` | a failed deploy — the only case the failure rate counts |
| absent, skipped or cancelled | **not a deploy at all** — dropped from both numerator and denominator |
| impossible to check (`gh` silent) | a failed deploy — a missing answer never absolves |

Green runs are never opened: a run that concluded successfully had a
successful deploy job too. The cost is one API call for the listing plus one
per *red* run (~1.5 s each), capped at `LIMITE_VERMELHOS_CHECADOS` (20,
overridable with `[dora] limite_vermelhos`). Anything past the cap stays
counted as a failure and is reported — the audit never trims in silence. The
snapshot's `reclassificacao` block carries the whole count (how many red runs
were checked, how many had shipped anyway, how many never deployed, how many
went unanswered), so the rate can be audited instead of trusted.

Being a deploy job is inferred from the job **name**; its conclusion is
measured. A job whose name carries gate/check/lint/test never counts as a
deploy however much "deploy" appears in it — `Checks rapidos (gate de
deploy)` decides *whether* the deploy runs, it does not run it.

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
workflow — and it is empty for two opposite reasons, which the panel spells
out rather than collapsing into one dash: no deploy failed in the window
(nothing to audit), or one failed and no green deploy has followed it yet.

Three limits worth stating plainly, because this is the axis people show
to a non-technical partner:

1. **A successful deploy means the deploy step succeeded — not that the
   application is healthy.** Where shipping is delegated (a webhook to
   Coolify, ArgoCD, a queue), a 200 proves the request was accepted and
   nothing more. Nothing in this axis observes the running service.
2. **Deploy frequency still counts runs, not deploy jobs.** Checking every
   candidate job-level would cost one API call per run (~1.5 s × the whole
   window) to correct a bias that only appears if a deploy job gets skipped
   inside a green run. Known, measured, and deliberately not paid.
3. **"Production" is wherever the production branch deploys to.** A project
   that only has a staging environment gets staging numbers — the metrics
   are still meaningful, but they are not evidence of production delivery.

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
| Actions pinned to a SHA | 3 | P1 | no `uses:` with a moving ref | the `governance` collector did not run, **or** the repo has zero workflows (nothing to audit) |
| Workflows declare `permissions` | 2 | P2 | every workflow has a top-level `permissions:` | idem |

A repository with **no workflows at all** is *not* credited on the two workflow
criteria: `"no unpinned action"` with zero workflows is vacuously true, and
before 2026-08-20 it granted 5 free points over a repo with one imperfect
workflow. Zero workflows now reads as "nothing to audit" — excluded from the
score, neither reward nor penalty.
| Dependencies up to date | 2 | P2 | fewer than 25 % outdated | `pip`/`npm` could not produce both counts |
| Automated dependency updates | 2 | P2 | `.github/dependabot.yml` or `.github/renovate.json` exists | the `governance` collector raised, **or** the snapshot predates the field (key absent from `governance`) |
| Framework security warnings | 3 | P1 | `check --deploy` reported no `security.*` | **nothing to audit** when the project has no `manage.py` (it is not a Django project); not audited when `check --deploy` did not run for another reason, **or** `[django] settings_module` is unset |

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
| Migrations applied | 2 | no pending migration | **nothing to audit** when the project has no `manage.py` (it is not a Django project); not audited when `showmigrations` failed, or the toml's `manage_py` does not resolve inside the root |
| Observable infrastructure | 2 | the repository declares at least one alerting rule | the `governance` collector raised, or nothing is declared and no deploy workflow was identified |

Below 85 % green, a team learns to ignore red. That is the reason the threshold
is where it is: an unstable pipeline is worse than no pipeline, because it
trains people to merge past it.

### Observable infrastructure — read from the repository

Until 2026-08-21 this criterion asked the host: any running container, or any
answer from the database collector, and the project counted as observable. That
measured the auditor's laptop, not the project — a repository with 45 versioned
alerting rules scored the same as one with none, and a repository with nothing
passed as long as some container happened to be up. It also could never fail,
only abstain.

It now reads the repository, where alerting rules and stack configuration
actually live:

- **stack** — `prometheus`, `alertmanager`, `grafana`, `loki`, `promtail`,
  `otel`, `datadog`, `newrelic`, matched in file and directory names, plus the
  service images declared inside `docker-compose*` files;
- **alerting rules** — lines matching `alert:` as a YAML key in any `.yml` /
  `.yaml` outside `.github/` (a workflow step is not an alerting rule);
- **runbooks** — reused from the Process axis.

Heavy directories (`node_modules`, `venv`, `.git`, `dist`, `vendor`, …) are
skipped, and the walk stops at 20 000 files, reporting `truncado: true` rather
than trimming in silence.

| Repository state | Result |
|---|---|
| at least one alerting rule declared | met |
| collection stack present, zero alerting rules | **not met** — it gathers metrics and warns nobody |
| nothing declared, but a deploy workflow exists | **not met** |
| nothing declared and no deploy workflow | nothing to audit — out of the denominator |
| the `dora` collector did not run | not audited — without it there is no way to know whether the project ships |

**Only projects that ship are held to this.** If DORA identified a deploy
workflow, the project reaches an environment and needs a way to learn that it
broke. A library that deploys nowhere has nothing to observe, and is treated the
same way as a repository with no workflows in the Security axis: out of the
denominator, neither rewarded nor punished.

**The limit, stated plainly: this measures declaration, not operation.** A
versioned `slos.yml` does not prove Prometheus is running, an alert firing, or
anyone reading it. That is the nature of auditing a repository — and it is still
far closer to the truth than asking the host whether any container is up.

**On cancelled runs:** the rate is, and stays, successes over *concluded* runs
(`success + failure`) — cancelling is not the same as failing, and is often a
run superseded by a later push. But a cancelled run does not confirm the
pipeline passed either, and hiding it let "CI green 100 %" cover a real
incident: a test job cancelled 26 minutes in, while a job named like a
deploy, in the *same* run, had already shipped to staging. The collector now
records how many runs were cancelled in the window; the criterion's label
shows the count when it is greater than zero (`CI green (100 % · 1
cancelled)`), and `findings()` adds a line explaining that a cancelled run is
not evidence of a green pipeline.

When it can tell cheaply, it also flags the sharper case: a cancelled run
that had, in the same run, a job whose *name* suggests deploy with
conclusion `success`. That wording is deliberate — the job's `success` is
measured, but "it was a deploy job" is inferred from its name, and a job
name is a weak signal on its own. A gate job that only decides *whether* a
deploy runs is not the deploy: the collector excludes any job name
containing `gate`, `check`, `checks`, `lint` or `test` from this check even
when it also contains `deploy` (a real false positive found in review: a job
named "Checks rápidos (gate de deploy)"). When a name is ambiguous beyond
that, the check simply does not fire — a missed real deploy costs less here
than a false accusation. The finding stays `alto` even with the hedge: the
filter already removes the known false-positive shape, and what remains — a
cleanly deploy-named job concluding successfully while the same run's test
job never confirmed anything — is structurally the incident that motivated
this check in the first place.

**On migrations:** the criterion used to be counted as met in any repository
without a `manage.py`, because "no Django" and "no pending migration" both
arrived as an empty list. They are now distinguished: with no `manage.py` the
field is `null` and the reason says so, and a `manage_py` configured in the toml
that does not resolve gets a different reason — a typo in the config must not
look like a project that simply is not Django.

**Not being a Django project is nothing to audit, not an environment
failure (2026-08-22 ruling).** A repository with no `manage.py` used to keep
this criterion — and "framework security warnings" below — permanently
environment-blocked: a Go or Node repository was banded in Security and
Reliability forever, carrying a P2 line whose action ("bring the service up,
install the tool…") could never be performed. Not having Django is the same
kind of absence as a library with no deploy workflow or a repo with zero
workflows: the project genuinely does not have that thing, so both criteria
now read as *nothing to audit* for this specific cause. Every other reason
the same field can be unmeasured — `showmigrations`/`check --deploy` timing
out, a `manage_py` configured in the toml that does not resolve, a broken
settings module — is still environment-blocked, exactly as before; the
distinction is made against the exact reason string the collector records,
never inferred.

## Process

| Criterion | Weight | Met when | Not audited / not applicable when |
|---|---|---|---|
| Production branch protected | 4 | the GitHub API reports branch protection | `gh` is absent, the repo has no GitHub remote, or the API call failed for any reason other than 404 |
| README | 2 | `README.md`/`.rst`/`.txt` exists | the `governance` collector raised |
| Documented decisions | 2 | `docs/adr/`, `docs/decisions/`, `adr/`, `docs/decisoes/` or `docs/` contains `.md` | idem |
| License | 1 | `LICENSE` (any common extension) exists | **not applicable** when the repo's visibility is `PRIVATE`; not audited when the `governance` collector raised — when visibility could not be determined the criterion still fails an absent file, the label only notes visibility wasn't established |
| Pre-commit hooks | 1 | `.pre-commit-config.yaml` exists | the `governance` collector raised |
| Changelog | 2 | `CHANGELOG.md` or `docs/CHANGELOG.md` exists | idem |

Branch protection is the heaviest single criterion of the axis because it is the
only one that does not depend on anyone's discipline.

**The file-existence criteria separate "the file is missing" from "nobody
looked."** README, documented decisions, license, pre-commit, changelog and the
operational runbook are read out of the `governance` collector, where a missing
field looks exactly like a missing file. The grade therefore checks the
collector first: when `governance` is absent from the snapshot — it raised, and
the error is in `errors.governance` — those criteria report as
environment-blocked, carry that error as the reason on the card, and stay in
the denominator, opening the axis's uncertainty band instead of quietly
dropping out. When the collector ran, an absent file is a finding, as it
should be. Without the distinction, one exception in a collector produced
accusations ("no README", "no license", "no documented decisions", "no
runbooks") about a project nobody had looked at.

**Pre-commit and changelog are measured, not skipped, when absent.** Both used
to read `value or None` — a measured "does not exist" (`false`/`null`) collapsed
into the same `None` as "the collector never ran," which pulled a real finding
out of the denominator. That is the opposite failure from the one above: there
it was "not measured" being read as clean, here it was "measured and missing"
being read as "not measured." Both criteria now report a plain failure when the
file is absent and the collector ran — the only case where they are not a
plain pass/fail is the `governance` collector having raised, and even then they
stay in the denominator as environment-blocked rather than disappearing from
it.

**License is not applicable in a private repository, and the grade now says
so instead of docking a point.** Visibility comes from the same `gh repo view`
call that feeds branch protection. `PRIVATE` removes the criterion from the
denominator with a *não se aplica* label — not a finding, not a pass by
omission. `PUBLIC` keeps failing an absent `LICENSE` exactly as before. When
visibility could not be determined (no `gh`, no GitHub remote, or the API call
failed) the criterion keeps today's behavior — an absent file still fails it —
but the label says the visibility itself was not established, so a missing
license there is not misread as "confirmed public."

A **404** from the protection endpoint is the answer that matters: the branch has
no protection. Any other failure — 403, rate limit, network — reports the
criterion as environment-blocked instead: it stays in the denominator and opens
the axis's uncertainty band, with the reason (rate limit, no `gh`, no remote…)
on the card. Reporting "unprotected" because the request failed would be an
accusation without a look.

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
