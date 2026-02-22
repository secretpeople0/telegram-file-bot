from pyrogram import Client, filters
 import os
 import random
 import string
 import psycopg2
 BOT_TOKEN = os.getenv("BOT_TOKEN")
 API_ID = int(os.getenv("API_ID"))
 API_HASH = os.getenv("API_HASH")
 ADMIN = int(os.getenv("ADMIN_ID"))
 DB_URL = os.getenv("DATABASE_URL")
 app = Client("filebot", bot_token=BOT_TOKEN, api_id=API_ID, api_hash=API_HASH)
 def db():
     conn = psycopg2.connect(DB_URL)
     cur = conn.cursor()
     cur.execute('''CREATE TABLE IF NOT EXISTS f(
         id SERIAL PRIMARY KEY,
         code TEXT UNIQUE,
         fid TEXT,
         tp TEXT,
         uid BIGINT)''')
     conn.commit()
     cur.close()
     return conn
 db()
 def code():
     return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
 @app.on_message(filters.command("start"))
 async def start(_, msg):
     await msg.reply("""
 📤 发送文件/图片/视频 = 生成提取码
 🔍 直接输入提取码 = 获取文件
 📋 /mycodes 查看我的提取码
 🗑️ /delcode 提取码 = 删除
 👨‍💼 /allcodes 管理员查看所有
 """)
 @app.on_message(filters.document | filters.photo | filters.video)
 async def upload(_, msg):
     uid = msg.from_user.id
     if msg.document:
         fid = msg.document.file_id
         tp = "doc"
     elif msg.photo:
         fid = msg.photo.file_id
         tp = "pic"
     elif msg.video:
         fid = msg.video.file_id
         tp = "vid"
     else:
         return
     c = code()
     conn = db()
     cur = conn.cursor()
     cur.execute("INSERT INTO f (code, fid, tp, uid) VALUES (%s,%s,%s,%s)", (c, fid, tp, uid))
     conn.commit()
     cur.close()
     conn.close()
     await msg.reply(f"✅ 已保存\n提取码：`{c}`")
 @app.on_message(filters.text & ~filters.command)
 async def get_file(_, msg):
     c = msg.text.strip()
     conn = db()
     cur = conn.cursor()
     cur.execute("SELECT fid, tp FROM f WHERE code=%s", (c,))
     row = cur.fetchone()
     cur.close()
     conn.close()
     if not row:
         await msg.reply("❌ 提取码无效")
         return
     fid, tp = row
     if tp == "doc": await msg.reply_document(fid)
     if tp == "pic": await msg.reply_photo(fid)
     if tp == "vid": await msg.reply_video(fid)
 @app.on_message(filters.command("mycodes"))
 async def mycodes(_, msg):
     uid = msg.from_user.id
     conn = db()
     cur = conn.cursor()
     cur.execute("SELECT code FROM f WHERE uid=%s", (uid,))
     rows = cur.fetchall()
     cur.close()
     conn.close()
     if not rows:
         await msg.reply("📭 你还没有文件")
         return
     txt = "📁 你的提取码：\n"
     for r in rows:
         txt += f"`{r[0]}`\n"
     await msg.reply(txt)
 @app.on_message(filters.command("delcode"))
 async def delcode(_, msg):
     if len(msg.command) < 2:
         await msg.reply("用法：/delcode 提取码")
         return
     c = msg.command[1]
     uid = msg.from_user.id
     conn = db()
     cur = conn.cursor()
     cur.execute("SELECT uid FROM f WHERE code=%s", (c,))
     row = cur.fetchone()
     if not row:
         await msg.reply("❌ 不存在")
         return
     if row[0] != uid and uid != ADMIN:
         await msg.reply("❌ 无权限")
         return
     cur.execute("DELETE FROM f WHERE code=%s", (c,))
     conn.commit()
     cur.close()
     conn.close()
     await msg.reply("🗑️ 已删除")
 @app.on_message(filters.command("allcodes"))
 async def allcodes(_, msg):
     if msg.from_user.id != ADMIN:
         await msg.reply("❌ 无权限")
         return
     conn = db()
     cur = conn.cursor()
     cur.execute("SELECT code, uid FROM f LIMIT 50")
     rows = cur.fetchall()
     cur.close()
     conn.close()
     if not rows:
         await msg.reply("📭 暂无文件")
         return
     txt = "📋 全部提取码：\n"
     for code_, uid in rows:
         txt += f"`{code_}` | 用户:{uid}\n"
     await msg.reply(txt)
 app.run()
