# ADR-0003: Multi-Tenancy em SQLite com Número Único e Whitelist Sincronizada

- **Status:** Aceita
- **Data:** 2026-11-22

## Contexto
O produto opera com um **único número oficial de WhatsApp** (`84 92001-2483`) para atender múltiplas lojas. Inicialmente, a configuração ficava em `.env` / `stores.json` e `WHATSAPP_ALLOW_ALL_USERS=true`, o que não suportava ciclo de vida de clientes nem protegia contra consultas de desconhecidos ou consumo indevido de tokens de LLM.

## Decisão
Adotar um banco **SQLite Multi-Tenant** (`data/falacaixa.db`) via `StoreRepository` com três entidades (`stores`, `store_users`, `dispatch_logs`), combinado com segurança em duas camadas:
1. **Camada de Gateway (`WHATSAPP_ALLOW_ALL_USERS=false`):** O repositório sincroniza automaticamente `WHATSAPP_ALLOWED_USERS` no `.env` do perfil `falacaixa` (gerando variantes com e sem o 9º dígito brasileiro) sempre que uma loja ou sócio é cadastrado/removido. Desconhecidos são bloqueados antes de acionar o modelo de IA.
2. **Camada de Aplicação (Plugin):** Ao receber uma mensagem, o plugin resolve o telefone do remetente (`HERMES_SESSION_USER_ID`) na tabela `store_users` e carrega exclusivamente as credenciais e a Chave PIX da loja correspondente.

## Alternativas Consideradas
- **Um perfil Hermes / número WhatsApp por loja:** inviável para escalar acima de 2–3 clientes por exigir múltiplos chips/sessões.
- **Bot aberto para qualquer número (`WHATSAPP_ALLOW_ALL_USERS=true`):** descartada nesta fase para evitar gasto de créditos de LLM com curiosos e reforçar o isolamento.
- **Postgres/Supabase desde o dia 1:** adiada para a migração para VPS, mantendo SQLite local no MVP pela simplicidade e custo zero.

## Consequências
- **Positivas:** Custo zero de infraestrutura no piloto, isolamento estrito entre lojas, suporte a múltiplos sócios por loja e zero gasto de IA com números não autorizados.
- **Negativas:** Migrar para Postgres será necessário caso o sistema passe a escalar para múltiplas instâncias/workers concorrentes em nuvem.
