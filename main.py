import os
import json
import random
import shutil
import string
import base64
import time
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters
)

# ==================== 全局配置 ====================
ADMIN_USER_ID = int(os.environ.get("ADMIN_USER_ID", "0"))
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
STORE_CHAT_ID = os.environ.get("STORE_CHAT_ID", "")

# ==================== 加密核心 ====================
E2EE_KEY = b"e2ee_secure_bot_2026"

def xor_data(data: bytes, key: bytes) -> bytes:
    key = key * (len(data) // len(key) + 1)
    return bytes(d ^ k for d, k in zip(data, key))

def encrypt_metadata(metadata: dict) -> str:
    try:
        raw = json.dumps(metadata).encode("utf-8")
        encrypted = xor_data(raw, E2EE_KEY)
        return base64.urlsafe_b64encode(encrypted).decode("utf-8").replace("=", "")
    except:
        return ""

def decrypt_metadata(encrypted_str: str) -> dict:
    try:
        encrypted_str += "=" * ((4 - len(encrypted_str) % 4) % 4)
        raw = base64.urlsafe_b64decode(encrypted_str.encode("utf-8"))
        decrypted = xor_data(raw, E2EE_KEY)
        return json.loads(decrypted.decode("utf-8"))
    except:
        return {}

# ==================== 数据 ====================
DATA_DIR = "/data"
BACKUP_DIR = os.path.join(DATA_DIR, "backup")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

user_sessions = {}
pending_naming = {}

def load_json(name):
    p = os.path.join(DATA_DIR, name)
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
            if name in ["bot_db", "user_index", "banned"] and not isinstance(data, dict):
                return {}
            return data
    except:
        return {} if name in ["bot_db", "user_index", "banned"] else []

def save_json(name, data):
    p = os.path.join(DATA_DIR, name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

bot_db = load_json("bot_db.json")
if not isinstance(bot_db, dict):
    bot_db = {}

user_idx = load_json("user_index.json")
if not isinstance(user_idx, dict):
    user_idx = {}

banned = load_json("banned.json")
if not isinstance(banned, list):
    banned = []

# ==================== 工具 ====================
def is_admin(user_id):
    return user_id == ADMIN_USER_ID

def is_banned(user_id):
    return str(user_id) in [str(x) for x in banned]

BOT_SELF_ID = None

async def track(user_id, name, username):
    uid = str(user_id)
    if uid not in user_idx or user_idx[uid]["name"] != name:
        user_idx[uid] = {"name": name, "username": username or "-"}
        save_json("user_index.json", user_idx)

def auto_backup():
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        for f in ["bot_db.json", "user_index.json", "banned.json"]:
            src = os.path.join(DATA_DIR, f)
            dst = os.path.join(BACKUP_DIR, f"{f}.{ts}")
            if os.path.exists(src):
                shutil.copy(src, dst)
    except:
        pass

# ==================== start ====================
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global BOT_SELF_ID
    if BOT_SELF_ID is None:
        me = await ctx.bot.get_me()
        BOT_SELF_ID = me.id

    if ctx.args:
        token = ctx.args[0]
        data = decrypt_metadata(token)
        if not data:
            await update.message.reply_text("❌ 链接无效或已过期")
            return

        try:
            await ctx.bot.get_chat(data["chat_id"])
        except Exception as e:
            await update.message.reply_text(f"❌ 无法访问存储频道：{str(e)[:80]}")
            return

        try:
            await ctx.bot.forward_message(
                chat_id=update.effective_chat.id,
                from_chat_id=data["chat_id"],
                message_id=data["message_id"]
            )
        except Exception as e:
            await update.message.reply_text(f"❌ 文件转发失败：{str(e)[:80]}")
        return

    await update.message.reply_text("📦 TG云盘机器人\n/my 我的提取码\n/del 提取码")

# ==================== my ====================
async def my_codes(update: Update, ctx: ContextTypes):
    uid = update.effective_user.id
    if is_banned(uid):
        await update.message.reply_text("❌ 你已被封禁")
        return
    items = [f"🔑 {c}｜{p['name']}" for c, p in bot_db.items() if str(p["uploader"]["id"]) == str(uid)]
    await update.message.reply_text("\n".join(items) if items else "📭 暂无文件")

# ==================== del ====================
async def del_code(update: Update, ctx: ContextTypes):
    uid = update.effective_user.id
    parts = update.message.text.split()
    if len(parts) < 2:
        await update.message.reply_text("用法：/del 提取码")
        return
    code = parts[1]
    if code not in bot_db:
        await update.message.reply_text("❌ 提取码不存在")
        return
    if str(bot_db[code]["uploader"]["id"]) != str(uid):
        await update.message.reply_text("❌ 只能删除自己的")
        return
    del bot_db[code]
    save_json("bot_db.json", bot_db)
    await update.message.reply_text(f"✅ 已删除：{code}")

# ==================== upload ====================
async def upload(update: Update, ctx: ContextTypes):
    global BOT_SELF_ID
    if BOT_SELF_ID is None:
        me = await ctx.bot.get_me()
        BOT_SELF_ID = me.id

    u = update.effective_user
    if is_banned(u.id) or u.id == BOT_SELF_ID:
        return

    msg = update.message
    if not msg.photo and not msg.video and not msg.document:
        return

    if not STORE_CHAT_ID:
        await msg.reply_text("⚠️ 未设置 STORE_CHAT_ID")
        return

    try:
        forwarded = await msg.forward(chat_id=int(STORE_CHAT_ID))

        if msg.photo:
            name = f"IMG_{datetime.now().strftime('%H%M%S')}.jpg"
        elif msg.video:
            name = msg.video.file_name or f"VID_{datetime.now().strftime('%H%M%S')}.mp4"
        elif msg.document:
            name = msg.document.file_name or f"FILE_{datetime.now().strftime('%H%M%S')}"
        else:
            name = "FILE"

        meta = {
            "chat_id": int(STORE_CHAT_ID),
            "message_id": forwarded.message_id,
            "name": name
        }
        enc = encrypt_metadata(meta)

        if u.id not in user_sessions:
            user_sessions[u.id] = []
        user_sessions[u.id].append({
            "meta_type": "perm_enc",
            "data": enc,
            "name": name
        })

        await msg.reply_text(f"✅ 存储成功！\n📄 {name}\n💡 /confirm 打包 /skip 跳过")
        await track(u.id, u.full_name, u.username)

    except Exception as e:
        await msg.reply_text(f"❌ 上传失败：{str(e)[:100]}")

# ==================== confirm ====================
async def confirm(update: Update, ctx: ContextTypes):
    u = update.effective_user.id
    if u not in user_sessions or len(user_sessions[u]) == 0:
        await update.message.reply_text("❌ 暂无待打包文件")
        return

    chars = string.ascii_letters + string.digits
    code = None
    for _ in range(50):
        candidate = ''.join(random.choice(chars) for _ in range(6))
        if candidate not in bot_db:
            code = candidate
            break
    if not code:
        await update.message.reply_text("❌ 生成失败")
        return

    pending_naming[u] = {
        "code": code,
        "files": user_sessions[u],
        "uploader": {"id": u, "name": update.effective_user.full_name}
    }
    del user_sessions[u]
    await update.message.reply_text("📦 输入包名，或 /skip")

# ==================== skip ====================
async def skip(update: Update, ctx: ContextTypes):
    global bot_db
    u = update.effective_user.id
    if u not in pending_naming:
        await update.message.reply_text("❌ 无任务")
        return
    pkg = pending_naming[u]
    pkg["name"] = f"文件包_{pkg['code']}"
    bot_db[pkg["code"]] = pkg
    save_json("bot_db.json", bot_db)
    auto_backup()
    del pending_naming[u]
    await update.message.reply_text(f"✅ 提取码：{pkg['code']}")

# ==================== 按钮回调：直接发文件（最安全） ====================
async def button_callback(update: Update, ctx: ContextTypes):
    query = update.callback_query
    await query.answer()

    if query.data.startswith("get_"):
        token = query.data.replace("get_", "")
        data = decrypt_metadata(token)
        if not data:
            await query.edit_message_text("❌ 无效或已过期")
            return

        try:
            await ctx.bot.forward_message(
                chat_id=query.from_user.id,
                from_chat_id=data["chat_id"],
                message_id=data["message_id"]
            )
        except Exception as e:
            await query.edit_message_text(f"❌ 失败：{str(e)[:60]}")

# ==================== text_handle（纯内部安全版） ====================
async def text_handle(update: Update, ctx: ContextTypes):
    global bot_db
    global BOT_SELF_ID
    if BOT_SELF_ID is None:
        me = await ctx.bot.get_me()
        BOT_SELF_ID = me.id

    u = update.effective_user.id
    txt = update.message.text.strip()

    if u == BOT_SELF_ID or is_banned(u.id):
        return

    if u in pending_naming:
        pkg = pending_naming[u]
        pkg["name"] = txt[:50]
        bot_db[pkg["code"]] = pkg
        save_json("bot_db.json", bot_db)
        auto_backup()
        del pending_naming[u]
        await update.message.reply_text(f"✅ 提取码：{pkg['code']}")
        return

    if len(txt) == 6:
        if txt not in bot_db:
            await update.message.reply_text("❌ 不存在")
            return
        pkg = bot_db[txt]
        await update.message.reply_text(f"📦 {pkg['name']}")

        for f in pkg.get("files", []):
            if not isinstance(f, dict):
                continue
            token = f.get("data", "")
            fname = f.get("name", "文件")

            # ✅ 安全按钮：不跳转、不链接、不点 t.me
            keyboard = [[InlineKeyboardButton(f"📥 取「{fname}」", callback_data=f"get_{token}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(f"📄 {fname}", reply_markup=reply_markup)
        return

# ==================== admin ====================
async def admin(update: Update, ctx: ContextTypes):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("❌ 无权限")
        return
    await update.message.reply_text("👮 管理面板")

# ==================== 启动 ====================
def main():
    if not BOT_TOKEN:
        print("❌ 缺 BOT_TOKEN")
        return
    app = ApplicationBuilder().token(BOT_TOKEN).read_timeout(60).write_timeout(60).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("my", my_codes))
    app.add_handler(CommandHandler("del", del_code))
    app.add_handler(CommandHandler("confirm", confirm))
    app.add_handler(CommandHandler("skip", skip))
    app.add_handler(CommandHandler("admin", admin))

    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, upload))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handle))

    print("✅ 机器人启动成功")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    while True:
        try:
            main()
        except:
            time.sleep(5)
