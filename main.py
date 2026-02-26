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
BOT_SELF_ID = None

def xor_data(data: bytes, key: bytes) -> bytes:
    key = key * (len(data) // len(key) + 1)
    return bytes(d ^ k for d, k in zip(data, key))

def encrypt_metadata(metadata: dict) -> str:
    try:
        raw = json.dumps(metadata).encode("utf-8")
        encrypted = xor_data(raw, E2EE_KEY)
        return base64.urlsafe_b64encode(encrypted).decode("utf-8")
    except Exception as e:
        print(f"加密元数据失败: {e}")
        return ""

def decrypt_metadata(encrypted_str: str) -> dict:
    try:
        raw = base64.urlsafe_b64decode(encrypted_str.encode("utf-8"))
        decrypted = xor_data(raw, E2EE_KEY)
        return json.loads(decrypted.decode("utf-8"))
    except Exception as e:
        print(f"解密密数据失败: {e}")
        return {}

# ==================== 兼容旧提取码 ====================
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

# ==================== 数据存储 ====================
DATA_DIR = "/data"
BACKUP_DIR = os.path.join(DATA_DIR, "backup")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

user_sessions = {}
pending_naming = {}
admin_ops = {}

def load_json(name):
    p = os.path.join(DATA_DIR, name)
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {} if name in ["bot_db", "user_index", "banned"] else []

def save_json(name, data):
    p = os.path.join(DATA_DIR, name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

bot_db = load_json("bot_db.json")
user_idx = load_json("user_index.json")
banned = load_json("banned.json")

# ==================== 工具 ====================
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

def auto_backup():
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    for f in ["bot_db.json", "user_index.json", "banned.json"]:
        src = os.path.join(DATA_DIR, f)
        dst = os.path.join(BACKUP_DIR, f"{f}.{ts}")
        if os.path.exists(src):
            shutil.copy(src, dst)
    olds = sorted([os.path.join(BACKUP_DIR, x) for x in os.listdir(BACKUP_DIR)], key=lambda x: os.path.getmtime(os.path.join(BACKUP_DIR, x)))
    for old in olds[:-3]:
        os.remove(os.path.join(BACKUP_DIR, old))

# ==================== 菜单 ====================
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

# ==================== 命令 ====================
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📦 TG云盘机器人（大文件突破版）\n/my 我的提取码\n/del 提取码")

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

# ==================== 上传：突破大小限制（核心稳定版） ====================
async def upload(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global BOT_SELF_ID
    if BOT_SELF_ID is None:
        me = await ctx.bot.get_me()
        BOT_SELF_ID = me.id

    u = update.effective_user
    if is_banned(u.id) or is_bot_self(u.id):
        return

    msg = update.message
    file_id = None
    orig_type = ""
    file_name = ""

    # 精准识别文件类型与核心ID，不调用任何受限API
    if msg.photo:
        file_id = msg.photo[-1].file_id
        orig_type = "photo"
        file_name = f"IMG_{datetime.now().strftime('%H%M%S')}.jpg"
    elif msg.video:
        file_id = msg.video.file_id
        orig_type = "video"
        file_name = msg.video.file_name or f"VID_{datetime.now().strftime('%H%M%S')}.mp4"
    elif msg.document:
        file_id = msg.document.file_id
        orig_type = "doc"
        file_name = msg.document.file_name or f"FILE_{datetime.now().strftime('%H%M%S')}"
    else:
        return  # 非文件类型直接忽略

    try:
        # 仅加密核心元数据，不触碰文件本身
        metadata = {
            "file_id": file_id,
            "name": file_name,
            "type": orig_type,
        }
        encrypted_meta = encrypt_metadata(metadata)
        if not encrypted_meta:
            return await msg.reply_text("❌ 元数据加密失败，请重试")

        # 写入用户会话
        if u.id not in user_sessions:
            user_sessions[u.id] = []
        user_sessions[u.id].append({
            "meta_type": "link_enc",
            "data": encrypted_meta,
            "name": file_name,
            "orig_type": orig_type
        })

        # 友好反馈
        await msg.reply_text(
            f"✅ 接收成功！已存入 {len(user_sessions[u.id])} 个文件\n"
            f"📄 文件名：{file_name}\n"
            f"💡 发送 /confirm 打包 | /skip 跳过命名"
        )
        await track(u.id, u.full_name, u.username)

    except Exception as e:
        # 精准捕获异常，避免机器人崩溃
        await msg.reply_text(f"❌ 上传异常：{str(e)[:80]}")

async def confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user.id
    if u not in user_sessions or not user_sessions[u]:
        return await update.message.reply_text("❌ 暂无待打包文件")
    
    # 生成唯一6位提取码
    chars = string.ascii_letters + string.digits
    code = None
    for _ in range(100):
        candidate = ''.join(random.choice(chars) for _ in range(6))
        if candidate not in bot_db:
            code = candidate
            break
    if code is None:
        return await update.message.reply_text("❌ 提取码生成失败，请稍后再试")
    
    pending_naming[u] = {
        "code": code,
        "files": user_sessions[u],
        "uploader": {"id": u, "name": update.effective_user.full_name}
    }
    del user_sessions[u]
    await update.message.reply_text("📦 请输入包名（最多50字），或发送 /skip 跳过")

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

# ==================== 提取：双模式兼容（稳定无崩溃） ====================
async def text_handle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global BOT_SELF_ID
    if BOT_SELF_ID is None:
        me = await ctx.bot.get_me()
        BOT_SELF_ID = me.id

    u = update.effective_user.id
    cid = update.effective_chat.id
    txt = update.message.text.strip()

    # 基础拦截
    if is_bot_self(u) or is_banned(u):
        return

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
        elif act == "search":
            res = [f"🔑 {c}｜{p['name']}" for c, p in bot_db.items() if txt.lower() in p['name'].lower()]
            return await update.message.reply_text("\n".join(res) if res else "🔍 未找到匹配文件包")
        elif act == "user_uploads":
            res = [f"🔑 {c}｜{p['name']}" for c, p in bot_db.items() if str(p['uploader']['id']) == txt.strip()]
            return await update.message.reply_text("\n".join(res) if res else "🔍 该用户暂无上传记录")
        elif act == "ban":
            parts = txt.split()
            if len(parts) != 2:
                return await update.message.reply_text("🚫 格式错误：封禁 123456 / 解封 123456")
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
            except ValueError:
                await update.message.reply_text("❌ 用户ID必须为数字")
            return

    # 包名命名处理
    if u in pending_naming:
        pkg = pending_naming[u]
        pkg["name"] = txt[:50]
        bot_db[pkg["code"]] = pkg
        save_json("bot_db.json", bot_db)
        auto_backup()
        del pending_naming[u]
        return await update.message.reply_text(f"✅ 包名设置成功，提取码：{pkg['code']}")

    # 提取码解析（核心逻辑）
    if len(txt) == 6:
        if txt not in bot_db:
            return await update.message.reply_text("❌ 提取码不存在或已失效")
        
        pkg = bot_db[txt]
        await update.message.reply_text(f"📦 正在提取文件包：{pkg['name']}（共{len(pkg['files'])}个文件）")
        
        for idx, f in enumerate(pkg["files"], 1):
            try:
                # 新模式：大文件直链加密（无大小限制）
                if f.get("meta_type") == "link_enc":
                    meta = decrypt_metadata(f["data"])
                    if not meta:
                        await update.message.reply_text(f"⚠️ 第{idx}个文件：解密失败（密钥不匹配）")
                        continue
                    fid = meta.get("file_id")
                    name = meta.get("name", f["name"])
                    typ = meta.get("type", f["orig_type"])
                    
                    # 精准转发，避免类型错误
                    if typ == "photo":
                        await update.message.reply_photo(photo=fid, filename=name, caption=f"📸 {idx}/{len(pkg['files'])}")
                    elif typ == "video":
                        await update.message.reply_video(video=fid, filename=name, caption=f"🎬 {idx}/{len(pkg['files'])}")
                    else:
                        await update.message.reply_document(document=fid, filename=name, caption=f"📄 {idx}/{len(pkg['files'])}")
                
                # 旧模式：兼容历史加密文件
                elif f.get("type") == "enc":
                    fid = decode_file_id(f["id"])
                    file = await ctx.bot.get_file(fid, read_timeout=20)
                    data = await file.download_as_bytearray(read_timeout=20)
                    dec = xor_data(data, E2EE_KEY)
                    with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(f["name"])[1]) as tmp:
                        tmp.write(dec)
                        temp_path = tmp.name
                    # 按类型发送
                    if f["orig_type"] == "photo":
                        await update.message.reply_photo(open(temp_path, "rb"), filename=f["name"])
                    elif f["orig_type"] == "video":
                        await update.message.reply_video(open(temp_path, "rb"), filename=f["name"])
                    else:
                        await update.message.reply_document(open(temp_path, "rb"), filename=f["name"])
                    os.unlink(temp_path)  # 清理临时文件
                
                else:
                    await update.message.reply_text(f"⚠️ 第{idx}个文件：不支持的文件类型")
            
            except Exception as e:
                # 分类处理异常，避免全局崩溃
                if "file not found" in str(e).lower():
                    await update.message.reply_text(f"❌ 第{idx}个文件：{f['name']}（原文件已被清理）")
                else:
                    await update.message.reply_text(f"❌ 第{idx}个文件：{f['name']} 发送失败")
        
        await update.message.reply_text(f"✅ 提取完成！文件包「{pkg['name']}」处理完毕")
        return

# ==================== 管理员面板（稳定版） ====================
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
    elif act == "统计":
        pkgs = len(bot_db)
        files = sum(len(v["files"]) for v in bot_db.values())
        users = len(user_idx)
        await q.edit_message_text(
            f"📊 机器人统计\n"
            f"📦 提取码总数：{pkgs}\n"
            f"📄 存储文件数：{files}\n"
            f"👥 注册用户数：{users}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="返回")]])
        )
    elif act == "用户列表":
        lines = [f"{uid}｜{d['name']}" for uid, d in user_idx.items()]
        display_text = "\n".join(lines[:50])
        if len(lines) > 50:
            display_text += f"\n...（共{len(lines)}个用户，仅显示前50个）"
        await q.edit_message_text(display_text or "📭 暂无注册用户", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="返回")]]))
    elif act == "搜文件":
        admin_ops[cid] = "search"
        await q.edit_message_text("🔍 请输入文件包名称关键词", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="返回")]]))
    elif act == "查用户上传":
        admin_ops[cid] = "user_uploads"
        await q.edit_message_text("👤 请输入用户ID（纯数字）", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="返回")]]))
    elif act == "删提取码":
        admin_ops[cid] = "del_code"
        await q.edit_message_text("🗑️ 请输入提取码（多个用空格分隔）", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="返回")]]))
    elif act == "封禁/解封":
        admin_ops[cid] = "ban"
        await q.edit_message_text("🚫 请输入「封禁 123456」或「解封 123456」", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data="返回")]]))

# ==================== 备份与数据库导出（防数据丢失） ====================
async def backup_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        return await update.message.reply_text("❌ 无管理员权限")
    auto_backup()
    await update.message.reply_text("✅ 数据库备份完成，已保留最近3份备份")

async def getdb_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        return await update.message.reply_text("❌ 无管理员权限")
    for f in ["bot_db.json", "user_index.json", "banned.json"]:
        p = os.path.join(DATA_DIR, f)
        if os.path.exists(p):
            await update.message.reply_document(open(p, "rb"), caption=f"📋 {f} 数据库文件")
    await update.message.reply_text("✅ 数据库文件导出完成")

# ==================== 启动入口（最终稳定版） ====================
def main():
    # 校验核心环境变量
    if not BOT_TOKEN:
        print("❌ 错误：未设置 BOT_TOKEN 环境变量")
        exit(1)
    if ADMIN_USER_ID == 0:
        print("⚠️ 警告：未设置 ADMIN_USER_ID，管理员功能将不可用")
    
    # 构建应用并启动
    app = ApplicationBuilder()\
        .token(BOT_TOKEN)\
        .connect_timeout(30)\
        .read_timeout(30)\
        .write_timeout(30)\
        .build()
    
    # 注册所有处理器
    command_handlers = [
        CommandHandler("start", start),
        CommandHandler("my", my_codes),
        CommandHandler("del", del_code),
        CommandHandler("confirm", confirm),
        CommandHandler("skip", skip),
        CommandHandler("admin", admin),
        CommandHandler("backup", backup_cmd),
        CommandHandler("getdb", getdb_cmd)
    ]
    for handler in command_handlers:
        app.add_handler(handler)
    
    # 注册回调与消息处理器
    app.add_handler(CallbackQueryHandler(admin_cb))
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, upload))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handle))
    
    # 启动机器人（带重启机制）
    print("✅ 机器人正在启动...")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,  # 启动时丢弃积压消息，避免崩溃
        timeout=30
    )

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("✅ 机器人已手动停止")
    except Exception as e:
        print(f"❌ 机器人启动失败：{e}")
        time.sleep(5)
        # 自动重启（针对临时网络错误）
        main()
