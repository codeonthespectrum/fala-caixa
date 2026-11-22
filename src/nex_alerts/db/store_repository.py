"""Repositório SQLite Multi-Tenant do Fala, Caixa!.

Gerencia lojas cadastradas, números de WhatsApp autorizados por loja (sócios/donos),
ciclo de vida de assinatura (trial de 14 dias, ativo, pausado, expirado), logs de disparo
e sincronização automática da allowlist do perfil `falacaixa` no Hermes Agent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
import os
from pathlib import Path
import re
import sqlite3
from typing import Iterable

from nex_alerts.config import AppConfig, _load_env_file
from nex_alerts.notification.dispatcher import normalize_phone_number

DEFAULT_HERMES_FALACAIXA_ENV = Path.home() / ".hermes" / "profiles" / "falacaixa" / ".env"
DEFAULT_ADMIN_BASE_PHONES = (
    "558491762882",  # Kim (pessoal)
    "558494635031",  # Kim (comercial)
    "5584920012483",  # Bot Fala, Caixa!
)


def phone_variants(phone: str) -> set[str]:
    """Gera variantes com e sem o 9º dígito brasileiro para comparação e allowlist."""
    digits = normalize_phone_number(phone)
    if not digits:
        return set()
    variants = {digits}
    if digits.startswith("55") and len(digits) == 13 and digits[4] == "9":
        variants.add(digits[:4] + digits[5:])
    elif digits.startswith("55") and len(digits) == 12:
        variants.add(digits[:4] + "9" + digits[4:])
    return variants


def _slugify(value: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return clean or "loja"


@dataclass(frozen=True)
class StoreUserRecord:
    """Número de WhatsApp autorizado a consultar e receber alertas de uma loja."""

    id: int
    store_id: int
    name: str
    phone: str
    role: str = "owner"
    created_at: str = ""


@dataclass(frozen=True)
class StoreRecord:
    """Cadastro completo de uma loja no modelo SaaS Multi-Tenant."""

    id: int
    slug: str
    store_name: str
    data_source_mode: str = "nex_web"
    nex_web_base_url: str = "https://web.nextar.com.br"
    nex_web_email: str = ""
    nex_web_password: str = ""
    nex_web_token: str = ""
    export_folder_path: str = "./dados_nex"
    pix_key: str = ""
    margin_threshold: Decimal = Decimal("0.30")
    whatsapp_notify_phone: str = ""
    schedule_time: str = "08:00"
    status: str = "trial"  # trial, active, paused, expired
    trial_ends_at: str = ""
    created_at: str = ""
    updated_at: str = ""
    users: tuple[StoreUserRecord, ...] = field(default_factory=tuple)

    def effective_status(self, reference_today: date | None = None) -> str:
        """Retorna o status efetivo considerando vencimento do período trial."""
        if self.status in {"paused", "expired", "active"}:
            return self.status
        if self.status == "trial" and self.trial_ends_at:
            today = reference_today or date.today()
            try:
                end_date = date.fromisoformat(self.trial_ends_at[:10])
                if today > end_date:
                    return "expired"
            except ValueError:
                pass
        return self.status

    def days_left_in_trial(self, reference_today: date | None = None) -> int | None:
        """Retorna quantos dias restam no trial, ou None se não estiver em trial."""
        if self.status != "trial" or not self.trial_ends_at:
            return None
        today = reference_today or date.today()
        try:
            end_date = date.fromisoformat(self.trial_ends_at[:10])
            return (end_date - today).days
        except ValueError:
            return None


class StoreRepository:
    """Persistência SQLite Multi-Loja do Fala, Caixa!."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS stores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    slug TEXT NOT NULL UNIQUE,
                    store_name TEXT NOT NULL,
                    data_source_mode TEXT NOT NULL DEFAULT 'nex_web',
                    nex_web_base_url TEXT NOT NULL DEFAULT 'https://web.nextar.com.br',
                    nex_web_email TEXT NOT NULL DEFAULT '',
                    nex_web_password TEXT NOT NULL DEFAULT '',
                    nex_web_token TEXT NOT NULL DEFAULT '',
                    export_folder_path TEXT NOT NULL DEFAULT './dados_nex',
                    pix_key TEXT NOT NULL DEFAULT '',
                    margin_threshold TEXT NOT NULL DEFAULT '0.30',
                    whatsapp_notify_phone TEXT NOT NULL DEFAULT '',
                    schedule_time TEXT NOT NULL DEFAULT '08:00',
                    status TEXT NOT NULL DEFAULT 'trial',
                    trial_ends_at TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS store_users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    store_id INTEGER NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
                    name TEXT NOT NULL DEFAULT '',
                    phone TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'owner',
                    created_at TEXT NOT NULL,
                    UNIQUE(store_id, phone)
                );

                CREATE TABLE IF NOT EXISTS dispatch_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    store_id INTEGER NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
                    dispatched_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT ''
                );
                """
            )

    def _load_users_map(self, conn: sqlite3.Connection) -> dict[int, list[StoreUserRecord]]:
        rows = conn.execute(
            "SELECT id, store_id, name, phone, role, created_at FROM store_users ORDER BY id ASC"
        ).fetchall()
        grouped: dict[int, list[StoreUserRecord]] = {}
        for row in rows:
            grouped.setdefault(row["store_id"], []).append(
                StoreUserRecord(
                    id=row["id"],
                    store_id=row["store_id"],
                    name=row["name"],
                    phone=row["phone"],
                    role=row["role"],
                    created_at=row["created_at"],
                )
            )
        return grouped

    @staticmethod
    def _row_to_store(row: sqlite3.Row, users: list[StoreUserRecord]) -> StoreRecord:
        return StoreRecord(
            id=row["id"],
            slug=row["slug"],
            store_name=row["store_name"],
            data_source_mode=row["data_source_mode"],
            nex_web_base_url=row["nex_web_base_url"],
            nex_web_email=row["nex_web_email"],
            nex_web_password=row["nex_web_password"],
            nex_web_token=row["nex_web_token"],
            export_folder_path=row["export_folder_path"],
            pix_key=row["pix_key"],
            margin_threshold=Decimal(str(row["margin_threshold"] or "0.30")),
            whatsapp_notify_phone=row["whatsapp_notify_phone"],
            schedule_time=row["schedule_time"],
            status=row["status"],
            trial_ends_at=row["trial_ends_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            users=tuple(users),
        )

    def list_stores(self) -> list[StoreRecord]:
        with self._connect() as conn:
            users_map = self._load_users_map(conn)
            rows = conn.execute("SELECT * FROM stores ORDER BY id ASC").fetchall()
            return [self._row_to_store(r, users_map.get(r["id"], [])) for r in rows]

    def get_store(self, store_id: int) -> StoreRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM stores WHERE id = ?", (store_id,)).fetchone()
            if row is None:
                return None
            users_map = self._load_users_map(conn)
            return self._row_to_store(row, users_map.get(store_id, []))

    def _unique_slug(self, conn: sqlite3.Connection, base_name: str) -> str:
        base = _slugify(base_name)
        candidate = base
        idx = 2
        while conn.execute("SELECT 1 FROM stores WHERE slug = ?", (candidate,)).fetchone():
            candidate = f"{base}-{idx}"
            idx += 1
        return candidate

    def create_store(
        self,
        *,
        store_name: str,
        data_source_mode: str = "nex_web",
        nex_web_base_url: str = "https://web.nextar.com.br",
        nex_web_email: str = "",
        nex_web_password: str = "",
        nex_web_token: str = "",
        export_folder_path: str = "./dados_nex",
        pix_key: str = "",
        margin_threshold: Decimal | str = Decimal("0.30"),
        whatsapp_notify_phone: str = "",
        schedule_time: str = "08:00",
        status: str = "trial",
        trial_days: int = 14,
        trial_ends_at: str | None = None,
        initial_users: Iterable[tuple[str, str, str]] = (),
    ) -> StoreRecord:
        """Cria uma nova loja (por padrão em trial de 14 dias) e seus números autorizados."""
        now_iso = datetime.now().isoformat(timespec="seconds")
        if trial_ends_at is None:
            trial_ends_at = (date.today() + timedelta(days=trial_days)).isoformat()
        notify_norm = normalize_phone_number(whatsapp_notify_phone)

        with self._connect() as conn:
            slug = self._unique_slug(conn, store_name)
            cur = conn.execute(
                """
                INSERT INTO stores (
                    slug, store_name, data_source_mode, nex_web_base_url,
                    nex_web_email, nex_web_password, nex_web_token, export_folder_path,
                    pix_key, margin_threshold, whatsapp_notify_phone, schedule_time,
                    status, trial_ends_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    slug,
                    store_name.strip(),
                    data_source_mode.strip().lower(),
                    nex_web_base_url.strip().rstrip("/") or "https://web.nextar.com.br",
                    nex_web_email.strip(),
                    nex_web_password.strip(),
                    nex_web_token.strip(),
                    export_folder_path.strip() or f"./dados_nex/{slug}",
                    pix_key.strip(),
                    str(margin_threshold),
                    notify_norm,
                    schedule_time.strip() or "08:00",
                    status.strip().lower(),
                    trial_ends_at,
                    now_iso,
                    now_iso,
                ),
            )
            store_id = int(cur.lastrowid)
            for name, phone, role in initial_users:
                norm = normalize_phone_number(phone)
                if not norm:
                    continue
                conn.execute(
                    """
                    INSERT OR IGNORE INTO store_users (store_id, name, phone, role, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (store_id, name.strip(), norm, role.strip() or "owner", now_iso),
                )
        created = self.get_store(store_id)
        assert created is not None
        return created

    def update_store(self, store_id: int, **fields: object) -> StoreRecord | None:
        allowed_cols = {
            "store_name",
            "data_source_mode",
            "nex_web_base_url",
            "nex_web_email",
            "nex_web_password",
            "nex_web_token",
            "export_folder_path",
            "pix_key",
            "margin_threshold",
            "whatsapp_notify_phone",
            "schedule_time",
            "status",
            "trial_ends_at",
        }
        updates: dict[str, object] = {}
        for k, v in fields.items():
            if k not in allowed_cols or v is None:
                continue
            if k == "whatsapp_notify_phone":
                updates[k] = normalize_phone_number(str(v))
            elif k == "margin_threshold":
                updates[k] = str(v)
            else:
                updates[k] = str(v).strip()
        if not updates:
            return self.get_store(store_id)
        updates["updated_at"] = datetime.now().isoformat(timespec="seconds")

        set_clause = ", ".join(f"{col} = ?" for col in updates)
        params = [*updates.values(), store_id]
        with self._connect() as conn:
            conn.execute(f"UPDATE stores SET {set_clause} WHERE id = ?", params)
        return self.get_store(store_id)

    def add_store_user(self, store_id: int, name: str, phone: str, role: str = "partner") -> bool:
        norm = normalize_phone_number(phone)
        if not norm:
            return False
        now_iso = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO store_users (store_id, name, phone, role, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (store_id, name.strip(), norm, role.strip() or "partner", now_iso),
            )
        return True

    def remove_store_user(self, user_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM store_users WHERE id = ?", (user_id,))

    def delete_store(self, store_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM stores WHERE id = ?", (store_id,))

    def add_dispatch_log(self, store_id: int, status: str, summary: str = "") -> None:
        now_iso = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO dispatch_logs (store_id, dispatched_at, status, summary) VALUES (?, ?, ?, ?)",
                (store_id, now_iso, status, summary[:1000]),
            )

    def list_dispatch_logs(self, limit: int = 30) -> list[dict[str, object]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT d.id, d.store_id, s.store_name, d.dispatched_at, d.status, d.summary
                FROM dispatch_logs d
                JOIN stores s ON s.id = d.store_id
                ORDER BY d.id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def resolve_store_for_phone(
        self,
        caller_candidates: set[str],
        reference_today: date | None = None,
    ) -> tuple[StoreRecord | None, str]:
        """Resolve a loja vinculada ao telefone do remetente e valida o status da assinatura."""
        if not caller_candidates:
            return None, "not_found"

        # Normaliza todos os candidatos incluindo variantes sem DDI 55 para match robusto
        expanded_candidates: set[str] = set()
        for c in caller_candidates:
            expanded_candidates |= phone_variants(c)
            digits = normalize_phone_number(c)
            if digits:
                expanded_candidates.add(digits)
                if digits.startswith("55") and len(digits) in (12, 13):
                    expanded_candidates.add(digits[2:])

        stores = self.list_stores()
        for store in stores:
            for user in store.users:
                user_vars = phone_variants(user.phone)
                for v in list(user_vars):
                    if v.startswith("55") and len(v) in (12, 13):
                        user_vars.add(v[2:])
                if expanded_candidates & user_vars:
                    eff = store.effective_status(reference_today=reference_today)
                    if eff in {"trial", "active"}:
                        return store, "ok"
                    return store, eff

            # Compatibilidade caso whatsapp_notify_phone esteja preenchido fora de store_users
            if store.whatsapp_notify_phone:
                notify_vars = phone_variants(store.whatsapp_notify_phone)
                if expanded_candidates & notify_vars:
                    eff = store.effective_status(reference_today=reference_today)
                    if eff in {"trial", "active"}:
                        return store, "ok"
                    return store, eff

        return None, "not_found"

    def list_eligible_stores_for_daily(self, reference_today: date | None = None) -> list[StoreRecord]:
        """Retorna todas as lojas com assinatura ativa ou trial dentro do prazo."""
        today = reference_today or date.today()
        return [
            st
            for st in self.list_stores()
            if st.effective_status(reference_today=today) in {"active", "trial"}
        ]

    @staticmethod
    def to_app_config(store: StoreRecord, base_config: AppConfig | None = None) -> AppConfig:
        """Converte um StoreRecord do banco em AppConfig imutável para o motor e coletores."""
        base = base_config or AppConfig()
        return AppConfig(
            store_name=store.store_name,
            pix_key=store.pix_key or base.pix_key,
            whatsapp_phone=store.whatsapp_notify_phone or base.whatsapp_phone,
            active=store.effective_status() in {"active", "trial"},
            margin_threshold=store.margin_threshold,
            lookback_hours=base.lookback_hours,
            price_rounding_step=base.price_rounding_step,
            overdue_min_days=base.overdue_min_days,
            cara_de_pau_max_days_since_visit=base.cara_de_pau_max_days_since_visit,
            sumido_min_days_since_visit=base.sumido_min_days_since_visit,
            bom_pagador_min_on_time_payments=base.bom_pagador_min_on_time_payments,
            data_source_mode=store.data_source_mode,
            export_folder_path=Path(store.export_folder_path),
            nex_web_base_url=store.nex_web_base_url,
            nex_web_token=store.nex_web_token,
            nex_web_email=store.nex_web_email,
            nex_web_password=store.nex_web_password,
            output_dir=base.output_dir,
            whatsapp_provider=base.whatsapp_provider,
            hermes_bridge_url=base.hermes_bridge_url,
            evolution_api_url=base.evolution_api_url,
            evolution_api_instance=base.evolution_api_instance,
            evolution_api_key=base.evolution_api_key,
            zapi_instance_url=base.zapi_instance_url,
            zapi_client_token=base.zapi_client_token,
            meta_phone_number_id=base.meta_phone_number_id,
            meta_access_token=base.meta_access_token,
            greenapi_id_instance=base.greenapi_id_instance,
            greenapi_api_token=base.greenapi_api_token,
            greenapi_api_url=base.greenapi_api_url,
            callmebot_api_key=base.callmebot_api_key,
            webhook_url=base.webhook_url,
            webhook_timeout_seconds=base.webhook_timeout_seconds,
        )

    def bootstrap_from_env_if_empty(self, env_path: Path) -> StoreRecord | None:
        """Importa a loja piloto do .env se o banco ainda estiver vazio (zero quebra no piloto)."""
        if self.list_stores() or not env_path.exists():
            return None

        _load_env_file(env_path, override=False)
        cfg = AppConfig.from_env(env_file=env_path)
        mode = cfg.data_source_mode
        if (cfg.nex_web_email or cfg.nex_web_token) and mode == "mock":
            mode = "nex_web"

        store_name = cfg.store_name if cfg.store_name != "Minimercado" else "Conveniencia Central"
        initial_users: list[tuple[str, str, str]] = [
            ("Kim Gomes", "558491762882", "admin"),
            ("Kim Comercial", "558494635031", "admin"),
        ]
        if cfg.whatsapp_phone:
            initial_users.append(("Bruno Coelho", cfg.whatsapp_phone, "owner"))
        if cfg.pix_key and normalize_phone_number(cfg.pix_key):
            initial_users.append(("Geyza Dantas", cfg.pix_key, "partner"))

        return self.create_store(
            store_name=store_name,
            data_source_mode=mode,
            nex_web_base_url=cfg.nex_web_base_url,
            nex_web_email=cfg.nex_web_email,
            nex_web_password=cfg.nex_web_password,
            nex_web_token=cfg.nex_web_token,
            export_folder_path=str(cfg.export_folder_path),
            pix_key=cfg.pix_key,
            margin_threshold=cfg.margin_threshold,
            whatsapp_notify_phone=cfg.whatsapp_phone,
            schedule_time="08:00",
            status="active",
            trial_days=365,
            initial_users=initial_users,
        )

    def collect_allowed_phones(self, extra_phones: Iterable[str] = DEFAULT_ADMIN_BASE_PHONES) -> list[str]:
        """Coleta todos os telefones autorizados (com e sem 9º dígito) para a allowlist do Hermes."""
        all_variants: set[str] = set()
        for phone in extra_phones:
            all_variants |= phone_variants(phone)
        for store in self.list_stores():
            for user in store.users:
                all_variants |= phone_variants(user.phone)
            if store.whatsapp_notify_phone:
                all_variants |= phone_variants(store.whatsapp_notify_phone)
        return sorted(all_variants)

    def sync_hermes_allowlist(self, hermes_env_path: Path = DEFAULT_HERMES_FALACAIXA_ENV) -> bool:
        """Atualiza WHATSAPP_ALLOWED_USERS no .env do perfil falacaixa sem tocar em outros campos."""
        if not hermes_env_path.exists():
            return False

        allowed_csv = ",".join(self.collect_allowed_phones())
        lines = hermes_env_path.read_text(encoding="utf-8").splitlines()
        new_lines: list[str] = []
        replaced_allowed = False
        replaced_allow_all = False

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("WHATSAPP_ALLOWED_USERS="):
                new_lines.append(f"WHATSAPP_ALLOWED_USERS={allowed_csv}")
                replaced_allowed = True
            elif stripped.startswith("WHATSAPP_ALLOW_ALL_USERS="):
                new_lines.append("WHATSAPP_ALLOW_ALL_USERS=false")
                replaced_allow_all = True
            else:
                new_lines.append(line)

        if not replaced_allow_all:
            new_lines.append("WHATSAPP_ALLOW_ALL_USERS=false")
        if not replaced_allowed:
            new_lines.append(f"WHATSAPP_ALLOWED_USERS={allowed_csv}")

        hermes_env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        return True
