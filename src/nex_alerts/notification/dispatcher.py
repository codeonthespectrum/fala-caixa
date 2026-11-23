"""Camada de Disparo/Notificação WhatsApp Multi-Provedor + Simulação Local.

Suporta nativamente:
1. Evolution API (`POST {evolution_api_url}/message/sendText/{evolution_api_instance}`)
2. Z-API (`POST {zapi_instance_url}/send-text`)
3. WhatsApp Cloud API Oficial da Meta (`POST https://graph.facebook.com/v19.0/{meta_phone_number_id}/messages`)
4. CallMeBot (`GET https://api.callmebot.com/whatsapp.php?phone=...&text=...&apikey=...`)
5. Webhook HTTP Genérico (n8n, Make, Zapier)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
import json
from pathlib import Path
import re
from typing import Any, Callable
import urllib.error
import urllib.parse
import urllib.request

from nex_alerts.config import AppConfig
from nex_alerts.domain.models import DailyDigestReport
from nex_alerts.notification.formatter import (
    format_daily_digest,
    format_individual_fiado_message,
)
from nex_alerts.phone_identity import candidatos_envio, normalizar


def _json_default_serializer(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError(f"Tipo não serializável: {type(obj)}")


def normalize_phone_number(phone: str) -> str:
    """Mantém apenas dígitos no telefone (ex: '+55 (84) 99999-9999' -> '5584999999999').

    Delegada canônica para `nex_alerts.phone_identity.normalizar` (mantida por
    compatibilidade com os pontos que já importam daqui).
    """
    return normalizar(phone)


@dataclass(frozen=True)
class DispatchResult:
    """Resultado consolidado do envio da notificação."""

    message_text: str
    saved_file_path: Path
    provider_used: str = "local_only"
    webhook_sent: bool = False
    webhook_status_code: int | None = None
    webhook_error: str | None = None


class NotificationDispatcher:
    """Despacha o resumo diário para WhatsApp (Evolution, Z-API, Meta, CallMeBot ou Webhook) e salva histórico."""

    def __init__(
        self,
        output_dir: Path = Path("./output/alertas_enviados"),
        webhook_url: str = "",
        webhook_timeout_seconds: int = 15,
        print_to_stdout: bool = True,
        config: AppConfig | None = None,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self.config = config
        self.output_dir = Path(config.output_dir if config else output_dir)
        self.webhook_url = (config.webhook_url if config else webhook_url).strip()
        self.webhook_timeout_seconds = (
            config.webhook_timeout_seconds if config else webhook_timeout_seconds
        )
        self.print_to_stdout = print_to_stdout
        self.opener = opener or urllib.request.urlopen
        self._greenapi_chat_id_cache: dict[str, str] = {}
        self._greenapi_state_checked: bool = False

    def build_webhook_payload(
        self, report: DailyDigestReport, message_text: str
    ) -> dict[str, Any]:
        """Monta o payload JSON padronizado para integração com Webhook / Gateways."""
        raw_dict = asdict(report)
        raw_dict["message_text"] = message_text
        return json.loads(json.dumps(raw_dict, default=_json_default_serializer))

    def _resolve_active_provider(self) -> str:
        """Detecta automaticamente qual provedor de WhatsApp usar com base nas credenciais preenchidas."""
        if self.config is None:
            return "webhook" if self.webhook_url else "none"

        explicit = self.config.whatsapp_provider.strip().lower()
        if explicit and explicit != "auto":
            return explicit

        if (
            self.config.evolution_api_url
            and self.config.evolution_api_instance
            and self.config.evolution_api_key
        ):
            return "evolution"
        if self.config.zapi_instance_url:
            return "zapi"
        if self.config.greenapi_id_instance and self.config.greenapi_api_token:
            return "greenapi"
        if self.config.meta_phone_number_id and self.config.meta_access_token:
            return "meta"
        if self.config.callmebot_api_key:
            return "callmebot"
        if self.webhook_url:
            return "webhook"
        return "none"

    def _resolve_hermes_jid(self, phone: str) -> str:
        """Resolve o JID (@s.whatsapp.net ou @lid) usando a sessão local do Hermes Agent (~/.hermes/whatsapp/session)."""
        if "@" in phone:
            return phone
        candidates = candidatos_envio(phone)

        session_dirs = [
            Path.home() / ".hermes" / "profiles" / "falacaixa" / "whatsapp" / "session",
            Path.home() / ".hermes" / "profiles" / "falacaixa" / "platforms" / "whatsapp" / "session",
            Path.home() / ".hermes" / "whatsapp" / "session",
        ]
        for session_dir in session_dirs:
            if session_dir.is_dir():
                for cand in candidates:
                    if (session_dir / f"lid-mapping-{cand}.json").exists():
                        return f"{cand}@s.whatsapp.net"

        return f"{candidates[0]}@s.whatsapp.net"

    def _send_via_hermes(self, phone: str, message_text: str) -> int:
        """Envia mensagem diretamente pelo bridge local do Hermes Agent (POST /send na porta do perfil falacaixa)."""
        assert self.config is not None
        if not phone:
            raise ValueError("WHATSAPP_PHONE é obrigatório para envio via Hermes Agent.")
        base = (self.config.hermes_bridge_url or "http://127.0.0.1:3001").rstrip("/")
        chat_id = self._resolve_hermes_jid(phone)
        url = f"{base}/send"
        parsed_host = urllib.parse.urlparse(base).netloc or "127.0.0.1:3001"
        body = json.dumps({"chatId": chat_id, "message": message_text}).encode("utf-8")
        req = urllib.request.Request(
            url=url,
            data=body,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Host": parsed_host,
                "User-Agent": "Mozilla/5.0 (FalaCaixa/1.0-Hermes)",
            },
            method="POST",
        )
        with self.opener(req, timeout=max(30, self.webhook_timeout_seconds)) as resp:
            return int(resp.status)

    def _resolve_greenapi_chat_id(self, base: str, phone: str) -> str:
        """Resolve o chatId exato do WhatsApp no Brasil (com ou sem o 9º dígito após o DDD)."""
        assert self.config is not None
        if "@" in phone:
            return phone
        if phone in self._greenapi_chat_id_cache:
            return self._greenapi_chat_id_cache[phone]

        candidates = candidatos_envio(phone)

        check_url = (
            f"{base}/waInstance{self.config.greenapi_id_instance}"
            f"/checkWhatsapp/{self.config.greenapi_api_token}"
        )
        for cand in candidates:
            try:
                body = json.dumps({"phoneNumber": int(cand)}).encode("utf-8")
                req = urllib.request.Request(
                    url=check_url,
                    data=body,
                    headers={
                        "Content-Type": "application/json; charset=utf-8",
                        "User-Agent": "Mozilla/5.0 (MargemViva/0.1)",
                    },
                    method="POST",
                )
                with self.opener(req, timeout=self.webhook_timeout_seconds) as resp:
                    raw = resp.read().decode("utf-8")
                    data = json.loads(raw) if raw else {}
                    if isinstance(data, dict) and data.get("existsWhatsapp"):
                        resolved = str(data.get("chatId") or f"{cand}@c.us")
                        self._greenapi_chat_id_cache[phone] = resolved
                        return resolved
            except Exception:
                continue

        # Fallback: o primeiro candidato já respeita a regra do DDD >= 30
        fallback_id = f"{candidates[0]}@c.us" if candidates else f"{phone}@c.us"
        self._greenapi_chat_id_cache[phone] = fallback_id
        return fallback_id

    def _send_via_greenapi(self, phone: str, message_text: str) -> int:
        assert self.config is not None
        if not phone:
            raise ValueError("WHATSAPP_PHONE é obrigatório para envio via Green-API.")
        base = self.config.greenapi_api_url or "https://api.green-api.com"

        # Verifica se a instância do Green-API está autorizada (conectada ao WhatsApp) apenas 1x por sessão
        if not self._greenapi_state_checked:
            self._greenapi_state_checked = True
            state_url = (
                f"{base}/waInstance{self.config.greenapi_id_instance}"
                f"/getStateInstance/{self.config.greenapi_api_token}"
            )
            try:
                state_req = urllib.request.Request(
                    url=state_url,
                    headers={"User-Agent": "Mozilla/5.0 (MargemViva/0.1)"},
                    method="GET",
                )
                with self.opener(state_req, timeout=self.webhook_timeout_seconds) as s_resp:
                    raw_state = s_resp.read()
                    if isinstance(raw_state, bytes):
                        raw_state = raw_state.decode("utf-8")
                    state_data = (
                        json.loads(raw_state)
                        if isinstance(raw_state, str) and raw_state.strip()
                        else {}
                    )
                    state_val = state_data.get("stateInstance") if isinstance(state_data, dict) else None
                    if state_val and state_val != "authorized":
                        raise RuntimeError(
                            f"Instância Green-API desconectada no WhatsApp (stateInstance='{state_val}'). "
                            "Escaneie o QR Code no painel console.green-api.com."
                        )
            except urllib.error.HTTPError:
                pass

        chat_id = self._resolve_greenapi_chat_id(base, phone)
        url = (
            f"{base}/waInstance{self.config.greenapi_id_instance}"
            f"/sendMessage/{self.config.greenapi_api_token}"
        )
        body = json.dumps({"chatId": chat_id, "message": message_text}).encode("utf-8")
        req = urllib.request.Request(
            url=url,
            data=body,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "Mozilla/5.0 (MargemViva/0.1)",
            },
            method="POST",
        )
        with self.opener(req, timeout=self.webhook_timeout_seconds) as resp:
            return int(resp.status)

    def _send_via_evolution(self, phone: str, message_text: str) -> int:
        assert self.config is not None
        if not phone:
            raise ValueError("WHATSAPP_PHONE é obrigatório para envio via Evolution API.")
        url = f"{self.config.evolution_api_url}/message/sendText/{self.config.evolution_api_instance}"
        body = json.dumps({"number": phone, "text": message_text}).encode("utf-8")
        req = urllib.request.Request(
            url=url,
            data=body,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "apikey": self.config.evolution_api_key,
            },
            method="POST",
        )
        with self.opener(req, timeout=self.webhook_timeout_seconds) as resp:
            return int(resp.status)

    def _send_via_zapi(self, phone: str, message_text: str) -> int:
        assert self.config is not None
        if not phone:
            raise ValueError("WHATSAPP_PHONE é obrigatório para envio via Z-API.")
        url = f"{self.config.zapi_instance_url}/send-text"
        body = json.dumps({"phone": phone, "message": message_text}).encode("utf-8")
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if self.config.zapi_client_token:
            headers["Client-Token"] = self.config.zapi_client_token
        req = urllib.request.Request(url=url, data=body, headers=headers, method="POST")
        with self.opener(req, timeout=self.webhook_timeout_seconds) as resp:
            return int(resp.status)

    def _send_via_meta(self, phone: str, message_text: str) -> int:
        assert self.config is not None
        if not phone:
            raise ValueError(
                "WHATSAPP_PHONE é obrigatório para envio via WhatsApp Cloud API (Meta)."
            )
        url = f"https://graph.facebook.com/v19.0/{self.config.meta_phone_number_id}/messages"
        body = json.dumps(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": phone,
                "type": "text",
                "text": {"preview_url": False, "body": message_text},
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            url=url,
            data=body,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {self.config.meta_access_token}",
            },
            method="POST",
        )
        with self.opener(req, timeout=self.webhook_timeout_seconds) as resp:
            return int(resp.status)

    def _send_via_callmebot(self, phone: str, message_text: str) -> int:
        assert self.config is not None
        if not phone:
            raise ValueError("WHATSAPP_PHONE é obrigatório para envio via CallMeBot.")
        params = urllib.parse.urlencode(
            {
                "phone": phone,
                "text": message_text,
                "apikey": self.config.callmebot_api_key,
            }
        )
        url = f"https://api.callmebot.com/whatsapp.php?{params}"
        req = urllib.request.Request(
            url=url,
            headers={"User-Agent": "Mozilla/5.0 (MargemViva/0.1)"},
            method="GET",
        )
        with self.opener(req, timeout=self.webhook_timeout_seconds) as resp:
            return int(resp.status)

    def _send_via_webhook(self, json_bytes: bytes) -> int:
        req = urllib.request.Request(
            url=self.webhook_url,
            data=json_bytes,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with self.opener(req, timeout=self.webhook_timeout_seconds) as resp:
            return int(resp.status)

    def _send_single_text(self, provider: str, phone: str, text: str) -> int:
        """Envia uma única mensagem de texto pelo provedor selecionado."""
        if provider == "hermes":
            return self._send_via_hermes(phone, text)
        if provider == "evolution":
            return self._send_via_evolution(phone, text)
        if provider == "zapi":
            return self._send_via_zapi(phone, text)
        if provider == "greenapi":
            return self._send_via_greenapi(phone, text)
        if provider == "meta":
            return self._send_via_meta(phone, text)
        if provider == "callmebot":
            return self._send_via_callmebot(phone, text)
        raise ValueError(f"Provedor WhatsApp desconhecido: '{provider}'")

    def dispatch(self, report: DailyDigestReport) -> DispatchResult:
        """Formata o relatório, salva em disco, exibe no terminal e envia o resumo + mensagens individuais de fiado."""
        message_text = format_daily_digest(report)
        individual_fiado_messages = [
            format_individual_fiado_message(
                item=f_item,
                store_name=report.store_name,
                fallback_pix_key=report.pix_key,
            )
            for f_item in report.fiado_alerts
        ]

        full_log_text = message_text
        if individual_fiado_messages:
            separator_card = "\n\n" + ("-" * 45) + "\n"
            full_log_text = (
                message_text
                + separator_card
                + separator_card.join(individual_fiado_messages)
            )

        if self.print_to_stdout:
            separator = "=" * 60
            print(f"\n{separator}")
            print(full_log_text)
            print(f"{separator}\n")

        self.output_dir.mkdir(parents=True, exist_ok=True)
        timestamp_slug = report.generated_at.strftime("%Y%m%d_%H%M%S")
        store_slug = (
            "".join(c if c.isalnum() else "_" for c in report.store_name.lower()).strip(
                "_"
            )
            or "loja"
        )
        txt_path = self.output_dir / f"alerta_{store_slug}_{timestamp_slug}.txt"
        json_path = self.output_dir / f"alerta_{store_slug}_{timestamp_slug}.json"

        txt_path.write_text(full_log_text, encoding="utf-8")
        payload = self.build_webhook_payload(report, message_text)
        payload["individual_fiado_messages"] = individual_fiado_messages
        json_bytes = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        json_path.write_bytes(json_bytes)

        provider = self._resolve_active_provider()
        if provider == "none":
            return DispatchResult(
                message_text=message_text,
                saved_file_path=txt_path,
                provider_used="local_only",
                webhook_sent=False,
            )

        phone = normalize_phone_number(
            report.whatsapp_phone or (self.config.whatsapp_phone if self.config else "")
        )
        webhook_sent = False
        webhook_status_code: int | None = None
        webhook_error: str | None = None

        try:
            if provider == "webhook":
                webhook_status_code = self._send_via_webhook(json_bytes)
            else:
                # 1. Dispara a mensagem principal (Resumo de Margem + Estoque Baixo + Resumo de Fiado)
                webhook_status_code = self._send_single_text(
                    provider, phone, message_text
                )
                # 2. Dispara 1 mensagem separada para cada cliente de fiado (pronta para encaminhar)
                for f_msg in individual_fiado_messages:
                    self._send_single_text(provider, phone, f_msg)

            webhook_sent = (
                webhook_status_code is not None and 200 <= webhook_status_code < 300
            )
        except urllib.error.HTTPError as exc:
            webhook_status_code = exc.code
            webhook_error = f"HTTP {exc.code}: {exc.reason}"
        except Exception as exc:
            webhook_error = str(exc)

        return DispatchResult(
            message_text=message_text,
            saved_file_path=txt_path,
            provider_used=provider,
            webhook_sent=webhook_sent,
            webhook_status_code=webhook_status_code,
            webhook_error=webhook_error,
        )
