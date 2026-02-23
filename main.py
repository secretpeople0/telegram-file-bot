from pyrogram import Client, filters
from pyrogram.types import Message
import os
import uuid
import json
import redis
import asyncio
import boto3
import tempfile
from botocore.config import Config

# 配置
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
REDIS_URL = os.environ.get("REDIS_URL")

# Cloudflare R2
R2_BUCKET = os.environ.get("R2_BUCKET")
R2_ENDPOINT = os.environ.get("R2_ENDPOINT")
R2_ACCESS_KEY = os.environ.get("R2_ACCESS_KEY")
R2_SECRET_KEY = os.environ.get("R2_SECRET_KEY")

# R2 客户端
s3 = boto3.client(
    "s3",
    endpoint_url=R2_ENDPOINT,
    aws_access_key_id=R2_ACCESS_KEY,
    aws_secret_access_key=R2_SECRET_KEY,
    config=Config(signature_version="s3v4"),
)

# Redis
r = redis.from_url(REDIS_URL)

app = Client(
    "my_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# 上传文件到 R2
def upload_to_r2(local_path):
    try:
        ext = os.path.splitext(local_path)[1]
        key = f"files/{uuid.uuid4()}{ext}"
        s3.upload_file(local_path, R2_BUCKET, key)
        return key
    except:
        return None

# 单图/文件
@app.on_message(filters.media & ~filters.media_group)
async def handle_single(client, message: Message):
    try:
        code = str(uuid.uuid4())[:8].upper()
        tmp = await message.download(tempfile.mktemp())
        key = upload_to_r2(tmp)
        os.remove(tmp)

        if not key:
            await message.reply("❌ 上传失败")
            return

        uid = message.from_user.id if message.from_user else 0
        r.set(f"file:{code}", json.dumps({
            "user_id": uid,
            "files": [key]
        }))

        await message.reply(f"✅ 保存成功！\n提取码：`{code}`")
    except Exception as e:
        await message.reply("❌ 出错了")

# 多张图（相册）
@app.on_message(filters.media_group)
async def handle_media_group(client, message: Message):
    try:
        msgs = await client.get_media_group(message.chat.id, message.id)
        code = str(uuid.uuid4())[:8].upper()
        keys = []
        uid = message.from_user.id if message.from_user else 0

        for m in msgs:
            tmp = await m.download(tempfile.mktemp())
            key = upload_to_r2(tmp)
            os.remove(tmp)
            if key:
                keys.append(key)

        if not keys:
            await message.reply("❌ 批量上传失败")
            return

        r.set(f"file:{code}", json.dumps({
            "user_id": uid,
            "files": keys
        }))

        await message.reply(f"✅ 批量保存成功！\n提取码：`{code}`")
    except Exception as e:
        await message.reply("❌ 批量处理失败")

# 提取
@app.on_message(filters.command("get"))
async def get_file(client, message: Message):
    try:
        if len(message.command) < 2:
            await message.reply("❌ 用法：/get 提取码")
            return

        code = message.command[1].upper()
        data = r.get(f"file:{code}")
        if not data:
            await message.reply("❌ 提取码不存在")
            return

        info = json.loads(data)
        uid = message.from_user.id if message.from_user else 0
        if info["user_id"] != uid:
            await message.reply("❌ 无权限")
            return

        keys = info["files"]
        urls = []
        for k in keys:
            url = s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": R2_BUCKET, "Key": k},
                ExpiresIn=3600
            )
            urls.append(url)

        text = "📥 下载链接（1小时有效）：\n" + "\n".join(urls)
        await message.reply_text(text)
    except:
        await message.reply("❌ 文件异常")

# 开始
@app.on_message(filters.command("start"))
async def start(client, message: Message):
    await message.reply("✅ 已启动\n单发/批量发图都支持\n生成提取码 → /get 提取码")

if __name__ == "__main__":
    app.run()
