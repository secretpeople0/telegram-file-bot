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
BACKUP_INTERVAL = 30 * 60  # 30分钟自动备份
MAX_BACKUP_COUNT = 2       # 仅保留2个备份

# 内存临时存储（重启丢失，不影响核心数据）
user_sessions = {}    # 存储待打包文件
pending_naming = {}   # 存储待命名的提取码
admin_operations = {} # 存储管理员的临时操作状态

# ====================== 数据目录与持久化（核心：/data 路径） ======================
DATA_DIR = "/data"
BACKUP_DIR = os.path.join(DATA_DIR, "backup")

# 确保目录存在
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

# ====================== 通用 JSON 读写方法（防崩溃） ======================
def load_json(filename):
    path = os.path.join(DATA_DIR, filename)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {} if "db" in filename or "index" in filename else []

def save_json(filename, data):
    path = os.path.join(DATA_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return True

# 初始化持久化数据
bot_db = load_json("bot_db.json")       # 提取码-文件包映射
user_index = load_json("user_index.json") # 用户ID-用户信息映射
banned_users = load_json("banned.json")  # 封禁列表

# ====================== 自动备份功能 ======================
last_backup_time = 0
def auto_backup():
    global last_backup_time
    now = datetime.now().timestamp()
    if now - last_backup_time < BACKUP_INTERVAL:
        return
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    # 备份核心文件
    for fname in ["bot_db.json", "user_index.json", "banned.json"]:
        src = os.path.join(DATA_DIR, fname)
        dst = os.path.join(BACKUP_DIR, f"{fname}.{ts}")
        if os.path.exists(src):
            shutil.copy(src, dst)
    # 清理旧备份
    backups = sorted(
        [os.path.join(BACKUP_DIR, f) for f in os.listdir(BACKUP_DIR)],
        key=os.path.getmtime,
        reverse=True
    )
    for old_file in backups[MAX_BACKUP_COUNT:]:
        os.remove(old_file)
    last_backup_time = now

# ====================== 权限与用户追踪 ======================
async def track_user(user_id: int, full_name: str, username: str):
    """追踪用户，更新用户索引"""
    uid = str(user_id)
    if uid not in user_index or user_index[uid]["name"] != full_name:
        user_index[uid] = {
            "name": full_name,
            "username": username or "无"
        }
        save_json("user_index.json", user_index)

def is_banned(user_id: int) -> bool:
    """检查是否被封禁"""
    return user_id in banned_users

# ====================== 管理员核心功能（含用户管理） ======================
async def admin_panel(update: Update, _):
    """管理员面板（恢复完整6个功能）"""
    user_id = update.effective_user.id
    if user_id != ADMIN_USER_ID:
        await update.message.reply_text("❌ 无管理员权限")
        return
    # 恢复你要的所有按钮，包括查看用户列表和用户上传
    keyboard = [
        [InlineKeyboardButton("📊 统计总数", callback_data="stats")],
        [InlineKeyboardButton("👥 查看用户列表", callback_data="list_users")],
        [InlineKeyboardButton("🔍 搜索文件", callback_data="search")],
        [InlineKeyboardButton("👁️ 查看用户上传", callback_data="view_user_upload")],
        [InlineKeyboardButton("🗑️ 删除提取码", callback_data="delete_code")],
        [InlineKeyboardButton("🚫 封禁/解封", callback_data="ban_user")],
    ]
    await update.message.reply_text(
        "👮 管理员控制面板\n请选择操作：",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """管理员回调处理（核心修复：无 Bug 逻辑）"""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if user_id != ADMIN_USER_ID:
        await query.edit_message_text("❌ 权限不足")
        return

    action = query.data
    chat_id = query.message.chat.id

    # 根据按钮动作设置管理员操作状态
    if action == "stats":
        # 直接执行，无需等待输入
        total_pkgs = len(bot_db)
        total_files = sum(len(pkg["files"]) for pkg in bot_db.values())
        total_users = len(user_index)
        await query.edit_message_text(
            f"📊 机器人运行统计\n"
            f"总用户数：{total_users} 人\n"
            f"文件包总数：{total_pkgs} 个\n"
            f"文件总数：{total_files} 个"
        )

    elif action == "list_users":
        # 查看用户列表（直接展示，前50名）
        if not user_index:
            await query.edit_message_text("❌ 暂无用户记录")
            return
        msg = "👥 <b>所有用户列表</b> (ID | 昵称 | 用户名)\n\n"
        count = 0
        for uid, info in user_index.items():
            if count >= 50:
                msg += f"\n... 还有 {len(user_index) - 50} 个用户未显示"
                break
            msg += f"ID: <code>{uid}</code> | 昵称: {info['name']} | 账号: @{info['username']}\n"
            count += 1
        await query.edit_message_text(msg, parse_mode="HTML")

    elif action == "search":
        # 搜索文件：等待输入关键词
        await query.edit_message_text("🔍 请回复此消息发送搜索关键词（支持文件名/格式）：")
        admin_operations[chat_id] = "search"

    elif action == "view_user_upload":
        # 查看用户上传：等待输入用户ID
        await query.edit_message_text("👤 请回复此消息发送要查询的 <b>用户ID</b>：", parse_mode="HTML")
        admin_operations[chat_id] = "view_user_upload"

    elif action == "delete_code":
        # 删除提取码：等待输入提取码
        await query.edit_message_text("🗑️ 请回复此消息发送要删除的 <b>6位提取码</b>：", parse_mode="HTML")
        admin_operations[chat_id] = "delete_code"

    elif action == "ban_user":
        # 封禁/解封：等待输入指令
        await query.edit_message_text(
            "🚫 请回复此消息发送指令：\n"
            "格式1：封禁 [用户ID]\n"
            "格式2：解封 [用户ID]",
            parse_mode="HTML"
        )
        admin_operations[chat_id] = "ban_user"

async def handle_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理管理员的后续输入（与用户功能完全隔离，无冲突）"""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    text = update.message.text.strip()

    # 非管理员或无待处理操作，直接退出
    if user_id != ADMIN_USER_ID or chat_id not in admin_operations:
        return

    action = admin_operations[chat_id]
    try:
        if action == "search":
            # 搜索文件功能
            results = []
            keyword = text.lower()
            for code, pkg in bot_db.items():
                if keyword in pkg["name"].lower() or any(keyword in f["name"].lower() for f in pkg["files"]):
                    results.append(f"🔑 <code>{code}</code> | {pkg['name']}")
            if results:
                await update.message.reply_text("\n".join(results), parse_mode="HTML")
            else:
                await update.message.reply_text("❌ 未找到匹配结果")

        elif action == "view_user_upload":
            # 查看指定用户上传（你要的核心功能）
            target_uid = text.strip()
            # 检查该用户是否存在
            if target_uid not in user_index:
                await update.message.reply_text(f"❌ 用户ID <code>{target_uid}</code> 不存在", parse_mode="HTML")
                return
            # 查找该用户上传的所有文件包
            user_uploads = []
            for code, pkg in bot_db.items():
                if str(pkg["uploader"]["id"]) == target_uid:
                    user_uploads.append(f"🔑 <code>{code}</code> | {pkg['name']}")
            # 发送结果
            if user_uploads:
                await update.message.reply_text(
                    f"👤 <b>用户 {target_uid} ({user_index[target_uid]['name']}) 的上传记录</b>：\n\n" +
                    "\n".join(user_uploads),
                    parse_mode="HTML"
                )
            else:
                await update.message.reply_text(
                    f"👤 用户 {target_uid} 暂无上传记录",
                    parse_mode="HTML"
                )

        elif action == "delete_code":
            # 删除提取码
            if len(text) == 6 and text.isdigit() and text in bot_db:
                del bot_db[text]
                save_json("bot_db.json", bot_db)
                await update.message.reply_text(f"✅ 提取码 <code>{text}</code> 已永久删除", parse_mode="HTML")
            else:
                await update.message.reply_text("❌ 提取码不存在或格式错误")

        elif action == "ban_user":
            # 封禁与解封
            parts = text.split()
            if len(parts) != 2:
                await update.message.reply_text("❌ 格式错误！请输入：封禁 123456 或 解封 123456")
                return
            cmd, tid_str = parts
            try:
                target_id = int(tid_str)
                if cmd == "封禁":
                    if target_id not in banned_users:
                        banned_users.append(target_id)
                        save_json("banned.json", banned_users)
                        await update.message.reply_text(f"✅ 已封禁用户 <code>{target_id}</code>", parse_mode="HTML")
                    else:
                        await update.message.reply_text(f"❌ 用户 <code>{target_id}</code> 已被封禁", parse_mode="HTML")
                elif cmd == "解封":
                    if target_id in banned_users:
                        banned_users.remove(target_id)
                        save_json("banned.json", banned_users)
                        await update.message.reply_text(f"✅ 已解封用户 <code>{target_id}</code>", parse_mode="HTML")
                    else:
                        await update.message.reply_text(f"❌ 用户 <code>{target_id}</code> 未被封禁", parse_mode="HTML")
                else:
                    await update.message.reply_text("❌ 指令错误，仅支持「封禁」或「解封」")
            except ValueError:
                await update.message.reply_text("❌ 用户ID必须是数字")
    finally:
        # 无论操作成功与否，执行完后清除操作状态
        del admin_operations[chat_id]

# ====================== 普通用户核心功能（无任何改动，保持稳定） ======================
async def start(update: Update, _):
    await update.message.reply_text(
        "👋 永久文件存储机器人（稳定版）\n\n"
        "📝 使用方法：\n"
        "1. 发送图片/视频/文档\n"
        "2. 发送 /confirm 打包\n"
        "3. 输入名称（或 /skip 跳过）\n"
        "4. 发送提取码取回文件"
    )

async def upload_file(update: Update, _):
    user = update.effective_user
    if is_banned(user.id):
        await update.message.reply_text("❌ 你已被封禁，无法使用本机器人")
        return
    if user.id not in user_sessions:
        user_sessions[user.id] = []
    msg = update.message
    file_data = None
    if msg.photo:
        file_data = {"type": "img", "id": msg.photo[-1].file_id, "name": f"图片_{datetime.now().strftime('%H%M%S')}.jpg"}
    elif msg.video:
        file_data = {"type": "video", "id": msg.video.file_id, "name": msg.video.file_name or f"视频_{datetime.now().strftime('%H%M%S')}.mp4"}
    elif msg.document:
        file_data = {"type": "doc", "id": msg.document.file_id, "name": msg.document.file_name or "未知文件"}
    else:
        await msg.reply_text("❌ 仅支持图片、视频、文档")
        return
    user_sessions[user.id].append(file_data)
    await msg.reply_text(f"✅ 已接收！当前待打包 {len(user_sessions[user.id])} 个文件\n💡 发送 /confirm 开始打包")
    await track_user(user.id, user.full_name, user.username)

async def confirm_package(update: Update, _):
    user = update.effective_user
    if is_banned(user.id):
        await update.message.reply_text("❌ 你已被封禁")
        return
    if user.id not in user_sessions or not user_sessions[user.id]:
        await update.message.reply_text("❌ 暂无待打包文件，请先上传内容")
        return
    # 生成唯一提取码
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
    del user_sessions[user.id]
    await update.message.reply_text("📦 打包成功！请输入文件包名称（或发送 /skip 跳过）")

async def skip_naming(update: Update, _):
    user = update.effective_user
    if user.id not in pending_naming:
        await update.message.reply_text("❌ 暂无待命名的文件包")
        return
    pkg = pending_naming[user.id]
    pkg["name"] = f"文件包_{pkg['code']}"
    bot_db[pkg["code"]] = pkg
    save_json("bot_db.json", bot_db)
    auto_backup()
    del pending_naming[user.id]
    await update.message.reply_text(f"✅ 命名成功！提取码：<code>{pkg['code']}</code>", parse_mode="HTML")

async def user_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    普通用户文本处理核心（严格优先级）
    1. 是管理员且有操作任务 → 交给管理员处理
    2. 是待命名状态 → 执行命名
    3. 是6位数字 → 执行取件
    4. 其他 → 忽略（避免干扰）
    """
    user = update.effective_user
    chat_id = update.effective_chat.id
    text = update.message.text.strip()

    # 优先级1：管理员操作（最高级，避免被命名或取件逻辑拦截）
    if user.id == ADMIN_USER_ID and chat_id in admin_operations:
        await handle_admin_input(update, context)
        return

    # 优先级2：检查封禁
    if is_banned(user.id):
        await update.message.reply_text("❌ 你已被封禁")
        return

    # 优先级3：处理命名（待命名状态）
    if user.id in pending_naming:
        if len(text) > 50:
            await update.message.reply_text("❌ 名称过长（最多50字），请重新输入")
            return
        pkg = pending_naming[user.id]
        pkg["name"] = text
        bot_db[pkg["code"]] = pkg
        save_json("bot_db.json", bot_db)
        auto_backup()
        del pending_naming[user.id]
        await update.message.reply_text(f"✅ 命名成功！提取码：<code>{pkg['code']}</code>", parse_mode="HTML")
        return

    # 优先级4：处理提取码（6位数字）
    if len(text) == 6 and text.isdigit():
        if text not in bot_db:
            await update.message.reply_text("❌ 提取码不存在，请检查输入")
            return
        pkg = bot_db[text]
        await update.message.reply_text(f"📦 正在取回：{pkg['name']}（共{len(pkg['files'])}个文件）")
        for f in pkg["files"]:
            try:
                if f["type"] == "img":
                    await update.message.reply_photo(f["id"], caption=f["name"])
                elif f["type"] == "video":
                    await update.message.reply_video(f["id"], caption=f["name"])
                elif f["type"] == "doc":
                    await update.message.reply_document(f["id"], filename=f["name"])
            except Exception as e:
                await update.message.reply_text(f"⚠️ 发送失败：{f['name']}（可能已过期）")
        return

    # 其他文本消息直接忽略，不做回复，避免刷屏

# ====================== 管理员工具命令（备份/下载数据库） ======================
async def manual_backup(update: Update, _):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("❌ 无权限")
        return
    auto_backup()
    await update.message.reply_text("✅ 手动备份完成！")

async def send_db(update: Update, _):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("❌ 无权限")
        return
    for fname in ["bot_db.json", "user_index.json", "banned.json"]:
        path = os.path.join(DATA_DIR, fname)
        if os.path.exists(path):
            await update.message.reply_document(open(path, "rb"), caption=fname)

# ====================== 机器人启动（处理器顺序严格固定） ======================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # 1. 命令处理器（最高优先级）
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("confirm", confirm_package))
    app.add_handler(CommandHandler("skip", skip_naming))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("backup", manual_backup))
    app.add_handler(CommandHandler("getdb", send_db))

    # 2. 回调处理器（管理员按钮）
    app.add_handler(CallbackQueryHandler(admin_callback_handler))

    # 3. 文件接收处理器（图片/视频/文档）
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, upload_file))

    # 4. 文本处理器（最低优先级：处理命名、取件、管理员输入）
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, user_text_handler))

    # 启动
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
