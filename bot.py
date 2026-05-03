print("BOT INICIADO")

import os
import sqlite3
import random
import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram import ReplyKeyboardRemove
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

load_dotenv()


def normalize_chat_id(raw_value):
    value = str(raw_value or "").strip()
    if not value:
        return None
    if value.startswith("@"):
        return value
    if value.lstrip("-").isdigit():
        if value.startswith("-100"):
            return int(value)
        if value.startswith("-"):
            return int(f"-100{value[1:]}")
        return int(value)
    return value


# ================= CONFIG =================
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

# Manejo seguro de IDs de administrador (SUPER ADMINS)
admin_env = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(x.strip()) for x in admin_env.split(",") if x.strip()]

LOG_GROUP_ID = os.getenv("LOG_GROUP_ID", "").strip()
CHANNEL_URL = os.getenv("CHANNEL_URL", "").strip()
CHANNEL_ID = os.getenv("CHANNEL_ID", "").strip()
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "").strip()


# ================= DB =================
@contextmanager
def get_db_connection():
    conn = sqlite3.connect("bot.db", check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            name TEXT,
            username TEXT,
            credits INTEGER DEFAULT 0,
            is_banned INTEGER DEFAULT 0,
            is_admin INTEGER DEFAULT 0,
            is_pending INTEGER DEFAULT 0,
            fecha_registro DATETIME DEFAULT (datetime('now', 'localtime')),
            ultima_recarga DATETIME,
            aprobado_en DATETIME
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS stock (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item TEXT
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            cantidad INTEGER,
            items TEXT,
            fecha DATETIME DEFAULT (datetime('now', 'localtime')),
            compra_id TEXT,
            expiracion DATETIME
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """)
        cursor.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('dias_vigencia', '30')"
        )

        # Migraciones para bases de datos antiguas
        try:
            cursor.execute("ALTER TABLE history ADD COLUMN compra_id TEXT")
        except:
            pass
        try:
            cursor.execute("ALTER TABLE history ADD COLUMN expiracion DATETIME")
        except:
            pass
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN is_banned INTEGER DEFAULT 0")
        except:
            pass
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0")
        except:
            pass
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN is_pending INTEGER DEFAULT 0")
        except:
            pass
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN fecha_registro DATETIME")
        except:
            pass
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN ultima_recarga DATETIME")
        except:
            pass
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN aprobado_en DATETIME")
        except:
            pass

        cursor.execute("PRAGMA table_info(users)")
        user_columns = {row[1] for row in cursor.fetchall()}
        if "is_pending" in user_columns:
            cursor.execute("UPDATE users SET is_pending=0 WHERE is_pending IS NULL")
        if "fecha_registro" in user_columns:
            cursor.execute(
                "UPDATE users SET fecha_registro=datetime('now', 'localtime') WHERE fecha_registro IS NULL OR fecha_registro=''"
            )
        for admin_id in ADMIN_IDS:
            cursor.execute(
                "UPDATE users SET is_pending=0, is_admin=1, aprobado_en=datetime('now', 'localtime') WHERE id=?",
                (str(admin_id),),
            )

        conn.commit()


init_db()
comprar_lock = asyncio.Lock()


# ================= HELPERS =================
def is_super_admin(uid):
    """Verifica si es el dueño absoluto (configurado en .env)"""
    return int(uid) in ADMIN_IDS


def is_admin(uid):
    """Verifica si es dueño absoluto O admin añadido en la BD"""
    if int(uid) in ADMIN_IDS:
        return True
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT is_admin FROM users WHERE id=?", (str(uid),))
        res = cursor.fetchone()
        return res[0] == 1 if res else False


def stock_count():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM stock")
        return cursor.fetchone()[0]


def gen_id():
    return str(random.randint(100000, 999999))


def buscar_usuario_por_arg(arg):
    arg = str(arg).strip()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, name, username, credits FROM users WHERE id=?",
            (arg.replace("@", ""),),
        )
        res = cursor.fetchone()
        if res:
            return res
        clean_user = arg.replace("@", "").lower()
        cursor.execute(
            "SELECT id, name, username, credits FROM users WHERE lower(username)=? OR lower(username)=?",
            (clean_user, "@" + clean_user),
        )
        return cursor.fetchone()


def is_user_banned(uid):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT is_banned FROM users WHERE id=?", (uid,))
        res = cursor.fetchone()
        return res[0] == 1 if res else False


def is_user_pending(uid):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT is_pending FROM users WHERE id=?", (str(uid),))
        res = cursor.fetchone()
        return res[0] == 1 if res else False


def get_setting(key):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key=?", (key,))
        res = cursor.fetchone()
        return res[0] if res else None


def set_setting(key, value):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        conn.commit()


def format_datetime(value):
    if not value:
        return "No disponible"
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").strftime(
            "%d/%m/%Y %H:%M:%S"
        )
    except Exception:
        return str(value)


def pending_users_text():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, name, username, fecha_registro FROM users WHERE is_pending=1 ORDER BY fecha_registro ASC"
        )
        pending_users = cursor.fetchall()

    if not pending_users:
        return "✅ No hay usuarios pendientes de aprobación."

    texto = "⏳ <b>USUARIOS PENDIENTES DE APROBACIÓN</b>\n\n"
    for user in pending_users:
        uname = (
            f"@{user[2]}" if user[2] and user[2] != "sin_username" else "Sin username"
        )
        texto += (
            f"👤 {user[1]} ({uname})\n"
            f"🆔 <code>{user[0]}</code>\n"
            f"🕒 Registro: {format_datetime(user[3])}\n\n"
        )
    texto += "💡 Usa /aprobar ID o @usuario para aprobar uno."
    return texto


async def remove_legacy_menu_once(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_data = context.user_data
    if user_data.get("legacy_menu_removed"):
        return
    message = update.effective_message
    if not message:
        return
    await message.reply_text(
        "✅ Menú actualizado. El teclado fijo ya no se usa.",
        reply_markup=ReplyKeyboardRemove(),
    )
    user_data["legacy_menu_removed"] = True


def user_menu_keyboard(uid=0):
    kb = [
        [
            InlineKeyboardButton("👤 Mi perfil", callback_data="main_me"),
            InlineKeyboardButton("💰 Recargar", callback_data="main_buy"),
        ],
        [
            InlineKeyboardButton("📜 Historial", callback_data="main_history"),
            InlineKeyboardButton("🆘 Ayuda", callback_data="help_start"),
        ],
    ]
    if is_admin(uid):
        kb.extend(
            [
                [
                    InlineKeyboardButton("👑 Admin", callback_data="admin_home"),
                    InlineKeyboardButton("📦 Productos", callback_data="stock_home"),
                ],
                [InlineKeyboardButton("👥 Usuarios", callback_data="users_menu")],
            ]
        )
    return InlineKeyboardMarkup(kb)


def cmds_text(uid):
    text = """
📜 <b>COMANDOS USUARIO</b>

/start - Inicia el bot y registra tu cuenta
/cmds - Muestra el menú principal
/ayuda - Pide ayuda al soporte
/me - Muestra tu perfil, créditos y stock
/buy - Muestra precios y cómo recargar
/comprar 1 - Compra 1 item y descuenta 1 crédito
/comprar 2 - Compra 2 items y descuenta 2 créditos
/comprar 3 - Compra 3 items y descuenta 3 créditos
/historia - Muestra tu historial de compras
"""
    if is_admin(uid):
        text += """
🛠 <b>COMANDOS ADMIN</b>

/admin - Abre el menú de admin
/productos - Abre el panel de productos
/users - Abre el panel de usuarios
/stock - Abre el panel de stock o agrega items
/anuncio TEXTO - Envía un DM a todos los usuarios
/canal TEXTO - Publica en el canal oficial
/testchats - Verifica canal y grupo debug
"""
    if is_super_admin(uid):
        text += """
👑 <b>COMANDOS DUEÑO (Super Admin)</b>

/addadmin ID/@user - Otorga permisos de Admin
/deladmin ID/@user - Quita permisos de Admin
/admins - Muestra la lista de Administradores
"""
    return text


async def send_menu_message(
    update: Update,
    text: str,
    reply_markup=None,
    parse_mode=ParseMode.HTML,
):
    query = update.callback_query
    message = update.effective_message
    if query:
        try:
            await query.edit_message_text(
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
            return
        except Exception as exc:
            if "Message is not modified" in str(exc):
                return
            if not message:
                return
            await message.reply_text(
                text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
            return
    if not message:
        return
    await message.reply_text(
        text,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
    )


def stock_menu_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📦 Ver productos", callback_data="stock_view"),
                InlineKeyboardButton("➕ Agregar", callback_data="stock_add_help"),
            ],
            [
                InlineKeyboardButton("🗑 Borrar N", callback_data="stock_del_help"),
                InlineKeyboardButton("⚙️ Vigencia", callback_data="admin_vigencia"),
            ],
            [
                InlineKeyboardButton("🔄 Ver activos", callback_data="stock_active"),
                InlineKeyboardButton("👑 Admin", callback_data="admin_home"),
            ],
            [
                InlineKeyboardButton("🏠 Menú", callback_data="back_cmds"),
            ],
        ]
    )


def stock_menu_text():
    total, text = stock_list_text(limit=5)
    return text + "\n\n📦 Usa /stock para agregar y /delstock N para borrar uno."


def users_menu_text():
    return (
        "👥 <b>MENÚ DE USUARIOS</b>\n\n"
        "/users - Ver todos los usuarios\n"
        "/info ID/@user - Ver info completa\n"
        "/addcred ID/@user CANTIDAD - Añadir créditos\n"
        "/delcred ID/@user CANTIDAD - Quitar créditos\n"
        "/compras ID/@user - Ver compras\n"
        "/panel - Ver cuentas activas\n"
        "/ban ID/@user - Banear\n"
        "/unban ID/@user - Desbanear\n"
        "/aprobar - Ver pendientes\n"
        "\n⬅️ Usa el botón volver para regresar al menú."
    )


def users_menu_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("👥 Ver usuarios", callback_data="users_view"),
                InlineKeyboardButton("⏳ Pendientes", callback_data="users_pending"),
            ],
            [
                InlineKeyboardButton("➕ Créditos", callback_data="users_addcred"),
                InlineKeyboardButton("➖ Quitar cred.", callback_data="users_delcred"),
            ],
            [
                InlineKeyboardButton("ℹ️ Info usuario", callback_data="users_info"),
                InlineKeyboardButton("🛍 Compras", callback_data="users_compras"),
            ],
            [
                InlineKeyboardButton("⛔ Ban", callback_data="users_ban"),
                InlineKeyboardButton("✅ Unban", callback_data="users_unban"),
            ],
            [
                InlineKeyboardButton("✅ Aprobar", callback_data="users_aprobar"),
                InlineKeyboardButton("👑 Admin", callback_data="admin_home"),
            ],
            [InlineKeyboardButton("🏠 Menú", callback_data="back_cmds")],
        ]
    )


def back_to_cmds_keyboard():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ Volver al menú", callback_data="back_cmds")]]
    )


def stock_list_text(limit=None):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        total = cursor.execute("SELECT COUNT(*) FROM stock").fetchone()[0]
        query = "SELECT id, item FROM stock ORDER BY id DESC"
        if limit is not None:
            query += f" LIMIT {int(limit)}"
        rows = cursor.execute(query).fetchall()

    if not rows:
        return (
            total,
            "📦 <b>Stock Actual:</b> Vacío.\n💡 Usa /stock para agregar items.",
        )

    texto = f"📦 <b>Stock Actual ({total} items):</b>\n"
    for idx, row in enumerate(rows, start=1):
        texto += f"{idx}. <code>{row[1]}</code> <i>(ID {row[0]})</i>\n"
    if total > len(rows):
        texto += f"\n... y {total - len(rows)} más"
    texto += "\n\n💡 Usa /delstock N para borrar un item por número."
    return total, texto


def parse_stock_items(raw_text):
    cleaned = (raw_text or "").replace("\r", "\n")
    cleaned = cleaned.replace("/stock", " ")
    cleaned = cleaned.replace(",", "\n").replace(";", "\n").replace("|", "\n")

    chunks = []
    for block in cleaned.split("\n"):
        block = block.strip()
        if not block:
            continue
        if " " in block:
            parts = [part.strip() for part in block.split() if part.strip()]
            chunks.extend(parts)
        else:
            chunks.append(block)

    return [item for item in chunks if item]


async def ensure_user_access(update: Update):
    uid = str(update.effective_user.id)
    message = update.effective_message
    if is_admin(uid):
        return True
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT is_pending, is_banned FROM users WHERE id=?", (uid,))
        row = cursor.fetchone()
    if not row:
        await message.reply_text("⚠️ Usa /start primero.")
        return False
    if row[1] == 1:
        await message.reply_text(
            "⛔️ <b>ACCESO DENEGADO</b>\nTu cuenta ha sido suspendida.",
            parse_mode=ParseMode.HTML,
        )
        return False
    if row[0] == 1:
        await message.reply_text(
            "⏳ <b>Cuenta pendiente</b>\nTu cuenta sigue esperando aprobación del administrador.",
            parse_mode=ParseMode.HTML,
        )
        return False
    return True


async def resolve_chat_target(bot, raw_chat_id):
    candidates = []
    normalized = normalize_chat_id(raw_chat_id)
    raw_value = str(raw_chat_id or "").strip()
    if normalized is not None:
        candidates.append(normalized)
    if raw_value:
        if raw_value.startswith("@"):
            candidates.append(raw_value)
        elif raw_value.lstrip("-").isdigit():
            candidates.append(int(raw_value))

    unique_candidates = []
    for candidate in candidates:
        if candidate not in unique_candidates:
            unique_candidates.append(candidate)

    errors = []
    for candidate in unique_candidates:
        try:
            chat = await bot.get_chat(candidate)
            return candidate, chat
        except Exception as exc:
            errors.append(f"{candidate}: {exc}")

    raise RuntimeError(" | ".join(errors) or "chat id vacio")


async def approve_user_if_admin(uid):
    uid = str(uid)
    if not is_super_admin(uid):
        return
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET is_pending=0, is_admin=1, aprobado_en=datetime('now', 'localtime') WHERE id=?",
            (uid,),
        )
        conn.commit()


async def send_debug_log(context: ContextTypes.DEFAULT_TYPE, text: str):
    if not LOG_GROUP_ID:
        return
    try:
        timestamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        chat_id, _ = await resolve_chat_target(context.bot, LOG_GROUP_ID)
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🔍 <b>AUDITORÍA</b> - {timestamp}\n\n{text}",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        print(f"Error enviando log a {LOG_GROUP_ID}: {e}")


# ================= ERROR =================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    err = str(context.error)
    print("ERROR CAUGHT:", err)
    if "'NoneType' object has no attribute 'reply_text'" in err:
        return
    await send_debug_log(context, f"⚠️ <b>ERROR DEL SISTEMA:</b>\n<code>{err}</code>")


# ================= START / REGISTER =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return
    uid = str(user.id)
    name = user.first_name or "Usuario"
    username = user.username if user.username else "sin_username"

    await approve_user_if_admin(uid)

    is_admin_user = is_super_admin(uid)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, is_pending FROM users WHERE id=?", (uid,))
        existing_user = cursor.fetchone()

        if is_admin_user:
            if existing_user:
                cursor.execute(
                    "UPDATE users SET is_pending=0, is_admin=1, aprobado_en=datetime('now', 'localtime') WHERE id=?",
                    (uid,),
                )
            else:
                cursor.execute(
                    "INSERT INTO users (id, name, username, credits, is_banned, is_admin, is_pending, fecha_registro, aprobado_en) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'), datetime('now', 'localtime'))",
                    (uid, name, username, 0, 0, 1, 0),
                )
            conn.commit()
            existing_user = (uid, 0)

        if not existing_user:
            # New user - insert as pending
            cursor.execute(
                "INSERT INTO users (id, name, username, credits, is_banned, is_admin, is_pending, fecha_registro) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))",
                (
                    uid,
                    name,
                    username,
                    0,
                    0,
                    1 if is_admin_user else 0,
                    0 if is_admin_user else 1,
                ),
            )
            conn.commit()
            await message.reply_text(
                """
👋 <b>Bienvenido a la Tienda Automática</b>

⏳ Tu cuenta está pendiente de aprobación por el administrador.
""",
                parse_mode=ParseMode.HTML,
                reply_markup=user_menu_keyboard(uid),
            )
            await send_debug_log(
                context,
                f"🆕 <b>NUEVO USUARIO PENDIENTE</b>\n👤 {name}\n🆔 <code>{uid}</code>\n📛 @{username}",
            )
        elif existing_user[1] == 1:
            # User exists but is pending
            await message.reply_text(
                """
⏳ <b>Cuenta Pendiente de Aprobación</b>

Tu cuenta está esperando aprobación por el administrador.
Por favor espera ser aprobado para usar el bot.
""",
                parse_mode=ParseMode.HTML,
                reply_markup=user_menu_keyboard(uid),
            )
        else:
            # User is approved
            await message.reply_text(
                """
👋 <b>Bienvenido a la Tienda Automática</b>

✅ Tu cuenta ha sido validada.
Usa /cmds para volver al inicio.
""",
                parse_mode=ParseMode.HTML,
                reply_markup=user_menu_keyboard(uid),
            )


async def register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


# ================= CMDS =================
async def cmds(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return
    uid = update.effective_user.id
    await remove_legacy_menu_once(update, context)
    await send_menu_message(
        update,
        cmds_text(uid),
        reply_markup=user_menu_keyboard(uid),
    )


async def productos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await stock(update, context)


async def usuarios_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_admin(update.effective_user.id):
        return
    await send_menu_message(
        update,
        users_menu_text(),
        reply_markup=users_menu_keyboard(),
    )


async def ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_user_access(update):
        return
    if not update.effective_user:
        return
    await remove_legacy_menu_once(update, context)
    context.user_data["awaiting_help_question"] = True
    await send_menu_message(
        update,
        "🆘 <b>AYUDA</b>\n\n¿Qué necesitas? Escríbelo en tu siguiente mensaje y lo mandaré al canal debug.",
        reply_markup=back_to_cmds_keyboard(),
    )
    await send_debug_log(
        context,
        f"🆘 <b>MODO AYUDA ACTIVADO</b>\n👤 {update.effective_user.first_name}\n🆔 <code>{update.effective_user.id}</code>\n📛 @{update.effective_user.username or 'sin_username'}",
    )


# ================= ME & BUY =================
async def me(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_user_access(update):
        return
    if not update.effective_user:
        return
    message = update.effective_message
    if not message:
        return
    uid = str(update.effective_user.id)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name, username, credits, fecha_registro, ultima_recarga FROM users WHERE id=?",
            (uid,),
        )
        user = cursor.fetchone()

    if not user:
        return await message.reply_text("⚠️ Usa /start primero.")

    texto_perfil = f"""
👤 <b>MI PERFIL</b>\n
👤 Nombre: {user[0]}
📛 Usuario: @{user[1]}
🆔 ID: <code>{uid}</code>
💰 Créditos Disponibles: {user[2]}
📦 Stock Tienda: {stock_count()}
🕒 Registro: {format_datetime(user[3])}
💳 Última recarga: {format_datetime(user[4])}
"""
    try:
        await message.reply_photo(
            photo=open("imagen/Estadisticas.jpeg", "rb"),
            caption=texto_perfil,
            parse_mode=ParseMode.HTML,
        )
    except FileNotFoundError:
        await message.reply_text(texto_perfil, parse_mode=ParseMode.HTML)


async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_user_access(update):
        return
    message = update.effective_message
    if not message:
        return
    kb = [
        [InlineKeyboardButton("📢 Canal Oficial", url=CHANNEL_URL)],
        [
            InlineKeyboardButton(
                "💬 Contactar Admin",
                url=f"https://t.me/{OWNER_USERNAME.replace('@', '')}",
            )
        ],
    ]
    await message.reply_text(
        f"""
💰 <b>RECARGA DE SALDO</b>

💳 Precios:
7$ = 5 créditos
12$ = 10 créditos

ℹ️ 1 Crédito = 1 Item.

📩 Para recargar contacta a: {OWNER_USERNAME}
📦 Stock disponible ahora mismo: {stock_count()}
""",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.HTML,
    )


# ================= ADMIN DASHBOARD =================
async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_admin(update.effective_user.id):
        return
    await remove_legacy_menu_once(update, context)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM users WHERE is_banned=1")
        banned_users = cursor.fetchone()[0]
        cursor.execute(
            "SELECT COUNT(*) FROM history WHERE expiracion > datetime('now', 'localtime')"
        )
        active_accounts = cursor.fetchone()[0]

    dias_vigencia = get_setting("dias_vigencia")

    texto = f"""
👑 <b>PANEL DE PRODUCTOS</b>

📦 <b>Stock Disponible:</b> {stock_count()}
⚙️ <b>Vigencia actual:</b> {dias_vigencia} días
✅ <b>Cuentas Activas:</b> {active_accounts}

👇 <i>Opciones de stock:</i>
"""
    kb = [
        [
            InlineKeyboardButton("📦 Ver stock", callback_data="admin_stock"),
            InlineKeyboardButton("➕ Agregar", callback_data="stock_add_help"),
        ],
        [
            InlineKeyboardButton("🗑 Borrar N", callback_data="stock_del_help"),
            InlineKeyboardButton("⚙️ Vigencia", callback_data="admin_vigencia"),
        ],
        [
            InlineKeyboardButton("🔄 Ver activos", callback_data="admin_panel"),
            InlineKeyboardButton("👥 Usuarios", callback_data="users_menu"),
        ],
        [InlineKeyboardButton("⬅️ Volver al menú", callback_data="back_cmds")],
    ]
    await send_menu_message(
        update,
        texto,
        reply_markup=InlineKeyboardMarkup(kb),
    )


async def admin_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    restricted_prefixes = ("admin_", "stock_", "users_")
    if query.data.startswith(restricted_prefixes) and not is_admin(query.from_user.id):
        return await query.answer("❌ No tienes permiso.", show_alert=True)

    await query.answer()
    if query.data == "main_refresh":
        await cmds(update, context)
    elif query.data == "main_me":
        await me(update, context)
    elif query.data == "main_buy":
        await buy(update, context)
    elif query.data == "main_history":
        await historia(update, context)
    elif query.data == "help_start":
        await ayuda(update, context)
    elif query.data == "admin_home":
        await admin(update, context)
    elif query.data == "admin_stock" or query.data == "stock_home":
        _, stock_text = stock_list_text(limit=5)
        await send_menu_message(
            update,
            stock_text,
            reply_markup=stock_menu_keyboard(),
        )
    elif query.data == "stock_view":
        _, stock_text = stock_list_text(limit=5)
        await send_menu_message(
            update,
            stock_text,
            reply_markup=stock_menu_keyboard(),
        )
    elif query.data == "stock_add_help":
        await send_menu_message(
            update,
            "➕ <b>Agregar productos</b>\nUsa <code>/stock producto1 producto2 producto3</code>\nEjemplo: <code>/stock user:pass user2:pass2</code>",
            reply_markup=stock_menu_keyboard(),
        )
    elif query.data == "stock_del_help":
        await send_menu_message(
            update,
            "🗑 <b>Borrar producto</b>\nUsa <code>/delstock N</code>\nEjemplo: <code>/delstock 2</code>",
            reply_markup=stock_menu_keyboard(),
        )
    elif query.data == "admin_panel":
        await panel(update, context)
    elif query.data == "admin_vigencia":
        await send_menu_message(
            update,
            "⚙️ <b>Vigencia</b>\nUsa <code>/setdias 30</code>\nEjemplo: <code>/setdias 30</code>",
            reply_markup=stock_menu_keyboard(),
        )
    elif query.data == "stock_active":
        await panel(update, context)
    elif query.data == "users_view":
        await users_list(update, context)
    elif query.data == "users_pending":
        await send_menu_message(
            update,
            pending_users_text(),
            reply_markup=users_menu_keyboard(),
        )
    elif query.data == "users_addcred":
        await send_menu_message(
            update,
            "➕ <b>Añadir créditos</b>\nUsa <code>/addcred ID CANTIDAD</code>\nEjemplo: <code>/addcred 123456789 5</code>",
            reply_markup=users_menu_keyboard(),
        )
    elif query.data == "users_delcred":
        await send_menu_message(
            update,
            "➖ <b>Quitar créditos</b>\nUsa <code>/delcred ID CANTIDAD</code>\nEjemplo: <code>/delcred 123456789 2</code>",
            reply_markup=users_menu_keyboard(),
        )
    elif query.data == "users_info":
        await send_menu_message(
            update,
            "ℹ️ <b>Info usuario</b>\nUsa <code>/info ID</code>\nEjemplo: <code>/info 123456789</code>",
            reply_markup=users_menu_keyboard(),
        )
    elif query.data == "users_compras":
        await send_menu_message(
            update,
            "🛍 <b>Compras</b>\nUsa <code>/compras ID</code>\nEjemplo: <code>/compras 123456789</code>",
            reply_markup=users_menu_keyboard(),
        )
    elif query.data == "users_ban":
        await send_menu_message(
            update,
            "⛔ <b>Banear</b>\nUsa <code>/ban ID</code>\nEjemplo: <code>/ban 123456789</code>",
            reply_markup=users_menu_keyboard(),
        )
    elif query.data == "users_unban":
        await send_menu_message(
            update,
            "✅ <b>Desbanear</b>\nUsa <code>/unban ID</code>\nEjemplo: <code>/unban 123456789</code>",
            reply_markup=users_menu_keyboard(),
        )
    elif query.data == "users_aprobar":
        await send_menu_message(
            update,
            "✅ <b>Aprobar usuarios</b>\nUsa <code>/aprobar</code> para ver pendientes\nEjemplo: <code>/aprobar 123456789</code>",
            reply_markup=users_menu_keyboard(),
        )
    elif query.data == "users_menu":
        await send_menu_message(
            update,
            users_menu_text(),
            reply_markup=users_menu_keyboard(),
        )
    elif query.data == "back_cmds":
        await cmds(update, context)


# ================= GESTIÓN DE ADMINISTRADORES =================
async def addadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_super_admin(update.effective_user.id):
        return await update.message.reply_text(
            "❌ Solo el Dueño Principal (Super Admin) puede añadir otros Administradores."
        )

    if not context.args:
        return await update.message.reply_text("Uso: /addadmin ID o @usuario")

    target = context.args[0]
    user_data = buscar_usuario_por_arg(target)

    if not user_data:
        return await update.message.reply_text(
            "❌ Usuario no encontrado en la base de datos."
        )

    with get_db_connection() as conn:
        conn.execute(
            "UPDATE users SET is_admin=1, is_pending=0, aprobado_en=datetime('now', 'localtime') WHERE id=?",
            (user_data[0],),
        )
        conn.commit()

    await update.message.reply_text(
        f"✅ <b>NUEVO ADMIN AÑADIDO</b>\nEl usuario {user_data[1]} ahora tiene poderes de Administrador.",
        parse_mode=ParseMode.HTML,
    )
    await send_debug_log(
        context,
        f"👑 <b>NUEVO ADMIN</b>\n👮 Añadido por: {update.effective_user.id}\n👤 Nuevo Admin: {user_data[1]} (<code>{user_data[0]}</code>)",
    )


async def deladmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_super_admin(update.effective_user.id):
        return await update.message.reply_text(
            "❌ Solo el Dueño Principal (Super Admin) puede quitar a otros Administradores."
        )

    if not context.args:
        return await update.message.reply_text("Uso: /deladmin ID o @usuario")

    target = context.args[0]
    user_data = buscar_usuario_por_arg(target)

    if not user_data:
        return await update.message.reply_text("❌ Usuario no encontrado.")

    with get_db_connection() as conn:
        conn.execute("UPDATE users SET is_admin=0 WHERE id=?", (user_data[0],))
        conn.commit()

    await update.message.reply_text(
        f"✅ <b>ADMIN REMOVIDO</b>\nEl usuario {user_data[1]} ya NO es Administrador.",
        parse_mode=ParseMode.HTML,
    )
    await send_debug_log(
        context,
        f"👑 <b>ADMIN REMOVIDO</b>\n👮 Quitado por: {update.effective_user.id}\n👤 Removido: {user_data[1]} (<code>{user_data[0]}</code>)",
    )


async def admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_super_admin(update.effective_user.id):
        return await update.message.reply_text(
            "❌ Solo el Dueño Principal puede ver esta lista."
        )

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, username FROM users WHERE is_admin=1")
        db_admins = cursor.fetchall()

    texto = "👑 <b>LISTA DE ADMINISTRADORES</b>\n\n"
    texto += "🔹 <b>Super Admins (Dueños .env):</b>\n"
    for sid in ADMIN_IDS:
        texto += f"- <code>{sid}</code>\n"

    texto += "\n🔹 <b>Admins Secundarios (Base de Datos):</b>\n"
    if not db_admins:
        texto += "- No hay admins adicionales.\n"
    else:
        for a in db_admins:
            uname = f"@{a[2]}" if a[2] and a[2] != "sin_username" else ""
            texto += f"- {a[1]} {uname} | <code>{a[0]}</code>\n"

    await update.message.reply_text(texto, parse_mode=ParseMode.HTML)


async def aprobar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_super_admin(update.effective_user.id):
        return await update.message.reply_text(
            "❌ Solo el Dueño Principal puede aprobar usuarios."
        )

    if not context.args:
        await update.message.reply_text(pending_users_text(), parse_mode=ParseMode.HTML)
        return

    target = context.args[0]
    user_data = buscar_usuario_por_arg(target)

    if not user_data:
        return await update.message.reply_text("❌ Usuario no encontrado.")

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET is_pending=0, aprobado_en=datetime('now', 'localtime') WHERE id=?",
            (user_data[0],),
        )
        conn.commit()

    await update.message.reply_text(
        f"✅ <b>USUARIO APROBADO</b>\nEl usuario {user_data[1]} (<code>{user_data[0]}</code>) ahora puede usar el bot.",
        parse_mode=ParseMode.HTML,
    )
    await send_debug_log(
        context,
        f"✅ <b>USUARIO APROBADO</b>\n👮 Aprobado por: {update.effective_user.id}\n👤 Usuario: {user_data[1]} (<code>{user_data[0]}</code>)",
    )
    try:
        await context.bot.send_message(
            chat_id=int(user_data[0]),
            text="✅ Tu cuenta ha sido aprobada. Ya puedes usar el bot con /cmds.",
        )
    except Exception:
        pass


# ================= RESTO DE FUNCIONES ADMIN =================
async def users_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return await update.effective_message.reply_text("❌ No tienes permisos.")
    with get_db_connection() as conn:
        users = conn.execute(
            "SELECT id, username, credits, is_pending FROM users ORDER BY id DESC"
        ).fetchall()
    if not users:
        return await update.effective_message.reply_text("No hay usuarios registrados.")

    texto = f"👥 <b>PANEL DE USUARIOS ({len(users)})</b>\n\n"
    for u in users:
        estado = "PENDIENTE" if u[3] == 1 else "ACTIVO"
        texto += f"{u[0]} | {u[1]} | 💰 {u[2]} | {estado}\n"
    texto += "\n💡 Usa /info, /addcred, /delcred, /ban, /unban, /aprobar"
    await update.effective_message.reply_text(
        texto[:4000], parse_mode=ParseMode.HTML, reply_markup=users_menu_keyboard()
    )


async def stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    if not is_admin(update.effective_user.id):
        return
    await remove_legacy_menu_once(update, context)
    partes = update.message.text.split(maxsplit=1)
    if len(partes) < 2:
        _, stock_text = stock_list_text(limit=5)
        return await update.effective_message.reply_text(
            stock_text
            + "\n\n➕ Usa <code>/stock item1 item2</code> para agregar productos.",
            parse_mode=ParseMode.HTML,
            reply_markup=stock_menu_keyboard(),
        )
    mensaje = partes[1].strip()
    items = parse_stock_items(mensaje)
    if not items:
        return await update.message.reply_text(
            "❌ No se detectaron items válidos para agregar."
        )
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.executemany(
            "INSERT INTO stock (item) VALUES (?)", [(item,) for item in items]
        )
        conn.commit()
    await update.message.reply_text(
        f"✅ {len(items)} item(s) agregados.\n📦 Stock total: {stock_count()}\n📝 Últimos agregados:\n"
        + "\n".join([f"• {item}" for item in items[:5]])
    )
    await send_debug_log(
        context,
        f"📦 <b>STOCK AGREGADO</b>\n👮 Admin: {update.effective_user.id}\n➕ Cantidad: {len(items)}\n📝 Items: {' | '.join(items[:10])}",
    )


async def delstock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args or not context.args[0].isdigit():
        return await update.message.reply_text("Uso: /delstock NUMERO")

    numero = int(context.args[0])
    with get_db_connection() as conn:
        cursor = conn.cursor()
        rows = cursor.execute("SELECT id, item FROM stock ORDER BY id DESC").fetchall()
        if not rows:
            return await update.message.reply_text("⚠️ No hay stock para borrar.")

        target_row = None
        if 1 <= numero <= len(rows):
            target_row = rows[numero - 1]
        else:
            target_row = cursor.execute(
                "SELECT id, item FROM stock WHERE id=?", (numero,)
            ).fetchone()

        if not target_row:
            return await update.message.reply_text(
                "❌ No encontré ese número en el stock."
            )

        cursor.execute("DELETE FROM stock WHERE id=?", (target_row[0],))
        conn.commit()

    await update.message.reply_text(
        f"🗑 Eliminado stock #{numero} -> <code>{target_row[1]}</code>",
        parse_mode=ParseMode.HTML,
    )
    await send_debug_log(
        context,
        f"🗑 <b>STOCK ELIMINADO</b>\n👮 Admin: {update.effective_user.id}\n# Número: {numero}\n🗂 Item: <code>{target_row[1]}</code>",
    )


async def resetstock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    with get_db_connection() as conn:
        conn.execute("DELETE FROM stock")
        conn.commit()
    await update.message.reply_text("✅ Todo el stock ha sido eliminado.")


async def addcred(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if len(context.args) < 2 or not context.args[1].isdigit():
        return await update.message.reply_text("Uso: /addcred ID/@user CANTIDAD")
    target, amount = context.args[0], int(context.args[1])
    user_data = buscar_usuario_por_arg(target)
    if not user_data:
        return await update.message.reply_text("❌ Usuario no registrado.")
    uid = user_data[0]
    with get_db_connection() as conn:
        conn.execute(
            "UPDATE users SET credits = credits + ?, ultima_recarga=datetime('now', 'localtime') WHERE id=?",
            (amount, uid),
        )
        conn.commit()
        new_credits = conn.execute(
            "SELECT credits FROM users WHERE id=?", (uid,)
        ).fetchone()[0]
    await update.message.reply_text(
        f"✅ Agregados {amount} créditos a <code>{uid}</code>.\n💰 Saldo: {new_credits}",
        parse_mode=ParseMode.HTML,
    )
    await send_debug_log(
        context,
        f"💰 <b>CRÉDITOS AÑADIDOS</b>\n👮 Admin: {update.effective_user.id}\n👤 Cliente: <code>{uid}</code>\n➕ Agregados: {amount}",
    )
    try:
        await context.bot.send_message(
            uid,
            f"🎉 ¡Se han añadido {amount} créditos a tu cuenta!\n💰 Tu saldo actual es: {new_credits}",
        )
    except Exception as e:
        print(f"No se pudo notificar a {uid}: {e}")


async def delcred(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if len(context.args) < 2 or not context.args[1].isdigit():
        return await update.message.reply_text("Uso: /delcred ID/@user CANTIDAD")
    target, amount = context.args[0], int(context.args[1])
    user_data = buscar_usuario_por_arg(target)
    if not user_data:
        return await update.message.reply_text("❌ Usuario no registrado.")
    uid = user_data[0]
    with get_db_connection() as conn:
        row = conn.execute("SELECT credits FROM users WHERE id=?", (uid,)).fetchone()
        new_credits = max(row[0] - amount, 0)
        conn.execute(
            "UPDATE users SET credits=?, ultima_recarga=datetime('now', 'localtime') WHERE id=?",
            (new_credits, uid),
        )
        conn.commit()
    await update.message.reply_text(
        f"✅ Créditos restados. Saldo de <code>{uid}</code>: {new_credits}",
        parse_mode=ParseMode.HTML,
    )


async def setdias(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args or not context.args[0].isdigit():
        return await update.message.reply_text("Uso: /setdias NUMERO")
    dias = context.args[0]
    set_setting("dias_vigencia", dias)
    await update.message.reply_text(
        f"⚙️ ✅ Los productos ahora durarán {dias} días por defecto.",
        parse_mode=ParseMode.HTML,
    )


async def ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        return await update.message.reply_text("Uso: /ban ID o @usuario")
    user = buscar_usuario_por_arg(context.args[0])
    if not user:
        return await update.message.reply_text("❌ Usuario no encontrado.")
    with get_db_connection() as conn:
        conn.execute("UPDATE users SET is_banned=1 WHERE id=?", (user[0],))
        conn.commit()
    await update.message.reply_text(
        f"⛔️ <b>BANEADO</b>\nEl usuario (ID: <code>{user[0]}</code>) ya no puede usar el bot.",
        parse_mode=ParseMode.HTML,
    )


async def unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        return await update.message.reply_text("Uso: /unban ID o @usuario")
    user = buscar_usuario_por_arg(context.args[0])
    if not user:
        return await update.message.reply_text("❌ Usuario no encontrado.")
    with get_db_connection() as conn:
        conn.execute("UPDATE users SET is_banned=0 WHERE id=?", (user[0],))
        conn.commit()
    await update.message.reply_text(
        f"✅ <b>DESBANEADO</b>\nEl usuario (ID: <code>{user[0]}</code>) ha recuperado el acceso.",
        parse_mode=ParseMode.HTML,
    )


async def info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        return await update.message.reply_text("Uso: /info ID/@user")
    user = buscar_usuario_por_arg(context.args[0])
    if not user:
        return await update.message.reply_text("❌ Usuario no encontrado.")
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, name, username, credits, is_banned, is_admin, is_pending,
                   fecha_registro, ultima_recarga, aprobado_en
            FROM users
            WHERE id=?
            """,
            (user[0],),
        )
        info_user = cursor.fetchone()
        cursor.execute(
            "SELECT COUNT(*), MAX(fecha), MAX(expiracion) FROM history WHERE user_id=?",
            (user[0],),
        )
        compras_total, ultima_compra, ultima_expiracion = cursor.fetchone()

    estado = "Pendiente" if info_user[6] == 1 else "Activo"
    if info_user[4] == 1:
        estado = "Baneado"
    admin_text = "Sí" if info_user[5] == 1 or is_super_admin(info_user[0]) else "No"
    username_text = f"@{info_user[2]}" if info_user[2] else "Sin username"
    texto = (
        f"👤 Nombre: {info_user[1]}\n"
        f"📛 Usuario: {username_text}\n"
        f"🆔 ID: <code>{info_user[0]}</code>\n"
        f"💰 Créditos: {info_user[3]}\n"
        f"📌 Estado: {estado}\n"
        f"👑 Admin: {admin_text}\n"
        f"🕒 Registro: {format_datetime(info_user[7])}\n"
        f"💳 Última recarga: {format_datetime(info_user[8])}\n"
        f"✅ Aprobado: {format_datetime(info_user[9])}\n"
        f"🛒 Compras: {compras_total}\n"
        f"📅 Última compra: {format_datetime(ultima_compra)}\n"
        f"⏳ Última expiración: {format_datetime(ultima_expiracion)}"
    )
    await update.message.reply_text(texto, parse_mode=ParseMode.HTML)


async def compras(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        return await update.message.reply_text("Uso: /compras ID/@user")
    user = buscar_usuario_por_arg(context.args[0])
    if not user:
        return await update.message.reply_text("❌ Usuario no encontrado.")
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT cantidad, fecha, items, expiracion FROM history WHERE user_id=? ORDER BY id DESC",
            (user[0],),
        ).fetchall()
    if not rows:
        return await update.message.reply_text("⚠️ No tiene compras.")
    texto = f"🛍 Compras de {user[1]}:\n"
    for r in rows:
        texto += (
            f"\n📅 {format_datetime(r[1])} - Compró {r[0]} items."
            f"\n⏳ Expira: {format_datetime(r[3])}"
        )
    await update.message.reply_text(texto[:4000])


async def testchats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    resultados = []
    for label, raw_id in (("Canal", CHANNEL_ID), ("Debug", LOG_GROUP_ID)):
        try:
            resolved_id, chat = await resolve_chat_target(context.bot, raw_id)
            resultados.append(
                f"✅ {label}: <code>{resolved_id}</code> | {chat.title or chat.username or chat.id}"
            )
        except Exception as exc:
            resultados.append(f"❌ {label}: <code>{raw_id or 'vacío'}</code> | {exc}")

    await update.message.reply_text("\n".join(resultados), parse_mode=ParseMode.HTML)


async def panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    with get_db_connection() as conn:
        activas = conn.execute("""
            SELECT h.user_id, u.username, h.items, h.fecha, h.expiracion
            FROM history h LEFT JOIN users u ON h.user_id = u.id
            WHERE h.expiracion > datetime('now', 'localtime') ORDER BY h.expiracion ASC
        """).fetchall()
    if not activas:
        return await update.effective_message.reply_text(
            "📊 <b>PANEL DE CUENTAS ACTIVAS</b>\n⚠️ No hay cuentas activas.",
            parse_mode=ParseMode.HTML,
            reply_markup=stock_menu_keyboard(),
        )
    texto = "📊 <b>PANEL DE CUENTAS ACTIVAS</b>\n\n"
    for row in activas:
        uname_str = (
            f"@{row[1]}" if row[1] and row[1] != "sin_username" else f"ID:{row[0]}"
        )
        exp_date = datetime.strptime(row[4], "%Y-%m-%d %H:%M:%S")
        dias = (exp_date - datetime.now()).days
        item_s = (
            row[2].split("\n")[0][:40] + "..."
            if len(row[2]) > 40
            else row[2].split("\n")[0]
        )
        texto += f"👤 {uname_str}\n📦 {item_s}\n⏳ <b>Le quedan:</b> {dias} días ({exp_date.strftime('%d/%m/%Y')})\n━━━━━━━━━━━━━━\n"
    for i in range(0, len(texto), 4000):
        await update.effective_message.reply_text(
            texto[i : i + 4000],
            parse_mode=ParseMode.HTML,
            reply_markup=stock_menu_keyboard(),
        )


# ================= CANAL / ANUNCIO =================
async def anuncio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    with get_db_connection() as conn:
        users = conn.execute("SELECT id FROM users WHERE is_banned=0").fetchall()
    if not context.args and not update.message.reply_to_message:
        return await update.message.reply_text(
            "Uso: /anuncio TEXTO o responde a un mensaje"
        )
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Ir a la Tienda", url=f"https://t.me/{context.bot.username}"
                )
            ]
        ]
    )
    enviados, fallidos = 0, 0
    for u in users:
        try:
            if update.message.reply_to_message:
                await context.bot.copy_message(
                    chat_id=u[0],
                    from_chat_id=update.message.chat_id,
                    message_id=update.message.reply_to_message.message_id,
                )
            else:
                await context.bot.send_message(
                    chat_id=u[0], text="📢 " + " ".join(context.args), reply_markup=kb
                )
            enviados += 1
        except Exception:
            fallidos += 1
        await asyncio.sleep(0.05)  # Evita FloodWait de Telegram (rate limit)
    await update.message.reply_text(
        f"📢 Anuncio Global finalizado.\n✅ Exitosos: {enviados}\n❌ Fallidos: {fallidos}"
    )


async def canal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not CHANNEL_ID:
        return await update.message.reply_text("❌ Configura CHANNEL_ID en .env")
    if not context.args and not update.message.reply_to_message:
        return await update.message.reply_text(
            "Uso: /canal TEXTO o responde a un mensaje"
        )
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🛒 Comprar Ahora", url=f"https://t.me/{context.bot.username}"
                )
            ]
        ]
    )
    try:
        target_chat_id, chat = await resolve_chat_target(context.bot, CHANNEL_ID)
        if update.message.reply_to_message:
            await context.bot.copy_message(
                chat_id=target_chat_id,
                from_chat_id=update.message.chat_id,
                message_id=update.message.reply_to_message.message_id,
            )
        else:
            await context.bot.send_message(
                chat_id=target_chat_id,
                text=" ".join(context.args),
                reply_markup=kb,
            )
        await update.message.reply_text(
            f"✅ Publicado exitosamente en <b>{chat.title or chat.username or target_chat_id}</b>.",
            parse_mode=ParseMode.HTML,
        )
        await send_debug_log(
            context,
            f"📣 <b>MENSAJE AL CANAL</b>\n👮 Admin: {update.effective_user.id}\n🎯 Chat: <code>{target_chat_id}</code>\n📝 Texto: {' '.join(context.args) if context.args else 'mensaje reenviado'}",
        )
    except Exception as e:
        await update.message.reply_text(
            f"❌ Error al enviar al canal.\nCHANNEL_ID actual: <code>{CHANNEL_ID}</code>\nError: <code>{e}</code>",
            parse_mode=ParseMode.HTML,
        )


# ================= COMPRAR (NÚCLEO) =================
async def comprar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if not await ensure_user_access(update):
        return

    if not context.args or not context.args[0].isdigit():
        return await update.message.reply_text("Uso: /comprar 1")
    cantidad = int(context.args[0])
    if cantidad <= 0:
        return await update.message.reply_text("❌ Cantidad inválida.")

    async with comprar_lock:
        with get_db_connection() as conn:
            user = conn.execute(
                "SELECT credits FROM users WHERE id=?", (uid,)
            ).fetchone()
            if not user:
                return await update.message.reply_text("⚠️ Usa /start primero.")
            if user[0] < cantidad:
                kb = [
                    [
                        InlineKeyboardButton(
                            "💬 Contactar para recargar",
                            url=f"https://t.me/{OWNER_USERNAME.replace('@', '')}",
                        )
                    ]
                ]
                return await update.message.reply_text(
                    "❌ Saldo insuficiente. Usa /buy",
                    reply_markup=InlineKeyboardMarkup(kb),
                )

            items = conn.execute(
                "SELECT id, item FROM stock LIMIT ?", (cantidad,)
            ).fetchall()
            if len(items) < cantidad:
                return await update.message.reply_text("❌ No hay stock suficiente.")

            ids, data = [int(i[0]) for i in items], [i[1] for i in items]
            compra_id, entrega_formateada, db_items_str_list = gen_id(), [], []

            for item in data:
                if ":" in item:
                    u_str, p_str = item.split(":", 1)
                    entrega_formateada.append(
                        f"👤 <b>Usuario:</b> <code>{u_str.strip()}</code>\n🔑 <b>Contraseña:</b> <code>{p_str.strip()}</code>"
                    )
                    db_items_str_list.append(item)
                else:
                    entrega_formateada.append(f"📦 <code>{item}</code>")
                    db_items_str_list.append(item)

            dias_vigencia = int(get_setting("dias_vigencia") or 30)
            expiracion = (datetime.now() + timedelta(days=dias_vigencia)).strftime(
                "%Y-%m-%d %H:%M:%S"
            )

            try:
                placeholders = ",".join(["?"] * len(ids))
                conn.execute(f"DELETE FROM stock WHERE id IN ({placeholders})", ids)
                conn.execute(
                    "UPDATE users SET credits = credits - ? WHERE id=?", (cantidad, uid)
                )
                conn.execute(
                    "INSERT INTO history (user_id, cantidad, items, compra_id, expiracion) VALUES (?, ?, ?, ?, ?)",
                    (
                        uid,
                        cantidad,
                        "\n".join(db_items_str_list),
                        compra_id,
                        expiracion,
                    ),
                )
                conn.commit()
            except Exception as e:
                conn.rollback()
                await send_debug_log(context, f"⚠️ <b>ERROR EN COMPRA</b>\n❌ {e}")
                return await update.message.reply_text(
                    "❌ Error en el servidor. Intenta de nuevo."
                )

    texto_compra = f"✅ <b>COMPRA EXITOSA</b>\n\n🧾 Recibo: #{compra_id}\n📦 Cantidad: {cantidad}\n\n🎁 <b>TUS ITEMS:</b>\n━━━━━━━━━━━━━━\n{chr(10).join(entrega_formateada)}\n━━━━━━━━━━━━━━\n\n💰 <i>Créditos restantes: {user[0] - cantidad}</i>"
    try:
        await update.effective_message.reply_photo(
            photo=open("imagen/Generando.jpeg", "rb"),
            caption=texto_compra,
            parse_mode=ParseMode.HTML,
        )
    except:
        await update.effective_message.reply_text(
            texto_compra, parse_mode=ParseMode.HTML
        )
    await send_debug_log(
        context,
        f"🛍 <b>NUEVA VENTA</b>\n👤 Usuario: <code>{uid}</code>\n📦 Items: {cantidad}\n🧾 Recibo: #{compra_id}",
    )


async def historia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_user_access(update):
        return
    uid = str(update.effective_user.id)
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT compra_id, cantidad, items, fecha, expiracion FROM history WHERE user_id=? ORDER BY id DESC",
            (uid,),
        ).fetchall()
    if not rows:
        return await update.message.reply_text("⚠️ No has realizado compras.")
    texto = f"📜 <b>TU HISTORIAL</b> ({len(rows)} compras)\n"
    for r in rows:
        exp_texto = ""
        if len(r) > 4 and r[4]:
            try:
                exp_date = datetime.strptime(r[4], "%Y-%m-%d %H:%M:%S")
                dias = (exp_date - datetime.now()).days
                if dias >= 0:
                    exp_texto = f"\n⏳ <b>Vigencia:</b> Quedan {dias} días"
                else:
                    exp_texto = "\n❌ <b>Expirado</b>"
            except:
                pass
        texto += f"\n🔹 Recibo: #{r[0]} | 📅 {r[3]}\n📦 Cantidad: {r[1]}\n{r[2]}{exp_texto}\n"
    for i in range(0, len(texto), 4000):
        await update.effective_message.reply_text(
            texto[i : i + 4000], parse_mode=ParseMode.HTML
        )


async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    comando = (update.message.text or "").split()[0].lower()
    if comando == "/menu":
        return await update.message.reply_text("❌ /menu no existe aquí. Usa /cmds")
    await update.message.reply_text("❌ Comando no reconocido. Usa /cmds")


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("awaiting_help_question"):
        context.user_data["awaiting_help_question"] = False
        pregunta = (update.message.text or "").strip()
        await update.message.reply_text(
            "✅ Tu mensaje fue enviado al canal debug. Te responderán lo antes posible.",
            reply_markup=user_menu_keyboard(update.effective_user.id),
        )
        await send_debug_log(
            context,
            f"🆘 <b>NUEVA PREGUNTA DE AYUDA</b>\n👤 {update.effective_user.first_name}\n🆔 <code>{update.effective_user.id}</code>\n📛 @{update.effective_user.username or 'sin_username'}\n❓ <b>Pregunta:</b> {pregunta}",
        )
        return
    await cmds(update, context)


# ================= MAIN =================
def main():
    if not TOKEN:
        return print("❌ Configura TELEGRAM_BOT_TOKEN en el archivo .env")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("register", register))
    app.add_handler(CommandHandler("cmds", cmds))
    app.add_handler(CommandHandler("ayuda", ayuda))
    app.add_handler(CommandHandler("me", me))
    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(CommandHandler("comprar", comprar))
    app.add_handler(CommandHandler("historia", historia))

    app.add_handler(CommandHandler("productos", productos))
    app.add_handler(CommandHandler("admin", admin))
    app.add_handler(
        CallbackQueryHandler(
            admin_callbacks,
            pattern="^(main_.*|help_.*|admin_.*|stock_.*|users_.*|back_cmds)$",
        )
    )

    app.add_handler(CommandHandler("addadmin", addadmin))
    app.add_handler(CommandHandler("deladmin", deladmin))
    app.add_handler(CommandHandler("admins", admins))
    app.add_handler(CommandHandler("users", usuarios_panel))
    app.add_handler(CommandHandler("aprobar", aprobar))

    app.add_handler(CommandHandler("stock", stock))
    app.add_handler(CommandHandler("delstock", delstock))
    app.add_handler(CommandHandler("resetstock", resetstock))
    app.add_handler(CommandHandler("addcred", addcred))
    app.add_handler(CommandHandler("delcred", delcred))
    app.add_handler(CommandHandler("compras", compras))
    app.add_handler(CommandHandler("info", info))
    app.add_handler(CommandHandler("anuncio", anuncio))
    app.add_handler(CommandHandler("canal", canal))
    app.add_handler(CommandHandler("testchats", testchats))
    app.add_handler(CommandHandler("panel", panel))
    app.add_handler(CommandHandler("setdias", setdias))
    app.add_handler(CommandHandler("ban", ban))
    app.add_handler(CommandHandler("unban", unban))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_handler(MessageHandler(filters.COMMAND, unknown))
    app.add_error_handler(error_handler)

    print("BOT INICIADO Y CORRIENDO...")
    app.run_polling()


if __name__ == "__main__":
    main()
