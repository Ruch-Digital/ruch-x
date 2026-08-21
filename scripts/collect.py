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


def caminho_contido(root, valor):
    """Resolve `valor` DENTRO de root; devolve None se escapar ou nao existir.

    O ruch-x.toml e conteudo do repositorio auditado. Sem esta checagem,
    `manage_py = "/etc/passwd"` vira argumento de comando e `apps_dir = "/"`
    faz a coleta varrer a maquina inteira (Path(root) / "/x" == "/x").
    """
    if not valor:
        return None
    try:
        alvo = (Path(root) / valor).resolve()
        base = Path(root).resolve()
        alvo.relative_to(base)
    except (ValueError, OSError):
        return None
    return alvo if alvo.exists() else None


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


# Redacao do snapshot. A ferramenta manda VERSIONAR o snapshot, entao tudo que
# entra nele e potencialmente publico. Os padroes ficam explicitos aqui — do
# mesmo jeito que os limiares da auditoria — pra poderem ser discutidos e
# estendidos por quem usa.
#
# O grupo de prefixo `(^|[^A-Za-z])` no padrao de atribuicao (chave=valor)
# existe porque `\b` nao separa "_" de letra: sem ele, `DATABASE_PASSWORD=...`
# nao seria pego (o "_" antes de "PASSWORD" nao e fronteira de palavra pro
# regex). O prefixo capturado volta no replace pra nao comer o "_"/inicio.
#
# Fix round 1 (revisao adversarial, 4 achados Critical):
# 1. DSN: a senha pode conter "@"/":" literal (ex: "p@ss:w0rd"). O padrao
#    original tinha a classe da senha excluindo "@", entao parava no
#    PRIMEIRO "@" e vazava o resto da senha como se fosse host. Trocado
#    pra `[^\s/]+` (so exclui espaco/barra, aceita "@" e ":"): sendo
#    guloso, o regex backtracka ate o ULTIMO "@" antes do host/path.
# 2. Nome composto por letras (sem separador nao-letra) tambem e credencial:
#    "PGPASSWORD" (variavel canonica do libpq) nao tem "_" nem espaco antes
#    de "PASSWORD" — adicionado como keyword explicita, do mesmo jeito que
#    os limiares da auditoria: material auditavel, nao regra generica.
#    "pwd"/"*_PWD"/"MYSQL_PWD" ja caem no `(^|[^A-Za-z])` existente (tem
#    "_" antes), so precisavam de "pwd" na lista de keywords.
# 3. Chave entre aspas (`"password": "valor"`, JSON) tem uma aspa de
#    fechamento ENTRE a keyword e o separador `[=:]` — o separador exigia
#    vir logo depois da keyword. Adicionado grupo de aspa opcional dos dois
#    lados da keyword+separador.
# 4. Bearer token e bloco PEM nao tinham padrao nenhum — adicionados como
#    padroes proprios (nao cabem no formato chave=valor).
REDACOES = [
    # senha embutida em URL/DSN: mantem usuario e host (diagnostico) e mata
    # a senha ate o ULTIMO "@" antes do host (a senha pode conter "@"/":").
    (re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://[^\s:@/]+:)[^\s/]+(@)"), r"\1***\2"),
    (re.compile(r"(?i)\b(PASSWORD)\s+'[^']*'"), r"\1 '***'"),
    (re.compile(r"(?i)(^|[^A-Za-z])(password|passwd|pwd|pgpassword|senha|"
                r"secret[_-]?key|api[_-]?key|token)"
                r"([\"']?)(\s*[=:]\s*)([\"']?)[^\s\"';,]{6,}"), r"\1\2\3\4\5***"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "***"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), "***"),
    (re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}"), "***"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "***"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"), "***"),
    # header HTTP de autenticacao: "Bearer <token>" -> mata so o token
    (re.compile(r"(?i)\b(bearer)\s+\S+"), r"\1 ***"),
    # bloco PEM (chave privada RSA/EC/OPENSSH/generica): mata o corpo,
    # mantem os marcadores BEGIN/END (o achado de que existe uma chave e
    # relevante pro diagnostico; o conteudo da chave nao)
    (re.compile(r"(-----BEGIN [A-Z ]*PRIVATE KEY-----)(.*?)(-----END [A-Z ]*PRIVATE KEY-----)",
                re.DOTALL), r"\1***\3"),
    # caminho de home. Nao e credencial, e por isso passou batido ate agora:
    # o snapshot e VERSIONADO, e o stderr de qualquer comando que falha traz o
    # caminho do interpretador — visto no 1o snapshot commitado deste proprio
    # repositorio, publico: "/Users/<usuario>/Documents/<empresa>/Projetos/
    # <projeto privado>/venv/bin/python3: No module named radon". Nome de
    # usuario, layout do disco e nome de projeto alheio nao ajudam a
    # diagnosticar nada; o RESTO do caminho ajuda, entao so o prefixo cai.
    # Quem roda a ferramenta nao vai reler o snapshot linha a linha antes de
    # commitar — a ferramenta e que tem que nao gravar isso.
    (re.compile(r"(?i)/(?:Users|home)/[^/\s:\"']+/"), "~/"),
    (re.compile(r"(?i)(?:[A-Za-z]:)?\\Users\\[^\\\s:\"']+\\"), r"~\\"),
]


def redigir(texto):
    """Mascara credencial em texto livre, preservando o resto legivel."""
    if not isinstance(texto, str):
        return texto
    for padrao, troca in REDACOES:
        texto = padrao.sub(troca, texto)
    return texto


def redigir_estrutura(obj):
    """Aplica a redacao em TODA string do snapshot, recursivamente.

    Feito na saida, uma vez, em vez de campo a campo: o proximo coletor que
    alguem escrever nao precisa lembrar de redigir nada.
    """
    if isinstance(obj, dict):
        return {k: redigir_estrutura(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redigir_estrutura(v) for v in obj]
    return redigir(obj)


# --------------------------------------------------------------------------
# codigo: linhas, linguagens, distribuicao por app
# --------------------------------------------------------------------------

def collect_code(root, cfg):
    """LOC por linguagem e por app Django. Usa scc, cai pro cloc, cai pro proprio."""
    out = {"tool": None, "total_loc": 0, "total_files": 0,
           "by_language": [], "by_app": [], "comment_ratio": None}

    if has("scc"):
        rc, so, se = run(["scc", "--format", "json", "--no-cocomo", str(root)])
        if rc != 0 or not so.strip():
            # scc instalado que falha nao pode virar "projeto com 0 linhas".
            nao_medido(out, "total_loc", _motivo(rc, se))
            nao_medido(out, "total_files", _motivo(rc, se))
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
        rc, so, se = run(["cloc", "--json", "--quiet", str(root)])
        if rc != 0 or not so.strip():
            # cloc instalado que falha nao pode virar "projeto com 0 linhas".
            nao_medido(out, "total_loc", _motivo(rc, se))
            nao_medido(out, "total_files", _motivo(rc, se))
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


# `.ruch-x` entra aqui pelo mesmo motivo que `.metricas` (o nome legado) ja
# estava: e a saida da propria ferramenta. Sem isso o `dashboard.html` gerado
# aqui e contado como codigo-fonte do projeto — no proprio ruch-x, um unico
# HTML derivado fazia a deteccao de stack devolver `['HTML']` pra uma
# ferramenta escrita em Python (medido em 2026-08-13).
IGNORE_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache",
               ".pytest_cache", ".ruff_cache", "migrations", "staticfiles", "static",
               "dist", "build", ".ruch-x", ".metricas", "htmlcov", ".tox", "vendor",
               "target", ".next", ".nuxt", "coverage", "Pods", ".gradle", "bin", "obj"}

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
    base = caminho_contido(root, configured) if configured else None
    if base and base.is_dir():
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

    # Motivo do lint nao ter saido. Fica pendurado aqui e so vira `nao_medido`
    # no fim, se nenhum dos dois caminhos (ruff, eslint) tiver medido: o
    # ARQUIVO INTEIRO segue a regra "None e nao-medido, com motivo" e estes
    # dois pontos eram a unica excecao — comando presente que falha, ou que
    # devolve saida ilegivel, deixava `ruff: null` mudo, indistinguivel de
    # "nao ha linter neste projeto".
    motivo_lint = "nenhum linter disponível (ruff ausente e sem eslint no projeto)"

    if has("ruff"):
        # `ruff check` sai 1 quando ACHA violacao — o returncode nao diz se
        # mediu. O sinal e a saida ser JSON: `[]` e "medi, esta limpo".
        rc, so, se = run(["ruff", "check", "--output-format", "json", "."], cwd=root)
        try:
            items = json.loads(so) if so.strip() else None
        except json.JSONDecodeError:
            items = None
        if isinstance(items, list):
            by_rule = Counter(i.get("code") or "?" for i in items if isinstance(i, dict))
            by_file = Counter(i.get("filename", "?") for i in items if isinstance(i, dict))
            out["ruff"] = {
                "total": len(items),
                "by_rule": [{"rule": k, "count": v} for k, v in by_rule.most_common(12)],
                "worst_files": [{"file": rel(k, root), "count": v}
                                for k, v in by_file.most_common(10)],
            }
        else:
            motivo_lint = f"ruff: {_motivo(rc, se) if rc else 'saída ilegível'}"

    if out["ruff"] is None and has("npx") and (Path(root) / "package.json").exists():
        # eslint so eh chamado se ja estiver configurado no projeto; --no-install
        # evita baixar pacote na maquina de quem esta so medindo.
        rc, so, se = run(["npx", "--no-install", "eslint", ".", "-f", "json"],
                         cwd=root, timeout=300)
        try:
            arquivos = json.loads(so) if so.strip().startswith("[") else None
        except json.JSONDecodeError:
            arquivos = None
        if isinstance(arquivos, list):
            itens = [(m.get("ruleId") or "?", a.get("filePath", "?"))
                     for a in arquivos if isinstance(a, dict)
                     for m in (a.get("messages") or []) if isinstance(m, dict)]
            by_rule = Counter(r for r, _ in itens)
            by_file = Counter(f for _, f in itens)
            out["ruff"] = {
                "tool": "eslint", "total": len(itens),
                "by_rule": [{"rule": k, "count": v} for k, v in by_rule.most_common(12)],
                "worst_files": [{"file": rel(k, root), "count": v}
                                for k, v in by_file.most_common(10)],
            }
        else:
            motivo_lint = f"eslint: {_motivo(rc, se) if rc else 'saída ilegível'}"

    if out["ruff"] is None:
        nao_medido(out, "ruff", motivo_lint)

    # radon: complexidade ciclomatica por funcao (do PROJETO, nao das libs —
    # ver radon_ignore()).
    # -P: sem o diretorio do repositorio auditado no sys.path. Sem isso, um
    # arquivo `radon.py` na raiz do projeto medido roda como __main__ na
    # maquina de quem audita.
    rc, so, se = run([sys.executable, "-P", "-m", "radon", "cc", "-j", "-s",
                     "--ignore", radon_ignore(), "."], cwd=root)
    medido = False
    if rc == 0 and so.strip():
        try:
            data = json.loads(so)
            # JSON valido com a FORMA errada (lista, string, numero) nao pode
            # estourar `.items()` e matar o coletor inteiro: cai no mesmo
            # `nao_medido` de qualquer outra saida ilegivel.
            if not isinstance(data, dict):
                raise json.JSONDecodeError("saida do radon nao e um objeto", so, 0)
            blocks = []
            for fname, items in data.items():
                if not isinstance(items, list):
                    continue
                for b in items:
                    if not isinstance(b, dict):
                        continue
                    blocks.append({
                        "file": rel(fname, root),
                        "name": b.get("name"),
                        "line": b.get("lineno"),
                        "complexity": b.get("complexity", 0),
                        "rank": b.get("rank"),
                    })
            blocks.sort(key=lambda x: -x["complexity"])
            scores = [b["complexity"] for b in blocks]
            # data valido com zero blocos (nenhum arquivo Python no projeto) eh
            # medicao real, nao ausencia de medicao - fica de fora do nao_medido.
            out["complexity"] = {
                "blocks_analyzed": len(blocks),
                "avg": round(sum(scores) / len(scores), 2) if scores else 0,
                "above_10": sum(1 for s in scores if s > 10),
                "worst": blocks[:15],
                "metodo": "radon",
            }
            medido = True
        except json.JSONDecodeError:
            pass
    if not medido:
        # radon ausente, quebrado ou saida ilegivel - nao pode virar
        # "complexidade zero" mudo, igual ao total_loc do scc/cloc.
        nao_medido(out, "complexity", _motivo(rc, se))
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
    if extra and caminho_contido(root, extra) is None:
        extra = None
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
           "duration_s": None, "slowest": [], "source": None,
           "coverage_age_days": None}

    cov_nome = cfg.get("coverage_json", "coverage.json")
    cov_path = caminho_contido(root, cov_nome) or (Path(root) / "coverage.json")
    run_tests = cfg.get("run_tests", False)

    if not cov_path.exists() and run_tests:
        args = cfg.get("pytest_args", ["-q", "--cov", "--cov-report=json", "--durations=10"])
        # -P: sem o diretorio do repositorio auditado no sys.path. Sem isso,
        # um arquivo `pytest.py` na raiz do projeto medido roda como
        # __main__ na maquina de quem audita (provado). Modulo instalado do
        # proprio projeto continua importando: o pytest insere o rootdir no
        # sys.path pelo mecanismo dele, nao pelo cwd do interpretador.
        rc, so, se = run([sys.executable, "-P", "-m", "pytest", *args], cwd=root,
                         timeout=cfg.get("test_timeout", 900))
        out["source"] = "pytest"
        m = re.search(r"(\d+) passed", so)
        if m:
            out["test_count"] = int(m.group(1))
        else:
            # Suite que nem chegou a rodar (erro de coleta, import quebrado,
            # timeout) deixava `test_count`/`duration_s` em None sem motivo —
            # o painel mostrava o campo vazio como se ninguem tivesse pedido
            # medicao. O returncode sozinho nao serve de sinal (pytest sai 1
            # com teste FALHANDO, e ai o "N passed" existe): o sinal e o
            # resumo da suite estar na saida.
            motivo = _motivo(rc, se)
            nao_medido(out, "test_count", motivo)
            nao_medido(out, "duration_s", motivo)
        m = re.search(r"in ([\d.]+)s", so)
        if m:
            out["duration_s"] = float(m.group(1))
        for line in so.splitlines():
            m = re.match(r"([\d.]+)s\s+(call|setup|teardown)\s+(.+)", line.strip())
            if m:
                out["slowest"].append({"duration_s": float(m.group(1)), "test": m.group(3)})

    def _idade(rel_path):
        """Ha quanto tempo o relatorio de cobertura foi gerado.

        A coleta le o arquivo que estiver no disco. Sem a idade, cobertura
        de tres meses atras entra no painel como se fosse de hoje.
        """
        try:
            mtime = (Path(root) / rel_path).stat().st_mtime
        except OSError:
            return None
        return int((datetime.now().timestamp() - mtime) / 86400)

    found = parse_coverage(root, cfg)
    if found:
        out["coverage_pct"] = found["pct"]
        out["source"] = found["source"]
        out["by_app"] = found.get("by_app", [])
        out["coverage_age_days"] = _idade(found["source"])
        return out

    if cov_path.exists():
        try:
            data = json.loads(cov_path.read_text(encoding="utf-8"))
            out["source"] = out["source"] or "coverage.json"
            out["coverage_age_days"] = _idade(cfg.get("coverage_json", "coverage.json"))
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

    configurado = cfg.get("manage_py")
    manage = caminho_contido(root, configurado or "manage.py")
    if manage is None:
        # Sair daqui com `pending_migrations = []` era CREDITO de graca: o
        # criterio "migrations aplicadas" le `len(pend) == 0` e dava o ponto
        # por atendido sem medicao nenhuma (sozinho, tirava o eixo
        # Confiabilidade de 0%/F pra 40%/D num repositorio que nem Django e).
        # Os dois motivos de cair aqui sao "nao medi", mas nao sao a mesma
        # coisa pra quem le o painel: projeto sem Django e o esperado;
        # `manage_py` do toml que nao resolve dentro da raiz e configuracao
        # quebrada, e ficaria invisivel se os dois dessem o mesmo texto.
        # `deploy_issues`/`other_issues` saiam daqui como `[]` pelo mesmo
        # motivo — e o rotulo do painel entao culpava o motivo ERRADO:
        # repositorio que nem Django e exibia "avisos de segurança do
        # framework (não auditado: settings de dev)", que e o texto do caso em
        # que o check RODOU contra um settings de desenvolvimento.
        motivo = (f"manage_py configurado ({configurado}) não resolve dentro do repositório"
                  if configurado else "projeto sem manage.py na raiz")
        for campo in ("pending_migrations", "deploy_issues", "other_issues"):
            nao_medido(out, campo, motivo)
        return out

    # `python` do toml so vale se apontar pra dentro do repositorio (venv do
    # projeto). Caminho de fora usa o interpretador de quem esta auditando.
    py = str(caminho_contido(root, cfg.get("python")) or sys.executable)

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

    # `commit: ""` era um campo vazio sem motivo: repositorio recem-criado
    # (sem HEAD) ou objeto corrompido saiam iguais a "nao perguntei".
    rc, so, se = run(["git", "rev-parse", "--short", "HEAD"], cwd=root)
    if rc != 0:
        nao_medido(out, "commit", _motivo(rc, se))
    else:
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
    rc, so, _ = run([sys.executable, "-P", "-m", "radon", "cc", "-j",
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
        metodo = "radon"
        if cx is None:
            # Fora do Python nao ha radon. Contar ramificacoes eh uma
            # aproximacao grosseira, mas serve pro que o mapa precisa:
            # ordenar arquivos entre si, nao produzir um numero absoluto.
            # O rotulo vai junto pro painel nao passar heuristica por medicao.
            metodo = "heuristica"
            try:
                src = f.read_text(encoding="utf-8", errors="ignore")
                cx = len(re.findall(BRANCH_WORDS, src))
            except OSError:
                cx = 0
        rows.append({"file": path, "churn": times, "complexity": cx, "loc": loc,
                     "score": times * cx, "metodo": metodo})
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
    icfg = cfg.get("infra", {})

    # FU-RUCHX-INFRA-DO-HOST (decisao do dono, 2026-08-20): este coletor le o
    # Docker do HOST, nao o repositorio auditado — sem um vinculo DECLARADO
    # repo<->containers ele atribuiria ao repo a infra da maquina inteira e
    # vazaria nome de container de OUTROS projetos pro snapshot versionado
    # (aconteceu em 2026-08-15: containers de outro produto dentro do
    # snapshot do ruch-x). O vinculo e o `[infra] project_prefix` do
    # ruch-x.toml; sem ele o coletor sai como "nao auditado" — nunca adivinha.
    prefix = icfg.get("project_prefix")
    if not prefix:
        raise RuntimeError(
            "sem vinculo declarado repo<->infra: defina [infra] project_prefix "
            "no ruch-x.toml — sem ele a coleta atribuiria ao repo os "
            "containers do host inteiro")

    if not has("docker"):
        raise RuntimeError("docker client nao encontrado")

    env = {}
    host = (env_or(icfg, "docker_host", "RUCHX_DOCKER_HOST")
            or os.environ.get("METRICAS_DOCKER_HOST") or os.environ.get("DOCKER_HOST"))
    if host:
        env["DOCKER_HOST"] = host

    # O host do docker vai pro snapshot versionado: guarda o esquema e o fato
    # de ser remoto, nao `ssh://root@<ip>` do servidor de producao de alguem.
    if host:
        rotulo = f"{host.split('://', 1)[0]}://***" if "://" in host else "remoto"
    else:
        rotulo = "local"
    out = {"host": rotulo, "containers": [], "images": [], "disk": None}

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

    # `prefix` e garantido la no topo (vinculo declarado e pre-condicao do
    # coletor). So o que e do projeto entra no snapshot; lista vazia com
    # prefix declarado e "medi e esta vazio", nao falha.
    out["containers"] = [c for c in out["containers"] if prefix in (c.get("name") or "")]
    return out


# --------------------------------------------------------------------------
# ci: github actions via gh cli
# --------------------------------------------------------------------------

# Quantos runs cancelados, no maximo, o coletor investiga a fundo (1 chamada
# `gh run view` cada). Cancelamento costuma ser raro numa janela de 40 runs;
# o limite existe pra um projeto com cancelamento em massa (ex: todo PR de um
# dia cancelado por um push seguinte) nao multiplicar chamadas de API.
LIMITE_CANCELADOS_CHECADOS = 8

# `_e_deploy()` foi desenhada pra nome de WORKFLOW, onde "deploy" no nome
# quase sempre significa que o workflow FAZ o deploy. Reaplicada sobre nome
# de JOB isso quebra: um job de PORTAO decide SE o deploy roda, nao faz o
# deploy — e o proprio ion tem um chamado "Checks rapidos (gate de deploy)".
# Sem esta lista, aquele job (cancelado ou nao, tanto faz — o que importa
# aqui e o CONCLUIDO com sucesso) casava com "deploy" e virava uma afirmacao
# categorica de que algo foi implantado, quando o job nem chegou perto disso.
# Nome com qualquer uma destas palavras VENCE "deploy" no mesmo nome — falso
# negativo aqui (nao contar um deploy de verdade com nome ambiguo) e mais
# barato que falso positivo (afirmar deploy que nao aconteceu).
PALAVRAS_DE_PORTAO_NO_JOB = ("gate", "check", "checks", "lint", "test")


def _job_parece_deploy(nome_job, cfg):
    """Nome de job que sugere ter EXECUTADO o deploy — nao so decidido se ele
    roda. Ver `PALAVRAS_DE_PORTAO_NO_JOB`: um job "Checks rapidos (gate de
    deploy)" nao pode contar so por ter "deploy" no nome; um job "Deploy
    staging (webhooks Coolify)" continua contando normalmente.
    """
    nome = (nome_job or "").lower()
    if any(palavra in nome for palavra in PALAVRAS_DE_PORTAO_NO_JOB):
        return False
    return _e_deploy(nome_job, cfg)


def _job_de_deploy_bem_sucedido(root, run_id, cfg):
    """True se o run cancelado teve algum JOB concluido com SUCESSO cujo nome
    sugere deploy — o caso mais caro de "CI verde ignorando cancelamento": o
    job que roda a suite foi cancelado, mas o job que sobe pra producao, no
    MESMO run, ja tinha terminado bem antes disso. `None` quando nao foi
    possivel checar (gh falhou, timeout, saida ilegivel) — nunca vira "nao
    tinha deploy" por engano.

    O nome do job e SINAL, nao prova: `success` e medido, "e um job de
    deploy" e inferido do nome (ver `_job_parece_deploy`). Quem le o achado
    precisa do mesmo hedge — texto em `render.py`.
    """
    if not run_id:
        return None
    rc, so, _ = run(["gh", "run", "view", str(run_id), "--json", "jobs"], cwd=root, timeout=30)
    if rc != 0 or not so.strip():
        return None
    try:
        data = json.loads(so)
    except json.JSONDecodeError:
        return None
    jobs = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(jobs, list):
        return None
    return any(
        isinstance(j, dict) and j.get("conclusion") == "success"
        and _job_parece_deploy(j.get("name"), cfg)
        for j in jobs
    )


def _cancelados_com_deploy_ok(root, cancelados, cfg):
    """Dos runs cancelados, quais tinham um job de deploy ja bem-sucedido.

    Best-effort: cada checagem que nao conseguir responder (`None`) e
    simplesmente omitida da lista — ela nunca vira falso "nao tinha".
    """
    achados = []
    for r in cancelados[:LIMITE_CANCELADOS_CHECADOS]:
        if _job_de_deploy_bem_sucedido(root, r.get("run_id"), cfg):
            achados.append({"workflow": r.get("workflow"), "title": r.get("title"),
                            "run_id": r.get("run_id")})
    return achados


def collect_ci(root, cfg):
    if not has("gh"):
        raise RuntimeError("gh cli nao encontrado (brew/apt install gh, depois gh auth login)")

    limit = cfg.get("ci", {}).get("limit", 40)
    fields = "conclusion,createdAt,updatedAt,displayTitle,workflowName,headBranch,event,databaseId"
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
                       "created_at": r.get("createdAt"), "duration_s": dur,
                       "run_id": r.get("databaseId")})

    # A taxa continua sendo sucesso sobre CONCLUIDO (sucesso+falha) — cancelar
    # nao e falhar, as vezes e run substituido por um push seguinte. O que
    # faltava e nao jogar o cancelamento fora: um run cancelado nao confirma
    # pipeline verde, e esconder isso deixa "CI verde 100%" cobrir um caso onde
    # ninguem sabe se a suite passou (medido: job de teste cancelado aos 26
    # min, job de deploy do MESMO run subiu pro staging do mesmo jeito).
    done = [r for r in parsed if r["conclusion"] in ("success", "failure")]
    cancelados = [r for r in parsed if r["conclusion"] == "cancelled"]
    durs = [r["duration_s"] for r in done if r["duration_s"]]
    return {
        "runs_analyzed": len(parsed),
        "success_rate": round(100 * sum(1 for r in done if r["conclusion"] == "success") / len(done), 1) if done else None,
        "cancelados": len(cancelados),
        "cancelados_com_deploy_ok": _cancelados_com_deploy_ok(root, cancelados, cfg),
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


def _valor_do_achado(rotulo, trecho):
    """O VALOR da credencial dentro do trecho casado — nunca a chave/usuario.

    Rodar o VALORES_DE_TESTE no trecho inteiro erra dos DOIS lados, e os dois
    foram medidos em 2026-08-13:

    - **Falso positivo (DSN):** o filtro nao era nem consultado, e
      `postgres://reader:password@host` de DOCUMENTACAO virava P0 — o achado
      mais caro do relatorio. Aconteceu com o README deste proprio repositorio.
    - **Regra morta (atribuicao):** o trecho casado COMECA pela chave
      (`password`, `senha`, `secret_key`, `api_key`), e todas elas casam com a
      lista de valores de teste. `VALORES_DE_TESTE.search(trecho)` era sempre
      verdadeiro, entao NENHUM achado desse rotulo passava — nem
      `password = "<16+ caracteres que nao parecem exemplo>"`. (O exemplo vai
      entre `<>` de proposito: escrito por extenso, este docstring viraria o
      proximo falso positivo — aconteceu na 1a rodada deste fix.)

    Olhar so o valor tambem protege do outro erro: "test" no NOME DE USUARIO
    (`postgres://testuser:S3nh4Real@host`) nao pode esconder uma senha real.
    """
    if rotulo == "DSN com senha":
        # O padrao nao aceita ":" nem "@" dentro da senha: o "@" fecha o
        # trecho e o ultimo ":" antes dele abre a senha.
        return trecho.rstrip("@").rsplit(":", 1)[-1]
    if rotulo == "senha em atribuicao":
        m = re.search(r"[\"']([^\"']+)[\"']\s*$", trecho)
        return m.group(1) if m else trecho
    return trecho


def _segredo_plausivel(rotulo, trecho, texto, pos):
    """Segundo filtro: o match parece segredo DE VERDADE?

    Doc tecnica fala sobre segredo o tempo todo — "salve o `-----BEGIN PRIVATE
    KEY-----`", `postgres://user:<senha>@host`. Sem esta checagem o relatorio
    acusa a propria documentacao de vazar credencial (visto no 1o uso real).
    """
    # Placeholder explicito em qualquer achado: <senha>, ${VAR}, ***, xxx
    if re.search(r"[<>${}]|\*{3,}|x{4,}", trecho, re.I):
        return False
    if rotulo in ("senha em atribuicao", "DSN com senha"):
        return not VALORES_DE_TESTE.search(_valor_do_achado(rotulo, trecho))
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
        # `[]` aqui seria "varri o repositorio inteiro e esta limpo" — o
        # achado P0 do relatorio. Sem git nao houve varredura nenhuma.
        return None
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
    out = {"count": 0, "sem_pin": [], "sem_permissions": [], "permissions_no_job": [],
           "arquivos": []}
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
            # Sem bloco no TOPO o workflow ainda entra em `sem_permissions`
            # (o que protege TODOS os jobs e o bloco raiz — isso nao muda).
            # Mas um `permissions:` indentado (dentro de um job) restringe
            # AQUELE job especifico — achado do 2o uso real: 2 dos 3
            # workflows "sem permissions" ja tinham o job que publica no
            # registro de pacotes limitado a `packages: write`. Registrar
            # separado pra o achado poder dizer isso, sem mudar o criterio
            # (que continua sendo o bloco no topo).
            if re.search(r"^[ \t]+permissions\s*:", texto, re.M):
                out["permissions_no_job"].append(f.name)
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
    rc, so, se = run(["gh", "api", f"repos/{slug}/branches/{branch}/protection"], cwd=root)
    if rc != 0:
        # 404 e a resposta que INTERESSA: a branch nao tem protecao. Qualquer
        # outra falha (403 sem permissao, rate limit, rede) nao autoriza dizer
        # "desprotegida" — isso seria acusar sem ter olhado. O padrao casa SO
        # a forma real da resposta do gh ("gh: Not Found (HTTP 404)"), com
        # ancora de palavra: "404" solto dentro de outro numero (epoch, id de
        # request) nao pode virar "404 confirmado".
        if re.search(r"\bnot found\b|\bhttp\s?404\b", se or "", re.I):
            return out
        out["disponivel"] = False
        out["motivo"] = _motivo(rc, se)
        out["protegido"] = None
        return out
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


def _total_deps_npm(root):
    """(total, motivo): dependencias diretas declaradas no package.json.

    `npm outdated` conta o numerador e nao devolve denominador nenhum. Sem o
    total, `desatualizadas` nao vira percentual e o criterio inteiro e
    DESCARTADO — 37 dependencias velhas medidas davam a mesma nota de
    Seguranca que um projeto em dia (medido em 2026-08-13). O denominador
    honesto e o mesmo universo que o `npm outdated` varre por padrao: as
    dependencias diretas do manifesto.
    """
    pkg = Path(root) / "package.json"
    try:
        dados = json.loads(pkg.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return None, f"package.json ilegível ({type(exc).__name__})"
    if not isinstance(dados, dict):
        return None, "package.json não é um objeto JSON"
    total = 0
    for chave in ("dependencies", "devDependencies", "optionalDependencies"):
        bloco = dados.get(chave)
        if isinstance(bloco, dict):
            total += len(bloco)
    return total, None


def _deps_desatualizadas(root, cfg):
    """Quantas dependencias estao para tras. Dependencia velha e divida que
    rende juros: quanto mais tempo passa, mais caro fica o upgrade."""
    out = {"ferramenta": None, "total": None, "desatualizadas": None, "exemplos": []}
    py = str(caminho_contido(root, cfg.get("python")) or sys.executable)
    if (Path(root) / "requirements.txt").exists() or (Path(root) / "pyproject.toml").exists():
        rc, so, _ = run([py, "-P", "-m", "pip", "list", "--outdated", "--format", "json"],
                        cwd=root, timeout=180)
        if rc == 0 and so.strip():
            try:
                itens = json.loads(so)
                rc2, so2, se2 = run([py, "-P", "-m", "pip", "list", "--format", "json"],
                                    cwd=root, timeout=120)
                total = len(json.loads(so2)) if rc2 == 0 and so2.strip() else None
                out.update({
                    "ferramenta": "pip", "total": total, "desatualizadas": len(itens),
                    "exemplos": [{"nome": i.get("name"), "atual": i.get("version"),
                                  "ultima": i.get("latest_version")} for i in itens[:8]],
                })
                if total is None:
                    # Numerador sem denominador: o criterio nao pode ser
                    # avaliado, e tem que DIZER isso em vez de sumir.
                    nao_medido(out, "total", _motivo(rc2, se2))
                return out
            except json.JSONDecodeError:
                pass
    if (Path(root) / "package.json").exists() and has("npm"):
        # O returncode NAO serve de sinal: `npm outdated` sai 1 justamente
        # quando ACHOU dependencia velha (medicao bem-sucedida). O sinal e a
        # saida ser um objeto JSON — `{}` e "medi e esta tudo em dia".
        rc, so, se = run(["npm", "outdated", "--json"], cwd=root, timeout=180)
        try:
            itens = json.loads(so) if so.strip() else None
        except json.JSONDecodeError:
            itens = None
        if not isinstance(itens, dict):
            nao_medido(out, "desatualizadas",
                       _motivo(rc, se) if rc else "npm outdated devolveu saída ilegível")
            return out
        out.update({"ferramenta": "npm", "desatualizadas": len(itens),
                    "exemplos": [{"nome": k, "atual": v.get("current"),
                                  "ultima": v.get("latest")}
                                 for k, v in list(itens.items())[:8]
                                 if isinstance(v, dict)]})
        total, motivo = _total_deps_npm(root)
        if total is None:
            nao_medido(out, "total", motivo)
        else:
            out["total"] = total
        return out
    if out["desatualizadas"] is None and "nao_medido" not in out:
        # Nenhum manifesto reconhecido (ou a ferramenta do stack ausente).
        # Sem marcar, o criterio aparecia como um "(—/—)" pelado, sem o
        # leitor descobrir se ninguem olhou ou se nao havia o que olhar.
        nao_medido(out, "desatualizadas",
                   "nenhum manifesto de dependências reconhecido (requirements.txt, "
                   "pyproject.toml ou package.json com npm)")
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

    resultado = {
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
    if resultado["segredos_commitados"] is None:
        nao_medido(resultado, "segredos_commitados",
                   "git ls-files falhou — nenhum arquivo foi varrido")
    return resultado


# --------------------------------------------------------------------------
# dora: as 4 metricas que dizem se o time entrega bem
# --------------------------------------------------------------------------

def _e_deploy(nome_workflow, cfg):
    palavras = cfg.get("dora", {}).get("deploy_keywords") or ["deploy", "release", "publish", "cd"]
    n = (nome_workflow or "").lower()
    return any(p in n for p in palavras)


# Quantos runs VERMELHOS, no maximo, o DORA abre pra ver se o deploy dentro
# deles passou (1 chamada `gh run view` cada, ~1,45s medidos em 2026-08-21).
# Run verde nao gasta chamada nenhuma: se o run inteiro concluiu com sucesso,
# o job de deploy dele tambem concluiu. O teto existe pro repo com muito
# vermelho na janela nao multiplicar chamadas de API — e o que fica de fora
# NAO some calado: conta em `reclassificacao.acima_do_teto` e segue contando
# como falha (direcao que nunca infla a nota).
LIMITE_VERMELHOS_CHECADOS = 20


def _resultado_do_job_de_deploy(root, run_id, cfg):
    """O que aconteceu com o DEPLOY dentro de um run que concluiu vermelho.

    O `conclusion` de um run e do WORKFLOW INTEIRO. Num pipeline com varios
    jobs isso troca o sujeito da frase: no ion, um `pip-audit` vermelho ao
    lado de um `Deploy staging (webhooks Coolify)` verde fazia o DORA
    registrar uma "falha de mudanca" que nunca existiu (run 32391926125).

    Retorna:
      `"ok"`         — o job de deploy concluiu com sucesso: o deploy subiu,
                       quem falhou foi outro job;
      `"falhou"`     — o job de deploy concluiu com failure: falha de
                       verdade, e a unica que o CFR deve contar;
      `"sem_deploy"` — nenhum job de deploy concluido no run (o build morreu
                       antes, o job foi pulado, ou o workflow ainda nem tinha
                       deploy). Nao houve deploy: o run sai da conta inteira,
                       nem numerador nem denominador;
      `None`         — nao deu pra apurar (gh fora do ar, timeout, saida
                       ilegivel). NUNCA vira absolvicao: quem chama mantem a
                       falha contada.

    "E um job de deploy" e inferido do NOME (`_job_parece_deploy`, que ja
    sabe recusar o `Checks rapidos (gate de deploy)` do proprio ion); o
    `conclusion` e medido.
    """
    if not run_id:
        return None
    rc, so, _ = run(["gh", "run", "view", str(run_id), "--json", "jobs"],
                    cwd=root, timeout=30)
    if rc != 0 or not so.strip():
        return None
    try:
        data = json.loads(so)
    except json.JSONDecodeError:
        return None
    jobs = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(jobs, list):
        return None
    de_deploy = [j for j in jobs
                 if isinstance(j, dict) and _job_parece_deploy(j.get("name"), cfg)]
    if not de_deploy:
        return "sem_deploy"
    if any(j.get("conclusion") == "success" for j in de_deploy):
        return "ok"
    if any(j.get("conclusion") == "failure" for j in de_deploy):
        return "falhou"
    # Job de deploy existe mas foi pulado/cancelado: o deploy nao aconteceu.
    return "sem_deploy"


def _reclassificar_pelo_job_de_deploy(root, deploys, cfg):
    """Reclassifica a lista de deploys olhando o JOB, nao o run.

    Devolve `(deploys, reclassificacao)`. Os contadores da reclassificacao
    vao pro snapshot pra que a conta seja auditavel: quem le o painel
    consegue ver quantos runs vermelhos eram deploy bem-sucedido, quantos
    nem chegaram a deployar, e quantos ficaram sem apurar.
    """
    reclass = {"vermelhos_checados": 0, "deploy_ok_apesar_do_run": 0,
               "sem_job_de_deploy": 0, "sem_resposta": 0, "acima_do_teto": 0}
    teto = cfg.get("dora", {}).get("limite_vermelhos") or LIMITE_VERMELHOS_CHECADOS
    mantidos = []
    for d in deploys:
        if d["ok"]:
            mantidos.append(d)
            continue
        if reclass["vermelhos_checados"] >= teto:
            reclass["acima_do_teto"] += 1
            mantidos.append(d)
            continue
        reclass["vermelhos_checados"] += 1
        resultado = _resultado_do_job_de_deploy(root, d.get("run_id"), cfg)
        if resultado == "ok":
            d["ok"] = True
            reclass["deploy_ok_apesar_do_run"] += 1
            mantidos.append(d)
        elif resultado == "sem_deploy":
            reclass["sem_job_de_deploy"] += 1  # nao entra: nao houve deploy
        else:
            if resultado is None:
                reclass["sem_resposta"] += 1
            mantidos.append(d)
    return mantidos, reclass


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
    campos = ("conclusion,createdAt,updatedAt,workflowName,headBranch,"
              "headSha,event,databaseId")
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
            "workflow": r.get("workflowName"), "run_id": r.get("databaseId"),
        })
    deploys = [d for d in deploys if d["quando"]]
    deploys.sort(key=lambda d: d["quando"])

    # O `conclusion` do run e do workflow inteiro — aqui ele deixa de ser a
    # palavra final sobre o deploy. Ver `_resultado_do_job_de_deploy`.
    deploys, reclassificacao = _reclassificar_pelo_job_de_deploy(root, deploys, cfg)

    out = {"workflows_de_deploy": sorted({d["workflow"] for d in deploys}),
           "branch": branch_prod, "deploys_analisados": len(deploys),
           "janela_dias": None, "deploys_por_semana": None,
           "lead_time_p50_h": None, "change_failure_rate": None,
           "mttr_h": None, "ultimo_deploy": None,
           "reclassificacao": reclassificacao}
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


def _nomes_de_coletor(bruto, flag):
    """Nomes de coletor de uma lista separada por virgula, avisando os que nao
    existem.

    Nome fora do REGISTRY era descartado em silencio nas duas flags. Em
    `--skip` isso falha seguro (o coletor roda), em `--only` nao: filtra tudo
    e produz um snapshot vazio com exit 0.
    """
    nomes = [c.strip() for c in bruto.split(",") if c.strip()]
    desconhecidos = [c for c in nomes if c not in REGISTRY]
    if desconhecidos:
        print(f"aviso: {flag} recebeu coletor(es) desconhecido(s): "
              f"{', '.join(desconhecidos)} — validos: {', '.join(sorted(REGISTRY))}",
              file=sys.stderr)
    return nomes


def gravar_snapshot(snapshot, path, outdir):
    """Redige e grava o snapshot em `path` e em `outdir/latest.json`.

    Redacao na saida: o snapshot e versionado, entao nada que passe por aqui
    pode carregar credencial (str(exc) de conexao, texto de query, host).
    """
    limpo = redigir_estrutura(snapshot)
    corpo = json.dumps(limpo, indent=2, ensure_ascii=False, default=str)
    path.write_text(corpo, encoding="utf-8")
    (outdir / "latest.json").write_text(corpo, encoding="utf-8")
    return path


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
        pedidos = _nomes_de_coletor(args.only, "--only")
        selected = [c for c in pedidos if c in REGISTRY]
        if not selected:
            # Um `--only gouvernance` (typo) filtrava tudo em silencio e saia
            # com exit 0: snapshot sem coletor nenhum, `errors: {}`, e o
            # dashboard desse snapshot dizendo "Nenhum alerta nos limiares
            # configurados" — laudo limpo de uma coleta que nao aconteceu.
            raise SystemExit("--only nao selecionou nenhum coletor valido — nada foi coletado")
    if args.skip:
        skip = set(_nomes_de_coletor(args.skip, "--skip"))
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
    gravar_snapshot(snapshot, path, outdir)

    print(f"\nsnapshot: {path}", file=sys.stderr)
    if snapshot["errors"]:
        print(f"coletores com falha: {', '.join(snapshot['errors'])}", file=sys.stderr)
    print(str(path))


if __name__ == "__main__":
    main()
