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
# 临时会话存储（用户上传中但未确认的文件）
user_sessions = {}

# ====================== 数据模型 ======================
def init_db():
    """初始化数据库，结构：{提取码: {文件列表, 上传信息}}"""
    if not os.path.exists(STORAGE_FILE):
        with open(STORAGE_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f)
    with open(STORAGE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

db = init_db()

def save_db():
    """保存数据库"""
    with open(STORAGE_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

# ====================== 管理员功能 ======================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_USER_ID:
        await update.message.reply_text("❌ 你没有管理员权限")
        return

    keyboard = [
        [InlineKeyboardButton("📊 统计总数", callback_data="admin_stats")],
        [InlineKeyboardButton("🔍 搜索文件", callback_data="admin_search")],
        [InlineKeyboardButton("🗑️ 删除文件", callback_data="admin_delete")]
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
        # 统计总数
        total_packages = len(db)
        total_files = sum(len(pkg["files"]) for pkg in db.values())
        await query.edit_message_text(
            f"📊 存储统计\n"
            f"打包总数：{total_packages} 个\n"
            f"文件总数：{total_files} 个"
        )

    elif query.data == "admin_search":
        # 等待用户输入搜索关键词
        await query.edit_message_text("🔍 请回复你要搜索的文件名关键词：")
        context.user_data["admin_action"] = "search"

    elif query.data == "admin_delete":
        # 等待用户输入提取码
        await query.edit_message_text("🗑️ 请回复要删除的提取码：")
        context.user_data["admin_action"] = "delete"

async def handle_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理管理员的文本输入（搜索/删除）"""
    user_id = update.effective_user.id
    if user_id != ADMIN_USER_ID or "admin_action" not in context.user_data:
        return

    action = context.user_data["admin_action"]
    text = update.message.text.strip()

    if action == "search":
        # 搜索文件名
        results = []
        for code, pkg in db.items():
            for f in pkg["files"]:
                if text.lower() in f["name"].lower():
                    results.append(
                        f"🔑 提取码：`{code}`\n"
                        f"📄 匹配文件：`{f['name']}`\n"
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
        # 删除提取码包
        if text in db:
            del db[text]
            save_db()
            await update.message.reply_text(f"✅ 提取码 `{text}` 已永久删除", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ 提取码不存在")

    # 清除动作标记
    del context.user_data["admin_action"]

# ====================== 用户核心功能 ======================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """欢迎语 & 使用指南"""
    await update.message.reply_text(
        "👋 欢迎使用【永久文件存储机器人】\n\n"
        "📝 使用方法：\n"
        "1. 连续发送多张图片/多个文件\n"
        "2. 发送命令 /confirm 确认打包\n"
        "3. 获得唯一提取码，凭码可取回所有文件\n"
        "4. 直接发送提取码，立即取回文件"
    )

async def collect_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """收集用户上传的文件，暂存于会话"""
    user_id = update.effective_user.id
    message = update.message

    # 初始化用户会话
    if user_id not in user_sessions:
        user_sessions[user_id] = []

    # 处理文件/图片
    file_info = None
    if message.document:
        file_info = {
            "type": "doc",
            "id": message.document.file_id,
            "name": message.document.file_name or "未命名文件"
        }
    elif message.photo:
        # 取最高清的一张
        file_info = {
            "type": "img",
            "id": message.photo[-1].file_id,
            "name": f"图片_{datetime.now().strftime('%H%M%S')}.jpg"
        }
    else:
        await message.reply_text("❌ 仅支持图片和文件")
        return

    # 加入临时会话
    user_sessions[user_id].append(file_info)
    await message.reply_text(
        f"✅ 已接收：`{file_info['name']}`\n"
        f"当前已收集 {len(user_sessions[user_id])} 个文件\n"
        f"输入 /confirm 完成打包",
        parse_mode="Markdown"
    )

async def confirm_package(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """确认打包，生成提取码"""
    user_id = update.effective_user.id
    if user_id not in user_sessions or not user_sessions[user_id]:
        await update.message.reply_text("❌ 你还没有上传任何文件")
        return

    # 生成唯一提取码
    while True:
        code = str(random.randint(100000, 999999))  # 升级为6位，降低冲突
        if code not in db:
            break

    # 保存到数据库
    db[code] = {
        "files": user_sessions[user_id],
        "uploader": {
            "id": user_id,
            "name": update.effective_user.full_name
        },
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "permanent": True  # 永久标记
    }
    save_db()

    # 清空临时会话
    del user_sessions[user_id]

    # 回复用户
    await update.message.reply_text(
        f"🎉 打包成功！\n"
        f"🔑 你的永久提取码：`{code}`\n"
        f"📦 包含 {len(db[code]['files'])} 个文件\n"
        f"💡 发送此提取码，即可取回所有文件",
        parse_mode="Markdown"
    )

async def retrieve_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """凭提取码取回文件包"""
    code = update.message.text.strip()
    if code not in db:
        return  # 非提取码，不处理

    pkg = db[code]
    # 发送文件包
    await update.message.reply_text(
        f"📦 正在为你取回文件包（共 {len(pkg['files'])} 个）...",
        parse_mode="Markdown"
    )

    # 逐个发送文件
    for f in pkg["files"]:
        try:
            if f["type"] == "img":
                await update.message.reply_photo(photo=f["id"], caption=f"📄 {f['name']}")
            else:
                await update.message.reply_document(
                    document=f["id"],
                    filename=f["name"]
                )
        except Exception as e:
            await update.message.reply_text(f"⚠️ 发送 `{f['name']}` 失败：{str(e)}")

# ====================== 主程序 ======================
def main():
    if not BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN 环境变量未设置")

    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # 注册所有处理器
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("confirm", confirm_package))
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(MessageHandler(filters.ATTACHMENT, collect_files))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, retrieve_files))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_text))
    application.add_handler(CallbackQueryHandler(admin_callback))

    # 启动轮询
    application.run_polling()

if __name__ == "__main__":
    main()
