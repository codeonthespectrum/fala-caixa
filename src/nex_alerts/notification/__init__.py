"""Pacote de formatação e notificação de alertas."""

from nex_alerts.notification.dispatcher import DispatchResult, NotificationDispatcher
from nex_alerts.notification.formatter import format_brl, format_daily_digest

__all__ = [
    "DispatchResult",
    "NotificationDispatcher",
    "format_brl",
    "format_daily_digest",
]
