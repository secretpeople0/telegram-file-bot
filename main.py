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

# ====================== 配置 ======================
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ADMIN_USER_ID = int(os.environ.get("ADMIN_USER_ID", "0"))

user_sessions = {}
pending_naming = {}

# ====================== 永久存储 ======================
def get_db():
    try:
        return json.loads(os.environ.get("BOT_DB", "{}"))
    except:
        return {}

def save_db(db_data):
    os.environ["BOT_DB"] = json.dumps(db_data, ensure_ascii=False, indent=2)

# 封禁
def get_banned():
    try:
        return json.loads(os.environ.get("BOT_BANNED", "[]"))
    except:
        return []

def save_banned(banned_list):
    os.environ["BOT_BANNED"] = json.dumps(banned_list, ensure_ascii=False)

banned_users = get_banned()

# ====================== 封禁检查 ======================
def check_ban(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id in banned_users:
            await update.message.reply_text("❌ 你已被封禁，无法使用。")
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
        [InlineKeyboardButton("🔍 搜索文件", callback_data="search")],
        [InlineKeyboardButton("🗑️ 删除提取码", callback_data="delete")],
        [InlineKeyboardButton("🚫 封禁/解封用户", callback_data="ban")],
        [InlineKeyboardButton("👤 查看用户上传", callback_data="view_user")],
    ]
    await update.message.reply_text("👮 管理员面板", reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.from_user.id != ADMIN_USER_ID:
        return

    if q.data == "stats":
        db = get_db()
        packs = len(db)
        files = sum(len(p["files"]) for p in db.values())
        await q.edit_message_text(f"📊 统计\n文件包：{packs}\n总文件：{files}")

    elif q.data == "search":
        await q.edit_message_text("🔍 请发送关键词，支持包含搜索")
        context.user_data["admin_act"] = "search"

    elif q.data == "delete":
        await q.edit_message_text("🗑️ 发送提取码")
        context.user_data["admin_act"] = "delete"

    elif q.data == "ban":
        await q.edit_message_text("🚫 发送用户ID，解封：/unban 123456")
        context.user_data["admin_act"] = "ban"

    elif q.data == "view_user":
        await q.edit_message_text("👤 发送用户ID")
        context.user_data["admin_act"] = "view_user"

# ====================== 管理员文本处理 ======================
async def handle_admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "admin_act" not in context.user_data:
        return

    uid = update.effective_user.id
    if uid != ADMIN_USER_ID:
        return

    act = context.user_data["admin_act"]
    text = update.message.text.strip()
    db = get_db()

    if act == "search":
        # ====================== 修复：包含关键词搜索 ======================
        keyword = text.lower()
        result = []
        for code, pkg in db.items():
            for f in pkg["files"]:
                fname = f["name"].lower()
                if keyword in fname:
                    line = f"🔑 {code}\n📦 {pkg.get('name','未命名')}\n📄 {f['name']}"
                    if line not in result:
                        result.append(line)
        if result:
            await update.message.reply_text("\n\n".join(result))
        else:
            await update.message.reply_text("❌ 无匹配结果")

    elif act == "delete":
        if text in db:
            del db[text]
            save_db(db)
            await update.message.reply_text("✅ 已删除")
        else:
            await update.message.reply_text("❌ 提取码不存在")

    elif act == "ban":
        try:
            if text.startswith("/unban"):
                tid = int(text.split()[1])
                if tid in banned_users:
                    banned_users.remove(tid)
                    save_banned(banned_users)
                    await update.message.reply_text(f"✅ 已解封 {tid}")
                else:
                    await update.message.reply_text("❌ 未封禁")
            else:
                tid = int(text)
                if tid not in banned_users:
                    banned_users.append(tid)
                    save_banned(banned_users)
                    await update.message.reply_text(f"✅ 已封禁 {tid}")
        except:
            await update.message.reply_text("❌ 格式错误")

    elif act == "view_user":
        try:
            target = int(text)
            res = []
            for code, pkg in db.items():
                if pkg["uploader"]["id"] == target:
                    res.append(f"🔑 {code} | {pkg.get('name','未命名')}")
            if res:
                await update.message.reply_text("\n".join(res))
            else:
                await update.message.reply_text("❌ 该用户无上传")
        except:
            await update.message.reply_text("❌ 无效ID")

    del context.user_data["admin_act"]

# ====================== 用户功能 ======================
@check_ban
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 发送文件 → /confirm 打包 → 命名 → 获取提取码")

@check_ban
async def upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    msg = update.message

    if uid not in user_sessions:
        user_sessions[uid] = []

    f = None
    if msg.document:
        f = {"type": "doc", "id": msg.document.file_id, "name": msg.document.file_name or "文件"}
    elif msg.photo:
        f = {"type": "img", "id": msg.photo[-1].file_id, "name": f"图片{datetime.now().strftime('%M%S')}"}

    if not f:
        return

    user_sessions[uid].append(f)
    await msg.reply_text(f"✅ 已接收：{len(user_sessions[uid])} 个\n输入 /confirm 打包")

@check_ban
async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in user_sessions or not user_sessions[uid]:
        await update.message.reply_text("❌ 你还没有上传文件")
        return

    db = get_db()
    while True:
        code = str(random.randint(100000, 999999))
        if code not in db:
            break

    pending_naming[uid] = {
        "code": code,
        "files": user_sessions[uid],
        "uploader": {"id": uid, "name": update.effective_user.full_name},
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    del user_sessions[uid]
    await update.message.reply_text(f"📦 打包成功！\n🔑 {code}\n请输入包名 或 /skip 跳过")

@check_ban
async def handle_all_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()
    db = get_db()

    # 命名流程
    if uid in pending_naming:
        pkg = pending_naming[uid]
        pkg["name"] = text if text != "/skip" else "未命名"
        db[pkg["code"]] = pkg
        save_db(db)
        del pending_naming[uid]
        await update.message.reply_text(f"✅ 完成！\n📦 {pkg['name']}\n🔑 {pkg['code']}")
        return

    # 管理员操作
    if "admin_act" in context.user_data:
        await handle_admin_action(update, context)
        return

    # 提取码
    if text in db:
        pkg = db[text]
        await update.message.reply_text(f"📦 正在获取：{pkg.get('name','未命名')}")
        for f in pkg["files"]:
            try:
                if f["type"] == "img":
                    await update.message.reply_photo(f["id"])
                else:
                    await update.message.reply_document(f["id"], filename=f["name"])
            except Exception as e:
                await update.message.reply_text(f"⚠️ 文件发送失败")

# ====================== 启动 ======================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("confirm", confirm))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(MessageHandler(filters.ATTACHMENT, upload))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_all_text))
    app.add_handler(CallbackQueryHandler(admin_callback))

    app.run_polling()

if __name__ == "__main__":
    main()
