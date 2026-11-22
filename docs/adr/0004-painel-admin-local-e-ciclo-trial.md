# ADR-0004: Painel Admin Local Zero-Dependência e Suspensão Suave de Assinatura

- **Status:** Aceita
- **Data:** 2026-11-22

## Contexto
O modelo comercial do **Fala, Caixa!** oferece **14 dias grátis (Trial)** seguidos de assinatura mensal de **R$ 67,00**. A operação precisa cadastrar novos lojistas em menos de 5 minutos, testar a conexão com o caixa, subir planilhas CSV quando necessário e controlar quem está com trial ativo, vencido ou pausado.

## Decisão
1. **Mini-Painel Web Admin (`src/nex_alerts/admin_web.py`):** Construído com `ThreadingHTTPServer` da biblioteca padrão do Python (sem dependências extras) na porta `8080`, oferecendo KPIs de MRR, cadastro de loja em Trial 14 dias, gestão de sócios, upload CSV/XLSX, teste de conexão e disparo manual.
2. **Suspensão Suave (`SubscriptionInactiveError`):** Quando `trial_ends_at < hoje` ou `status` é alterado para `paused`/`expired`, o cron diário ignora a loja automaticamente e as ferramentas do agente bloqueiam o acesso aos dados do ERP, respondendo com instrução amigável de reativação (sem remover o cliente do histórico).

## Alternativas Consideradas
- **CLI puro apenas via terminal:** descartado por ser menos prático para testes rápidos de conexão, upload de CSV e visão de vencimento dos trials.
- **Bloqueio silencioso removendo o número da allowlist:** descartado porque o lojista ficaria sem entender por que o bot parou de responder após os 14 dias.

## Consequências
- **Positivas:** Onboarding visual rápido, zero dependência externa para rodar o painel e experiência clara de renovação para o cliente.
- **Negativas:** O painel atual roda apenas em `127.0.0.1` (uso local do administrador) e não possui autenticação própria caso seja exposto na internet sem VPN/proxy reverso.
