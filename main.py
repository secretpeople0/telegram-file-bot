import os
import json
import random
import shutil
string
import base64
import tempfile
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

# ==================== 全局配置（无频次限制）====================
ADMIN_USER_ID = int(os.environ.get("ADMIN_USER_ID", "0"))
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

# ==================== 加密核心（与旧代码完全一致，保证兼容）====================
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
        return base64.urlsafe_b64encode(encrypted).decode("utf-8")
    except:
        return ""

def decrypt_metadata(encrypted_str: str) -> dict:
    try:
        raw = base64.urlsafe_b64decode(encrypted_str.encode("utf-8"))
        decrypted = xor_data(raw, E2EE_KEY)
        return json.loads(decrypted.decode("utf-8"))
    except:
        return {}

# ==================== 兼容旧提取码 ====================
def get_file_unique_key(file_id):
    return file_id[:20] + file_id[-20:]

def encode_file_id(file_id: str) -> str:
    try:
        unique_key = get_file_unique_key(file_id)
        raw = json.dumps([file_id, unique_key]).encode()
        return base64.urlsafe_b64encode(xor_data(raw, ENCODE_KEY)).decode()
    except:
        return file_id

def decode_file_id(encoded_id: str) -> str:
    try:
        raw = xor_data(base64.urlsafe_b64decode(encoded_id.encode()), ENCODE_KEY)
        data = json.loads(raw.decode())
        if isinstance(data, list) and len(data) >= 1:
            return data[0]
        return str(data)
    except:
        return encoded_id

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

# ==================== 菜单 ====================
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
    await update.message.reply_text("📦 TG云盘机器人（直链加密版）\n/my 我的提取码\n/del 提取码")

async def my_codes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if is_banned(uid):
        return await update.message.reply_text("❌ 你已被封禁")
    items = [f"🔑 {c}｜{p['name']}" for c, p in bot_db.items() if str(p["uploader"]["id"]) == str(uid)]
    await update.message.reply_text("\n".join(items) if items else "📭 暂无文件")

async def del_code(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
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

# ==================== 【已修复：突破文件大小限制，不调用 get_file】 ====================
async def upload(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global BOT_SELF_ID
    if BOT_SELF_ID is None:
        me = await ctx.bot.get_me()
        BOT_SELF_ID = me.id

    u = update.effective_user
    if is_banned(u.id) or is_bot_self(u.id):
        return

    msg = update.message
    file_id = None
    orig_type = ""
    file_name = ""
    file_size = 0

    if msg.photo:
        file_id = msg.photo[-1].file_id
        orig_type = "photo"
        file_name = f"IMG_{datetime.now().strftime('%H%M%S')}.jpg"
    elif msg.video:
        file_id = msg.video.file_id
        orig_type = "video"
        file_name = msg.video.file_name or f"VID_{datetime.now().strftime('%H%M%S')}.mp4"
    elif msg.document:
        file_id = msg.document.file_id
        orig_type = "doc"
        file_name = msg.document.file_name or f"FILE_{datetime.now().strftime('%H%M%S')}"
    else:
        return

    try:
        # 不调用 get_file，彻底突破大小限制
        metadata = {
            "file_id": file_id,
            "name": file_name,
            "type": orig_type,
        }

        encrypted_meta = encrypt_metadata(metadata)

        obj = {
            "meta_type": "link_enc",
            "data": encrypted_meta,
            "name": file_name,
            "orig_type": orig_type
        }

        if u.id not in user_sessions:
            user_sessions[u.id] = []
        user_sessions[u.id].append(obj)

        await msg.reply_text(
            f"✅ 直链加密成功！已接收 {len(user_sessions[u.id])} 个文件\n"
            f"📄 {file_name}\n"
            f"/confirm 打包 | /skip 跳过命名"
        )
        await track(u.id, u.full_name, u.username)

    except Exception as e:
        await msg.reply_text(f"❌ 上传失败：{str(e)[:100]}")

async def confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user.id
    if u not in user_sessions or not user_sessions[u]:
        return await update.message.reply_text("❌ 暂无待打包文件")
    
    chars = string.ascii_letters + string.digits
    while True:
        code = ''.join(random.choice(chars) for _ in range(6))
        if code not in bot_db:
            break
    
    pending_naming[u] = {
        "code": code,
        "files": user_sessions[u],
        "uploader": {"id": u, "name": update.effective_user.full_name}
    }
    del user_sessions[u]
    await update.message.reply_text("📦 输入包名 或 /skip")

async def skip(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
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

# ==================== 提取 ====================
async def text_handle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global BOT_SELF_ID
    if BOT_SELF_ID is None:
        me = await ctx.bot.get_me()
        BOT_SELF_ID = me.id

    u = update.effective_user.id
    cid = update.effective_chat.id
    txt = update.message.text.strip()

    if is_bot_self(u) or is_banned(u):
        return

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
            return await update.message.reply_text(f"✅ 已删：{' '.join(dels)}\n❌ 不存在：{' '.join(nf)}")
        if act == "search":
            res = [f"🔑 {c}｜{p['name']}" for c, p in bot_db.items() if txt.lower() in p['name'].lower()]
            return await update.message.reply_text("\n".join(res) if res else "🔍 无结果")
        if act == "user_uploads":
            res = [f"🔑 {c}｜{p['name']}" for c, p in bot_db.items() if str(p["uploader"]["id"]) == txt.strip()]
            return await update.message.reply_text("\n".join(res) if res else "🔍 无记录")
        if act == "ban":
            parts = txt.split()
            if len(parts) != 2:
                return await update.message.reply_text("🚫 格式：封禁 123 / 解封 123")
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

    if u in pending_naming:
        pkg = pending_naming[u]
        pkg["name"] = txt[:50]
        bot_db[pkg["code"]] = pkg
        save_json("bot_db.json", bot_db)
        auto_backup()
        del pending_naming[u]
        return await update.message.reply_text(f"✅ 提取码：{pkg['code']}")

    if len(txt) == 6:
        if txt not in bot_db:
            return await update.message.reply_text("❌ 提取码不存在")
        
        pkg = bot_db[txt]
        await update.message.reply_text(f"📦 正在提取：{pkg['name']}")
        
        for idx, f in enumerate(pkg["files"], 1):
            try:
                if f.get("meta_type") == "link_enc":
                    meta = decrypt_metadata(f["data"])
                    if not meta:
                        await update.message.reply_text(f"⚠️ {idx} 解密失败")
                        continue

                    fid = meta.get("file_id")
                    name = meta.get("name")
                    typ = meta.get("type")

                    if typ == "photo":
                        await update.message.reply_photo(photo=fid, filename=name, caption=f"{idx}/{len(pkg['files'])}")
                    elif typ == "video":
                        await update.message.reply_video(video=fid, filename=name, caption=f"{idx}/{len(pkg['files'])}")
                    else:
                        await update.message.reply_document(document=fid, filename=name, caption=f"{idx}/{len(pkg['files'])}")

                elif f.get("type") == "enc":
                    fid = decode_file_id(f["id"])
                    file = await ctx.bot.get_file(fid, read_timeout=15)
                    data = await file.download_as_bytearray(read_timeout=15)
                    dec = xor_data(data, E2EE_KEY)
                    with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(f["name"])[1]) as tmp:
                        tmp.write(dec)
                        p = tmp.name
                    if f["orig_type"] == "photo":
                        await update.message.reply_photo(open(p, "rb"), filename=f["name"])
                    elif f["orig_type"] == "video":
                        await update.message.reply_video(open(p, "rb"), filename=f["name"])
                    else:
                        await update.message.reply_document(open(p, "rb"), filename=f["name"])
                    os.unlink(p)

            except Exception as e:
                await update.message.reply_text(f"❌ {idx} 失败")
        
        await update.message.reply_text("✅ 提取完成")
        return

# ==================== 管理员面板 ====================
async def admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        return await update.message.reply_text("❌ 无权限")
    await update.message.reply_text("👮 管理员面板", reply_markup=InlineKeyboardMarkup(admin_menu()))

async def admin_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.from_user.id != ADMIN_USER_ID:
        return await q.edit_message_text("❌ 无权限")
    
    act = q.data
    cid = q.message.chat.id

    if act == "返回":
        return await q.edit_message_text("👮 管理员面板", reply_markup=InlineKeyboardMarkup(admin_menu()))
    if act == "统计":
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
        await q.edit_message_text("🚫 封禁 123 / 解封 123", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="返回")]]))

async def backup_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID: return
    auto_backup()
    await update.message.reply_text("✅ 备份完成")

async def getdb_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID: return
    for f in ["bot_db.json", "user_index.json", "banned.json"]:
        p = os.path.join(DATA_DIR, f)
        if os.path.exists(p):
            await update.message.reply_document(open(p, "rb"))

# ==================== 启动 ====================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
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
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
