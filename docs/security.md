# Security model

Ruch-X audits a repository by running tools against it. Some of those tools are
the repository's own code. This document states exactly what runs, what ends up
in the versioned snapshot, and what is **not** protected.

Read it before pointing Ruch-X at code you did not write.

---

## 1. What Ruch-X executes

`scripts/collect.py` shells out. Every call goes through one helper (`run()`),
which uses `subprocess.run` with a list argv (no shell), a timeout, and the
**full environment of the calling process** (`{**os.environ, **extra}`).

| Command | Collector | Notes |
|---|---|---|
| `git rev-parse`, `rev-list`, `shortlog`, `log`, `ls-files`, `show` | `git`, `governance`, `dora` | read-only history |
| `scc --format json --no-cocomo <root>` | `code` | line counter, if installed |
| `cloc --json --quiet <root>` | `code` | fallback if `scc` is absent |
| `ruff check --output-format json .` | `quality` | reads the repo's ruff config |
| `npx --no-install eslint . -f json` | `quality` | **runs the binary in the repo's `node_modules`** |
| `python -P -m radon cc -j -s --ignore ... .` | `quality`, `git` | AST-based, does not import the code |
| `python -P -m pytest <pytest_args>` | `tests` | **only when `run_tests = true`** |
| `<python> -P -m pip list --outdated --format json` | `governance` | queries the configured package index |
| `npm outdated --json` | `governance` | uses the repo's `.npmrc` |
| `docker stats --no-stream`, `docker ps`, `docker system df` | `infra` | honours `DOCKER_HOST` |
| `gh run list`, `gh repo view`, `gh api repos/<slug>/branches/<branch>/protection` | `ci`, `dora`, `governance` | your `gh` credentials |
| `<python> manage.py showmigrations --plan` | `django` | **executes the audited project** |
| `<python> manage.py check --deploy` | `django` | **executes the audited project** |

`collect_db` also opens an outbound PostgreSQL connection with `psycopg` (or
`psycopg2`) when a DSN is configured.

`<python>` — in the `pip` row and in the two `manage.py` rows — is the
interpreter running `collect.py`, **unless the repository's `ruch-x.toml` sets
`python`**, in which case it is a file from the repository being audited. Note
that the `pip` row belongs to `governance`, so it runs even with
`--skip django`. See §6. The `radon` and `pytest` rows always use the
interpreter running `collect.py`.

### The `manage.py` line

The two `manage.py` calls are different in kind from everything else in that
table. Django's `manage.py` imports the settings module, imports every app in
`INSTALLED_APPS`, executes `AppConfig.ready()` (and therefore any signal
registration), reads whatever `.env` the settings load, and opens a database
connection. That is arbitrary code from the audited repository, running as your
user, with your environment.

There is no version of "measure pending migrations and deployment checks in a
Django project" that does not do this. The alternative is to not measure it.
Ruch-X chose to measure it and to say so here.

`python -P -m` is used for `radon`, `pytest` and `pip` so a `radon.py`,
`pytest.py` or `pip.py` dropped in the audited repository's root cannot be
imported as `__main__` instead of the real module. `manage.py` deliberately has
**no** `-P`: it must import its own project.

`-P` guards which *module* gets imported. It says nothing about which
*interpreter* runs, and the toml's `python` key chooses that — including, if the
repository wants, a file from the repository. Both boundaries are stated in §6.

## 2. Threat model

**Run Ruch-X on repositories you trust.**

The threat model is: the operator is auditing code they are responsible for —
their own project, their team's project, a client project they already run
locally. Ruch-X's duty in that setting is to not lie, to not damage the person
who trusted it, and to not promise protection it does not deliver.

Ruch-X is **not** a sandbox and does not provide isolation. Auditing third-party
or untrusted code — real due diligence on someone else's repository — requires a
container or a disposable VM, with no credentials, no SSH agent, and no network
access you care about. Nothing in this tool substitutes for that.

If you cannot isolate, you can reduce exposure — read the list as damage
reduction, not as safety.

**Read the repository's `ruch-x.toml` before running anything.** It is the
control surface: `python`, `manage_py`, `run_tests`, `pytest_args`,
`[infra] docker_host` and `[db] dsn` decide what gets executed and what gets
connected to. Delete or override what you did not put there.

- `--skip django` removes the two `manage.py` calls. It does **not** remove
  every execution of a repository-controlled binary: `python` from the toml is
  also used by the `governance` collector, so a binary shipped in the repository
  still runs (§6).
- `run_tests` stays `false` by default. Leave it there for code you do not
  trust, and do not let the repository's `pytest_args` stand.
- `--skip infra,db` drops the outbound SSH and PostgreSQL connections that the
  toml can aim at a host of its choosing.
- `--only stack` is the only selection that is purely file reads. Adding `code`
  brings in your own `scc`/`cloc`; adding `git` brings in `git` and `radon`;
  adding `governance` brings in `git`, `gh`, `pip list --outdated` and
  `npm outdated` — the last two reach the network, and `npm` reads the
  repository's `.npmrc` to decide which registry that is.

The result is a partial audit, and the dashboard says which criteria were not
measured (see §5).

## 3. What goes into the snapshot

Snapshots are meant to be committed — that is where the time series comes from.
Everything written to `.ruch-x/<date>.json` is therefore potentially public.
(`latest.json` gets the same treatment but stays out of git — it only points at
the newest snapshot.)

Fields that carry text from outside the tool:

| Field | Source |
|---|---|
| `errors.<collector>` | `str(exception)` from the failing collector (300 chars) — a bad DSN lands here |
| `<collector>.nao_medido.<field>` | last line of the failed command's stderr (200 chars) — routinely an absolute path, e.g. `~/projects/venv/bin/python3: No module named radon` (the home prefix is rewritten; see below) |
| `db.slow_queries[].query` | first 160 chars of query text from `pg_stat_statements` |
| `db.<query>.unavailable` | PostgreSQL error text |
| `django.deploy_issues[].message`, `django.other_issues[].message` | `check --deploy` output (200 chars) |
| `infra.host` | Docker host |
| `ci.recent[].title` | commit subject lines from GitHub Actions |
| `git.hotspots[].file`, `quality.*.file` | repository paths |
| `governance.dependencias.exemplos[]` | package names and versions |

Two mechanisms keep credentials out.

**Recursive redaction before writing.** `gravar_snapshot` runs
`redigir_estrutura` over the whole structure — every string **value**, at every
depth, in both files. Dictionary keys are walked but not rewritten, so a
collector must never use a secret as a key. It is applied on the way out rather
than field by field, so a new collector cannot forget it. The patterns live in
`REDACOES` in `collect.py`, explicit so they can be reviewed and extended. There
are twelve of them:

| Input | Stored |
|---|---|
| `postgresql://user:p@ss:w0rd@db.example.com:5432/app` | `postgresql://user:***@db.example.com:5432/app` |
| `CREATE USER x WITH PASSWORD 'hunter2abc';` | `CREATE USER x WITH PASSWORD '***';` |
| `DATABASE_PASSWORD=supersecretvalue` | `DATABASE_PASSWORD=***` |
| `{"password": "supersecretvalue"}` | `{"password": "***"}` |
| `PGPASSWORD=abcdef123456` | `PGPASSWORD=***` |
| `ghp_…`, `github_pat_…`, `sk-…`, `sk-ant-…`, `AKIA…`, `xoxb-…` | `***` |
| `Authorization: Bearer eyJhbGciOi…` | `Authorization: Bearer ***` |
| `-----BEGIN RSA PRIVATE KEY-----…-----END RSA PRIVATE KEY-----` | markers kept, body replaced by `***` |
| `/Users/ana/proj/venv/bin/python3`, `/home/ana/...`, `C:\Users\Ana\...` | `~/proj/venv/bin/python3` — home prefix only |

The DSN pattern keeps user and host — they are diagnostic — and masks the
password up to the **last** `@` before the host, so a password containing `@` or
`:` does not leak as if it were part of the hostname.

**`infra.host` never stores the address.** The Docker host is reduced to its
scheme before it reaches the snapshot: `ssh://root@1.2.3.4` becomes `ssh://***`,
a host with no scheme becomes `remoto`, and no host at all becomes `local`.

**The secret scanner never stores the matched value.** `_varre_segredos` records
only `{"file": ..., "line": ..., "kind": ...}` — path, line number, and a label
such as `token do GitHub`. The matched text is used for the plausibility filter
and then discarded. A finding tells you where to look; it does not republish the
credential into a file you are about to commit.

**Home directories are rewritten too.** They are not credentials, which is why
they went unnoticed until the first snapshot of this repository was read before
being committed: the reason recorded for a failed `radon` call carried
`/Users/<user>/Documents/<company>/Projects/<private project>/venv/bin/python3`.
Username, disk layout and the name of an unrelated project, on their way into a
public repository. `/Users/<name>/`, `/home/<name>/` and `C:\Users\<name>\`
now become `~/`; the rest of the path survives, because that is the part with
diagnostic value.

**What still passes through untouched:** machine and container names, branch
names, commit subjects, package names, and any path that is not under a home
directory. They are diagnostic, and no pattern separates a sensitive one from a
useful one. Read your first snapshot before committing it, especially in a
public repository.

Redaction is a safety net, not a licence. A collector that has a secret in hand
should not put it in the snapshot in the first place.

## 4. The dashboard

`scripts/render.py` produces a single self-contained HTML file. It is offline in
the strict sense: no `<script>` tag, no `<link>`, no `<img>`, no `src=`, no
remote font, no `fetch`. CSS is inline, charts are inline SVG, and the fonts are
whatever the system provides. Verify it yourself:

```bash
grep -c "<script\|<link\|<img\|src=\|https\?://" .ruch-x/dashboard.html
```

A snapshot is a versioned file: it may have come from a teammate, from an older
version of the tool, or from an editor. `render.py` therefore treats it as
untrusted input.

- Every free-text field goes through `html.escape` (`e()`).
- Every field assumed to be numeric goes through `num()` / `milhar()`, which
  return an escaped value if the field is not actually a number. A forged
  snapshot cannot inject markup through a "number".
- `_seguro()` coerces fields of the wrong type to a safe default before any
  arithmetic, indexing or `.get()` runs.
- `milhar()` formats large integers without going through `float`, so an
  oversized integer in a snapshot does not raise `OverflowError`.
- A `.json` file that fails to parse is skipped silently; one that parses but
  is not a JSON object is skipped with a warning on stderr. Neither aborts the
  render with a traceback.
- If `.ruch-x` is a symlink, or `.ruch-x/dashboard.html` is a symlink, or the
  resolved output path falls outside the snapshot directory, `render.py`
  refuses to write and exits with a message. Writing through a symlink would
  silently overwrite a file outside the repository. An explicit `--out` bypasses
  the check — that is the supported way to choose another destination.

## 5. Not audited is not clean

When a command fails, the field it would have filled is set to `null` and the
reason is recorded in `nao_medido: {field: reason}`. `0` and `[]` mean "measured,
and there was nothing"; `null` means "could not measure".

This applies to the Django check, pending migrations (including a project with
no `manage.py`, and a `manage_py` in the toml that does not resolve), the secret
scan, git metrics, hotspots, line counts (both `scc` and `cloc`) and complexity.
Branch protection follows the same contract through a different shape: the
collector reports `disponivel: false` with a `motivo`, and `protegido: null`.

A collector that raises never reaches the snapshot at all — its error goes to
`errors.<collector>`. The grade treats that as "not measured" too, which matters
most for the criteria read out of `governance` by file existence (README,
license, documented decisions, pre-commit, changelog, runbooks): a missing
field there is indistinguishable from a missing file, so the absence of the
collector is checked before the absence of the file. An exception must not
turn into a pile of accusations about a repository nobody looked at.

In the dashboard the criterion is shown as *não auditado* with its reason, and
it is removed from the denominator of the axis grade — it neither rewards nor
punishes. An axis where nothing could be measured gets no letter at all; it does
not become an `F`.

**A third state, distinct from "not audited": not applicable.** The license
criterion drops out of the denominator in a `PRIVATE` repository too, but for a
different reason than "nobody looked" — the collector ran, read the file, and
the criterion simply does not apply there (the finding itself says so: "em
repositório privado é aceitável"). The dashboard labels it *não se aplica*
rather than *não auditado* so the two are not read as the same excuse. See
[criteria.md](criteria.md#process).

**One deliberate exception: a missing coverage report is a finding, not "not
audited."** Coverage never gets a `nao_medido` entry; when no report exists the
criterion fails and produces a P1. Choosing not to measure coverage is a fact
about the project, not a limitation of the tool. See
[criteria.md](criteria.md).

This is not cosmetic. A report that cannot tell "we scanned the repository and
found no committed secrets" from "the scan never ran" is worthless as an audit,
and dangerous as reassurance. The first time it mattered: a settings module that
did not exist turned into "0 security warnings".

## 6. What is NOT protected

The honest list. None of these are bugs to be filed; they are the accepted
consequences of the threat model in §2.

- **The audited project's `manage.py` is executed** — by design, twice per run.
  It imports settings, apps and `.env` and opens a database connection.
- **`python` in the toml names the binary that Ruch-X executes,** and it is
  required to live inside the repository. A repository that ships
  `python = "tools/whatever"` gets that file executed, as your user. It is used
  in two places, and only one of them is Django: `manage.py`, and
  `pip list --outdated` in the `governance` collector. **It therefore survives
  `--skip django`,** and it needs neither Django nor `run_tests`. Proven with a
  fake repository: the shipped binary was invoked as
  `tools/fakepy -P -m pip list --outdated --format json`. The `-P` protects the
  module, not the interpreter — when the interpreter itself is the repository's
  file, there is nothing left to protect.
- **`run_tests`, `pytest_args` and `test_timeout` come from the repository's own
  `ruch-x.toml`.** With `run_tests = true`, collection runs the repository's test
  suite, and `conftest.py` is arbitrary code. It is also the one path where
  collection **writes** into the audited tree: the suite writes whatever it
  writes, and `--cov-report=json` leaves `coverage.json` and `.coverage` behind.
  The default is `false`.
- **`npx --no-install eslint` runs the binary that shipped in the repository's
  `node_modules`,** with the repository's `eslint.config.js`.
- **The entire environment is passed to every subprocess.** `GITHUB_TOKEN`,
  `AWS_*`, `RUCHX_DATABASE_URL` and everything else in `os.environ` are visible
  to every command listed in §1, including `manage.py`.
- **`[infra] docker_host` and `[db] dsn` in the toml point outbound connections
  at a host chosen by the repository** — an SSH connection for Docker, a libpq
  connection for PostgreSQL. Environment variables take precedence over the toml
  for both, which is the way to keep control of the destination.
- **`gh` resolves the target host from the repository's git remote,** and it runs
  with your authenticated token. **`npm outdated` uses the repository's
  `.npmrc`,** including a redefined registry.
- **File reads have no size ceiling in most places.** The 1.5 MB cap exists only
  in the secret scanner. Line counting, module sizing and hotspot measurement
  read whatever is on disk.
- **Path containment is a blast radius, not a shield.** `manage_py`, `python`,
  `apps_dir`/`modules_dir`, `coverage_file` and `coverage_json` are resolved
  inside the repository root, and absolute paths and `../` are refused (see
  [configuration.md](configuration.md)). That keeps Ruch-X from reaching *out* of
  the repository — `manage_py = "/etc/passwd"`, `apps_dir = "/"`. It does nothing
  about what is *inside* it. For `manage_py` and `python` the contained path is
  precisely what gets executed: containment is the trigger, not the protection.
  Other values in the toml are not paths and get no such treatment.

## 7. Reporting a vulnerability

Open an issue at <https://github.com/Ruch-Digital/ruch-x/issues>.

If the finding involves a credential, do not paste it into the issue — describe
the shape of the input and the field where it surfaced. The maintainers do not
need the secret to reproduce a leak.
