from pyrogram import Client, filters
from pyrogram.types import Message
import os
import uuid
import json
import redis
import oss2
import tempfile
import traceback

# 从环境变量读取配置
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
REDIS_URL = os.environ.get("REDIS_URL")

OSS_ENDPOINT = os.environ.get("OSS_ENDPOINT")
OSS_BUCKET = os.environ.get("OSS_BUCKET")
OSS_ACCESS_KEY = os.environ.get("OSS_ACCESS_KEY")
OSS_SECRET_KEY = os.environ.get("OSS_SECRET_KEY")

# 初始化阿里云 OSS
auth = oss2.Auth(OSS_ACCESS_KEY, OSS_SECRET_KEY)
bucket = oss2.Bucket(auth, OSS_ENDPOINT, OSS_BUCKET)

# 初始化 Redis
r = redis.from_url(REDIS_URL)

app = Client("tg_oss_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# 上传文件到 OSS
def upload_to_oss(file_path):
    try:
        file_ext = os.path.splitext(file_path)[1]
        object_key = f"tg_files/{uuid.uuid4()}{file_ext}"
        bucket.put_object_from_file(object_key, file_path)
        return object_key
    except Exception as e:
        print(f"上传失败: {e}")
        return None

# 处理 /start 命令
@app.on_message(filters.command("start"))
async def start(client, message):
    await message.reply("✅ 机器人已启动！发送图片或文件，我会帮你保存到阿里云 OSS。")

# 处理 /get 命令，根据提取码获取下载链接
@app.on_message(filters.command("get"))
async def get_file(client, message):
    if len(message.command) < 2:
        await message.reply("❌ 用法：/get 提取码")
        return
    code = message.command[1].upper()
    data = r.get(f"file:{code}")
    if not data:
        await message.reply("❌ 提取码不存在或已过期")
        return
    data = json.loads(data)
    if message.from_user.id != data["user_id"]:
        await message.reply("❌ 你没有权限获取这个文件")
        return
    # 生成带签名的下载链接（1小时有效期）
    urls = [bucket.sign_url("GET", key, 3600) for key in data["keys"]]
    await message.reply("📥 下载链接（1小时内有效）：\n" + "\n".join(urls))

# 处理单张图片/单个文件
@app.on_message(filters.media & ~filters.media_group)
async def handle_single_media(client, message):
    try:
        # 下载文件到临时目录
        tmp_path = tempfile.mktemp()
        await message.download(tmp_path)
        # 上传到 OSS
        key = upload_to_oss(tmp_path)
        os.remove(tmp_path)
        if not key:
            await message.reply("❌ 文件上传到 OSS 失败")
            return
        # 生成提取码并存入 Redis
        code = uuid.uuid4().hex[:8].upper()
        r.set(f"file:{code}", json.dumps({
            "user_id": message.from_user.id,
            "keys": [key]
        }), ex=86400)  # 提取码有效期 24 小时
        await message.reply(f"✅ 保存成功！\n提取码：`{code}`\n使用 /get {code} 获取下载链接")
    except Exception as e:
        await message.reply(f"❌ 处理失败: {traceback.format_exc()}")

# 处理相册（多张图片）
@app.on_message(filters.media_group)
async def handle_media_group(client, message):
    try:
        group = await client.get_media_group(message.chat.id, message.id)
        keys = []
        for msg in group:
            tmp_path = tempfile.mktemp()
            await msg.download(tmp_path)
            key = upload_to_oss(tmp_path)
            os.remove(tmp_path)
            if key:
                keys.append(key)
        if not keys:
            await message.reply("❌ 批量上传失败，没有文件成功保存")
            return
        code = uuid.uuid4().hex[:8].upper()
        r.set(f"file:{code}", json.dumps({
            "user_id": message.from_user.id,
            "keys": keys
        }), ex=86400)
        await message.reply(f"✅ 批量保存成功！共 {len(keys)} 个文件\n提取码：`{code}`\n使用 /get {code} 获取下载链接")
    except Exception as e:
        await message.reply(f"❌ 批量处理失败: {traceback.format_exc()}")

if __name__ == "__main__":
    app.run()
