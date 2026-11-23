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
import difflib
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
import unicodedata
from typing import Any

# Garante que o pacote `nex_alerts` do repositório MargemViva esteja importável
_REPO_ROOT = Path("/Users/kimgomes/.gemini/antigravity/scratch/nex-alertas-mvp")
_SRC_DIR = _REPO_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from nex_alerts.config import AppConfig, _load_env_file, load_multi_store_configs
from nex_alerts.db.store_repository import StoreRepository
from nex_alerts.db.store_resolver import StoreResolver
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

_CACHE_LOCK = threading.Lock()
_CACHE_TTL_SECONDS = 300.0  # 5 minutos de cache em memória para respostas instantâneas
_PRODUCTS_CACHE: dict[str, tuple[float, list[Any]]] = {}
_DEBTS_CACHE: dict[str, tuple[float, list[Any]]] = {}


def _strip_accents(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", str(text or "").strip().lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


_UNIT_EXPAND_MAP = {
    "1l": {"1l", "1lt", "1 litro", "1 litros", "1000ml"},
    "1lt": {"1l", "1lt", "1 litro", "1 litros", "1000ml"},
    "2l": {"2l", "2lt", "2 litros", "2 litro", "2000ml"},
    "2lt": {"2l", "2lt", "2 litros", "2 litro", "2000ml"},
    "litro": {"litro", "litros", "1l", "1lt", "2l", "2lt", "lt"},
    "litros": {"litro", "litros", "1l", "1lt", "2l", "2lt", "lt"},
    "350ml": {"350ml", "lata", "350"},
    "lata": {"lata", "latinha", "350ml", "269ml", "473ml", "latão", "latao"},
    "latinha": {"lata", "latinha", "350ml", "269ml"},
    "latao": {"latao", "latão", "473ml", "550ml"},
    "ln": {"ln", "long neck", "longneck", "330ml"},
    "long": {"ln", "long", "330ml"},
    "neck": {"ln", "neck", "330ml"},
    "cx": {"cx", "caixa", "fardo", "pack", "c/12", "12x"},
    "caixa": {"cx", "caixa", "fardo"},
    "fardo": {"cx", "caixa", "fardo", "c/12", "12x"},
    "und": {"und", "un", "unidade", "avulsa", "avulso"},
    "unidade": {"und", "un", "unidade"},
    "cerveja": {"cerv", "cerveja"},
    "cerv": {"cerv", "cerveja"},
    "biscoito": {"bisc", "biscoito", "bolacha"},
    "bolacha": {"bisc", "biscoito", "bolacha"},
    "bisc": {"bisc", "biscoito", "bolacha"},
    "chocolate": {"choc", "choco", "chocolate"},
    "choc": {"choc", "choco", "chocolate"},
    "refrigerante": {"refri", "refrigerante", "coca", "fanta", "sprite", "guarana", "pepsi"},
    "refri": {"refri", "refrigerante", "coca", "fanta", "sprite", "guarana", "pepsi"},
    "absorvente": {"abs", "absorvente"},
    "abs": {"abs", "absorvente"},
    "cigarro": {"cigarro", "carteira", "maco"},
    "carteira": {"cigarro", "carteira"},
    "desodorante": {"desod", "desodorante"},
    "desod": {"desod", "desodorante"},
    "energetico": {"energetico", "energ", "monster", "infinity", "baly", "red bull"},
    "achocolatado": {"achoc", "achocolatado", "nescau", "toddynho"},
    "achoc": {"achoc", "achocolatado"},
    "aparelho": {"apar", "aparelho", "barbear", "prestobarba", "gillette"},
    "barbeador": {"apar", "aparelho", "barbear", "barbeador"},
}

_STOP_WORDS = {
    "de", "da", "do", "das", "dos", "com", "sem", "em", "no", "na", "nos", "nas",
    "para", "pra", "pro", "por", "um", "uma", "o", "a", "os", "as", "tem", "qual",
    "quanto", "quantas", "quantos", "estoque", "preco", "valor", "custa", "ta", "esta",
}


def _extract_query_tokens(query: str) -> list[str]:
    q_norm = _strip_accents(query)
    # Normaliza padrões juntos como "2 l" -> "2l", "350 ml" -> "350ml"
    q_norm = re.sub(r"(\d+)\s*(l|lt|ml|g|kg)\b", r"\1\2", q_norm)
    return [
        tok
        for tok in re.split(r"[^a-z0-9]+", q_norm)
        if tok and tok not in _STOP_WORDS
    ]


def _score_smart_query(query: str, target_name: str) -> float:
    """Pontua o grau de correspondência PLN entre o termo falado pelo lojista e o cadastro no ERP/Nex.
    - >= 1.0: match exato ou todos os tokens (com sinônimos/abreviações) bateram
    - 0.35 .. 0.99: match parcial (algumas palavras-chave ou similaridade fonética/fuzzy)
    - 0.0: sem relação
    """
    q_norm = _strip_accents(query)
    if not q_norm:
        return 1.0
    t_norm = _strip_accents(target_name)
    if q_norm == t_norm:
        return 2.0
    if q_norm in t_norm:
        return 1.5

    q_tokens = _extract_query_tokens(query)
    if not q_tokens:
        return 1.0

    t_tokens = [tok for tok in re.split(r"[^a-z0-9]+", t_norm) if tok]
    matched_tokens = 0.0

    for tok in q_tokens:
        variants = _UNIT_EXPAND_MAP.get(tok, {tok})
        # 1. Substring ou sinônimo direto no nome do produto
        if any(v in t_norm for v in variants):
            matched_tokens += 1.0
            continue
        # 2. Prefix match (ex: "cervej" casa com "cerv", "choc" casa com "chocolate")
        if any(
            (len(tok) >= 3 and len(tt) >= 3 and (tok.startswith(tt[:4]) or tt.startswith(tok[:4])))
            for tt in t_tokens
        ):
            matched_tokens += 0.85
            continue
        # 3. Fuzzy / erro de digitação (ex: "coca colla", "budwiser", "daniloco")
        best_fuzzy = max(
            (difflib.SequenceMatcher(None, tok, tt).ratio() for tt in t_tokens),
            default=0.0,
        )
        if best_fuzzy >= 0.74:
            matched_tokens += best_fuzzy

    ratio = matched_tokens / len(q_tokens)
    if ratio >= 0.99:
        return 1.0 + (0.1 * len(q_tokens))
    return ratio


def _matches_smart_query(query: str, target_name: str) -> bool:
    """Compatibilidade booleana rápida (>= 60% dos tokens ou match direto)."""
    return _score_smart_query(query, target_name) >= 0.60


def _get_cached_stock_entries(cfg: AppConfig) -> list[Any]:
    key = f"{cfg.store_name}:{cfg.nex_web_email}:{cfg.data_source_mode}"
    now_ts = time.monotonic()
    with _CACHE_LOCK:
        cached = _PRODUCTS_CACHE.get(key)
        if cached and (now_ts - cached[0]) < _CACHE_TTL_SECONDS:
            return cached[1]
    collector = build_collector(cfg)
    entries = collector.fetch_recent_stock_entries(reference_now=datetime.now())
    with _CACHE_LOCK:
        _PRODUCTS_CACHE[key] = (now_ts, entries)
    return entries


def _get_cached_customer_debts(cfg: AppConfig, today: date) -> list[Any]:
    key = f"{cfg.store_name}:{cfg.nex_web_email}:{cfg.data_source_mode}:{today.isoformat()}"
    now_ts = time.monotonic()
    with _CACHE_LOCK:
        cached = _DEBTS_CACHE.get(key)
        if cached and (now_ts - cached[0]) < _CACHE_TTL_SECONDS:
            return cached[1]
    collector = build_collector(cfg)
    debts = collector.fetch_customer_debts(reference_today=today)
    with _CACHE_LOCK:
        _DEBTS_CACHE[key] = (now_ts, debts)
    return debts


def _phone_variants(phone: str) -> set[str]:
    """Gera variantes com e sem o 9º dígito brasileiro para comparação robusta.

    Delegada canônica para `nex_alerts.phone_identity.variantes`.
    """
    from nex_alerts.phone_identity import variantes

    return variantes(phone, incluir_sem_ddi=True)


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


class SubscriptionInactiveError(RuntimeError):
    """Alias legado: a decisão de assinatura mora no Resolver de Loja."""

    def __init__(self, store_name: str, status: str) -> None:
        self.store_name = store_name
        self.status = status
        super().__init__(
            f"O acesso da loja '{store_name}' está '{status}'. "
            "Para reativar por R$ 67,00/mês, entre em contato com o suporte do Fala, Caixa!."
        )


def _resolve_store_config(store_hint: str = "") -> AppConfig:
    """Carrega as configurações do Fala, Caixa! priorizando o banco SQLite Multi-Loja."""
    from dataclasses import replace

    env_path = _REPO_ROOT / ".env"
    if env_path.exists():
        _load_env_file(env_path, override=False)

    base_cfg = AppConfig.from_env(env_file=env_path if env_path.exists() else None)
    if (base_cfg.nex_web_email or base_cfg.nex_web_token) and base_cfg.data_source_mode == "mock":
        base_cfg = replace(base_cfg, data_source_mode="nex_web")

    db_path = _REPO_ROOT / "data" / "falacaixa.db"
    repo = StoreRepository(db_path)
    repo.bootstrap_from_env_if_empty(env_path)
    db_stores = repo.list_stores()

    if db_stores:
        # Resolver de Loja: decisão única de identidade, autorização e assinatura.
        # Nunca adivinha loja por fallback; o hint `loja` só vale para membros
        # daquela loja ou para Admins de Suporte declarados em FALACAIXA_ADMIN_PHONES.
        admin_csv = os.getenv("FALACAIXA_ADMIN_PHONES", "")
        admin_phones = [p.strip() for p in admin_csv.split(",") if p.strip()]
        resolver = StoreResolver(repo, admin_phones=admin_phones)
        caller_phones = _detect_caller_phone_candidates()
        store = resolver.autorizar(caller_phones, hint_loja=store_hint)
        return repo.to_app_config(store, base_config=base_cfg)

    stores_file = _REPO_ROOT / "stores.json"
    stores = load_multi_store_configs(
        base_config=base_cfg,
        stores_file_path=stores_file if stores_file.exists() else None,
    )
    if not stores:
        return base_cfg

    if store_hint and store_hint.strip():
        hint_norm = _strip_accents(store_hint)
        for st in stores:
            if hint_norm in _strip_accents(st.store_name):
                return st

    caller_phones = _detect_caller_phone_candidates()
    if caller_phones:
        for st in stores:
            st_variants = _phone_variants(st.whatsapp_phone) | _phone_variants(st.pix_key)
            if caller_phones & st_variants:
                return st

    return stores[0]


def tool_consultar_fiado(
    cliente: str = "",
    incluir_menores_30_dias: bool = True,
    loja: str = "",
) -> str:
    """Consulta clientes com fiado/débito em aberto no Nex (Read-Only) e gera texto pronto de cobrança com PIX."""
    try:
        cfg = _resolve_store_config(loja)
        today = date.today()
        debts = _get_cached_customer_debts(cfg, today)

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

        results = []
        total_geral = Decimal("0.00")

        # Ordena do maior valor devido para o menor
        debts_sorted = sorted(
            debts,
            key=lambda r: (-r.total_overdue_amount, r.customer_name),
        )

        scored_records: list[tuple[float, Any]] = []
        for rec in debts_sorted:
            if rec.total_overdue_amount <= Decimal("0"):
                continue
            days_overdue = max(0, (today - rec.oldest_due_date).days)
            if not incluir_menores_30_dias and days_overdue <= cfg.overdue_min_days:
                continue
            score = _score_smart_query(cliente, rec.customer_name) if cliente else 1.0
            if cliente and score < 0.45:
                continue
            scored_records.append((score, rec))

        if cliente:
            scored_records.sort(
                key=lambda x: (-x[0], -x[1].total_overdue_amount, x[1].customer_name)
            )

        for _score, rec in scored_records:
            days_overdue = max(0, (today - rec.oldest_due_date).days)

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
        entries = _get_cached_stock_entries(cfg)

        scored_matched: list[tuple[float, dict[str, Any]]] = []
        for entry in entries:
            if not entry.active:
                continue
            score = _score_smart_query(produto, entry.product_name) if produto else 1.0
            if produto and score < 0.40:
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

            scored_matched.append(
                (
                    score,
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
                    },
                )
            )

        if produto:
            # Se houver matches fortes (>= 0.95), prioriza eles; senão retorna os parciais mais próximos
            strong = [x for x in scored_matched if x[0] >= 0.95]
            if strong:
                scored_matched = strong
            scored_matched.sort(
                key=lambda x: (-x[0], not x[1]["em_estoque_na_gondola"], x[1]["produto"])
            )

        matched = [x[1] for x in scored_matched]

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
