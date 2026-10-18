"""Coletor Mock que reproduz fielmente os cenários da Especificação do MVP + casos de controle."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from nex_alerts.collectors.base import BaseNexCollector
from nex_alerts.domain.models import CustomerDebtRecord, StockEntry


class MockNexCollector(BaseNexCollector):
    """Gera dados determinísticos de teste baseados na especificação oficial do MVP."""

    def fetch_recent_stock_entries(
        self, reference_now: datetime | None = None
    ) -> list[StockEntry]:
        now = reference_now or datetime.now()
        return [
            # Caso 1 da Especificação: Óleo de Soja 900ml
            # Custo: R$ 6,20 | Venda Atual: R$ 7,40 -> Margem: (7.40 - 6.20)/7.40 = 16.21% (16%)
            # Sugestão para 30%: 6.20 / 0.70 = 8.857 -> R$ 8,90
            StockEntry(
                product_id="PROD-001",
                product_name="Óleo de Soja 900ml",
                entry_date=now - timedelta(hours=6),
                cost_price=Decimal("6.20"),
                sale_price=Decimal("7.40"),
            ),
            # Caso 2 da Especificação: Biscoito Recheado X
            # Custo: R$ 2,50 | Venda Atual: R$ 3,10 -> Margem: (3.10 - 2.50)/3.10 = 19.35% (~20%)
            # Nota: Com arredondamento padrão ou 3.125 dá 20%. Vamos usar 3.10 conforme exemplo!
            # Sugestão para 30%: 2.50 / 0.70 = 3.571 -> R$ 3,60
            StockEntry(
                product_id="PROD-002",
                product_name="Biscoito Recheado X",
                entry_date=now - timedelta(hours=12),
                cost_price=Decimal("2.50"),
                sale_price=Decimal("3.10"),
            ),
            # Caso de Controle 1: Produto com margem saudável (35% >= 30%) -> NÃO deve gerar alerta
            StockEntry(
                product_id="PROD-003",
                product_name="Arroz Tipo 1 5kg",
                entry_date=now - timedelta(hours=10),
                cost_price=Decimal("19.50"),
                sale_price=Decimal("30.00"),
            ),
            # Caso de Controle 2: Produto com margem baixa, mas entrada antiga (há 72h > 48h) -> NÃO deve gerar alerta
            StockEntry(
                product_id="PROD-004",
                product_name="Feijão Carioca 1kg",
                entry_date=now - timedelta(hours=72),
                cost_price=Decimal("6.50"),
                sale_price=Decimal("7.50"),
            ),
        ]

    def fetch_customer_debts(
        self, reference_today: date | None = None
    ) -> list[CustomerDebtRecord]:
        today = reference_today or date.today()
        return [
            # Nível 2 (O Cara-de-Pau): Marcos (R$ 142,00) — Comprou há 3 dias, vencido há 40 dias
            CustomerDebtRecord(
                customer_id="CLI-001",
                customer_name="Marcos",
                total_overdue_amount=Decimal("142.00"),
                oldest_due_date=today - timedelta(days=40),
                status="Em Aberto",
                last_purchase_date=today - timedelta(days=3),
                on_time_payments_count=1,
                is_habitual_good_payer=False,
            ),
            # Nível 1 (O Sumido): Dona Neide (R$ 85,50) — Sem visitas há 34 dias, vencido há 35 dias
            CustomerDebtRecord(
                customer_id="CLI-002",
                customer_name="Dona Neide",
                total_overdue_amount=Decimal("85.50"),
                oldest_due_date=today - timedelta(days=35),
                status="Em Aberto",
                last_purchase_date=today - timedelta(days=34),
                on_time_payments_count=0,
                is_habitual_good_payer=False,
            ),
            # Nível 3 (Bom Pagador): Seu Carlos (R$ 60,00) — Bom pagador habitual, vencido há 32 dias
            CustomerDebtRecord(
                customer_id="CLI-003",
                customer_name="Seu Carlos",
                total_overdue_amount=Decimal("60.00"),
                oldest_due_date=today - timedelta(days=32),
                status="Em Aberto",
                last_purchase_date=today - timedelta(days=20),
                on_time_payments_count=8,
                is_habitual_good_payer=True,
            ),
            # Caso de Controle: Cliente devendo há apenas 15 dias (<= 30 dias) -> NÃO deve entrar no alerta
            CustomerDebtRecord(
                customer_id="CLI-004",
                customer_name="João Recente",
                total_overdue_amount=Decimal("50.00"),
                oldest_due_date=today - timedelta(days=15),
                status="Em Aberto",
                last_purchase_date=today - timedelta(days=2),
                on_time_payments_count=2,
            ),
        ]
