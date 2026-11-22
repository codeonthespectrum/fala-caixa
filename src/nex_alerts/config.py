"""Configurações centralizadas do MargemViva (Single-Tenant e Multi-Loja até ~30 clientes)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
import json
import os
from pathlib import Path
from typing import Any


def _load_env_file(env_path: Path, override: bool = False) -> None:
    """Carrega um arquivo .env simples nativamente usando apenas a biblioteca padrão."""
    if not env_path.exists() or not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        if override or key not in os.environ:
            os.environ[key] = val


@dataclass(frozen=True)
class AppConfig:
    """Configuração imutável de uma loja ou do ambiente global do MargemViva."""

    store_name: str = "Minimercado"
    pix_key: str = "84999999999"
    whatsapp_phone: str = ""
    active: bool = True

    # Funcionalidade A: Margem Oculta
    margin_threshold: Decimal = Decimal("0.30")
    lookback_hours: int = 48
    price_rounding_step: Decimal = Decimal("0.10")

    # Funcionalidade B: Régua de Fiado
    overdue_min_days: int = 30
    cara_de_pau_max_days_since_visit: int = 15
    sumido_min_days_since_visit: int = 30
    bom_pagador_min_on_time_payments: int = 3

    # Coleta de dados
    data_source_mode: str = "mock"  # "mock", "folder", "nex_web"
    export_folder_path: Path = Path("./dados_nex")

    # Nex na Web (Somente Leitura)
    nex_web_base_url: str = "https://web.nextar.com.br"
    nex_web_token: str = ""
    nex_web_email: str = ""
    nex_web_password: str = ""

    # Saída e Notificação WhatsApp (Multi-Provedor Nativo)
    output_dir: Path = Path("./output/alertas_enviados")
    whatsapp_provider: str = "auto"  # "auto", "hermes", "greenapi", "evolution", "zapi", "meta", "callmebot", "webhook", "none"

    # 0. Hermes Agent Local WhatsApp Bridge (Perfil isolado falacaixa na porta 3001)
    hermes_bridge_url: str = "http://127.0.0.1:3001"

    # 1. Evolution API
    evolution_api_url: str = ""
    evolution_api_instance: str = ""
    evolution_api_key: str = ""

    # 2. Z-API
    zapi_instance_url: str = ""  # Ex: https://api.z-api.io/instances/SUA_INSTANCIA/token/SEU_TOKEN
    zapi_client_token: str = ""

    # 3. WhatsApp Cloud API (Oficial Meta)
    meta_phone_number_id: str = ""
    meta_access_token: str = ""

    # 4. Green-API (Plano Developer Gratuito permanente para até 3 números)
    greenapi_id_instance: str = ""
    greenapi_api_token: str = ""
    greenapi_api_url: str = "https://api.green-api.com"

    # 5. CallMeBot
    callmebot_api_key: str = ""

    # 6. Webhook HTTP Genérico (n8n, Make, Zapier)
    webhook_url: str = ""
    webhook_timeout_seconds: int = 15

    @classmethod
    def from_env(cls, env_file: str | Path | None = None) -> AppConfig:
        """Carrega configurações a partir de variáveis de ambiente e arquivo .env opcional."""
        if env_file:
            _load_env_file(Path(env_file), override=True)
        else:
            _load_env_file(Path(".env"), override=False)

        return cls(
            store_name=os.getenv("STORE_NAME", "Minimercado"),
            pix_key=os.getenv("PIX_KEY", "84999999999"),
            whatsapp_phone=os.getenv("WHATSAPP_PHONE", "").strip(),
            active=os.getenv("STORE_ACTIVE", "true").strip().lower()
            not in {"false", "0", "no", "nao"},
            margin_threshold=Decimal(os.getenv("MARGIN_THRESHOLD", "0.30")),
            lookback_hours=int(os.getenv("LOOKBACK_HOURS", "48")),
            price_rounding_step=Decimal(os.getenv("PRICE_ROUNDING_STEP", "0.10")),
            overdue_min_days=int(os.getenv("OVERDUE_MIN_DAYS", "30")),
            cara_de_pau_max_days_since_visit=int(
                os.getenv("CARA_DE_PAU_MAX_DAYS_SINCE_VISIT", "15")
            ),
            sumido_min_days_since_visit=int(
                os.getenv("SUMIDO_MIN_DAYS_SINCE_VISIT", "30")
            ),
            bom_pagador_min_on_time_payments=int(
                os.getenv("BOM_PAGADOR_MIN_ON_TIME_PAYMENTS", "3")
            ),
            data_source_mode=os.getenv("DATA_SOURCE_MODE", "mock").strip().lower(),
            export_folder_path=Path(os.getenv("EXPORT_FOLDER_PATH", "./dados_nex")),
            nex_web_base_url=os.getenv(
                "NEX_WEB_BASE_URL", "https://web.nextar.com.br"
            ).rstrip("/"),
            nex_web_token=os.getenv("NEX_WEB_TOKEN", "").strip(),
            nex_web_email=os.getenv("NEX_WEB_EMAIL", "").strip(),
            nex_web_password=os.getenv("NEX_WEB_PASSWORD", "").strip(),
            output_dir=Path(os.getenv("OUTPUT_DIR", "./output/alertas_enviados")),
            whatsapp_provider=os.getenv("WHATSAPP_PROVIDER", "auto").strip().lower(),
            hermes_bridge_url=os.getenv(
                "HERMES_BRIDGE_URL", "http://127.0.0.1:3001"
            ).strip().rstrip("/"),
            evolution_api_url=os.getenv("EVOLUTION_API_URL", "").strip().rstrip("/"),
            evolution_api_instance=os.getenv("EVOLUTION_API_INSTANCE", "").strip(),
            evolution_api_key=os.getenv("EVOLUTION_API_KEY", "").strip(),
            zapi_instance_url=os.getenv("ZAPI_INSTANCE_URL", "").strip().rstrip("/"),
            zapi_client_token=os.getenv("ZAPI_CLIENT_TOKEN", "").strip(),
            meta_phone_number_id=os.getenv("META_PHONE_NUMBER_ID", "").strip(),
            meta_access_token=os.getenv("META_ACCESS_TOKEN", "").strip(),
            greenapi_id_instance=os.getenv("GREENAPI_ID_INSTANCE", "").strip(),
            greenapi_api_token=os.getenv("GREENAPI_API_TOKEN", "").strip(),
            greenapi_api_url=os.getenv(
                "GREENAPI_API_URL", "https://api.green-api.com"
            ).strip().rstrip("/"),
            callmebot_api_key=os.getenv("CALLMEBOT_API_KEY", "").strip(),
            webhook_url=os.getenv("WEBHOOK_URL", "").strip(),
            webhook_timeout_seconds=int(os.getenv("WEBHOOK_TIMEOUT_SECONDS", "15")),
        )


def load_multi_store_configs(
    base_config: AppConfig,
    stores_json_str: str | None = None,
    stores_file_path: Path | None = None,
) -> list[AppConfig]:
    """Retorna a lista de lojas ativas a partir de STORES_CONFIG_JSON, arquivo JSON ou loja única padrão."""
    raw_json = (
        stores_json_str
        if stores_json_str is not None
        else os.getenv("STORES_CONFIG_JSON", "").strip()
    )
    if not raw_json and stores_file_path and stores_file_path.exists():
        raw_json = stores_file_path.read_text(encoding="utf-8").strip()

    if not raw_json:
        return [base_config] if base_config.active else []

    parsed: Any = json.loads(raw_json)
    if isinstance(parsed, dict):
        parsed = parsed.get("stores", [parsed])
    if not isinstance(parsed, list):
        raise ValueError("STORES_CONFIG_JSON deve ser uma lista JSON de objetos de loja.")

    configs: list[AppConfig] = []
    for idx, item in enumerate(parsed, start=1):
        if not isinstance(item, dict):
            continue
        is_active = bool(item.get("active", True))
        if not is_active:
            continue

        store_name = str(item.get("store_name") or f"Loja {idx}").strip()
        pix_key = str(item.get("pix_key") or base_config.pix_key).strip()
        whatsapp_phone = str(
            item.get("whatsapp_phone") or base_config.whatsapp_phone
        ).strip()
        mode = (
            str(item.get("mode") or item.get("data_source_mode") or "nex_web")
            .strip()
            .lower()
        )
        nex_email = str(item.get("nex_email") or item.get("nex_web_email") or "").strip()
        nex_password = str(
            item.get("nex_password") or item.get("nex_web_password") or ""
        ).strip()
        nex_token = str(item.get("nex_token") or item.get("nex_web_token") or "").strip()
        webhook_url = str(item.get("webhook_url") or base_config.webhook_url).strip()
        greenapi_id = str(
            item.get("greenapi_id_instance") or base_config.greenapi_id_instance
        ).strip()
        greenapi_token = str(
            item.get("greenapi_api_token") or base_config.greenapi_api_token
        ).strip()
        callmebot_key = str(
            item.get("callmebot_api_key") or base_config.callmebot_api_key
        ).strip()
        whatsapp_provider = str(
            item.get("whatsapp_provider") or base_config.whatsapp_provider
        ).strip().lower()
        hermes_bridge_url = str(
            item.get("hermes_bridge_url") or base_config.hermes_bridge_url
        ).strip().rstrip("/")
        margin_threshold = Decimal(
            str(item.get("margin_threshold", base_config.margin_threshold))
        )

        store_cfg = replace(
            base_config,
            store_name=store_name,
            pix_key=pix_key,
            whatsapp_phone=whatsapp_phone,
            active=True,
            data_source_mode=mode,
            nex_web_email=nex_email,
            nex_web_password=nex_password,
            nex_web_token=nex_token,
            webhook_url=webhook_url,
            greenapi_id_instance=greenapi_id,
            greenapi_api_token=greenapi_token,
            callmebot_api_key=callmebot_key,
            whatsapp_provider=whatsapp_provider,
            hermes_bridge_url=hermes_bridge_url,
            margin_threshold=margin_threshold,
        )
        configs.append(store_cfg)

    return configs
