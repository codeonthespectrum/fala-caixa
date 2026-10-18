"""Interface base somente-leitura (Read-Only) para coletores de dados do Nex."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime

from nex_alerts.domain.models import CustomerDebtRecord, StockEntry


class ReadOnlyViolationError(RuntimeError):
    """Exceção lançada caso qualquer operação de escrita seja tentada no conector."""


class BaseNexCollector(ABC):
    """Contrato estrito de leitura para obtenção de dados do Nex (Nextar)."""

    @abstractmethod
    def fetch_recent_stock_entries(
        self, reference_now: datetime | None = None
    ) -> list[StockEntry]:
        """Retorna registros de entradas/reposições de produtos em modo somente-leitura."""

    @abstractmethod
    def fetch_customer_debts(
        self, reference_today: date | None = None
    ) -> list[CustomerDebtRecord]:
        """Retorna registros de débitos (fiado) e histórico de compras dos clientes."""
