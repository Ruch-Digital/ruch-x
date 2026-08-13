"""Fabrica de repositorio de mentira pros guards.

Os testes precisam de um diretorio que PARECA um projeto (manage.py,
package.json, .git) sem depender do repositorio real de ninguem.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Os scripts moram um nivel acima de tests/ e nao sao um pacote instalavel.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def fake_repo(tmpdir, **arquivos):
    """Cria arquivos dentro de tmpdir. Chave = caminho relativo, valor = conteudo."""
    root = Path(tmpdir)
    for caminho, conteudo in arquivos.items():
        alvo = root / caminho
        alvo.parent.mkdir(parents=True, exist_ok=True)
        alvo.write_text(conteudo, encoding="utf-8")
    return root


MANAGE_QUE_EXPLODE = (
    "import sys\n"
    "sys.stderr.write('ModuleNotFoundError: No module named "
    "\\'projeto.settings.producao\\'\\n')\n"
    "sys.exit(1)\n"
)

MANAGE_QUE_MEDE = r"""
import sys
cmd = sys.argv[1] if len(sys.argv) > 1 else ""
if cmd == "check":
    print("SystemCheckError: System check identified some issues:")
    print("")
    print("WARNINGS:")
    print("?: (security.W009) Your SECRET_KEY has less than 50 characters.")
    print("?: (drf_spectacular.E001) Schema generation threw exception.")
    print("")
    print("System check identified 2 issues (0 silenced).")
    sys.exit(1)
if cmd == "showmigrations":
    print("[X] app.0001_initial")
    print("[ ] app.0002_novo")
    sys.exit(0)
sys.exit(0)
"""
