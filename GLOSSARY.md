# Glossário do Domínio — Fala, Caixa!

Linguagem ubíqua do produto. Sem detalhes de implementação: aqui mora só o
significado das palavras do negócio.

## Loja

Um comércio cliente do Fala, Caixa! (minimercado, mercearia ou conveniência).
Cada Loja tem seus próprios dados de estoque, preços, fiado, Chave PIX e
ciclo de assinatura, isolados das demais.

## Lojista

O dono da Loja, que conversa com o Fala, Caixa! pelo WhatsApp.

## Sócio

Pessoa autorizada pelo Lojista a consultar os dados da Loja no WhatsApp
(sócio, gerente, cônjuge). Um mesmo número pode ser Sócio de mais de uma Loja.

## Admin de Suporte

Número autorizado pela operação do Fala, Caixa! a consultar qualquer Loja
pelo nome, para fins de atendimento. Não é dono nem Sócio de nenhuma Loja
específica.

## Número Autorizado

Telefone de WhatsApp vinculado a uma Loja (Lojista ou Sócio) ou à operação
(Admin de Suporte). Números fora dessa lista não recebem resposta.

## Resolver de Loja

A decisão de **qual Loja** está falando e **se ela pode** ser atendida agora.
Cruza o número de quem chama, um pedido explícito de Loja pelo nome (quando
houver) e o estado da assinatura. Nunca adivinha: quando não reconhece o
chamador, nega o atendimento com orientação em vez de escolher uma Loja
qualquer.

## Fiado

Venda a prazo anotada na caderneta/ERP da Loja, com cliente, valor e
vencimento.

## Régua de Fiado

Classificação dos clientes em atraso em três níveis — **Sumido**,
**Cara-de-Pau** e **Bom Pagador Habitual** — que define o tom e a prioridade
da cobrança.

## Margem Oculta

Diferença entre preço de venda e preço de custo abaixo da meta da Loja,
detectada na reposição do estoque, com sugestão de novo preço.

## Trial

Período de experimento gratuito de 14 dias de uma Loja recém-cadastrada.
Vencido o Trial sem pagamento, a Loja é suspensa com mensagem amigável.
