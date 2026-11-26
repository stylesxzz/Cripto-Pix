# Cripto Pix

Painel de administração para cobrar em reais por **Pix** e receber em **cripto (USDC/USDT)** direto na sua carteira. A conversão é feita pela **Hodle** e o sistema não guarda chave privada nenhuma.

## O que faz

- **Cobranças avulsas:** você digita o valor, gera o link de pagamento e envia pelo WhatsApp.
- **Assinaturas:** pré-cadastro com nome, WhatsApp e valor mensal (mínimo R$ 100). Todo mês a cobrança é criada e avisada no WhatsApp.
- **Carteira fixa no `.env`:** todo pagamento vai para a mesma carteira, e não existe campo no painel para trocar o destino.
- **Segurança:** login com senha em hash, proteção CSRF, limite de tentativas de login, limite de valor por cobrança, webhook validado e idempotente, e log de auditoria.

> **Status da integração com a Hodle:** o painel, as assinaturas e o WhatsApp funcionam. As chamadas à API da Hodle (criar Pix, converter, sacar e validar webhook) ficam no arquivo `hodle.py`, que precisa ser preenchido com a documentação oficial da Hodle. Enquanto isso, o sistema roda em **modo manual** (`HODLE_LIVE=0`).

## Instalação (Termux ou Linux)

```bash
git clone https://github.com/stylesxzz/Cripto-Pix.git
cd Cripto-Pix
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Gere o hash da senha do admin e cole a linha no `.env`:

```bash
python gerar_hash.py
```

Rodar:

```bash
python app.py
```

Abra `http://localhost:5000` e conclua o assistente de configuração na tela. Em produção, use PostgreSQL persistente; o SQLite local é apenas para desenvolvimento.

## Primeiro acesso e configuração

Ao abrir o painel pela primeira vez, a própria tela apresenta o assistente de configuração. Cadastre um único usuário administrador, senha, carteira, chave API e Workspace ID da Hodle. Os dados do WhatsApp são opcionais. Nos próximos acessos, será exibido somente o formulário de login.

As credenciais sensíveis são criptografadas antes de serem salvas no PostgreSQL. Em produção, configure `DATABASE_URL`, `APP_SESSION_SECRET` e `APP_ENCRYPTION_KEY` nas variáveis privadas da hospedagem. O projeto Vercel usa PostgreSQL gerenciado pelo Neon; não coloque essas chaves no GitHub.

O formulário guarda a chave e o Workspace da Hodle, mas **não ativa a automação financeira**: os métodos de criar Pix, converter e enviar cripto em `hodle.py` continuam aguardando a documentação oficial da API. O modo automático permanece desligado até essa integração ser implementada e testada.

### 1. Carteira cripto

```env
WALLET_ASSET=USDC
WALLET_NETWORK=Base
WALLET_ADDRESS=0xSEU_ENDERECO_PUBLICO
```

| Campo | Valores aceitos |
|---|---|
| `WALLET_ASSET` | `USDC` ou `USDT` |
| `WALLET_NETWORK` | `Base`, `Ethereum`, `Solana` ou `Tron` |
| `WALLET_ADDRESS` | endereço **público** da carteira, no formato da rede |

Para trocar de carteira: edite essas três linhas, salve e reinicie o app. Cobranças já criadas mantêm a carteira que tinham quando foram geradas. Use sempre o endereço **da mesma rede** configurada, senão os fundos podem ser perdidos. **Nunca** coloque frase de recuperação ou chave privada.

### 2. API da Hodle

1. No painel da Hodle, abra **APIs e webhooks > API Keys** e crie uma chave (use a chave de **sandbox** para testar primeiro).
2. Cole no `.env`:

```env
HODLE_API_KEY=sua_chave_aqui
HODLE_LIVE=0
```

3. Quando o `hodle.py` estiver implementado e testado, mude para `HODLE_LIVE=1` para ligar o modo automático.

### 3. Webhook da Hodle

O webhook é como a Hodle avisa o sistema que o Pix foi pago.

1. Coloque o app num endereço **HTTPS público** (por exemplo, um VPS com domínio e proxy reverso).
2. No painel da Hodle, abra **APIs e webhooks > Webhooks** e cadastre esta URL:

```
https://SEU_DOMINIO/webhook/hodle
```

3. Se a Hodle mostrar uma chave secreta do webhook, cole no `.env`:

```env
HODLE_WEBHOOK_SECRET=sua_chave_secreta_aqui
```

4. Ajuste também no `.env`:

```env
BASE_URL=https://SEU_DOMINIO
FORCE_HTTPS=1
```

Enquanto `hodle.py` não validar a assinatura, o webhook **rejeita todas as chamadas** (comportamento seguro de propósito).

### 4. API do WhatsApp (envio automático)

O envio automático usa a **API oficial do WhatsApp Cloud (Meta)**.

1. Crie um app em [developers.facebook.com](https://developers.facebook.com) e adicione o produto **WhatsApp**.
2. Copie o **token de acesso** e o **ID do número de telefone**.
3. Em **Modelos de mensagem**, crie e envie para aprovação um modelo chamado `cobranca_mensal`, idioma `pt_BR`, categoria utilidade, com este corpo:

```
Olá, {{1}}! Sua cobrança de R$ {{2}} está disponível: {{3}}
```

4. Cole no `.env`:

```env
WA_TOKEN=seu_token_aqui
WA_PHONE_ID=seu_id_do_numero
WA_TEMPLATE=cobranca_mensal
WA_LANG=pt_BR
```

Sem `WA_TOKEN` e `WA_PHONE_ID`, o sistema continua funcionando e você envia a cobrança pelo botão **WhatsApp** do painel. A Meta cobra por mensagem enviada.

## Outras variáveis

| Variável | Função |
|---|---|
| `APP_SESSION_SECRET` | chave persistente que protege a sessão |\n| `APP_ENCRYPTION_KEY` | chave usada para criptografar as credenciais salvas |\n| `DATABASE_URL` | conexão PostgreSQL persistente (Neon) |
| `ADMIN_USER` | usuário do login do painel |
| `ADMIN_PASS_HASH` | hash da senha (gerado por `gerar_hash.py`, entre aspas simples) |
| `MIN_SUB_BRL` | valor mínimo de assinatura (padrão 100) |
| `MAX_BRL` | valor máximo por cobrança (padrão 2000) |
| `DB_PATH` | arquivo do banco SQLite |

## Segurança: antes de subir para o GitHub

- **Nunca envie o `.env`.** Ele tem chaves e senhas. Use o `.gitignore` que acompanha o projeto.
- Se uma chave ou senha já foi commitada ou colada em algum lugar público, **troque na hora** (gere outra chave de API, outra senha, outro token).
- Confira antes do commit: `git status` não pode listar `.env` nem `*.db`.
- Mantenha o repositório **privado** se houver qualquer dado de clientes.
- Use uma senha de admin forte e exclusiva para este painel.

## Estrutura

```
app.py            painel, assinaturas, webhook
hodle.py          integração com a Hodle (preencher com a doc oficial)
whatsapp.py       envio pela API oficial do WhatsApp
gerar_hash.py     gera o hash da senha do admin
templates/        telas (login, painel, página de pagamento)
static/           estilo
.env.example      modelo de configuração (sem segredos)
```

## Solução de problemas

- **O login não sai da tela:** use `BASE_URL=http://localhost:5000` e `FORCE_HTTPS=0` fora de HTTPS, gere o hash com `python gerar_hash.py` e confira `ADMIN_USER`.
- **Cobrança mensal não saiu:** a execução automática recorrente ainda precisa ser ligada a um Vercel Cron/Workflow; uma thread em segundo plano não é executada de forma confiável em funções serverless.
- **WhatsApp não chegou:** confira se o modelo foi aprovado pela Meta e se o token está válido. O erro aparece na linha da cobrança.

## Aviso

Este projeto lida com pagamentos reais. Teste primeiro com valores pequenos e na sandbox. Receber e converter em cripto pode ter obrigações fiscais e regulatórias, então confirme as regras que se aplicam a você.
