from pyrogram import Client, filters
from pyrogram.types import Message
import os
import json
import uuid
import redis
import oss2
import tempfile
from collections import defaultdict

# ========== 配置 ==========
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
REDIS_URL = os.environ.get("REDIS_URL")

OSS_ENDPOINT = os.environ.get("OSS_ENDPOINT")
OSS_BUCKET = os.environ.get("OSS_BUCKET")
OSS_ACCESS_KEY = os.environ.get("OSS_ACCESS_KEY")
OSS_SECRET_KEY = os.environ.get("OSS_SECRET_KEY")

ADMIN_IDS = list(map(int, os.environ.get("ADMIN_IDS", "").split(","))) if os.environ.get("ADMIN_IDS") else []

# ========== 初始化 ==========
auth = oss2.Auth(OSS_ACCESS_KEY, OSS_SECRET_KEY)
bucket = oss2.Bucket(auth, OSS_ENDPOINT, OSS_BUCKET)
r = redis.from_url(REDIS_URL)

app = Client("file_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

user_upload_temp = defaultdict(dict)
pending_purge = {}

# ========== 工具 ==========
def is_admin(user_id: int):
    return user_id in ADMIN_IDS

def upload_to_oss(file_path):
    try:
        ext = os.path.splitext(file_path)[1]
        key = f"files/{uuid.uuid4()}{ext}"
        bucket.put_object_from_file(key, file_path)
        return key
    except:
        return None

# ========== 用户功能 ==========
@app.on_message(filters.command("start"))
async def start(client, message):
    welcome = """👋 欢迎使用永久文件保存机器人

✅ 使用方法：
直接发送 图片 / 文件 / 相册 即可自动上传

📁 文件夹命名：
• 上传后发送文字 = 设置文件夹名
• 上传后发送 /skip = 不命名（默认未命名）

🔗 获取下载链接：
/get 提取码

💡 所有提取码永久有效"""
    await message.reply(welcome)

# 图片/文件上传（必触发，不会卡住）
@app.on_message(filters.media)
async def upload_media(client, message):
    uid = message.from_user.id
    tmp = tempfile.mktemp()
    await message.download(tmp)
    
    key = upload_to_oss(tmp)
    os.remove(tmp)
    
    if not key:
        await message.reply("❌ 上传失败")
        return

    user_upload_temp[uid] = {
        "keys": [key],
        "pending": True
    }
    await message.reply("📝 请输入文件夹名，或发送 /skip 跳过")

# 文字 = 文件夹名
@app.on_message(filters.text & filters.private)
async def name_folder(client, message):
    uid = message.from_user.id
    if not user_upload_temp[uid].get("pending"):
        return

    folder = message.text.strip()
    data = user_upload_temp[uid]
    del user_upload_temp[uid]

    code = uuid.uuid4().hex[:8].upper()
    r.set(f"file:{code}", json.dumps({
        "user_id": uid,
        "keys": data["keys"],
        "folder": folder
    }))

    await message.reply(f"✅ 保存成功\n📁 文件夹：{folder}\n📌 提取码：`{code}`\n🔗 /get {code}")

# 跳过命名
@app.on_message(filters.command("skip"))
async def skip(client, message):
    uid = message.from_user.id
    if not user_upload_temp[uid].get("pending"):
        await message.reply("❌ 请先上传文件")
        return

    data = user_upload_temp[uid]
    del user_upload_temp[uid]
    code = uuid.uuid4().hex[:8].upper()

    r.set(f"file:{code}", json.dumps({
        "user_id": uid,
        "keys": data["keys"],
        "folder": None
    }))

    await message.reply(f"✅ 保存成功\n📁 文件夹：未命名\n📌 提取码：`{code}`\n🔗 /get {code}")

# 获取下载链接
@app.on_message(filters.command("get"))
async def get(client, message):
    if len(message.command) < 2:
        await message.reply("❌ 用法：/get 提取码")
        return
    code = message.command[1].upper()
    data = r.get(f"file:{code}")
    if not data:
        await message.reply("❌ 提取码不存在")
        return
    d = json.loads(data)
    if message.from_user.id != d["user_id"] and not is_admin(message.from_user.id):
        await message.reply("❌ 无权限")
        return
    urls = [bucket.sign_url("GET", k, 3600) for k in d["keys"]]
    folder = d.get("folder") or "未命名"
    await message.reply(f"📁 文件夹：{folder}\n🔗 下载链接：\n" + "\n".join(urls))

# ========== 管理员 ==========
@app.on_message(filters.command("admin"))
async def admin(client, message):
    if not is_admin(message.from_user.id):
        await message.reply("❌ 无权限")
        return
    txt = """🔐 管理员菜单
/admin — 菜单
/list — 全部记录
/user ID — 查询用户
/delete 码 — 删除
/purge ID — 清空用户
/confirm — 确认清空
/stats — 统计
/clean — 清理无效"""
    await message.reply(txt)

@app.on_message(filters.command("list"))
async def list_all(client, message):
    if not is_admin(message.from_user.id):
        await message.reply("❌ 无权限")
        return
    keys = r.keys("file:*")
    if not keys:
        await message.reply("暂无记录")
        return
    lines = []
    for k in keys:
        code = k.decode().split(":")[1]
        d = json.loads(r.get(k))
        folder = d.get("folder") or "未命名"
        lines.append(f"`{code}` | UID:{d['user_id']} | {len(d['keys'])}个 | {folder}")
    await message.reply("\n".join(lines))

@app.on_message(filters.command("user"))
async def user_files(client, message):
    if not is_admin(message.from_user.id):
        await message.reply("❌ 无权限")
        return
    if len(message.command) < 2:
        await message.reply("/user <ID>")
        return
    target = int(message.command[1])
    allkeys = r.keys("file:*")
    res = []
    for k in allkeys:
        d = json.loads(r.get(k))
        if d["user_id"] == target:
            code = k.decode().split(":")[1]
            folder = d.get("folder") or "未命名"
            res.append(f"`{code}` | {len(d['keys'])}个 | {folder}")
    if not res:
        await message.reply("无文件")
        return
    await message.reply("\n".join(res))

@app.on_message(filters.command("delete"))
async def delete_code(client, message):
    if not is_admin(message.from_user.id):
        await message.reply("❌ 无权限")
        return
    if len(message.command) < 2:
        await message.reply("/delete <提取码>")
        return
    code = message.command[1].upper()
    key = f"file:{code}"
    data = r.get(key)
    if not data:
        await message.reply("不存在")
        return
    d = json.loads(data)
    for f in d["keys"]:
        try:
            bucket.delete_object(f)
        except:
            pass
    r.delete(key)
    await message.reply(f"✅ {code} 已删除")

@app.on_message(filters.command("purge"))
async def purge(client, message):
    if not is_admin(message.from_user.id):
        await message.reply("❌ 无权限")
        return
    if len(message.command) < 2:
        await message.reply("/purge <ID>")
        return
    target = int(message.command[1])
    pending_purge[message.from_user.id] = target
    await message.reply(f"⚠️ 确定清空 {target}？发送 /confirm 确认")

@app.on_message(filters.command("confirm"))
async def confirm(client, message):
    uid = message.from_user.id
    if not is_admin(uid):
        await message.reply("❌ 无权限")
        return
    if uid not in pending_purge:
        await message.reply("❌ 无待确认操作")
        return
    target = pending_purge[uid]
    del pending_purge[uid]
    allkeys = r.keys("file:*")
    cnt_code = 0
    cnt_file = 0
    for k in allkeys:
        d = json.loads(r.get(k))
        if d["user_id"] == target:
            for f in d["keys"]:
                try:
                    bucket.delete_object(f)
                    cnt_file += 1
                except:
                    pass
            r.delete(k)
            cnt_code += 1
    await message.reply(f"🗑️ 清空完成\n用户：{target}\n提取码：{cnt_code}\n文件：{cnt_file}")

@app.on_message(filters.command("stats"))
async def stats(client, message):
    if not is_admin(message.from_user.id):
        await message.reply("❌ 无权限")
        return
    codes = len(r.keys("file:*"))
    await message.reply(f"📊 提取码总数：{codes}")

@app.on_message(filters.command("clean"))
async def clean(client, message):
    if not is_admin(message.from_user.id):
        await message.reply("❌ 无权限")
        return
    keys = r.keys("file:*")
    bad = 0
    for k in keys:
        if not r.get(k):
            r.delete(k)
            bad += 1
    await message.reply(f"🧹 清理无效记录：{bad}")

if __name__ == "__main__":
    app.run()
