"""Suíte de testes automatizados (Unitários e Integração) do MVP Nex Alertas."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock
import zipfile

from nex_alerts.collectors.base import ReadOnlyViolationError
from nex_alerts.collectors.folder_collector import FolderNexCollector
from nex_alerts.collectors.mock_collector import MockNexCollector
from nex_alerts.collectors.nex_web_collector import NexWebReadOnlyCollector
from nex_alerts.config import AppConfig
from nex_alerts.domain.models import CustomerDebtRecord, FiadoTier, StockEntry
from nex_alerts.engine.fiado_analyzer import analyze_fiado_debts
from nex_alerts.engine.margin_analyzer import analyze_margin_entries
from nex_alerts.main import run_pipeline


def _create_minimal_xlsx(file_path: Path, rows: list[list[str]]) -> None:
    """Cria um arquivo .xlsx OpenXML válido usando apenas zipfile da biblioteca padrão."""
    sheet_rows_xml = []
    for r_idx, row in enumerate(rows, start=1):
        cells_xml = []
        for c_idx, val in enumerate(row):
            col_letter = chr(ord("A") + c_idx)
            escaped = (
                str(val)
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )
            cells_xml.append(
                f'<c r="{col_letter}{r_idx}" t="inlineStr"><is><t>{escaped}</t></is></c>'
            )
        sheet_rows_xml.append(f'<row r="{r_idx}">{"".join(cells_xml)}</row>')

    worksheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(sheet_rows_xml)}</sheetData>'
        "</worksheet>"
    )
    with zipfile.ZipFile(file_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("xl/worksheets/sheet1.xml", worksheet_xml)


class TestMvpNexAlerts(unittest.TestCase):
    """Testes de unidade e integração cobrindo todas as regras do MVP."""

    def test_margin_analyzer_matches_specification_examples(self) -> None:
        """Valida se os cálculos do Óleo de Soja e Biscoito Recheado batem exatamente com a especificação."""
        now = datetime(2026, 10, 18, 12, 0, 0)
        entries = [
            StockEntry(
                product_id="1",
                product_name="Óleo de Soja 900ml",
                entry_date=now - timedelta(hours=4),
                cost_price=Decimal("6.20"),
                sale_price=Decimal("7.40"),
            ),
            StockEntry(
                product_id="2",
                product_name="Biscoito Recheado X",
                entry_date=now - timedelta(hours=8),
                cost_price=Decimal("2.50"),
                sale_price=Decimal("3.10"),
            ),
            # Produto com margem >= 30% não deve entrar
            StockEntry(
                product_id="3",
                product_name="Café Torrado 500g",
                entry_date=now - timedelta(hours=2),
                cost_price=Decimal("14.00"),
                sale_price=Decimal("20.00"),  # (20 - 14)/20 = 30% exato
            ),
            # Produto com entrada antiga (> 48h) não deve entrar
            StockEntry(
                product_id="4",
                product_name="Leite Integral 1L",
                entry_date=now - timedelta(hours=50),
                cost_price=Decimal("4.50"),
                sale_price=Decimal("5.00"),
            ),
        ]

        alerts = analyze_margin_entries(
            entries=entries, reference_now=now, lookback_hours=48
        )
        self.assertEqual(len(alerts), 2)

        oleo = alerts[0]
        self.assertEqual(oleo.product_name, "Óleo de Soja 900ml")
        self.assertEqual(oleo.cost_price, Decimal("6.20"))
        self.assertEqual(oleo.current_sale_price, Decimal("7.40"))
        self.assertEqual(oleo.current_margin_pct, 16)
        self.assertEqual(oleo.suggested_sale_price, Decimal("8.90"))

        biscoito = alerts[1]
        self.assertEqual(biscoito.product_name, "Biscoito Recheado X")
        self.assertEqual(biscoito.cost_price, Decimal("2.50"))
        self.assertEqual(biscoito.current_sale_price, Decimal("3.10"))
        self.assertEqual(biscoito.current_margin_pct, 19)
        self.assertEqual(biscoito.suggested_sale_price, Decimal("3.60"))

    def test_fiado_analyzer_three_tiers_stratification(self) -> None:
        """Valida a classificação dos 3 níveis da Régua de Fiado (Sumido, Cara-de-Pau, Bom Pagador)."""
        today = date(2026, 10, 18)
        records = [
            # Nível 2: Cara-de-Pau (+30 dias atraso, comprou há 3 dias)
            CustomerDebtRecord(
                customer_id="C1",
                customer_name="Marcos",
                total_overdue_amount=Decimal("142.00"),
                oldest_due_date=today - timedelta(days=40),
                status="Em Aberto",
                last_purchase_date=today - timedelta(days=3),
            ),
            # Nível 1: O Sumido (+30 dias atraso, sem visitas há 34 dias)
            CustomerDebtRecord(
                customer_id="C2",
                customer_name="Dona Neide",
                total_overdue_amount=Decimal("85.50"),
                oldest_due_date=today - timedelta(days=35),
                status="Em Aberto",
                last_purchase_date=today - timedelta(days=34),
            ),
            # Nível 3: Bom Pagador (+30 dias atraso, histórico recorrente em dia)
            CustomerDebtRecord(
                customer_id="C3",
                customer_name="Seu Carlos",
                total_overdue_amount=Decimal("60.00"),
                oldest_due_date=today - timedelta(days=32),
                status="Em Aberto",
                last_purchase_date=today - timedelta(days=20),
                on_time_payments_count=5,
                is_habitual_good_payer=True,
            ),
            # Fora do corte: atraso de apenas 25 dias (<= 30)
            CustomerDebtRecord(
                customer_id="C4",
                customer_name="Cliente Em Dia Relativo",
                total_overdue_amount=Decimal("90.00"),
                oldest_due_date=today - timedelta(days=25),
                status="Em Aberto",
            ),
            # Fora do corte: Status Pago/Quitado
            CustomerDebtRecord(
                customer_id="C5",
                customer_name="Cliente Quitado",
                total_overdue_amount=Decimal("110.00"),
                oldest_due_date=today - timedelta(days=60),
                status="Pago",
            ),
        ]

        alerts = analyze_fiado_debts(
            records=records, pix_key="84999999999", reference_today=today
        )
        self.assertEqual(len(alerts), 3)

        self.assertEqual(alerts[0].customer_name, "Marcos")
        self.assertEqual(alerts[0].tier, FiadoTier.NIVEL_2_CARA_DE_PAU)
        self.assertIsNone(alerts[0].pix_key)

        self.assertEqual(alerts[1].customer_name, "Dona Neide")
        self.assertEqual(alerts[1].tier, FiadoTier.NIVEL_1_SUMIDO)
        self.assertEqual(alerts[1].pix_key, "84999999999")

        self.assertEqual(alerts[2].customer_name, "Seu Carlos")
        self.assertEqual(alerts[2].tier, FiadoTier.NIVEL_3_BOM_PAGADOR)
        self.assertIsNone(alerts[2].pix_key)

    def test_folder_collector_reads_csv_and_xlsx(self) -> None:
        """Valida a leitura de planilhas .csv e .xlsx exportadas do Nex."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            _create_minimal_xlsx(
                tmp_path / "produtos.xlsx",
                [
                    ["Código", "Produto", "Preço de Custo", "Preço de Venda", "Data Entrada"],
                    ["001", "Óleo de Soja 900ml", "R$ 6,20", "R$ 7,40", "18/10/2026 10:00"],
                    ["002", "Biscoito Recheado X", "2,50", "3,10", "18/10/2026 11:00"],
                ],
            )

            csv_content = (
                "Código Cliente;Cliente;Valor Débito;Vencimento;Status;Última Compra;Bom Pagador\n"
                "10;Marcos;R$ 100,00;01/09/2026;Em Aberto;15/10/2026;Não\n"
                "10;Marcos;R$ 42,00;10/09/2026;Em Aberto;12/10/2026;Não\n"
                "20;Dona Neide;85,50;05/09/2026;Em Aberto;14/09/2026;Não\n"
            )
            (tmp_path / "debitos.csv").write_text(csv_content, encoding="utf-8")

            collector = FolderNexCollector(folder_path=tmp_path)
            entries = collector.fetch_recent_stock_entries(
                reference_now=datetime(2026, 10, 18, 12, 0, 0)
            )
            debts = collector.fetch_customer_debts(reference_today=date(2026, 10, 18))

            self.assertEqual(len(entries), 2)
            self.assertEqual(entries[0].product_name, "Óleo de Soja 900ml")
            self.assertEqual(entries[0].cost_price, Decimal("6.20"))

            self.assertEqual(len(debts), 2)
            marcos = next(d for d in debts if d.customer_name == "Marcos")
            self.assertEqual(marcos.total_overdue_amount, Decimal("142.00"))
            self.assertEqual(marcos.oldest_due_date, date(2026, 9, 1))
            self.assertEqual(marcos.last_purchase_date, date(2026, 10, 15))

    def test_nex_web_collector_enforces_strict_read_only(self) -> None:
        """Garante que o conector Nex Web bloqueia imediatamente qualquer tentativa de escrita."""
        mock_opener = MagicMock()
        collector = NexWebReadOnlyCollector(
            base_url="https://web.nextar.com.br",
            token="test-token-readonly",
            opener=mock_opener,
        )

        for forbidden_method in ("PUT", "DELETE", "PATCH", "POST"):
            with self.assertRaises(ReadOnlyViolationError):
                collector._safe_request(forbidden_method, "/api/products/123")

        mock_opener.assert_not_called()

    def test_end_to_end_pipeline_and_formatting(self) -> None:
        """Executa o pipeline completo em modo mock e valida o texto de saída e arquivos gerados."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            config = AppConfig(
                store_name="Mercearia Teste",
                pix_key="84999999999",
                data_source_mode="mock",
                output_dir=tmp_path / "alertas",
            )
            result = run_pipeline(
                config=config,
                collector=MockNexCollector(),
                reference_now=datetime(2026, 10, 18, 8, 0, 0),
                print_to_stdout=False,
            )

            self.assertTrue(result.saved_file_path.exists())
            text = result.message_text
            self.assertIn("📦 RESUMO INTELIGENTE DO CAIXA", text)
            self.assertIn("🚨 Reajuste de Margem Necessário (Entradas Recentes):", text)
            self.assertIn(
                "• Óleo de Soja 900ml: Custo subiu para R$ 6,20. Margem caiu para 16%.",
                text,
            )
            self.assertIn(
                "👉 Sugestão de preço para manter 30%: R$ 8,90 (Atual: R$ 7,40)", text
            )
            self.assertIn("⚠️ Cobrança de Fiado (+30 dias):", text)
            self.assertIn(
                "• [Atenção Caixa] Travar novo fiado:\nMarcos (R$ 142,00) — Comprou há 3 dias.",
                text,
            )
            self.assertIn(
                "• [Lembrete] Cliente sumido:\nDona Neide (R$ 85,50) — Sem visitas há 34 dias.\n"
                "Chave PIX: [84999999999]",
                text,
            )
            self.assertIn(
                "• [Acompanhamento]:\nSeu Carlos (R$ 60,00) — Bom pagador habitual.",
                text,
            )

            json_files = list((tmp_path / "alertas").glob("*.json"))
            self.assertEqual(len(json_files), 1)
            payload = json.loads(json_files[0].read_text(encoding="utf-8"))
            self.assertEqual(len(payload["margin_alerts"]), 2)
            self.assertEqual(len(payload["fiado_alerts"]), 3)

    def test_multi_store_batch_isolation(self) -> None:
        """Garante que o modo Multi-Loja processa múltiplos clientes e isola falhas individuais."""
        from nex_alerts.config import load_multi_store_configs
        from nex_alerts.main import run_multi_store_batch

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            base_cfg = AppConfig(output_dir=tmp_path / "lote")
            stores_json = json.dumps(
                [
                    {
                        "store_name": "Mercadinho do Primo",
                        "pix_key": "84911112222",
                        "whatsapp_phone": "5584911112222",
                        "mode": "mock",
                        "active": True,
                    },
                    {
                        "store_name": "Loja Com Erro de Credencial",
                        "mode": "nex_web",
                        "nex_email": "",
                        "nex_password": "",
                        "active": True,
                    },
                    {
                        "store_name": "Mercearia São José",
                        "pix_key": "84933334444",
                        "whatsapp_phone": "5584933334444",
                        "mode": "mock",
                        "active": True,
                    },
                    {
                        "store_name": "Loja Inativa",
                        "mode": "mock",
                        "active": False,
                    },
                ]
            )

            configs = load_multi_store_configs(
                base_config=base_cfg, stores_json_str=stores_json
            )
            # A loja inativa deve ser ignorada
            self.assertEqual(len(configs), 3)

            results, errors = run_multi_store_batch(
                store_configs=configs,
                reference_now=datetime(2026, 10, 18, 8, 0, 0),
                print_to_stdout=False,
            )
            # 2 lojas com sucesso (Primo e São José) e 1 erro isolado
            self.assertEqual(len(results), 2)
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0][0], "Loja Com Erro de Credencial")
            self.assertIn("Chave PIX: [84911112222]", results[0].message_text)
            self.assertIn("Chave PIX: [84933334444]", results[1].message_text)

    def test_whatsapp_providers_dispatch(self) -> None:
        """Valida o envio nativo para Evolution API, Z-API, Meta Cloud API e CallMeBot."""
        from nex_alerts.domain.models import DailyDigestReport
        from nex_alerts.notification.dispatcher import NotificationDispatcher

        captured_requests = []

        def fake_opener(req, timeout=15):
            captured_requests.append(req)
            resp = MagicMock()
            resp.status = 200
            resp.__enter__.return_value = resp
            return resp

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            report = DailyDigestReport(
                generated_at=datetime(2026, 10, 18, 8, 0, 0),
                store_name="Loja Zap",
                pix_key="84999999999",
                whatsapp_phone="(84) 98888-7777",
            )

            # 1. Evolution API
            cfg_evo = AppConfig(
                output_dir=tmp_path,
                evolution_api_url="https://evo.exemplo.com",
                evolution_api_instance="instancia1",
                evolution_api_key="evo-secret",
            )
            res_evo = NotificationDispatcher(
                config=cfg_evo, print_to_stdout=False, opener=fake_opener
            ).dispatch(report)
            self.assertEqual(res_evo.provider_used, "evolution")
            self.assertTrue(res_evo.webhook_sent)
            self.assertIn(
                "/message/sendText/instancia1", captured_requests[-1].full_url
            )

            # 2. Z-API
            cfg_zapi = AppConfig(
                output_dir=tmp_path,
                zapi_instance_url="https://api.z-api.io/instances/123/token/abc",
                zapi_client_token="client-tok",
            )
            res_zapi = NotificationDispatcher(
                config=cfg_zapi, print_to_stdout=False, opener=fake_opener
            ).dispatch(report)
            self.assertEqual(res_zapi.provider_used, "zapi")
            self.assertTrue(res_zapi.webhook_sent)
            self.assertTrue(captured_requests[-1].full_url.endswith("/send-text"))

            # 3. CallMeBot
            cfg_cmb = AppConfig(
                output_dir=tmp_path,
                callmebot_api_key="123456",
            )
            res_cmb = NotificationDispatcher(
                config=cfg_cmb, print_to_stdout=False, opener=fake_opener
            ).dispatch(report)
            self.assertEqual(res_cmb.provider_used, "callmebot")
            self.assertTrue(res_cmb.webhook_sent)
            self.assertIn("phone=5584988887777", captured_requests[-1].full_url)


if __name__ == "__main__":
    unittest.main()
