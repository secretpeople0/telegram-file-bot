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

id_to_token = {}

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

    await update.message.reply_text("📦 TG文件云盘机器人\n/my 我的提取码\n/del 提取码")

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
    if str(bot_db[code]["uploader"]["id"]) != str(uid) and not is_admin(uid):
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

    u = update.effective_user.id
    if is_banned(u) or u == BOT_SELF_ID:
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

        if u not in user_sessions:
            user_sessions[u] = []
        user_sessions[u].append({
            "meta_type": "perm_enc",
            "data": enc,
            "name": name
        })

        user = update.effective_user
        await track(u, user.full_name, user.username)

        await msg.reply_text(f"✅ 存储成功！\n📄 {name}\n💡 /confirm 打包 /skip 跳过")

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
        candidate = ''.join(random.choice(chars, k=6))
        if candidate not in bot_db:
            code = candidate
            break
    if not code:
        await update.message.reply_text("❌ 生成失败")
        return

    user = update.effective_user
    pending_naming[u] = {
        "code": code,
        "files": user_sessions[u],
        "uploader": {"id": u, "name": user.full_name}
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

# ==================== 一键取全部 & 按钮回调 ====================
async def button_callback(update: Update, ctx: ContextTypes):
    global id_to_token, bot_db
    query = update.callback_query
    await query.answer()

    if is_banned(query.from_user.id):
        await query.answer("❌ 你已被封禁", show_alert=True)
        return

    if query.data.startswith("get_"):
        short_id = query.data.replace("get_", "")
        if short_id not in id_to_token:
            await query.edit_message_text("❌ 链接已过期或无效")
            return
        token = id_to_token[short_id]
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

    elif query.data.startswith("all_"):
        code = query.data.replace("all_", "")
        if code not in bot_db:
            await query.edit_message_text("❌ 提取码不存在")
            return
        pkg = bot_db[code]
        await query.edit_message_text(f"📦 正在发送「{pkg['name']}」所有文件...")
        for f in pkg.get("files", []):
            if not isinstance(f, dict):
                continue
            token = f.get("data", "")
            data = decrypt_metadata(token)
            if not data:
                continue
            try:
                await ctx.bot.forward_message(
                    chat_id=query.from_user.id,
                    from_chat_id=data["chat_id"],
                    message_id=data["message_id"]
                )
            except Exception as e:
                await ctx.bot.send_message(
                    chat_id=query.from_user.id,
                    text=f"❌ {f.get('name','文件')} 发送失败"
                )
        await ctx.bot.send_message(
            chat_id=query.from_user.id,
            text=f"✅ 「{pkg['name']}」全部发送完成"
        )

# ==================== text_handle ====================
async def text_handle(update: Update, ctx: ContextTypes):
    global bot_db, BOT_SELF_ID, id_to_token
    if BOT_SELF_ID is None:
        me = await ctx.bot.get_me()
        BOT_SELF_ID = me.id

    u = update.effective_user.id
    txt = update.message.text.strip()

    if u == BOT_SELF_ID or is_banned(u):
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

        keyboard_all = [[InlineKeyboardButton("📥 一键取全部", callback_data=f"all_{txt}")]]
        await update.message.reply_text("⚡ 点我一次性取完所有文件",
            reply_markup=InlineKeyboardMarkup(keyboard_all))

        for f in pkg.get("files", []):
            if not isinstance(f, dict):
                continue
            token = f.get("data", "")
            fname = f.get("name", "文件")
            short_id = ''.join(random.choices(string.ascii_letters+string.digits,k=8))
            id_to_token[short_id] = token
            kb = [[InlineKeyboardButton(f"📥 取 {fname}", callback_data=f"get_{short_id}")]]
            await update.message.reply_text(f"📄 {fname}", reply_markup=InlineKeyboardMarkup(kb))
        return

# ==================== 管理员面板（完整版！不再空白） ====================
async def admin(update: Update, ctx: ContextTypes):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("❌ 无权限")
        return

    text = (
        "🔐 管理员面板\n\n"
        f"📦 总提取码：{len(bot_db)} 个\n"
        f"👤 总用户数：{len(user_idx)} 人\n"
        f"🚫 黑名单：{len(banned)} 人\n\n"
        "管理员命令：\n"
        "/stats - 统计信息\n"
        "/list - 全部提取码\n"
        "/search 关键词 - 搜索提取码\n"
        "/admindel 码 - 强制删除\n"
        "/ban ID - 封禁用户\n"
        "/unban ID - 解封\n"
        "/banlist - 黑名单"
    )
    await update.message.reply_text(text)

async def admin_stats(update: Update, ctx: ContextTypes):
    if not is_admin(update.effective_user.id):
        return
    await update.message.reply_text(
        f"📊 统计\n"
        f"提取码：{len(bot_db)}\n"
        f"用户：{len(user_idx)}\n"
        f"黑名单：{len(banned)}"
    )

async def admin_list(update: Update, ctx: ContextTypes):
    if not is_admin(update.effective_user.id):
        return
    if not bot_db:
        await update.message.reply_text("📭 无提取码")
        return
    lines = [f"{c}｜{p['name']}" for c,p in list(bot_db.items())[:50]]
    await update.message.reply_text("\n".join(lines))

async def admin_search(update: Update, ctx: ContextTypes):
    if not is_admin(update.effective_user.id):
        return
    parts = update.message.text.split()
    if len(parts) < 2:
        await update.message.reply_text("/search 关键词")
        return
    w = parts[1].lower()
    res = [c for c,p in bot_db.items() if w in p['name'].lower()]
    await update.message.reply_text("\n".join(res[:20]) if res else "❌ 无结果")

async def admin_del(update: Update, ctx: ContextTypes):
    if not is_admin(update.effective_user.id):
        return
    parts = update.message.text.split()
    if len(parts)<2:
        await update.message.reply_text("/admindel 提取码")
        return
    code = parts[1]
    if code in bot_db:
        del bot_db[code]
        save_json("bot_db.json",bot_db)
        await update.message.reply_text(f"✅ 已删：{code}")
    else:
        await update.message.reply_text("❌ 不存在")

async def ban_user(update: Update, ctx: ContextTypes):
    if not is_admin(update.effective_user.id):
        return
    parts = update.message.text.split()
    if len(parts)<2:
        await update.message.reply_text("/ban 用户ID")
        return
    uid = parts[1]
    if uid not in banned:
        banned.append(uid)
        save_json("banned.json",banned)
    await update.message.reply_text(f"✅ 已封禁 {uid}")

async def unban_user(update: Update, ctx: ContextTypes):
    if not is_admin(update.effective_user.id):
        return
    parts = update.message.text.split()
    if len(parts)<2:
        await update.message.reply_text("/unban 用户ID")
        return
    uid = parts[1]
    if uid in banned:
        banned.remove(uid)
        save_json("banned.json",banned)
    await update.message.reply_text(f"✅ 已解封 {uid}")

async def ban_list(update: Update, ctx: ContextTypes):
    if not is_admin(update.effective_user.id):
        return
    await update.message.reply_text("\n".join(banned) if banned else "🚫 空")

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
    app.add_handler(CommandHandler("stats", admin_stats))
    app.add_handler(CommandHandler("list", admin_list))
    app.add_handler(CommandHandler("search", admin_search))
    app.add_handler(CommandHandler("admindel", admin_del))
    app.add_handler(CommandHandler("ban", ban_user))
    app.add_handler(CommandHandler("unban", unban_user))
    app.add_handler(CommandHandler("banlist", ban_list))

    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, upload))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handle))

    print("✅ 机器人启动成功 —— 管理员面板已加载")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    while True:
        try:
            main()
        except:
            time.sleep(3)
