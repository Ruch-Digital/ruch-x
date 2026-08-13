#!/usr/bin/env python3
"""
Ruch-X - coletor.

Roda na raiz de um repositorio, coleta o que conseguir e grava um snapshot
JSON em .metricas/. Cada coletor eh independente e falha em silencio (o erro
vai pro campo "errors" do snapshot) - a ideia eh que um Postgres fora do ar
nao derrube a coleta de codigo e testes.

Uso:
    python collect.py                      # coleta tudo que der
    python collect.py --only code,git      # so alguns coletores
    python collect.py --skip db,ci         # tudo menos alguns
    python collect.py --config metricas.toml
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 2  # 2: coletores governance e dora (auditoria de engenharia)
SNAPSHOT_DIR = ".ruch-x"
SNAPSHOT_DIR_LEGADO = ".metricas"
COLLECTORS = ["stack", "code", "quality", "tests", "django", "git", "db",
              "infra", "ci", "governance", "dora"]


# --------------------------------------------------------------------------
# utilidades
# --------------------------------------------------------------------------

def run(cmd, cwd=None, timeout=180, env=None):
    """Executa comando e devolve (returncode, stdout, stderr). Nunca levanta."""
    merged = {**os.environ, **(env or {})}
    try:
        p = subprocess.run(
            cmd, cwd=cwd, timeout=timeout, env=merged,
            capture_output=True, text=True,
            shell=isinstance(cmd, str),
        )
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout apos {timeout}s"
    except FileNotFoundError:
        return 127, "", "comando nao encontrado"
    except Exception as exc:  # noqa: BLE001
        return 1, "", str(exc)


def has(binary):
    return shutil.which(binary) is not None


def load_config(path):
    """Le metricas.toml se existir. Config eh toda opcional."""
    cfg = {}
    p = Path(path)
    if not p.exists():
        return cfg
    try:
        import tomllib
        cfg = tomllib.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"aviso: nao consegui ler {path}: {exc}", file=sys.stderr)
    return cfg


def env_or(cfg, key, env_name, default=None):
    """Config file perde pra variavel de ambiente. Segredo nunca vai pro toml."""
    return os.environ.get(env_name) or cfg.get(key) or default


def nao_medido(out, campo, motivo):
    """Marca um campo como NAO MEDIDO — e nao como medido-e-vazio.

    A diferenca e a razao de existir de um relatorio de auditoria. `[]` diz
    "varri e nao achei nada"; `None` diz "nao consegui varrer". Trocar um pelo
    outro faz o painel dar criterio por atendido em cima de um comando que
    morreu (visto no 1o uso real: settings inexistente no toml virava
    "0 avisos de seguranca").
    """
    out[campo] = None
    out.setdefault("nao_medido", {})[campo] = str(motivo)[:200]
    return out


def _motivo(rc, se):
    """Motivo curto pro campo `nao_medido`, a partir do retorno de run()."""
    if rc == 127:
        return "comando nao encontrado"
    if rc == 124:
        return "timeout"
    linhas = [ln.strip() for ln in (se or "").splitlines() if ln.strip()]
    return linhas[-1][:200] if linhas else f"comando falhou (rc={rc})"


# --------------------------------------------------------------------------
# codigo: linhas, linguagens, distribuicao por app
# --------------------------------------------------------------------------

def collect_code(root, cfg):
    """LOC por linguagem e por app Django. Usa scc, cai pro cloc, cai pro proprio."""
    out = {"tool": None, "total_loc": 0, "total_files": 0,
           "by_language": [], "by_app": [], "comment_ratio": None}

    if has("scc"):
        rc, so, _ = run(["scc", "--format", "json", "--no-cocomo", str(root)])
        if rc == 0 and so.strip():
            data = json.loads(so)
            out["tool"] = "scc"
            langs = []
            code = comments = files = 0
            for lang in data:
                langs.append({
                    "language": lang.get("Name"),
                    "files": lang.get("Count", 0),
                    "code": lang.get("Code", 0),
                    "comments": lang.get("Comment", 0),
                    "blanks": lang.get("Blank", 0),
                    "complexity": lang.get("Complexity", 0),
                })
                code += lang.get("Code", 0)
                comments += lang.get("Comment", 0)
                files += lang.get("Count", 0)
            langs.sort(key=lambda x: -x["code"])
            out["by_language"] = langs[:15]
            out["total_loc"] = code
            out["total_files"] = files
            out["comment_ratio"] = round(comments / code, 3) if code else None

    elif has("cloc"):
        rc, so, _ = run(["cloc", "--json", "--quiet", str(root)])
        if rc == 0 and so.strip():
            data = json.loads(so)
            out["tool"] = "cloc"
            langs = []
            for name, v in data.items():
                if name in ("header", "SUM"):
                    continue
                langs.append({"language": name, "files": v.get("nFiles", 0),
                              "code": v.get("code", 0), "comments": v.get("comment", 0),
                              "blanks": v.get("blank", 0), "complexity": 0})
            langs.sort(key=lambda x: -x["code"])
            out["by_language"] = langs[:15]
            total = data.get("SUM", {})
            out["total_loc"] = total.get("code", 0)
            out["total_files"] = total.get("nFiles", 0)
            if total.get("code"):
                out["comment_ratio"] = round(total.get("comment", 0) / total["code"], 3)

    else:
        # fallback puro python - so conta o que importa num projeto Django
        out["tool"] = "builtin"
        exts = SOURCE_EXTS
        counts = defaultdict(lambda: {"files": 0, "code": 0})
        for path in iter_source_files(root):
            lang = exts.get(path.suffix)
            if not lang:
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue
            counts[lang]["files"] += 1
            counts[lang]["code"] += sum(1 for ln in lines if ln.strip())
        langs = [{"language": k, "files": v["files"], "code": v["code"],
                  "comments": 0, "blanks": 0, "complexity": 0}
                 for k, v in counts.items()]
        langs.sort(key=lambda x: -x["code"])
        out["by_language"] = langs
        out["total_loc"] = sum(x["code"] for x in langs)
        out["total_files"] = sum(x["files"] for x in langs)

    out["by_app"], out["module_label"] = loc_by_module(root, cfg)
    return out


IGNORE_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache",
               ".pytest_cache", ".ruff_cache", "migrations", "staticfiles", "static",
               "dist", "build", ".metricas", "htmlcov", ".tox", "vendor", "target",
               ".next", ".nuxt", "coverage", "Pods", ".gradle", "bin", "obj"}

# Extensoes tratadas como codigo-fonte em qualquer linguagem. Usadas pelo mapa
# de atrito e pelo contador proprio quando scc/cloc nao existem.
SOURCE_EXTS = {
    ".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".vue": "Vue", ".svelte": "Svelte",
    ".go": "Go", ".rs": "Rust", ".rb": "Ruby", ".php": "PHP", ".java": "Java",
    ".kt": "Kotlin", ".swift": "Swift", ".cs": "C#", ".c": "C", ".h": "C",
    ".cpp": "C++", ".hpp": "C++", ".ex": "Elixir", ".exs": "Elixir", ".dart": "Dart",
    ".scala": "Scala", ".lua": "Lua", ".sh": "Shell", ".sql": "SQL",
    ".html": "HTML", ".css": "CSS", ".scss": "SCSS", ".yml": "YAML", ".yaml": "YAML",
}

# Palavras que indicam ramificacao, por familia de linguagem. Servem de
# aproximacao de complexidade quando nao ha ferramenta dedicada instalada.
BRANCH_WORDS = r"\b(if|else\s+if|elif|for|while|case|catch|except|switch|&&|\|\||\?\?)\b"


def radon_ignore():
    """Lista de diretorios pro `--ignore` do radon.

    Sem isso o radon varre o ambiente virtual e o dashboard passa a medir a
    complexidade das DEPENDENCIAS (achado 2026-08-12: os 8 piores casos de um
    projeto Django eram todos `venv/Lib/site-packages`, e o total de "funcoes
    acima de 10" virou ruido). O radon aceita padroes separados por virgula e
    casa por nome de diretorio em qualquer profundidade.
    """
    dirs = sorted(IGNORE_DIRS - {"migrations"})  # migrations conta como codigo
    return ",".join(dirs + [f"*/{d}/*" for d in ("site-packages", "node_modules")])


def iter_source_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS and not d.startswith(".")]
        for fn in filenames:
            yield Path(dirpath) / fn


TEST_HINTS = ("test", "spec", "__tests__", "tests", "_test")


def is_test_file(path):
    name = path.name.lower()
    return (any(h in name for h in ("test", "spec"))
            or any(p.lower() in TEST_HINTS for p in path.parts))


def module_candidates(root, cfg):
    """
    Descobre as unidades logicas do projeto, seja qual for a linguagem.

    Ordem de tentativa: pasta configurada, apps Django, pastas de codigo
    convencionais (src, packages, cmd, internal, lib, app, services) e, em
    ultimo caso, as proprias pastas da raiz. O rotulo muda junto para o
    dashboard nao chamar pacote Go de "app".
    """
    root = Path(root)
    configured = cfg.get("apps_dir") or cfg.get("modules_dir")
    if configured and (root / configured).is_dir():
        base = root / configured
        dirs = [d for d in base.iterdir() if d.is_dir() and d.name not in IGNORE_DIRS]
        if dirs:
            return dirs, "Módulo"

    django_apps = [d for d in root.rglob("apps.py")
                   if not any(p in IGNORE_DIRS for p in d.parts)]
    if django_apps:
        return [f.parent for f in django_apps], "App"

    # Acumula todas as pastas convencionais que existirem. Um projeto Go tem
    # cmd/ e internal/ ao mesmo tempo; parar na primeira esconderia metade dele.
    convencionais = ("apps", "src", "packages", "cmd", "internal", "pkg",
                     "lib", "app", "services", "modules")
    achados, rotulos = [], set()
    for conv in convencionais:
        base = root / conv
        if not base.is_dir():
            continue
        rotulos.add("Pacote" if conv in ("cmd", "internal", "pkg", "packages") else "Módulo")
        filhos = [d for d in base.iterdir() if d.is_dir() and d.name not in IGNORE_DIRS]
        achados.extend(filhos or [base])
    if achados:
        return achados, ("Pacote" if rotulos == {"Pacote"} else "Módulo")

    dirs = [d for d in root.iterdir()
            if d.is_dir() and d.name not in IGNORE_DIRS and not d.name.startswith(".")]
    return dirs, "Pasta"


def loc_by_module(root, cfg):
    """Linhas de codigo e de teste por modulo, contando qualquer linguagem."""
    candidates, label = module_candidates(root, cfg)
    result = []
    for mod in candidates:
        code = tests = 0
        for f in mod.rglob("*"):
            if not f.is_file() or f.suffix not in SOURCE_EXTS:
                continue
            if any(part in IGNORE_DIRS for part in f.parts):
                continue
            try:
                lines = f.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue
            n = sum(1 for ln in lines
                    if ln.strip() and not ln.strip().startswith(("#", "//", "*", "/*")))
            if is_test_file(f):
                tests += n
            else:
                code += n
        if code or tests:
            result.append({"app": mod.name, "code": code, "tests": tests,
                           "test_ratio": round(tests / code, 2) if code else None})
    result.sort(key=lambda x: -x["code"])
    return result[:25], label


# --------------------------------------------------------------------------
# qualidade: ruff + complexidade ciclomatica
# --------------------------------------------------------------------------

def collect_quality(root, cfg):
    out = {"ruff": None, "complexity": None}

    if has("ruff"):
        rc, so, _ = run(["ruff", "check", "--output-format", "json", "."], cwd=root)
        if so.strip():
            try:
                items = json.loads(so)
                by_rule = Counter(i.get("code") or "?" for i in items)
                by_file = Counter(i.get("filename", "?") for i in items)
                out["ruff"] = {
                    "total": len(items),
                    "by_rule": [{"rule": k, "count": v} for k, v in by_rule.most_common(12)],
                    "worst_files": [{"file": rel(k, root), "count": v}
                                    for k, v in by_file.most_common(10)],
                }
            except json.JSONDecodeError:
                pass

    if out["ruff"] is None and has("npx") and (Path(root) / "package.json").exists():
        # eslint so eh chamado se ja estiver configurado no projeto; --no-install
        # evita baixar pacote na maquina de quem esta so medindo.
        rc, so, _ = run(["npx", "--no-install", "eslint", ".", "-f", "json"],
                        cwd=root, timeout=300)
        if so.strip().startswith("["):
            try:
                arquivos = json.loads(so)
                itens = [(m.get("ruleId") or "?", a.get("filePath", "?"))
                         for a in arquivos for m in a.get("messages", [])]
                if itens:
                    by_rule = Counter(r for r, _ in itens)
                    by_file = Counter(f for _, f in itens)
                    out["ruff"] = {
                        "tool": "eslint", "total": len(itens),
                        "by_rule": [{"rule": k, "count": v} for k, v in by_rule.most_common(12)],
                        "worst_files": [{"file": rel(k, root), "count": v}
                                        for k, v in by_file.most_common(10)],
                    }
            except json.JSONDecodeError:
                pass

    # radon: complexidade ciclomatica por funcao (do PROJETO, nao das libs —
    # ver radon_ignore()).
    rc, so, _ = run([sys.executable, "-m", "radon", "cc", "-j", "-s",
                     "--ignore", radon_ignore(), "."], cwd=root)
    if rc == 0 and so.strip():
        try:
            data = json.loads(so)
            blocks = []
            for fname, items in data.items():
                if not isinstance(items, list):
                    continue
                for b in items:
                    blocks.append({
                        "file": rel(fname, root),
                        "name": b.get("name"),
                        "line": b.get("lineno"),
                        "complexity": b.get("complexity", 0),
                        "rank": b.get("rank"),
                    })
            blocks.sort(key=lambda x: -x["complexity"])
            scores = [b["complexity"] for b in blocks]
            out["complexity"] = {
                "blocks_analyzed": len(blocks),
                "avg": round(sum(scores) / len(scores), 2) if scores else 0,
                "above_10": sum(1 for s in scores if s > 10),
                "worst": blocks[:15],
            }
        except json.JSONDecodeError:
            pass
    return out


def rel(path, root):
    try:
        return str(Path(path).resolve().relative_to(Path(root).resolve()))
    except (ValueError, OSError):
        return str(path)


# --------------------------------------------------------------------------
# testes: cobertura + duracao
# --------------------------------------------------------------------------

COVERAGE_FILES = [
    ("coverage.json", "coverage.py"),
    ("coverage/coverage-summary.json", "istanbul"),
    ("coverage/coverage-final.json", "istanbul-final"),
    ("coverage/lcov.info", "lcov"),
    ("lcov.info", "lcov"),
    ("coverage.xml", "cobertura"),
    ("coverage.out", "go"),
    ("cobertura.xml", "cobertura"),
    ("target/site/jacoco/jacoco.xml", "jacoco"),
]


def parse_coverage(root, cfg):
    """
    Le o relatorio de cobertura em qualquer formato comum. Cada ecossistema
    exporta o seu, entao em vez de exigir um, tenta os que existem: coverage.py,
    Istanbul (jest/vitest), lcov, Cobertura e Go.
    """
    extra = cfg.get("coverage_file")
    candidatos = ([(extra, "custom")] if extra else []) + COVERAGE_FILES

    for rel_path, kind in candidatos:
        f = Path(root) / rel_path
        if not f.exists():
            continue
        try:
            if kind in ("coverage.py", "custom") and f.suffix == ".json":
                d = json.loads(f.read_text(encoding="utf-8"))
                if "totals" in d:
                    return {"pct": round(d["totals"].get("percent_covered", 0), 1),
                            "source": str(rel_path), "by_app": coverage_by_module_py(d)}
                if "total" in d:
                    kind = "istanbul"
            if kind == "istanbul":
                d = json.loads(f.read_text(encoding="utf-8"))
                total = d.get("total", {}).get("lines", {})
                if total:
                    return {"pct": round(total.get("pct", 0), 1), "source": str(rel_path)}
            if kind == "istanbul-final":
                d = json.loads(f.read_text(encoding="utf-8"))
                hit = tot = 0
                for fdata in d.values():
                    counts = (fdata or {}).get("s", {}).values()
                    tot += len(list(counts))
                    hit += sum(1 for c in (fdata or {}).get("s", {}).values() if c)
                if tot:
                    return {"pct": round(100 * hit / tot, 1), "source": str(rel_path)}
            if kind == "lcov":
                lf = lh = 0
                for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
                    if line.startswith("LF:"):
                        lf += int(line[3:] or 0)
                    elif line.startswith("LH:"):
                        lh += int(line[3:] or 0)
                if lf:
                    return {"pct": round(100 * lh / lf, 1), "source": str(rel_path)}
            if kind in ("cobertura", "jacoco"):
                import xml.etree.ElementTree as ET
                rootx = ET.parse(f).getroot()
                rate = rootx.get("line-rate")
                if rate:
                    return {"pct": round(100 * float(rate), 1), "source": str(rel_path)}
                covered = missed = 0
                for c in rootx.iter("counter"):
                    if c.get("type") == "LINE":
                        covered += int(c.get("covered", 0))
                        missed += int(c.get("missed", 0))
                if covered + missed:
                    return {"pct": round(100 * covered / (covered + missed), 1),
                            "source": str(rel_path)}
            if kind == "go":
                total = hit = 0
                for line in f.read_text(encoding="utf-8", errors="ignore").splitlines()[1:]:
                    parts = line.rsplit(" ", 2)
                    if len(parts) == 3:
                        n, count = int(parts[1]), int(parts[2])
                        total += n
                        if count > 0:
                            hit += n
                if total:
                    return {"pct": round(100 * hit / total, 1), "source": str(rel_path)}
        except Exception:  # noqa: BLE001
            continue
    return None


def coverage_by_module_py(data):
    per = defaultdict(lambda: {"statements": 0, "missing": 0})
    for fname, fdata in data.get("files", {}).items():
        parts = Path(fname).parts
        mod = parts[1] if len(parts) > 1 and parts[0] in ("apps", "src") else parts[0]
        sm = fdata.get("summary", {})
        per[mod]["statements"] += sm.get("num_statements", 0)
        per[mod]["missing"] += sm.get("missing_lines", 0)
    rows = []
    for mod, v in per.items():
        if v["statements"] < 10:
            continue
        cov = v["statements"] - v["missing"]
        rows.append({"app": mod, "statements": v["statements"],
                     "coverage_pct": round(100 * cov / v["statements"], 1)})
    rows.sort(key=lambda x: x["coverage_pct"])
    return rows


def collect_tests(root, cfg):
    """Le o relatorio de cobertura em qualquer formato; roda pytest se pedido."""
    out = {"coverage_pct": None, "by_app": [], "test_count": None,
           "duration_s": None, "slowest": [], "source": None}

    cov_path = Path(root) / cfg.get("coverage_json", "coverage.json")
    run_tests = cfg.get("run_tests", False)

    if not cov_path.exists() and run_tests:
        args = cfg.get("pytest_args", ["-q", "--cov", "--cov-report=json", "--durations=10"])
        rc, so, se = run([sys.executable, "-m", "pytest", *args], cwd=root,
                         timeout=cfg.get("test_timeout", 900))
        out["source"] = "pytest"
        m = re.search(r"(\d+) passed", so)
        if m:
            out["test_count"] = int(m.group(1))
        m = re.search(r"in ([\d.]+)s", so)
        if m:
            out["duration_s"] = float(m.group(1))
        for line in so.splitlines():
            m = re.match(r"([\d.]+)s\s+(call|setup|teardown)\s+(.+)", line.strip())
            if m:
                out["slowest"].append({"duration_s": float(m.group(1)), "test": m.group(3)})

    found = parse_coverage(root, cfg)
    if found:
        out["coverage_pct"] = found["pct"]
        out["source"] = found["source"]
        out["by_app"] = found.get("by_app", [])
        return out

    if cov_path.exists():
        try:
            data = json.loads(cov_path.read_text(encoding="utf-8"))
            out["source"] = out["source"] or "coverage.json"
            out["coverage_pct"] = round(data.get("totals", {}).get("percent_covered", 0), 1)
            per_app = defaultdict(lambda: {"statements": 0, "missing": 0})
            for fname, fdata in data.get("files", {}).items():
                parts = Path(fname).parts
                app = parts[1] if len(parts) > 1 and parts[0] == "apps" else parts[0]
                s = fdata.get("summary", {})
                per_app[app]["statements"] += s.get("num_statements", 0)
                per_app[app]["missing"] += s.get("missing_lines", 0)
            rows = []
            for app, v in per_app.items():
                if v["statements"] < 10:
                    continue
                covered = v["statements"] - v["missing"]
                rows.append({"app": app, "statements": v["statements"],
                             "coverage_pct": round(100 * covered / v["statements"], 1)})
            rows.sort(key=lambda x: x["coverage_pct"])
            out["by_app"] = rows
        except (json.JSONDecodeError, OSError):
            pass
    return out


# --------------------------------------------------------------------------
# stack: que linguagens e frameworks existem aqui
# --------------------------------------------------------------------------

STACK_MARKERS = [
    ("Django", ["manage.py"]),
    ("Python", ["pyproject.toml", "requirements.txt", "setup.py", "Pipfile"]),
    ("Node", ["package.json"]),
    ("Go", ["go.mod"]),
    ("Rust", ["Cargo.toml"]),
    ("PHP/Laravel", ["artisan"]),
    ("PHP", ["composer.json"]),
    ("Ruby/Rails", ["config/application.rb"]),
    ("Ruby", ["Gemfile"]),
    ("Java/Maven", ["pom.xml"]),
    ("Java/Gradle", ["build.gradle", "build.gradle.kts"]),
    ("Elixir", ["mix.exs"]),
    (".NET", ["global.json"]),
    ("Docker", ["Dockerfile", "docker-compose.yml", "compose.yml"]),
]


def collect_stack(root, cfg):
    """
    Identifica o que o projeto eh antes de medir. Serve pro dashboard rotular
    as coisas certo e pra saber quais coletores fazem sentido aqui.
    """
    root = Path(root)
    detected = []
    for nome, arquivos in STACK_MARKERS:
        for a in arquivos:
            if (root / a).exists():
                detected.append(nome)
                break

    if not detected:
        for f in root.rglob("*"):
            if f.is_file() and f.suffix in SOURCE_EXTS and not any(p in IGNORE_DIRS for p in f.parts):
                detected.append(SOURCE_EXTS[f.suffix])
                break

    principal = detected[0] if detected else "desconhecido"
    deps = None
    pkg = root / "package.json"
    if pkg.exists():
        try:
            d = json.loads(pkg.read_text(encoding="utf-8"))
            deps = len(d.get("dependencies", {})) + len(d.get("devDependencies", {}))
        except (json.JSONDecodeError, OSError):
            pass
    req = root / "requirements.txt"
    if deps is None and req.exists():
        deps = sum(1 for ln in req.read_text(encoding="utf-8", errors="ignore").splitlines()
                   if ln.strip() and not ln.strip().startswith("#"))

    return {"detected": sorted(set(detected)), "primary": principal, "dependencies": deps}


# --------------------------------------------------------------------------
# django: checks, migrations, contagem de models
# --------------------------------------------------------------------------

def collect_django(root, cfg):
    out = {"pending_migrations": [], "deploy_issues": [], "apps": None,
           "models": None, "version": None, "settings_module": None,
           "other_issues": []}

    manage = Path(root) / cfg.get("manage_py", "manage.py")
    if not manage.exists():
        return out

    py = cfg.get("python", sys.executable)

    rc, so, se = run([py, str(manage), "showmigrations", "--plan"], cwd=root, timeout=120)
    if rc == 0:
        for line in so.splitlines():
            if line.strip().startswith("[ ]"):
                out["pending_migrations"].append(line.strip()[3:].strip())
    else:
        nao_medido(out, "pending_migrations", _motivo(rc, se))

    # `check --deploy` roda com o settings do AMBIENTE ATUAL. Em maquina de
    # dev isso significa avisar sobre DEBUG/SSL que so valem em producao —
    # ruido garantido. Quem quiser o retrato de producao aponta o modulo:
    #   [django] settings_module = "meuprojeto.settings.production"
    env_check = None
    settings_mod = cfg.get("django", {}).get("settings_module") if isinstance(cfg.get("django"), dict) else None
    if settings_mod:
        env_check = {**os.environ, "DJANGO_SETTINGS_MODULE": settings_mod}
    out["settings_module"] = settings_mod or os.environ.get("DJANGO_SETTINGS_MODULE")
    # Sem `[django] settings_module` no toml, o check reflete a maquina de
    # DEV — e ali DEBUG=True e a falta de HSTS/SSL sao esperados, nao
    # defeito. Marcar isso evita o pior erro possivel num relatorio de
    # auditoria: acusar de inseguro um sistema cuja producao esta correta
    # (aconteceu no 1o uso real — 5 "avisos de seguranca" que sumiam com o
    # settings de producao).
    out["ambiente_de_producao"] = bool(settings_mod)

    rc, so, se = run([py, str(manage), "check", "--deploy"], cwd=root,
                     timeout=120, env=env_check)
    blob = so + se
    # O returncode NAO serve de sinal: `check` sai 1 tambem quando RODOU e
    # achou ERROR (medido em 2026-08-13 no ion — exit 1 por drf_spectacular
    # com o security.W009 devidamente reportado). O sinal de que o comando
    # chegou a medir e a saida ter FORMATO de check; um traceback nao tem.
    rodou = bool(re.search(r"System check identified|^\?\:", blob, re.M))
    if not rodou:
        nao_medido(out, "deploy_issues", _motivo(rc, se))
        nao_medido(out, "other_issues", _motivo(rc, se))
    else:
        # Separa o que e SEGURANCA (security.*) do que e recado de biblioteca
        # (drf_spectacular, staticfiles...). Misturar os dois fazia o painel
        # gritar "configuracao insegura" por causa de um schema de API —
        # achado 2026-08-12: 10 "avisos de seguranca" e nenhum era de
        # seguranca.
        for m in re.finditer(r"^\?\:\s*\((\w+\.\w+)\)\s*(.+)$", blob, re.M):
            item = {"code": m.group(1), "message": m.group(2).strip()[:200]}
            alvo = "deploy_issues" if m.group(1).startswith("security.") else "other_issues"
            out[alvo].append(item)
        if not out["deploy_issues"]:
            for m in re.finditer(r"\((security\.\w+)\)", blob):
                out["deploy_issues"].append({"code": m.group(1), "message": ""})

    # contagem de models sem subir o Django: conta declaracoes em models.py
    model_count = app_count = 0
    for f in Path(root).rglob("models.py"):
        if any(p in IGNORE_DIRS for p in f.parts):
            continue
        try:
            src = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        model_count += len(re.findall(r"^class\s+\w+\(.*models\.Model", src, re.M))
    for f in Path(root).rglob("apps.py"):
        if not any(p in IGNORE_DIRS for p in f.parts):
            app_count += 1
    out["models"] = model_count
    out["apps"] = app_count

    req = Path(root) / "requirements.txt"
    if req.exists():
        m = re.search(r"^[Dd]jango[=><~]+([\d.]+)", req.read_text(encoding="utf-8", errors="ignore"), re.M)
        if m:
            out["version"] = m.group(1)
    return out


# --------------------------------------------------------------------------
# git: ritmo e hotspots (churn x complexidade)
# --------------------------------------------------------------------------

def collect_git(root, cfg):
    out = {"branch": None, "commit": None, "commits_30d": 0, "commits_90d": 0,
           "authors_30d": [], "hotspots": [], "age_days": None}

    rc, so, se = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=root)
    if rc != 0:
        # Sem git nao ha o que medir — e "0 commits" seria um veredito sobre
        # o ritmo do time que ninguem apurou.
        motivo = _motivo(rc, se)
        for campo in ("commits_30d", "commits_90d", "authors_30d",
                      "hotspots", "age_days"):
            nao_medido(out, campo, motivo)
        return out
    out["branch"] = so.strip()

    _, so, _ = run(["git", "rev-parse", "--short", "HEAD"], cwd=root)
    out["commit"] = so.strip()

    for window, key in (("30 days ago", "commits_30d"), ("90 days ago", "commits_90d")):
        rc, so, se = run(["git", "rev-list", "--count", f"--since={window}", "HEAD"], cwd=root)
        if rc != 0:
            nao_medido(out, key, _motivo(rc, se))
        else:
            out[key] = int(so.strip() or 0)

    rc, so, se = run(["git", "shortlog", "-sn", "--since=30 days ago", "HEAD"], cwd=root)
    if rc != 0:
        nao_medido(out, "authors_30d", _motivo(rc, se))
    else:
        for line in so.splitlines():
            parts = line.strip().split("\t")
            if len(parts) == 2:
                out["authors_30d"].append({"author": parts[1], "commits": int(parts[0])})

    rc, so, se = run(["git", "log", "--reverse", "--format=%ct", "-1"], cwd=root)
    if rc != 0:
        nao_medido(out, "age_days", _motivo(rc, se))
    elif so.strip():
        first = datetime.fromtimestamp(int(so.strip()), tz=timezone.utc)
        out["age_days"] = (datetime.now(timezone.utc) - first).days

    achado = hotspots(root, cfg)
    if achado is None:
        nao_medido(out, "hotspots", "git log falhou")
    else:
        out["hotspots"] = achado
    return out


def hotspots(root, cfg):
    """
    Cruza churn (quantas vezes o arquivo mudou) com complexidade.

    O numero de linhas sozinho nao diz onde dói. Um arquivo grande e estavel
    voce nunca abre. O que machuca eh o arquivo que voce mexe toda semana E
    que ninguem entende - por isso o cruzamento.
    """
    window = cfg.get("hotspot_window", "180 days ago")
    rc, so, _ = run(["git", "log", f"--since={window}", "--name-only", "--format="], cwd=root)
    if rc != 0:
        return None  # nao ha historico pra cruzar; quem chama marca nao-medido
    churn = Counter(
        line.strip() for line in so.splitlines()
        if line.strip() and Path(line.strip()).suffix in SOURCE_EXTS
        and not any(p in IGNORE_DIRS for p in Path(line.strip()).parts)
    )
    if not churn:
        return []

    # complexidade por arquivo via radon (soma dos blocos)
    per_file = {}
    rc, so, _ = run([sys.executable, "-m", "radon", "cc", "-j",
                     "--ignore", radon_ignore(), "."], cwd=root)
    if rc == 0 and so.strip():
        try:
            data = json.loads(so)
            for fname, items in data.items():
                if isinstance(items, list):
                    per_file[rel(fname, root)] = sum(b.get("complexity", 0) for b in items)
        except json.JSONDecodeError:
            pass

    rows = []
    for path, times in churn.most_common(200):
        f = Path(root) / path
        if not f.exists():
            continue
        try:
            loc = sum(1 for ln in f.read_text(encoding="utf-8", errors="ignore").splitlines() if ln.strip())
        except OSError:
            continue
        cx = per_file.get(path)
        if cx is None:
            # Fora do Python nao ha radon. Contar ramificacoes eh uma
            # aproximacao grosseira, mas serve pro que o mapa precisa:
            # ordenar arquivos entre si, nao produzir um numero absoluto.
            try:
                src = f.read_text(encoding="utf-8", errors="ignore")
                cx = len(re.findall(BRANCH_WORDS, src))
            except OSError:
                cx = 0
        rows.append({"file": path, "churn": times, "complexity": cx, "loc": loc,
                     "score": times * cx})
    rows.sort(key=lambda x: -x["score"])
    return rows[:40]


# --------------------------------------------------------------------------
# banco: tamanho, indices ociosos, cache hit, queries caras
# --------------------------------------------------------------------------

DB_QUERIES = {
    "size": """
        SELECT pg_database_size(current_database()) AS bytes,
               current_database() AS name;
    """,
    "tables": """
        SELECT relname AS table,
               n_live_tup AS live_rows,
               n_dead_tup AS dead_rows,
               pg_total_relation_size(relid) AS total_bytes,
               pg_indexes_size(relid) AS index_bytes,
               seq_scan, idx_scan
        FROM pg_stat_user_tables
        ORDER BY pg_total_relation_size(relid) DESC
        LIMIT 25;
    """,
    "unused_indexes": """
        SELECT s.relname AS table, s.indexrelname AS index,
               pg_relation_size(s.indexrelid) AS bytes, s.idx_scan
        FROM pg_stat_user_indexes s
        JOIN pg_index i ON i.indexrelid = s.indexrelid
        WHERE s.idx_scan < 50
          AND NOT i.indisunique
          AND NOT i.indisprimary
          AND pg_relation_size(s.indexrelid) > 1024 * 512
        ORDER BY pg_relation_size(s.indexrelid) DESC
        LIMIT 20;
    """,
    "cache_hit": """
        SELECT sum(heap_blks_hit) AS hit, sum(heap_blks_read) AS read
        FROM pg_statio_user_tables;
    """,
    "connections": """
        SELECT count(*) FILTER (WHERE state = 'active') AS active,
               count(*) FILTER (WHERE state = 'idle') AS idle,
               count(*) AS total,
               (SELECT setting::int FROM pg_settings WHERE name = 'max_connections') AS max
        FROM pg_stat_activity
        WHERE datname = current_database();
    """,
    "slow_queries": """
        SELECT left(query, 160) AS query, calls,
               round(mean_exec_time::numeric, 2) AS mean_ms,
               round(total_exec_time::numeric / 1000, 1) AS total_s
        FROM pg_stat_statements
        WHERE query NOT LIKE '%pg_stat%'
        ORDER BY total_exec_time DESC
        LIMIT 15;
    """,
}


def collect_db(root, cfg):
    """
    Consultas somente-leitura em catalogo/estatistica. Nunca toca em dado de
    negocio - o objetivo eh saude do banco, nao conteudo.
    """
    dsn = (env_or(cfg.get("db", {}), "dsn", "RUCHX_DATABASE_URL")
           or os.environ.get("METRICAS_DATABASE_URL")
           or os.environ.get("DATABASE_URL"))
    if not dsn:
        raise RuntimeError(
            "sem DSN. Defina RUCHX_DATABASE_URL (ou DATABASE_URL) apontando "
            "para um usuario somente-leitura."
        )

    try:
        import psycopg
        connect = lambda: psycopg.connect(dsn, connect_timeout=10)  # noqa: E731
        dict_rows = True
    except ImportError:
        try:
            import psycopg2
            import psycopg2.extras
            connect = lambda: psycopg2.connect(dsn, connect_timeout=10)  # noqa: E731
            dict_rows = False
        except ImportError:
            raise RuntimeError("instale psycopg[binary] ou psycopg2-binary para coletar o banco")

    out = {}
    conn = connect()
    try:
        conn.autocommit = True
        for key, sql in DB_QUERIES.items():
            try:
                if dict_rows:
                    import psycopg.rows
                    cur = conn.cursor(row_factory=psycopg.rows.dict_row)
                else:
                    import psycopg2.extras
                    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute("SET statement_timeout = '15s';")
                cur.execute(sql)
                rows = [dict(r) for r in cur.fetchall()]
                cur.close()
                out[key] = rows
            except Exception as exc:  # noqa: BLE001
                # pg_stat_statements costuma nao estar instalado - nao eh erro fatal
                out[key] = {"unavailable": str(exc).strip().splitlines()[0][:160]}
    finally:
        conn.close()

    hit = out.get("cache_hit")
    if isinstance(hit, list) and hit:
        h, r = hit[0].get("hit") or 0, hit[0].get("read") or 0
        out["cache_hit_ratio"] = round(100 * h / (h + r), 2) if (h + r) else None

    tables = out.get("tables")
    if isinstance(tables, list):
        out["bloat_suspects"] = [
            {"table": t["table"], "dead_rows": t["dead_rows"], "live_rows": t["live_rows"],
             "dead_pct": round(100 * t["dead_rows"] / max(t["live_rows"] + t["dead_rows"], 1), 1)}
            for t in tables
            if t.get("dead_rows", 0) > 10000
            and t["dead_rows"] > 0.2 * max(t.get("live_rows", 0), 1)
        ]
        out["seq_scan_suspects"] = [
            {"table": t["table"], "seq_scan": t["seq_scan"], "idx_scan": t["idx_scan"],
             "live_rows": t["live_rows"]}
            for t in tables
            if t.get("live_rows", 0) > 5000 and t.get("seq_scan", 0) > 10 * max(t.get("idx_scan") or 0, 1)
        ]
    return out


# --------------------------------------------------------------------------
# infra: containers docker (local ou remoto via DOCKER_HOST=ssh://)
# --------------------------------------------------------------------------

def collect_infra(root, cfg):
    """
    Easypanel roda Docker por baixo. Em vez de adivinhar a API dele, aponta o
    docker client pro host: DOCKER_HOST=ssh://usuario@servidor. Mesma leitura,
    zero acoplamento com a versao do painel.
    """
    if not has("docker"):
        raise RuntimeError("docker client nao encontrado")

    icfg = cfg.get("infra", {})
    env = {}
    host = (env_or(icfg, "docker_host", "RUCHX_DOCKER_HOST")
            or os.environ.get("METRICAS_DOCKER_HOST") or os.environ.get("DOCKER_HOST"))
    if host:
        env["DOCKER_HOST"] = host

    out = {"host": host or "local", "containers": [], "images": [], "disk": None}

    fmt = "{{json .}}"
    rc, so, se = run(["docker", "stats", "--no-stream", "--format", fmt], env=env, timeout=60)
    if rc != 0:
        raise RuntimeError(se.strip()[:200] or "docker stats falhou")
    for line in so.splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        out["containers"].append({
            "name": d.get("Name"), "cpu": d.get("CPUPerc"), "mem": d.get("MemUsage"),
            "mem_pct": d.get("MemPerc"), "net": d.get("NetIO"), "block": d.get("BlockIO"),
        })

    rc, so, _ = run(["docker", "ps", "--format", fmt], env=env, timeout=60)
    status = {}
    for line in so.splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        status[d.get("Names")] = {"image": d.get("Image"), "status": d.get("Status"),
                                  "state": d.get("State")}
    for c in out["containers"]:
        c.update(status.get(c["name"], {}))

    rc, so, _ = run(["docker", "system", "df", "--format", fmt], env=env, timeout=60)
    disk = []
    for line in so.splitlines():
        try:
            disk.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    out["disk"] = disk

    prefix = icfg.get("project_prefix")
    if prefix:
        out["containers"] = [c for c in out["containers"] if prefix in (c.get("name") or "")]
    return out


# --------------------------------------------------------------------------
# ci: github actions via gh cli
# --------------------------------------------------------------------------

def collect_ci(root, cfg):
    if not has("gh"):
        raise RuntimeError("gh cli nao encontrado (brew/apt install gh, depois gh auth login)")

    limit = cfg.get("ci", {}).get("limit", 40)
    fields = "conclusion,createdAt,updatedAt,displayTitle,workflowName,headBranch,event"
    rc, so, se = run(["gh", "run", "list", "--limit", str(limit), "--json", fields], cwd=root)
    if rc != 0:
        raise RuntimeError(se.strip()[:200] or "gh run list falhou")

    runs = json.loads(so or "[]")
    parsed = []
    for r in runs:
        dur = None
        try:
            a = datetime.fromisoformat(r["createdAt"].replace("Z", "+00:00"))
            b = datetime.fromisoformat(r["updatedAt"].replace("Z", "+00:00"))
            dur = round((b - a).total_seconds(), 1)
        except (KeyError, ValueError):
            pass
        parsed.append({"workflow": r.get("workflowName"), "conclusion": r.get("conclusion"),
                       "branch": r.get("headBranch"), "title": (r.get("displayTitle") or "")[:80],
                       "created_at": r.get("createdAt"), "duration_s": dur})

    done = [r for r in parsed if r["conclusion"] in ("success", "failure")]
    durs = [r["duration_s"] for r in done if r["duration_s"]]
    return {
        "runs_analyzed": len(parsed),
        "success_rate": round(100 * sum(1 for r in done if r["conclusion"] == "success") / len(done), 1) if done else None,
        "avg_duration_s": round(sum(durs) / len(durs), 1) if durs else None,
        "max_duration_s": max(durs) if durs else None,
        "recent": parsed[:15],
    }


# --------------------------------------------------------------------------
# orquestracao
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# governance: o que um auditor olha antes de olhar codigo
# --------------------------------------------------------------------------

# Padroes de segredo commitado. Deliberadamente conservadores: um falso
# positivo custa a confianca do relatorio inteiro.
SECRET_PATTERNS = [
    ("AWS access key", r"\bAKIA[0-9A-Z]{16}\b"),
    ("chave privada", r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"),
    ("token do GitHub", r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    ("token do Slack", r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    ("chave da OpenAI", r"\bsk-[A-Za-z0-9]{32,}\b"),
    ("chave da Anthropic", r"\bsk-ant-[A-Za-z0-9_-]{32,}\b"),
    ("DSN com senha", r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^\s:@/]+:[^\s:@/]{6,}@"),
    ("senha em atribuicao", r"(?i)\b(?:password|senha|secret_key|api_key)\s*[=:]\s*[\"'][^\"'\s${}]{16,}[\"']"),
]

# Valor que casa com isso e senha de teste, nao segredo. Sem esta lista o
# relatorio acusa `password="testpass123"` de suite e perde a credibilidade —
# e um alarme falso num relatorio de auditoria contamina todos os outros
# achados (visto no 1o uso real, 2026-08-12: 8 de 8 "segredos" eram fixture).
VALORES_DE_TESTE = re.compile(
    r"(?i)(test|senha|password|dummy|exemplo|example|changeme|placeholder|"
    r"fake|mock|sample|foobar|123456|xxx+|secret)"
)


def _segredo_plausivel(rotulo, trecho, texto, pos):
    """Segundo filtro: o match parece segredo DE VERDADE?

    Doc tecnica fala sobre segredo o tempo todo — "salve o `-----BEGIN PRIVATE
    KEY-----`", `postgres://user:<senha>@host`. Sem esta checagem o relatorio
    acusa a propria documentacao de vazar credencial (visto no 1o uso real).
    """
    # Placeholder explicito em qualquer achado: <senha>, ${VAR}, ***, xxx
    if re.search(r"[<>${}]|\*{3,}|x{4,}", trecho, re.I):
        return False
    if rotulo == "senha em atribuicao":
        return not VALORES_DE_TESTE.search(trecho)
    if rotulo == "chave privada":
        # Chave real tem corpo base64 logo abaixo do cabecalho; mencao em doc
        # vem sozinha na linha, entre crases ou aspas.
        corpo = texto[pos:pos + 400].splitlines()[1:4]
        return any(re.fullmatch(r"[A-Za-z0-9+/=]{40,}", ln.strip()) for ln in corpo)
    return True


def _e_arquivo_de_teste(rel_path):
    p = rel_path.lower().replace("\\", "/")
    return (
        "/tests/" in p or p.startswith("tests/")
        or re.search(r"(^|/)(test_|tests_|conftest)", p) is not None
        or re.search(r"(_test|\.test|\.spec)\.[a-z]+$", p) is not None
        or "/fixtures/" in p or "factories" in p
    )

DOCS_ESPERADOS = {
    "readme": ["README.md", "README.rst", "README.txt", "readme.md"],
    "licenca": ["LICENSE", "LICENSE.md", "LICENCA", "LICENSE.txt"],
    "contributing": ["CONTRIBUTING.md", "docs/CONTRIBUTING.md"],
    "changelog": ["CHANGELOG.md", "docs/CHANGELOG.md"],
    "security": ["SECURITY.md", ".github/SECURITY.md"],
    "instrucoes_agente": ["CLAUDE.md", "AGENTS.md", ".cursorrules", "GEMINI.md"],
}


def _achar_doc(root, nomes):
    for nome in nomes:
        p = Path(root) / nome
        if p.exists():
            return nome
    return None


def _varre_segredos(root, limite=8):
    """Procura segredo em arquivo VERSIONADO (git ls-files).

    So o que o git rastreia importa: segredo em .env local nao vaza, segredo
    commitado vaza pra sempre — mesmo que apagado depois, fica no historico.
    """
    rc, so, _ = run(["git", "ls-files"], cwd=root, timeout=60)
    if rc != 0:
        return []
    achados = []
    exts_ignoradas = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".ico", ".woff",
                      ".woff2", ".ttf", ".zip", ".gz", ".lock", ".svg", ".webp"}
    for rel_path in so.splitlines():
        if len(achados) >= limite:
            break
        p = Path(root) / rel_path
        if p.suffix.lower() in exts_ignoradas or not p.is_file():
            continue
        if _e_arquivo_de_teste(rel_path):
            continue
        try:
            if p.stat().st_size > 1_500_000:
                continue
            texto = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for rotulo, padrao in SECRET_PATTERNS:
            m = re.search(padrao, texto)
            if not m:
                continue
            if not _segredo_plausivel(rotulo, m.group(0), texto, m.start()):
                continue
            linha = texto[:m.start()].count("\n") + 1
            achados.append({"file": rel_path, "line": linha, "kind": rotulo})
            break
    return achados


def _analisa_workflows(root):
    """Actions sem pin e workflow sem `permissions` — dois vetores conhecidos.

    Action referenciada por tag movel (@v4) executa o que o dono publicar
    amanha dentro do seu CI, com os seus secrets. Workflow sem bloco
    `permissions` herda o token com escopo amplo demais.
    """
    wdir = Path(root) / ".github" / "workflows"
    out = {"count": 0, "sem_pin": [], "sem_permissions": [], "arquivos": []}
    if not wdir.is_dir():
        return out
    for f in sorted(list(wdir.glob("*.yml")) + list(wdir.glob("*.yaml"))):
        out["count"] += 1
        out["arquivos"].append(f.name)
        try:
            texto = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        # `permissions:` precisa estar no TOPO do workflow (coluna 0). O mesmo
        # bloco dentro de um job so protege aquele job — os demais seguem
        # herdando o token amplo. Sem a ancora, o auditor SUBESTIMAVA o
        # problema (dizia 1 workflow exposto quando eram 3; achado do 1o uso).
        if not re.search(r"^permissions\s*:", texto, re.M):
            out["sem_permissions"].append(f.name)
        for m in re.finditer(r"uses:\s*([\w.-]+/[\w.-]+)@([\w.\-/]+)", texto):
            repo_action, ref = m.group(1), m.group(2)
            if not re.fullmatch(r"[0-9a-f]{40}", ref):
                item = f"{repo_action}@{ref}"
                if item not in out["sem_pin"]:
                    out["sem_pin"].append(item)
    return out


def _branch_protection(root):
    """Regra de protecao do branch default, via API do GitHub."""
    if not has("gh"):
        return {"disponivel": False, "motivo": "gh cli ausente"}
    rc, so, _ = run(["gh", "repo", "view", "--json",
                     "defaultBranchRef,nameWithOwner,visibility"], cwd=root)
    if rc != 0:
        return {"disponivel": False, "motivo": "repo sem remote GitHub"}
    try:
        info = json.loads(so)
    except json.JSONDecodeError:
        return {"disponivel": False, "motivo": "resposta invalida"}
    branch = (info.get("defaultBranchRef") or {}).get("name") or "main"
    slug = info.get("nameWithOwner")
    out = {"disponivel": True, "repo": slug, "branch": branch,
           "visibility": info.get("visibility"), "protegido": False,
           "exige_review": None, "exige_checks": None}
    rc, so, _ = run(["gh", "api", f"repos/{slug}/branches/{branch}/protection"], cwd=root)
    if rc != 0:
        return out  # 404 = sem protecao; e a resposta que interessa
    try:
        prot = json.loads(so)
    except json.JSONDecodeError:
        return out
    out["protegido"] = True
    pr = prot.get("required_pull_request_reviews") or {}
    out["exige_review"] = pr.get("required_approving_review_count", 0) if pr else 0
    checks = (prot.get("required_status_checks") or {}).get("contexts")
    out["exige_checks"] = len(checks) if isinstance(checks, list) else 0
    return out


def _deps_desatualizadas(root, cfg):
    """Quantas dependencias estao para tras. Dependencia velha e divida que
    rende juros: quanto mais tempo passa, mais caro fica o upgrade."""
    out = {"ferramenta": None, "total": None, "desatualizadas": None, "exemplos": []}
    py = cfg.get("python", sys.executable)
    if (Path(root) / "requirements.txt").exists() or (Path(root) / "pyproject.toml").exists():
        rc, so, _ = run([py, "-m", "pip", "list", "--outdated", "--format", "json"],
                        cwd=root, timeout=180)
        if rc == 0 and so.strip():
            try:
                itens = json.loads(so)
                rc2, so2, _ = run([py, "-m", "pip", "list", "--format", "json"],
                                  cwd=root, timeout=120)
                total = len(json.loads(so2)) if rc2 == 0 and so2.strip() else None
                out.update({
                    "ferramenta": "pip", "total": total, "desatualizadas": len(itens),
                    "exemplos": [{"nome": i.get("name"), "atual": i.get("version"),
                                  "ultima": i.get("latest_version")} for i in itens[:8]],
                })
                return out
            except json.JSONDecodeError:
                pass
    if (Path(root) / "package.json").exists() and has("npm"):
        rc, so, _ = run(["npm", "outdated", "--json"], cwd=root, timeout=180)
        if so.strip():
            try:
                itens = json.loads(so)
                out.update({"ferramenta": "npm", "desatualizadas": len(itens),
                            "exemplos": [{"nome": k, "atual": v.get("current"),
                                          "ultima": v.get("latest")}
                                         for k, v in list(itens.items())[:8]]})
            except json.JSONDecodeError:
                pass
    return out


def collect_governance(root, cfg):
    """Governanca: docs, protecao de branch, supply chain, segredo commitado.

    Nada aqui exige instalar coisa no projeto do cliente — le arquivo, git e
    a API do GitHub. E o que separa "repositorio de codigo" de "projeto de
    engenharia" numa auditoria.
    """
    root = Path(root)
    docs = {chave: _achar_doc(root, nomes) for chave, nomes in DOCS_ESPERADOS.items()}

    # Pastas de documentacao viva: ADR, runbook, docs/
    def _tem_dir(*nomes):
        for n in nomes:
            p = root / n
            if p.is_dir() and any(p.rglob("*.md")):
                return n
        return None

    docs["adr"] = _tem_dir("docs/adr", "docs/decisions", "adr", "docs/decisoes")
    docs["runbooks"] = _tem_dir("docs/runbooks", "runbooks", "docs/deploy/runbooks")
    docs["docs_dir"] = _tem_dir("docs", "documentation")

    gitignore = root / ".gitignore"
    ignora_env = False
    if gitignore.exists():
        txt = gitignore.read_text(encoding="utf-8", errors="ignore")
        ignora_env = bool(re.search(r"^\s*\.env", txt, re.M))

    return {
        "docs": docs,
        "gitignore": {"existe": gitignore.exists(), "ignora_env": ignora_env},
        "dependabot": (root / ".github" / "dependabot.yml").exists()
                      or (root / ".github" / "renovate.json").exists(),
        "pre_commit": (root / ".pre-commit-config.yaml").exists(),
        "editorconfig": (root / ".editorconfig").exists(),
        "containerizado": (root / "Dockerfile").exists()
                          or bool(list(root.glob("docker-compose*.y*ml"))),
        "workflows": _analisa_workflows(root),
        "branch_protection": _branch_protection(root),
        "segredos_commitados": _varre_segredos(root),
        "dependencias": _deps_desatualizadas(root, cfg),
    }


# --------------------------------------------------------------------------
# dora: as 4 metricas que dizem se o time entrega bem
# --------------------------------------------------------------------------

def _e_deploy(nome_workflow, cfg):
    palavras = cfg.get("dora", {}).get("deploy_keywords") or ["deploy", "release", "publish", "cd"]
    n = (nome_workflow or "").lower()
    return any(p in n for p in palavras)


def collect_dora(root, cfg):
    """DORA (DevOps Research and Assessment): frequencia de deploy, lead time,
    taxa de falha de mudanca e tempo de recuperacao.

    Sao as 4 metricas que a industria usa pra comparar times — e as unicas do
    painel que um socio nao-tecnico entende sem tradutor. Derivadas do
    historico de Actions + git, sem instrumentar nada.
    """
    if not has("gh"):
        raise RuntimeError("gh cli nao encontrado — DORA sai do historico do GitHub Actions")

    limite = cfg.get("dora", {}).get("limit", 120)
    campos = "conclusion,createdAt,updatedAt,workflowName,headBranch,headSha,event"
    rc, so, se = run(["gh", "run", "list", "--limit", str(limite), "--json", campos], cwd=root)
    if rc != 0:
        raise RuntimeError(se.strip()[:200] or "gh run list falhou")
    runs = json.loads(so or "[]")

    def _dt(valor):
        try:
            return datetime.fromisoformat((valor or "").replace("Z", "+00:00"))
        except ValueError:
            return None

    # Branch de producao: so o que roda nela conta como deploy. Sem esse
    # filtro, run de PR e de branch de feature inflam a frequencia (visto no
    # 1o uso real: 50 "deploys por semana" contando CI de pull request).
    branch_prod = cfg.get("dora", {}).get("branch")
    if not branch_prod:
        rc_b, so_b, _ = run(["gh", "repo", "view", "--json", "defaultBranchRef"], cwd=root)
        try:
            branch_prod = (json.loads(so_b).get("defaultBranchRef") or {}).get("name")
        except (json.JSONDecodeError, AttributeError):
            branch_prod = "main"

    deploys = []
    for r in runs:
        if not _e_deploy(r.get("workflowName"), cfg):
            continue
        if r.get("conclusion") not in ("success", "failure"):
            continue
        if branch_prod and r.get("headBranch") != branch_prod:
            continue
        if r.get("event") not in (None, "push", "workflow_dispatch"):
            continue
        deploys.append({
            "quando": _dt(r.get("createdAt")), "fim": _dt(r.get("updatedAt")),
            "ok": r.get("conclusion") == "success", "sha": r.get("headSha"),
            "workflow": r.get("workflowName"),
        })
    deploys = [d for d in deploys if d["quando"]]
    deploys.sort(key=lambda d: d["quando"])

    out = {"workflows_de_deploy": sorted({d["workflow"] for d in deploys}),
           "branch": branch_prod, "deploys_analisados": len(deploys),
           "janela_dias": None, "deploys_por_semana": None,
           "lead_time_p50_h": None, "change_failure_rate": None,
           "mttr_h": None, "ultimo_deploy": None}
    if not deploys:
        return out

    janela = (deploys[-1]["quando"] - deploys[0]["quando"]).total_seconds() / 86400 or 1
    out["janela_dias"] = round(janela, 1)
    out["deploys_por_semana"] = round(len(deploys) / (janela / 7), 1)
    out["ultimo_deploy"] = deploys[-1]["quando"].isoformat(timespec="seconds")
    falhas = [d for d in deploys if not d["ok"]]
    out["change_failure_rate"] = round(100 * len(falhas) / len(deploys), 1)

    # Lead time: do commit ate o deploy que o levou (mediana).
    leads = []
    for d in deploys:
        if not d["sha"] or not d["ok"]:
            continue
        rc, so, _ = run(["git", "show", "-s", "--format=%cI", d["sha"]], cwd=root, timeout=30)
        commit_dt = _dt(so.strip()) if rc == 0 else None
        if commit_dt:
            # Ate o FIM do run: lead time DORA e commit -> em producao, nao
            # commit -> pipeline comecou.
            horas = ((d["fim"] or d["quando"]) - commit_dt).total_seconds() / 3600
            if 0 <= horas < 24 * 30:
                leads.append(horas)
    if leads:
        leads.sort()
        out["lead_time_p50_h"] = round(leads[len(leads) // 2], 2)

    # MTTR: da falha ate o proximo deploy verde no mesmo workflow.
    recuperacoes = []
    for i, d in enumerate(deploys):
        if d["ok"]:
            continue
        for seguinte in deploys[i + 1:]:
            if seguinte["workflow"] == d["workflow"] and seguinte["ok"]:
                recuperacoes.append((seguinte["fim"] or seguinte["quando"]
                                     ) - (d["fim"] or d["quando"]))
                break
    if recuperacoes:
        horas = sorted(r.total_seconds() / 3600 for r in recuperacoes)
        out["mttr_h"] = round(horas[len(horas) // 2], 2)
    return out


REGISTRY = {
    "stack": collect_stack, "code": collect_code, "quality": collect_quality,
    "tests": collect_tests, "django": collect_django, "git": collect_git,
    "db": collect_db, "infra": collect_infra, "ci": collect_ci,
    "governance": collect_governance, "dora": collect_dora,
}


def main():
    ap = argparse.ArgumentParser(description="Coleta metricas do projeto")
    ap.add_argument("--root", default=".")
    ap.add_argument("--config", default=None)
    ap.add_argument("--only", help="lista separada por virgula")
    ap.add_argument("--skip", help="lista separada por virgula")
    ap.add_argument("--out", help="caminho do snapshot (padrao .metricas/<data>.json)")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    # ruch-x.toml eh o nome atual; metricas.toml continua valendo pra quem ja tinha.
    if args.config:
        cfg = load_config(root / args.config)
    else:
        cfg = load_config(root / "ruch-x.toml") or load_config(root / "metricas.toml")

    selected = COLLECTORS
    if args.only:
        selected = [c.strip() for c in args.only.split(",") if c.strip() in REGISTRY]
    if args.skip:
        skip = {c.strip() for c in args.skip.split(",")}
        selected = [c for c in selected if c not in skip]

    snapshot = {
        "schema": SCHEMA_VERSION,
        "project": cfg.get("project") or root.name,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "collectors_run": [],
        "errors": {},
    }

    for name in selected:
        print(f"  coletando {name}...", file=sys.stderr, flush=True)
        try:
            snapshot[name] = REGISTRY[name](root, cfg)
            snapshot["collectors_run"].append(name)
        except Exception as exc:  # noqa: BLE001
            snapshot["errors"][name] = str(exc)[:300]
            print(f"    ! {name}: {exc}", file=sys.stderr)

    # Se ja existe historico na pasta antiga, continua gravando la em vez de
    # comecar do zero - o valor da ferramenta esta na serie acumulada.
    legado = root / SNAPSHOT_DIR_LEGADO
    outdir = legado if legado.is_dir() else root / SNAPSHOT_DIR
    outdir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d")
    path = Path(args.out) if args.out else outdir / f"{stamp}.json"
    path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    (outdir / "latest.json").write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    print(f"\nsnapshot: {path}", file=sys.stderr)
    if snapshot["errors"]:
        print(f"coletores com falha: {', '.join(snapshot['errors'])}", file=sys.stderr)
    print(str(path))


if __name__ == "__main__":
    main()
