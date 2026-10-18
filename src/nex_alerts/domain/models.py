"""Modelos de domínio imutáveis para entradas de estoque, débitos de fiado e alertas."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum


class FiadoTier(str, Enum):
    """Níveis de classificação da Régua de Fiado (Funcionalidade B)."""

    NIVEL_1_SUMIDO = "NIVEL_1_SUMIDO"
    NIVEL_2_CARA_DE_PAU = "NIVEL_2_CARA_DE_PAU"
    NIVEL_3_BOM_PAGADOR = "NIVEL_3_BOM_PAGADOR"


@dataclass(frozen=True)
class StockEntry:
    """Representa um registro de reposição/entrada de produto no estoque."""

    product_id: str
    product_name: str
    entry_date: datetime
    cost_price: Decimal
    sale_price: Decimal


@dataclass(frozen=True)
class MarginAlertItem:
    """Resultado processado de um item com margem inferior ao limite (< 30%)."""

    product_id: str
    product_name: str
    cost_price: Decimal
    current_sale_price: Decimal
    current_margin_ratio: Decimal  # Ex.: Decimal("0.1621")
    current_margin_pct: int  # Ex.: 16 (para exibição "16%")
    suggested_sale_price: Decimal
    entry_date: datetime


@dataclass(frozen=True)
class CustomerDebtRecord:
    """Representa a situação consolidada de débito e comportamento de um cliente."""

    customer_id: str
    customer_name: str
    total_overdue_amount: Decimal
    oldest_due_date: date
    status: str = "Em Aberto"
    last_purchase_date: date | None = None
    on_time_payments_count: int = 0
    is_habitual_good_payer: bool = False


@dataclass(frozen=True)
class FiadoAlertItem:
    """Item classificado na Régua de Fiado em 3 Níveis."""

    customer_id: str
    customer_name: str
    total_overdue_amount: Decimal
    days_overdue: int
    days_since_last_purchase: int | None
    tier: FiadoTier
    pix_key: str | None = None


@dataclass(frozen=True)
class DailyDigestReport:
    """Relatório consolidado diário pronto para formatação e envio."""

    generated_at: datetime
    store_name: str
    pix_key: str
    whatsapp_phone: str = ""
    margin_alerts: list[MarginAlertItem] = field(default_factory=list)
    fiado_alerts: list[FiadoAlertItem] = field(default_factory=list)

    @property
    def has_alerts(self) -> bool:
        """Retorna True se houver pelo menos um alerta de margem ou de fiado."""
        return bool(self.margin_alerts or self.fiado_alerts)
