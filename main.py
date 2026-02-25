import os
import json
import random
import shutil
import string
import base64
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    Filters,
    CallbackContext
)

# ====================== 轻量 XOR 加密（无第三方库，100%兼容）======================
def xor_crypt(data: bytes, key: bytes = b"tg_bot_secure") -> bytes:
    key = key * (len(data) // len(key) + 1)
    return bytes([d ^ k for d, k in zip(data, key)])

def encode_file_id(file_id: str) -> str:
    try:
        encrypted = xor_crypt(file_id.encode("utf-8"))
        return base64.urlsafe_b64encode(encrypted).decode("utf-8")
    except:
        return file_id

def decode_file_id(encoded_id: str) -> str:
    try:
        encrypted = base64.urlsafe_b64decode(encoded_id.encode("utf-8"))
        return xor_crypt(encrypted).decode("utf-8")
    except:
        return encoded_id

# ====================== 万能 file_id 解码 ======================
def decode_any_file_id(file_id):
    try:
        import struct
        if file_id.startswith(('s', 'v', 'f', 'w', 'g', 'p', 'c', 'A')):
            try:
                decoded = base64.urlsafe_b64decode(file_id + '=' * (-len(file_id) % 4))
                ver = decoded[0]
                if ver == 2:
                    ptr = 1
                    dc = struct.unpack('<i', decoded[ptr:ptr+4])[0]
                    ptr += 4
                    id = struct.unpack('<q', decoded[ptr:ptr+8])[0]
                    ptr += 8
                    access_hash = struct.unpack('<q', decoded[ptr:ptr+8])[0]
                    ptr += 8
                    volume_id = struct.unpack('<q', decoded[ptr:ptr+8])[0]
                    ptr += 8
                    local_id = struct.unpack('<i', decoded[ptr:ptr+4])[0]
                    ptr += 4
                    return file_id
            except:
                pass
        return file_id
    except:
        return file_id

# ====================== 核心配置 ======================
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ADMIN_USER_ID = int(os.environ.get("ADMIN_USER_ID", "0"))
BACKUP_INTERVAL = 30 * 60
MAX_BACKUP_COUNT = 2

user_sessions = {}
pending_naming = {}
admin_operations = {}

# ====================== 数据目录 ======================
DATA_DIR = "/data"
BACKUP_DIR = os.path.join(DATA_DIR, "backup")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

def load_json(filename):
    path = os.path.join(DATA_DIR, filename)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {} if "db" in filename or "index" in filename else []

def save_json(filename, data):
    path = os.path.join(DATA_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return True

bot_db = load_json("bot_db.json")
user_index = load_json("user_index.json")
banned_users = load_json("banned.json")

# ====================== 自动备份 ======================
last_backup_time = 0
def auto_backup():
    global last_backup_time
    now = datetime.now().timestamp()
    if now - last_backup_time < BACKUP_INTERVAL:
        return
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    for fname in ["bot_db.json", "user_index.json", "banned.json"]:
        src = os.path.join(DATA_DIR, fname)
        dst = os.path.join(BACKUP_DIR, f"{fname}.{ts}")
        if os.path.exists(src):
            shutil.copy(src, dst)
    backups = sorted([os.path.join(BACKUP_DIR, f) for f in os.listdir(BACKUP_DIR)], key=os.path.getmtime, reverse=True)
    for old in backups[MAX_BACKUP_COUNT:]:
        os.remove(old)
    last_backup_time = now

# ====================== 用户追踪 ======================
def track_user(user_id: int, full_name: str, username: str):
    uid = str(user_id)
    if uid not in user_index or user_index[uid]["name"] != full_name:
        user_index[uid] = {"name": full_name, "username": username or "无"}
        save_json("user_index.json", user_index)

def is_banned(user_id: int) -> bool:
    return user_id in banned_users

# ====================== 菜单 ======================
def back_to_admin_menu():
    return [
        [InlineKeyboardButton("📊 统计总数", callback_data="stats")],
        [InlineKeyboardButton("👥 用户列表", callback_data="list_users")],
        [InlineKeyboardButton("🔍 搜索文件", callback_data="search")],
        [InlineKeyboardButton("👁️ 查看用户上传", callback_data="view_user_upload")],
        [InlineKeyboardButton("🗑️ 删除提取码", callback_data="delete_code")],
        [InlineKeyboardButton("🚫 封禁/解封", callback_data="ban_user")],
        [InlineKeyboardButton("🔙 返回管理菜单", callback_data="back_to_admin")]
    ]

def admin_panel(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_USER_ID:
        update.message.reply_text("❌ 无权限")
        return
    update.message.reply_text("👮 管理菜单", reply_markup=InlineKeyboardMarkup(back_to_admin_menu()))

def admin_callback_handler(update: Update, context: CallbackContext):
    q = update.callback_query
    q.answer()
    if q.from_user.id != ADMIN_USER_ID:
        q.edit_message_text("❌ 权限不足")
        return
    action = q.data
    cid = q.message.chat.id

    if action == "back_to_admin":
        q.edit_message_text("👮 返回管理菜单", reply_markup=InlineKeyboardMarkup(back_to_admin_menu()))
        return

    if action == "stats":
        tp = len(bot_db)
        tf = sum(len(p["files"]) for p in bot_db.values())
        tu = len(user_index)
        q.edit_message_text(f"📊 统计\n总用户：{tu}\n文件包：{tp}\n文件数：{tf}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="back_to_admin")]]))
    elif action == "list_users":
        if not user_index:
            q.edit_message_text("❌ 无用户", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="back_to_admin")]]))
            return
        msg = "👥 用户列表\n"
        cnt = 0
        for uid, info in user_index.items():
            if cnt >= 50: break
            msg += f"ID: {uid} | {info['name']}\n"
            cnt +=1
        q.edit_message_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="back_to_admin")]]))
    elif action == "search":
        q.edit_message_text("🔍 发关键词搜索", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="back_to_admin")]]))
        admin_operations[cid] = "search"
    elif action == "view_user_upload":
        q.edit_message_text("👤 发用户ID查询", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="back_to_admin")]]))
        admin_operations[cid] = "view_user_upload"
    elif action == "delete_code":
        q.edit_message_text("🗑️ 发送提取码", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="back_to_admin")]]))
        admin_operations[cid] = "delete_code"
    elif action == "ban_user":
        q.edit_message_text("🚫 封禁 123 / 解封 123", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="back_to_admin")]]))
        admin_operations[cid] = "ban_user"

def handle_admin_input(update: Update, context: CallbackContext):
    uid = update.effective_user.id
    cid = update.effective_chat.id
    txt = update.message.text.strip()
    if uid != ADMIN_USER_ID or cid not in admin_operations:
        return
    act = admin_operations.pop(cid)
    if act == "delete_code":
        dels = []
        nf = []
        for code in txt.split():
            if code in bot_db:
                del bot_db[code]
                dels.append(code)
            else:
                nf.append(code)
        save_json("bot_db.json", bot_db)
        update.message.reply_text(f"✅ 删：{' '.join(dels)}\n❌ 无：{' '.join(nf)}" if dels or nf else "❌ 无操作")
    elif act == "search":
        res = [f"{c}｜{p['name']}" for c,p in bot_db.items() if txt.lower() in p['name'].lower()]
        update.message.reply_text("\n".join(res) if res else "❌ 无结果")
    elif act == "view_user_upload":
        tuid = txt.strip()
        res = [f"{c}｜{p['name']}" for c,p in bot_db.items() if str(p['uploader']['id']) == tuid]
        update.message.reply_text("\n".join(res) if res else "❌ 无记录")
    elif act == "ban_user":
        p = txt.split()
        if len(p)!=2:
            update.message.reply_text("❌ 格式：封禁 123")
            return
        cmd,tid = p
        try:
            tid=int(tid)
            if cmd=="封禁":
                if tid not in banned_users:
                    banned_users.append(tid)
                    save_json("banned.json", banned_users)
                update.message.reply_text(f"✅ 封禁 {tid}")
            elif cmd=="解封":
                if tid in banned_users:
                    banned_users.remove(tid)
                    save_json("banned.json", banned_users)
                update.message.reply_text(f"✅ 解封 {tid}")
        except:
            update.message.reply_text("❌ ID错误")

def my_codes(update: Update, context: CallbackContext):
    uid = update.effective_user.id
    if is_banned(uid):
        update.message.reply_text("❌ 已封禁")
        return
    lst = [f"🔑 {c}｜{p['name']}" for c,p in bot_db.items() if str(p['uploader']['id'])==str(uid)]
    update.message.reply_text("\n".join(lst) if lst else "📭 暂无提取码")

def del_my_code(update: Update, context: CallbackContext):
    uid = update.effective_user.id
    args = update.message.text.split()
    if len(args)<2:
        update.message.reply_text("📌 /del 提取码")
        return
    code = args[1]
    if code not in bot_db:
        update.message.reply_text("❌ 不存在")
        return
    if str(bot_db[code]['uploader']['id'])!=str(uid):
        update.message.reply_text("❌ 无权删除")
        return
    del bot_db[code]
    save_json("bot_db.json", bot_db)
    update.message.reply_text(f"✅ {code} 已删除")

def start(update: Update, context: CallbackContext):
    update.message.reply_text("👋 文件存储机器人\n📌 /my 我的\n🗑️ /del 提取码")

# ====================== 上传：编码存储 ======================
def upload_file(update: Update, context: CallbackContext):
    u = update.effective_user
    if is_banned(u.id):
        update.message.reply_text("❌ 已封禁")
        return
    msg = update.message
    fd = None
    if msg.photo:
        raw = msg.photo[-1].file_id
        enc = encode_file_id(decode_any_file_id(raw))
        fd = {"type":"img","id":enc,"name":f"图片_{datetime.now().strftime('%H%M%S')}.jpg"}
    elif msg.video:
        raw = msg.video.file_id
        enc = encode_file_id(decode_any_file_id(raw))
        fd = {"type":"video","id":enc,"name":msg.video.file_name or f"视频_{datetime.now().strftime('%H%M%S')}.mp4"}
    elif msg.document:
        raw = msg.document.file_id
        enc = encode_file_id(decode_any_file_id(raw))
        fd = {"type":"doc","id":enc,"name":msg.document.file_name or "文件"}
    else:
        msg.reply_text("❌ 仅支持图片/视频/文档")
        return
    if u.id not in user_sessions:
        user_sessions[u.id] = []
    user_sessions[u.id].append(fd)
    msg.reply_text(f"✅ 已收 {len(user_sessions[u.id])} 个\n/confirm 打包")
    track_user(u.id, u.full_name, u.username)

def confirm_package(update: Update, context: CallbackContext):
    u = update.effective_user
    if u.id not in user_sessions or not user_sessions[u.id]:
        update.message.reply_text("❌ 无文件")
        return
    chars = string.ascii_letters + string.digits
    while True:
        code = ''.join(random.choice(chars) for _ in range(6))
        if code not in bot_db:
            break
    pending_naming[u.id] = {"code":code,"files":user_sessions[u.id],"uploader":{"id":u.id,"name":u.full_name}}
    del user_sessions[u.id]
    update.message.reply_text("📦 输入包名 /skip 跳过")

def skip_naming(update: Update, context: CallbackContext):
    u = update.effective_user
    if u.id not in pending_naming:
        update.message.reply_text("❌ 无待命名")
        return
    pkg = pending_naming[u.id]
    pkg["name"] = f"文件包_{pkg['code']}"
    bot_db[pkg["code"]] = pkg
    save_json("bot_db.json", bot_db)
    auto_backup()
    del pending_naming[u.id]
    update.message.reply_text(f"✅ 提取码：{pkg['code']}")

def user_text_handler(update: Update, context: CallbackContext):
    u = update.effective_user
    cid = update.effective_chat.id
    txt = update.message.text.strip()
    if u.id == ADMIN_USER_ID and cid in admin_operations:
        handle_admin_input(update, context)
        return
    if is_banned(u.id):
        update.message.reply_text("❌ 已封禁")
        return
    if u.id in pending_naming:
        pkg = pending_naming[u.id]
        pkg["name"] = txt[:50]
        bot_db[pkg["code"]] = pkg
        save_json("bot_db.json", bot_db)
        auto_backup()
        del pending_naming[u.id]
        update.message.reply_text(f"✅ 提取码：{pkg['code']}")
        return
    if len(txt) == 6:
        if txt not in bot_db:
            update.message.reply_text("❌ 不存在")
            return
        pkg = bot_db[txt]
        update.message.reply_text(f"📦 {pkg['name']}")
        for f in pkg["files"]:
            try:
                real_id = decode_file_id(f["id"])
                real_id = decode_any_file_id(real_id)
                if f["type"] == "img":
                    update.message.reply_photo(real_id)
                elif f["type"] == "video":
                    update.message.reply_video(real_id)
                elif f["type"] == "doc":
                    update.message.reply_document(real_id)
            except Exception as e:
                update.message.reply_text(f"⚠️ 发送失败：{f['name']}")

def manual_backup(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_USER_ID:
        return
    auto_backup()
    update.message.reply_text("✅ 备份完成")

def send_db(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_USER_ID:
        return
    for f in ["bot_db.json","user_index.json","banned.json"]:
        p = os.path.join(DATA_DIR, f)
        if os.path.exists(p):
            update.message.reply_document(open(p,"rb"))

# ====================== 启动 ======================
def main():
    updater = Updater(token=BOT_TOKEN)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("my", my_codes))
    dp.add_handler(CommandHandler("del", del_my_code))
    dp.add_handler(CommandHandler("confirm", confirm_package))
    dp.add_handler(CommandHandler("skip", skip_naming))
    dp.add_handler(CommandHandler("admin", admin_panel))
    dp.add_handler(CommandHandler("backup", manual_backup))
    dp.add_handler(CommandHandler("getdb", send_db))
    dp.add_handler(CallbackQueryHandler(admin_callback_handler))
    dp.add_handler(MessageHandler(Filters.photo | Filters.video | Filters.document, upload_file))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, user_text_handler))

    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
