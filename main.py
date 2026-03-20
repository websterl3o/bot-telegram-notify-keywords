import json
import datetime
import os
import sqlite3
import uuid
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Updater, CommandHandler, MessageHandler, Filters,
    CallbackContext, CallbackQueryHandler, ConversationHandler,
)

DB_FILE = 'bot_data.db'

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

def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS keyword_groups (
                id TEXT PRIMARY KEY,
                owner_user_id INTEGER NOT NULL,
                keywords_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                notification_count INTEGER NOT NULL DEFAULT 0
            )
            '''
        )
        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS observed_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                from_user_id INTEGER,
                sender_name TEXT,
                chat_name TEXT,
                text TEXT NOT NULL,
                date TEXT NOT NULL,
                UNIQUE(chat_id, message_id)
            )
            '''
        )
        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS keyword_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword_group_id TEXT NOT NULL,
                observed_message_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(keyword_group_id, observed_message_id),
                FOREIGN KEY(keyword_group_id) REFERENCES keyword_groups(id) ON DELETE CASCADE,
                FOREIGN KEY(observed_message_id) REFERENCES observed_messages(id) ON DELETE CASCADE
            )
            '''
        )
        conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_keyword_owner ON keyword_groups(owner_user_id)'
        )
        conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_match_group ON keyword_matches(keyword_group_id)'
        )


# ─── Helpers ──────────────────────────────────────────────────────────────────

def row_to_group_dict(row: sqlite3.Row) -> dict:
    return {
        'id': row['id'],
        'owner_user_id': row['owner_user_id'],
        'keywords': json.loads(row['keywords_json']),
        'created_at': row['created_at'],
        'updated_at': row['updated_at'],
        'notification_count': row['notification_count'],
    }


def get_user_groups(owner_user_id: int) -> list:
    with get_conn() as conn:
        rows = conn.execute(
            '''
            SELECT id, owner_user_id, keywords_json, created_at, updated_at, notification_count
            FROM keyword_groups
            WHERE owner_user_id = ?
            ORDER BY created_at DESC
            ''',
            (owner_user_id,),
        ).fetchall()
    return [row_to_group_dict(row) for row in rows]


def get_group_by_id(group_id: str, owner_user_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            '''
            SELECT id, owner_user_id, keywords_json, created_at, updated_at, notification_count
            FROM keyword_groups
            WHERE id = ? AND owner_user_id = ?
            ''',
            (group_id, owner_user_id),
        ).fetchone()
    if not row:
        return None
    return row_to_group_dict(row)


def get_all_keywords_for_user(owner_user_id: int, exclude_id: str = None) -> set:
    groups = get_user_groups(owner_user_id)
    result = set()
    for group in groups:
        if exclude_id and group['id'] == exclude_id:
            continue
        for keyword in group['keywords']:
            result.add(keyword.lower())
    return result


def create_group(owner_user_id: int, keywords: list) -> None:
    now = datetime.datetime.utcnow().isoformat()
    with get_conn() as conn:
        conn.execute(
            '''
            INSERT INTO keyword_groups (id, owner_user_id, keywords_json, created_at, updated_at, notification_count)
            VALUES (?, ?, ?, ?, ?, 0)
            ''',
            (str(uuid.uuid4()), owner_user_id, json.dumps(keywords, ensure_ascii=False), now, now),
        )


def update_group_keywords(group_id: str, owner_user_id: int, keywords: list) -> bool:
    now = datetime.datetime.utcnow().isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            '''
            UPDATE keyword_groups
            SET keywords_json = ?, updated_at = ?
            WHERE id = ? AND owner_user_id = ?
            ''',
            (json.dumps(keywords, ensure_ascii=False), now, group_id, owner_user_id),
        )
    return cur.rowcount > 0


def delete_group(group_id: str, owner_user_id: int) -> bool:
    with get_conn() as conn:
        conn.execute('DELETE FROM keyword_matches WHERE keyword_group_id = ?', (group_id,))
        cur = conn.execute(
            'DELETE FROM keyword_groups WHERE id = ? AND owner_user_id = ?',
            (group_id, owner_user_id),
        )
    return cur.rowcount > 0


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


def contains_keywords(text: str, keywords: list) -> bool:
    text_lower = text.lower()
    return any(keyword in text_lower for keyword in keywords)


def save_observed_message(message) -> int:
    now = datetime.datetime.utcnow().isoformat()
    chat_name = (
        getattr(message.chat, 'title', None)
        or getattr(message.chat, 'username', None)
        or str(message.chat.id)
    )
    sender_name = message.from_user.full_name if message.from_user else 'Desconhecido'

    with get_conn() as conn:
        conn.execute(
            '''
            INSERT OR IGNORE INTO observed_messages
            (chat_id, message_id, from_user_id, sender_name, chat_name, text, date)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                message.chat.id,
                message.message_id,
                message.from_user.id if message.from_user else None,
                sender_name,
                chat_name,
                message.text,
                now,
            ),
        )
        row = conn.execute(
            'SELECT id FROM observed_messages WHERE chat_id = ? AND message_id = ?',
            (message.chat.id, message.message_id),
        ).fetchone()
    return row['id']


def insert_match_if_needed(group_id: str, observed_message_id: int) -> bool:
    now = datetime.datetime.utcnow().isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            '''
            INSERT OR IGNORE INTO keyword_matches (keyword_group_id, observed_message_id, created_at)
            VALUES (?, ?, ?)
            ''',
            (group_id, observed_message_id, now),
        )
        if cur.rowcount > 0:
            conn.execute(
                '''
                UPDATE keyword_groups
                SET notification_count = notification_count + 1,
                    updated_at = ?
                WHERE id = ?
                ''',
                (now, group_id),
            )
            return True
    return False


def get_group_related_messages(group_id: str, owner_user_id: int, limit: int = 20) -> list:
    with get_conn() as conn:
        rows = conn.execute(
            '''
            SELECT om.chat_name, om.sender_name, om.text, om.date
            FROM keyword_matches km
            JOIN keyword_groups kg ON kg.id = km.keyword_group_id
            JOIN observed_messages om ON om.id = km.observed_message_id
            WHERE kg.id = ? AND kg.owner_user_id = ?
            ORDER BY om.id DESC
            LIMIT ?
            ''',
            (group_id, owner_user_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def get_all_messages_for_owner(owner_user_id: int, limit: int = 20) -> list:
    with get_conn() as conn:
        rows = conn.execute(
            '''
            SELECT kg.keywords_json, om.chat_name, om.sender_name, om.text, om.date
            FROM keyword_matches km
            JOIN keyword_groups kg ON kg.id = km.keyword_group_id
            JOIN observed_messages om ON om.id = km.observed_message_id
            WHERE kg.owner_user_id = ?
            ORDER BY om.id DESC
            LIMIT ?
            ''',
            (owner_user_id, limit),
        ).fetchall()
    parsed = []
    for row in rows:
        parsed.append({
            'keywords': json.loads(row['keywords_json']),
            'chat': row['chat_name'],
            'from': row['sender_name'],
            'text': row['text'],
            'date': row['date'],
        })
    return parsed


def reanalyze_group(group_id: str, owner_user_id: int) -> tuple[int, int] | None:
    group = get_group_by_id(group_id, owner_user_id)
    if not group:
        return None

    with get_conn() as conn:
        conn.execute('DELETE FROM keyword_matches WHERE keyword_group_id = ?', (group_id,))
        rows = conn.execute(
            'SELECT id, text FROM observed_messages WHERE text IS NOT NULL'
        ).fetchall()

        total = len(rows)
        matched = 0
        now = datetime.datetime.utcnow().isoformat()
        for row in rows:
            if contains_keywords(row['text'], group['keywords']):
                conn.execute(
                    '''
                    INSERT INTO keyword_matches (keyword_group_id, observed_message_id, created_at)
                    VALUES (?, ?, ?)
                    ''',
                    (group_id, row['id'], now),
                )
                matched += 1

        conn.execute(
            '''
            UPDATE keyword_groups
            SET notification_count = ?, updated_at = ?
            WHERE id = ? AND owner_user_id = ?
            ''',
            (matched, now, group_id, owner_user_id),
        )

    return matched, total


def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('➕ Cadastrar palavra-chave', callback_data='menu_add')],
        [InlineKeyboardButton('📋 Listar palavras-chave', callback_data='menu_list')],
        [InlineKeyboardButton('💬 Ver lista de mensagens', callback_data='menu_messages')],
    ])


# ─── /start ───────────────────────────────────────────────────────────────────

def start(update: Update, context: CallbackContext) -> None:
    init_db()
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
    owner_user_id = update.effective_user.id
    keywords = parse_keywords_input(update.message.text)
    if not keywords:
        update.message.reply_text('Nenhuma palavra-chave válida. Tente novamente ou /cancel.')
        return ADD_KEYWORD

    existing = get_all_keywords_for_user(owner_user_id)
    duplicates = [kw for kw in keywords if kw in existing]
    if duplicates:
        update.message.reply_text(
            f'⚠️ As palavras já estão cadastradas: *{", ".join(duplicates)}*\nOperação cancelada.',
            parse_mode='Markdown',
        )
        return ConversationHandler.END

    create_group(owner_user_id, keywords)
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
    owner_user_id = query.from_user.id
    groups = get_user_groups(owner_user_id)
    if not groups:
        query.edit_message_text(
            'Nenhuma palavra-chave cadastrada.',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('◀️ Voltar', callback_data='menu_back')]]),
        )
        return

    keyboard = [
        [InlineKeyboardButton(f'🔍 {" | ".join(group["keywords"])}', callback_data=f'view_{group["id"]}')]
        for group in groups
    ]
    keyboard.append([InlineKeyboardButton('◀️ Voltar', callback_data='menu_back')])
    query.edit_message_text('📋 Palavras-chave cadastradas:', reply_markup=InlineKeyboardMarkup(keyboard))


# ─── Detalhe de uma palavra-chave ─────────────────────────────────────────────

def view_keyword_detail(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    query.answer()
    entry_id = query.data[len('view_'):]
    owner_user_id = query.from_user.id

    entry = get_group_by_id(entry_id, owner_user_id)
    if not entry:
        query.edit_message_text('Entrada não encontrada (ou não pertence a você).')
        return

    related_messages = get_group_related_messages(entry_id, owner_user_id, limit=1)
    msg_count = entry['notification_count'] if entry['notification_count'] else len(related_messages)
    text = (
        f'🔑 *Keywords:* {" | ".join(entry["keywords"])}\n'
        f'📅 *Criada em:* {entry["created_at"]}\n'
        f'🔄 *Última atualização:* {entry["updated_at"]}\n'
        f'🔔 *Notificações:* {entry["notification_count"]}\n'
        f'💬 *Mensagens relacionadas:* {msg_count}'
    )
    keyboard = [
        [InlineKeyboardButton('💬 Ver mensagens relacionadas', callback_data=f'msgs_{entry_id}')],
        [InlineKeyboardButton('🔄 Buscar mensagens novamente', callback_data=f'recheck_{entry_id}')],
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
    owner_user_id = query.from_user.id

    entry = get_group_by_id(entry_id, owner_user_id)
    if not entry:
        query.edit_message_text('Entrada não encontrada (ou não pertence a você).')
        return

    messages = get_group_related_messages(entry_id, owner_user_id, limit=20)
    if not messages:
        text = f'Nenhuma mensagem capturada ainda para: *{" | ".join(entry["keywords"])}*'
    else:
        lines = [f'💬 *Mensagens — {" | ".join(entry["keywords"])}:*\n']
        for i, msg in enumerate(messages[-20:], 1):
            lines.append(
                f'*{i}.* [{msg.get("date", "")[:19]}] '
                f'*{msg.get("sender_name", "?")}* em _{msg.get("chat_name", "?")}:_\n'
                f'  {msg.get("text", "")}'
            )
        text = '\n'.join(lines)

    keyboard = [[InlineKeyboardButton('◀️ Voltar', callback_data=f'view_{entry_id}')]]
    query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))


# ─── Ver todas as mensagens ───────────────────────────────────────────────────

def view_all_messages(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    query.answer()
    owner_user_id = query.from_user.id
    all_msgs = get_all_messages_for_owner(owner_user_id, limit=20)

    if not all_msgs:
        text = 'Nenhuma mensagem capturada ainda.'
    else:
        lines = [f'💬 *Todas as mensagens capturadas ({len(all_msgs)}):*\n']
        for msg in all_msgs:
            lines.append(
                f'🔑 _{" | ".join(msg["keywords"])}_\n'
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
    owner_user_id = query.from_user.id

    entry = get_group_by_id(entry_id, owner_user_id)
    if not entry:
        query.edit_message_text('Entrada não encontrada (ou não pertence a você).')
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
    owner_user_id = update.effective_user.id
    keywords = parse_keywords_input(update.message.text)

    if not keywords:
        update.message.reply_text('Nenhuma palavra-chave válida. Tente novamente ou /cancel.')
        return EDIT_KEYWORD

    entry = get_group_by_id(entry_id, owner_user_id)
    if not entry:
        update.message.reply_text('Entrada não encontrada (ou não pertence a você).')
        return ConversationHandler.END

    existing = get_all_keywords_for_user(owner_user_id, exclude_id=entry_id)
    duplicates = [kw for kw in keywords if kw in existing]
    if duplicates:
        update.message.reply_text(
            f'⚠️ Palavras já cadastradas em outro grupo: *{" | ".join(duplicates)}*\nOperação cancelada.',
            parse_mode='Markdown',
        )
        return ConversationHandler.END

    update_group_keywords(entry_id, owner_user_id, keywords)
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
    owner_user_id = query.from_user.id

    entry = get_group_by_id(entry_id, owner_user_id)
    if not entry:
        query.edit_message_text('Entrada não encontrada (ou não pertence a você).')
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
    owner_user_id = query.from_user.id

    deleted = delete_group(entry_id, owner_user_id)
    if not deleted:
        query.edit_message_text('Entrada não encontrada (ou não pertence a você).')
        return

    query.edit_message_text(
        '🗑️ Palavra-chave removida com sucesso.',
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('◀️ Voltar à lista', callback_data='menu_list')]]),
    )


def recheck_messages(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    query.answer()
    entry_id = query.data[len('recheck_'):]
    owner_user_id = query.from_user.id

    result = reanalyze_group(entry_id, owner_user_id)
    if not result:
        query.edit_message_text('Entrada não encontrada (ou não pertence a você).')
        return

    matched_count, total = result
    group = get_group_by_id(entry_id, owner_user_id)
    query.edit_message_text(
        f'✅ Reanálise concluída para *{" | ".join(group["keywords"])}*.\n\n'
        f'Encontradas *{matched_count}* mensagens entre *{total}* mensagens observadas pelo bot.',
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('◀️ Voltar', callback_data=f'view_{entry_id}')]]),
    )


# ─── Monitor de mensagens ─────────────────────────────────────────────────────

def monitor_messages(update: Update, context: CallbackContext) -> None:
    message = update.message
    if not message or not message.text:
        return

    observed_message_id = save_observed_message(message)
    groups_to_notify = []
    sender = message.from_user.full_name if message.from_user else 'Desconhecido'
    chat_title = (
        getattr(message.chat, 'title', None)
        or getattr(message.chat, 'username', None)
        or str(message.chat.id)
    )

    with get_conn() as conn:
        rows = conn.execute(
            '''
            SELECT id, owner_user_id, keywords_json
            FROM keyword_groups
            '''
        ).fetchall()

    for row in rows:
        keywords = json.loads(row['keywords_json'])
        if contains_keywords(message.text, keywords):
            inserted = insert_match_if_needed(row['id'], observed_message_id)
            if inserted:
                groups_to_notify.append({
                    'owner_user_id': row['owner_user_id'],
                    'keywords': keywords,
                })

    for item in groups_to_notify:
        try:
            context.bot.send_message(
                chat_id=item['owner_user_id'],
                text=(
                    f'🔔 *Palavra-chave detectada!*\n'
                    f'🔑 Keywords: *{" | ".join(item["keywords"])}*\n'
                    f'👤 De: {sender}\n'
                    f'💬 Chat: {chat_title}\n'
                    f'📝 Mensagem:\n  {message.text}'
                ),
                parse_mode='Markdown',
            )
        except Exception:
            # Usuário pode não ter iniciado chat com o bot ainda.
            continue


# ─── Cancel ───────────────────────────────────────────────────────────────────

def cancel(update: Update, context: CallbackContext) -> int:
    update.message.reply_text('Operação cancelada.', reply_markup=main_menu_keyboard())
    return ConversationHandler.END


# ─── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    if not API_TOKEN:
        raise ValueError('API_TOKEN não encontrado no .env')

    init_db()

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
