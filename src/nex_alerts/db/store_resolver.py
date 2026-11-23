"""Resolver de Loja do Fala, Caixa!.

Decisão única e explícita de **qual loja** está falando e **se ela pode** ser
atendida agora. Centraliza o que antes vivia espalhado no plugin do Hermes:

- casamento do telefone do chamador com os Números Autorizados de cada Loja;
- pedido explícito de Loja pelo nome (hint) — só vale para quem é membro
  daquela Loja ou é Admin de Suporte declarado;
- validação do estado da assinatura (trial válido ou ativa).

Regra inviolável: o Resolver **nunca adivinha**. Se o chamador não é
reconhecido, o atendimento é negado com orientação — nenhuma loja é escolhida
"por fallback".
"""

from __future__ import annotations

import unicodedata
from typing import Iterable

from nex_alerts.db.store_repository import StoreRecord, StoreRepository
from nex_alerts.phone_identity import variantes


class StoreResolutionError(RuntimeError):
    """Erro base da decisão de acesso a uma Loja. A mensagem é amigável e pode ir ao WhatsApp."""


class LojaNaoEncontradaError(StoreResolutionError):
    """O número do chamador não está vinculado a nenhuma Loja (ou o nome pedido não existe)."""


class NaoAutorizadoError(StoreResolutionError):
    """O chamador existe, mas não é membro da Loja pedida nem Admin de Suporte."""


class AssinaturaInativaError(StoreResolutionError):
    """A Loja foi identificada, mas a assinatura está pausada ou o trial venceu."""

    def __init__(self, store_name: str, status: str) -> None:
        self.store_name = store_name
        self.status = status
        super().__init__(
            f"O acesso da loja '{store_name}' está '{status}'. "
            "Para reativar por R$ 67,00/mês, entre em contato com o suporte do Fala, Caixa!."
        )


def _strip_accents(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", str(text or "").strip().lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _expand_candidates(caller_candidates: Iterable[str]) -> set[str]:
    """Normaliza os candidatos do chamador: variantes com/sem 9º dígito e com/sem DDI 55."""
    expanded: set[str] = set()
    for c in caller_candidates:
        expanded |= variantes(c, incluir_sem_ddi=True)
    return expanded


def _store_phone_variants(store: StoreRecord) -> set[str]:
    """Todos os telefones que representam a Loja: membros + telefone de notificação (compat)."""
    variants: set[str] = set()
    for user in store.users:
        variants |= variantes(user.phone, incluir_sem_ddi=True)
    if store.whatsapp_notify_phone:
        variants |= variantes(store.whatsapp_notify_phone)
    return variants


class StoreResolver:
    """Módulo profundo de identidade e autorização Multi-Loja.

    Interface mínima: entra quem está chamando (+ nome de Loja opcional),
    sai a Loja autorizada — ou um erro explícito e amigável.
    """

    def __init__(
        self,
        repo: StoreRepository,
        admin_phones: Iterable[str] = (),
    ) -> None:
        self.repo = repo
        self._admin_variants: set[str] = set()
        for phone in admin_phones:
            self._admin_variants |= variantes(phone)

    def autorizar(
        self,
        caller_candidates: Iterable[str],
        hint_loja: str = "",
    ) -> StoreRecord:
        """Resolve e autoriza a Loja do chamador. Levanta StoreResolutionError caso contrário."""
        expanded = _expand_candidates(caller_candidates)
        if not expanded:
            raise LojaNaoEncontradaError(
                "Não consegui identificar o seu número de WhatsApp. "
                "Se você é cliente do Fala, Caixa!, fale com o suporte."
            )

        is_admin = bool(expanded & self._admin_variants)
        stores = self.repo.list_stores()
        member_of = [s for s in stores if expanded & _store_phone_variants(s)]

        hint_norm = _strip_accents(hint_loja)
        if hint_norm:
            target = next(
                (s for s in stores if hint_norm in _strip_accents(s.store_name)),
                None,
            )
            if target is None:
                raise LojaNaoEncontradaError(
                    f"Não encontrei nenhuma loja com o nome '{hint_loja}'."
                )
            if not is_admin and all(s.id != target.id for s in member_of):
                raise NaoAutorizadoError(
                    f"O seu número não está autorizado a consultar a loja '{target.store_name}'."
                )
            return self._check_subscription(target)

        if not member_of:
            raise LojaNaoEncontradaError(
                "Não encontrei nenhuma loja autorizada para o seu número de WhatsApp. "
                "Se você é cliente do Fala, Caixa!, fale com o suporte."
            )

        # Chamador membro de várias lojas sem hint: usa a primeira com assinatura válida;
        # se todas estiverem inativas, informa o estado da primeira.
        for store in member_of:
            if store.effective_status() in {"active", "trial"}:
                return store
        first = member_of[0]
        raise AssinaturaInativaError(first.store_name, first.effective_status())

    def _check_subscription(self, store: StoreRecord) -> StoreRecord:
        eff = store.effective_status()
        if eff not in {"active", "trial"}:
            raise AssinaturaInativaError(store.store_name, eff)
        return store
