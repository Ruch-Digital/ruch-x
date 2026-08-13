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

SCHEMA_VERSION = 1
SNAPSHOT_DIR = ".ruch-x"
SNAPSHOT_DIR_LEGADO = ".metricas"
COLLECTORS = ["stack", "code", "quality", "tests", "django", "git", "db",
              "infra", "ci"]


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

    # `check --deploy` roda com o settings do AMBIENTE ATUAL. Em maquina de
    # dev isso significa avisar sobre DEBUG/SSL que so valem em producao —
    # ruido garantido. Quem quiser o retrato de producao aponta o modulo:
    #   [django] settings_module = "meuprojeto.settings.production"
    env_check = None
    settings_mod = cfg.get("django", {}).get("settings_module") if isinstance(cfg.get("django"), dict) else None
    if settings_mod:
        env_check = {**os.environ, "DJANGO_SETTINGS_MODULE": settings_mod}
    out["settings_module"] = settings_mod or os.environ.get("DJANGO_SETTINGS_MODULE")

    rc, so, se = run([py, str(manage), "check", "--deploy"], cwd=root,
                     timeout=120, env=env_check)
    blob = so + se
    # Separa o que e SEGURANCA (security.*) do que e recado de biblioteca
    # (drf_spectacular, staticfiles...). Misturar os dois fazia o painel
    # gritar "configuracao insegura" por causa de um schema de API — achado
    # 2026-08-12: 10 "avisos de seguranca" e nenhum era de seguranca.
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

    rc, so, _ = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=root)
    if rc != 0:
        return out
    out["branch"] = so.strip()

    _, so, _ = run(["git", "rev-parse", "--short", "HEAD"], cwd=root)
    out["commit"] = so.strip()

    for window, key in (("30 days ago", "commits_30d"), ("90 days ago", "commits_90d")):
        _, so, _ = run(["git", "rev-list", "--count", f"--since={window}", "HEAD"], cwd=root)
        out[key] = int(so.strip() or 0)

    _, so, _ = run(["git", "shortlog", "-sn", "--since=30 days ago", "HEAD"], cwd=root)
    for line in so.splitlines():
        parts = line.strip().split("\t")
        if len(parts) == 2:
            out["authors_30d"].append({"author": parts[1], "commits": int(parts[0])})

    _, so, _ = run(["git", "log", "--reverse", "--format=%ct", "-1"], cwd=root)
    if so.strip():
        first = datetime.fromtimestamp(int(so.strip()), tz=timezone.utc)
        out["age_days"] = (datetime.now(timezone.utc) - first).days

    out["hotspots"] = hotspots(root, cfg)
    return out


def hotspots(root, cfg):
    """
    Cruza churn (quantas vezes o arquivo mudou) com complexidade.

    O numero de linhas sozinho nao diz onde dói. Um arquivo grande e estavel
    voce nunca abre. O que machuca eh o arquivo que voce mexe toda semana E
    que ninguem entende - por isso o cruzamento.
    """
    window = cfg.get("hotspot_window", "180 days ago")
    _, so, _ = run(["git", "log", f"--since={window}", "--name-only", "--format="], cwd=root)
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

REGISTRY = {
    "stack": collect_stack, "code": collect_code, "quality": collect_quality,
    "tests": collect_tests, "django": collect_django, "git": collect_git,
    "db": collect_db, "infra": collect_infra, "ci": collect_ci,
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
