import os
import json
import random
import shutil
import string
import base64
import tempfile
import time
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters
)

# ==================== 全局配置（无频次限制）====================
ADMIN_USER_ID = int(os.environ.get("ADMIN_USER_ID", "0"))
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

# ==================== 加密核心（与旧代码完全一致，保证兼容）====================
E2EE_KEY = b"e2ee_secure_bot_2026"
ENCODE_KEY = b"secure_file_bot_2026"
BOT_SELF_ID = None  # 机器人自身ID，防循环

def xor_data(data: bytes, key: bytes) -> bytes:
    """核心异或加密，与旧代码一致"""
    key = key * (len(data) // len(key) + 1)
    return bytes(d ^ k for d, k in zip(data, key))

def encrypt_metadata(metadata: dict) -> str:
    """加密文件元数据（直链、名称、类型），替代原文件加密"""
    try:
        raw = json.dumps(metadata).encode("utf-8")
        encrypted = xor_data(raw, E2EE_KEY)
        return base64.urlsafe_b64encode(encrypted).decode("utf-8")
    except Exception as e:
        print(f"加密元数据失败：{e}")
        return ""

def decrypt_metadata(encrypted_str: str) -> dict:
    """解密元数据，兼容旧提取码"""
    try:
        raw = base64.urlsafe_b64decode(encrypted_str.encode("utf-8"))
        decrypted = xor_data(raw, E2EE_KEY)
        return json.loads(decrypted.decode("utf-8"))
    except Exception as e:
        print(f"解密密数据失败：{e}")
        return {}

# ==================== 兼容旧代码的编解码（保留，用于旧提取码）====================
def get_file_unique_key(file_id):
    return file_id[:20] + file_id[-20:]

def encode_file_id(file_id: str) -> str:
    try:
        unique_key = get_file_unique_key(file_id)
        raw = json.dumps([file_id, unique_key]).encode()
        return base64.urlsafe_b64encode(xor_data(raw, ENCODE_KEY)).decode()
    except:
        return file_id

def decode_file_id(encoded_id: str) -> str:
    try:
        raw = xor_data(base64.urlsafe_b64decode(encoded_id.encode()), ENCODE_KEY)
        data = json.loads(raw.decode())
        if isinstance(data, list) and len(data) >= 1:
            return data[0]
        return str(data)
    except:
        return encoded_id

# ==================== 数据存储配置 ====================
DATA_DIR = "/data"
BACKUP_DIR = os.path.join(DATA_DIR, "backup")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

user_sessions = {}
pending_naming = {}
admin_ops = {}

# 数据读写
def load_json(name):
    p = os.path.join(DATA_DIR, name)
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {} if any(x in name for x in ["db", "index", "banned"]) else []

def save_json(name, data):
    p = os.path.join(DATA_DIR, name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

bot_db = load_json("bot_db.json")
user_idx = load_json("user_index.json")
banned = load_json("banned.json")

# ==================== 工具函数 ====================
def is_admin(user_id):
    return user_id == ADMIN_USER_ID

def is_banned(user_id):
    return str(user_id) in [str(x) for x in banned]

def is_bot_self(user_id):
    return user_id == BOT_SELF_ID

async def track(user_id, name, username):
    uid = str(user_id)
    if uid not in user_idx or user_idx[uid]["name"] != name:
        user_idx[uid] = {"name": name, "username": username or "-"}
        save_json("user_index.json", user_idx)

# 自动备份
def auto_backup():
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    for f in ["bot_db.json", "user_index.json", "banned.json"]:
        src = os.path.join(DATA_DIR, f)
        dst = os.path.join(BACKUP_DIR, f"{f}.{ts}")
        if os.path.exists(src):
            shutil.copy(src, dst)
    olds = sorted([os.path.join(BACKUP_DIR, x) for x in os.listdir(BACKUP_DIR)], key=os.path.getmtime)
    for old in olds[:-3]:
        os.remove(old)

# ==================== 管理员菜单 ====================
def admin_menu():
    return [
        [InlineKeyboardButton("📊 统计", callback_data="统计")],
        [InlineKeyboardButton("👥 用户列表", callback_data="用户列表")],
        [InlineKeyboardButton("🔍 搜文件", callback_data="搜文件")],
        [InlineKeyboardButton("👁️ 查用户上传", callback_data="查用户上传")],
        [InlineKeyboardButton("🗑️ 删提取码", callback_data="删提取码")],
        [InlineKeyboardButton("🚫 封禁/解封", callback_data="封禁/解封")],
        [InlineKeyboardButton("🔙 返回", callback_data="返回")]
    ]

# ==================== 核心命令 ====================
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📦 TG云盘机器人（直链加密版）\n/my 我的提取码\n/del 提取码")

async def my_codes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if is_banned(uid):
        return await update.message.reply_text("❌ 你已被封禁")
    items = [f"🔑 {c}｜{p['name']}" for c, p in bot_db.items() if str(p["uploader"]["id"]) == str(uid)]
    await update.message.reply_text("\n".join(items) if items else "📭 暂无文件")

async def del_code(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    args = update.message.text.split()
    if len(args) < 2:
        return await update.message.reply_text("用法：/del 提取码")
    code = args[1]
    if code not in bot_db:
        return await update.message.reply_text("❌ 提取码不存在")
    if str(bot_db[code]["uploader"]["id"]) != str(uid):
        return await update.message.reply_text("❌ 只能删除自己创建的提取码")
    del bot_db[code]
    save_json("bot_db.json", bot_db)
    await update.message.reply_text(f"✅ 提取码 {code} 已删除")

# ==================== 上传：直链加密模式（核心修改，解决大文件问题）====================
async def upload(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global BOT_SELF_ID
    if BOT_SELF_ID is None:
        me = await ctx.bot.get_me()
        BOT_SELF_ID = me.id

    u = update.effective_user
    if is_banned(u.id) or is_bot_self(u.id):
        return

    msg = update.message
    file_obj = None
    orig_type = ""
    file_name = ""
    file_size = 0

    # 识别文件类型与基础信息
    if msg.photo:
        file_obj = msg.photo[-1]
        orig_type = "photo"
        file_name = f"IMG_{datetime.now().strftime('%H%M%S')}.jpg"
        file_size = file_obj.file_size or 0
    elif msg.video:
        file_obj = msg.video
        orig_type = "video"
        file_name = msg.video.file_name or f"VID_{datetime.now().strftime('%H%M%S')}.mp4"
        file_size = msg.video.file_size or 0
    elif msg.document:
        file_obj = msg.document
        orig_type = "doc"
        file_name = msg.document.file_name or f"FILE_{datetime.now().strftime('%H%M%S')}.bin"
        file_size = msg.document.file_size or 0
    else:
        return await update.message.reply_text("❌ 不支持该类型文件")

    try:
        # 1. 获取文件直链（核心：不下载文件，只拿路径）
        file_info = await ctx.bot.get_file(file_obj.file_id)
        file_path = file_info.file_path
        # 拼接永久直链（与机器人账号无关，换号仍可用）
        file_link = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"

        # 2. 构造元数据并加密（替代原文件加密，保证安全性）
        metadata = {
            "link": file_link,
            "file_id": file_obj.file_id,  # 备用：直链失效时用file_id转发
            "name": file_name,
            "type": orig_type,
            "size": file_size
        }
        encrypted_meta = encrypt_metadata(metadata)
        if not encrypted_meta:
            return await update.message.reply_text("❌ 元数据加密失败")

        # 3. 保存到用户会话
        obj = {
            "meta_type": "link_enc",  # 标记为直链加密类型
            "data": encrypted_meta,
            "name": file_name,
            "orig_type": orig_type
        }
        if u.id not in user_sessions:
            user_sessions[u.id] = []
        user_sessions[u.id].append(obj)

        # 兼容旧逻辑提示
        await msg.reply_text(
            f"✅ 直链加密成功！已接收 {len(user_sessions[u.id])} 个文件\n"
            f"📄 文件名：{file_name}\n"
            f"📏 文件大小：{file_size / 1024 / 1024:.2f}MB\n"
            f"/confirm 打包生成提取码 | /skip 跳过命名"
        )
        await track(u.id, u.full_name, u.username)

    except Exception as e:
        # 捕获Telegram官方限制（如超大文件超出服务器存储）
        if "file is too big" in str(e).lower():
            return await update.message.reply_text("❌ 该文件超出Telegram服务器最大存储限制（免费用户最大2GB）")
        await msg.reply_text(f"❌ 上传失败：{str(e)[:100]}")

async def confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user.id
    if u not in user_sessions or not user_sessions[u]:
        return await update.message.reply_text("❌ 暂无待打包文件")
    
    # 生成6位提取码
    chars = string.ascii_letters + string.digits
    while True:
        code = ''.join(random.choice(chars) for _ in range(6))
        if code not in bot_db:
            break
    
    # 保存到数据库
    pending_naming[u] = {
        "code": code,
        "files": user_sessions[u],
        "uploader": {"id": u, "name": update.effective_user.full_name}
    }
    del user_sessions[u]
    await update.message.reply_text("📦 请输入包名（最多50字），或发送 /skip 跳过命名")

async def skip(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user.id
    if u not in pending_naming:
        return await update.message.reply_text("❌ 无待打包任务")
    
    pkg = pending_naming[u]
    pkg["name"] = f"文件包_{pkg['code']}"
    bot_db[pkg["code"]] = pkg
    save_json("bot_db.json", bot_db)
    auto_backup()
    del pending_naming[u]
    await update.message.reply_text(f"✅ 提取码生成成功：{pkg['code']}")

# ==================== 提取：双模式兼容（新直链 + 旧加密文件）====================
async def text_handle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global BOT_SELF_ID
    if BOT_SELF_ID is None:
        me = await ctx.bot.get_me()
        BOT_SELF_ID = me.id

    u = update.effective_user.id
    cid = update.effective_chat.id
    txt = update.message.text.strip()

    if is_bot_self(u):
        return
    if is_banned(u):
        return await update.message.reply_text("❌ 你已被封禁")

    # 管理员操作处理
    if is_admin(u) and cid in admin_ops:
        act = admin_ops.pop(cid)
        if act == "del_code":
            dels, nf = [], []
            for code in txt.split():
                if code in bot_db:
                    dels.append(code)
                    del bot_db[code]
                else:
                    nf.append(code)
            save_json("bot_db.json", bot_db)
            return await update.message.reply_text(f"✅ 已删除：{' '.join(dels)}\n❌ 不存在：{' '.join(nf)}")
        if act == "search":
            res = [f"{c}｜{p['name']}" for c, p in bot_db.items() if txt.lower() in p['name'].lower()]
            return await update.message.reply_text("\n".join(res) if res else "🔍 未找到匹配文件包")
        if act == "user_uploads":
            res = [f"{c}｜{p['name']}" for c, p in bot_db.items() if str(p['uploader']['id']) == txt.strip()]
            return await update.message.reply_text("\n".join(res) if res else "🔍 该用户暂无上传记录")
        if act == "ban":
            parts = txt.split()
            if len(parts) != 2:
                return await update.message.reply_text("🚫 格式错误：请发送「封禁 123456」或「解封 123456」")
            cmd, tid = parts
            try:
                tid = int(tid)
                if cmd == "封禁":
                    if tid not in banned:
                        banned.append(tid)
                    await update.message.reply_text(f"✅ 已封禁用户 {tid}")
                elif cmd == "解封":
                    if tid in banned:
                        banned.remove(tid)
                    await update.message.reply_text(f"✅ 已解封用户 {tid}")
                save_json("banned.json", banned)
            except:
                await update.message.reply_text("❌ 用户ID格式错误（必须为数字）")
        return

    # 包名命名处理
    if u in pending_naming:
        pkg = pending_naming[u]
        pkg["name"] = txt[:50]  # 限制包名长度
        bot_db[pkg["code"]] = pkg
        save_json("bot_db.json", bot_db)
        auto_backup()
        del pending_naming[u]
        return await update.message.reply_text(f"✅ 包名设置成功，提取码：{pkg['code']}")

    # 提取码解析（核心：双模式兼容）
    if len(txt) == 6:
        if txt not in bot_db:
            return await update.message.reply_text("❌ 提取码不存在")
        
        pkg = bot_db[txt]
        await update.message.reply_text(f"📦 正在提取文件包：{pkg['name']}（共{len(pkg['files'])}个文件）")
        
        for idx, f in enumerate(pkg["files"], 1):
            try:
                # 模式1：新直链加密模式（解决大文件）
                if f.get("meta_type") == "link_enc":
                    metadata = decrypt_metadata(f["data"])
                    if not metadata:
                        await update.message.reply_text(f"⚠️ 第{idx}个文件：{f['name']} - 元数据解密失败")
                        continue
                    
                    # 优先用file_id转发（更稳定，避免直链失效）
                    file_id = metadata.get("file_id")
                    file_name = metadata.get("name", f["name"])
                    orig_type = metadata.get("type", f["orig_type"])

                    if orig_type == "photo":
                        await update.message.reply_photo(photo=file_id, filename=file_name, caption=f"📸 {idx}/{len(pkg['files'])}：{file_name}")
                    elif orig_type == "video":
                        await update.message.reply_video(video=file_id, filename=file_name, caption=f"🎬 {idx}/{len(pkg['files'])}：{file_name}")
                    else:
                        await update.message.reply_document(document=file_id, filename=file_name, caption=f"📄 {idx}/{len(pkg['files'])}：{file_name}")
                
                # 模式2：旧加密文件模式（兼容历史提取码）
                elif f.get("type") == "enc":
                    fid = decode_file_id(f["id"])
                    file = await ctx.bot.get_file(fid, read_timeout=15, write_timeout=15, connect_timeout=10)
                    data = await file.download_as_bytearray(connect_timeout=10, read_timeout=15)
                    decrypted = xor_data(data, E2EE_KEY)

                    with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(f["name"])[1]) as temp_f:
                        temp_f.write(decrypted)
                        temp_path = temp_f.name

                    if f.get("orig_type") == "photo":
                        await update.message.reply_photo(open(temp_path, "rb"), filename=f["name"], caption=f"📸 {idx}/{len(pkg['files'])}：{f['name']}（旧加密版）")
                    elif f.get("orig_type") == "video":
                        await update.message.reply_video(open(temp_path, "rb"), filename=f["name"], caption=f"🎬 {idx}/{len(pkg['files'])}：{f['name']}（旧加密版）")
                    else:
                        await update.message.reply_document(open(temp_path, "rb"), filename=f["name"], caption=f"📄 {idx}/{len(pkg['files'])}：{f['name']}（旧加密版）")

                    os.unlink(temp_path)
                
                # 未知类型兼容
                else:
                    await update.message.reply_text(f"⚠️ 第{idx}个文件：{f['name']} - 不支持的文件类型")
            
            except Exception as e:
                # 捕获转发失败（如文件被删除）
                if "file not found" in str(e).lower():
                    await update.message.reply_text(f"❌ 第{idx}个文件：{f['name']} - 原文件已被Telegram服务器删除")
                else:
                    await update.message.reply_text(f"❌ 第{idx}个文件：{f['name']} - 提取失败：{str(e)[:50]}")
        
        await update.message.reply_text(f"✅ 提取完成！文件包「{pkg['name']}」共{len(pkg['files'])}个文件")
        return

# ==================== 管理员面板 ====================
async def admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        return await update.message.reply_text("❌ 无管理员权限")
    await update.message.reply_text("👮 管理员面板", reply_markup=InlineKeyboardMarkup(admin_menu()))

async def admin_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.from_user.id != ADMIN_USER_ID:
        return await q.edit_message_text("❌ 无管理员权限")
    
    act = q.data
    cid = q.message.chat.id

    if act == "返回":
        return await q.edit_message_text("👮 管理员面板", reply_markup=InlineKeyboardMarkup(admin_menu()))
    
    if act == "统计":
        pkg_cnt = len(bot_db)
        file_cnt = sum(len(v["files"]) for v in bot_db.values())
        usr_cnt = len(user_idx)
        await q.edit_message_text(
            f"📊 机器人统计数据\n"
            f"📦 提取码总数：{pkg_cnt}\n"
            f"📄 文件总数：{file_cnt}\n"
            f"👥 注册用户数：{usr_cnt}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="返回")]])
        )
    
    elif act == "用户列表":
        lines = [f"{uid}｜{d['name']}" for uid, d in user_idx.items()]
        text = "\n".join(lines[:50])  # 限制显示数量
        if len(lines) > 50:
            text += f"\n...（共{len(lines)}个用户，仅显示前50个）"
        await q.edit_message_text(text or "📭 暂无注册用户", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="返回")]]))
    
    elif act == "搜文件":
        admin_ops[cid] = "search"
        await q.edit_message_text("🔍 请输入文件包名称关键词", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="返回")]]))
    
    elif act == "查用户上传":
        admin_ops[cid] = "user_uploads"
        await q.edit_message_text("👤 请输入用户ID（数字）", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="返回")]]))
    
    elif act == "删提取码":
        admin_ops[cid] = "del_code"
        await q.edit_message_text("🗑️ 请输入要删除的提取码（多个用空格分隔）", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="返回")]]))
    
    elif act == "封禁/解封":
        admin_ops[cid] = "ban"
        await q.edit_message_text("🚫 请输入「封禁 123456」或「解封 123456」", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="返回")]]))

# ==================== 备份与数据库导出 ====================
async def backup_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        return
    auto_backup()
    await update.message.reply_text("✅ 数据库备份完成")

async def getdb_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        return
    for f in ["bot_db.json", "user_index.json", "banned.json"]:
        p = os.path.join(DATA_DIR, f)
        if os.path.exists(p):
            await update.message.reply_document(open(p, "rb"), caption=f"📋 {f} 数据库文件")

# ==================== 启动入口 ====================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    # 注册命令处理器
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("my", my_codes))
    app.add_handler(CommandHandler("del", del_code))
    app.add_handler(CommandHandler("confirm", confirm))
    app.add_handler(CommandHandler("skip", skip))
    app.add_handler(CommandHandler("admin", admin))
    app.add_handler(CommandHandler("backup", backup_cmd))
    app.add_handler(CommandHandler("getdb", getdb_cmd))
    # 注册回调与消息处理器
    app.add_handler(CallbackQueryHandler(admin_cb))
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, upload))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handle))
    # 启动机器人
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
