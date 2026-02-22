from pyrogram import Client, filters
import os
import random
import string
import psycopg2

# 从环境变量读取配置
BOT_TOKEN = os.getenv("BOT_TOKEN")
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
DATABASE_URL = os.getenv("DATABASE_URL")

app = Client("filebot", bot_token=BOT_TOKEN, api_id=API_ID, api_hash=API_HASH)

# 初始化数据库连接
def init_db():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS files (
            id SERIAL PRIMARY KEY,
            code TEXT UNIQUE,
            file_id TEXT,
            file_type TEXT,
            user_id BIGINT
        )
    ''')
    conn.commit()
    cur.close()
    conn.close()

# 生成随机提取码
def generate_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

@app.on_message(filters.command("start"))
async def start(client, message):
    await message.reply("✅ 我是文件存储机器人！\n发送文件/图片/视频给我，我会给你一个提取码。")

@app.on_message(filters.document | filters.photo | filters.video)
async def handle_file(client, message):
    if message.document:
        file_id = message.document.file_id
        file_type = "document"
    elif message.photo:
        file_id = message.photo.file_id
        file_type = "photo"
    elif message.video:
        file_id = message.video.file_id
        file_type = "video"
    else:
        await message.reply("❌ 不支持的文件类型")
        return

    code = generate_code()
    user_id = message.from_user.id

    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO files (code, file_id, file_type, user_id) VALUES (%s, %s, %s, %s)",
            (code, file_id, file_type, user_id)
        )
        conn.commit()
        cur.close()
        conn.close()
        await message.reply(f"✅ 文件已保存！\n提取码：`{code}`")
    except Exception as e:
        await message.reply(f"❌ 保存失败：{str(e)}")

@app.on_message(filters.command("mycodes"))
async def my_codes(client, message):
    user_id = message.from_user.id
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("SELECT code, file_type FROM files WHERE user_id = %s", (user_id,))
        rows = cur.fetchall()
        cur.close()
        conn.close()

        if not rows:
            await message.reply("📭 你没有保存任何文件")
            return

    except Exception as e:
        await message.reply(f"❌ 查询失败：{str(e)}")
        return

    text = "📁 你的提取码：\n"
    for code, file_type in rows:
        text += f"- `{code}` ({file_type})\n"
    await message.reply(text)

@app.on_message(filters.command("delcode"))
async def del_code(client, message):
    if len(message.command) < 2:
        await message.reply("⚠️ 用法：/delcode [提取码]")
        return

    code = message.command[1]
    user_id = message.from_user.id

    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM files WHERE code = %s AND user_id = %s",
            (code, user_id)
        )
        if cur.rowcount == 0:
            await message.reply("❌ 提取码不存在或不属于你")
        else:
            conn.commit()
            await message.reply("✅ 文件已删除")
        cur.close()
        conn.close()
    except Exception as e:
        await message.reply(f"❌ 删除失败：{str(e)}")

@app.on_message(filters.command("allcodes"))
async def all_codes(client, message):
    if message.from_user.id != ADMIN_ID:
        await message.reply("❌ 你没有权限使用此命令")
        return

    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("SELECT code, user_id, file_type FROM files")
        rows = cur.fetchall()
        cur.close()
        conn.close()

        if not rows:
            await message.reply("📭 数据库中没有任何文件")
            return

    except Exception as e:
        await message.reply(f"❌ 查询失败：{str(e)}")
        return

    text = "📊 所有文件：\n"
    for code, user_id, file_type in rows:
        text += f"- `{code}` | 用户: {user_id} | 类型: {file_type}\n"
    await message.reply(text)

@app.on_message(filters.text & ~filters.command(["start", "mycodes", "delcode", "allcodes"]))
async def get_file(client, message):
    code = message.text.strip()
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("SELECT file_id, file_type FROM files WHERE code = %s", (code,))
        row = cur.fetchone()
        cur.close()
        conn.close()

        if not row:
            await message.reply("❌ 提取码不存在")
            return

        file_id, file_type = row
        if file_type == "document":
            await app.send_document(message.chat.id, file_id)
        elif file_type == "photo":
            await app.send_photo(message.chat.id, file_id)
        elif file_type == "video":
            await app.send_video(message.chat.id, file_id)
    except Exception as e:
        await message.reply(f"❌ 获取失败：{str(e)}")

if __name__ == "__main__":
    init_db()
    app.run()
