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

    def test_coluna_password_hash_em_lista_de_tabela_nao_muda(self):
        # Nome de coluna real do Postgres — "password" e prefixo de um
        # identificador, nao uma atribuicao. Pedido no fix round 1.
        texto = "id, username, password_hash, created_at"
        self.assertEqual(collect.redigir(texto), texto)

    def test_mensagem_libpq_sem_credencial_nao_muda(self):
        # Texto real do libpq (traduzido) — "senha" aparece como PALAVRA,
        # sem "=" nem ":" depois. Nenhuma credencial na frase.
        texto = 'senha authentication failed for user "leitor"'
        self.assertEqual(collect.redigir(texto), texto)

    def test_arquivo_guia_bearer_nao_muda(self):
        # "bearer" sem espaco depois (hifen) nao pode disparar o padrao
        # de header HTTP.
        texto = "docs/bearer-token-guide.md"
        self.assertEqual(collect.redigir(texto), texto)


class TestRedigirFixRound1(unittest.TestCase):
    """4 achados Critical da revisao adversarial (fix round 1/5).

    Cada teste usa a entrada VERBATIM do achado, mais os vizinhos obvios
    (DSN com dois "@", Bearer curto vs longo, PEM com e sem corpo) pedidos
    junto com o fix.
    """

    # Critical 1 — scripts/collect.py:117 — a classe da senha excluia "@",
    # entao o regex parava no PRIMEIRO "@" e vazava o resto da senha como
    # se fosse host, com "***" dando falsa sensacao de que a linha foi
    # tratada.
    def test_critical1_dsn_com_arroba_na_senha(self):
        texto = "postgresql://leitor:p@ss:w0rd@10.0.0.9:5432/prod"
        saida = collect.redigir(texto)
        # "w0rd" e o pedaco que o padrao antigo deixava vazar (parava no
        # PRIMEIRO "@", mascarando so o "p" e devolvendo "ss:w0rd" cru).
        self.assertNotIn("w0rd", saida)
        self.assertNotIn("p@ss:w0rd", saida)
        self.assertIn("leitor:***@", saida)
        self.assertIn("10.0.0.9:5432/prod", saida)  # diagnostico fica

    def test_critical1_vizinho_dsn_dois_arrobas_ordem_diferente(self):
        texto = "mysql://u:se@nha@db.internal:3306/app"
        saida = collect.redigir(texto)
        # "nha" e o pedaco que vazava (padrao antigo mascarava so "se").
        self.assertNotIn("nha", saida)
        self.assertNotIn("se@nha", saida)
        self.assertIn("db.internal:3306/app", saida)

    # Critical 2 — :119-120 — variavel composta por letras (sem separador
    # nao-letra antes da keyword) nao era detectada. PGPASSWORD e a
    # variavel canonica do libpq.
    def test_critical2_pgpassword(self):
        saida = collect.redigir("PGPASSWORD=xyz123456")
        self.assertNotIn("xyz123456", saida)

    def test_critical2_vizinho_mysql_pwd(self):
        saida = collect.redigir("MYSQL_PWD=xyz123456")
        self.assertNotIn("xyz123456", saida)

    def test_critical2_vizinho_sufixo_pwd_generico(self):
        saida = collect.redigir("DB_PWD=outraSenha1")
        self.assertNotIn("outraSenha1", saida)

    # Critical 3 — :119-120 — chave entre aspas (JSON) tem uma aspa de
    # fechamento entre a keyword e o separador "[=:]", que o padrao antigo
    # nao tolerava.
    def test_critical3_chave_entre_aspas_json(self):
        saida = collect.redigir('{"password": "abc123456"}')
        self.assertNotIn("abc123456", saida)
        self.assertIn('"password"', saida)  # rotulo da chave sobrevive

    def test_critical3_vizinho_token_entre_aspas_json(self):
        saida = collect.redigir('{"token": "ghsecretvalue123"}')
        self.assertNotIn("ghsecretvalue123", saida)

    # Critical 4 — :115-125 — sem padrao pra Bearer token nem bloco PEM.
    def test_critical4_bearer_token(self):
        texto = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abcdefgh.ijklmnop"
        saida = collect.redigir(texto)
        self.assertNotIn("eyJhbGciOiJIUzI1NiJ9.abcdefgh.ijklmnop", saida)
        self.assertIn("Bearer ***", saida)
        self.assertIn("Authorization:", saida)

    def test_critical4_vizinho_bearer_curto(self):
        saida = collect.redigir("Bearer abc123")
        self.assertNotIn("abc123", saida)
        self.assertIn("Bearer ***", saida)

    def test_critical4_vizinho_bearer_longo(self):
        token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9." + "x" * 60
        saida = collect.redigir(f"Bearer {token}")
        self.assertNotIn(token, saida)
        self.assertIn("Bearer ***", saida)

    def test_critical4_pem_com_corpo(self):
        texto = (
            "-----BEGIN PRIVATE KEY-----\n"
            "MIIEvQIBADANBgkqhkiG9w0BAQEF\n"
            "AASCBKc=\n"
            "-----END PRIVATE KEY-----"
        )
        saida = collect.redigir(texto)
        self.assertNotIn("MIIEvQIBADANBgkqhkiG9w0BAQEF", saida)
        self.assertIn("-----BEGIN PRIVATE KEY-----", saida)
        self.assertIn("-----END PRIVATE KEY-----", saida)

    def test_critical4_vizinho_pem_variante_rsa(self):
        texto = ("-----BEGIN RSA PRIVATE KEY-----\nABCDEFGHIJKLMNOP\n"
                  "-----END RSA PRIVATE KEY-----")
        saida = collect.redigir(texto)
        self.assertNotIn("ABCDEFGHIJKLMNOP", saida)
        self.assertIn("-----BEGIN RSA PRIVATE KEY-----", saida)
        self.assertIn("-----END RSA PRIVATE KEY-----", saida)

    def test_critical4_vizinho_pem_sem_corpo(self):
        texto = "-----BEGIN PRIVATE KEY----------END PRIVATE KEY-----"
        saida = collect.redigir(texto)
        self.assertIn("-----BEGIN PRIVATE KEY-----", saida)
        self.assertIn("-----END PRIVATE KEY-----", saida)


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
