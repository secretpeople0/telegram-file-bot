import os
import json
import random
import shutil
import string
import base64
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

# ==================== 跨机器人兼容层（增强版，不影响原有功能）====================
def get_file_unique_key(file_id):
    """生成文件唯一标识，让新旧机器人都能识别"""
    return file_id[:20] + file_id[-20:]

# ==================== 轻量加密（防扫描、不影响任何功能）====================
def xor_crypt(data: bytes, key: bytes = b"secure_file_bot_2026") -> bytes:
    key = key * (len(data) // len(key) + 1)
    return bytes(d ^ k for d, k in zip(data, key))

def encode_file_id(file_id: str) -> str:
    try:
        # 兼容层：保存文件唯一特征，方便跨机器人恢复
        unique_key = get_file_unique_key(file_id)
        raw = json.dumps([file_id, unique_key]).encode()
        return base64.urlsafe_b64encode(xor_crypt(raw)).decode()
    except:
        return file_id

def decode_file_id(encoded_id: str) -> str:
    try:
        raw = xor_crypt(base64.urlsafe_b64decode(encoded_id.encode()))
        data = json.loads(raw.decode())
        if isinstance(data, list) and len(data) >= 1:
            return data[0]
        return str(data)
    except:
        return encoded_id

# ==================== 核心配置 ====================
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ADMIN_USER_ID = int(os.environ.get("ADMIN_USER_ID", "0"))

DATA_DIR = "/data"
BACKUP_DIR = os.path.join(DATA_DIR, "backup")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

user_sessions = {}
pending_naming = {}
admin_ops = {}

# ==================== 数据读写 ====================
def load_json(name):
    p = os.path.join(DATA_DIR, name)
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {} if any(x in name for x in ["db", "index", "banned"]) else []

def save_json(name, data):
    p = os.path.join(DATA_DIR, name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

bot_db    = load_json("bot_db.json")
user_idx  = load_json("user_index.json")
banned    = load_json("banned.json")

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

# ==================== 工具 ====================
async def track(user_id, name, username):
    uid = str(user_id)
    if uid not in user_idx or user_idx[uid]["name"] != name:
        user_idx[uid] = {"name": name, "username": username or "-"}
        save_json("user_index.json", user_idx)

def is_banned(user_id):
    return str(user_id) in [str(x) for x in banned]

# ==================== 管理员菜单（中文按钮 + 中文 callback_data）====================
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
    await update.message.reply_text("📦 TG云盘机器人\n/my 我的提取码\n/del 提取码")

async def my_codes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if is_banned(uid):
        return await update.message.reply_text("❌ 你已被封禁")
    items = [f"🔑 {c}｜{p['name']}" for c,p in bot_db.items() if str(p["uploader"]["id"])==str(uid)]
    await update.message.reply_text("\n".join(items) if items else "📭 暂无文件")

async def del_code(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    args = update.message.text.split()
    if len(args)<2:
        return await update.message.reply_text("用法：/del 提取码")
    code = args[1]
    if code not in bot_db:
        return await update.message.reply_text("❌ 不存在")
    if str(bot_db[code]["uploader"]["id"])!=str(uid):
        return await update.message.reply_text("❌ 只能删自己的")
    del bot_db[code]
    save_json("bot_db.json", bot_db)
    await update.message.reply_text(f"✅ {code} 已删除")

async def upload(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if is_banned(u.id):
        return
    msg = update.message
    obj = None

    if msg.photo:
        fid = msg.photo[-1].file_id
        obj = {"type":"img","id":encode_file_id(fid),"name":f"IMG_{datetime.now().strftime('%H%M%S')}.jpg"}
    elif msg.video:
        fid = msg.video.file_id
        nm  = msg.video.file_name or f"VID_{datetime.now().strftime('%H%M%S')}.mp4"
        obj = {"type":"video","id":encode_file_id(fid),"name":nm}
    elif msg.document:
        fid = msg.document.file_id
        nm  = msg.document.file_name or "file"
        obj = {"type":"doc","id":encode_file_id(fid),"name":nm}
    else:
        return

    if u.id not in user_sessions:
        user_sessions[u.id] = []
    user_sessions[u.id].append(obj)
    await msg.reply_text(f"✅ 已收 {len(user_sessions[u.id])} 个\n/confirm 打包")
    await track(u.id, u.full_name, u.username)

async def confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user.id
    if u not in user_sessions or not user_sessions[u]:
        return await update.message.reply_text("❌ 暂无文件")
    chars = string.ascii_letters + string.digits
    while True:
        code = ''.join(random.choice(chars) for _ in range(6))
        if code not in bot_db:
            break
    pending_naming[u] = {
        "code":code,
        "files":user_sessions[u],
        "uploader":{"id":u,"name":update.effective_user.full_name}
    }
    del user_sessions[u]
    await update.message.reply_text("📦 输入包名 /skip 跳过")

async def skip(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user.id
    if u not in pending_naming:
        return await update.message.reply_text("❌ 无待打包")
    pkg = pending_naming[u]
    pkg["name"] = f"包_{pkg['code']}"
    bot_db[pkg["code"]] = pkg
    save_json("bot_db.json", bot_db)
    auto_backup()
    del pending_naming[u]
    await update.message.reply_text(f"✅ 提取码：{pkg['code']}")

async def text_handle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u   = update.effective_user.id
    cid = update.effective_chat.id
    txt = update.message.text.strip()

    # 管理员操作
    if update.effective_user.id == ADMIN_USER_ID and cid in admin_ops:
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
            return await update.message.reply_text(f"✅删：{' '.join(dels)}\n❌无：{' '.join(nf)}")
        if act == "search":
            res = [f"{c}｜{p['name']}" for c,p in bot_db.items() if txt.lower() in p['name'].lower()]
            return await update.message.reply_text("\n".join(res) if res else "无")
        if act == "user_uploads":
            res = [f"{c}｜{p['name']}" for c,p in bot_db.items() if str(p['uploader']['id'])==txt.strip()]
            return await update.message.reply_text("\n".join(res) if res else "无")
        if act == "ban":
            parts = txt.split()
            if len(parts)!=2:
                return await update.message.reply_text("格式：封禁 123 / 解封 123")
            cmd,tid = parts
            try:
                tid=int(tid)
                if cmd=="封禁":
                    if tid not in banned:
                        banned.append(tid)
                    await update.message.reply_text(f"✅ 封禁 {tid}")
                elif cmd=="解封":
                    if tid in banned:
                        banned.remove(tid)
                    await update.message.reply_text(f"✅ 解封 {tid}")
                save_json("banned.json", banned)
            except:
                await update.message.reply_text("❌ ID错误")
        return

    if is_banned(u):
        return

    # 命名
    if u in pending_naming:
        pkg = pending_naming[u]
        pkg["name"] = txt[:50]
        bot_db[pkg["code"]] = pkg
        save_json("bot_db.json", bot_db)
        auto_backup()
        del pending_naming[u]
        return await update.message.reply_text(f"✅ 提取码：{pkg['code']}")

    # 提取码
    if len(txt) == 6:
        if txt not in bot_db:
            return await update.message.reply_text("❌ 不存在")
        pkg = bot_db[txt]
        await update.message.reply_text(f"📦 {pkg['name']}")
        for f in pkg["files"]:
            try:
                fid = decode_file_id(f["id"])
                if f["type"] == "img":
                    await update.message.reply_photo(fid)
                elif f["type"] == "video":
                    await update.message.reply_video(fid)
                elif f["type"] == "doc":
                    await update.message.reply_document(fid)
            except Exception as e:
                await update.message.reply_text(f"⚠️ 无法发送：{f['name']}")

# ==================== 管理员面板 ====================
async def admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        return await update.message.reply_text("❌ 无权限")
    await update.message.reply_text("👮 管理面板", reply_markup=InlineKeyboardMarkup(admin_menu()))

async def admin_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.from_user.id != ADMIN_USER_ID:
        return await q.edit_message_text("❌ 无权限")
    act = q.data
    cid = q.message.chat.id

    if act == "返回":
        return await q.edit_message_text("👮 管理面板", reply_markup=InlineKeyboardMarkup(admin_menu()))

    if act == "统计":
        pkg_cnt = len(bot_db)
        file_cnt= sum(len(v["files"]) for v in bot_db.values())
        usr_cnt = len(user_idx)
        await q.edit_message_text(f"📊 统计\n包：{pkg_cnt}\n文件：{file_cnt}\n用户：{usr_cnt}",
                          reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回",callback_data="返回")]]))
    elif act == "用户列表":
        lines = [f"{uid}｜{d['name']}" for uid,d in list(user_idx.items())[:50]]
        await q.edit_message_text("\n".join(lines) if lines else "无用户",
                          reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回",callback_data="返回")]]))
    elif act == "搜文件":
        admin_ops[cid] = "search"
        await q.edit_message_text("🔍 输入关键词",
                          reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回",callback_data="返回")]]))
    elif act == "查用户上传":
        admin_ops[cid] = "user_uploads"
        await q.edit_message_text("👤 输入用户ID",
                          reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回",callback_data="返回")]]))
    elif act == "删提取码":
        admin_ops[cid] = "del_code"
        await q.edit_message_text("🗑️ 输入提取码（空格分隔）",
                          reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回",callback_data="返回")]]))
    elif act == "封禁/解封":
        admin_ops[cid] = "ban"
        await q.edit_message_text("🚫 格式：封禁 123 / 解封 123",
                          reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回",callback_data="返回")]]))

async def backup_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID: return
    auto_backup()
    await update.message.reply_text("✅ 已备份")

async def getdb_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID: return
    for f in ["bot_db.json","user_index.json","banned.json"]:
        p = os.path.join(DATA_DIR,f)
        if os.path.exists(p):
            await update.message.reply_document(open(p,"rb"))

# ==================== 启动 ====================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",   start))
    app.add_handler(CommandHandler("my",      my_codes))
    app.add_handler(CommandHandler("del",     del_code))
    app.add_handler(CommandHandler("confirm", confirm))
    app.add_handler(CommandHandler("skip",    skip))
    app.add_handler(CommandHandler("admin",   admin))
    app.add_handler(CommandHandler("backup",  backup_cmd))
    app.add_handler(CommandHandler("getdb",   getdb_cmd))
    app.add_handler(CallbackQueryHandler(admin_cb))
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, upload))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handle))
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
