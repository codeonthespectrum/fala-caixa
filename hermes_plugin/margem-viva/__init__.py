"""Plugin MargemViva para Hermes Agent.

Registra o toolset `margemviva` com 4 ferramentas 100% Read-Only para consulta ao vivo
do Nex (Nextar) via WhatsApp / CLI e roteamento automático Multi-Loja pelo número do remetente:
- `margemviva_consultar_fiado`
- `margemviva_consultar_produto`
- `margemviva_simular_preco`
- `margemviva_enviar_resumo_agora`
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, ROUND_FLOOR
import json
import os
from pathlib import Path
import re
import sys
import unicodedata
from typing import Any

# Garante que o pacote `nex_alerts` do repositório MargemViva esteja importável
_REPO_ROOT = Path("/Users/kimgomes/.gemini/antigravity/scratch/nex-alertas-mvp")
_SRC_DIR = _REPO_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from nex_alerts.config import AppConfig, _load_env_file, load_multi_store_configs
from nex_alerts.domain.models import FiadoAlertItem, FiadoTier
from nex_alerts.engine.fiado_analyzer import analyze_fiado_debts
from nex_alerts.engine.margin_analyzer import (
    analyze_low_stock_entries,
    analyze_margin_entries,
    calculate_margin_ratio,
    calculate_suggested_price,
)
from nex_alerts.main import build_collector, run_pipeline
from nex_alerts.notification.dispatcher import normalize_phone_number
from nex_alerts.notification.formatter import (
    format_brl,
    format_daily_digest,
    format_individual_fiado_message,
    format_qty,
)

_SKILL_MD = Path(__file__).resolve().parent / "skills" / "consultor" / "SKILL.md"


def _strip_accents(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", str(text or "").strip().lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _phone_variants(phone: str) -> set[str]:
    """Gera variantes com e sem o 9º dígito brasileiro para comparação robusta."""
    digits = normalize_phone_number(phone)
    if not digits:
        return set()
    variants = {digits}
    if digits.startswith("55") and len(digits) == 13 and digits[4] == "9":
        variants.add(digits[:4] + digits[5:])
    elif digits.startswith("55") and len(digits) == 12:
        variants.add(digits[:4] + "9" + digits[4:])
    # Também adiciona sem o DDI 55
    for v in list(variants):
        if v.startswith("55") and len(v) in (12, 13):
            variants.add(v[2:])
    return variants


def _detect_caller_phone_candidates() -> set[str]:
    """Obtém os possíveis números de telefone de quem está conversando na sessão atual do Hermes."""
    candidates: set[str] = set()
    try:
        from gateway.session_context import get_session_env

        raw_ids = [
            get_session_env("HERMES_SESSION_ACTOR_ID", ""),
            get_session_env("HERMES_SESSION_USER_ID", ""),
            get_session_env("HERMES_SESSION_USER_ID_ALT", ""),
            get_session_env("HERMES_SESSION_CHAT_ID", ""),
        ]
    except Exception:
        raw_ids = []

    try:
        from gateway.whatsapp_identity import expand_whatsapp_aliases
    except Exception:
        expand_whatsapp_aliases = None

    for raw in raw_ids:
        if not raw:
            continue
        aliases = expand_whatsapp_aliases(raw) if expand_whatsapp_aliases else {raw}
        for alias in aliases:
            digits = re.sub(r"\D+", "", str(alias).split("@", 1)[0].split(":", 1)[0])
            if digits:
                candidates |= _phone_variants(digits)
    return candidates


def _resolve_store_config(store_hint: str = "") -> AppConfig:
    """Carrega as configurações do MargemViva e seleciona a loja correspondente ao remetente ou ao nome informado."""
    from dataclasses import replace

    env_path = _REPO_ROOT / ".env"
    if env_path.exists():
        _load_env_file(env_path, override=False)

    base_cfg = AppConfig.from_env(env_file=env_path if env_path.exists() else None)
    if (base_cfg.nex_web_email or base_cfg.nex_web_token) and base_cfg.data_source_mode == "mock":
        base_cfg = replace(base_cfg, data_source_mode="nex_web")
    stores_file = _REPO_ROOT / "stores.json"
    stores = load_multi_store_configs(
        base_config=base_cfg,
        stores_file_path=stores_file if stores_file.exists() else None,
    )
    if not stores:
        return base_cfg

    # 1. Se o usuário/admin informou explicitamente o nome da loja
    if store_hint and store_hint.strip():
        hint_norm = _strip_accents(store_hint)
        for st in stores:
            if hint_norm in _strip_accents(st.store_name):
                return st

    # 2. Roteamento automático pelo telefone de quem está falando no WhatsApp
    caller_phones = _detect_caller_phone_candidates()
    if caller_phones:
        for st in stores:
            st_variants = _phone_variants(st.whatsapp_phone) | _phone_variants(st.pix_key)
            if caller_phones & st_variants:
                return st

    # 3. Fallback: primeira loja ativa (ex: Conveniencia Central)
    return stores[0]


def tool_consultar_fiado(
    cliente: str = "",
    incluir_menores_30_dias: bool = True,
    loja: str = "",
) -> str:
    """Consulta clientes com fiado/débito em aberto no Nex (Read-Only) e gera texto pronto de cobrança com PIX."""
    try:
        cfg = _resolve_store_config(loja)
        collector = build_collector(cfg)
        today = date.today()
        debts = collector.fetch_customer_debts(reference_today=today)

        # Classifica também na régua oficial (+30 dias) para exibir o tier quando aplicável
        overdue_alerts = analyze_fiado_debts(
            records=debts,
            pix_key=cfg.pix_key,
            reference_today=today,
            overdue_min_days=cfg.overdue_min_days,
            cara_de_pau_max_days_since_visit=cfg.cara_de_pau_max_days_since_visit,
            sumido_min_days_since_visit=cfg.sumido_min_days_since_visit,
            bom_pagador_min_on_time_payments=cfg.bom_pagador_min_on_time_payments,
        )
        tier_by_id = {a.customer_id: a for a in overdue_alerts}

        query_norm = _strip_accents(cliente)
        results = []
        total_geral = Decimal("0.00")

        # Ordena do maior valor devido para o menor
        debts_sorted = sorted(
            debts,
            key=lambda r: (-r.total_overdue_amount, r.customer_name),
        )

        for rec in debts_sorted:
            if rec.total_overdue_amount <= Decimal("0"):
                continue
            days_overdue = max(0, (today - rec.oldest_due_date).days)
            if not incluir_menores_30_dias and days_overdue <= cfg.overdue_min_days:
                continue
            if query_norm and query_norm not in _strip_accents(rec.customer_name):
                continue

            total_geral += rec.total_overdue_amount
            alert_item = tier_by_id.get(rec.customer_id)
            tier_val = (
                alert_item.tier
                if alert_item
                else (
                    FiadoTier.NIVEL_3_BOM_PAGADOR
                    if rec.is_habitual_good_payer
                    else FiadoTier.NIVEL_1_SUMIDO
                )
            )
            synthetic_item = alert_item or FiadoAlertItem(
                customer_id=rec.customer_id,
                customer_name=rec.customer_name,
                total_overdue_amount=rec.total_overdue_amount,
                days_overdue=days_overdue,
                days_since_last_purchase=(
                    (today - rec.last_purchase_date).days
                    if rec.last_purchase_date
                    else None
                ),
                tier=tier_val,
                pix_key=cfg.pix_key,
            )
            cobranca_msg = format_individual_fiado_message(
                item=synthetic_item,
                store_name=cfg.store_name,
                fallback_pix_key=cfg.pix_key,
            )

            results.append(
                {
                    "cliente": rec.customer_name,
                    "valor_em_aberto": format_brl(rec.total_overdue_amount),
                    "valor_numerico": float(rec.total_overdue_amount),
                    "dias_sem_movimentacao": days_overdue,
                    "acima_de_30_dias": days_overdue > cfg.overdue_min_days,
                    "classificacao_regua": (
                        alert_item.tier.value
                        if alert_item
                        else f"EM_ABERTO_{days_overdue}_DIAS (abaixo do corte de 30 dias)"
                    ),
                    "mensagem_whatsapp_pronta_para_encaminhar": cobranca_msg,
                }
            )

        return json.dumps(
            {
                "success": True,
                "loja": cfg.store_name,
                "chave_pix_loja": cfg.pix_key,
                "filtro_cliente": cliente or "TODOS",
                "quantidade_clientes_encontrados": len(results),
                "soma_total_em_aberto": format_brl(total_geral),
                "clientes": results,
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)


def tool_consultar_produto(
    produto: str = "",
    apenas_estoque_baixo: bool = False,
    apenas_margem_baixa: bool = False,
    limite: int = 25,
    loja: str = "",
) -> str:
    """Consulta estoque atual, estoque mínimo, preço de custo, preço de venda, margem e preço sugerido no Nex (Read-Only)."""
    try:
        cfg = _resolve_store_config(loja)
        collector = build_collector(cfg)
        now = datetime.now()
        entries = collector.fetch_recent_stock_entries(reference_now=now)

        query_norm = _strip_accents(produto)
        matched = []
        for entry in entries:
            if not entry.active:
                continue
            if query_norm and query_norm not in _strip_accents(entry.product_name):
                continue

            margin_ratio = calculate_margin_ratio(entry.cost_price, entry.sale_price)
            margin_pct = int(
                (margin_ratio * Decimal("100")).quantize(
                    Decimal("1"), rounding=ROUND_FLOOR
                )
            )
            suggested = calculate_suggested_price(
                cost_price=entry.cost_price,
                target_margin=cfg.margin_threshold,
                rounding_step=cfg.price_rounding_step,
            )
            cur_stock = entry.current_stock if entry.current_stock is not None else Decimal("0")
            eff_min = (
                entry.min_stock
                if (entry.min_stock is not None and entry.min_stock > Decimal("0"))
                else Decimal("2")
            )
            is_low_stock = (
                entry.stock_control
                and entry.has_stock
                and cur_stock > Decimal("0")
                and cur_stock <= eff_min
            )
            is_low_margin = entry.has_stock and margin_ratio < cfg.margin_threshold

            if apenas_estoque_baixo and not is_low_stock:
                continue
            if apenas_margem_baixa and not is_low_margin:
                continue

            matched.append(
                {
                    "codigo": entry.product_id,
                    "produto": entry.product_name,
                    "estoque_atual": format_qty(cur_stock),
                    "estoque_minimo": format_qty(eff_min),
                    "em_estoque_na_gondola": bool(entry.has_stock and cur_stock > Decimal("0")),
                    "alerta_estoque_baixo": is_low_stock,
                    "preco_custo": format_brl(entry.cost_price),
                    "preco_venda_atual": format_brl(entry.sale_price),
                    "margem_atual_pct": f"{margin_pct}%",
                    "alerta_margem_abaixo_30pct": is_low_margin,
                    "preco_sugerido_para_30pct": format_brl(suggested),
                }
            )

        total_found = len(matched)
        cap = max(1, min(int(limite or 25), 100))
        return json.dumps(
            {
                "success": True,
                "loja": cfg.store_name,
                "filtro_produto": produto or "TODOS",
                "total_encontrados": total_found,
                "exibindo": len(matched[:cap]),
                "produtos": matched[:cap],
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)


def tool_simular_preco(
    custo: float,
    preco_venda_atual: float = 0.0,
    margem_alvo_pct: float = 30.0,
    nome_produto: str = "Produto",
) -> str:
    """Simula a margem bruta real e calcula o preço de venda sugerido para atingir a margem alvo (padrão 30%)."""
    try:
        cost_dec = Decimal(str(custo)).quantize(Decimal("0.01"))
        target_dec = Decimal(str(margem_alvo_pct)) / Decimal("100")
        suggested = calculate_suggested_price(
            cost_price=cost_dec,
            target_margin=target_dec,
            rounding_step=Decimal("0.10"),
        )
        lucro_por_unidade_sugerido = suggested - cost_dec

        result: dict[str, Any] = {
            "success": True,
            "produto": nome_produto,
            "custo_informado": format_brl(cost_dec),
            "margem_alvo": f"{int(margem_alvo_pct)}%",
            "preco_venda_sugerido": format_brl(suggested),
            "lucro_bruto_por_unidade_no_sugerido": format_brl(lucro_por_unidade_sugerido),
        }

        if preco_venda_atual and preco_venda_atual > 0:
            sale_dec = Decimal(str(preco_venda_atual)).quantize(Decimal("0.01"))
            ratio = calculate_margin_ratio(cost_dec, sale_dec)
            pct = int(
                (ratio * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_FLOOR)
            )
            result["preco_venda_atual"] = format_brl(sale_dec)
            result["margem_atual_pct"] = f"{pct}%"
            result["lucro_bruto_atual_por_unidade"] = format_brl(sale_dec - cost_dec)
            result["abaixo_da_margem_alvo"] = ratio < target_dec

        return json.dumps(result, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)


def tool_enviar_resumo_agora(
    enviar_whatsapp: bool = True,
    loja: str = "",
) -> str:
    """Executa o pipeline completo do MargemViva agora e retorna/dispara o Resumo Inteligente do Caixa + mensagens individuais de fiado."""
    try:
        from dataclasses import replace

        cfg = _resolve_store_config(loja)
        if enviar_whatsapp:
            cfg = replace(cfg, whatsapp_provider="hermes")
        else:
            cfg = replace(cfg, whatsapp_provider="none")

        res = run_pipeline(config=cfg, print_to_stdout=False)
        return json.dumps(
            {
                "success": True,
                "loja": cfg.store_name,
                "whatsapp_destino": cfg.whatsapp_phone,
                "provedor_usado": res.provider_used,
                "enviado_no_whatsapp": res.webhook_sent,
                "status_http": res.webhook_status_code,
                "erro_envio": res.webhook_error,
                "arquivo_salvo": str(res.saved_file_path),
                "texto_resumo": res.message_text,
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)


def register(ctx) -> None:
    """Ponto de entrada do plugin no Hermes Agent."""
    if _SKILL_MD.exists():
        ctx.register_skill(
            name="consultor",
            path=_SKILL_MD,
            description=(
                "Consultor Conversacional MargemViva para lojistas Nex (Nextar): "
                "consulta fiado + mensagem PIX, estoque/preço de produtos, simulador de margem e resumo diário."
            ),
        )

    ctx.register_tool(
        name="margemviva_consultar_fiado",
        toolset="margemviva",
        description=(
            "Consulta clientes com fiado/débito em aberto no Nex (Nextar) em modo 100% Read-Only. "
            "Retorna o valor devido, dias sem movimentação, classificação na Régua de Fiado e a mensagem "
            "individual pronta com a Chave PIX da loja para encaminhar no WhatsApp."
        ),
        emoji="💳",
        schema={
            "name": "margemviva_consultar_fiado",
            "description": (
                "Consulta saldos devedores de fiado de clientes no Nex (Nextar) e gera a mensagem individual "
                "de cobrança com Chave PIX pronta para encaminhar."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "cliente": {
                        "type": "string",
                        "description": "Nome (ou parte do nome) do cliente para buscar. Deixe vazio para listar todos os clientes com fiado em aberto.",
                    },
                    "incluir_menores_30_dias": {
                        "type": "boolean",
                        "description": "Se true (padrão), inclui todos os clientes devendo (mesmo com menos de 30 dias). Se false, mostra apenas os atrasados há +30 dias.",
                    },
                    "loja": {
                        "type": "string",
                        "description": "Opcional. Nome da loja (se vazio, identifica automaticamente pelo telefone do lojista no WhatsApp).",
                    },
                },
                "required": [],
            },
        },
        handler=lambda args, **kw: tool_consultar_fiado(
            cliente=str(args.get("cliente") or ""),
            incluir_menores_30_dias=bool(args.get("incluir_menores_30_dias", True)),
            loja=str(args.get("loja") or ""),
        ),
    )

    ctx.register_tool(
        name="margemviva_consultar_produto",
        toolset="margemviva",
        description=(
            "Consulta produtos no Nex (Nextar) em modo 100% Read-Only: estoque atual, estoque mínimo, "
            "preço de custo, preço de venda atual, margem bruta (%) e preço sugerido para manter 30% de margem."
        ),
        emoji="📦",
        schema={
            "name": "margemviva_consultar_produto",
            "description": (
                "Busca produtos no catálogo ativo do Nex por nome ou filtra itens com estoque baixo ou margem abaixo de 30%."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "produto": {
                        "type": "string",
                        "description": "Nome ou palavra-chave do produto (ex: 'Coca', 'Skol', '51', 'Oreo'). Deixe vazio para listar vários.",
                    },
                    "apenas_estoque_baixo": {
                        "type": "boolean",
                        "description": "Se true, retorna apenas produtos ativos cujo estoque atual está menor ou igual ao mínimo.",
                    },
                    "apenas_margem_baixa": {
                        "type": "boolean",
                        "description": "Se true, retorna apenas produtos em estoque cuja margem atual está abaixo de 30%.",
                    },
                    "limite": {
                        "type": "integer",
                        "description": "Quantidade máxima de produtos para retornar (padrão: 25).",
                    },
                    "loja": {
                        "type": "string",
                        "description": "Opcional. Nome da loja.",
                    },
                },
                "required": [],
            },
        },
        handler=lambda args, **kw: tool_consultar_produto(
            produto=str(args.get("produto") or ""),
            apenas_estoque_baixo=bool(args.get("apenas_estoque_baixo", False)),
            apenas_margem_baixa=bool(args.get("apenas_margem_baixa", False)),
            limite=int(args.get("limite") or 25),
            loja=str(args.get("loja") or ""),
        ),
    )

    ctx.register_tool(
        name="margemviva_simular_preco",
        toolset="margemviva",
        description=(
            "Calcula o preço de venda sugerido para garantir 30% de margem (ou outra margem alvo) "
            "a partir de um novo custo de fornecedor, com arredondamento comercial de R$ 0,10."
        ),
        emoji="🧮",
        schema={
            "name": "margemviva_simular_preco",
            "description": (
                "Simula margem de lucro e preço ideal de gôndola dado o preço de custo e (opcionalmente) o preço de venda atual."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "custo": {
                        "type": "number",
                        "description": "Preço de custo unitário ou da caixa/fardo em R$ (ex: 36.00).",
                    },
                    "preco_venda_atual": {
                        "type": "number",
                        "description": "Opcional. Preço de venda atual na gôndola em R$ para comparar a margem atual.",
                    },
                    "margem_alvo_pct": {
                        "type": "number",
                        "description": "Margem de lucro desejada em % sobre a venda (padrão: 30).",
                    },
                    "nome_produto": {
                        "type": "string",
                        "description": "Nome do produto sendo simulado.",
                    },
                },
                "required": ["custo"],
            },
        },
        handler=lambda args, **kw: tool_simular_preco(
            custo=float(args.get("custo") or 0.0),
            preco_venda_atual=float(args.get("preco_venda_atual") or 0.0),
            margem_alvo_pct=float(args.get("margem_alvo_pct") or 30.0),
            nome_produto=str(args.get("nome_produto") or "Produto"),
        ),
    )

    ctx.register_tool(
        name="margemviva_enviar_resumo_agora",
        toolset="margemviva",
        description=(
            "Executa o motor completo do MargemViva agora mesmo (Margem < 30% em produtos ativos + Estoque Baixo "
            "+ Régua de Fiado +30 dias com mensagens individuais PIX) e envia pelo WhatsApp do Hermes."
        ),
        emoji="🚀",
        schema={
            "name": "margemviva_enviar_resumo_agora",
            "description": (
                "Gera e envia o Resumo Inteligente do Caixa completo + mensagens individuais de fiado no WhatsApp."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "enviar_whatsapp": {
                        "type": "boolean",
                        "description": "Se true (padrão), dispara direto no WhatsApp do lojista via bridge do Hermes.",
                    },
                    "loja": {
                        "type": "string",
                        "description": "Opcional. Nome da loja.",
                    },
                },
                "required": [],
            },
        },
        handler=lambda args, **kw: tool_enviar_resumo_agora(
            enviar_whatsapp=bool(args.get("enviar_whatsapp", True)),
            loja=str(args.get("loja") or ""),
        ),
    )


__all__ = ["register"]
