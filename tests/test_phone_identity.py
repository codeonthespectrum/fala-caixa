"""Testes da Identidade de Telefone (regra canônica do 9º dígito e do DDI 55)."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from nex_alerts.db.store_repository import phone_variants  # wrapper de compatibilidade
from nex_alerts.notification.dispatcher import normalize_phone_number  # wrapper de compat.
from nex_alerts.phone_identity import (
    candidatos_envio,
    corresponde,
    normalizar,
    variantes,
)


class NormalizarTests(unittest.TestCase):
    def test_remove_pontuacao(self) -> None:
        self.assertEqual(normalizar("+55 (84) 99999-9999"), "5584999999999")

    def test_insere_ddi_quando_so_vem_ddd(self) -> None:
        self.assertEqual(normalizar("84999999999"), "5584999999999")
        self.assertEqual(normalizar("84988776655"), "5584988776655")

    def test_nao_duplica_ddi(self) -> None:
        self.assertEqual(normalizar("5584999999999"), "5584999999999")

    def test_vazio(self) -> None:
        self.assertEqual(normalizar(""), "")
        self.assertEqual(normalizar("abc"), "")

    def test_wrapper_dispatcher_compativel(self) -> None:
        self.assertEqual(normalize_phone_number("(84) 98877-6655"), "5584988776655")


class VariantesTests(unittest.TestCase):
    def test_com_nono_digito_gera_sem_e_vice_versa(self) -> None:
        self.assertEqual(
            variantes("5584999887766"),
            {"5584999887766", "558499887766"},
        )
        self.assertEqual(
            variantes("558499887766"),
            {"558499887766", "5584999887766"},
        )

    def test_sem_ddi_opcional(self) -> None:
        com_ddi = variantes("5584999887766")
        self.assertNotIn("84999887766", com_ddi)
        completo = variantes("5584999887766", incluir_sem_ddi=True)
        self.assertEqual(
            completo,
            {"5584999887766", "558499887766", "84999887766", "8499887766"},
        )

    def test_numero_estrangeiro_nao_gera_variante(self) -> None:
        self.assertEqual(variantes("1900555123456"), {"1900555123456"})

    def test_wrapper_repository_compativel(self) -> None:
        self.assertEqual(
            phone_variants("5584987780489"),
            {"5584987780489", "558487780489"},
        )


class CorrespondeTests(unittest.TestCase):
    def test_mesmo_numero_em_formatos_diferentes(self) -> None:
        self.assertTrue(corresponde("5584999999999", "84999999999"))
        self.assertTrue(corresponde("(84) 99999-9999", "558499999999"))
        self.assertTrue(corresponde("558499999999", "5584999999999"))

    def test_numeros_diferentes(self) -> None:
        self.assertFalse(corresponde("5584999999999", "5584111111111"))
        self.assertFalse(corresponde("", "5584999999999"))


class CandidatosEnvioTests(unittest.TestCase):
    def test_ddd_maior_igual_30_prefere_12_digitos(self) -> None:
        # DDD 84 (Natal): ID interno do WhatsApp costuma ser sem o 9º dígito
        self.assertEqual(
            candidatos_envio("5584999887766"),
            ["558499887766", "5584999887766"],
        )

    def test_ddd_menor_que_30_prefere_13_digitos(self) -> None:
        # DDD 11 (São Paulo): formato completo primeiro
        self.assertEqual(
            candidatos_envio("5511998887777"),
            ["5511998887777", "551198887777"],
        )

    def test_entrada_com_12_digitos(self) -> None:
        self.assertEqual(
            candidatos_envio("558499887766"),
            ["558499887766", "5584999887766"],
        )

    def test_numero_estrangeiro_e_vazio(self) -> None:
        self.assertEqual(candidatos_envio("1900555123456"), ["1900555123456"])
        self.assertEqual(candidatos_envio(""), [])


if __name__ == "__main__":
    unittest.main()
