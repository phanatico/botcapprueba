print("BOT INICIADO")

import os
import sqlite3
import random
import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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

# ================= CONFIG =================
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

# Manejo seguro de IDs de administrador (SUPER ADMINS)
admin_env = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(x.strip()) for x in admin_env.split(",") if x.strip()]

LOG_GROUP_ID = int(os.getenv("LOG_GROUP_ID", "0"))
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
            is_admin INTEGER DEFAULT 0
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


async def send_debug_log(context: ContextTypes.DEFAULT_TYPE, text: str):
    if LOG_GROUP_ID != 0:
        try:
            await context.bot.send_message(
                chat_id=LOG_GROUP_ID,
                text=f"🔍 <b>AUDITORÍA</b>\n\n{text}",
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            print(f"Error enviando log: {e}")


# ================= ERROR =================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    print("ERROR CAUGHT:", context.error)
    await send_debug_log(
        context, f"⚠️ <b>ERROR DEL SISTEMA:</b>\n<code>{context.error}</code>"
    )


# ================= START / REGISTER =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = str(user.id)
    name = user.first_name or "Usuario"
    username = user.username if user.username else "sin_username"

    is_new_user = False
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE id=?", (uid,))
        if not cursor.fetchone():
            cursor.execute(
                "INSERT INTO users (id, name, username, credits, is_banned, is_admin) VALUES (?, ?, ?, ?, ?, ?)",
                (uid, name, username, 0, 0, 0),
            )
            conn.commit()
            is_new_user = True

    await update.message.reply_text(
        """
👋 <b>Bienvenido a la Tienda Automática</b>

✅ Tu cuenta ha sido validada.
Usa /cmds para ver todos los comandos.
""",
        parse_mode=ParseMode.HTML,
    )

    if is_new_user:
        await send_debug_log(
            context,
            f"🆕 <b>NUEVO USUARIO</b>\n👤 {name}\n🆔 <code>{uid}</code>\n📛 @{username}",
        )


async def register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


# ================= CMDS =================
async def cmds(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = """
📜 <b>COMANDOS USUARIO</b>

/start - Inicia el bot y te registra
/me - Muestra tu perfil, créditos y stock
/buy - Muestra precios y cómo recargar
/comprar 1 - Compra 1 item (descuenta 1 crédito)
/historia - Muestra tu historial de compras
"""
    if is_admin(uid):
        text += """
🛠 <b>COMANDOS ADMIN</b>

/admin - 🎛 Abre el Panel de Control Interactivo
/stock TEXTO - Agrega items (puedes pegar una lista)
/resetstock - Borra todo el stock
/addcred ID/@user - Agrega créditos
/delcred ID/@user - Quita créditos
/anuncio TEXTO - Envía un DM a todos los usuarios
/canal TEXTO - Publica en el canal oficial
/compras ID/@user - Ver compras de alguien
/info ID/@user - Ver saldo de alguien
/panel - Ver cuentas activas y días restantes
/setdias N - ⚙️ Cambia duración de productos
/ban ID/@user - ⛔️ Bloquea a un usuario
/unban ID/@user - ✅ Desbloquea a un usuario
"""
    if is_super_admin(uid):
        text += """
👑 <b>COMANDOS DUEÑO (Super Admin)</b>

/addadmin ID/@user - Otorga permisos de Admin
/deladmin ID/@user - Quita permisos de Admin
/admins - Muestra la lista de Administradores
"""
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# ================= ME & BUY =================
async def me(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name, username, credits FROM users WHERE id=?", (uid,))
        user = cursor.fetchone()

    if not user:
        return await update.message.reply_text("⚠️ Usa /start primero.")

    texto_perfil = f"""
👤 <b>MI PERFIL</b>\n
👤 Nombre: {user[0]}
📛 Usuario: @{user[1]}
🆔 ID: <code>{uid}</code>
💰 Créditos Disponibles: {user[2]}
📦 Stock Tienda: {stock_count()}
"""
    try:
        await update.effective_message.reply_photo(
            photo=open("imagen/Estadisticas.jpeg", "rb"),
            caption=texto_perfil,
            parse_mode=ParseMode.HTML,
        )
    except FileNotFoundError:
        await update.effective_message.reply_text(
            texto_perfil, parse_mode=ParseMode.HTML
        )


async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("📢 Canal Oficial", url=CHANNEL_URL)],
        [
            InlineKeyboardButton(
                "💬 Contactar Admin",
                url=f"https://t.me/{OWNER_USERNAME.replace('@', '')}",
            )
        ],
    ]
    await update.message.reply_text(
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
    if not is_admin(update.effective_user.id):
        return

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
👑 <b>PANEL DE CONTROL PROFESIONAL</b>

👥 <b>Usuarios Registrados:</b> {total_users} (⛔️ {banned_users} baneados)
📦 <b>Stock Disponible:</b> {stock_count()}
✅ <b>Cuentas Activas:</b> {active_accounts}
⚙️ <b>Vigencia actual:</b> {dias_vigencia} días

👇 <i>Opciones Rápidas:</i>
"""
    kb = [
        [
            InlineKeyboardButton("📦 Info Stock", callback_data="admin_stock"),
            InlineKeyboardButton("📊 Ver Activos", callback_data="admin_panel"),
        ],
        [
            InlineKeyboardButton("⚙️ Vigencia", callback_data="admin_vigencia"),
            InlineKeyboardButton("🧹 Limpiar", callback_data="admin_limpiar"),
        ],
    ]
    await update.message.reply_text(
        texto, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML
    )


async def admin_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        return await query.answer("❌ No tienes permiso.", show_alert=True)

    await query.answer()
    if query.data == "admin_stock":
        await query.message.reply_text(
            f"📦 <b>Stock Actual:</b> {stock_count()} items.\n💡 <i>Con /stock puedes pegar múltiples cuentas separadas por salto de línea.</i>",
            parse_mode=ParseMode.HTML,
        )
    elif query.data == "admin_panel":
        await panel(update, context)
    elif query.data == "admin_vigencia":
        await query.message.reply_text(
            "⚙️ Para cambiar la duración: <code>/setdias 30</code>",
            parse_mode=ParseMode.HTML,
        )
    elif query.data == "admin_limpiar":
        await query.message.reply_text(
            "⚠️ Para borrar TODO el stock, escribe: <code>/resetstock</code>",
            parse_mode=ParseMode.HTML,
        )


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
        conn.execute("UPDATE users SET is_admin=1 WHERE id=?", (user_data[0],))
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


# ================= RESTO DE FUNCIONES ADMIN =================
async def users_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text("❌ No tienes permisos.")
    with get_db_connection() as conn:
        users = conn.execute("SELECT id, username, credits FROM users").fetchall()
    if not users:
        return await update.message.reply_text("No hay usuarios registrados.")

    texto = f"👥 <b>LISTA DE USUARIOS ({len(users)})</b>\n\n"
    for u in users:
        texto += f"ID: <code>{u[0]}</code> | {u[1]} | 💰 {u[2]}\n"
    for i in range(0, len(texto), 4000):
        await update.message.reply_text(texto[i : i + 4000], parse_mode=ParseMode.HTML)


async def stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    partes = update.message.text.split(maxsplit=1)
    if len(partes) < 2:
        return await update.message.reply_text(
            "Uso: /stock correo:pass\n💡 Puedes pegar varias a la vez."
        )
    mensaje = partes[1].strip()
    items = [line.strip() for line in mensaje.split("\n") if line.strip()]
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.executemany(
            "INSERT INTO stock (item) VALUES (?)", [(item,) for item in items]
        )
        conn.commit()
    await update.message.reply_text(
        f"✅ {len(items)} item(s) agregados.\n📦 Stock total: {stock_count()}"
    )
    await send_debug_log(
        context,
        f"📦 <b>STOCK AGREGADO</b>\n👮 Admin: {update.effective_user.id}\n➕ Cantidad: {len(items)}",
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
        conn.execute("UPDATE users SET credits = credits + ? WHERE id=?", (amount, uid))
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
        conn.execute("UPDATE users SET credits=? WHERE id=?", (new_credits, uid))
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
    await update.message.reply_text(
        f"👤 Nombre: {user[1]}\n📛 @{user[2]}\n🆔 <code>{user[0]}</code>\n💰 Créditos: {user[3]}",
        parse_mode=ParseMode.HTML,
    )


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
            "SELECT cantidad, fecha, items FROM history WHERE user_id=? ORDER BY id DESC",
            (user[0],),
        ).fetchall()
    if not rows:
        return await update.message.reply_text("⚠️ No tiene compras.")
    texto = f"🛍 Compras de {user[1]}:\n"
    for r in rows:
        texto += f"\n📅 {r[1]} - Compró {r[0]} items."
    await update.message.reply_text(texto[:4000])


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
        return await update.message.reply_text(
            "📊 <b>PANEL</b>\n⚠️ No hay cuentas activas.", parse_mode=ParseMode.HTML
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
        await update.message.reply_text(texto[i : i + 4000], parse_mode=ParseMode.HTML)


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
        if update.message.reply_to_message:
            await context.bot.copy_message(
                chat_id=CHANNEL_ID,
                from_chat_id=update.message.chat_id,
                message_id=update.message.reply_to_message.message_id,
                reply_markup=kb,
            )
        else:
            await context.bot.send_message(
                chat_id=CHANNEL_ID, text=" ".join(context.args), reply_markup=kb
            )
        await update.message.reply_text(f"✅ Publicado exitosamente.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error al enviar: {e}")


# ================= COMPRAR (NÚCLEO) =================
async def comprar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if is_user_banned(uid):
        return await update.message.reply_text(
            "⛔️ <b>ACCESO DENEGADO</b>\nTu cuenta ha sido suspendida.",
            parse_mode=ParseMode.HTML,
        )

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
    await update.message.reply_text("❌ Comando no reconocido. Usa /cmds")


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Si le hablan normal, le mostramos los comandos
    await cmds(update, context)


# ================= MAIN =================
def main():
    if not TOKEN:
        return print("❌ Configura TELEGRAM_BOT_TOKEN en el archivo .env")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("register", register))
    app.add_handler(CommandHandler("cmds", cmds))
    app.add_handler(CommandHandler("me", me))
    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(CommandHandler("comprar", comprar))
    app.add_handler(CommandHandler("historia", historia))

    app.add_handler(CommandHandler("admin", admin))
    app.add_handler(CallbackQueryHandler(admin_callbacks, pattern="^admin_"))

    app.add_handler(CommandHandler("addadmin", addadmin))
    app.add_handler(CommandHandler("deladmin", deladmin))
    app.add_handler(CommandHandler("admins", admins))
    app.add_handler(CommandHandler("users", users_list))

    app.add_handler(CommandHandler("stock", stock))
    app.add_handler(CommandHandler("resetstock", resetstock))
    app.add_handler(CommandHandler("addcred", addcred))
    app.add_handler(CommandHandler("delcred", delcred))
    app.add_handler(CommandHandler("compras", compras))
    app.add_handler(CommandHandler("info", info))
    app.add_handler(CommandHandler("anuncio", anuncio))
    app.add_handler(CommandHandler("canal", canal))
    app.add_handler(CommandHandler("panel", panel))
    app.add_handler(CommandHandler("setdias", setdias))
    app.add_handler(CommandHandler("ban", ban))
    app.add_handler(CommandHandler("unban", unban))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_handler(MessageHandler(filters.COMMAND, unknown))
    app.add_error_handler(error_handler)

    print("BOT INICIADO Y CORRIENDO...")

    import sys, asyncio

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    app.run_polling()


if __name__ == "__main__":
    main()
