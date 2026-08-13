#!/usr/bin/env python3
"""
Ruch-X - diagnostico. Examina o projeto e o ambiente, e imprime um passo a passo personalizado.

Não mede nada e não altera nada. Serve para responder duas perguntas antes da
primeira coleta: o que dá pra medir aqui, e o que falta instalar pra medir o resto.

Uso:
    python doctor.py            # relatório em texto
    python doctor.py --json     # mesma informação em JSON
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from collect import IGNORE_DIRS, STACK_MARKERS, COVERAGE_FILES, load_config  # noqa: E402

VERDE, AMARELO, VERMELHO, CINZA, NEGRITO, FIM = (
    "\033[32m", "\033[33m", "\033[31m", "\033[90m", "\033[1m", "\033[0m")

if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
    VERDE = AMARELO = VERMELHO = CINZA = NEGRITO = FIM = ""

OK, PARCIAL, FALTA = f"{VERDE}✓{FIM}", f"{AMARELO}~{FIM}", f"{VERMELHO}✗{FIM}"


def tem(b):
    return shutil.which(b) is not None


def tem_modulo(nome):
    try:
        # -P: sem o diretorio atual no sys.path. Sem isso, um arquivo
        # `<nome>.py` na raiz de quem roda o doctor sombrearia o modulo
        # instalado (mesma classe de risco do -m em collect.py). Modulo
        # de verdade continua importando — instalado nao depende do cwd.
        subprocess.run([sys.executable, "-P", "-c", f"import {nome}"],
                       capture_output=True, timeout=20, check=True)
        return True
    except Exception:  # noqa: BLE001
        return False


def tem_arquivo_com_extensao(root, extensoes):
    """True se existir pelo menos um arquivo com essas extensoes no projeto,
    fora dos diretorios ignorados. Para na primeira ocorrencia e poda as
    pastas grandes (venv, node_modules) durante a caminhada."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in IGNORE_DIRS and not d.startswith(".")]
        if any(f.endswith(extensoes) for f in filenames):
            return True
    return False


def detectar_stack(root):
    """Stack pelos manifestos, com uma segunda porta pelos arquivos-fonte.

    Amarrar a deteccao a `STACK_MARKERS` (pyproject/requirements/setup.py/
    Pipfile) fazia o doctor PULAR EM SILENCIO os checks de radon e ruff em
    todo repositorio Python sem manifesto — inclusive o proprio ruch-x, onde
    a ausencia do radon e justamente o que derruba o eixo Qualidade pra F. O
    README promete que "cada ausencia apaga um pedaco do painel — e o
    doctor.py diz exatamente qual"; sem esta porta, ele nao dizia.
    """
    achados = []
    for nome, arquivos in STACK_MARKERS:
        if any((root / a).exists() for a in arquivos):
            achados.append(nome)
    if not any(x in achados for x in ("Django", "Python")) and \
            tem_arquivo_com_extensao(root, (".py",)):
        achados.append("Python")
    return achados


def instalar(pacote, tipo="pip"):
    if tipo == "pip":
        return f"pip install {pacote}"
    if tipo == "npm":
        return f"npm install -D {pacote}"
    return pacote


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--json", action="store_true", dest="como_json")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    cfg = load_config(root / "ruch-x.toml") or load_config(root / "metricas.toml")
    stack = detectar_stack(root)
    é_git = (root / ".git").exists()

    tem_python = any(x in stack for x in ("Django", "Python"))
    tem_js = "Node" in stack

    # cada item: (nome, disponível, o que habilita, como instalar, se aplica)
    checks = [
        ("git", é_git, "ritmo de commits e o mapa de atrito",
         "este diretório não é um repositório git", True),
        ("scc ou cloc", tem("scc") or tem("cloc"),
         "contagem de linhas precisa por linguagem",
         "brew install scc  (ou: apt install cloc)", True),
        ("radon", tem_modulo("radon"), "complexidade real das funções Python",
         instalar("radon"), tem_python),
        ("ruff", tem("ruff"), "violações de lint agrupadas por regra",
         instalar("ruff"), tem_python),
        ("eslint", (root / "node_modules" / ".bin" / "eslint").exists(),
         "violações de lint em JavaScript/TypeScript",
         instalar("eslint", "npm"), tem_js),
        ("psycopg", tem_modulo("psycopg") or tem_modulo("psycopg2"),
         "tamanho do banco, índices ociosos, queries lentas",
         instalar('"psycopg[binary]"'), True),
        ("docker", tem("docker"), "CPU e memória dos containers",
         "instale o Docker, ou pule com --skip infra", True),
        ("gh", tem("gh"), "taxa de sucesso e duração do CI",
         "instale o gh e rode: gh auth login", True),
    ]

    cobertura = next((c for c, _ in COVERAGE_FILES if (root / c).exists()), None)
    dsn = bool(os.environ.get("RUCHX_DATABASE_URL") or os.environ.get("DATABASE_URL"))
    docker_host = os.environ.get("RUCHX_DOCKER_HOST") or (cfg.get("infra", {}) or {}).get("docker_host")

    if args.como_json:
        print(json.dumps({
            "projeto": root.name, "stack": stack, "git": é_git,
            "cobertura_encontrada": cobertura, "dsn_configurado": dsn,
            "docker_host": docker_host,
            "ferramentas": {n: bool(d) for n, d, _, _, ap in checks if ap},
        }, indent=2, ensure_ascii=False))
        return

    print(f"\n{NEGRITO}Ruch-X · diagnóstico — {root.name}{FIM}")
    print(f"{CINZA}{'─' * 62}{FIM}")
    print(f"Stack detectado: {NEGRITO}{' · '.join(stack) or 'não identificado'}{FIM}\n")

    for nome, disponivel, habilita, como, aplica in checks:
        if not aplica:
            # Cobrar ruff num projeto Go só gera ruído. Some da lista.
            continue
        marca = OK if disponivel else FALTA
        print(f"  {marca} {nome:<14} {CINZA}{habilita}{FIM}")
        if not disponivel:
            print(f"      {AMARELO}→ {como}{FIM}")

    print(f"\n{NEGRITO}Configuração{FIM}")
    print(f"  {OK if cobertura else FALTA} cobertura      ", end="")
    if cobertura:
        print(f"{CINZA}lendo de {cobertura}{FIM}")
    else:
        print(f"{CINZA}nenhum relatório encontrado{FIM}")
        print(f"      {AMARELO}→ {comando_cobertura(stack)}{FIM}")

    print(f"  {OK if dsn else FALTA} banco          ", end="")
    print(f"{CINZA}RUCHX_DATABASE_URL definida{FIM}" if dsn else
          f"{CINZA}sem DSN{FIM}\n      {AMARELO}→ export RUCHX_DATABASE_URL="
          f'"postgresql://leitor:senha@host:5432/banco"{FIM}')

    print(f"  {OK if docker_host else PARCIAL} docker host    ", end="")
    print(f"{CINZA}{docker_host}{FIM}" if docker_host else
          f"{CINZA}usando o Docker local{FIM}\n      {AMARELO}→ para medir o servidor: "
          f'export RUCHX_DOCKER_HOST="ssh://root@seu-vps"{FIM}')

    # Auditoria (eixos Entrega/Segurança/Processo): tudo vem de git + API do
    # GitHub. Sem `gh` autenticado o veredito sai capenga, entao avisa aqui.
    gh_ok = bool(shutil.which("gh"))
    print(f"  {OK if gh_ok else FALTA} auditoria      ", end="")
    print(f"{CINZA}DORA, branch protection e supply chain via gh{FIM}" if gh_ok else
          f"{CINZA}sem gh — eixos Entrega e Processo ficam vazios{FIM}"
          f"\n      {AMARELO}→ instale o gh e rode: gh auth login{FIM}")

    aplicaveis = [c for c in checks if c[4]]
    prontos = sum(1 for c in aplicaveis if c[1])
    print(f"\n{NEGRITO}Próximo passo{FIM}")
    print(f"{CINZA}{'─' * 62}{FIM}")

    pular = []
    if not dsn:
        pular.append("db")
    if not tem("docker"):
        pular.append("infra")
    if not tem("gh"):
        pular.append("ci")
    skip = f" --skip {','.join(pular)}" if pular else ""

    print(f"  1. python scripts/collect.py{skip}")
    print(f"  2. python scripts/render.py --open")
    print(f"\n  {prontos} de {len(aplicaveis)} coletores prontos. O que falta não impede "
          f"a coleta —\n  a seção correspondente aparece vazia com a instrução de como ligar.\n")


def comando_cobertura(stack):
    if "Django" in stack or "Python" in stack:
        return "pytest --cov --cov-report=json"
    if "Node" in stack:
        return "vitest run --coverage  (ou jest --coverage --coverageReporters=json-summary)"
    if "Go" in stack:
        return "go test ./... -coverprofile=coverage.out"
    if "Rust" in stack:
        return "cargo llvm-cov --lcov --output-path lcov.info"
    if any("PHP" in s for s in stack):
        return "vendor/bin/phpunit --coverage-clover coverage.xml"
    if any("Ruby" in s for s in stack):
        return "COVERAGE=1 bundle exec rspec  (simplecov exporta lcov)"
    if any("Java" in s for s in stack):
        return "mvn test jacoco:report"
    return "rode sua suíte exportando lcov, cobertura ou json"


if __name__ == "__main__":
    main()
