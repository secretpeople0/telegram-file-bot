import os
import json
import random
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

# ====================== 配置区 ======================
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ADMIN_USER_ID = int(os.environ.get("ADMIN_USER_ID", "0"))

# 临时会话存储
user_sessions = {}
pending_naming = {}

# ====================== 永久存储核心 ======================
def get_db():
    try:
        return json.loads(os.environ.get("BOT_DB", "{}"))
    except:
        return {}

def save_db(db_data):
    os.environ["BOT_DB"] = json.dumps(db_data, ensure_ascii=False, indent=2)

def get_banned():
    try:
        return json.loads(os.environ.get("BOT_BANNED", "[]"))
    except:
        return []

def save_banned(banned_list):
    os.environ["BOT_BANNED"] = json.dumps(banned_list, ensure_ascii=False)

def get_user_index():
    try:
        return json.loads(os.environ.get("USER_INDEX", "{}"))
    except:
        return {}

def save_user_index(index_data):
    os.environ["USER_INDEX"] = json.dumps(index_data, ensure_ascii=False, indent=2)

banned_users = get_banned()

# ====================== 用户记录 ======================
async def track_user(update: Update):
    user = update.effective_user
    if not user:
        return
    user_id = str(user.id)
    index = get_user_index()
    if user_id not in index or index[user_id]["name"] != user.full_name:
        index[user_id] = {
            "name": user.full_name,
            "username": user.username or "无"
        }
        save_user_index(index)

# ====================== 封禁检查 ======================
def check_ban(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await track_user(update)
        user_id = update.effective_user.id
        if user_id in banned_users:
            await update.message.reply_text("❌ 你已被封禁，无法使用本机器人。")
            return
        return await func(update, context)
    return wrapper

# ====================== 管理员 ======================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_USER_ID:
        await update.message.reply_text("❌ 你没有管理员权限")
        return
    keyboard = [
        [InlineKeyboardButton("📊 统计总数", callback_data="stats")],
        [InlineKeyboardButton("👥 查看用户列表", callback_data="list_users")],
        [InlineKeyboardButton("🔍 搜索文件", callback_data="search")],
        [InlineKeyboardButton("👁️ 查看用户上传", callback_data="view_user_upload")],
        [InlineKeyboardButton("🗑️ 删除提取码", callback_data="delete")],
        [InlineKeyboardButton("🚫 封禁/解封", callback_data="ban")],
    ]
    await update.message.reply_text("👮 管理员控制面板", reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if user_id != ADMIN_USER_ID:
        await query.edit_message_text("❌ 权限不足")
        return
    db = get_db()
    user_index = get_user_index()
    if query.data == "stats":
        total_packages = len(db)
        total_files = sum(len(pkg["files"]) for pkg in db.values())
        total_users = len(user_index)
        await query.edit_message_text(
            f"📊 机器人运行统计\n"
            f"总用户数：{total_users} 人\n"
            f"文件包总数：{total_packages} 个\n"
            f"文件总数：{total_files} 个"
        )
    elif query.data == "list_users":
        if not user_index:
            await query.edit_message_text("❌ 暂无用户记录")
            return
        msg = "👥 所有使用过的用户列表 (ID | 昵称)\n\n"
        for uid, info in list(user_index.items())[:30]:
            msg += f"ID: `{uid}` | 昵称: {info['name']}\n"
        if len(user_index) > 30:
            msg += f"\n... 还有 {len(user_index) - 30} 个用户未显示"
        await query.edit_message_text(msg, parse_mode="Markdown")
    elif query.data == "search":
        await query.edit_message_text("🔍 请发送搜索关键词（支持实时匹配包名和文件名）：")
        context.user_data["admin_act"] = "search"
    elif query.data == "view_user_upload":
        await query.edit_message_text("👤 请发送用户ID，查看该用户所有上传的文件包：")
        context.user_data["admin_act"] = "view_user_upload"
    elif query.data == "delete":
        await query.edit_message_text("🗑️ 请发送要删除的6位提取码：")
        context.user_data["admin_act"] = "delete"
    elif query.data == "ban":
        await query.edit_message_text("🚫 请发送用户ID，或输入 /unban [用户ID] 进行解封：")
        context.user_data["admin_act"] = "ban"

async def handle_admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "admin_act" not in context.user_data:
        return
    user_id = update.effective_user.id
    if user_id != ADMIN_USER_ID:
        return
    action = context.user_data["admin_act"]
    text = update.message.text.strip()
    db = get_db()
    if action == "search":
        keyword = text.lower()
        results = []
        for code, pkg in db.items():
            match = False
            if keyword in pkg.get("name", "").lower():
                match = True
            else:
                for file in pkg["files"]:
                    if keyword in file["name"].lower():
                        match = True
                        break
            if match:
                result_line = f"🔑 提取码：`{code}`\n📦 包名：{pkg.get('name', '未命名')}\n👤 上传者ID：`{pkg['uploader']['id']}`"
                if result_line not in results:
                    results.append(result_line)
        if results:
            await update.message.reply_text(f"✅ 找到 {len(results)} 个匹配结果：\n\n" + "\n\n".join(results), parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ 未找到包含该关键词的文件")
    elif action == "view_user_upload":
        target_id = text.strip()
        results = []
        for code, pkg in db.items():
            if str(pkg["uploader"]["id"]) == target_id:
                results.append(f"🔑 `{code}` | {pkg.get('name', '未命名')}")
        if results:
            await update.message.reply_text(f"👤 用户 {target_id} 上传过：\n\n" + "\n".join(results), parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ 该用户无上传记录")
    elif action == "delete":
        if text in db:
            del db[text]
            save_db(db)
            await update.message.reply_text(f"✅ 提取码 `{text}` 已永久删除", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ 提取码不存在")
    elif action == "ban":
        try:
            if text.startswith("/unban"):
                target_id = int(text.split()[1])
                if target_id in banned_users:
                    banned_users.remove(target_id)
                    save_banned(banned_users)
                    await update.message.reply_text(f"✅ 已解封用户 ID: `{target_id}`", parse_mode="Markdown")
                else:
                    await update.message.reply_text("❌ 该用户未被封禁")
            else:
                target_id = int(text)
                if target_id not in banned_users:
                    banned_users.append(target_id)
                    save_banned(banned_users)
                    await update.message.reply_text(f"✅ 已封禁用户 ID: `{target_id}`", parse_mode="Markdown")
                else:
                    await update.message.reply_text("❌ 该用户已被封禁")
        except:
            await update.message.reply_text("❌ 格式错误，请输入纯数字ID，或 /unban 数字ID")
    del context.user_data["admin_act"]

# ====================== 用户功能 ======================
@check_ban
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 欢迎使用永久文件存储机器人！\n\n"
        "📝 使用指南：\n"
        "1. 直接发送图片、视频、文档\n"
        "2. 发送 /confirm 确认打包\n"
        "3. 输入名称（或 /skip 跳过）\n"
        "4. 获得提取码，凭码取件"
    )

@check_ban
async def upload_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    message = update.message
    if user_id not in user_sessions:
        user_sessions[user_id] = []
    file_info = None
    if message.document:
        file_info = {
            "type": "doc",
            "id": message.document.file_id,
            "name": message.document.file_name or "未命名文件"
        }
    elif message.photo:
        file_info = {
            "type": "img",
            "id": message.photo[-1].file_id,
            "name": f"图片_{datetime.now().strftime('%H%M%S')}.jpg"
        }
    elif message.video:
        file_info = {
            "type": "video",
            "id": message.video.file_id,
            "name": message.video.file_name or f"视频_{datetime.now().strftime('%H%M%S')}.mp4"
        }
    else:
        await message.reply_text("❌ 暂不支持此类型文件，仅支持图片、视频、文档")
        return
    user_sessions[user_id].append(file_info)
    await message.reply_text(f"✅ 已接收！当前共收集 {len(user_sessions[user_id])} 个文件。\n输入 /confirm 完成打包。")

@check_ban
async def confirm_package(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_sessions or not user_sessions[user_id]:
        await update.message.reply_text("❌ 你还没有上传任何文件！")
        return
    db = get_db()
    while True:
        code = str(random.randint(100000, 999999))
        if code not in db:
            break
    pending_naming[user_id] = {
        "code": code,
        "files": user_sessions[user_id],
        "uploader": {
            "id": user_id,
            "name": update.effective_user.full_name
        },
    }
    await update.message.reply_text("📝 请为这个文件包输入一个名称（方便记忆）：\n或发送 /skip 跳过命名")

@check_ban
async def skip_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in pending_naming:
        await update.message.reply_text("❌ 没有待命名的文件包")
        return
    pkg = pending_naming[user_id]
    pkg["name"] = f"文件包_{pkg['code']}"
    db = get_db()
    db[pkg["code"]] = pkg
    save_db(db)
    del user_sessions[user_id]
    del pending_naming[user_id]
    await update.message.reply_text(
        f"✅ 打包完成！\n\n"
        f"📦 包名：{pkg['name']}\n"
        f"🔑 提取码：`{pkg['code']}`\n\n"
        f"使用方法：发送提取码给我即可取件",
        parse_mode="Markdown"
    )

@check_ban
async def set_package_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in pending_naming:
        return
    name = update.message.text.strip()
    if len(name) > 50:
        await update.message.reply_text("❌ 名称太长，请控制在50字以内")
        return
    pkg = pending_naming[user_id]
    pkg["name"] = name
    db = get_db()
    db[pkg["code"]] = pkg
    save_db(db)
    del user_sessions[user_id]
    del pending_naming[user_id]
    await update.message.reply_text(
        f"✅ 打包完成！\n\n"
        f"📦 包名：{pkg['name']}\n"
        f"🔑 提取码：`{pkg['code']}`\n\n"
        f"使用方法：发送提取码给我即可取件",
        parse_mode="Markdown"
    )

@check_ban
async def fetch_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    db = get_db()
    if len(text) == 6 and text.isdigit() and text in db:
        pkg = db[text]
        await update.message.reply_text(f"📦 正在为你取出文件包：{pkg.get('name', '未命名')}")
        for f in pkg["files"]:
            try:
                if f["type"] == "img":
                    await update.message.reply_photo(photo=f["id"], caption=f["name"])
                elif f["type"] == "video":
                    await update.message.reply_video(video=f["id"], caption=f["name"])
                elif f["type"] == "doc":
                    await update.message.reply_document(document=f["id"], filename=f["name"])
            except Exception as e:
                await update.message.reply_text(f"⚠️ 文件 {f['name']} 可能已过期或被TG删除")
        return

# ====================== 启动（修复顺序） ======================
def main():
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # 管理员
    application.add_handler(CommandHandler("admin", admin_panel), 1)
    application.add_handler(CallbackQueryHandler(admin_callback), 2)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_action), 3)

    # 用户命令
    application.add_handler(CommandHandler("start", start), 10)
    application.add_handler(CommandHandler("confirm", confirm_package), 11)
    application.add_handler(CommandHandler("skip", skip_name), 12)

    # 文件上传
    application.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL | filters.VIDEO, upload_file), 20)

    # 命名逻辑（优先级高于取码）
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, set_package_name), 30)

    # 取码逻辑（最低优先级）
    application.add_handler(MessageHandler(filters.TEXT, fetch_file), 999)

    application.run_polling()

if __name__ == "__main__":
    main()
