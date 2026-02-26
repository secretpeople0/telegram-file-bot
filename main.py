import os
import json
import random
import shutil
import string
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

# ==================== 宽松防炸（自动过期，绝不永久拉黑）====================
RATE_LIMIT_PER_MINUTE = 500  # 普通用户 1 分钟最多 500 次
ADMIN_USER_ID = int(os.environ.get("ADMIN_USER_ID", "0"))
RATE_LIMIT_FILE = "/data/rate_limit.json"

def load_rate_limit():
    if os.path.exists(RATE_LIMIT_FILE):
        try:
            with open(RATE_LIMIT_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_rate_limit(data):
    with open(RATE_LIMIT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def is_admin(user_id):
    return user_id == ADMIN_USER_ID

# ==================== 核心限流：自动清理，自动解除，永不拉黑 ====================
def check_allow(user_id):
    if is_admin(user_id):
        return True
    now = time.time()
    data = load_rate_limit()
    uid = str(user_id)

    # 每次必清理超过 60s 的旧记录 → 到时间一定自动解除
    if uid in data:
        data[uid] = [t for t in data[uid] if now - t < 60]
    else:
        data[uid] = []

    # 超过限制拒绝
    if len(data[uid]) >= RATE_LIMIT_PER_MINUTE:
        save_rate_limit(data)
        return False

    # 没超过就允许，并记录这次请求
    data[uid].append(now)
    save_rate_limit(data)
    return True

# 重置单个用户
def reset_user(user_id):
    data = load_rate_limit()
    uid = str(user_id)
    if uid in data:
        del data[uid]
        save_rate_limit(data)

# 一键清空所有限流（解除所有人）
def reset_all_rate_limit():
    save_rate_limit({})

# ==================== 加密 ====================
E2EE_KEY = b"e2ee_secure_bot_2026"
BOT_SELF_ID = None

def xor_data(data: bytes, key: bytes) -> bytes:
    key = key * (len(data) // len(key) + 1)
    return bytes(d ^ k for d, k in zip(data, key))

def get_file_unique_key(file_id):
    return file_id[:20] + file_id[-20:]

def encode_file_id(file_id: str) -> str:
    try:
        unique_key = get_file_unique_key(file_id)
        raw = json.dumps([file_id, unique_key]).encode()
        return base64.urlsafe_b64encode(xor_data(raw, b"secure_file_bot_2026")).decode()
    except:
        return file_id

def decode_file_id(encoded_id: str) -> str:
    try:
        raw = xor_data(base64.urlsafe_b64decode(encoded_id.encode()), b"secure_file_bot_2026")
        data = json.loads(raw.decode())
        if isinstance(data, list) and len(data) >= 1:
            return data[0]
        return str(data)
    except:
        return encoded_id

# ==================== 基础配置 ====================
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DATA_DIR = "/data"
BACKUP_DIR = os.path.join(DATA_DIR, "backup")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

user_sessions = {}
pending_naming = {}
admin_ops = {}

def load_json(name):
    p = os.path.join(DATA, name)
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {} if any(x in name for x in ["db", "index", "banned"]) else {}

def save_json(name, data):
    p = os.path.join(DATA_DIR, name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

bot_db = load_json("bot_db.json")
user_idx = load_json("user_index.json")
banned = load_json("banned.json")

# ==================== 自动备份 ====================
def auto_backup():
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    for f in ["bot_db.json", "user_index.json", "banned.json"]:
        src = os.path.join(DATA_DIR, f)
        dst = os.path.join(BACKUP_DIR, f"{f}.{ts}")
        if os.path.exists(src):
            shutil.copy(src, dst)
    olds = sorted([os.path.join(BACKUP_DIR, x) for x in os.listdir(BACKUP_DIR)], key=os.path.getmtime)
    for old in olds[:-3]:
        os.remove(old)

async def track(user_id, name, username):
    uid = str(user_id)
    if uid not in user_idx or user_idx[uid]["name"] != name:
        user_idx[uid] = {"name": name, "username": username or "-"}
        save_json("user_index.json", user_idx)

def is_banned(user_id):
    return str(user_id) in [str(x) for x in banned]

# ==================== 管理员菜单 ====================
def admin_menu():
    return [
        [InlineKeyboardButton("📊 统计", callback_data="统计")],
        [InlineKeyboardButton("👥 用户列表", callback_data="用户列表")],
        [InlineKeyboardButton("🔍 搜文件", callback_data="搜文件")],
        [InlineKeyboardButton("👁️ 查用户上传", callback_data="查用户上传")],
        [InlineKeyboardButton("🗑️ 删提取码", callback_data="删提取码")],
        [InlineKeyboardButton("🚫 封禁/解封", callback_data="封禁/解封")],
        [InlineKeyboardButton("🗑️ 清空所有限流", callback_data="清空所有限流")],
        [InlineKeyboardButton("🔙 返回", callback_data="返回")]
    ]

# ==================== 命令 ====================
async def start(update: Update, ctx: ContextTypes):
    await update.message.reply_text("📦 TG云盘机器人\n/my 我的提取码\n/del 提取码")

async def my_codes(update: Update, ctx: ContextTypes):
    uid = update.effective_user.id
    if is_banned(uid):
        await update.message.reply_text("❌ 你已被封禁")
        return
    items = [f"🔑 {c}｜{p['name']}" for c, p in bot_db.items() if str(p["uploader"]["id"]) == str(uid)]
    await update.message.reply_text("\n".join(items) if items else "📭 暂无文件")

async def del_code(update: Update, ctx: ContextTypes):
    uid = update.effective_user.id
    args = update.message.text.split()
    if len(args) < 2:
        await update.message.reply_text("用法：/del 提取码")
        return
    code = args[1]
    if code not in bot_db:
        await update.message.reply_text("❌ 不存在")
        return
    if str(bot_db[code]["uploader"]["id"]) != str(uid):
        await update.message.reply_text("❌ 只能删自己的")
        return
    del bot_db[code]
    save_json("bot_db.json", bot_db)
    await update.message.reply_text(f"✅ {code} 已删除")

# ==================== 上传 ====================
async def upload(update: Update, ctx: ContextTypes):
    global BOT_SELF_ID
    if BOT_SELF_ID is None:
        me = await ctx.bot.get_me()
        BOT_SELF_ID = me.id

    u = update.effective_user
    if is_banned(u.id) or u.id == BOT_SELF_ID:
        return

    # 限流判断：超限必回复提示，不会沉默
    if not check_allow(u.id):
        await update.message.reply_text("⏳ 操作频繁，1 分钟后会自动恢复，无需找管理员")
        return

    msg = update.message
    file_obj = None
    orig_type = ""
    file_name = ""

    if msg.photo:
        file_obj = msg.photo[-1]
        orig_type = "photo"
        file_name = f"IMG_{datetime.now().strftime('%H%M%S')}.jpg"
    elif msg.video:
        file_obj = msg.video
        orig_type = "video"
        file_name = msg.video.file_name or f"VID_{datetime.now().strftime('%H%M%S')}.mp4"
    elif msg.document:
        file_obj = msg.document
        orig_type = "doc"
        file_name = msg.document.file_name or f"FILE_{datetime.now().strftime('%H%M%S')}.bin"
    else:
        return

    temp_path = None
    try:
        f = await ctx.bot.get_file(file_obj.file_id, read_timeout=15)
        file_bytes = await f.download_as_bytearray(read_timeout=15)
        encrypted = xor_data(file_bytes, E2EE_KEY)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".enc") as temp_f:
            temp_f.write(encrypted)
            temp_path = temp_f.name

        sent_msg = await msg.reply_document(
            document=open(temp_path, "rb"),
            filename=f"{file_name}.enc",
            disable_notification=True,
            read_timeout=15
        )
        await ctx.bot.delete_message(chat_id=sent_msg.chat.id, message_id=sent_msg.message_id)
        os.unlink(temp_path)

        obj = {
            "type": "enc",
            "id": encode_file_id(sent_msg.document.file_id),
            "name": file_name,
            "orig_type": orig_type
        }
        if u.id not in user_sessions:
            user_sessions[u.id] = []
        user_sessions[u.id].append(obj)
        await msg.reply_text(f"✅ 加密成功！已收 {len(user_sessions[u.id])} 个\n/confirm 打包")
        await track(u.id, u.full_name, u.username)
    except Exception as e:
        if temp_path:
            os.unlink(temp_path)
        await msg.reply_text(f"❌ 上传失败：{str(e)[:80]}")

async def confirm(update: Update, ctx: ContextTypes):
    uid = update.effective_user.id
    if not check_allow(uid):
        await update.message.reply_text("⏳ 操作频繁，1 分钟后自动恢复")
        return
    if uid not in user_sessions or not user_sessions[uid]:
        await update.message.reply_text("❌ 暂无文件")
        return
    chars = string.ascii_letters + string.digits
    code = ''.join(random.choice(chars) for _ in range(6))
    while code in bot_db:
        code = ''.join(random.choice(chars) for _ in range(6))
    pending_naming[uid] = {
        "code": code,
        "files": user_sessions[uid],
        "uploader": {"id": uid, "name": update.effective_user.full_name}
    }
    del user_sessions[uid]
    await update.message.reply_text("📦 输入包名 /skip 跳过")

async def skip(update: Update, ctx: ContextTypes):
    uid = update.effective_user.id
    if not check_allow(uid):
        await update.message.reply_text("⏳ 操作频繁，1 分钟后自动恢复")
        return
    if uid not in pending_naming:
        await update.message.reply_text("❌ 无待打包")
        return
    pkg = pending_naming[uid]
    pkg["name"] = f"包_{pkg['code']}"
    bot_db[pkg["code"]] = pkg
    save_json("bot_db.json", bot_db)
    auto_backup()
    del pending_naming[uid]
    await update.message.reply_text(f"✅ 提取码：{pkg['code']}")

# ==================== 文本/提取码 ====================
async def text_handle(update: Update, ctx: ContextTypes):
    global BOT_SELF_ID
    if BOT_SELF_ID is None:
        me = await ctx.bot.get_me()
        BOT_SELF_ID = me.id

    uid = update.effective_user.id
    txt = update.message.text.strip()
    cid = update.effective_chat.id

    if uid == BOT_SELF_ID:
        return

    # 限流：超限就提示，不会沉默不回复
    if not check_allow(uid):
        await update.message.reply_text("⏳ 操作频繁，1 分钟后自动恢复，不会永久限制")
        return

    if is_admin(uid) and cid in admin_ops:
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
            await update.message.reply_text(f"✅ 删：{' '.join(dels)}\n❌ 无：{' '.join(nf)}" if dels else "❌ 未找到")
            return
        if act == "search":
            res = [f"{c}｜{p['name']}" for c, p in bot_db.items() if txt.lower() in p['name'].lower()]
            await update.message.reply_text("\n".join(res[:20]) if res else "无")
            return
        if act == "user_uploads":
            res = [f"{c}｜{p['name']}" for c, p in bot_db.items() if str(p["uploader"]["id"]) == txt.strip()]
            await update.message.reply_text("\n".join(res[:20]) if res else "无")
            return
        if act == "ban":
            parts = txt.split()
            if len(parts) != 2:
                await update.message.reply_text("格式：封禁 123 / 解封 123")
                return
            cmd, tid = parts
            try:
                tid = int(tid)
                if cmd == "封禁":
                    if tid not in banned:
                        banned.append(tid)
                    await update.message.reply_text(f"✅ 封禁 {tid}")
                elif cmd == "解封":
                    if tid in banned:
                        banned.remove(tid)
                    await update.message.reply_text(f"✅ 解封 {tid}")
                save_json("banned.json", banned)
            except:
                await update.message.reply_text("❌ ID错误")
            return

    if is_banned(uid):
        await update.message.reply_text("❌ 你已被封禁")
        return

    if uid in pending_naming:
        pkg = pending_naming[uid]
        pkg["name"] = txt[:50]
        bot_db[pkg["code"]] = pkg
        save_json("bot_db.json", bot_db)
        auto_backup()
        del pending_naming[uid]
        await update.message.reply_text(f"✅ 提取码：{pkg['code']}")
        return

    if len(txt) == 6:
        if txt not in bot_db:
            await update.message.reply_text("❌ 不存在")
            return
        pkg = bot_db[txt]
        await update.message.reply_text(f"📦 {pkg['name']}")
        for f in pkg["files"]:
            temp_path = None
            try:
                fid = decode_file_id(f["id"])
                file = await ctx.bot.get_file(fid, read_timeout=15, write_timeout=15, connect_timeout=10)
                data = await file.download_as_bytearray(connect_timeout=10, read_timeout=15)
                dec = xor_data(data, E2EE_KEY)
                ext = os.path.splitext(f["name"])[1]
                with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                    tmp.write(dec)
                    tmp_path = tmp.name
                if f["orig_type"] == "photo":
                    await update.message.reply_photo(open(tmp_path, "rb"), filename=f["name"], read_timeout=15)
                elif f["orig_type"] == "video":
                    await update.message.reply_video(open(tmp_path, "rb"), filename=f["name"], read_timeout=15)
                else:
                    await update.message.reply_document(open(tmp_path, "rb"), filename=f["name"], read_timeout=15)
                os.unlink(tmp_path)
            except Exception as e:
                if temp_path:
                    os.unlink(temp_path)
                await update.message.reply_text(f"⚠️ 加载失败：{f['name']}")
        return

# ==================== 管理员面板 ====================
async def admin(update: Update, ctx: ContextTypes):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("❌ 无权限")
        return
    await update.message.reply_text("👮 管理面板", reply_markup=InlineKeyboardMarkup(admin_menu()))

async def admin_cb(update: Update, ctx: ContextTypes):
    q = update.callback_query
    await q.answer()
    if q.from_user.id != ADMIN_USER_ID:
        return

    act = q.data
    if act == "返回":
        await q.edit_message_text("👮 管理面板", reply_markup=InlineKeyboardMarkup(admin_menu()))
    elif act == "统计":
        pkg_cnt = len(bot_db)
        file_cnt = sum(len(v["files"]) for v in bot_db.values())
        usr_cnt = len(user_idx)
        await q.edit_message_text(f"📊 统计\n包：{pkg_cnt}\n文件：{file_cnt}\n用户：{usr_cnt}",
                                  reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="返回")]]))
    elif act == "用户列表":
        lines = [f"{uid}｜{d['name']}" for uid, d in user_idx.items()]
        text = "\n".join(lines[:30]) + "\n…仅显示前30个" if len(lines) > 30 else "\n".join(lines)
        await q.edit_message_text(text or "无用户", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="返回")]]))
    elif act == "搜文件":
        admin_ops[q.message.chat.id] = "search"
        await q.edit_message_text("🔍 输入关键词", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="返回")]]))
    elif act == "查用户上传":
        admin_ops[q.message.chat.id] = "user_uploads"
        await q.edit_message_text("👤 输入用户ID", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="返回")]]))
    elif act == "删提取码":
        admin_ops[q.message.chat.id] = "del_code"
        await q.edit_message_text("🗑️ 输入提取码", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="返回")]]))
    elif act == "封禁/解封":
        admin_ops[q.message.chat.id] = "ban"
        await q.edit_message_text("🚫 输入：封禁 123 / 解封 123", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="返回")]]))
    elif act == "清空所有限流":
        reset_all_rate_limit()
        await q.edit_message_text("✅ 已清空所有用户限流 → 所有人立即恢复", reply_markup=InlineKeyboardMarkup(admin_menu()))

async def backup_cmd(update: Update, ctx):
    if update.effective_user.id != ADMIN_USER_ID:
        return
    auto_backup()
    await update.message.reply_text("✅ 已备份")

async def getdb_cmd(update: Update, ctx):
    if update.effective_user.id != ADMIN_USER_ID:
        return
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
