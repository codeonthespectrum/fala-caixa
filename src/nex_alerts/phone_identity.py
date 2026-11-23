"""Identidade de Telefone do Fala, Caixa!.

Regra canônica e única de telefonia brasileira do produto:

- normalização (só dígitos, DDI 55 inserido quando ausente);
- variantes com/sem o 9º dígito e com/sem DDI 55 (para casar quem chama);
- candidatos de envio ordenados para provedores de WhatsApp, incluindo a
  regra do DDD >= 30 (cujo ID interno no WhatsApp costuma ter 12 dígitos).

Antes deste módulo, essa lógica vivia em 4 lugares com pequenas divergências.
Aqui ela existe uma vez só — e é testada uma vez só.
"""

from __future__ import annotations

import re

_DDD_CORTE_NONO_DIGITO = 30


def normalizar(phone: str) -> str:
    """Mantém apenas dígitos; insere o DDI 55 quando o número vem só com DDD."""
    digits = re.sub(r"\D+", "", phone or "")
    if len(digits) in (10, 11) and not digits.startswith("55"):
        digits = f"55{digits}"
    return digits


def variantes(phone: str, incluir_sem_ddi: bool = False) -> set[str]:
    """Gera as variantes com e sem o 9º dígito; opcionalmente também sem o DDI 55."""
    digits = normalizar(phone)
    if not digits:
        return set()
    result = {digits}
    if digits.startswith("55") and len(digits) == 13 and digits[4] == "9":
        result.add(digits[:4] + digits[5:])
    elif digits.startswith("55") and len(digits) == 12:
        result.add(digits[:4] + "9" + digits[4:])
    if incluir_sem_ddi:
        for v in list(result):
            if v.startswith("55") and len(v) in (12, 13):
                result.add(v[2:])
    return result


def corresponde(phone_a: str, phone_b: str) -> bool:
    """Verifica se dois telefones são o mesmo número (qualquer combinação de 9º dígito/DDI)."""
    return bool(
        variantes(phone_a, incluir_sem_ddi=True)
        & variantes(phone_b, incluir_sem_ddi=True)
    )


def candidatos_envio(phone: str) -> list[str]:
    """Candidatos ordenados de ID de envio para gateways de WhatsApp.

    No Brasil, números de DDD >= 30 costumam ter o ID interno de 12 dígitos
    (sem o 9º dígito) nos gateways; DDDs menores preferem o formato completo.
    """
    digits = normalizar(phone)
    if not digits:
        return []
    if digits.startswith("55") and len(digits) == 13 and digits[4] == "9":
        sem_nono = digits[:4] + digits[5:]
        ddd = int(digits[2:4])
        return [sem_nono, digits] if ddd >= _DDD_CORTE_NONO_DIGITO else [digits, sem_nono]
    if digits.startswith("55") and len(digits) == 12:
        return [digits, digits[:4] + "9" + digits[4:]]
    return [digits]
