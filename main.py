import os
import logging
import random
import string
import sqlite3
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    Filters,
    ConversationHandler
)
import oss2

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OSS_ACCESS_KEY = os.getenv("OSS_ACCESS_KEY")
OSS_SECRET_KEY = os.getenv("OSS_SECRET_KEY")
OSS_ENDPOINT = os.getenv("OSS_ENDPOINT")
OSS_BUCKET = os.getenv("OSS_BUCKET")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))
DATABASE_FILE = "/app/bot.db"

WAITING_FOR_NAME = 1
BANNED_USERS = set()

auth = oss2.Auth(OSS_ACCESS_KEY, OSS_SECRET_KEY)
bucket = oss2.Bucket(auth, OSS_ENDPOINT, OSS_BUCKET)

def get_db_connection():
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('''CREATE TABLE IF NOT EXISTS user_files
                       (id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        code TEXT UNIQUE NOT NULL,
                        name TEXT,
                        file_paths TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS banned_users
                       (user_id INTEGER PRIMARY KEY,
                        banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
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
    except:
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
        except:
            continue
    return ''.join(random.choice(chars) for _ in range(8))

async def upload_files_to_oss(file_paths, user_id, code):
    oss_paths = []
    for idx, fp in enumerate(file_paths):
        try:
            oss_path = f"files/{user_id}/{code}/{idx}_{os.path.basename(fp)}"
            with open(fp, 'rb') as f:
                bucket.put_object(oss_path, f)
            oss_paths.append(oss_path)
            os.remove(fp)
        except Exception as e:
            logger.error(f"❌ OSS 上传失败: {e}")
            return None
    return ','.join(oss_paths)

def start(update: Update, context):
    u = update.effective_user
    if is_banned(u.id):
        update.message.reply_text("❌ 你已被封禁")
        return
    update.message.reply_text("👋 发送文件即可存储，自动生成提取码")

def myfiles(update: Update, context):
    u = update.effective_user
    if is_banned(u.id):
        update.message.reply_text("❌ 你已被封禁")
        return
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT code,name,created_at FROM user_files WHERE user_id=? ORDER BY created_at DESC", (u.id,))
        rows = cur.fetchall()
        cur.close()
        conn.close()
    except Exception as e:
        update.message.reply_text(f"⚠️ 数据库异常: {e}")
        return
    if not rows:
        update.message.reply_text("📂 暂无文件")
        return
    text = "📋 你的文件：\n"
    kb = []
    for r in rows:
        text += f"• `{r['code']}` {r['name'] or ''}\n"
        kb.append([InlineKeyboardButton(f"删除 {r['code']}", callback_data=f"del_{r['code']}")])
    update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

def handle_file(update: Update, context):
    u = update.effective_user
    if is_banned(u.id):
        return
    if 'file_queue' not in context.user_data:
        context.user_data['file_queue'] = []
    try:
        f = None
        if update.message.document:
            f = update.message.document.get_file()
        elif update.message.photo:
            f = update.message.photo[-1].get_file()
        elif update.message.video:
            f = update.message.video.get_file()
        else:
            return
        path = f.download()
        context.user_data['file_queue'].append(path)
        update.message.reply_text(f"✅ 已收 {len(context.user_data['file_queue'])} 个文件，等待生成提取码...")
        context.job_queue.run_once(do_gen_code, 6, context=(u.id, context.user_data))
    except Exception as e:
        logger.error(e)
        update.message.reply_text("❌ 处理失败")

def do_gen_code(context):
    uid, ud = context.job.context
    if not ud.get('file_queue'):
        return
    code = generate_unique_code()
    paths = upload_files_to_oss(ud['file_queue'], uid, code)
    if not paths:
        context.bot.send_message(uid, "❌ 上传OSS失败")
        ud['file_queue'].clear()
        return
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("INSERT INTO user_files (user_id,code,name,file_paths) VALUES (?,?,?,?)",
                    (uid, code, None, paths))
        conn.commit()
        cur.close()
        conn.close()
    except:
        pass
    kb = [
        [InlineKeyboardButton("命名", callback_data=f"name_{code}")],
        [InlineKeyboardButton("跳过", callback_data=f"skip_{code}")]
    ]
    context.bot.send_message(uid, f"🎉 提取码：`{code}`",
                               reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
    ud['file_queue'].clear()

def btn(update: Update, context):
    q = update.callback_query
    q.answer()
    uid = q.from_user.id
    data = q.data
    if data.startswith('del_'):
        code = data[4:]
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("DELETE FROM user_files WHERE code=? AND user_id=?", (code, uid))
            conn.commit()
            cur.close()
            conn.close()
            q.edit_message_text(f"🗑️ 已删除 `{code}`", parse_mode='Markdown')
        except:
            q.edit_message_text("❌ 删除失败")
        return
    if data.startswith('name_'):
        code = data[5:]
        context.user_data['pending'] = code
        q.edit_message_text("✏️ 回复消息设置名称")
        return WAITING_FOR_NAME
    if data.startswith('skip_'):
        code = data[5:]
        q.edit_message_text(f"✅ 已跳过 `{code}`", parse_mode='Markdown')
        return

def on_text(update: Update, context):
    if 'pending' in context.user_data:
        code = context.user_data.pop('pending')
        name = update.message.text.strip()
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("UPDATE user_files SET name=? WHERE code=? AND user_id=?",
                        (name, code, update.effective_user.id))
            conn.commit()
            cur.close()
            conn.close()
            update.message.reply_text(f"✅ `{code}` 已命名：{name}", parse_mode='Markdown')
        except:
            update.message.reply_text("❌ 命名失败")
        return ConversationHandler.END
    update.message.reply_text("📤 发送文件即可存储")

def cancel(update: Update, context):
    update.message.reply_text("❌ 已取消")
    return ConversationHandler.END

def main():
    init_db()
    updater = Updater(TELEGRAM_BOT_TOKEN)
    dp = updater.dispatcher
    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(btn)],
        states={WAITING_FOR_NAME: [MessageHandler(Filters.text & ~Filters.command, on_text)]},
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("myfiles", myfiles))
    dp.add_handler(conv)
    dp.add_handler(MessageHandler(Filters.all & ~Filters.command & ~Filters.text, handle_file))
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
