# ADR-0002: Integração 100% Read-Only e Padrão Adapter para ERPs

- **Status:** Aceita
- **Data:** 2026-11-22

## Contexto
Pequenos comerciantes têm receio legítimo de conectar ferramentas externas ao sistema de caixa por medo de alteração acidental de preços, exclusão de vendas ou travamento do PDV. Além disso, o mercado utiliza múltiplos ERPs (Nextar, Bling, Omie, SysPDV, MarketUP) e planilhas Excel/CSV.

## Decisão
1. **Política estrita Read-Only:** Nenhum conector ou ferramenta do agente executa operações de escrita (`POST`/`PUT`/`PATCH`/`DELETE` de negócio) no ERP do cliente. Toda alteração sugerida (ex.: novo preço de gôndola ou envio de cobrança de fiado) é apenas recomendada ao lojista no WhatsApp.
2. **Padrão Adapter (`BaseNexCollector`):** O motor de regras (`margin_analyzer` e `fiado_analyzer`) consome exclusivamente modelos de domínio normalizados (`StockEntry` e `CustomerDebtRecord`). Hoje estão ativos `NexWebReadOnlyCollector` (Nextar ao vivo) e `FolderNexCollector` (importação universal via Excel/CSV), permitindo plugar Bling ou Omie sob demanda sem alterar o motor.

## Alternativas Consideradas
- **Escrita automática de preços no ERP:** descartada por risco jurídico/operacional e resistência de adoção pelos lojistas.
- **Implementar conectores de todos os ERPs antes de vender:** descartada para evitar desperdício de esforço antes da validação comercial.

## Consequências
- **Positivas:** Segurança total para o caixa do cliente ("100% somente leitura") e onboarding imediato via Excel/CSV para lojas que não usam Nextar.
- **Negativas:** Lojas no modo CSV exigem envio periódico da planilha até que um conector API específico seja adicionado.
