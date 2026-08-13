"""Guards da redacao.

O snapshot e VERSIONADO por recomendacao da propria ferramenta. Qualquer
credencial que entre nele vira commit — e commit vaza pra sempre, mesmo
apagado depois.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from _fake_repo import fake_repo  # noqa: F401  (garante o sys.path dos scripts)

import collect


class TestRedigir(unittest.TestCase):

    def test_dsn_com_senha(self):
        texto = "connection failed: postgresql://leitor:s3nh4Secreta@10.0.0.9:5432/prod"
        saida = collect.redigir(texto)
        self.assertNotIn("s3nh4Secreta", saida)
        self.assertIn("leitor:***@", saida)
        self.assertIn("10.0.0.9", saida)  # o que ajuda a diagnosticar fica

    def test_password_em_comando_sql(self):
        saida = collect.redigir("ALTER ROLE leitor PASSWORD 'trocaEsta123'")
        self.assertNotIn("trocaEsta123", saida)

    def test_token_do_github(self):
        saida = collect.redigir("remote: token ghp_" + "A" * 36 + " invalido")
        self.assertNotIn("A" * 36, saida)

    def test_atribuicao_de_senha(self):
        saida = collect.redigir('DATABASE_PASSWORD="umaSenhaLonga123"')
        self.assertNotIn("umaSenhaLonga123", saida)

    def test_texto_inocente_nao_muda(self):
        texto = "48 de 150 dependencias desatualizadas"
        self.assertEqual(collect.redigir(texto), texto)


class TestRedigirNaoRegressao(unittest.TestCase):
    """Texto legitimo do painel nao pode ser mutilado pela redacao.

    Cada caso aqui e um jeito real de o painel escrever algo que PARECE
    credencial mas nao e — se a redacao virar generica demais, essas
    strings somem e o diagnostico fica ilegivel.
    """

    def test_estatistica_com_numeros_nao_muda(self):
        texto = "48 de 150 dependencias desatualizadas"
        self.assertEqual(collect.redigir(texto), texto)

    def test_dsn_sem_credencial_nao_muda(self):
        texto = "postgres://localhost:5432/app"
        saida = collect.redigir(texto)
        self.assertEqual(saida, texto)
        self.assertNotIn("***", saida)

    def test_rotulo_de_achado_de_seguranca_sobrevive(self):
        # O que aparece em governance.segredos_commitados[].kind — e o
        # ROTULO do achado, nao a credencial em si.
        texto = "token do GitHub"
        self.assertEqual(collect.redigir(texto), texto)

    def test_nome_de_arquivo_comum_nao_muda(self):
        texto = "src/api_key_helper.py"
        self.assertEqual(collect.redigir(texto), texto)


class TestRedigirEstrutura(unittest.TestCase):

    def test_campo_aninhado(self):
        snap = {
            "errors": {"db": "could not connect: postgres://u:senhaReal123@h/db"},
            "db": {"slow_queries": [{"query": "ALTER ROLE x PASSWORD 'abc123def'"}]},
            "code": {"total_loc": 12345},
        }
        limpo = collect.redigir_estrutura(snap)
        self.assertNotIn("senhaReal123", limpo["errors"]["db"])
        self.assertNotIn("abc123def", limpo["db"]["slow_queries"][0]["query"])
        self.assertEqual(limpo["code"]["total_loc"], 12345)  # numero intacto

    def test_nao_quebra_tipos(self):
        entrada = {"a": None, "b": True, "c": 1.5, "d": [1, "x"], "e": {}}
        self.assertEqual(collect.redigir_estrutura(entrada), entrada)


class TestGravarSnapshotNaoVazaCredencial(unittest.TestCase):
    """Prova que a redacao esta ligada no caminho REAL de gravacao.

    Nao basta `redigir_estrutura` funcionar isolada: se `main()` gravar o
    snapshot cru (json.dumps direto, sem passar pela redacao), a senha vaza
    pro arquivo do mesmo jeito. Este teste monta um snapshot sujo do jeito
    que `errors[coletor] = str(exc)` deixaria, grava via `gravar_snapshot`
    (a mesma funcao que `main()` chama) e rele o ARQUIVO do disco.
    """

    def test_dsn_com_senha_em_errors_nao_chega_ao_disco(self):
        # Reproduz o caso real: psycopg.connect(dsn) falha e o unico
        # argumento (a connection string com senha) vira str(exc), que
        # `main()` grava direto em errors[coletor].
        snapshot = {
            "schema": 1,
            "project": "demo",
            "collectors_run": [],
            "errors": {
                "db": "connection failed: postgresql://leitor:s3nh4Secreta@10.0.0.9:5432/prod",
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            outdir = Path(tmp)
            path = outdir / "2026-08-13.json"

            collect.gravar_snapshot(snapshot, path, outdir)

            for arquivo in (path, outdir / "latest.json"):
                corpo_disco = arquivo.read_text(encoding="utf-8")
                self.assertNotIn("s3nh4Secreta", corpo_disco)

                relido = json.loads(corpo_disco)
                self.assertNotIn("s3nh4Secreta", relido["errors"]["db"])
                self.assertIn("10.0.0.9", relido["errors"]["db"])  # diagnostico fica
                self.assertEqual(relido["project"], "demo")  # texto legitimo intacto


if __name__ == "__main__":
    unittest.main()
