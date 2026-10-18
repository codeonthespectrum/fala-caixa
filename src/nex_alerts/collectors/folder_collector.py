"""Coletor de pasta local monitorada para relatórios exportados pelo Nex (.xls, .xlsx, .csv).

Opera em modo 100% Read-Only: apenas abre e lê os arquivos na pasta configurada,
sem jamais alterar os arquivos originais ou o banco do Nex.
"""

from __future__ import annotations

import csv
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from nex_alerts.collectors.base import BaseNexCollector
from nex_alerts.domain.models import CustomerDebtRecord, StockEntry


def _normalize_header(text: Any) -> str:
    """Normaliza nome de coluna removendo acentos, espaços extras e caracteres especiais."""
    if text is None:
        return ""
    raw = str(text).strip().lower()
    nfkd = unicodedata.normalize("NFKD", raw)
    no_accents = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "_", no_accents).strip("_")


def parse_brl_decimal(value: Any, default: Decimal = Decimal("0.00")) -> Decimal:
    """Converte strings monetárias brasileiras (ex: 'R$ 1.234,56' ou '6,20' ou 6.2) em Decimal."""
    if value is None or value == "":
        return default
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))

    text = str(value).strip()
    # Remove símbolo de moeda e espaços
    text = re.sub(r"[rR]\$\s*", "", text).strip()
    if not text:
        return default

    # Se possui vírgula e ponto (ex: 1.234,56), remove pontos de milhar e troca vírgula por ponto
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")

    try:
        return Decimal(text)
    except InvalidOperation:
        return default


def parse_flexible_datetime(value: Any, fallback: datetime | None = None) -> datetime | None:
    """Converte valores de data/hora de planilhas (BR dd/mm/yyyy, ISO, ou datetime nativo)."""
    if value is None or value == "":
        return fallback
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())

    text = str(value).strip()
    formats = [
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%d-%m-%Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return fallback


def parse_flexible_date(value: Any, fallback: date | None = None) -> date | None:
    """Converte valores de data em objeto `date`."""
    dt = parse_flexible_datetime(value)
    return dt.date() if dt is not None else fallback


def read_tabular_file(file_path: Path) -> list[dict[str, Any]]:
    """Lê um arquivo .csv, .xlsx ou .xls em modo somente-leitura e retorna lista de dicionários."""
    suffix = file_path.suffix.lower()

    if suffix == ".csv":
        return _read_csv_file(file_path)
    if suffix == ".xlsx":
        return _read_xlsx_file(file_path)
    if suffix == ".xls":
        return _read_xls_file(file_path)
    raise ValueError(f"Formato de arquivo não suportado: {file_path}")


def _read_csv_file(file_path: Path) -> list[dict[str, Any]]:
    content: str | None = None
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            content = file_path.read_text(encoding=encoding)
            break
        except UnicodeDecodeError:
            continue

    if not content:
        return []

    lines = [line for line in content.splitlines() if line.strip()]
    if not lines:
        return []

    # Detecta delimitador (; é padrão no Excel em português, , em CSV internacional)
    first_line = lines[0]
    delimiter = ";" if first_line.count(";") >= first_line.count(",") else ","

    reader = csv.reader(lines, delimiter=delimiter)
    rows = list(reader)
    if len(rows) < 2:
        return []

    headers = [_normalize_header(h) for h in rows[0]]
    records: list[dict[str, Any]] = []
    for row in rows[1:]:
        if not any(str(cell).strip() for cell in row):
            continue
        record = {
            headers[i]: row[i]
            for i in range(min(len(headers), len(row)))
            if headers[i]
        }
        records.append(record)
    return records


def _read_xlsx_file(file_path: Path) -> list[dict[str, Any]]:
    try:
        import openpyxl

        wb = openpyxl.load_workbook(filename=file_path, read_only=True, data_only=True)
        try:
            ws = wb.active
            if ws is None:
                return []
            rows = list(ws.iter_rows(values_only=True))
            if len(rows) < 2:
                return []
            headers = [_normalize_header(h) for h in rows[0]]
            records: list[dict[str, Any]] = []
            for row in rows[1:]:
                if not any(cell is not None and str(cell).strip() != "" for cell in row):
                    continue
                record = {
                    headers[i]: row[i]
                    for i in range(min(len(headers), len(row)))
                    if headers[i]
                }
                records.append(record)
            return records
        finally:
            wb.close()
    except ImportError:
        return _read_xlsx_stdlib(file_path)


def _read_xlsx_stdlib(file_path: Path) -> list[dict[str, Any]]:
    """Lê arquivos .xlsx usando apenas zipfile e xml.etree.ElementTree da biblioteca padrão."""
    import xml.etree.ElementTree as ET
    import zipfile

    ns = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(file_path, "r") as zf:
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            ss_root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in ss_root.findall("main:si", ns):
                texts = [t.text or "" for t in si.findall(".//main:t", ns)]
                shared_strings.append("".join(texts))

        sheet_files = sorted(
            name for name in zf.namelist() if name.startswith("xl/worksheets/sheet")
        )
        if not sheet_files:
            return []

        sheet_root = ET.fromstring(zf.read(sheet_files[0]))
        rows: list[list[Any]] = []
        for row_el in sheet_root.findall(".//main:sheetData/main:row", ns):
            row_vals: list[Any] = []
            for cell_el in row_el.findall("main:c", ns):
                cell_type = cell_el.attrib.get("t", "")
                val_el = cell_el.find("main:v", ns)
                inline_el = cell_el.find(".//main:is/main:t", ns)
                if cell_type == "s" and val_el is not None and val_el.text is not None:
                    idx = int(val_el.text)
                    row_vals.append(shared_strings[idx] if idx < len(shared_strings) else "")
                elif cell_type == "inlineStr" and inline_el is not None:
                    row_vals.append(inline_el.text or "")
                elif val_el is not None and val_el.text is not None:
                    row_vals.append(val_el.text)
                else:
                    row_vals.append("")
            rows.append(row_vals)

        if len(rows) < 2:
            return []
        headers = [_normalize_header(h) for h in rows[0]]
        records: list[dict[str, Any]] = []
        for row in rows[1:]:
            if not any(cell is not None and str(cell).strip() != "" for cell in row):
                continue
            record = {
                headers[i]: row[i]
                for i in range(min(len(headers), len(row)))
                if headers[i]
            }
            records.append(record)
        return records


def _read_xls_file(file_path: Path) -> list[dict[str, Any]]:
    try:
        import xlrd

        wb = xlrd.open_workbook(filename=str(file_path), on_demand=True)
        try:
            sheet = wb.sheet_by_index(0)
            if sheet.nrows < 2:
                return []
            headers = [
                _normalize_header(sheet.cell_value(0, col))
                for col in range(sheet.ncols)
            ]
            records: list[dict[str, Any]] = []
            for row_idx in range(1, sheet.nrows):
                row_vals: list[Any] = []
                for col_idx in range(sheet.ncols):
                    cell = sheet.cell(row_idx, col_idx)
                    if cell.ctype == xlrd.XL_CELL_DATE:
                        dt_tuple = xlrd.xldate_as_tuple(cell.value, wb.datemode)
                        if dt_tuple[0] > 0:
                            row_vals.append(datetime(*dt_tuple))
                        else:
                            row_vals.append(cell.value)
                    else:
                        row_vals.append(cell.value)

                if not any(v is not None and str(v).strip() != "" for v in row_vals):
                    continue
                record = {
                    headers[i]: row_vals[i]
                    for i in range(min(len(headers), len(row_vals)))
                    if headers[i]
                }
                records.append(record)
            return records
        finally:
            wb.release_resources()
    except ImportError:
        # Muitos sistemas (incluindo exportações rápidas em Delphi) salvam .xls como TSV/CSV ou XML
        return _read_csv_file(file_path)


def _pick_first(row: dict[str, Any], candidates: tuple[str, ...]) -> Any:
    """Retorna o primeiro valor não-vazio encontrado entre as chaves candidatas."""
    for key in candidates:
        if key in row and row[key] is not None and str(row[key]).strip() != "":
            return row[key]
    return None


class FolderNexCollector(BaseNexCollector):
    """Lê arquivos exportados do Nex (.xls, .xlsx, .csv) de uma pasta local."""

    STOCK_Id_COLS = ("codigo", "id", "cod", "cod_produto", "codigo_produto", "sku")
    STOCK_NAME_COLS = (
        "produto",
        "descricao",
        "nome",
        "nome_do_produto",
        "item",
        "mercadoria",
    )
    STOCK_COST_COLS = (
        "preco_de_custo",
        "custo",
        "valor_custo",
        "custo_unitario",
        "ultimo_custo",
        "cost_price",
    )
    STOCK_SALE_COLS = (
        "preco_de_venda",
        "preco",
        "venda",
        "valor_venda",
        "preco_atual",
        "sale_price",
    )
    STOCK_DATE_COLS = (
        "data_entrada",
        "data_da_entrada",
        "data_compra",
        "ultima_entrada",
        "atualizado_em",
        "data",
        "entry_date",
    )

    DEBT_ID_COLS = ("codigo_cliente", "id_cliente", "codigo", "id", "cod")
    DEBT_NAME_COLS = ("cliente", "nome_do_cliente", "nome", "consumidor", "customer_name")
    DEBT_AMOUNT_COLS = (
        "debito",
        "valor_debito",
        "total_em_aberto",
        "valor_em_aberto",
        "saldo_devedor",
        "valor",
        "total_overdue_amount",
    )
    DEBT_DUE_COLS = (
        "vencimento",
        "data_vencimento",
        "data_de_vencimento",
        "vencimento_mais_antigo",
        "data_debito",
        "oldest_due_date",
    )
    DEBT_STATUS_COLS = ("status", "situacao", "estado")
    DEBT_LAST_PURCHASE_COLS = (
        "ultima_compra",
        "data_ultima_compra",
        "ultima_venda",
        "ultima_visita",
        "last_purchase_date",
    )
    DEBT_ON_TIME_COLS = (
        "pagamentos_em_dia",
        "historico_pagamentos_em_dia",
        "qtd_pagamentos_em_dia",
        "on_time_payments_count",
    )
    DEBT_GOOD_PAYER_COLS = (
        "bom_pagador",
        "pagador_habitual",
        "is_habitual_good_payer",
        "perfil",
    )

    def __init__(self, folder_path: Path) -> None:
        self.folder_path = Path(folder_path)

    def _iter_supported_files(self) -> list[Path]:
        if not self.folder_path.exists():
            return []
        files = [
            p
            for p in self.folder_path.iterdir()
            if p.is_file()
            and not p.name.startswith("~$")
            and p.suffix.lower() in {".csv", ".xlsx", ".xls"}
        ]
        files.sort(key=lambda p: p.name)
        return files

    def fetch_recent_stock_entries(
        self, reference_now: datetime | None = None
    ) -> list[StockEntry]:
        now = reference_now or datetime.now()
        entries: list[StockEntry] = []

        for file_path in self._iter_supported_files():
            rows = read_tabular_file(file_path)
            if not rows:
                continue
            # Verifica se o arquivo tem colunas compatíveis com Produtos/Estoque
            sample_keys = set(rows[0].keys())
            has_name = bool(sample_keys.intersection(self.STOCK_NAME_COLS))
            has_cost = bool(sample_keys.intersection(self.STOCK_COST_COLS))
            has_sale = bool(sample_keys.intersection(self.STOCK_SALE_COLS))
            if not (has_name and has_cost and has_sale):
                continue

            # Caso o relatório exportado de produtos não tenha coluna de data de entrada,
            # utiliza o mtime do arquivo exportado como referência
            file_mtime = datetime.fromtimestamp(file_path.stat().st_mtime)

            for idx, row in enumerate(rows, start=1):
                name_val = _pick_first(row, self.STOCK_NAME_COLS)
                if not name_val:
                    continue
                prod_id = str(_pick_first(row, self.STOCK_Id_COLS) or f"ITEM-{idx:04d}").strip()
                cost_price = parse_brl_decimal(_pick_first(row, self.STOCK_COST_COLS))
                sale_price = parse_brl_decimal(_pick_first(row, self.STOCK_SALE_COLS))
                raw_date = _pick_first(row, self.STOCK_DATE_COLS)
                entry_dt = parse_flexible_datetime(raw_date, fallback=now if raw_date is None else file_mtime)

                if cost_price > Decimal("0") and entry_dt is not None:
                    entries.append(
                        StockEntry(
                            product_id=prod_id,
                            product_name=str(name_val).strip(),
                            entry_date=entry_dt,
                            cost_price=cost_price,
                            sale_price=sale_price,
                        )
                    )

        return entries

    def fetch_customer_debts(
        self, reference_today: date | None = None
    ) -> list[CustomerDebtRecord]:
        records_by_customer: dict[str, CustomerDebtRecord] = {}

        for file_path in self._iter_supported_files():
            rows = read_tabular_file(file_path)
            if not rows:
                continue
            sample_keys = set(rows[0].keys())
            has_customer = bool(sample_keys.intersection(self.DEBT_NAME_COLS))
            has_amount = bool(sample_keys.intersection(self.DEBT_AMOUNT_COLS))
            if not (has_customer and has_amount):
                continue

            for idx, row in enumerate(rows, start=1):
                name_val = _pick_first(row, self.DEBT_NAME_COLS)
                if not name_val:
                    continue
                cust_name = str(name_val).strip()
                cust_id = str(
                    _pick_first(row, self.DEBT_ID_COLS) or cust_name.lower()
                ).strip()
                amount = parse_brl_decimal(_pick_first(row, self.DEBT_AMOUNT_COLS))
                if amount <= Decimal("0"):
                    continue

                due_date = parse_flexible_date(_pick_first(row, self.DEBT_DUE_COLS))
                if due_date is None:
                    continue

                status_raw = str(_pick_first(row, self.DEBT_STATUS_COLS) or "Em Aberto").strip()
                last_purchase = parse_flexible_date(
                    _pick_first(row, self.DEBT_LAST_PURCHASE_COLS)
                )
                on_time_raw = _pick_first(row, self.DEBT_ON_TIME_COLS)
                try:
                    on_time_count = int(float(str(on_time_raw))) if on_time_raw is not None else 0
                except ValueError:
                    on_time_count = 0

                good_payer_raw = str(
                    _pick_first(row, self.DEBT_GOOD_PAYER_COLS) or ""
                ).strip().lower()
                is_good_payer = good_payer_raw in {
                    "sim",
                    "s",
                    "true",
                    "1",
                    "bom pagador",
                    "yes",
                }

                # Se houver múltiplas linhas para o mesmo cliente (várias notas em aberto),
                # soma os débitos em aberto e preserva o vencimento mais antigo e a compra mais recente
                existing = records_by_customer.get(cust_id)
                if existing is None:
                    records_by_customer[cust_id] = CustomerDebtRecord(
                        customer_id=cust_id,
                        customer_name=cust_name,
                        total_overdue_amount=amount,
                        oldest_due_date=due_date,
                        status=status_raw,
                        last_purchase_date=last_purchase,
                        on_time_payments_count=on_time_count,
                        is_habitual_good_payer=is_good_payer,
                    )
                else:
                    merged_oldest_due = min(existing.oldest_due_date, due_date)
                    if existing.last_purchase_date and last_purchase:
                        merged_last_purchase = max(
                            existing.last_purchase_date, last_purchase
                        )
                    else:
                        merged_last_purchase = (
                            existing.last_purchase_date or last_purchase
                        )
                    records_by_customer[cust_id] = CustomerDebtRecord(
                        customer_id=cust_id,
                        customer_name=existing.customer_name,
                        total_overdue_amount=existing.total_overdue_amount + amount,
                        oldest_due_date=merged_oldest_due,
                        status=existing.status,
                        last_purchase_date=merged_last_purchase,
                        on_time_payments_count=max(
                            existing.on_time_payments_count, on_time_count
                        ),
                        is_habitual_good_payer=(
                            existing.is_habitual_good_payer or is_good_payer
                        ),
                    )

        return list(records_by_customer.values())
