# MargemViva — Alertas Inteligentes para Nex (Nextar)

O **MargemViva** é uma camada complementar, leve e **100% Read-Only (somente leitura)** de inteligência operacional via WhatsApp para minimercados e mercearias que utilizam o sistema **Nex (Nextar)**.

## Funcionalidades
1. **Funcionalidade A — Alerta de Margem Oculta na Reposição (Anti-Prejuízo):**
   - Varre produtos com entrada nas últimas 24 a 48 horas.
   - Calcula $\text{Margem} = \frac{\text{Preço de Venda} - \text{Preço de Custo}}{\text{Preço de Venda}}$.
   - Identifica itens com margem inferior a `30%` e sugere o novo preço de gôndola ($\text{Preço de Custo} / 0,70$).
2. **Funcionalidade B — Régua de Fiado Estratificada em 3 Níveis (Anti-Calote):**
   - Filtra contas em aberto vencidas há mais de 30 dias (`Vencimento < Hoje - 30 dias`).
   - Classifica cada cliente em:
     - **Nível 2 (O Cara-de-Pau):** Compras nos últimos 15 dias $\rightarrow$ Alerta imediato para travar novo fiado no caixa.
     - **Nível 1 (O Sumido):** Sem compras nos últimos 30 dias $\rightarrow$ Lembrete com chave PIX configurada pelo lojista.
     - **Nível 3 (Bom Pagador):** Histórico recorrente de pagamentos em dia $\rightarrow$ Acompanhamento brando.

---

## Como Operar para Múltiplos Clientes (Modo SaaS / GitHub Actions até ~30 Lojas)

Você pode atender dezenas de mercearias sem precisar instalar nada no computador delas, usando a sincronização **Nex na Web** e o **GitHub Actions**:

1. No seu repositório do GitHub, acesse **Settings $\rightarrow$ Secrets and variables $\rightarrow$ Actions**.
2. Crie um Secret chamado **`STORES_CONFIG_JSON`** contendo a lista dos seus clientes (veja o modelo em `stores.example.json`):
   ```json
   [
     {
       "store_name": "Mercadinho do Primo",
       "nex_email": "primo@email.com",
       "nex_password": "senha_do_nex_do_primo",
       "pix_key": "84999999999",
       "whatsapp_phone": "5584999999999",
       "mode": "nex_web",
       "active": true
     }
   ]
   ```
3. O workflow `.github/workflows/margemviva_daily.yml` roda sozinho todos os dias às **08h00 (horário de Brasília)** ou a qualquer momento clicando no botão **Run workflow** na aba **Actions** do GitHub.
4. **Isolamento de Falhas:** Se um cliente trocar a senha do Nex e esquecer de te avisar, o **MargemViva** registra o aviso daquela loja e continua processando todos os outros clientes normalmente.
