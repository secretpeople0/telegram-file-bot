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

user_sessions = {}
pending_naming = {}

# ====================== 数据库 ======================
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

# ====================== 自动记录用户 ======================
async def track_user(update: Update):
    user = update.effective_user
    if not user: return
    uid = str(user.id)
    idx = get_user_index()
    if uid not in idx or idx[uid]["name"] != user.full_name:
        idx[uid] = {
            "name": user.full_name,
            "username": user.username or "无"
        }
        save_user_index(idx)

# ====================== 封禁检查 ======================
def check_ban(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await track_user(update)
        if update.effective_user.id in banned_users:
            await update.message.reply_text("❌ 你已被封禁。")
            return
        return await func(update, context)
    return wrapper

# ====================== 管理员面板 ======================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("❌ 无权限")
        return
    keyboard = [
        [InlineKeyboardButton("📊 统计总数", callback_data="stats")],
        [InlineKeyboardButton("👥 用户列表", callback_data="list_users")],
        [InlineKeyboardButton("🔍 搜索文件", callback_data="search")],
        [InlineKeyboardButton("🗑️ 删除提取码", callback_data="delete")],
        [InlineKeyboardButton("🚫 封禁/解封", callback_data="ban")],
    ]
    await update.message.reply_text("👮 管理员面板", reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.from_user.id != ADMIN_USER_ID:
        return

    db = get_db()
    ui = get_user_index()

    if q.data == "stats":
        packs = len(db)
        files = sum(len(p["files"]) for p in db.values())
        users = len(ui)
        await q.edit_message_text(f"📊 统计\n总用户：{users}\n文件包：{packs}\n总文件：{files}")

    elif q.data == "list_users":
        if not ui:
            await q.edit_message_text("❌ 暂无用户")
            return
        msg = "👥 用户列表（ID 可复制）\n\n"
        for uid, info in list(ui.items())[:30]:
            msg += f"ID: `{uid}` | {info['name']}\n"
        await q.edit_message_text(msg, parse_mode="Markdown")

    elif q.data == "search":
        await q.edit_message_text("🔍 发送关键词，自动搜：包名 + 文件名")
        context.user_data["admin_act"] = "search"

    elif q.data == "delete":
        await q.edit_message_text("🗑️ 发送提取码")
        context.user_data["admin_act"] = "delete"

    elif q.data == "ban":
        await q.edit_message_text("🚫 发送用户ID，解封：/unban 123")
        context.user_data["admin_act"] = "ban"

# ====================== 【已修复】管理员搜索 ======================
async def handle_admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "admin_act" not in context.user_data:
        return
    if update.effective_user.id != ADMIN_USER_ID:
        return

    act = context.user_data["admin_act"]
    txt = update.message.text.strip()
    db = get_db()
    keyword = txt.lower()

    if act == "search":
        result = []
        for code, pkg in db.items():
            pack_name = pkg.get("name", "").lower()
            match = False

            # ========== 修复点 1：搜包名 ==========
            if keyword in pack_name:
                match = True

            # ========== 修复点 2：搜文件名 ==========
            for f in pkg["files"]:
                if keyword in f["name"].lower():
                    match = True
                    break

            if match:
                line = f"🔑 `{code}`\n📦 {pkg.get('name','未命名')}\n👤 用户ID: `{pkg['uploader']['id']}`"
                if line not in result:
                    result.append(line)

        if result:
            await update.message.reply_text("\n\n".join(result), parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ 无匹配结果")

    elif act == "delete":
        if txt in db:
            del db[txt]
            save_db(db)
            await update.message.reply_text("✅ 删除成功")
        else:
            await update.message.reply_text("❌ 提取码不存在")

    elif act == "ban":
        try:
            if txt.startswith("/unban"):
                tid = int(txt.split()[1])
                if tid in banned_users:
                    banned_users.remove(tid)
                    save_banned(banned_users)
                    await update.message.reply_text(f"✅ 已解封 {tid}")
            else:
                tid = int(txt)
                if tid not in banned_users:
                    banned_users.append(tid)
                    save_banned(banned_users)
                    await update.message.reply_text(f"✅ 已封禁 {tid}")
        except:
            await update.message.reply_text("❌ 格式错误")

    del context.user_data["admin_act"]

# ====================== 用户功能 ======================
@check_ban
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 发送文件 → /confirm 打包 → 命名 → 获取提取码")

@check_ban
async def upload_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    msg = update.message
    if uid not in user_sessions:
        user_sessions[uid] = []

    f = None
    if msg.document:
        f = {
            "type": "doc",
            "id": msg.document.file_id,
            "name": msg.document.file_name or "文件"
        }
    elif msg.photo:
        f = {
            "type": "img",
            "id": msg.photo[-1].file_id,
            "name": "图片"  # 图片统一名称，不影响搜索
        }

    if f:
        user_sessions[uid].append(f)
        await msg.reply_text(f"✅ 已收 {len(user_sessions[uid])} 个\n输入 /confirm 打包")

@check_ban
async def confirm_package(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in user_sessions or not user_sessions[uid]:
        await update.message.reply_text("❌ 未上传文件")
        return

    db = get_db()
    while True:
        code = str(random.randint(100000, 999999))
        if code not in db: break

    pending_naming[uid] = {
        "code": code,
        "files": user_sessions[uid],
        "uploader": {"id": uid, "name": update.effective_user.full_name},
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    del user_sessions[uid]
    await update.message.reply_text(f"📦 打包成功！\n🔑 `{code}`\n请输入包名 或 /skip", parse_mode="Markdown")

@check_ban
async def handle_all_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()
    db = get_db()

    # 命名入库
    if uid in pending_naming:
        pkg = pending_naming[uid]
        pkg["name"] = text if text != "/skip" else "未命名"
        db[pkg["code"]] = pkg
        save_db(db)
        del pending_naming[uid]
        await update.message.reply_text(f"✅ 完成！\n📦 {pkg['name']}\n🔑 `{pkg['code']}`", parse_mode="Markdown")
        return

    # 管理员操作
    if "admin_act" in context.user_data:
        await handle_admin_action(update, context)
        return

    # 取件
    if text in db:
        pkg = db[text]
        await update.message.reply_text(f"📦 正在获取：{pkg.get('name','未命名')}")
        for f in pkg["files"]:
            try:
                if f["type"] == "img":
                    await update.message.reply_photo(f["id"])
                else:
                    await update.message.reply_document(f["id"], filename=f["name"])
            except:
                await update.message.reply_text("⚠️ 发送失败")
        return

    await update.message.reply_text("❌ 无效指令或提取码错误")

# ====================== 启动 ======================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("confirm", confirm_package))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(MessageHandler(filters.ATTACHMENT, upload_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_all_text))
    app.add_handler(CallbackQueryHandler(admin_callback))
    app.run_polling()

if __name__ == "__main__":
    main()
