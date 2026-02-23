import os
import time
import logging
from typing import Optional
from telegram import Update, File
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters
)
import oss2
import psycopg2
from psycopg2 import sql

# 配置日志
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# 直接从环境变量读取配置（Railway 上直接配置）
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OSS_ACCESS_KEY = os.getenv("OSS_ACCESS_KEY")
OSS_SECRET_KEY = os.getenv("OSS_SECRET_KEY")
OSS_ENDPOINT = os.getenv("OSS_ENDPOINT", "oss-cn-hangzhou.aliyuncs.com")
OSS_BUCKET = os.getenv("OSS_BUCKET", "my-tg-bot-files")
DATABASE_URL = os.getenv("DATABASE_URL")
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "50"))
UPLOAD_TIMEOUT = int(os.getenv("UPLOAD_TIMEOUT", "300"))

# 全局 OSS Bucket 实例（懒加载）
_oss_bucket: Optional[oss2.Bucket] = None

def get_oss_bucket() -> Optional[oss2.Bucket]:
    """获取 OSS Bucket 实例，失败时返回 None 而不是崩溃"""
    global _oss_bucket
    if _oss_bucket is None:
        try:
            auth = oss2.Auth(OSS_ACCESS_KEY, OSS_SECRET_KEY)
            _oss_bucket = oss2.Bucket(auth, OSS_ENDPOINT, OSS_BUCKET)
            # 测试连接
            _oss_bucket.get_bucket_acl()
            logger.info("✅ OSS Bucket 初始化成功")
        except Exception as e:
            logger.error(f"❌ OSS Bucket 初始化失败: {e}")
            _oss_bucket = None
    return _oss_bucket

def upload_to_oss(file_path: str, object_name: str, max_retries: int = 3) -> bool:
    """
    上传文件到 OSS，带重试机制
    :param file_path: 本地文件路径
    :param object_name: OSS 中的对象名
    :param max_retries: 最大重试次数
    :return: 是否上传成功
    """
    bucket = get_oss_bucket()
    if not bucket:
        return False

    for attempt in range(max_retries):
        try:
            with open(file_path, 'rb') as f:
                result = bucket.put_object(object_name, f)
            if result.status == 200:
                logger.info(f"✅ 文件 {object_name} 上传成功")
                return True
            else:
                logger.warning(f"⚠️ 第 {attempt+1} 次上传失败，状态码: {result.status}")
        except Exception as e:
            logger.error(f"❌ 第 {attempt+1} 次上传异常: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # 指数退避重试
    return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="👋 你好！我是文件存储机器人，直接发送文件给我即可上传到 OSS。"
    )

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    file: File = update.message.document or update.message.photo[-1] or update.message.video
    if not file:
        await update.message.reply_text("❌ 无法识别的文件类型")
        return

    # 检查文件大小
    if file.file_size > MAX_FILE_SIZE_MB * 1024 * 1024:
        await update.message.reply_text(f"❌ 文件过大，最大支持 {MAX_FILE_SIZE_MB}MB")
        return

    # 下载文件到本地临时目录
    try:
        file_path = await file.download_to_drive()
        logger.info(f"✅ 文件 {file.file_name} 下载到本地成功")
    except Exception as e:
        logger.error(f"❌ 文件下载失败: {e}")
        await update.message.reply_text("❌ 文件下载失败，请重试")
        return

    # 生成 OSS 对象名（用户ID/时间戳_文件名）
    timestamp = int(time.time())
    object_name = f"{user_id}/{timestamp}_{file.file_name}"

    # 上传到 OSS
    if upload_to_oss(file_path, object_name):
        # 上传成功，删除本地临时文件
        os.unlink(file_path)
        await update.message.reply_text(f"✅ 文件上传成功！\nOSS 路径: `{object_name}`")
    else:
        os.unlink(file_path)
        await update.message.reply_text("❌ 文件上传到 OSS 失败，请稍后重试")

def main():
    # 校验必填配置
    if not all([TELEGRAM_BOT_TOKEN, OSS_ACCESS_KEY, OSS_SECRET_KEY, DATABASE_URL]):
        raise ValueError("❌ 关键环境变量缺失，请检查 Railway 配置")
    logger.info("✅ 所有配置校验通过")

    # 初始化 OSS
    get_oss_bucket()

    # 启动 Telegram Bot
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # 注册处理器
    application.add_handler(CommandHandler('start', start))
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_file))

    # 启动轮询
    application.run_polling()

if __name__ == '__main__':
    main()
