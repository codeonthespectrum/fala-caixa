"""Orquestrador diário Multi-Loja (08h00) do Fala, Caixa!.

Lê todas as lojas ativas ou em trial válido do SQLite (`data/falacaixa.db`),
executa o pipeline 100% Read-Only (Margem + Estoque Baixo + Fiado +30 dias),
envia pelo bridge do perfil `falacaixa` (porta 3001) e grava histórico em `dispatch_logs`.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from nex_alerts.config import AppConfig, _load_env_file
from nex_alerts.db.store_repository import StoreRepository
from nex_alerts.main import run_pipeline


def run_daily_for_all_stores(
    repo_root: Path = REPO_ROOT,
    hermes_bridge_url: str = "http://127.0.0.1:3001",
    whatsapp_provider: str = "hermes",
) -> int:
    env_path = repo_root / ".env"
    if env_path.exists():
        _load_env_file(env_path, override=False)

    base_cfg = AppConfig.from_env(env_file=env_path if env_path.exists() else None)
    db_path = repo_root / "data" / "falacaixa.db"
    repo = StoreRepository(db_path)
    repo.bootstrap_from_env_if_empty(env_path)

    eligible_stores = repo.list_eligible_stores_for_daily()
    if not eligible_stores:
        print("Nenhuma loja ativa ou em trial válido para disparo hoje.")
        return 0

    failures = 0
    for store in eligible_stores:
        cfg = repo.to_app_config(store, base_config=base_cfg)
        cfg = replace(
            cfg,
            data_source_mode=(
                "nex_web"
                if (cfg.nex_web_email or cfg.nex_web_token) and cfg.data_source_mode == "mock"
                else cfg.data_source_mode
            ),
            whatsapp_provider=whatsapp_provider,
            hermes_bridge_url=hermes_bridge_url,
            output_dir=repo_root / "output" / "alertas_enviados",
        )
        try:
            res = run_pipeline(config=cfg, print_to_stdout=False)
            status = "ok" if res.webhook_sent or whatsapp_provider == "none" else "error"
            summary = f"Enviado via {res.provider_used} para {cfg.whatsapp_phone}" if status == "ok" else (res.webhook_error or "Falha no envio")
            repo.add_dispatch_log(store.id, status, summary)
            if status != "ok":
                failures += 1
                print(f"[ERRO] {store.store_name}: {summary}")
            else:
                print(f"[OK] {store.store_name}: {summary}")
        except Exception as exc:
            failures += 1
            repo.add_dispatch_log(store.id, "error", str(exc))
            print(f"[ERRO] {store.store_name}: {exc}")

    return 1 if failures == len(eligible_stores) else 0


if __name__ == "__main__":
    raise SystemExit(run_daily_for_all_stores())
