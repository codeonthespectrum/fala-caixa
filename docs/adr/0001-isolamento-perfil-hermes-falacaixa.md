# ADR-0001: Isolamento do Agente Fala, Caixa! em Perfil Dedicado do Hermes

- **Status:** Aceita
- **Data:** 2026-11-22

## Contexto
O host local do Hermes Agent já executava um projeto distinto no perfil `default` (conectado a um número de WhatsApp próprio e com conjunto específico de plugins e instruções). Compartilhar o mesmo perfil causava colisão de instruções de sistema, excesso de ferramentas no contexto (aumentando a latência de resposta) e risco de interferência entre projetos.

## Decisão
Isolar o **Fala, Caixa!** em um perfil dedicado chamado `falacaixa` (`~/.hermes/profiles/falacaixa`), utilizando:
- Sessão WhatsApp própria na porta `3001` (`http://127.0.0.1:3001`), pareada ao número exclusivo `84 92001-2483`;
- Conjunto mínimo de plugins habilitados (`margem-viva`) com `tool_search` desabilitado para eliminar chamadas extras de descoberta de ferramentas;
- Cron diário próprio (`margemviva_daily.py`) apontando explicitamente para o bridge da porta `3001`.

## Alternativas Consideradas
- **Manter no perfil `default`:** descartada por causar conflito de contexto e risco operacional para o outro projeto no mesmo host.
- **Subir container separado desde o dia 1:** adiada para a etapa de migração para VPS na nuvem, pois o perfil isolado resolve o problema localmente com custo zero.

## Consequências
- **Positivas:** Zero interferência entre agentes, menor latência por turno e número de WhatsApp dedicado ao produto.
- **Negativas:** O perfil `falacaixa` depende do gateway multiplex do host estar ativo; exige atenção para nunca alterar arquivos do perfil `default`.
