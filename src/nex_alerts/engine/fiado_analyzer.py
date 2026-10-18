"""Motor da Funcionalidade B: Régua de Fiado Estratificada em 3 Níveis (Anti-Calote).

Critério Base de Corte:
- Contas a receber com Status == "Em Aberto" e Data de Vencimento < (Hoje - 30 dias)
  (ou seja, estritamente mais de 30 dias em atraso, ou >= 30 dias conforme configuração).

Estratificação:
- Nível 2 (O Cara-de-Pau): Cliente em atraso (+30 dias) que continua frequentando a loja
  (compras nos últimos 7 a 15 dias -> days_since_last_purchase <= 15).
- Nível 3 (Bom Pagador): Cliente em atraso (+30 dias) com histórico recorrente de
  pagamentos em dia em meses anteriores (is_habitual_good_payer=True ou
  on_time_payments_count >= bom_pagador_min_on_time_payments).
- Nível 1 (O Sumido): Cliente em atraso (+30 dias) sem compras registradas nos últimos
  30 dias (days_since_last_purchase >= 30 ou sem registro de compra recente).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from nex_alerts.domain.models import CustomerDebtRecord, FiadoAlertItem, FiadoTier


OPEN_STATUS_ALIASES = {
    "em aberto",
    "aberto",
    "pendente",
    "vencido",
    "atrasado",
    "open",
    "overdue",
    "a receber",
    "débito",
    "debito",
}


def is_open_status(status: str) -> bool:
    """Verifica se o status da conta corresponde a um débito em aberto."""
    normalized = status.strip().lower()
    return normalized in OPEN_STATUS_ALIASES


def classify_customer_debt(
    record: CustomerDebtRecord,
    reference_today: date,
    pix_key: str,
    overdue_min_days: int = 30,
    cara_de_pau_max_days_since_visit: int = 15,
    sumido_min_days_since_visit: int = 30,
    bom_pagador_min_on_time_payments: int = 3,
) -> FiadoAlertItem | None:
    """Avalia um registro de cliente e retorna o alerta estratificado ou None se fora do corte."""
    if not is_open_status(record.status):
        return None

    if record.total_overdue_amount <= Decimal("0"):
        return None

    days_overdue = (reference_today - record.oldest_due_date).days
    # Critério Base: Data de Vencimento < (Hoje - 30 dias) -> days_overdue > 30
    if days_overdue <= overdue_min_days:
        return None

    days_since_last_purchase: int | None = None
    if record.last_purchase_date is not None:
        days_since_last_purchase = max(
            0, (reference_today - record.last_purchase_date).days
        )

    is_good_payer = (
        record.is_habitual_good_payer
        or record.on_time_payments_count >= bom_pagador_min_on_time_payments
    )

    # Prioridade 1 de Contenção no Caixa: Nível 2 (O Cara-de-Pau)
    # Continua comprando recentemente (nos últimos 15 dias) mesmo devendo há +30 dias
    if (
        days_since_last_purchase is not None
        and days_since_last_purchase <= cara_de_pau_max_days_since_visit
    ):
        tier = FiadoTier.NIVEL_2_CARA_DE_PAU
        item_pix = None
    # Prioridade 2: Nível 3 (Bom Pagador habitual)
    elif is_good_payer:
        tier = FiadoTier.NIVEL_3_BOM_PAGADOR
        item_pix = None
    # Prioridade 3: Nível 1 (O Sumido - sem compras nos últimos 30 dias ou período intermediário sem histórico)
    else:
        tier = FiadoTier.NIVEL_1_SUMIDO
        item_pix = pix_key

    return FiadoAlertItem(
        customer_id=record.customer_id,
        customer_name=record.customer_name,
        total_overdue_amount=record.total_overdue_amount.quantize(Decimal("0.01")),
        days_overdue=days_overdue,
        days_since_last_purchase=days_since_last_purchase,
        tier=tier,
        pix_key=item_pix,
    )


def analyze_fiado_debts(
    records: list[CustomerDebtRecord],
    pix_key: str,
    reference_today: date | None = None,
    overdue_min_days: int = 30,
    cara_de_pau_max_days_since_visit: int = 15,
    sumido_min_days_since_visit: int = 30,
    bom_pagador_min_on_time_payments: int = 3,
) -> list[FiadoAlertItem]:
    """Processa todos os clientes e retorna a lista ordenada por prioridade operacional."""
    today = reference_today or date.today()
    alerts: list[FiadoAlertItem] = []

    for record in records:
        item = classify_customer_debt(
            record=record,
            reference_today=today,
            pix_key=pix_key,
            overdue_min_days=overdue_min_days,
            cara_de_pau_max_days_since_visit=cara_de_pau_max_days_since_visit,
            sumido_min_days_since_visit=sumido_min_days_since_visit,
            bom_pagador_min_on_time_payments=bom_pagador_min_on_time_payments,
        )
        if item is not None:
            alerts.append(item)

    # Ordena na mesma sequência do Exemplo do Alerta Consolidado Diário (Seção 5):
    # 1º Nível 2 ([Atenção Caixa] Travar novo fiado)
    # 2º Nível 1 ([Lembrete] Cliente sumido)
    # 3º Nível 3 ([Acompanhamento] Bom pagador habitual)
    tier_priority = {
        FiadoTier.NIVEL_2_CARA_DE_PAU: 1,
        FiadoTier.NIVEL_1_SUMIDO: 2,
        FiadoTier.NIVEL_3_BOM_PAGADOR: 3,
    }
    alerts.sort(
        key=lambda x: (
            tier_priority[x.tier],
            -x.total_overdue_amount,
            x.customer_name,
        )
    )
    return alerts
