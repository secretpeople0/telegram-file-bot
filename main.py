import os
import json
import random
import shutil
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

# ====================== 核心配置 ======================
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ADMIN_USER_ID = int(os.environ.get("ADMIN_USER_ID", "0"))
BACKUP_INTERVAL = 30 * 60
MAX_BACKUP_COUNT = 2

# 内存临时存储
user_sessions = {}
pending_naming = {}
admin_operations = {}

# ====================== 数据目录 ======================
DATA_DIR = "/data"
BACKUP_DIR = os.path.join(DATA_DIR, "backup")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

# ====================== JSON 读写 ======================
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
async def track_user(user_id, full_name, username):
    uid = str(user_id)
    if uid not in user_index or user_index[uid]["name"] != full_name:
        user_index[uid] = {"name": full_name, "username": username or "无"}
        save_json("user_index.json", user_index)

def is_banned(user_id):
    return user_id in banned_users

# ====================== 返回主菜单按钮（你要的返回键） ======================
def back_to_admin_menu():
    return [
        [InlineKeyboardButton("📊 统计总数", callback_data="stats")],
        [InlineKeyboardButton("👥 查看用户列表", callback_data="list_users")],
        [InlineKeyboardButton("🔍 搜索文件", callback_data="search")],
        [InlineKeyboardButton("👁️ 查看用户上传", callback_data="view_user_upload")],
        [InlineKeyboardButton("🗑️ 删除提取码", callback_data="delete_code")],
        [InlineKeyboardButton("🚫 封禁/解封", callback_data="ban_user")],
        [InlineKeyboardButton("🔙 返回管理菜单", callback_data="back_to_admin")]
    ]

# ====================== 管理员面板 ======================
async def admin_panel(update: Update, _):
    user_id = update.effective_user.id
    if user_id != ADMIN_USER_ID:
        await update.message.reply_text("❌ 无权限")
        return
    keyboard = back_to_admin_menu()
    await update.message.reply_text("👮 管理菜单", reply_markup=InlineKeyboardMarkup(keyboard))

# ====================== 管理员回调（含返回键） ======================
async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if user_id != ADMIN_USER_ID:
        await query.edit_message_text("❌ 权限不足")
        return
    action = query.data
    chat_id = query.message.chat.id

    # 返回菜单
    if action == "back_to_admin":
        await query.edit_message_text("👮 返回管理菜单", reply_markup=InlineKeyboardMarkup(back_to_admin_menu()))
        return

    if action == "stats":
        total_pkgs = len(bot_db)
        total_files = sum(len(p["files"]) for p in bot_db.values())
        total_users = len(user_index)
        await query.edit_message_text(f"📊 统计\n总用户：{total_users}\n文件包：{total_pkgs}\n文件数：{total_files}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="back_to_admin")]]))

    elif action == "list_users":
        if not user_index:
            await query.edit_message_text("❌ 无用户")
            return
        msg = "👥 用户列表\n"
        for i, (uid, info) in enumerate(user_index.items()):
            if i >= 50: break
            msg += f"ID: {uid} | {info['name']}\n"
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="back_to_admin")]]))

    elif action == "search":
        await query.edit_message_text("🔍 发关键词搜索", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="back_to_admin")]]))
        admin_operations[chat_id] = "search"

    elif action == "view_user_upload":
        await query.edit_message_text("👤 发用户ID查询", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="back_to_admin")]]))
        admin_operations[chat_id] = "view_user_upload"

    elif action == "delete_code":
        await query.edit_message_text("🗑️ 发送提取码（可多个，空格分隔）", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="back_to_admin")]]))
        admin_operations[chat_id] = "delete_code"

    elif action == "ban_user":
        await query.edit_message_text("🚫 格式：封禁 123 / 解封 123", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="back_to_admin")]]))
        admin_operations[chat_id] = "ban_user"

# ====================== 管理员输入处理（支持批量删除） ======================
async def handle_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    text = update.message.text.strip()
    if user_id != ADMIN_USER_ID or chat_id not in admin_operations:
        return
    action = admin_operations.pop(chat_id)

    if action == "delete_code":
        codes = text.split()
        deleted = []
        not_found = []
        for c in codes:
            if len(c) == 6 and c.isdigit() and c in bot_db:
                del bot_db[c]
                deleted.append(c)
            else:
                not_found.append(c)
        save_json("bot_db.json", bot_db)
        msg = ""
        if deleted:
            msg += f"✅ 已删除：{' '.join(deleted)}\n"
        if not_found:
            msg += f"❌ 不存在：{' '.join(not_found)}"
        await update.message.reply_text(msg)
        return

    # 其他功能不变
    if action == "search":
        res = []
        for code, pkg in bot_db.items():
            if text.lower() in pkg["name"].lower():
                res.append(f"{code}｜{pkg['name']}")
        await update.message.reply_text("\n".join(res) if res else "❌ 无结果")

    elif action == "view_user_upload":
        uid = text.strip()
        up = []
        for code, pkg in bot_db.items():
            if str(pkg["uploader"]["id"]) == uid:
                up.append(f"{code}｜{pkg['name']}")
        await update.message.reply_text("\n".join(up) if up else "❌ 无记录")

    elif action == "ban_user":
        parts = text.split()
        if len(parts) != 2:
            await update.message.reply_text("❌ 格式错误")
            return
        cmd, tid = parts
        try:
            tid = int(tid)
            if cmd == "封禁":
                if tid not in banned_users:
                    banned_users.append(tid)
                    save_json("banned.json", banned_users)
                await update.message.reply_text(f"✅ 封禁 {tid}")
            elif cmd == "解封":
                if tid in banned_users:
                    banned_users.remove(tid)
                    save_json("banned.json", banned_users)
                await update.message.reply_text(f"✅ 解封 {tid}")
        except:
            await update.message.reply_text("❌ ID错误")

# ====================== 用户查看自己的取件码 /my ======================
async def my_codes(update: Update, _):
    user_id = update.effective_user.id
    if is_banned(user_id):
        await update.message.reply_text("❌ 已封禁")
        return
    my_list = []
    for code, pkg in bot_db.items():
        if str(pkg["uploader"]["id"]) == str(user_id):
            my_list.append(f"🔑 {code}｜{pkg['name']}")
    if not my_list:
        await update.message.reply_text("📭 暂无提取码")
        return
    await update.message.reply_text("\n".join(my_list))

# ====================== 用户删除自己的取件码 /del ======================
async def del_my_code(update: Update, _):
    user_id = update.effective_user.id
    args = update.message.text.split()
    if len(args) < 2:
        await update.message.reply_text("📌 使用：/del 提取码")
        return
    code = args[1]
    if code not in bot_db:
        await update.message.reply_text("❌ 提取码不存在")
        return
    if str(bot_db[code]["uploader"]["id"]) != str(user_id):
        await update.message.reply_text("❌ 无权删除他人提取码")
        return
    del bot_db[code]
    save_json("bot_db.json", bot_db)
    await update.message.reply_text(f"✅ {code} 已删除并失效")

# ====================== 用户基础功能 ======================
async def start(update: Update, _):
    await update.message.reply_text("👋 文件存储机器人\n📌 /my 查看我的提取码\n🗑️ /del 提取码 删除")

async def upload_file(update: Update, _):
    user = update.effective_user
    if is_banned(user.id):
        await update.message.reply_text("❌ 已封禁")
        return
    msg = update.message
    file_data = None
    if msg.photo:
        file_data = {"type":"img","id":msg.photo[-1].file_id,"name":f"图片_{datetime.now().strftime('%H%M%S')}.jpg"}
    elif msg.video:
        file_data = {"type":"video","id":msg.video.file_id,"name":msg.video.file_name or f"视频_{datetime.now().strftime('%H%M%S')}.mp4"}
    elif msg.document:
        file_data = {"type":"doc","id":msg.document.file_id,"name":msg.document.file_name or "文件"}
    else:
        await msg.reply_text("❌ 仅支持图片/视频/文档")
        return
    if user.id not in user_sessions:
        user_sessions[user.id] = []
    user_sessions[user.id].append(file_data)
    await msg.reply_text(f"✅ 已收 {len(user_sessions[user.id])} 个\n发送 /confirm 打包")
    await track_user(user.id, user.full_name, user.username)

async def confirm_package(update: Update, _):
    user = update.effective_user
    if user.id not in user_sessions or not user_sessions[user.id]:
        await update.message.reply_text("❌ 无文件")
        return
    while True:
        code = str(random.randint(100000,999999))
        if code not in bot_db:
            break
    pending_naming[user.id] = {
        "code":code, "files":user_sessions[user.id],
        "uploader":{"id":user.id,"name":user.full_name}
    }
    del user_sessions[user.id]
    await update.message.reply_text("📦 输入包名 /skip 跳过")

async def skip_naming(update: Update, _):
    user = update.effective_user
    if user.id not in pending_naming:
        await update.message.reply_text("❌ 无待命名")
        return
    pkg = pending_naming[user.id]
    pkg["name"] = f"文件包_{pkg['code']}"
    bot_db[pkg["code"]] = pkg
    save_json("bot_db.json", bot_db)
    auto_backup()
    del pending_naming[user.id]
    await update.message.reply_text(f"✅ 提取码：{pkg['code']}")

async def user_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    text = update.message.text.strip()
    if user.id == ADMIN_USER_ID and chat_id in admin_operations:
        await handle_admin_input(update, context)
        return
    if is_banned(user.id):
        await update.message.reply_text("❌ 已封禁")
        return
    if user.id in pending_naming:
        pkg = pending_naming[user.id]
        pkg["name"] = text[:50]
        bot_db[pkg["code"]] = pkg
        save_json("bot_db.json", bot_db)
        auto_backup()
        del pending_naming[user.id]
        await update.message.reply_text(f"✅ 提取码：{pkg['code']}")
        return
    if len(text) == 6 and text.isdigit():
        if text not in bot_db:
            await update.message.reply_text("❌ 不存在")
            return
        pkg = bot_db[text]
        await update.message.reply_text(f"📦 {pkg['name']}")
        for f in pkg["files"]:
            try:
                if f["type"] == "img": await update.message.reply_photo(f["id"])
                elif f["type"] == "video": await update.message.reply_video(f["id"])
                elif f["type"] == "doc": await update.message.reply_document(f["id"])
            except:
                await update.message.reply_text(f"⚠️ 发送失败：{f['name']}")

# ====================== 管理员备份 ======================
async def manual_backup(update: Update, _):
    if update.effective_user.id != ADMIN_USER_ID: return
    auto_backup()
    await update.message.reply_text("✅ 备份完成")

async def send_db(update: Update, _):
    if update.effective_user.id != ADMIN_USER_ID: return
    for f in ["bot_db.json","user_index.json","banned.json"]:
        p = os.path.join(DATA_DIR, f)
        if os.path.exists(p):
            await update.message.reply_document(open(p,"rb"))

# ====================== 启动 ======================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("my", my_codes))
    app.add_handler(CommandHandler("del", del_my_code))
    app.add_handler(CommandHandler("confirm", confirm_package))
    app.add_handler(CommandHandler("skip", skip_naming))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("backup", manual_backup))
    app.add_handler(CommandHandler("getdb", send_db))
    app.add_handler(CallbackQueryHandler(admin_callback_handler))
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, upload_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, user_text_handler))
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
