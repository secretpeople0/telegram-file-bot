from pyrogram import Client, filters
from pyrogram.types import Message
import os
import uuid
import zipfile
from io import BytesIO
import json
import redis
import asyncio

# 从环境变量读取配置
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
REDIS_URL = os.environ.get("REDIS_URL")

# 连接 Redis
r = redis.from_url(REDIS_URL)

app = Client(
    "my_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# 临时存放相册消息
pending_album = {}

# 处理相册（多张图片一起发）
@app.on_message(filters.media_group)
async def handle_media_group(client, message: Message):
    gid = message.media_group_id
    uid = message.from_user.id if message.from_user else 0

    if gid not in pending_album:
        pending_album[gid] = {
            "user_id": uid,
            "msgs": [],
        }

    pending_album[gid]["msgs"].append(message)
    await asyncio.sleep(1)

    if len(pending_album[gid]["msgs"]) == message.media_group_count:
        await process_album(client, gid)

async def process_album(client, gid):
    data = pending_album.pop(gid)
    msgs = data["msgs"]
    uid = data["user_id"]
    code = str(uuid.uuid4())[:8].upper()

    paths = []
    for msg in msgs:
        path = await msg.download()
        if path:
            paths.append(path)

    r.set(f"file:{code}", json.dumps({
        "user_id": uid,
        "files": paths,
    }))

    await msgs[0].reply(f"✅ 批量保存成功！\n提取码：`{code}`\n使用 /get {code} 提取全部文件")

# 处理单张图片
@app.on_message(filters.media & ~filters.media_group)
async def handle_single_media(client, message: Message):
    code = str(uuid.uuid4())[:8].upper()
    path = await message.download()
    uid = message.from_user.id if message.from_user else 0

    r.set(f"file:{code}", json.dumps({
        "user_id": uid,
        "files": [path],
    }))

    await message.reply(f"✅ 保存成功！\n提取码：`{code}`\n使用 /get {code} 提取文件")

# 提取文件
@app.on_message(filters.command("get"))
async def get_file(client, message: Message):
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
        await message.reply("❌ 你没有权限提取这个文件")
        return

    files = info["files"]

    if len(files) == 1:
        await message.reply_document(files[0], caption=f"提取码：{code}")
    else:
        z = BytesIO()
        z.name = f"{code}.zip"
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zp:
            for f in files:
                zp.write(f, os.path.basename(f))
        z.seek(0)
        await message.reply_document(z, caption=f"✅ 批量提取：共 {len(files)} 个文件")

@app.on_message(filters.command("start"))
async def start(client, message: Message):
    await message.reply("✅ 发送文件/图片，自动生成提取码\n多张图片 = 一个提取码")

if __name__ == "__main__":
    app.run()
