import json
import datetime
import os
import uuid
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Updater, CommandHandler, MessageHandler, Filters,
    CallbackContext, CallbackQueryHandler, ConversationHandler,
)

DATA_FILE = 'keywords.json'

# Estados das conversas
ADD_KEYWORD = 1
EDIT_KEYWORD = 2


def load_env(path: str = '.env') -> None:
    try:
        with open(path, 'r') as env_file:
            for line in env_file:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, value = line.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip())
    except FileNotFoundError:
        pass


load_env()
API_TOKEN = os.getenv('API_TOKEN')


# ─── Persistência ─────────────────────────────────────────────────────────────

def load_data() -> list:
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            for entry in data:
                entry.setdefault('id', str(uuid.uuid4()))
                entry.setdefault('related_messages', [])
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_data(data: list) -> None:
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def get_all_keywords_flat(data: list, exclude_id: str = None) -> list:
    result = []
    for entry in data:
        if entry.get('id') == exclude_id:
            continue
        result.extend(kw.lower() for kw in entry.get('keywords', []))
    return result


def parse_keywords_input(user_input: str) -> list:
    user_input = user_input.strip()
    if user_input.startswith('['):
        try:
            parsed = json.loads(user_input)
            if isinstance(parsed, list):
                return [str(k).strip().lower() for k in parsed if str(k).strip()]
        except json.JSONDecodeError:
            pass
    return [kw.strip().lower() for kw in user_input.split(',') if kw.strip()]


def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('➕ Cadastrar palavra-chave', callback_data='menu_add')],
        [InlineKeyboardButton('📋 Listar palavras-chave', callback_data='menu_list')],
        [InlineKeyboardButton('💬 Ver lista de mensagens', callback_data='menu_messages')],
    ])


# ─── /start ───────────────────────────────────────────────────────────────────

def start(update: Update, context: CallbackContext) -> None:
    context.bot_data.setdefault('admin_chat_id', update.effective_chat.id)
    update.message.reply_text(
        'Bem-vindo! Escolha uma opção:',
        reply_markup=main_menu_keyboard(),
    )


def menu_back(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    query.answer()
    query.edit_message_text('Escolha uma opção:', reply_markup=main_menu_keyboard())


# ─── Cadastrar palavra-chave ──────────────────────────────────────────────────

def menu_add_callback(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    query.edit_message_text(
        '✏️ Envie as palavras-chave separadas por vírgula ou como array JSON.\n\n'
        'Exemplos:\n'
        '• `memoria, ram, ddr4`\n'
        '• `["memoria", "ram", "ddr4"]`\n\n'
        'Ou /cancel para cancelar.',
        parse_mode='Markdown',
    )
    return ADD_KEYWORD


def receive_keyword(update: Update, context: CallbackContext) -> int:
    keywords = parse_keywords_input(update.message.text)
    if not keywords:
        update.message.reply_text('Nenhuma palavra-chave válida. Tente novamente ou /cancel.')
        return ADD_KEYWORD

    data = load_data()
    existing = get_all_keywords_flat(data)
    duplicates = [kw for kw in keywords if kw in existing]
    if duplicates:
        update.message.reply_text(
            f'⚠️ As palavras já estão cadastradas: *{", ".join(duplicates)}*\nOperação cancelada.',
            parse_mode='Markdown',
        )
        return ConversationHandler.END

    now = datetime.datetime.utcnow().isoformat()
    data.append({
        'id': str(uuid.uuid4()),
        'keywords': keywords,
        'created_at': now,
        'updated_at': now,
        'notification_count': 0,
        'related_messages': [],
    })
    save_data(data)
    update.message.reply_text(
        f'✅ Cadastrado com sucesso!\nKeywords: *{", ".join(keywords)}*',
        parse_mode='Markdown',
        reply_markup=main_menu_keyboard(),
    )
    return ConversationHandler.END


# ─── Listar palavras-chave ────────────────────────────────────────────────────

def list_keywords_view(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    query.answer()
    data = load_data()
    if not data:
        query.edit_message_text(
            'Nenhuma palavra-chave cadastrada.',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('◀️ Voltar', callback_data='menu_back')]]),
        )
        return

    keyboard = [
        [InlineKeyboardButton(f'🔍 {" | ".join(e["keywords"])}', callback_data=f'view_{e["id"]}')]
        for e in data
    ]
    keyboard.append([InlineKeyboardButton('◀️ Voltar', callback_data='menu_back')])
    query.edit_message_text('📋 Palavras-chave cadastradas:', reply_markup=InlineKeyboardMarkup(keyboard))


# ─── Detalhe de uma palavra-chave ─────────────────────────────────────────────

def view_keyword_detail(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    query.answer()
    entry_id = query.data[len('view_'):]

    data = load_data()
    entry = next((e for e in data if e['id'] == entry_id), None)
    if not entry:
        query.edit_message_text('Entrada não encontrada.')
        return

    msg_count = len(entry.get('related_messages', []))
    text = (
        f'🔑 *Keywords:* {" | ".join(entry["keywords"])}\n'
        f'📅 *Criada em:* {entry["created_at"]}\n'
        f'🔄 *Última atualização:* {entry["updated_at"]}\n'
        f'🔔 *Notificações:* {entry["notification_count"]}\n'
        f'💬 *Mensagens relacionadas:* {msg_count}'
    )
    keyboard = [
        [InlineKeyboardButton('💬 Ver mensagens relacionadas', callback_data=f'msgs_{entry_id}')],
        [
            InlineKeyboardButton('✏️ Editar', callback_data=f'edit_{entry_id}'),
            InlineKeyboardButton('🗑️ Remover', callback_data=f'del_{entry_id}'),
        ],
        [InlineKeyboardButton('◀️ Voltar', callback_data='menu_list')],
    ]
    query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))


# ─── Mensagens relacionadas ───────────────────────────────────────────────────

def view_related_messages(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    query.answer()
    entry_id = query.data[len('msgs_'):]

    data = load_data()
    entry = next((e for e in data if e['id'] == entry_id), None)
    if not entry:
        query.edit_message_text('Entrada não encontrada.')
        return

    messages = entry.get('related_messages', [])
    if not messages:
        text = f'Nenhuma mensagem capturada ainda para: *{" | ".join(entry["keywords"])}*'
    else:
        lines = [f'💬 *Mensagens — {" | ".join(entry["keywords"])}:*\n']
        for i, msg in enumerate(messages[-20:], 1):
            lines.append(
                f'*{i}.* [{msg.get("date", "")[:19]}] '
                f'*{msg.get("from", "?")}* em _{msg.get("chat", "?")}:_\n'
                f'  {msg.get("text", "")}'
            )
        text = '\n'.join(lines)

    keyboard = [[InlineKeyboardButton('◀️ Voltar', callback_data=f'view_{entry_id}')]]
    query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))


# ─── Ver todas as mensagens ───────────────────────────────────────────────────

def view_all_messages(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    query.answer()
    data = load_data()

    all_msgs = []
    for entry in data:
        for msg in entry.get('related_messages', []):
            all_msgs.append((entry['keywords'], msg))

    if not all_msgs:
        text = 'Nenhuma mensagem capturada ainda.'
    else:
        lines = [f'💬 *Todas as mensagens capturadas ({len(all_msgs)}):*\n']
        for kws, msg in all_msgs[-20:]:
            lines.append(
                f'🔑 _{" | ".join(kws)}_\n'
                f'[{msg.get("date", "")[:19]}] *{msg.get("from", "?")}* em _{msg.get("chat", "?")}:_\n'
                f'  {msg.get("text", "")}\n'
            )
        text = '\n'.join(lines)

    keyboard = [[InlineKeyboardButton('◀️ Voltar', callback_data='menu_back')]]
    query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))


# ─── Editar palavra-chave ─────────────────────────────────────────────────────

def edit_keyword_prompt(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    entry_id = query.data[len('edit_'):]

    data = load_data()
    entry = next((e for e in data if e['id'] == entry_id), None)
    if not entry:
        query.edit_message_text('Entrada não encontrada.')
        return ConversationHandler.END

    context.user_data['editing_id'] = entry_id
    query.edit_message_text(
        f'Palavras atuais: *{" | ".join(entry["keywords"])}*\n\n'
        'Envie as novas palavras-chave (separadas por vírgula ou JSON array):\n'
        'Ou /cancel para cancelar.',
        parse_mode='Markdown',
    )
    return EDIT_KEYWORD


def receive_edit_keyword(update: Update, context: CallbackContext) -> int:
    entry_id = context.user_data.get('editing_id')
    keywords = parse_keywords_input(update.message.text)

    if not keywords:
        update.message.reply_text('Nenhuma palavra-chave válida. Tente novamente ou /cancel.')
        return EDIT_KEYWORD

    data = load_data()
    entry = next((e for e in data if e['id'] == entry_id), None)
    if not entry:
        update.message.reply_text('Entrada não encontrada.')
        return ConversationHandler.END

    existing = get_all_keywords_flat(data, exclude_id=entry_id)
    duplicates = [kw for kw in keywords if kw in existing]
    if duplicates:
        update.message.reply_text(
            f'⚠️ Palavras já cadastradas em outro grupo: *{" | ".join(duplicates)}*\nOperação cancelada.',
            parse_mode='Markdown',
        )
        return ConversationHandler.END

    entry['keywords'] = keywords
    entry['updated_at'] = datetime.datetime.utcnow().isoformat()
    save_data(data)
    update.message.reply_text(
        f'✅ Palavras-chave atualizadas: *{" | ".join(keywords)}*',
        parse_mode='Markdown',
        reply_markup=main_menu_keyboard(),
    )
    return ConversationHandler.END


# ─── Remover palavra-chave ────────────────────────────────────────────────────

def delete_keyword_confirm(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    query.answer()
    entry_id = query.data[len('del_'):]

    data = load_data()
    entry = next((e for e in data if e['id'] == entry_id), None)
    if not entry:
        query.edit_message_text('Entrada não encontrada.')
        return

    keyboard = [[
        InlineKeyboardButton('✅ Confirmar', callback_data=f'delok_{entry_id}'),
        InlineKeyboardButton('❌ Cancelar', callback_data=f'view_{entry_id}'),
    ]]
    query.edit_message_text(
        f'Deseja remover *{" | ".join(entry["keywords"])}*?',
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


def delete_keyword_execute(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    query.answer()
    entry_id = query.data[len('delok_'):]

    data = load_data()
    new_data = [e for e in data if e['id'] != entry_id]
    save_data(new_data)
    query.edit_message_text(
        '🗑️ Palavra-chave removida com sucesso.',
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('◀️ Voltar à lista', callback_data='menu_list')]]),
    )


# ─── Monitor de mensagens ─────────────────────────────────────────────────────

def monitor_messages(update: Update, context: CallbackContext) -> None:
    message = update.message
    if not message or not message.text:
        return

    text_lower = message.text.lower()
    data = load_data()
    matched_entries = []

    for entry in data:
        if any(kw in text_lower for kw in entry['keywords']):
            now = datetime.datetime.utcnow().isoformat()
            chat_title = (
                getattr(message.chat, 'title', None)
                or getattr(message.chat, 'username', None)
                or str(message.chat.id)
            )
            sender = message.from_user.full_name if message.from_user else 'Desconhecido'
            entry['notification_count'] += 1
            entry['updated_at'] = now
            entry['related_messages'].append({
                'text': message.text,
                'from': sender,
                'chat': chat_title,
                'date': now,
                'message_id': message.message_id,
            })
            matched_entries.append((entry, sender, chat_title))

    if matched_entries:
        save_data(data)
        admin_chat_id = context.bot_data.get('admin_chat_id')
        if admin_chat_id:
            for entry, sender, chat_title in matched_entries:
                context.bot.send_message(
                    chat_id=admin_chat_id,
                    text=(
                        f'🔔 *Palavra-chave detectada!*\n'
                        f'🔑 Keywords: *{" | ".join(entry["keywords"])}*\n'
                        f'👤 De: {sender}\n'
                        f'💬 Chat: {chat_title}\n'
                        f'📝 Mensagem:\n  {message.text}'
                    ),
                    parse_mode='Markdown',
                )


# ─── Cancel ───────────────────────────────────────────────────────────────────

def cancel(update: Update, context: CallbackContext) -> int:
    update.message.reply_text('Operação cancelada.', reply_markup=main_menu_keyboard())
    return ConversationHandler.END


# ─── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    if not API_TOKEN:
        raise ValueError('API_TOKEN não encontrado no .env')

    updater = Updater(API_TOKEN)
    dp = updater.dispatcher

    add_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_add_callback, pattern='^menu_add$')],
        states={ADD_KEYWORD: [MessageHandler(Filters.text & ~Filters.command, receive_keyword)]},
        fallbacks=[CommandHandler('cancel', cancel)],
        per_message=False,
    )

    edit_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(edit_keyword_prompt, pattern='^edit_')],
        states={EDIT_KEYWORD: [MessageHandler(Filters.text & ~Filters.command, receive_edit_keyword)]},
        fallbacks=[CommandHandler('cancel', cancel)],
        per_message=False,
    )

    dp.add_handler(CommandHandler('start', start))
    dp.add_handler(add_conv)
    dp.add_handler(edit_conv)

    dp.add_handler(CallbackQueryHandler(menu_back,              pattern='^menu_back$'))
    dp.add_handler(CallbackQueryHandler(list_keywords_view,     pattern='^menu_list$'))
    dp.add_handler(CallbackQueryHandler(view_all_messages,      pattern='^menu_messages$'))
    dp.add_handler(CallbackQueryHandler(view_keyword_detail,    pattern='^view_'))
    dp.add_handler(CallbackQueryHandler(view_related_messages,  pattern='^msgs_'))
    dp.add_handler(CallbackQueryHandler(recheck_messages,       pattern='^recheck_'))
    dp.add_handler(CallbackQueryHandler(delete_keyword_confirm, pattern='^del_'))
    dp.add_handler(CallbackQueryHandler(delete_keyword_execute, pattern='^delok_'))

    # Monitor de mensagens (deve ser o último handler)
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, monitor_messages))

    updater.start_polling(allowed_updates=Update.ALL_TYPES)
    updater.idle()


if __name__ == '__main__':
    main()
