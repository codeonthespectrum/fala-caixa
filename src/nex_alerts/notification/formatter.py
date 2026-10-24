"""Formatador de mensagens WhatsApp de acordo com a especificação oficial do MVP."""

from __future__ import annotations

from decimal import Decimal

from nex_alerts.domain.models import (
    DailyDigestReport,
    FiadoAlertItem,
    FiadoTier,
    LowStockAlertItem,
    MarginAlertItem,
)


def format_brl(value: Decimal) -> str:
    """Formata um valor Decimal no padrão monetário brasileiro (ex: R$ 1.234,56)."""
    quantized = value.quantize(Decimal("0.01"))
    formatted = f"{quantized:,.2f}"
    # Converte de 1,234.56 para 1.234,56
    brl = formatted.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {brl}"


def format_margin_item(item: MarginAlertItem) -> str:
    """Formata um item de alerta de margem oculta."""
    cost_str = format_brl(item.cost_price)
    suggested_str = format_brl(item.suggested_sale_price)
    current_str = format_brl(item.current_sale_price)
    return (
        f"• {item.product_name}: Custo subiu para {cost_str}. "
        f"Margem caiu para {item.current_margin_pct}%.\n"
        f"👉 Sugestão de preço para manter 30%: {suggested_str} (Atual: {current_str})"
    )


def format_fiado_item(item: FiadoAlertItem, fallback_pix_key: str) -> str:
    """Formata um item da Régua de Fiado de acordo com o seu Nível (1, 2 ou 3)."""
    amount_str = format_brl(item.total_overdue_amount)

    if item.tier == FiadoTier.NIVEL_2_CARA_DE_PAU:
        days_visit = item.days_since_last_purchase or 0
        visit_label = (
            "Comprou hoje."
            if days_visit == 0
            else f"Comprou há {days_visit} {'dia' if days_visit == 1 else 'dias'}."
        )
        return (
            f"• [Atenção Caixa] Travar novo fiado:\n"
            f"{item.customer_name} ({amount_str}) — {visit_label}"
        )

    if item.tier == FiadoTier.NIVEL_1_SUMIDO:
        days_visit = item.days_since_last_purchase
        visit_label = (
            f"Sem visitas há {days_visit} dias."
            if days_visit is not None
            else f"Em atraso há {item.days_overdue} dias."
        )
        pix_to_show = item.pix_key or fallback_pix_key
        return (
            f"• [Lembrete] Cliente sumido:\n"
            f"{item.customer_name} ({amount_str}) — {visit_label}\n"
            f"Chave PIX: [{pix_to_show}]"
        )

    # Nível 3: Bom Pagador
    return (
        f"• [Acompanhamento]:\n"
        f"{item.customer_name} ({amount_str}) — Bom pagador habitual."
    )


def format_qty(value: Decimal) -> str:
    """Formata quantidade de estoque sem casas decimais desnecessárias (ex: 3.0 -> '3')."""
    if value == value.to_integral_value():
        return str(int(value))
    return f"{value.normalize():f}".replace(".", ",")


def format_low_stock_item(item: LowStockAlertItem) -> str:
    """Formata um item de alerta de estoque baixo (Funcionalidade C)."""
    cur_str = format_qty(item.current_stock)
    min_str = format_qty(item.min_stock)
    unit_label = "unidade" if item.current_stock == Decimal("1") else "unidades"
    return (
        f"• {item.product_name}: Restam apenas {cur_str} {unit_label} "
        f"(Mínimo ideal: {min_str})"
    )


def format_individual_fiado_message(
    item: FiadoAlertItem, store_name: str, fallback_pix_key: str
) -> str:
    """Gera uma mensagem individual pronta para encaminhar ao cliente de fiado no WhatsApp."""
    amount_str = format_brl(item.total_overdue_amount)
    pix_to_show = item.pix_key or fallback_pix_key
    clean_store = store_name.strip() or "nossa loja"

    if item.tier == FiadoTier.NIVEL_3_BOM_PAGADOR:
        return (
            f"Olá, *{item.customer_name}*, tudo bem? 😊\n"
            f"Passando aqui da *{clean_store}* só para atualizar o seu controle: "
            f"consta um saldo em aberto de *{amount_str}* na sua conta.\n\n"
            f"Se preferir acertar por PIX, nossa chave é:\n"
            f"🔑 *Chave PIX:* {pix_to_show}\n\n"
            f"Qualquer dúvida é só chamar. Muito obrigado pela parceria de sempre! 🙏"
        )

    return (
        f"Olá, *{item.customer_name}*, tudo bem?\n"
        f"Passando aqui da *{clean_store}* para lembrar que ficou um saldo em aberto "
        f"de *{amount_str}* na sua conta.\n\n"
        f"Quando puder, você pode acertar aqui na loja ou direto pelo nosso PIX:\n"
        f"🔑 *Chave PIX:* {pix_to_show}\n\n"
        f"Qualquer dúvida é só chamar. Obrigado! 🙏"
    )


def format_daily_digest(report: DailyDigestReport) -> str:
    """Gera o texto único consolidado pronto para disparo no WhatsApp (lista completa)."""
    lines: list[str] = ["📦 RESUMO INTELIGENTE DO CAIXA"]

    if not report.has_alerts:
        lines.append("")
        lines.append("✅ Nenhum alerta crítico de margem, estoque ou fiado identificado hoje.")
        return "\n".join(lines)

    if report.margin_alerts:
        lines.append("")
        lines.append("🚨 Reajuste de Margem Necessário (Entradas Recentes):")
        for item in report.margin_alerts:
            lines.append(format_margin_item(item))

    if report.low_stock_alerts:
        lines.append("")
        lines.append("📉 Alerta de Estoque Baixo (Reposição Necessária):")
        for item in report.low_stock_alerts:
            lines.append(format_low_stock_item(item))

    if report.fiado_alerts:
        lines.append("")
        lines.append("⚠️ Cobrança de Fiado (+30 dias):")
        for item in report.fiado_alerts:
            lines.append(format_fiado_item(item, fallback_pix_key=report.pix_key))
        lines.append("")
        lines.append(
            "📲 *Abaixo estão as mensagens individuais prontas para você encaminhar para cada cliente:*"
        )

    return "\n".join(lines)
