"""Conector Read-Only para o Nex na Web (web.nextar.com.br).

Segurança Arquitetural:
- Este cliente possui uma trava explícita (`ReadOnlyViolationError`) que proíbe qualquer
  chamada de mutação de dados (PUT, PATCH, DELETE, ou POST de criação/edição).
- Apenas GET e os endpoints oficiais de leitura/autenticação da API Nextar são permitidos:
  - Auth (POST): https://api.auth.nextar.com.br/api/v2/authenticate
  - Produtos (GET): https://api.web.nextar.com.br/api/v1/product/{shopCode}
  - Transações de Estoque / Entradas (POST de consulta paginada): /stock/{shopCode}/product-transactions
  - Clientes / Débitos (GET): https://api.web.nextar.com.br/api/v1/customer/{shopCode}
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
import json
import os
from typing import Any, Callable
import urllib.error
import urllib.parse
import urllib.request

from nex_alerts.collectors.base import BaseNexCollector, ReadOnlyViolationError
from nex_alerts.collectors.folder_collector import (
    parse_brl_decimal,
    parse_flexible_date,
    parse_flexible_datetime,
)
from nex_alerts.domain.models import CustomerDebtRecord, StockEntry


ALLOWED_POST_READ_PATTERNS = (
    "/authenticate",
    "/api/login",
    "/api/auth",
    "/auth/login",
    "/login",
    "/product-transactions",
    "/sale/historic",
    "/historic",
)

def _nex_basic_auth_header() -> str:
    """Credencial de cliente da API Nextar (Basic auth), lida do ambiente.

    O valor NUNCA fica no código-fonte: configure NEX_WEB_BASIC_AUTH no .env
    (credencial Basic no formato base64 fornecida pela integração Nextar).
    """
    value = os.environ.get("NEX_WEB_BASIC_AUTH", "").strip()
    if not value:
        raise ValueError(
            "Para autenticar no Nex Web com e-mail/senha, defina NEX_WEB_BASIC_AUTH "
            "no arquivo .env (credencial Basic da integração Nextar)."
        )
    return value


def _strip_tz(dt: datetime | None) -> datetime | None:
    """Converte datetime timezone-aware para hora local naive para comparação uniforme."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone().replace(tzinfo=None)
    return dt


def parse_iso_or_flexible_datetime(
    value: Any, fallback: datetime | None = None
) -> datetime | None:
    """Converte timestamps ISO 8601 (com Z ou offset) ou formatos brasileiros para datetime naive local."""
    if value is None or value == "":
        return fallback
    if isinstance(value, datetime):
        return _strip_tz(value)
    text = str(value).strip()
    try:
        iso_normalized = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso_normalized)
        return _strip_tz(dt)
    except ValueError:
        return parse_flexible_datetime(text, fallback=fallback)


class NexWebReadOnlyCollector(BaseNexCollector):
    """Coletor Read-Only para sincronização com a conta Nex na Web."""

    def __init__(
        self,
        base_url: str = "https://api.web.nextar.com.br/api/v1",
        auth_base_url: str = "https://api.auth.nextar.com.br/api/v2",
        token: str = "",
        email: str = "",
        password: str = "",
        shop_code: str = "",
        timeout_seconds: int = 20,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        if "web.nextar.com.br" in base_url and "api.web.nextar.com.br" not in base_url:
            self.base_url = "https://api.web.nextar.com.br/api/v1"
        else:
            self.base_url = base_url.rstrip("/")

        self.auth_base_url = auth_base_url.rstrip("/")
        self.token = token
        self.email = email
        self.password = password
        self.shop_code = shop_code
        self.store_name = ""
        self.timeout_seconds = timeout_seconds
        self.opener = opener or urllib.request.urlopen
        self.headers: dict[str, str] = {
            "Accept": "application/json",
            "Accept-Language": "pt-BR",
            "User-Agent": "Mozilla/5.0 (NexAlertsReadOnly/0.1)",
        }
        if self.token:
            clean_tok = (
                self.token
                if self.token.startswith("Bearer ")
                else f"Bearer {self.token}"
            )
            self.headers["Authorization"] = clean_tok

    def _is_allowed_readonly_post(self, path: str) -> bool:
        """Verifica se um POST é estritamente de autenticação ou listagem paginada de leitura."""
        for pattern in ALLOWED_POST_READ_PATTERNS:
            if path == pattern or path.endswith(pattern):
                return True
        if "/transactions/" in path and path.endswith("/customer"):
            return True
        return False

    def _safe_request(
        self,
        method: str,
        endpoint_path: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        custom_base_url: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> Any:
        """Executa requisição HTTP garantindo que apenas operações Read-Only sejam permitidas."""
        upper_method = method.strip().upper()
        normalized_path = "/" + endpoint_path.lstrip("/")

        if upper_method != "GET":
            if not (
                upper_method == "POST"
                and self._is_allowed_readonly_post(normalized_path)
            ):
                raise ReadOnlyViolationError(
                    f"Operação bloqueada por política Read-Only: método '{upper_method}' "
                    f"não é permitido no endpoint '{normalized_path}'."
                )

        target_base = (custom_base_url or self.base_url).rstrip("/")
        url = f"{target_base}{normalized_path}"
        if params:
            query_str = urllib.parse.urlencode(params)
            url = f"{url}?{query_str}"

        req_headers = dict(self.headers)
        if extra_headers:
            req_headers.update(extra_headers)

        body_bytes: bytes | None = None
        if json_body is not None:
            req_headers["Content-Type"] = "application/json"
            body_bytes = json.dumps(json_body).encode("utf-8")

        req = urllib.request.Request(
            url=url,
            data=body_bytes,
            headers=req_headers,
            method=upper_method,
        )
        with self.opener(req, timeout=self.timeout_seconds) as response:
            raw_data = response.read().decode("utf-8")
            return json.loads(raw_data) if raw_data else {}

    def authenticate_if_needed(self) -> None:
        """Autentica na API v2 do Nextar (`POST /authenticate`) para obter JWT e `shopCode`."""
        if self.token and self.shop_code:
            return
        if not (self.email and self.password) and not self.token:
            raise ValueError(
                "Para usar o modo 'nex_web', informe NEX_WEB_TOKEN ou (NEX_WEB_EMAIL e NEX_WEB_PASSWORD)."
            )

        if self.email and self.password and not self.token:
            auth_headers = {
                "Authorization": f"Basic {_nex_basic_auth_header()}",
                "X-Requested-With": "XMLHttpRequest",
            }
            payload_candidates = [
                {"username": self.email, "password": self.password, "origin": 3},
                {"email": self.email, "password": self.password, "origin": 3},
            ]
            last_error: Exception | None = None
            data: dict[str, Any] = {}
            for body in payload_candidates:
                try:
                    resp = self._safe_request(
                        method="POST",
                        endpoint_path="/authenticate",
                        json_body=body,
                        custom_base_url=self.auth_base_url,
                        extra_headers=auth_headers,
                    )
                    if isinstance(resp, dict):
                        data = resp.get("data", resp) if "token" not in resp else resp
                        if data.get("token"):
                            break
                except Exception as exc:
                    last_error = exc

            if not data.get("token") and last_error is not None:
                raise last_error

            token = data.get("token") or data.get("access_token") or ""
            if token:
                self.token = str(token)
                self.headers["Authorization"] = f"Bearer {self.token}"

            user_creds = data.get("userCredentials") or {}
            if isinstance(user_creds, dict):
                sc = user_creds.get("shopCode") or user_creds.get("shopcode")
                if sc is not None:
                    self.shop_code = str(sc)
                sn = user_creds.get("store_name")
                if sn:
                    self.store_name = str(sn)

    def fetch_raw_products(self) -> list[dict[str, Any]]:
        """Retorna a lista completa de produtos ativos da loja no Nex Web (com paginação automática)."""
        self.authenticate_if_needed()
        if not self.shop_code:
            return self._fetch_legacy_products()

        all_items: list[dict[str, Any]] = []
        page = 0
        max_pages = 15
        while page < max_pages:
            payload = self._safe_request(
                "GET",
                f"/product/{self.shop_code}",
                params={
                    "page": page,
                    "size": 200,
                    "sort": "description",
                    "direction": "asc",
                    "filter": "active",
                },
            )
            batch: list[dict[str, Any]] = []
            total_pages = 1
            if isinstance(payload, dict):
                total_pages = int(payload.get("totalPages") or 1)
                content = payload.get("content")
                if isinstance(content, list):
                    batch = content
                else:
                    data_inner = payload.get("data")
                    if isinstance(data_inner, dict) and isinstance(
                        data_inner.get("content"), list
                    ):
                        batch = data_inner["content"]
                    else:
                        items = payload.get("items")
                        if isinstance(items, list):
                            batch = items
            elif isinstance(payload, list):
                batch = payload

            if not batch:
                break
            all_items.extend(batch)
            page += 1
            if page >= total_pages:
                break

        return all_items

    def _fetch_legacy_products(self) -> list[dict[str, Any]]:
        payload = self._safe_request("GET", "/api/products")
        items = payload.get("items", payload) if isinstance(payload, dict) else payload
        return items if isinstance(items, list) else []

    def fetch_raw_stock_transactions(self) -> list[dict[str, Any]]:
        """Retorna o histórico de movimentações de estoque e vendas da loja."""
        self.authenticate_if_needed()
        if not self.shop_code:
            return []
        try:
            payload = self._safe_request(
                "POST",
                f"/stock/{self.shop_code}/product-transactions",
                params={"size": 100, "sort": "description", "direction": "desc", "page": 0},
                json_body={"canceled": False, "display_sales": True},
            )
            if isinstance(payload, dict):
                tx_obj = payload.get("transactions") or {}
                if isinstance(tx_obj, dict) and isinstance(tx_obj.get("content"), list):
                    return tx_obj["content"]
        except Exception:
            return []
        return []

    def fetch_raw_customers(self) -> list[dict[str, Any]]:
        """Retorna a lista bruta de clientes da loja no Nex Web."""
        self.authenticate_if_needed()
        if not self.shop_code:
            payload = self._safe_request("GET", "/api/customers/debts")
            items = payload.get("items", payload) if isinstance(payload, dict) else payload
            return items if isinstance(items, list) else []

        payload = self._safe_request(
            "GET",
            f"/customer/{self.shop_code}",
            params={
                "page": 0,
                "size": 200,
                "sort": "name",
                "direction": "asc",
                "filter": "active",
                "stats": "true",
            },
        )
        if isinstance(payload, dict):
            content = payload.get("content")
            if isinstance(content, list):
                return content
            data_inner = payload.get("data")
            if isinstance(data_inner, dict) and isinstance(
                data_inner.get("content"), list
            ):
                return data_inner["content"]
            items = payload.get("items")
            if isinstance(items, list):
                return items
        return payload if isinstance(payload, list) else []

    def fetch_recent_stock_entries(
        self, reference_now: datetime | None = None
    ) -> list[StockEntry]:
        """Busca produtos e entradas recentes via API do Nex Web e converte para StockEntry."""
        now = reference_now or datetime.now()
        items = self.fetch_raw_products()
        transactions = self.fetch_raw_stock_transactions()

        # Mapeia a data da última entrada ("inbound") por UID de produto, caso exista no histórico
        inbound_dates_by_uid: dict[str, datetime] = {}
        for tx in transactions:
            if not isinstance(tx, dict) or tx.get("canceled"):
                continue
            entry_type = str(tx.get("entry_type") or "").lower()
            tx_dt = parse_iso_or_flexible_datetime(tx.get("date"))
            if entry_type == "inbound" and tx_dt is not None:
                for p in tx.get("products") or []:
                    p_uid = str(p.get("uid") or "")
                    if p_uid and (
                        p_uid not in inbound_dates_by_uid
                        or tx_dt > inbound_dates_by_uid[p_uid]
                    ):
                        inbound_dates_by_uid[p_uid] = tx_dt

        entries: list[StockEntry] = []
        for idx, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                continue
            uid = str(item.get("uid") or "")
            prod_id = str(
                item.get("code") or item.get("id") or uid or f"WEB-{idx:04d}"
            )
            name = str(item.get("description") or item.get("name") or "").strip()
            if not name:
                continue
            cost = parse_brl_decimal(item.get("cost_price") or item.get("cost"))
            sale = parse_brl_decimal(
                item.get("price") or item.get("unit_price") or item.get("sale_price")
            )
            entry_dt = inbound_dates_by_uid.get(uid) or parse_iso_or_flexible_datetime(
                item.get("last_entry_date")
                or item.get("updated_at")
                or item.get("included_on"),
                fallback=now,
            )
            if cost > Decimal("0") and entry_dt is not None:
                is_active = bool(item.get("active", True)) and not bool(
                    item.get("inactive", False)
                )
                has_stock_flag = bool(item.get("has_stock", True))
                stock_ctrl_flag = bool(item.get("stock_control", True))
                raw_cur_stock = item.get("current_stock")
                if raw_cur_stock is None:
                    raw_cur_stock = item.get("stock")
                cur_stock_dec = (
                    parse_brl_decimal(raw_cur_stock)
                    if raw_cur_stock is not None and raw_cur_stock != ""
                    else None
                )
                raw_min_stock = item.get("stockmin")
                if raw_min_stock is None:
                    raw_min_stock = item.get("min_stock")
                min_stock_dec = (
                    parse_brl_decimal(raw_min_stock)
                    if raw_min_stock is not None and raw_min_stock != ""
                    else None
                )
                # No Nex, produtos com estoque zerado/negativo ou sem controle de estoque ativo
                # não estão fisicamente em giro na prateleira.
                effective_has_stock = has_stock_flag and (
                    cur_stock_dec is None or cur_stock_dec > Decimal("0")
                )

                entries.append(
                    StockEntry(
                        product_id=prod_id,
                        product_name=name,
                        entry_date=entry_dt,
                        cost_price=cost,
                        sale_price=sale,
                        current_stock=cur_stock_dec,
                        min_stock=min_stock_dec,
                        active=is_active,
                        has_stock=effective_has_stock,
                        stock_control=stock_ctrl_flag,
                    )
                )
        return entries

    def fetch_customer_debts(
        self, reference_today: date | None = None
    ) -> list[CustomerDebtRecord]:
        """Busca clientes, saldos devedores (`debit_value`) e histórico de compras/dívidas no Nex Web."""
        today = reference_today or date.today()
        items = self.fetch_raw_customers()
        transactions = self.fetch_raw_stock_transactions()

        # Analisa transações para encontrar a data de vencimento/dívida mais antiga e contagem de pagamentos em dia
        oldest_debt_date_by_cust: dict[str, date] = {}
        last_purchase_by_cust: dict[str, date] = {}
        paid_tx_count_by_cust: dict[str, int] = {}

        for tx in transactions:
            if not isinstance(tx, dict) or tx.get("canceled"):
                continue
            cust_obj = tx.get("customer") or {}
            c_uid = str(cust_obj.get("uid") or "") if isinstance(cust_obj, dict) else ""
            if not c_uid:
                continue
            tx_dt = parse_iso_or_flexible_datetime(tx.get("date"))
            if tx_dt is None:
                continue
            tx_date = tx_dt.date()

            # Atualiza última compra
            if str(tx.get("entry_type") or "").lower() == "outbound":
                if (
                    c_uid not in last_purchase_by_cust
                    or tx_date > last_purchase_by_cust[c_uid]
                ):
                    last_purchase_by_cust[c_uid] = tx_date

            debt_val = parse_brl_decimal(tx.get("debt"))
            paid_val = parse_brl_decimal(tx.get("paid"))
            if debt_val > Decimal("0"):
                if (
                    c_uid not in oldest_debt_date_by_cust
                    or tx_date < oldest_debt_date_by_cust[c_uid]
                ):
                    oldest_debt_date_by_cust[c_uid] = tx_date
            elif paid_val > Decimal("0"):
                paid_tx_count_by_cust[c_uid] = paid_tx_count_by_cust.get(c_uid, 0) + 1

        records: list[CustomerDebtRecord] = []
        for idx, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                continue
            uid = str(item.get("uid") or "")
            cust_id = str(
                item.get("code") or item.get("id") or uid or f"CLI-{idx:04d}"
            )
            name = str(item.get("name") or item.get("customer_name") or "").strip()
            amount = parse_brl_decimal(
                item.get("debit_value")
                or item.get("debit")
                or item.get("overdue_amount")
            )
            # Se balance for negativo no Nex Web, também representa débito
            balance_val = parse_brl_decimal(item.get("balance"))
            if amount <= Decimal("0") and balance_val < Decimal("0"):
                amount = abs(balance_val)

            if not name or amount <= Decimal("0"):
                continue

            balance_updated_dt = parse_iso_or_flexible_datetime(
                item.get("balance_status_updated_at")
                or item.get("updated_at")
                or item.get("created_at")
            )
            due_date = (
                parse_flexible_date(item.get("oldest_due_date") or item.get("due_date"))
                or oldest_debt_date_by_cust.get(uid)
                or (balance_updated_dt.date() if balance_updated_dt else None)
                or today
            )

            last_visit_dt = parse_iso_or_flexible_datetime(item.get("last_visit"))
            last_purchase = (
                parse_flexible_date(item.get("last_purchase_date"))
                or last_purchase_by_cust.get(uid)
                or (last_visit_dt.date() if last_visit_dt else None)
            )
            on_time_count = int(
                item.get("on_time_payments_count")
                or paid_tx_count_by_cust.get(uid, 0)
            )

            records.append(
                CustomerDebtRecord(
                    customer_id=cust_id,
                    customer_name=name,
                    total_overdue_amount=amount,
                    oldest_due_date=due_date,
                    status=str(item.get("status") or "Em Aberto"),
                    last_purchase_date=last_purchase,
                    on_time_payments_count=on_time_count,
                    is_habitual_good_payer=bool(
                        item.get("is_habitual_good_payer", False)
                    ),
                )
            )
        return records
