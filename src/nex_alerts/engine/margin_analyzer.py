"""Motor da Funcionalidade A: Alerta de Margem Oculta na Reposição (Anti-Prejuízo).

Regras:
- Filtra entradas de estoque registradas nas últimas `lookback_hours` (24 a 48h).
- Calcula Margem = (Preço de Venda - Preço de Custo) / Preço de Venda.
- Filtra produtos onde Margem < 0.30 (30%).
- Calcula Preço Sugerido = Preço de Custo / (1 - 0.30) = Preço de Custo / 0.70,
  com arredondamento comercial configurável (padrão: múltiplo superior de R$ 0,10).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP

from nex_alerts.domain.models import MarginAlertItem, StockEntry


def round_up_to_step(value: Decimal, step: Decimal = Decimal("0.10")) -> Decimal:
    """Arredonda um valor monetário para cima no múltiplo de `step` (ex: 0.10 ou 0.01).

    Exemplos com step=0.10:
    - 6.20 / 0.70 = 8.8571... -> 8.90
    - 2.50 / 0.70 = 3.5714... -> 3.60
    """
    if step <= Decimal("0"):
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    # Trunca/arredonda para 4 casas primeiro para evitar ruído de dízima exata
    normalized = value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    steps_count = (normalized / step).to_integral_value(rounding=ROUND_CEILING)
    result = steps_count * step
    return result.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def calculate_margin_ratio(cost_price: Decimal, sale_price: Decimal) -> Decimal:
    """Calcula a margem bruta sobre o preço de venda: (Venda - Custo) / Venda."""
    if sale_price <= Decimal("0"):
        return Decimal("-1.0")
    return (sale_price - cost_price) / sale_price


def calculate_suggested_price(
    cost_price: Decimal,
    target_margin: Decimal = Decimal("0.30"),
    rounding_step: Decimal = Decimal("0.10"),
) -> Decimal:
    """Calcula o preço sugerido para recompor a margem alvo: Custo / (1 - target_margin)."""
    divisor = Decimal("1.00") - target_margin
    if divisor <= Decimal("0"):
        raise ValueError("A margem alvo deve ser menor que 1.0 (100%).")
    raw_suggested = cost_price / divisor
    return round_up_to_step(raw_suggested, step=rounding_step)


def analyze_margin_entries(
    entries: list[StockEntry],
    reference_now: datetime | None = None,
    lookback_hours: int = 48,
    margin_threshold: Decimal = Decimal("0.30"),
    rounding_step: Decimal = Decimal("0.10"),
) -> list[MarginAlertItem]:
    """Varre entradas recentes e retorna os alertas de margem abaixo de `margin_threshold`."""
    now = reference_now or datetime.now()
    cutoff_time = now - timedelta(hours=lookback_hours)

    # Se houver múltiplas entradas do mesmo produto na janela, avalia a mais recente
    latest_by_product: dict[str, StockEntry] = {}
    for entry in entries:
        if cutoff_time <= entry.entry_date <= now + timedelta(minutes=5):
            existing = latest_by_product.get(entry.product_id)
            if existing is None or entry.entry_date >= existing.entry_date:
                latest_by_product[entry.product_id] = entry

    alerts: list[MarginAlertItem] = []
    for entry in latest_by_product.values():
        if entry.cost_price <= Decimal("0"):
            continue

        margin_ratio = calculate_margin_ratio(entry.cost_price, entry.sale_price)
        if margin_ratio < margin_threshold:
            margin_pct = int(
                (margin_ratio * Decimal("100")).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                )
            )
            suggested_price = calculate_suggested_price(
                cost_price=entry.cost_price,
                target_margin=margin_threshold,
                rounding_step=rounding_step,
            )
            alerts.append(
                MarginAlertItem(
                    product_id=entry.product_id,
                    product_name=entry.product_name,
                    cost_price=entry.cost_price.quantize(Decimal("0.01")),
                    current_sale_price=entry.sale_price.quantize(Decimal("0.01")),
                    current_margin_ratio=margin_ratio.quantize(Decimal("0.0001")),
                    current_margin_pct=margin_pct,
                    suggested_sale_price=suggested_price,
                    entry_date=entry.entry_date,
                )
            )

    # Ordena da menor margem (mais crítica) para a maior
    alerts.sort(key=lambda item: (item.current_margin_ratio, item.product_name))
    return alerts
