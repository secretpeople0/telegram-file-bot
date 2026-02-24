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

# ====================== 配置区 ======================
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ADMIN_USER_ID = int(os.environ.get("ADMIN_USER_ID", "0"))
BACKUP_INTERVAL = 30 * 60  # 30分钟自动备份一次
MAX_BACKUP_COUNT = 2       # 只保留最近2个备份（你要的）

# 临时会话存储
user_sessions = {}
pending_naming = {}

# ====================== 自动备份目录 ======================
if not os.path.exists("backup"):
    os.makedirs("backup")

# ====================== 文件存储（永久不丢） ======================
def get_db():
    try:
        with open("bot_db.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_db(db_data):
    with open("bot_db.json", "w", encoding="utf-8") as f:
        json.dump(db_data, f, ensure_ascii=False, indent=2)
    auto_backup()

def get_banned():
    try:
        with open("banned.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []

def save_banned(banned_list):
    with open("banned.json", "w", encoding="utf-8") as f:
        json.dump(banned_list, f, ensure_ascii=False)

def get_user_index():
    try:
        with open("user_index.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_user_index(index_data):
    with open("user_index.json", "w", encoding="utf-8") as f:
        json.dump(index_data, f, ensure_ascii=False, indent=2)

# ====================== 备份 + 自动清理旧备份 ======================
def backup_now():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    files = ["bot_db.json", "banned.json", "user_index.json"]
    for f in files:
        if os.path.exists(f):
            shutil.copy(f, f"backup/{f}.{timestamp}")
    auto_clean_old_backups()
    return timestamp

def auto_clean_old_backups():
    backup_dir = "backup"
    backup_files = []
    for f in os.listdir(backup_dir):
        path = os.path.join(backup_dir, f)
        if os.path.isfile(path):
            backup_files.append((os.path.getmtime(path), path))
    backup_files.sort(reverse=True)
    if len(backup_files) > MAX_BACKUP_COUNT:
        for tm, path in backup_files[MAX_BACKUP_COUNT:]:
            try:
                os.remove(path)
            except:
                pass

last_backup_time = 0

def auto_backup():
    global last_backup_time
    now = datetime.now().timestamp()
    if now - last_backup_time > BACKUP_INTERVAL:
        backup_now()
        last_backup_time = now

# ====================== 备份命令 ======================
async def backup_command(update: Update, context: ContextTypes):
    user_id = update.effective_user.id
    if user_id != ADMIN_USER_ID:
        await update.message.reply_text("❌ 无权限")
        return
    ts = backup_now()
    await update.message.reply_text(f"✅ 备份完成：{ts}\n🗑️ 已自动清理旧备份，保留最近{MAX_BACKUP_COUNT}个")

# ====================== 手机获取数据库文件 ======================
async def get_db_file(update: Update, context: ContextTypes):
    user_id = update.effective_user.id
    if user_id != ADMIN_USER_ID:
        await update.message.reply_text("❌ 无权限")
        return
    if os.path.exists("bot_db.json"):
        await update.message.reply_document(document=open("bot_db.json", "rb"))
    if os.path.exists("banned.json"):
        await update.message.reply_document(document=open("banned.json", "rb"))
    if os.path.exists("user_index.json"):
        await update.message.reply_document(document=open("user_index.json", "rb"))

banned_users = get_banned()

# ====================== 用户记录 ======================
async def track_user(update: Update):
    user = update.effective_user
    if not user:
        return
    user_id = str(user.id)
    index = get_user_index()
    if user_id not in index or index[user_id]["name"] != user.full_name:
        index[user_id] = {
            "name": user.full_name,
            "username": user.username or "无"
        }
        save_user_index(index)

# ====================== 封禁检查 ======================
def check_ban(func):
    async def wrapper(update: Update, context: ContextTypes):
        await track_user(update)
        user_id = update.effective_user.id
        if user_id in banned_users:
            await update.message.reply_text("❌ 你已被封禁，无法使用本机器人。")
            return
        return await func(update, context)
    return wrapper

# ====================== 管理员 ======================
async def admin_panel(update: Update, context: ContextTypes):
    user_id = update.effective_user.id
    if user_id != ADMIN_USER_ID:
        await update.message.reply_text("❌ 你没有管理员权限")
        return
    keyboard = [
        [InlineKeyboardButton("📊 统计总数", callback_data="stats")],
        [InlineKeyboardButton("👥 查看用户列表", callback_data="list_users")],
        [InlineKeyboardButton("🔍 搜索文件", callback_data="search")],
        [InlineKeyboardButton("👁️ 查看用户上传", callback_data="view_user_upload")],
        [InlineKeyboardButton("🗑️ 删除提取码", callback_data="delete")],
        [InlineKeyboardButton("🚫 封禁/解封", callback_data="ban")],
    ]
    await update.message.reply_text("👮 管理员控制面板", reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_callback(update: Update, context: ContextTypes):
    query = update.callback_query
    await query.answer()
    user_id = query.from.id
    if user_id != ADMIN_USER_ID:
        await query.edit_message_text("❌ 权限不足")
        return
    db = get_db()
    user_index = get_user_index()
    if query.data == "stats":
        total_packages = len(db)
        total_files = sum(len(pkg["files"]) for pkg in db.values())
        total_users = len(user_index)
        await query.edit_message_text(
            f"📊 机器人运行统计\n"
            f"总用户数：{total_users} 人\n"
            f"文件包总数：{total_packages} 个\n"
            f"文件总数：{total_files} 个"
        )
    elif query.data == "list_users":
        if not user_index:
            await query.edit_message_text("❌ 暂无用户记录")
            return
        msg = "👥 所有使用过的用户列表 (ID | 昵称)\n\n"
        for uid, info in list(user_index.items())[:30]:
            msg += f"ID: `{uid}` | 昵称: {info['name']}\n"
        if len(user_index) > 30:
            msg += f"\n... 还有 {len(user_index) - 30} 个未显示"
        await query.edit_message_text(msg, parse_mode="Markdown")
    elif query.data == "search":
        await query.edit_message_text("🔍 请发送搜索关键词：")
        context.user_data["admin_act"] = "search"
    elif query.data == "view_user_upload":
        await query.edit_message_text("👤 请发送用户ID：")
        context.user_data["admin_act"] = "view_user_upload"
    elif query.data == "delete":
        await query.edit_message_text("🗑️ 请发送6位提取码：")
        context.user_data["admin_act"] = "delete"
    elif query.data == "ban":
        await query.edit_message_text("🚫 请发送用户ID：")
        context.user_data["admin_act"] = "ban"

async def handle_admin_action(update: Update, context: ContextTypes):
    if "admin_act" not in context.user_data:
        return
    user_id = update.effective_user.id
    if user_id != ADMIN_USER_ID:
        return
    action = context.user_data["admin_act"]
    text = update.message.text.strip()
    db = get_db()
    if action == "search":
        keyword = text.lower()
        results = []
        for code, pkg in db.items():
            match = False
            if keyword in pkg.get("name", "").lower():
                match = True
            else:
                for file in pkg["files"]:
                    if keyword in file["name"].lower():
                        match = True
                        break
            if match:
                results.append(f"🔑 `{code}`｜{pkg.get('name','未命名')}")
        if results:
            await update.message.reply_text("\n".join(results), parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ 无结果")
    elif action == "view_user_upload":
        target = text.strip()
        res = [f"`{c}`｜{p.get('name')}" for c,p in db.items() if str(p["uploader"]["id"])==target]
        await update.message.reply_text("\n".join(res) if res else "❌ 无记录", parse_mode="Markdown")
    elif action == "delete":
        if text in db:
            del db[text]
            save_db(db)
            await update.message.reply_text(f"✅ 删除 {text} 成功")
        else:
            await update.message.reply_text("❌ 不存在")
    elif action == "ban":
        try:
            if text.startswith("/unban"):
                tid = int(text.split()[1])
                if tid in banned_users:
                    banned_users.remove(tid)
                    save_banned(banned_users)
                    await update.message.reply_text(f"✅ 已解封 {tid}")
                else:
                    await update.message.reply_text("❌ 未封禁")
            else:
                tid = int(text)
                if tid not in banned_users:
                    banned_users.append(tid)
                    save_banned(banned_users)
                    await update.message.reply_text(f"✅ 已封禁 {tid}")
                else:
                    await update.message.reply_text("❌ 已封禁")
        except:
            await update.message.reply_text("❌ 格式错误")
    del context.user_data["admin_act"]

# ====================== 用户功能 ======================
@check_ban
async def start(update: Update, context: ContextTypes):
    await update.message.reply_text(
        "👋 永久文件存储机器人\n\n"
        "📝 使用方法：\n"
        "1. 发送图片/视频/文档\n"
        "2. 发送 /confirm\n"
        "3. 输入名称 或 /skip\n"
        "4. 用6位提取码取回文件"
    )

@check_ban
async def upload_file(update: Update, context: ContextTypes):
    uid = update.effective_user.id
    msg = update.message
    if uid not in user_sessions:
        user_sessions[uid] = []
    fi = None
    if msg.document:
        fi = {"type":"doc","id":msg.document.file_id,"name":msg.document.file_name or "文件"}
    elif msg.photo:
        fi = {"type":"img","id":msg.photo[-1].file_id,"name":f"图片_{datetime.now().strftime('%H%M%S')}.jpg"}
    elif msg.video:
        fi = {"type":"video","id":msg.video.file_id,"name":msg.video.file_name or f"视频_{datetime.now().strftime('%H%M%S')}.mp4"}
    else:
        await msg.reply_text("❌ 仅支持图片、视频、文档")
        return
    user_sessions[uid].append(fi)
    await msg.reply_text(f"✅ 已接收 {len(user_sessions[uid])} 个文件，发送 /confirm 打包")

@check_ban
async def confirm_package(update: Update, context: ContextTypes):
    uid = update.effective_user.id
    if uid not in user_sessions or not user_sessions[uid]:
        await update.message.reply_text("❌ 暂无文件")
        return
    db = get_db()
    while True:
        code = str(random.randint(100000,999999))
        if code not in db: break
    pending_naming[uid] = {
        "code":code,
        "files":user_sessions[uid],
        "uploader":{"id":uid,"name":update.effective_user.full_name}
    }
    await update.message.reply_text("📝 输入文件包名称，或发送 /skip")

@check_ban
async def skip_name(update: Update, context: ContextTypes):
    uid = update.effective_user.id
    if uid not in pending_naming:
        await update.message.reply_text("❌ 无效")
        return
    pkg = pending_naming[uid]
    pkg["name"] = f"文件包_{pkg['code']}"
    db = get_db()
    db[pkg["code"]] = pkg
    save_db(db)
    del user_sessions[uid]
    del pending_naming[uid]
    await update.message.reply_text(f"✅ 完成！提取码：`{pkg['code']}`", parse_mode="Markdown")

@check_ban
async def set_package_name(update: Update, context: ContextTypes):
    uid = update.effective_user.id
    if uid not in pending_naming:
        return
    name = update.message.text.strip()
    if len(name) > 50:
        await update.message.reply_text("❌ 名称太长（最多50字）")
        return
    pkg = pending_naming[uid]
    pkg["name"] = name
    db = get_db()
    db[pkg["code"]] = pkg
    save_db(db)
    del user_sessions[uid]
    del pending_naming[uid]
    await update.message.reply_text(f"✅ 完成！提取码：`{pkg['code']}`", parse_mode="Markdown")

@check_ban
async def fetch_file(update: Update, context: ContextTypes):
    txt = update.message.text.strip()
    db = get_db()
    if len(txt) == 6 and txt.isdigit() and txt in db:
        pkg = db[txt]
        for f in pkg["files"]:
            try:
                if f["type"] == "img":
                    await update.message.reply_photo(f["id"], caption=f["name"])
                elif f["type"] == "video":
                    await update.message.reply_video(f["id"], caption=f["name"])
                elif f["type"] == "doc":
                    await update.message.reply_document(f["id"], filename=f["name"])
            except:
                await update.message.reply_text("⚠️ 文件可能已过期")

# ====================== 启动 ======================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("backup", backup_command))
    app.add_handler(CommandHandler("getdb", get_db_file))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CallbackQueryHandler(admin_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_action))

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("confirm", confirm_package))
    app.add_handler(CommandHandler("skip", skip_name))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL | filters.VIDEO, upload_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, set_package_name))
    app.add_handler(MessageHandler(filters.TEXT, fetch_file))

    app.run_polling()

if __name__ == "__main__":
    main()
