import os
import time
import logging
import random
import string
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
import psycopg2
import psycopg2.extras
import oss2

# --- 配置日志 ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- 环境变量配置 (从Railway读取) ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OSS_ACCESS_KEY = os.getenv("OSS_ACCESS_KEY")
OSS_SECRET_KEY = os.getenv("OSS_SECRET_KEY")
OSS_ENDPOINT = os.getenv("OSS_ENDPOINT")
OSS_BUCKET = os.getenv("OSS_BUCKET")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID"))
DATABASE_URL = os.getenv("DATABASE_URL")

# --- 全局常量 ---
WAITING_FOR_NAME = 1
BANNED_USERS = set()

# --- 初始化 OSS ---
auth = oss2.Auth(OSS_ACCESS_KEY, OSS_SECRET_KEY)
bucket = oss2.Bucket(auth, OSS_ENDPOINT, OSS_BUCKET)

# --- 数据库工具函数 ---
def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    # 创建用户文件表
    cur.execute('''CREATE TABLE IF NOT EXISTS user_files
                   (id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    code TEXT UNIQUE NOT NULL,
                    name TEXT,
                    file_paths TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    # 创建封禁表
    cur.execute('''CREATE TABLE IF NOT EXISTS banned_users
                   (user_id BIGINT PRIMARY KEY,
                    banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    cur.close()
    conn.close()
    logger.info("✅ 数据库初始化成功")

def is_banned(user_id):
    if user_id in BANNED_USERS:
        return True
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM banned_users WHERE user_id = %s", (user_id,))
    result = cur.fetchone() is not None
    cur.close()
    conn.close()
    if result:
        BANNED_USERS.add(user_id)
    return result

# --- 核心功能函数 ---
def generate_unique_code(length=8):
    chars = string.ascii_uppercase + string.digits
    while True:
        code = ''.join(random.choice(chars) for _ in range(length))
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM user_files WHERE code = %s", (code,))
        if cur.fetchone() is None:
            cur.close()
            conn.close()
            return code
        cur.close()
        conn.close()

async def upload_files_to_oss(file_paths, user_id, code):
    oss_paths = []
    for idx, file_path in enumerate(file_paths):
        oss_path = f"files/{user_id}/{code}/{idx}_{os.path.basename(file_path)}"
        try:
            with open(file_path, 'rb') as f:
                bucket.put_object(oss_path, f)
            oss_paths.append(oss_path)
            os.remove(file_path) # 删除本地临时文件
        except Exception as e:
            logger.error(f"上传失败: {e}")
            return None
    return ','.join(oss_paths)

# --- 命令处理器 ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_banned(user_id):
        await update.message.reply_text("❌ 你已被封禁，无法使用本机器人。")
        return

    await update.message.reply_text(
        "👋 欢迎使用文件存储机器人！\n"
        "📤 直接发送文件/图片/视频即可上传。\n"
        "📝 上传后可选择为代码命名。\n"
        "🔧 发送 /myfiles 查看你的文件。\n"
        "⚙️ 管理员发送 /admin 进入面板。"
    )

async def myfiles(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_banned(user_id):
        await update.message.reply_text("❌ 你已被封禁。")
        return

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur.execute("SELECT code, name, created_at FROM user_files WHERE user_id = %s ORDER BY created_at DESC", (user_id,))
    files = cur.fetchall()
    cur.close()
    conn.close()

    if not files:
        await update.message.reply_text("📂 你的文件库是空的。")
        return

    text = "📋 你的文件列表：\n"
    keyboard = []
    for f in files:
        text += f"• 代码: `{f['code']}` | 名称: {f['name'] or '未命名'} | 时间: {f['created_at'].strftime('%Y-%m-%d')}\n"
        keyboard.append([InlineKeyboardButton(f"删除 {f['code']}", callback_data=f"del_{f['code']}")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')

# --- 管理员命令 ---
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_USER_ID:
        await update.message.reply_text("❌ 你没有管理员权限。")
        return

    keyboard = [
        [InlineKeyboardButton("📊 查看所有文件", callback_data="admin_all")],
        [InlineKeyboardButton("🔨 封禁用户", callback_data="admin_ban")],
        [InlineKeyboardButton("🔎 搜索文件", callback_data="admin_search")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("⚙️ 管理员面板", reply_markup=reply_markup)

# --- 消息与回调处理器 ---
async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_banned(user_id):
        return

    # 初始化用户的文件列表
    if 'file_queue' not in context.user_data:
        context.user_data['file_queue'] = []

    # 下载文件
    try:
        new_file = None
        if update.message.document:
            new_file = await update.message.document.get_file()
        elif update.message.photo:
            new_file = await update.message.photo[-1].get_file()
        elif update.message.video:
            new_file = await update.message.video.get_file()
        else:
            await update.message.reply_text("❌ 不支持的文件类型")
            return

        file_path = await new_file.download_to_drive()
        context.user_data['file_queue'].append(file_path)
        await update.message.reply_text(f"✅ 已接收文件 ({len(context.user_data['file_queue'])})，继续发送或等待生成代码...")

        # 自动生成代码（延迟5秒，等待用户批量上传）
        context.job_queue.run_once(generate_code_job, 5, user_id=user_id, data=context.user_data)

    except Exception as e:
        logger.error(f"处理文件失败: {e}")
        await update.message.reply_text("❌ 文件处理失败，请重试。")

async def generate_code_job(context: ContextTypes.DEFAULT_TYPE):
    user_data = context.job.data
    user_id = context.job.user_id

    if not user_data.get('file_queue'):
        return

    # 生成唯一代码
    code = generate_unique_code()
    # 上传到OSS
    oss_paths = await upload_files_to_oss(user_data['file_queue'], user_id, code)
    if not oss_paths:
        await context.bot.send_message(user_id, "❌ 文件上传到OSS失败。")
        user_data['file_queue'].clear()
        return

    # 保存到数据库（先设为未命名）
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO user_files (user_id, code, name, file_paths) VALUES (%s, %s, %s, %s)",
        (user_id, code, None, oss_paths)
    )
    conn.commit()
    cur.close()
    conn.close()

    # 询问用户是否命名
    keyboard = [
        [InlineKeyboardButton("✅ 命名", callback_data=f"name_{code}")],
        [InlineKeyboardButton("❌ 跳过", callback_data=f"skip_{code}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(
        user_id,
        f"🎉 文件上传成功！\n你的提取代码: `{code}`\n是否为该代码命名？",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

    # 清空队列
    user_data['file_queue'].clear()

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    # 普通用户删除
    if data.startswith('del_'):
        code = data[4:]
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM user_files WHERE code = %s AND user_id = %s", (code, user_id))
        conn.commit()
        if cur.rowcount > 0:
            await query.edit_message_text(f"🗑️ 代码 `{code}` 已成功删除。", parse_mode='Markdown')
        else:
            await query.edit_message_text("❌ 你没有权限删除此代码。", parse_mode='Markdown')
        cur.close()
        conn.close()
        return

    # 命名/跳过
    elif data.startswith('name_'):
        code = data[5:]
        context.user_data['pending_rename_code'] = code
        await query.edit_message_text("✏️ 请回复此消息，发送你想要设置的名称：")
        return WAITING_FOR_NAME

    elif data.startswith('skip_'):
        code = data[5:]
        await query.edit_message_text(f"✅ 已跳过命名。代码：`{code}`", parse_mode='Markdown')
        return

    # 管理员功能
    if user_id != ADMIN_USER_ID:
        await query.edit_message_text("❌ 你没有管理员权限。")
        return

    if data == "admin_all":
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("SELECT u.code, u.name, u.user_id, u.created_at FROM user_files u ORDER BY u.created_at DESC LIMIT 50")
        files = cur.fetchall()
        cur.close()
        conn.close()

        if not files:
            await query.edit_message_text("📊 数据库中暂无文件记录。")
            return

        text = "📊 所有用户文件（最近50条）：\n"
        for f in files:
            text += f"• 代码: `{f['code']}` | 名称: {f['name'] or '未命名'} | 用户ID: {f['user_id']}\n"
        await query.edit_message_text(text, parse_mode='Markdown')

    elif data == "admin_ban":
        await query.edit_message_text("🔨 请回复此消息，发送要封禁的用户ID：")
        context.user_data['admin_action'] = 'ban'
        return WAITING_FOR_NAME

    elif data == "admin_search":
        await query.edit_message_text("🔎 请回复此消息，发送搜索关键词：")
        context.user_data['admin_action'] = 'search'
        return WAITING_FOR_NAME

async def handle_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    # 处理重命名 (修复了此处的SQL语法错误)
    if 'pending_rename_code' in context.user_data:
        code = context.user_data.pop('pending_rename_code')
        conn = get_db_connection()
        cur = conn.cursor()
        # 完整的SQL语句，无截断
        cur.execute(
            "UPDATE user_files SET name = %s WHERE code = %s AND user_id = %s",
            (text, code, user_id)
        )
        conn.commit()
        cur.close()
        conn.close()
        await update.message.reply_text(f"✅ 代码 `{code}` 已命名为：{text}", parse_mode='Markdown')
        return ConversationHandler.END

    # 处理管理员操作
    if user_id == ADMIN_USER_ID and 'admin_action' in context.user_data:
        action = context.user_data.pop('admin_action')
        if action == 'ban':
            try:
                target_id = int(text)
                conn = get_db_connection()
                cur = conn.cursor()
                cur.execute("INSERT INTO banned_users (user_id) VALUES (%s) ON CONFLICT DO NOTHING", (target_id,))
                conn.commit()
                cur.close()
                conn.close()
                BANNED_USERS.add(target_id)
                await update.message.reply_text(f"🔨 用户 `{target_id}` 已被封禁。", parse_mode='Markdown')
            except ValueError:
                await update.message.reply_text("❌ 无效的用户ID（必须是数字）。")
        elif action == 'search':
            conn = get_db_connection()
            cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cur.execute(
                "SELECT code, name, user_id FROM user_files WHERE code ILIKE %s OR name ILIKE %s",
                (f'%{text}%', f'%{text}%')
            )
            results = cur.fetchall()
            cur.close()
            conn.close()

            if not results:
                await update.message.reply_text(f"❌ 未找到包含「{text}」的记录。")
                return

            reply_text = f"🔎 搜索结果（共{len(results)}条）：\n"
            for r in results:
                reply_text += f"• 代码: `{r['code']}` | 名称: {r['name'] or '未命名'} | 用户ID: {r['user_id']}\n"
            await update.message.reply_text(reply_text, parse_mode='Markdown')
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ 操作已取消。")
    return ConversationHandler.END

# --- 主程序 ---
def main():
    # 初始化数据库
    init_db()

    # 构建应用
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # 对话处理器（处理命名、封禁、搜索）
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler), CommandHandler('admin', admin_panel)],
        states={
            WAITING_FOR_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_conversation)]
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    # 注册处理器
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('myfiles', myfiles))
    application.add_handler(conv_handler)
    # 处理文件上传（排除文本和命令）
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND & ~filters.TEXT, handle_file))

    # 启动机器人
    application.run_polling()

if __name__ == '__main__':
    main()
