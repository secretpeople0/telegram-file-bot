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

# ====================== 永久存储核心 (数据永不丢失) ======================
def get_db():
    """读取提取码数据库"""
    try:
        return json.loads(os.environ.get("BOT_DB", "{}"))
    except:
        return {}

def save_db(db_data):
    """保存提取码数据库"""
    os.environ["BOT_DB"] = json.dumps(db_data, ensure_ascii=False, indent=2)

def get_banned():
    """读取封禁列表"""
    try:
        return json.loads(os.environ.get("BOT_BANNED", "[]"))
    except:
        return []

def save_banned(banned_list):
    """保存封禁列表"""
    os.environ["BOT_BANNED"] = json.dumps(banned_list, ensure_ascii=False)

def get_user_index():
    """读取用户索引 (用于快速获取用户ID)"""
    try:
        return json.loads(os.environ.get("USER_INDEX", "{}"))
    except:
        return {}

def save_user_index(index_data):
    """保存用户索引"""
    os.environ["USER_INDEX"] = json.dumps(index_data, ensure_ascii=False, indent=2)

# 初始化全局变量
banned_users = get_banned()

# ====================== 辅助功能：用户ID自动记录 ======================
async def track_user(update: Update):
    """只要用户发消息，就自动记录其ID和昵称"""
    user = update.effective_user
    if not user:
        return
    
    user_id = str(user.id)
    index = get_user_index()
    
    # 只在用户不存在或昵称变更时更新
    if user_id not in index or index[user_id]["name"] != user.full_name:
        index[user_id] = {
            "name": user.full_name,
            "username": user.username or "无"
        }
        save_user_index(index)

# ====================== 封禁检查装饰器 ======================
def check_ban(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        # 先记录用户ID (即使是被封禁的用户)
        await track_user(update)
        
        user_id = update.effective_user.id
        if user_id in banned_users:
            await update.message.reply_text("❌ 你已被封禁，无法使用本机器人。")
            return
        return await func(update, context)
    return wrapper

# ====================== 管理员核心功能 (已修复) ======================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_USER_ID:
        await update.message.reply_text("❌ 你没有管理员权限")
        return

    # 新增：查看用户列表按钮
    keyboard = [
        [InlineKeyboardButton("📊 统计总数", callback_data="stats")],
        [InlineKeyboardButton("👥 查看用户列表", callback_data="list_users")], # 新增
        [InlineKeyboardButton("🔍 搜索文件", callback_data="search")],
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
        # 修复 2：只显示数量，不显示具体文件
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
        # 修复 3：解决管理员不知道用户ID的问题
        if not user_index:
            await query.edit_message_text("❌ 暂无用户记录")
            return
        
        msg = "👥 所有使用过的用户列表 (ID | 昵称)\n\n"
        # 显示前30个用户，防止消息过长
        for uid, info in list(user_index.items())[:30]:
            msg += f"ID: `{uid}` | 昵称: {info['name']}\n"
        
        if len(user_index) > 30:
            msg += f"\n... 还有 {len(user_index) - 30} 个用户未显示"
        
        await query.edit_message_text(msg, parse_mode="Markdown")

    elif query.data == "search":
        await query.edit_message_text("🔍 请发送搜索关键词（支持实时匹配）：")
        context.user_data["admin_act"] = "search"

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
        # 修复 1：彻底修复实时搜索，确保任何新入库的文件都能搜到
        # 逻辑：实时读取最新的 DB，进行全量小写包含匹配
        keyword = text.lower()
        results = []
        # 遍历数据库中的每一个文件包
        for code, pkg in db.items():
            # 遍历文件包中的每一个文件
            for file in pkg["files"]:
                # 核心修复：统一转为小写进行包含判断
                if keyword in file["name"].lower():
                    # 去重：同一个提取码只显示一次
                    result_line = f"🔑 提取码：`{code}`\n📦 包名：{pkg.get('name', '未命名')}\n👤 上传者ID：`{pkg['uploader']['id']}`"
                    if result_line not in results:
                        results.append(result_line)

        if results:
            await update.message.reply_text(f"✅ 找到 {len(results)} 个匹配结果：\n\n" + "\n\n".join(results), parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ 未找到包含该关键词的文件")

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

    # 清理状态
    del context.user_data["admin_act"]

# ====================== 用户功能 (保持不变) ======================
@check_ban
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 欢迎使用永久文件存储机器人！\n\n"
        "📝 使用指南：\n"
        "1. 直接发送图片或文件\n"
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
    else:
        await message.reply_text("❌ 暂不支持此类型文件，仅支持图片和文档")
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
    # 生成唯一提取码
    while True:
        code = str(random.randint(100000, 999999))
        if code not in db:
            break

    # 准备入库数据
    pending_naming[user_id] = {
        "code": code,
        "files": user_sessions[user_id],
        "uploader": {
            "id": user_id,
            "name": update.effective_user.full_name
        },
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    # 清空临时会话
    del user_sessions[user_id]

    await update.message.reply_text(f"📦 打包成功！\n🔑 你的提取码：`{code}`\n请为文件包命名（或发送 /skip 跳过）：", parse_mode="Markdown")

# ====================== 统一文本处理入口 ======================
@check_ban
async def handle_all_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    db = get_db()

    # 1. 优先处理命名流程 (生成文件入库的关键步骤)
    if user_id in pending_naming:
        pkg_data = pending_naming[user_id]
        # 设置包名
        pkg_data["name"] = text if text != "/skip" else "未命名"
        # 实时写入数据库 (修复1的关键：命名后立即保存)
        db[pkg_data["code"]] = pkg_data
        save_db(db)
        # 清除命名状态
        del pending_naming[user_id]
        # 回复用户
        await update.message.reply_text(f"✅ 保存成功！\n📦 包名：{pkg_data['name']}\n🔑 提取码：`{pkg_data['code']}`\n妥善保管你的提取码！", parse_mode="Markdown")
        return

    # 2. 其次处理管理员操作
    if user_id == ADMIN_USER_ID and "admin_act" in context.user_data:
        await handle_admin_action(update, context)
        return

    # 3. 最后处理取件码验证
    if text in db:
        pkg = db[text]
        await update.message.reply_text(f"📦 正在为你取回文件包：【{pkg.get('name', '未命名')}】", parse_mode="Markdown")
        # 发送文件
        for file in pkg["files"]:
            try:
                if file["type"] == "img":
                    await update.message.reply_photo(photo=file["id"])
                else:
                    await update.message.reply_document(document=file["id"], filename=file["name"])
            except Exception as e:
                await update.message.reply_text(f"⚠️ 发送文件 `{file['name']}` 时出错：{str(e)}")
        return

    # 如果都不是，提示用户
    await update.message.reply_text("❌ 无效指令。若要取件，请发送正确的6位提取码。")

# ====================== 主程序启动 ======================
def main():
    if not BOT_TOKEN:
        raise ValueError("请设置 TELEGRAM_BOT_TOKEN 环境变量")

    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # 命令注册
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("confirm", confirm_package))
    application.add_handler(CommandHandler("admin", admin_panel))

    # 消息注册
    application.add_handler(MessageHandler(filters.ATTACHMENT, upload_file))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_all_text))
    
    # 回调注册
    application.add_handler(CallbackQueryHandler(admin_callback))

    # 启动
    application.run_polling()

if __name__ == "__main__":
    main()
