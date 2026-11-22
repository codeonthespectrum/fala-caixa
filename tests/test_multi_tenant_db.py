"""Testes da arquitetura Multi-Tenant (SQLite, Allowlist e Painel Admin) do Fala, Caixa!."""

from __future__ import annotations

from datetime import date, timedelta
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from nex_alerts.admin_web import create_handler
from nex_alerts.db.store_repository import StoreRepository
from nex_alerts.db.store_resolver import (
    AssinaturaInativaError,
    LojaNaoEncontradaError,
    NaoAutorizadoError,
    StoreResolver,
)


class MultiTenantStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "data" / "falacaixa.db"
        self.repo = StoreRepository(self.db_path)
        self.hermes_env = self.root / "falacaixa.env"
        self.hermes_env.write_text(
            "WHATSAPP_ENABLED=true\nWHATSAPP_ALLOW_ALL_USERS=true\nWHATSAPP_ALLOWED_USERS=123\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_bootstrap_from_env_imports_pilot_store(self) -> None:
        env_file = self.root / ".env"
        env_file.write_text(
            "STORE_NAME=Conveniencia Central\n"
            "PIX_KEY=84987019079\n"
            "WHATSAPP_PHONE=5584987780489\n"
            "DATA_SOURCE_MODE=nex_web\n"
            "NEX_WEB_EMAIL=bruno@teste.com\n"
            "NEX_WEB_PASSWORD=segredo\n",
            encoding="utf-8",
        )
        created = self.repo.bootstrap_from_env_if_empty(env_file)
        self.assertIsNotNone(created)
        self.assertEqual(created.store_name, "Conveniencia Central")
        self.assertEqual(created.status, "active")

        # Segunda chamada não duplica
        self.assertIsNone(self.repo.bootstrap_from_env_if_empty(env_file))
        self.assertEqual(len(self.repo.list_stores()), 1)

    def test_isolation_between_stores_and_9th_digit_variants(self) -> None:
        s1 = self.repo.create_store(
            store_name="Loja A",
            pix_key="111",
            status="active",
            initial_users=[("Bruno", "5584987780489", "owner")],
        )
        s2 = self.repo.create_store(
            store_name="Loja B",
            pix_key="222",
            status="trial",
            trial_days=14,
            initial_users=[("Maria", "5584999887766", "owner")],
        )

        # Match sem o 9º dígito para Bruno deve retornar Loja A
        matched, status = self.repo.resolve_store_for_phone({"558487780489"})
        self.assertEqual(status, "ok")
        self.assertIsNotNone(matched)
        self.assertEqual(matched.id, s1.id)

        # Match para Maria deve retornar Loja B
        matched2, status2 = self.repo.resolve_store_for_phone({"558499887766"})
        self.assertEqual(status2, "ok")
        self.assertIsNotNone(matched2)
        self.assertEqual(matched2.id, s2.id)

        # Número desconhecido não encontra nenhuma loja
        matched3, status3 = self.repo.resolve_store_for_phone({"5511912345678"})
        self.assertIsNone(matched3)
        self.assertEqual(status3, "not_found")

    def test_expired_trial_and_paused_store_block_access_and_daily_cron(self) -> None:
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        expired_store = self.repo.create_store(
            store_name="Loja Expirada",
            status="trial",
            trial_ends_at=yesterday,
            initial_users=[("Carlos", "5584991112222", "owner")],
        )
        paused_store = self.repo.create_store(
            store_name="Loja Pausada",
            status="paused",
            initial_users=[("Ana", "5584993334444", "owner")],
        )
        active_store = self.repo.create_store(
            store_name="Loja Ativa",
            status="active",
            initial_users=[("Pedro", "5584995556666", "owner")],
        )

        st_exp, status_exp = self.repo.resolve_store_for_phone({"5584991112222"})
        self.assertEqual(st_exp.id, expired_store.id)
        self.assertEqual(status_exp, "expired")

        st_pau, status_pau = self.repo.resolve_store_for_phone({"5584993334444"})
        self.assertEqual(st_pau.id, paused_store.id)
        self.assertEqual(status_pau, "paused")

        eligible = self.repo.list_eligible_stores_for_daily()
        self.assertEqual([s.id for s in eligible], [active_store.id])

    def test_sync_hermes_allowlist_writes_variants_and_locks_allow_all(self) -> None:
        self.repo.create_store(
            store_name="Mercadinho Teste",
            initial_users=[("Joao", "5584999998888", "owner")],
        )
        synced = self.repo.sync_hermes_allowlist(self.hermes_env)
        self.assertTrue(synced)
        txt = self.hermes_env.read_text(encoding="utf-8")
        self.assertIn("WHATSAPP_ALLOW_ALL_USERS=false", txt)
        self.assertIn("5584999998888", txt)
        self.assertIn("558499998888", txt)

    def test_admin_web_api_lifecycle(self) -> None:
        handler = create_handler(self.repo, repo_root=self.root, hermes_env_path=self.hermes_env)
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            # 1. Criar loja via POST /api/stores
            conn.request(
                "POST",
                "/api/stores",
                body=json.dumps(
                    {
                        "store_name": "Padaria Estrela",
                        "data_source_mode": "folder",
                        "pix_key": "84988887777",
                        "owner_name": "Marcos",
                        "owner_phone": "5584988887777",
                        "margin_pct": 35,
                    }
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            resp = conn.getresponse()
            body = resp.read().decode("utf-8")
            self.assertEqual(resp.status, 201)
            created = json.loads(body)
            self.assertEqual(created["store_name"], "Padaria Estrela")
            self.assertEqual(created["effective_status"], "trial")

            # 2. Listar dashboard
            conn.request("GET", "/api/dashboard")
            resp2 = conn.getresponse()
            dash = json.loads(resp2.read().decode("utf-8"))
            self.assertEqual(len(dash["stores"]), 1)
            self.assertEqual(dash["stores"][0]["users"][0]["phone"], "5584988887777")
            conn.close()
        finally:
            server.shutdown()
            server.server_close()


class StoreResolverTests(unittest.TestCase):
    """Matriz de autorização do Resolver de Loja (telefone x hint x status)."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = StoreRepository(self.root / "data" / "falacaixa.db")
        self.loja_a = self.repo.create_store(
            store_name="Loja A",
            pix_key="111",
            status="active",
            initial_users=[("Bruno", "5584987780489", "owner")],
        )
        self.loja_b = self.repo.create_store(
            store_name="Loja B",
            pix_key="222",
            status="trial",
            trial_days=14,
            initial_users=[("Maria", "5584999887766", "owner")],
        )
        self.loja_pausada = self.repo.create_store(
            store_name="Loja Pausada",
            pix_key="333",
            status="paused",
            initial_users=[("Ana", "5584993334444", "owner")],
        )
        self.admin_phone = "5584001112222"
        self.resolver = StoreResolver(self.repo, admin_phones=[self.admin_phone])
        self.resolver_sem_admin = StoreResolver(self.repo)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_membro_resolve_a_propria_loja_pelo_telefone(self) -> None:
        # Com e sem o 9º dígito
        store = self.resolver.autorizar({"558487780489"})
        self.assertEqual(store.id, self.loja_a.id)
        store2 = self.resolver.autorizar({"5584987780489"})
        self.assertEqual(store2.id, self.loja_a.id)

    def test_membro_pode_pedir_a_propria_loja_pelo_nome(self) -> None:
        store = self.resolver.autorizar({"5584987780489"}, hint_loja="loja a")
        self.assertEqual(store.id, self.loja_a.id)

    def test_membro_NAO_pode_consultar_outra_loja_pelo_hint(self) -> None:
        # Regressão anti-vazamento (S1): Bruno (Loja A) pedindo a Loja B
        with self.assertRaises(NaoAutorizadoError):
            self.resolver.autorizar({"5584987780489"}, hint_loja="Loja B")

    def test_numero_desconhecido_e_negado_sem_fallback(self) -> None:
        # Regressão anti-fallback: existem lojas elegíveis, mas nenhuma é devolvida
        with self.assertRaises(LojaNaoEncontradaError):
            self.resolver.autorizar({"5511912345678"})

    def test_candidatos_vazios_sao_negados(self) -> None:
        with self.assertRaises(LojaNaoEncontradaError):
            self.resolver.autorizar(set())

    def test_admin_pode_consultar_qualquer_loja_pelo_hint(self) -> None:
        store = self.resolver.autorizar({self.admin_phone}, hint_loja="Loja B")
        self.assertEqual(store.id, self.loja_b.id)

    def test_admin_sem_hint_e_sem_vinculo_tambem_e_negado(self) -> None:
        with self.assertRaises(LojaNaoEncontradaError):
            self.resolver.autorizar({self.admin_phone})

    def test_sem_admin_declarado_ninguem_fura_o_isolamento(self) -> None:
        with self.assertRaises(NaoAutorizadoError):
            self.resolver_sem_admin.autorizar({"5584987780489"}, hint_loja="Loja B")

    def test_loja_pausada_bloqueia_com_status_explicito(self) -> None:
        with self.assertRaises(AssinaturaInativaError) as ctx:
            self.resolver.autorizar({"5584993334444"})
        self.assertEqual(ctx.exception.status, "paused")
        self.assertEqual(ctx.exception.store_name, "Loja Pausada")

    def test_trial_expirado_bloqueia(self) -> None:
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        self.repo.create_store(
            store_name="Loja Expirada",
            status="trial",
            trial_ends_at=yesterday,
            initial_users=[("Carlos", "5584991112222", "owner")],
        )
        with self.assertRaises(AssinaturaInativaError) as ctx:
            self.resolver.autorizar({"5584991112222"})
        self.assertEqual(ctx.exception.status, "expired")

    def test_hint_de_loja_inexistente(self) -> None:
        with self.assertRaises(LojaNaoEncontradaError):
            self.resolver.autorizar({"5584987780489"}, hint_loja="Loja Que Nao Existe")

    def test_socio_de_duas_lojas_sem_hint_recebe_a_primeira_ativa(self) -> None:
        # Ana é dona da Loja Pausada e também sócia da Loja B
        self.repo.add_store_user(self.loja_b.id, "Ana", "5584993334444", "partner")
        store = self.resolver.autorizar({"5584993334444"})
        self.assertEqual(store.id, self.loja_b.id)


if __name__ == "__main__":
    unittest.main()
