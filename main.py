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

# 持久化存储文件
STORAGE_FILE = "database.json"
BAN_FILE = "banned_users.json"

# 临时会话存储（用户上传中但未确认的文件）
user_sessions = {}
# 等待命名的会话（用户刚打包完，等待输入名称）
pending_naming = {}

# ====================== 数据模型 ======================
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

# ====================== 封禁检查装饰器 ======================
def check_ban(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id in banned_users:
            await update.message.reply_text("❌ 你已被封禁，无法使用本机器人。")
            return
        return await func(update, context)
    return wrapper

# ====================== 管理员功能 ======================
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
    await update.message.reply_text(
        "👮 管理员控制面板",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

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
        await query.edit_message_text(
            f"📊 存储统计\n"
            f"打包总数：{total_packages} 个\n"
            f"文件总数：{total_files} 个"
        )

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
                    results.append(
                        f"🔑 提取码：`{code}`\n"
                        f"📦 包名：`{pkg.get('name', '未命名')}`\n"
                        f"📄 匹配文件：`{f['name']}`\n"
                        f"👤 上传者：{pkg['uploader']['name']} (ID: {pkg['uploader']['id']})\n"
                        f"⏰ 上传时间：{pkg['time']}\n"
                    )
        if results:
            await update.message.reply_text(
                f"✅ 找到 {len(results)} 个匹配结果：\n\n" + "\n".join(results),
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text("❌ 未找到匹配文件")

    elif action == "delete":
        if text in db:
            del db[text]
            save_db()
            await update.message.reply_text(f"✅ 提取码 `{text}` 已永久删除", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ 提取码不存在")

    elif action == "ban":
        if text.startswith("/unban "):
            target_id = int(text.split()[1])
            if target_id in banned_users:
                banned_users.remove(target_id)
                save_banned()
                await update.message.reply_text(f"✅ 已解封用户 {target_id}")
            else:
                await update.message.reply_text("❌ 该用户未被封禁")
        else:
            try:
                target_id = int(text)
                if target_id not in banned_users:
                    banned_users.append(target_id)
                    save_banned()
                    await update.message.reply_text(f"✅ 已封禁用户 {target_id}")
                else:
                    await update.message.reply_text("❌ 该用户已被封禁")
            except:
                await update.message.reply_text("❌ 请输入正确的用户ID")

    elif action == "view_uploader":
        try:
            target_id = int(text)
            user_packages = []
            for code, pkg in db.items():
                if pkg["uploader"]["id"] == target_id:
                    user_packages.append(
                        f"🔑 提取码：`{code}`\n"
                        f"📦 包名：`{pkg.get('name', '未命名')}`\n"
                        f"📁 文件数：{len(pkg['files'])}\n"
                        f"⏰ 上传时间：{pkg['time']}\n"
                    )
            if user_packages:
                await update.message.reply_text(
                    f"👤 用户 {target_id} 的上传记录：\n\n" + "\n".join(user_packages),
                    parse_mode="Markdown"
                )
            else:
                await update.message.reply_text("❌ 该用户没有上传任何文件")
        except:
            await update.message.reply_text("❌ 请输入正确的用户ID")

    del context.user_data["admin_action"]

# ====================== 用户核心功能 ======================
@check_ban
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 欢迎使用【永久文件存储机器人】\n\n"
        "📝 使用方法：\n"
        "1. 连续发送多张图片/多个文件\n"
        "2. 发送命令 /confirm 确认打包\n"
        "3. 可给文件包命名，也可跳过\n"
        "4. 直接发送提取码，立即取回文件"
    )

@check_ban
async def collect_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    message = update.message

    if user_id not in user_sessions:
        user_sessions[user_id] = []

    file_info = None
    if message.document:
        file_info = {
            "type": "doc",
            "id": message.document.file_id,
            "name": message.document.file_name or "未命名文件"
        }
    elif message.photo:
        file_info = {
            "type": "img",
            "id": message.photo[-1].file_id,
            "name": f"图片_{datetime.now().strftime('%H%M%S')}.jpg"
        }
    else:
        await message.reply_text("❌ 仅支持图片和文件")
        return

    user_sessions[user_id].append(file_info)
    await message.reply_text(
        f"✅ 已接收：`{file_info['name']}`\n"
        f"当前已收集 {len(user_sessions[user_id])} 个文件\n"
        f"输入 /confirm 完成打包",
        parse_mode="Markdown"
    )

@check_ban
async def confirm_package(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_sessions or not user_sessions[user_id]:
        await update.message.reply_text("❌ 你还没有上传任何文件")
        return

    # 生成唯一提取码
    while True:
        code = str(random.randint(100000, 999999))
        if code not in db:
            break

    # 先保存到 pending_naming，等待用户命名
    pending_naming[user_id] = {
        "code": code,
        "files": user_sessions[user_id],
        "uploader": {
            "id": user_id,
            "name": update.effective_user.full_name
        },
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    del user_sessions[user_id]

    await update.message.reply_text(
        f"📦 打包完成！\n"
        f"🔑 提取码：`{code}`\n"
        f"请为这个文件包命名（最多50字），或发送 /skip 跳过命名。",
        parse_mode="Markdown"
    )

@check_ban
async def handle_naming(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in pending_naming:
        return

    text = update.message.text.strip()
    pkg_data = pending_naming[user_id]

    if text == "/skip":
        pkg_data["name"] = "未命名"
    else:
        pkg_data["name"] = text[:50]  # 限制长度

    # 正式存入数据库
    db[pkg_data["code"]] = pkg_data
    save_db()
    del pending_naming[user_id]

    await update.message.reply_text(
        f"✅ 保存成功！\n"
        f"📦 包名：`{pkg_data['name']}`\n"
        f"🔑 提取码：`{pkg_data['code']}`\n"
        f"💡 发送此提取码，即可取回所有文件",
        parse_mode="Markdown"
    )

@check_ban
async def retrieve_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip()
    if code not in db:
        return

    pkg = db[code]
    await update.message.reply_text(
        f"📦 正在为你取回文件包「{pkg.get('name', '未命名')}」（共 {len(pkg['files'])} 个）...",
        parse_mode="Markdown"
    )

    for f in pkg["files"]:
        try:
            if f["type"] == "img":
                await update.message.reply_photo(photo=f["id"], caption=f"📄 {f['name']}")
            else:
                await update.message.reply_document(document=f["id"], filename=f["name"])
        except Exception as e:
            await update.message.reply_text(f"⚠️ 发送 `{f['name']}` 失败：{str(e)}")

# ====================== 主程序（修复处理器顺序） ======================
def main():
    if not BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN 环境变量未设置")

    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # 1. 命令处理器
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("confirm", confirm_package))
    application.add_handler(CommandHandler("skip", handle_naming))
    application.add_handler(CommandHandler("admin", admin_panel))

    # 2. 管理员文本操作（优先级最高）
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_text))

    # 3. 文件命名处理（优先级次之）
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_naming))

    # 4. 提取码取回文件（最后处理）
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, retrieve_files))

    # 5. 文件收集
    application.add_handler(MessageHandler(filters.ATTACHMENT, collect_files))

    # 6. 回调处理
    application.add_handler(CallbackQueryHandler(admin_callback))

    application.run_polling()

if __name__ == "__main__":
    main()
