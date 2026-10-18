"""Camada de Disparo/Notificação: Simulação Local + Webhook HTTP Genérico."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
import json
from pathlib import Path
from typing import Any
import urllib.error
import urllib.request

from nex_alerts.domain.models import DailyDigestReport
from nex_alerts.notification.formatter import format_daily_digest


def _json_default_serializer(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError(f"Tipo não serializável: {type(obj)}")


@dataclass(frozen=True)
class DispatchResult:
    """Resultado consolidado do envio da notificação."""

    message_text: str
    saved_file_path: Path
    webhook_sent: bool = False
    webhook_status_code: int | None = None
    webhook_error: str | None = None


class NotificationDispatcher:
    """Despacha o resumo diário para simulação local (terminal + arquivo) e/ou Webhook HTTP."""

    def __init__(
        self,
        output_dir: Path = Path("./output/alertas_enviados"),
        webhook_url: str = "",
        webhook_timeout_seconds: int = 10,
        print_to_stdout: bool = True,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.webhook_url = webhook_url.strip()
        self.webhook_timeout_seconds = webhook_timeout_seconds
        self.print_to_stdout = print_to_stdout

    def build_webhook_payload(
        self, report: DailyDigestReport, message_text: str
    ) -> dict[str, Any]:
        """Monta o payload JSON padronizado para integração com Webhook / Gateways."""
        raw_dict = asdict(report)
        raw_dict["message_text"] = message_text
        # Normaliza tipos Decimal/datetime via round-trip JSON
        return json.loads(json.dumps(raw_dict, default=_json_default_serializer))

    def dispatch(self, report: DailyDigestReport) -> DispatchResult:
        """Formata o relatório, salva em disco, exibe no terminal e envia via Webhook se configurado."""
        message_text = format_daily_digest(report)

        if self.print_to_stdout:
            separator = "=" * 60
            print(f"\n{separator}")
            print(message_text)
            print(f"{separator}\n")

        self.output_dir.mkdir(parents=True, exist_ok=True)
        timestamp_slug = report.generated_at.strftime("%Y%m%d_%H%M%S")
        store_slug = "".join(
            c if c.isalnum() else "_" for c in report.store_name.lower()
        ).strip("_") or "loja"
        txt_path = self.output_dir / f"alerta_{store_slug}_{timestamp_slug}.txt"
        json_path = self.output_dir / f"alerta_{store_slug}_{timestamp_slug}.json"

        txt_path.write_text(message_text, encoding="utf-8")
        payload = self.build_webhook_payload(report, message_text)
        json_bytes = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        json_path.write_bytes(json_bytes)

        webhook_sent = False
        webhook_status_code: int | None = None
        webhook_error: str | None = None

        if self.webhook_url:
            try:
                req = urllib.request.Request(
                    url=self.webhook_url,
                    data=json_bytes,
                    headers={"Content-Type": "application/json; charset=utf-8"},
                    method="POST",
                )
                with urllib.request.urlopen(
                    req, timeout=self.webhook_timeout_seconds
                ) as resp:
                    webhook_status_code = resp.status
                    webhook_sent = 200 <= resp.status < 300
            except urllib.error.HTTPError as exc:
                webhook_status_code = exc.code
                webhook_error = f"HTTP {exc.code}: {exc.reason}"
            except Exception as exc:
                webhook_error = str(exc)

        return DispatchResult(
            message_text=message_text,
            saved_file_path=txt_path,
            webhook_sent=webhook_sent,
            webhook_status_code=webhook_status_code,
            webhook_error=webhook_error,
        )
