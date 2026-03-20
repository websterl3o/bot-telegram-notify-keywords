# Bot Telegram para notificar palavras-chave

Objetivo é criar um bot no telegram que leia suas mensagens em tempo real e te notifique quando um conjunto de palavras for dito em uma mensagem.

## Configuração

1. Clone o repositório:

```bash
    git clone git@github.com:websterl3o/bot-telegram-notify-keywords.git
```
2. Acesse o diretório do projeto:

```bash
    cd bot-telegram-notify-keywords
```

3. Instale as dependências:

```bash
    pip install python-telegram-bot==13.15 urllib3==1.26.20
    pip install APScheduler==3.11.2 --no-deps
```

4. Configure as variáveis de ambiente
```bash
    cp .env.example .env
```

Edite o arquivo `.env` e preencha as seguintes variáveis
```env
    API_TOKEN=seu_token_aqui
```

## Execução
Execute o bot:

```bash
    python main.py
```