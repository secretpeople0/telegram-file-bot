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

# ====================== 永久存储核心 ======================
def get_db():
    data = os.environ.get("BOT_DB", "{}")
    try:
        return json.loads(data)
    except:
        return {}

def save_db(db_data):
    os.environ["BOT_DB"] = json.dumps(db_data, ensure_ascii=False, indent=2)

db = get_db()

# ====================== 封禁 ======================
banned_users = []
try:
    banned_users = json.loads(os.environ.get("BOT_BANNED", "[]"))
except:
    banned_users = []

def save_banned():
    os.environ["BOT_BANNED"] = json.dumps(banned_users, ensure_ascii=False)

# ====================== 封禁检查 ======================
def check_ban(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id in banned_users:
            await update.message.reply_text("❌ 你已被封禁。")
            return
        return await func(update, context)
    return wrapper

# ====================== 管理员 ======================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        return
    keyboard = [
        [InlineKeyboardButton("📊 统计", callback_data="stats")],
        [InlineKeyboardButton("🔍 搜索", callback_data="search")],
        [InlineKeyboardButton("🗑️ 删除", callback_data="delete")],
        [InlineKeyboardButton("🚫 封禁", callback_data="ban")],
        [InlineKeyboardButton("👤 查看用户", callback_data="user")],
    ]
    await update.message.reply_text("👮 管理面板", reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.from_user.id != ADMIN_USER_ID:
        return
    if q.data == "stats":
        t = len(db)
        f = sum(len(i["files"]) for i in db.values())
        await q.edit_message_text(f"📊 打包：{t}\n文件：{f}")
    elif q.data == "search":
        await q.edit_message_text("🔍 发关键词")
        context.user_data["act"] = "search"
    elif q.data == "delete":
        await q.edit_message_text("🗑️ 发提取码")
        context.user_data["act"] = "del"
    elif q.data == "ban":
        await q.edit_message_text("🚫 发用户ID 或 /unban ID")
        context.user_data["act"] = "ban"
    elif q.data == "user":
        await q.edit_message_text("👤 发用户ID")
        context.user_data["act"] = "user"

async def admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "act" not in context.user_data:
        return
    uid = update.effective_user.id
    if uid != ADMIN_USER_ID:
        return
    act = context.user_data["act"]
    txt = update.message.text.strip()
    db = get_db()

    if act == "search":
        res = []
        for code, pkg in db.items():
            for f in pkg["files"]:
                if txt.lower() in f["name"].lower():
                    res.append(f"🔑 {code}\n📦 {pkg.get('name','未命名')}\n📄 {f['name']}")
        await update.message.reply_text("\n\n".join(res) if res else "❌ 无结果")
    elif act == "del":
        if txt in db:
            del db[txt]
            save_db(db)
            await update.message.reply_text("✅ 删除成功")
        else:
            await update.message.reply_text("❌ 不存在")
    elif act == "ban":
        try:
            if txt.startswith("/unban"):
                id = int(txt.split()[1])
                if id in banned_users:
                    banned_users.remove(id)
                    save_banned()
                    await update.message.reply_text("✅ 已解封")
            else:
                id = int(txt)
                if id not in banned_users:
                    banned_users.append(id)
                    save_banned()
                    await update.message.reply_text("✅ 已封禁")
        except:
            await update.message.reply_text("❌ 格式错误")
    elif act == "user":
        try:
            target = int(txt)
            res = []
            for code, pkg in db.items():
                if pkg["uploader"]["id"] == target:
                    res.append(f"🔑 {code} | {pkg.get('name','未命名')}")
            await update.message.reply_text("\n".join(res) if res else "❌ 无记录")
        except:
            await update.message.reply_text("❌ 错误")
    del context.user_data["act"]

# ====================== 用户功能 ======================
@check_ban
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 发文件 → /confirm → 命名 → 获取提取码")

@check_ban
async def upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    msg = update.message
    if uid not in user_sessions:
        user_sessions[uid] = []
    f = None
    if msg.document:
        f = {"type":"doc","id":msg.document.file_id,"name":msg.document.file_name or "文件"}
    elif msg.photo:
        f = {"type":"img","id":msg.photo[-1].file_id,"name":f"图片{datetime.now().strftime('%M%S')}"}
    if not f:
        return
    user_sessions[uid].append(f)
    await msg.reply_text(f"✅ 已收 {len(user_sessions[uid])} 个\n输入 /confirm 打包")

@check_ban
async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in user_sessions or not user_sessions[uid]:
        await update.message.reply_text("❌ 未上传文件")
        return
    db = get_db()
    while True:
        code = str(random.randint(100000,999999))
        if code not in db: break
    pending_naming[uid] = {
        "code": code,
        "files": user_sessions[uid],
        "uploader": {"id":uid, "name":update.effective_user.full_name},
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    del user_sessions[uid]
    await update.message.reply_text(f"📦 打包成功！\n🔑 {code}\n请输入名称 或 /skip 跳过")

# ====================== 统一文本处理（修复命名） ======================
@check_ban
async def handle_all_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    txt = update.message.text.strip()
    db = get_db()

    # 正在命名
    if uid in pending_naming:
        pkg = pending_naming[uid]
        pkg["name"] = txt if txt != "/skip" else "未命名"
        db[pkg["code"]] = pkg
        save_db(db)
        del pending_naming[uid]
        await update.message.reply_text(f"✅ 完成！\n📦 {pkg['name']}\n🔑 {pkg['code']}")
        return

    # 管理员
    if "act" in context.user_data:
        await admin_text(update, context)
        return

    # 提取码
    if txt in db:
        pkg = db[txt]
        await update.message.reply_text(f"📦 {pkg.get('name','未命名')}")
        for f in pkg["files"]:
            try:
                if f["type"] == "img":
                    await update.message.reply_photo(f["id"])
                else:
                    await update.message.reply_document(f["id"], filename=f["name"])
            except:
                await update.message.reply_text("⚠️ 发送失败")

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
