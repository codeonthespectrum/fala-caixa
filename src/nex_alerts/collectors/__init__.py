"""Pacote de coletores de dados somente-leitura para o Nex."""

from nex_alerts.collectors.base import BaseNexCollector, ReadOnlyViolationError
from nex_alerts.collectors.folder_collector import FolderNexCollector
from nex_alerts.collectors.mock_collector import MockNexCollector
from nex_alerts.collectors.nex_web_collector import NexWebReadOnlyCollector

__all__ = [
    "BaseNexCollector",
    "FolderNexCollector",
    "MockNexCollector",
    "NexWebReadOnlyCollector",
    "ReadOnlyViolationError",
]
