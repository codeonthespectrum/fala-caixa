# ADR-0005: Busca Híbrida de Produtos/Clientes e Cache em Memória no Plugin

- **Status:** Aceita
- **Data:** 2026-11-22

## Contexto
Consultas em linguagem natural pelo WhatsApp apresentam abreviações e variações de grafia comuns do varejo (ex.: `"coca 2l"` vs `"REFRIG COCA COLA 2LT"`, `"bolacha"` vs `"BISCOITO"`). Além disso, consultar toda a base paginada do Nextar a cada pergunta consecutiva aumentava a latência de resposta para dezenas de segundos.

## Decisão
1. **Cache em memória por loja (`TTL = 300s`):** Os produtos ativos e saldos de fiado ficam em cache por 5 minutos, chaveado por loja e modo de coleta.
2. **Scoring semântico leve (`_score_smart_query`):** Combina normalização sem acentos, expansão de sinônimos/unidades de varejo (`2l`/`2lt`, `cx`/`fardo`, `bisc`/`bolacha`, `cerv`/`cerveja`), cobertura de tokens e `difflib.SequenceMatcher`, filtrando itens inativos (`active=False`) para não poluir o WhatsApp.

## Alternativas Consideradas
- **Embeddings / Banco Vetorial externo:** desnecessário para catálogos típicos de minimercado (300 a 2.000 SKUs) e adicionaria custo e latência de rede.
- **Match exato de substring:** descartado por falhar com abreviações e erros leves de digitação no WhatsApp.

## Consequências
- **Positivas:** Respostas rápidas após a primeira leitura e alta taxa de acerto em perguntas naturais do lojista.
- **Negativas:** Alterações feitas no caixa nos últimos 5 minutos podem levar até o vencimento do TTL para refletir em consultas conversacionais.
