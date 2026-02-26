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
ENCODE_KEY = b"secure_file_bot_2026"
BOT_SELF_ID = None

def xor_data(data: bytes, key: bytes) -> bytes:
    key = key * (len(data) // len(key) + 1)
    return bytes(d ^ k for d, k in zip(data, key))

def encrypt_metadata(metadata: dict) -> str:
    try:
        raw = json.dumps(metadata).encode("utf-8")
        encrypted = xor_data(raw, E2EE_KEY)
        return base64.urlsafe_b64encode(encrypted).decode("utf-8").replace("=", "")
    except Exception as e:
        print(f"加密失败: {e}")
        return ""

def decrypt_metadata(encrypted_str: str) -> dict:
    try:
        encrypted_str += "=" * ((4 - len(encrypted_str) % 4) % 4)
        raw = base64.urlsafe_b64decode(encrypted_str.encode("utf-8"))
        decrypted = xor_data(raw, E2EE_KEY)
        return json.loads(decrypted.decode("utf-8"))
    except Exception as e:
        print(f"解密失败: {e}")
        return {}

# ==================== 数据存储 ====================
DATA_DIR = "/data"
BACKUP_DIR = os.path.join(DATA_DIR, "backup")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

user_sessions = {}
pending_naming = {}
admin_ops = {}

def load_json(name):
    p = os.path.join(DATA_DIR, name)
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {} if name in ["bot_db", "user_index", "banned"] else []

def save_json(name, data):
    p = os.path.join(DATA_DIR, name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

bot_db = load_json("bot_db.json")
user_idx = load_json("user_index.json")
banned = load_json("banned.json")

# ==================== 工具 ====================
def is_admin(user_id):
    return user_id == ADMIN_USER_ID

def is_banned(user_id):
    return str(user_id) in [str(x) for x in banned]

def is_bot_self(user_id):
    return user_id == BOT_SELF_ID

async def track(user_id, name, username):
    uid = str(user_id)
    if uid not in user_idx or user_idx[uid]["name"] != name:
        user_idx[uid] = {"name": name, "username": username or "-"}
        save_json("user_index.json", user_idx)

def auto_backup():
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    for f in ["bot_db.json", "user_index.json", "banned.json"]:
        src = os.path.join(DATA_DIR, f)
        dst = os.path.join(BACKUP_DIR, f"{f}.{ts}")
        if os.path.exists(src):
            shutil.copy(src, dst)
    olds = sorted([os.path.join(BACKUP_DIR, x) for x in os.listdir(BACKUP_DIR)], key=lambda x: os.path.getmtime(os.path.join(BACKUP_DIR, x)))
    for old in olds[:-3]:
        os.remove(os.path.join(BACKUP_DIR, old))

# ==================== 管理员菜单 ====================
def admin_menu():
    return [
        [InlineKeyboardButton("📊 统计", callback_data="统计")],
        [InlineKeyboardButton("👥 用户列表", callback_data="用户列表")],
        [InlineKeyboardButton("🔍 搜文件", callback_data="搜文件")],
        [InlineKeyboardButton("👁️ 查用户上传", callback_data="查用户上传")],
        [InlineKeyboardButton("🗑️ 删提取码", callback_data="删提取码")],
        [InlineKeyboardButton("🚫 封禁/解封", callback_data="封禁/解封")],
        [InlineKeyboardButton("🔙 返回", callback_data="返回")]
    ]

# ==================== 命令 ====================
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global BOT_SELF_ID
    if BOT_SELF_ID is None:
        me = await ctx.bot.get_me()
        BOT_SELF_ID = me.id

    args = ctx.args
    if args:
        token = args[0]
        data = decrypt_metadata(token)
        if data:
            try:
                await ctx.bot.forward_message(
                    chat_id=update.effective_chat.id,
                    from_chat_id=data["chat_id"],
                    message_id=data["message_id"]
                )
                return
            except Exception as e:
                await update.message.reply_text("❌ 文件已失效或被删除")
                return

    await update.message.reply_text("📦 TG云盘机器人（跨机器人永久版）\n/my 我的提取码\n/del 提取码")

async def my_codes(update: Update, ctx: ContextTypes):
    uid = update.effective_user.id
    if is_banned(uid):
        return await update.message.reply_text("❌ 你已被封禁")
    items = [f"🔑 {c}｜{p['name']}" for c, p in bot_db.items() if str(p["uploader"]["id"]) == str(uid)]
    await update.message.reply_text("\n".join(items) if items else "📭 暂无文件")

async def del_code(update: Update, ctx: ContextTypes):
    uid = update.effective_user.id
    args = update.message.text.split()
    if len(args) < 2:
        return await update.message.reply_text("用法：/del 提取码")
    code = args[1]
    if code not in bot_db:
        return await update.message.reply_text("❌ 提取码不存在")
    if str(bot_db[code]["uploader"]["id"]) != str(uid):
        return await update.message.reply_text("❌ 只能删除自己创建的提取码")
    del bot_db[code]
    save_json("bot_db.json", bot_db)
    await update.message.reply_text(f"✅ 提取码 {code} 已删除")

# ==================== 上传：直接转发，不下载 ====================
async def upload(update: Update, ctx: ContextTypes):
    global BOT_SELF_ID
    if BOT_SELF_ID is None:
        me = await ctx.bot.get_me()
        BOT_SELF_ID = me.id

    u = update.effective_user
    if is_banned(u.id) or is_bot_self(u.id):
        return

    msg = update.message
    if not msg.photo and not msg.video and not msg.document:
        return

    if not STORE_CHAT_ID:
        await msg.reply_text("⚠️ 未设置存储频道，请先配置 STORE_CHAT_ID")
        return

    try:
        forwarded = await msg.forward(chat_id=int(STORE_CHAT_ID))
        if msg.photo:
            file_name = f"IMG_{datetime.now().strftime('%H%M%S')}.jpg"
        elif msg.video:
            file_name = msg.video.file_name or f"VID_{datetime.now().strftime('%H%M%S')}.mp4"
        elif msg.document:
            file_name = msg.document.file_name or f"FILE_{datetime.now().strftime('%H%M%S')}"

        metadata = {
            "chat_id": int(STORE_CHAT_ID),
            "message_id": forwarded.message_id,
            "name": file_name
        }
        enc = encrypt_metadata(metadata)

        if u.id not in user_sessions:
            user_sessions[u.id] = []
        user_sessions[u.id].append({
            "meta_type": "perm_enc",
            "data": enc,
            "name": file_name
        })

        await msg.reply_text(
            f"✅ 永久存储成功！\n"
            f"📄 {file_name}\n"
            f"💡 /confirm 打包 | /skip 跳过命名"
        )
        await track(u.id, u.full_name, u.username)

    except Exception as e:
        await msg.reply_text(f"❌ 上传失败：{str(e)[:80]}")

async def confirm(update: Update, ctx: ContextTypes):
    u = update.effective_user.id
    if u not in user_sessions or not user_sessions[u]:
        return await update.message.reply_text("❌ 暂无待打包文件")
    
    chars = string.ascii_letters + string.digits
    code = None
    for _ in range(100):
        candidate = ''.join(random.choice(chars) for _ in range(6))
        if candidate not in bot_db:
            code = candidate
            break
    if not code:
        return await update.message.reply_text("❌ 提取码生成失败")
    
    pending_naming[u] = {
        "code": code,
        "files": user_sessions[u],
        "uploader": {"id": u, "name": update.effective_user.full_name}
    }
    del user_sessions[u]
    await update.message.reply_text("📦 输入包名，或 /skip 跳过命名")

async def skip(update: Update, ctx: ContextTypes):
    u = update.effective_user.id
    if u not in pending_naming:
        return await update.message.reply_text("❌ 无待打包任务")
    
    pkg = pending_naming[u]
    pkg["name"] = f"文件包_{pkg['code']}"
    bot_db[pkg["code"]] = pkg
    save_json("bot_db.json", bot_db)
    auto_backup()
    del pending_naming[u]
    await update.message.reply_text(f"✅ 提取码：{pkg['code']}")

# ==================== 提取码 → 只发加密链接 ====================
async def text_handle(update: Update, ctx: ContextTypes):
    global BOT_SELF_ID
    if BOT_SELF_ID is None:
        me = await ctx.bot.get_me()
        BOT_SELF_ID = me.id

    u = update.effective_user.id
    cid = update.effective_chat.id
    txt = update.message.text.strip()

    if is_bot_self(u) or is_banned(u):
        return

    # 管理员操作
    if is_admin(u) and cid in admin_ops:
        act = admin_ops.pop(cid)
        if act == "del_code":
            dels, nf = [], []
            for code in txt.split():
                if code in bot_db:
                    dels.append(code)
                    del bot_db[code]
                else:
                    nf.append(code)
            save_json("bot_db.json", bot_db)
            await update.message.reply_text(f"✅ 已删：{' '.join(dels)} ❌ 不存在：{' '.join(nf)}")
            return
        elif act == "search":
            res = [f"🔑 {c}｜{p['name']}" for c, p in bot_db.items() if txt.lower() in p['name'].lower()]
            await update.message.reply_text("\n".join(res) if res else "🔍 无结果")
            return
        elif act == "user_uploads":
            res = [f"🔑 {c}｜{p['name']}" for c, p in bot_db.items() if str(p['uploader']['id']) == txt.strip()]
            await update.message.reply_text("\n".join(res) if res else "🔍 无记录")
            return
        elif act == "ban":
            parts = txt.split()
            if len(parts) != 2:
                await update.message.reply_text("🚫 封禁 123 / 解封 123")
                return
            cmd, tid = parts
            try:
                tid = int(tid)
                if cmd == "封禁":
                    if tid not in banned: banned.append(tid)
                    await update.message.reply_text(f"✅ 封禁 {tid}")
                elif cmd == "解封":
                    if tid in banned: banned.remove(tid)
                    await update.message.reply_text(f"✅ 解封 {tid}")
                save_json("banned.json", banned)
            except:
                await update.message.reply_text("❌ ID错误")
            return

    # 命名包名
    if u in pending_naming:
        pkg = pending_naming[u]
        pkg["name"] = txt[:50]
        bot_db[pkg["code"]] = pkg
        save_json("bot_db.json", bot_db)
        auto_backup()
        del pending_naming[u]
        await update.message.reply_text(f"✅ 提取码：{pkg['code']}")
        return

    # 6位提取码 → 只发加密链接，不发文件
    if len(txt) == 6:
        if txt not in bot_db:
            await update.message.reply_text("❌ 提取码不存在")
            return
        pkg = bot_db[txt]
        await update.message.reply_text(f"📦 {pkg['name']}")

        me = await ctx.bot.get_me()
        username = me.username

        for f in pkg["files"]:
            token = f["data"]
            name = f.get("name", "文件")
            link = f"https://t.me/{username}?start={token}"
            await update.message.reply_text(f"🔗 {name}\n{link}")

        await update.message.reply_text("✅ 请点击链接获取文件")
        return

# ==================== 管理员面板 ====================
async def admin(update: Update, ctx: ContextTypes):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("❌ 无权限")
        return
    await update.message.reply_text("👮 管理员面板", reply_markup=InlineKeyboardMarkup(admin_menu()))

async def admin_cb(update: Update, ctx: ContextTypes):
    q = update.callback_query
    await q.answer()
    if q.from_user.id != ADMIN_USER_ID:
        await q.edit_message_text("❌ 无权限")
        return
    act = q.data
    cid = q.message.chat.id

    if act == "返回":
        await q.edit_message_text("👮 管理员面板", reply_markup=InlineKeyboardMarkup(admin_menu()))
    elif act == "统计":
        pkgs = len(bot_db)
        files = sum(len(v["files"]) for v in bot_db.values())
        users = len(user_idx)
        await q.edit_message_text(f"📦 提取码：{pkgs}\n📄 文件：{files}\n👥 用户：{users}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="返回")]]))
    elif act == "用户列表":
        lines = [f"{uid}｜{d['name']}" for uid, d in user_idx.items()]
        await q.edit_message_text("\n".join(lines[:50]) or "📭 无用户", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="返回")]]))
    elif act == "搜文件":
        admin_ops[cid] = "search"
        await q.edit_message_text("🔍 输入关键词", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="返回")]]))
    elif act == "查用户上传":
        admin_ops[cid] = "user_uploads"
        await q.edit_message_text("👤 输入用户ID", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="返回")]]))
    elif act == "删提取码":
        admin_ops[cid] = "del_code"
        await q.edit_message_text("🗑️ 输入提取码", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="返回")]]))
    elif act == "封禁/解封":
        admin_ops[cid] = "ban"
        await q.edit_message_text("🚫 封禁/解封 ID", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="返回")]]))

async def backup_cmd(update: Update, ctx: ContextTypes):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("❌ 无权限")
        return
    auto_backup()
    await update.message.reply_text("✅ 备份完成")

async def getdb_cmd(update: Update, ctx: ContextTypes):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("❌ 无权限")
        return
    for f in ["bot_db.json", "user_index.json", "banned.json"]:
        p = os.path.join(DATA_DIR, f)
        if os.path.exists(p):
            await update.message.reply_document(open(p, "rb"))
    await update.message.reply_text("✅ 导出完成")

# ==================== 启动 ====================
def main():
    if not BOT_TOKEN:
        print("❌ 未设置 TELEGRAM_BOT_TOKEN")
        exit(1)
    if not STORE_CHAT_ID:
        print("⚠️ 未设置 STORE_CHAT_ID，将无法保存文件")
    if ADMIN_USER_ID == 0:
        print("⚠️ 未设置 ADMIN_USER_ID")

    app = ApplicationBuilder().token(BOT_TOKEN).connect_timeout(30).read_timeout(60).write_timeout(60).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("my", my_codes))
    app.add_handler(CommandHandler("del", del_code))
    app.add_handler(CommandHandler("confirm", confirm))
    app.add_handler(CommandHandler("skip", skip))
    app.add_handler(CommandHandler("admin", admin))
    app.add_handler(CommandHandler("backup", backup_cmd))
    app.add_handler(CommandHandler("getdb", getdb_cmd))

    app.add_handler(CallbackQueryHandler(admin_cb))
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, upload))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handle))

    print("✅ 机器人启动成功（只发链接·防炸版）")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    while True:
        try:
            main()
        except:
            time.sleep(5)
