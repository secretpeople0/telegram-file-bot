import os
import random
import json
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

STORAGE_FILE = "database.json"
BAN_FILE = "banned_users.json"

user_sessions = {}
pending_naming = {}  # 正在命名的用户
db = {}
banned_users = []

# ====================== 数据库 ======================
def init_db():
    if not os.path.exists(STORAGE_FILE):
        with open(STORAGE_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f)
    with open(STORAGE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def init_banned():
    if not os.path.exists(BAN_FILE):
        with open(BAN_FILE, "w", encoding="utf-8") as f:
            json.dump([], f)
    with open(BAN_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

db = init_db()
banned_users = init_banned()

def save_db():
    with open(STORAGE_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

def save_banned():
    with open(BAN_FILE, "w", encoding="utf-8") as f:
        json.dump(banned_users, f, ensure_ascii=False, indent=2)

# ====================== 封禁检查 ======================
def check_ban(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id in banned_users:
            await update.message.reply_text("❌ 你已被封禁，无法使用本机器人。")
            return
        return await func(update, context)
    return wrapper

# ====================== 管理员 ======================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_USER_ID:
        await update.message.reply_text("❌ 你没有管理员权限")
        return

    keyboard = [
        [InlineKeyboardButton("📊 统计总数", callback_data="admin_stats")],
        [InlineKeyboardButton("🔍 搜索文件", callback_data="admin_search")],
        [InlineKeyboardButton("🗑️ 删除文件", callback_data="admin_delete")],
        [InlineKeyboardButton("🚫 封禁/解封用户", callback_data="admin_ban")],
        [InlineKeyboardButton("👤 查看上传者详情", callback_data="admin_view_uploader")],
    ]
    await update.message.reply_text("👮 管理员控制面板", reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if user_id != ADMIN_USER_ID:
        await query.edit_message_text("❌ 权限不足")
        return

    if query.data == "admin_stats":
        total_packages = len(db)
        total_files = sum(len(pkg["files"]) for pkg in db.values())
        await query.edit_message_text(f"📊 存储统计\n打包总数：{total_packages} 个\n文件总数：{total_files} 个")

    elif query.data == "admin_search":
        await query.edit_message_text("🔍 请回复你要搜索的文件名关键词：")
        context.user_data["admin_action"] = "search"

    elif query.data == "admin_delete":
        await query.edit_message_text("🗑️ 请回复要删除的提取码：")
        context.user_data["admin_action"] = "delete"

    elif query.data == "admin_ban":
        await query.edit_message_text("🚫 请回复用户ID（封禁）或 /unban [用户ID]（解封）：")
        context.user_data["admin_action"] = "ban"

    elif query.data == "admin_view_uploader":
        await query.edit_message_text("👤 请回复要查看的用户ID：")
        context.user_data["admin_action"] = "view_uploader"

async def handle_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_USER_ID or "admin_action" not in context.user_data:
        return

    action = context.user_data["admin_action"]
    text = update.message.text.strip()

    if action == "search":
        results = []
        for code, pkg in db.items():
            for f in pkg["files"]:
                if text.lower() in f["name"].lower():
                    results.append(f"🔑 提取码：`{code}`\n📦 包名：`{pkg.get('name', '未命名')}`\n📄 文件：`{f['name']}`\n👤 {pkg['uploader']['name']}\n⏰ {pkg['time']}\n")
        await update.message.reply_text(f"✅ 找到 {len(results)} 条结果\n\n" + "\n".join(results) if results else "❌ 无结果", parse_mode="Markdown")

    elif action == "delete":
        if text in db:
            del db[text]
            save_db()
            await update.message.reply_text(f"✅ 删除 {text} 成功")
        else:
            await update.message.reply_text("❌ 提取码不存在")

    elif action == "ban":
        try:
            if text.startswith("/unban"):
                uid = int(text.split()[1])
                if uid in banned_users:
                    banned_users.remove(uid)
                    save_banned()
                    await update.message.reply_text(f"✅ 已解封 {uid}")
                else:
                    await update.message.reply_text("❌ 未封禁")
            else:
                uid = int(text)
                if uid not in banned_users:
                    banned_users.append(uid)
                    save_banned()
                    await update.message.reply_text(f"✅ 已封禁 {uid}")
        except:
            await update.message.reply_text("❌ 格式错误")

    elif action == "view_uploader":
        try:
            uid = int(text)
            res = []
            for code, pkg in db.items():
                if pkg["uploader"]["id"] == uid:
                    res.append(f"🔑 `{code}`\n📦 {pkg.get('name','未命名')} | {len(pkg['files'])}个文件")
            await update.message.reply_text("\n\n".join(res) if res else "❌ 无上传记录", parse_mode="Markdown")
        except:
            await update.message.reply_text("❌ 输入正确用户ID")

    del context.user_data["admin_action"]

# ====================== 用户功能 ======================
@check_ban
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 发送文件 → /confirm 打包 → 命名 → 获取提取码")

@check_ban
async def collect_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    msg = update.message

    if uid not in user_sessions:
        user_sessions[uid] = []

    f = None
    if msg.document:
        f = {"type":"doc","id":msg.document.file_id,"name":msg.document.file_name or "文件"}
    elif msg.photo:
        f = {"type":"img","id":msg.photo[-1].file_id,"name":f"图{datetime.now().strftime('%H%M%S')}"}

    if not f:
        await msg.reply_text("❌ 仅支持图片/文件")
        return

    user_sessions[uid].append(f)
    await msg.reply_text(f"✅ 已收：{len(user_sessions[uid])} 个\n输入 /confirm 打包")

@check_ban
async def confirm_package(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in user_sessions or not user_sessions[uid]:
        await update.message.reply_text("❌ 你还没上传文件")
        return

    while True:
        code = str(random.randint(100000,999999))
        if code not in db: break

    pending_naming[uid] = {
        "code": code,
        "files": user_sessions[uid],
        "uploader": {"id":uid, "name":update.effective_user.full_name},
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    del user_sessions[uid]
    await update.message.reply_text(f"📦 打包成功！\n🔑 提取码：`{code}`\n请输入包名，或 /skip 跳过", parse_mode="Markdown")

@check_ban
async def handle_text_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()

    # ———— 【修复】：如果正在命名，优先处理 ————
    if uid in pending_naming:
        pkg = pending_naming[uid]
        if text == "/skip":
            pkg["name"] = "未命名"
        else:
            pkg["name"] = text[:50]

        db[pkg["code"]] = pkg
        save_db()
        del pending_naming[uid]
        await update.message.reply_text(f"✅ 完成！\n📦 包名：`{pkg['name']}`\n🔑 提取码：`{pkg['code']}`", parse_mode="Markdown")
        return

    # ———— 管理员操作 ————
    if uid == ADMIN_USER_ID and "admin_action" in context.user_data:
        await handle_admin_text(update, context)
        return

    # ———— 提取码 ————
    if text in db:
        pkg = db[text]
        await update.message.reply_text(f"📦 正在取回：{pkg.get('name','未命名')}")
        for f in pkg["files"]:
            try:
                if f["type"] == "img":
                    await update.message.reply_photo(f["id"])
                else:
                    await update.message.reply_document(f["id"], filename=f["name"])
            except:
                await update.message.reply_text(f"⚠️ 文件发送失败")
        return

# ====================== 启动 ======================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("confirm", confirm_package))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(MessageHandler(filters.ATTACHMENT, collect_files))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_all))
    app.add_handler(CallbackQueryHandler(admin_callback))

    app.run_polling()

if __name__ == "__main__":
    main()
