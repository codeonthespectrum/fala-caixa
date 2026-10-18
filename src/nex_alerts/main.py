"""Ponto de entrada principal (CLI e Orquestrador Multi-Loja) do MargemViva."""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime
from pathlib import Path
import sys

from nex_alerts.collectors.base import BaseNexCollector
from nex_alerts.collectors.folder_collector import FolderNexCollector
from nex_alerts.collectors.mock_collector import MockNexCollector
from nex_alerts.collectors.nex_web_collector import NexWebReadOnlyCollector
from nex_alerts.config import AppConfig, load_multi_store_configs
from nex_alerts.domain.models import DailyDigestReport
from nex_alerts.engine.fiado_analyzer import analyze_fiado_debts
from nex_alerts.engine.margin_analyzer import analyze_margin_entries
from nex_alerts.notification.dispatcher import DispatchResult, NotificationDispatcher


def build_collector(config: AppConfig) -> BaseNexCollector:
    """Instancia o coletor apropriado de acordo com `config.data_source_mode`."""
    mode = config.data_source_mode.lower()
    if mode == "mock":
        return MockNexCollector()
    if mode == "folder":
        return FolderNexCollector(folder_path=config.export_folder_path)
    if mode == "nex_web":
        return NexWebReadOnlyCollector(
            base_url=config.nex_web_base_url,
            token=config.nex_web_token,
            email=config.nex_web_email,
            password=config.nex_web_password,
        )
    raise ValueError(
        f"Modo de fonte de dados inválido: '{config.data_source_mode}'. "
        "Use 'mock', 'folder' ou 'nex_web'."
    )


def run_pipeline(
    config: AppConfig,
    collector: BaseNexCollector | None = None,
    reference_now: datetime | None = None,
    print_to_stdout: bool = True,
) -> DispatchResult:
    """Executa o fluxo completo Read-Only para uma loja: Coleta -> Regras A & B -> Formatação -> Disparo."""
    now = reference_now or datetime.now()
    active_collector = collector or build_collector(config)

    stock_entries = active_collector.fetch_recent_stock_entries(reference_now=now)
    customer_debts = active_collector.fetch_customer_debts(reference_today=now.date())

    # Se o coletor NexWeb descobriu o nome oficial da loja e a config estava com o nome padrão, usa o da loja
    effective_store_name = config.store_name
    if (
        isinstance(active_collector, NexWebReadOnlyCollector)
        and active_collector.store_name
        and config.store_name in {"Minimercado", "Minimercado Exemplo", ""}
    ):
        effective_store_name = active_collector.store_name

    margin_alerts = analyze_margin_entries(
        entries=stock_entries,
        reference_now=now,
        lookback_hours=config.lookback_hours,
        margin_threshold=config.margin_threshold,
        rounding_step=config.price_rounding_step,
    )

    fiado_alerts = analyze_fiado_debts(
        records=customer_debts,
        pix_key=config.pix_key,
        reference_today=now.date(),
        overdue_min_days=config.overdue_min_days,
        cara_de_pau_max_days_since_visit=config.cara_de_pau_max_days_since_visit,
        sumido_min_days_since_visit=config.sumido_min_days_since_visit,
        bom_pagador_min_on_time_payments=config.bom_pagador_min_on_time_payments,
    )

    report = DailyDigestReport(
        generated_at=now,
        store_name=effective_store_name,
        pix_key=config.pix_key,
        whatsapp_phone=config.whatsapp_phone,
        margin_alerts=margin_alerts,
        fiado_alerts=fiado_alerts,
    )

    dispatcher = NotificationDispatcher(
        output_dir=config.output_dir,
        webhook_url=config.webhook_url,
        webhook_timeout_seconds=config.webhook_timeout_seconds,
        print_to_stdout=print_to_stdout,
    )
    return dispatcher.dispatch(report)


def run_multi_store_batch(
    store_configs: list[AppConfig],
    reference_now: datetime | None = None,
    print_to_stdout: bool = True,
) -> tuple[list[DispatchResult], list[tuple[str, str]]]:
    """Executa o MargemViva em lote para todas as lojas ativas com isolamento de falhas."""
    results: list[DispatchResult] = []
    errors: list[tuple[str, str]] = []

    total = len(store_configs)
    for idx, store_cfg in enumerate(store_configs, start=1):
        if print_to_stdout and total > 1:
            print(f"\n>>> [{idx}/{total}] Processando loja: {store_cfg.store_name}...")
        try:
            res = run_pipeline(
                config=store_cfg,
                reference_now=reference_now,
                print_to_stdout=print_to_stdout,
            )
            results.append(res)
            if print_to_stdout:
                print(
                    f"[OK] ({store_cfg.store_name}) Alerta salvo em: {res.saved_file_path}"
                )
        except Exception as exc:
            err_msg = str(exc)
            errors.append((store_cfg.store_name, err_msg))
            print(
                f"[ERRO] Falha ao processar loja '{store_cfg.store_name}': {err_msg}",
                file=sys.stderr,
            )

    return results, errors


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="MargemViva — Sistema de Alertas Inteligentes Read-Only para Nex (Nextar)"
    )
    parser.add_argument(
        "--mode",
        choices=["mock", "folder", "nex_web"],
        help="Sobrescreve DATA_SOURCE_MODE (mock, folder ou nex_web)",
    )
    parser.add_argument(
        "--path",
        type=Path,
        help="Caminho da pasta com planilhas exportadas do Nex (.xls/.xlsx/.csv)",
    )
    parser.add_argument(
        "--pix-key",
        type=str,
        help="Chave PIX a ser exibida nos alertas de Nível 1 (Cliente Sumido)",
    )
    parser.add_argument(
        "--webhook-url",
        type=str,
        help="URL de Webhook HTTP opcional para envio do payload JSON",
    )
    parser.add_argument(
        "--stores-file",
        type=Path,
        help="Caminho para arquivo JSON com múltiplas lojas (alternativa a STORES_CONFIG_JSON)",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Caminho para arquivo .env customizado",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    base_config = AppConfig.from_env(env_file=args.env_file)

    if args.mode:
        base_config = replace(base_config, data_source_mode=args.mode)
    if args.path:
        base_config = replace(base_config, export_folder_path=args.path)
    if args.pix_key:
        base_config = replace(base_config, pix_key=args.pix_key)
    if args.webhook_url:
        base_config = replace(base_config, webhook_url=args.webhook_url)

    store_configs = load_multi_store_configs(
        base_config=base_config,
        stores_file_path=args.stores_file,
    )
    if not store_configs:
        print("[AVISO] Nenhuma loja ativa encontrada para processamento.")
        return 0

    results, errors = run_multi_store_batch(store_configs=store_configs)

    if len(store_configs) > 1:
        print(
            f"\n[RESUMO LOTE MARGEMVIVA] Lojas processadas com sucesso: {len(results)}/{len(store_configs)} | Falhas: {len(errors)}"
        )

    # Retorna código de erro apenas se TODAS as lojas falharem
    if errors and not results:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
