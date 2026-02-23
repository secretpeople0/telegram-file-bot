import os
import logging
import random
import string
import sqlite3
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ConversationHandler
)
import oss2

# --- 日志配置 ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- 环境变量配置 ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OSS_ACCESS_KEY = os.getenv("OSS_ACCESS_KEY")
OSS_SECRET_KEY = os.getenv("OSS_SECRET_KEY")
OSS_ENDPOINT = os.getenv("OSS_ENDPOINT")
OSS_BUCKET = os.getenv("OSS_BUCKET")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))
DATABASE_FILE = "/app/bot.db"

# --- 全局常量 ---
WAITING_FOR_NAME = 1
BANNED_USERS = set()

# --- 初始化 OSS ---
auth = oss2.Auth(OSS_ACCESS_KEY, OSS_SECRET_KEY)
bucket = oss2.Bucket(auth, OSS_ENDPOINT, OSS_BUCKET)

# --- 数据库工具函数 ---
def get_db_connection():
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        # 创建表（兼容 SQLite 时间戳语法）
        cur.execute('''CREATE TABLE IF NOT EXISTS user_files
                       (id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        code TEXT UNIQUE NOT NULL,
                        name TEXT,
                        file_paths TEXT NOT NULL,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS banned_users
                       (user_id INTEGER PRIMARY KEY,
                        banned_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
        conn.commit()
        cur.close()
        conn.close()
        logger.info("✅ SQLite 数据库初始化成功")
    except Exception as e:
        logger.error(f"❌ 数据库初始化失败: {e}")

def is_banned(user_id):
    if user_id in BANNED_USERS:
        return True
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM banned_users WHERE user_id = ?", (user_id,))
        res = cur.fetchone() is not None
        cur.close()
        conn.close()
        if res:
            BANNED_USERS.add(user_id)
        return res
    except Exception as e:
        logger.error(f"❌ 封禁查询失败: {e}")
        return False

def generate_unique_code(length=8):
    chars = string.ascii_uppercase + string.digits
    for _ in range(20):
        code = ''.join(random.choice(chars) for _ in range(length))
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM user_files WHERE code = ?", (code,))
            if cur.fetchone() is None:
                cur.close()
                conn.close()
                return code
            cur.close()
            conn.close()
        except Exception as e:
            logger.error(f"❌ 生成提取码失败: {e}")
            continue
    return ''.join(random.choice(chars) for _ in range(8))

# --- 核心功能函数 ---
async def upload_files_to_oss(file_paths, user_id, code):
    oss_paths = []
    for idx, fp in enumerate(file_paths):
        try:
            oss_path = f"files/{user_id}/{code}/{idx}_{os.path.basename(fp)}"
            with open(fp, 'rb') as f:
                bucket.put_object(oss_path, f)
            oss_paths.append(oss_path)
            os.remove(fp)  # 删除本地临时文件
        except Exception as e:
            logger.error(f"❌ OSS 上传失败: {e}")
            return None
    return ','.join(oss_paths)

# --- 命令处理器 ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_banned(user.id):
        await update.message.reply_text("❌ 你已被封禁，无法使用本机器人")
        return
    await update.message.reply_text(
        "👋 欢迎使用文件存储机器人！\n"
        "📤 直接发送图片/视频/文档即可上传\n"
        "📝 上传后可给提取码命名\n"
        "🔧 发送 /myfiles 查看/管理你的文件"
    )

async def myfiles(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_banned(user.id):
        await update.message.reply_text("❌ 你已被封禁")
        return
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT code, name, created_at FROM user_files WHERE user_id = ? ORDER BY created_at DESC",
            (user.id,)
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
    except Exception as e:
        await update.message.reply_text(f"⚠️ 数据库异常: {str(e)}")
        return

    if not rows:
        await update.message.reply_text("📂 你的文件库为空")
        return

    text = "📋 你的文件列表：\n"
    keyboard = []
    for r in rows:
        text += f"• 提取码: `{r['code']}` | 名称: {r['name'] or '未命名'} | 时间: {r['created_at']}\n"
        keyboard.append([InlineKeyboardButton(f"删除 {r['code']}", callback_data=f"del_{r['code']}")])

    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

# --- 消息与回调处理器 ---
async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_banned(user.id):
        return

    # 初始化文件队列
    if 'file_queue' not in context.user_data:
        context.user_data['file_queue'] = []

    try:
        # 处理不同类型文件
        file_to_download = None
        if update.message.document:
            file_to_download = await update.message.document.get_file()
        elif update.message.photo:
            file_to_download = await update.message.photo[-1].get_file()  # 取最高清图
        elif update.message.video:
            file_to_download = await update.message.video.get_file()
        else:
            await update.message.reply_text("❌ 不支持的文件类型")
            return

        # 下载文件到本地
        file_path = await file_to_download.download_to_drive()
        context.user_data['file_queue'].append(file_path)
        await update.message.reply_text(f"✅ 已接收 {len(context.user_data['file_queue'])} 个文件，即将生成提取码...")

        # 延迟生成提取码（支持批量上传）
        context.job_queue.run_once(
            generate_code_job,
            5,
            user_id=user.id,
            data=context.user_data
        )

    except Exception as e:
        logger.error(f"❌ 文件处理失败: {e}")
        await update.message.reply_text("❌ 文件处理失败，请重试")

async def generate_code_job(context: ContextTypes.DEFAULT_TYPE):
    user_data = context.job.data
    user_id = context.job.user_id

    if not user_data.get('file_queue'):
        return

    # 生成唯一提取码
    code = generate_unique_code()
    # 上传到 OSS
    oss_paths = await upload_files_to_oss(user_data['file_queue'], user_id, code)

    if not oss_paths:
        await context.bot.send_message(user_id, "❌ 文件上传至阿里云 OSS 失败")
        user_data['file_queue'].clear()
        return

    # 写入数据库
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO user_files (user_id, code, name, file_paths) VALUES (?, ?, ?, ?)",
            (user_id, code, None, oss_paths)
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"❌ 写入数据库失败: {e}")
        await context.bot.send_message(user_id, "⚠️ 文件上传成功，但记录保存失败")

    # 发送提取码并询问命名
    keyboard = [
        [InlineKeyboardButton("✅ 命名", callback_data=f"name_{code}")],
        [InlineKeyboardButton("❌ 跳过", callback_data=f"skip_{code}")]
    ]
    await context.bot.send_message(
        user_id,
        f"🎉 文件上传成功！\n你的提取码: `{code}`",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

    # 清空队列
    user_data['file_queue'].clear()

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()  # 必须先调用，避免 Telegram 报错
    user_id = query.from_user.id
    data = query.data

    # 处理删除
    if data.startswith('del_'):
        code = data[4:]
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM user_files WHERE code = ? AND user_id = ?",
                (code, user_id)
            )
            conn.commit()
            cur.close()
            conn.close()
            await query.edit_message_text(f"🗑️ 提取码 `{code}` 已成功删除", parse_mode='Markdown')
        except Exception as e:
            await query.edit_message_text(f"❌ 删除失败: {str(e)}", parse_mode='Markdown')
        return

    # 处理命名
    if data.startswith('name_'):
        code = data[5:]
        context.user_data['pending_rename_code'] = code
        await query.edit_message_text("✏️ 请回复本条消息，发送你想要设置的名称")
        return WAITING_FOR_NAME

    # 处理跳过
    if data.startswith('skip_'):
        code = data[5:]
        await query.edit_message_text(f"✅ 已跳过命名，提取码: `{code}`", parse_mode='Markdown')
        return

async def handle_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip()

    # 处理重命名
    if 'pending_rename_code' in context.user_data:
        code = context.user_data.pop('pending_rename_code')
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute(
                "UPDATE user_files SET name = ? WHERE code = ? AND user_id = ?",
                (text, code, user.id)
            )
            conn.commit()
            cur.close()
            conn.close()
            await update.message.reply_text(f"✅ 提取码 `{code}` 已命名为：{text}", parse_mode='Markdown')
        except Exception as e:
            await update.message.reply_text(f"❌ 命名失败: {str(e)}", parse_mode='Markdown')
        return ConversationHandler.END

    # 默认回复
    await update.message.reply_text("📤 直接发送文件即可上传，发送 /myfiles 查看你的文件")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ 操作已取消")
    return ConversationHandler.END

# --- 主程序 ---
def main():
    # 初始化数据库
    init_db()

    # 构建应用（适配 20.7 版本 API）
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # 对话处理器（处理命名）
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler)],
        states={
            WAITING_FOR_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_conversation)]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    # 注册处理器
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("myfiles", myfiles))
    application.add_handler(conv_handler)
    # 处理文件（排除文本和命令）
    application.add_handler(MessageHandler(
        filters.ALL & ~filters.COMMAND & ~filters.TEXT,
        handle_file
    ))

    # 启动机器人
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
