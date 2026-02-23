import os
import time
import logging
from datetime import datetime
import oss2
import psycopg2
from psycopg2 import sql
from telegram import Update, File
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters
)

# 日志配置（简化版，便于排查问题）
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# -------------------------- 核心配置（仅读取，不强制校验，避免启动崩溃） --------------------------
# 所有变量均从 Railway 环境变量读取，缺失时赋空值，由后续逻辑兜底
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
OSS_ACCESS_KEY = os.getenv("OSS_ACCESS_KEY", "")
OSS_SECRET_KEY = os.getenv("OSS_SECRET_KEY", "")
OSS_ENDPOINT = os.getenv("OSS_ENDPOINT", "oss-cn-hangzhou.aliyuncs.com")
OSS_BUCKET = os.getenv("OSS_BUCKET", "my-tg-bot-files")
DATABASE_URL = os.getenv("DATABASE_URL", "")
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "50"))

# -------------------------- OSS 核心函数 --------------------------
def get_oss_bucket():
    """获取 OSS 实例，缺失时返回 None"""
    if not OSS_ACCESS_KEY or not OSS_SECRET_KEY:
        logger.warning("OSS 密钥未配置")
        return None
    try:
        auth = oss2.Auth(OSS_ACCESS_KEY, OSS_SECRET_KEY)
        bucket = oss2.Bucket(auth, OSS_ENDPOINT, OSS_BUCKET)
        # 轻量测试：仅判断 Bucket 是否存在（避免权限不足的 403 报错）
        bucket.list_objects(max_keys=1)
        return bucket
    except Exception as e:
        logger.error(f"OSS 初始化失败: {e}")
        return None

def upload_to_oss(file_path: str, oss_path: str) -> bool:
    """上传文件到 OSS，带 2 次重试"""
    bucket = get_oss_bucket()
    if not bucket:
        return False
    for attempt in range(2):
        try:
            with open(file_path, 'rb') as f:
                res = bucket.put_object(oss_path, f)
            if res.status == 200:
                return True
        except Exception as e:
            logger.warning(f"第 {attempt+1} 次上传失败: {e}")
            time.sleep(1)
    return False

# -------------------------- 数据库核心函数 --------------------------
def init_db():
    """初始化数据库表，失败时仅日志提示，不崩溃"""
    if not DATABASE_URL:
        logger.warning("数据库 URL 未配置，跳过表初始化")
        return
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute(sql.SQL("""
            CREATE TABLE IF NOT EXISTS file_uploads (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                file_name TEXT NOT NULL,
                oss_path TEXT NOT NULL UNIQUE,
                file_size BIGINT,
                upload_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """))
        conn.commit()
        cur.close()
        conn.close()
        logger.info("数据库表初始化成功")
    except Exception as e:
        logger.error(f"数据库初始化失败: {e}")

def save_upload_record(user_id: int, file_name: str, oss_path: str, file_size: int):
    """保存记录到数据库，失败时仅日志提示"""
    if not DATABASE_URL:
        logger.warning("数据库 URL 未配置，跳过记录保存")
        return
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute(sql.SQL("""
            INSERT INTO file_uploads (user_id, file_name, oss_path, file_size)
            VALUES (%s, %s, %s, %s);
        """), (user_id, file_name, oss_path, file_size))
        conn.commit()
        cur.close()
        conn.close()
        logger.info(f"记录已保存: {oss_path}")
    except Exception as e:
        logger.error(f"保存记录失败: {e}")

# -------------------------- 机器人消息处理器 --------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "你好！发送文件即可上传到阿里云 OSS，自动记录到数据库。"
    )

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.message

    # 识别文件类型
    if message.document:
        file = message.document
    elif message.photo:
        file = message.photo[-1]  # 最高清图片
    elif message.video:
        file = message.video
    else:
        await message.reply_text("不支持的文件类型")
        return

    # 文件大小校验
    if file.file_size > MAX_FILE_SIZE_MB * 1024 * 1024:
        await message.reply_text(f"文件过大，最大支持 {MAX_FILE_SIZE_MB}MB")
        return

    # 下载文件到本地
    try:
        file_path = await file.download_to_drive()
        logger.info(f"已下载文件: {file_path}")
    except Exception as e:
        logger.error(f"文件下载失败: {e}")
        await message.reply_text("文件下载失败，请重试")
        return

    # 生成 OSS 存储路径
    oss_path = f"user_{user.id}/{datetime.now().strftime('%Y%m%d_%H%M%S')}_{file.file_name or 'photo.jpg'}"

    # 上传到 OSS
    if upload_to_oss(file_path, oss_path):
        # 保存数据库记录
        save_upload_record(user.id, file.file_name or 'photo.jpg', oss_path, file.file_size)
        # 清理本地文件
        os.unlink(file_path)
        await message.reply_text(f"✅ 上传成功！\nOSS 路径: `{oss_path}`")
    else:
        os.unlink(file_path)
        await message.reply_text("❌ 上传失败，请稍后重试")

# -------------------------- 主程序入口 --------------------------
if __name__ == '__main__':
    # 初始化数据库（失败时不崩溃）
    init_db()
    # 启动机器人
    if not TELEGRAM_BOT_TOKEN:
        logger.error("❌ TELEGRAM_BOT_TOKEN 未配置")
    else:
        app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_file))
        app.run_polling()
