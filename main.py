import os
import json
import random
import shutil
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

# ====================== 核心配置 ======================
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ADMIN_USER_ID = int(os.environ.get("ADMIN_USER_ID", "0"))
BACKUP_INTERVAL = 30 * 60
MAX_BACKUP_COUNT = 2

# 全局存储（仅内存，重启丢失，不影响核心数据）
user_sessions = {}  # 存储待打包文件
pending_naming = {} # 存储待命名的提取码
banned_users = []   # 封禁列表

# ====================== 数据目录初始化（永久存储） ======================
DATA_DIR = "/data"
BACKUP_DIR = os.path.join(DATA_DIR, "backup")

# 确保目录存在，防止报错
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

# ====================== 数据库核心方法（修复读写逻辑） ======================
def load_json(filename):
    """通用加载JSON方法，防止文件不存在报错"""
    path = os.path.join(DATA_DIR, filename)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        # 文件不存在或损坏，返回空数据
        return {} if "index" in filename or "db" in filename else []

def save_json(filename, data):
    """通用保存JSON方法，确保写入持久化目录"""
    path = os.path.join(DATA_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return True

# 初始化核心数据
bot_db = load_json("bot_db.json")
user_index = load_json("user_index.json")
banned_users = load_json("banned.json")

# ====================== 备份功能（保持不变） ======================
last_backup = 0
def auto_backup():
    global last_backup
    now = datetime.now().timestamp()
    if now - last_backup < BACKUP_INTERVAL:
        return
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    for f in ["bot_db.json", "user_index.json", "banned.json"]:
        src = os.path.join(DATA_DIR, f)
        dst = os.path.join(BACKUP_DIR, f"{f}.{ts}")
        if os.path.exists(src):
            shutil.copy(src, dst)
    # 清理旧备份
    backups = sorted([os.path.join(BACKUP_DIR, fn) for fn in os.listdir(BACKUP_DIR)], key=os.path.getmtime, reverse=True)
    for old in backups[MAX_BACKUP_COUNT:]:
        os.remove(old)
    last_backup = now

# ====================== 权限与统计（修复语法错误） ======================
async def track_user(user_id: int, full_name: str, username: str):
    """用户行为追踪"""
    uid = str(user_id)
    if uid not in user_index or user_index[uid]["name"] != full_name:
        user_index[uid] = {"name": full_name, "username": username or "无"}
        save_json("user_index.json", user_index)

def is_banned(user_id: int) -> bool:
    """检查是否被封禁"""
    return user_id in banned_users

# ====================== 管理员命令（修复回调查询逻辑） ======================
async def admin(update: Update, _):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("❌ 无管理员权限")
        return
    kb = [
        [InlineKeyboardButton("📊 统计", callback_data="stats")],
        [InlineKeyboardButton("🔍 搜索", callback_data="search")],
        [InlineKeyboardButton("🗑️ 删除提取码", callback_data="del_code")],
        [InlineKeyboardButton("🚫 封禁管理", callback_data="ban_manage")]
    ]
    await update.message.reply_text("👮 管理员面板", reply_markup=InlineKeyboardMarkup(kb))

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_USER_ID:
        await query.edit_message_text("❌ 权限不足")
        return
    data = query.data
    if data == "stats":
        total = len(bot_db)
        files = sum(len(pkg["files"]) for pkg in bot_db.values())
        users = len(user_index)
        await query.edit_message_text(f"📊 统计：\n用户数：{users}\n文件包：{total}\n总文件：{files}")
    elif data == "search":
        await query.edit_message_text("🔍 请发送搜索关键词（支持文件名/格式）：")
        context.user_data["admin_task"] = "search"
    elif data == "del_code":
        await query.edit_message_text("🗑️ 请发送要删除的6位提取码：")
        context.user_data["admin_task"] = "del_code"
    elif data == "ban_manage":
        await query.edit_message_text("🚫 请发送：封禁[用户ID] 或 解封[用户ID]（例：封禁123456）")
        context.user_data["admin_task"] = "ban_manage"

# ====================== 核心业务逻辑（修复优先级冲突） ======================
async def start(update: Update, _):
    """启动命令"""
    await update.message.reply_text(
        "👋 永久存储机器人（修复版）\n"
        "1. 发送图片/视频/文档\n"
        "2. 发送 /confirm 打包\n"
        "3. 输入名称（或 /skip 跳过）\n"
        "4. 发送提取码取回文件"
    )

async def upload(update: Update, _):
    """接收文件（图片/视频/文档）"""
    user = update.effective_user
    if is_banned(user.id):
        await update.message.reply_text("❌ 你已被封禁")
        return
    # 初始化用户会话
    if user.id not in user_sessions:
        user_sessions[user.id] = []
    # 识别文件类型
    msg = update.message
    file_data = None
    if msg.photo:
        # 取最高清的一张
        photo = msg.photo[-1]
        file_data = {"type": "img", "id": photo.file_id, "name": f"图片_{datetime.now().strftime('%H%M%S')}.jpg"}
    elif msg.video:
        file_data = {"type": "video", "id": msg.video.file_id, "name": msg.video.file_name or f"视频_{datetime.now().strftime('%H%M%S')}.mp4"}
    elif msg.document:
        file_data = {"type": "doc", "id": msg.document.file_id, "name": msg.document.file_name or "未知文件"}
    else:
        await msg.reply_text("❌ 仅支持图片、视频、文档")
        return
    # 加入会话
    user_sessions[user.id].append(file_data)
    await msg.reply_text(f"✅ 已接收！当前待打包：{len(user_sessions[user.id])} 个\n💡 发送 /confirm 开始打包")
    # 追踪用户
    await track_user(user.id, user.full_name, user.username)

async def confirm(update: Update, _):
    """打包文件，生成提取码"""
    user = update.effective_user
    if is_banned(user.id):
        await update.message.reply_text("❌ 你已被封禁")
        return
    # 检查是否有文件
    if user.id not in user_sessions or not user_sessions[user.id]:
        await update.message.reply_text("❌ 暂无待打包文件，请先上传图片/视频/文档")
        return
    # 生成唯一6位提取码
    while True:
        code = str(random.randint(100000, 999999))
        if code not in bot_db:
            break
    # 存入待命名队列
    pending_naming[user.id] = {
        "code": code,
        "files": user_sessions[user.id],
        "uploader": {"id": user.id, "name": user.full_name}
    }
    # 清空临时会话
    del user_sessions[user.id]
    await update.message.reply_text(f"📦 已为你打包！\n💡 请输入文件包名称（或发送 /skip 跳过命名）")

async def skip_name(update: Update, _):
    """跳过命名"""
    user = update.effective_user
    if user.id not in pending_naming:
        await update.message.reply_text("❌ 暂无待命名的文件包")
        return
    # 补全默认名称
    pkg = pending_naming[user.id]
    pkg["name"] = f"文件包_{pkg['code']}"
    # 存入数据库
    bot_db[pkg["code"]] = pkg
    save_json("bot_db.json", bot_db)
    auto_backup()
    # 清空待命名队列
    del pending_naming[user.id]
    await update.message.reply_text(f"✅ 命名成功！\n你的提取码：`{pkg['code']}`\n⚠️ 请妥善保存，永久有效", parse_mode="Markdown")

async def fetch_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """提取文件（核心修复：优先处理提取码）"""
    user = update.effective_user
    if is_banned(user.id):
        await update.message.reply_text("❌ 你已被封禁")
        return
    # 优先处理管理员任务（避免冲突）
    if "admin_task" in context.user_data:
        await handle_admin_task(update, context)
        return
    # 处理命名（待命名状态下，文本优先作为名称）
    if user.id in pending_naming:
        name = update.message.text.strip()
        if len(name) > 50:
            await update.message.reply_text("❌ 名称过长（最多50字），请重新输入")
            return
        pkg = pending_naming[user.id]
        pkg["name"] = name
        bot_db[pkg["code"]] = pkg
        save_json("bot_db.json", bot_db)
        auto_backup()
        del pending_naming[user.id]
        await update.message.reply_text(f"✅ 命名成功！\n你的提取码：`{pkg['code']}`", parse_mode="Markdown")
        return
    # 最后处理提取码
    code = update.message.text.strip()
    if len(code) != 6 or not code.isdigit():
        # 不是提取码，直接忽略，避免干扰
        return
    if code not in bot_db:
        await update.message.reply_text("❌ 提取码不存在，请检查是否输入错误")
        return
    # 发送文件
    pkg = bot_db[code]
    await update.message.reply_text(f"📦 正在为你取回：{pkg['name']}（共{len(pkg['files'])}个文件）")
    for f in pkg["files"]:
        try:
            if f["type"] == "img":
                await update.message.reply_photo(f["id"], caption=f["name"])
            elif f["type"] == "video":
                await update.message.reply_video(f["id"], caption=f["name"])
            elif f["type"] == "doc":
                await update.message.reply_document(f["id"], filename=f["name"])
        except Exception as e:
            await update.message.reply_text(f"⚠️ 发送失败：{f['name']}（可能已过期或被清理）")

async def handle_admin_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理管理员任务"""
    if update.effective_user.id != ADMIN_USER_ID:
        del context.user_data["admin_task"]
        return
    task = context.user_data["admin_task"]
    text = update.message.text.strip()
    if task == "search":
        results = []
        for code, pkg in bot_db.items():
            if text.lower() in pkg["name"].lower() or any(text.lower() in f["name"].lower() for f in pkg["files"]):
                results.append(f"🔑 `{code}` - {pkg['name']}")
        await update.message.reply_text("\n".join(results) if results else "❌ 无搜索结果", parse_mode="Markdown")
    elif task == "del_code":
        if text in bot_db:
            del bot_db[text]
            save_json("bot_db.json", bot_db)
            await update.message.reply_text(f"✅ 提取码 `{text}` 已删除", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ 提取码不存在")
    elif task == "ban_manage":
        if text.startswith("封禁"):
            uid = int(text[2:])
            if uid not in banned_users:
                banned_users.append(uid)
                save_json("banned.json", banned_users)
                await update.message.reply_text(f"✅ 已封禁用户 {uid}")
            else:
                await update.message.reply_text(f"❌ 用户 {uid} 已被封禁")
        elif text.startswith("解封"):
            uid = int(text[2:])
            if uid in banned_users:
                banned_users.remove(uid)
                save_json("banned.json", banned_users)
                await update.message.reply_text(f"✅ 已解封用户 {uid}")
            else:
                await update.message.reply_text(f"❌ 用户 {uid} 未被封禁")
        else:
            await update.message.reply_text("❌ 格式错误，请发送：封禁123456 或 解封123456")
    # 清除任务
    del context.user_data["admin_task"]

# ====================== 管理员备份命令 ======================
async def backup(_, update: Update):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("❌ 无权限")
        return
    auto_backup()
    await update.message.reply_text("✅ 手动备份完成！")

async def getdb(_, update: Update):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("❌ 无权限")
        return
    for f in ["bot_db.json", "user_index.json", "banned.json"]:
        path = os.path.join(DATA_DIR, f)
        if os.path.exists(path):
            await update.message.reply_document(open(path, "rb"))

# ====================== 启动机器人（修复处理器顺序） ======================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    # 1. 命令处理器（最高优先级）
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("confirm", confirm))
    app.add_handler(CommandHandler("skip", skip_name))
    app.add_handler(CommandHandler("admin", admin))
    app.add_handler(CommandHandler("backup", backup))
    app.add_handler(CommandHandler("getdb", getdb))
    # 2. 回调查询处理器（管理员面板）
    app.add_handler(CallbackQueryHandler(admin_callback))
    # 3. 文件接收处理器（图片/视频/文档）
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, upload))
    # 4. 文本处理器（最低优先级：处理命名、提取码、管理员任务）
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, fetch_file))
    # 启动
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
