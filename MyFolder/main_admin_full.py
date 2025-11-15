
import os
import json
import subprocess
import telebot
from telebot import types
from flask import Flask, request

# -------------------------
# Configuration
# -------------------------
BOT_TOKEN = "8204294026:AAFCWiidQNHN0VqsLaL9RKdn8Q0XLmroQQM"
OWNER_ID = 7975219600  # ضع آيدي صاحب البوت هنا (المالك)
DATA_DIR = "data_files"

if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

FILES_META = os.path.join(DATA_DIR, "files_meta.json")
ADMINS_FILE = os.path.join(DATA_DIR, "admins.json")
PIN_FILE = os.path.join(DATA_DIR, "admin_pin.json")
STATS_FILE = os.path.join(DATA_DIR, "stats.json")

# Default persistent structures
def load_json(path, default):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print("Error loading", path, e)
    return default

def save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("Error saving", path, e)

files_meta = load_json(FILES_META, {})  # { filename: {uploader_id, uploaded_at, running:bool, pid:int|null} }
admins = load_json(ADMINS_FILE, [OWNER_ID])  # list of admin user ids
admin_pin = load_json(PIN_FILE, {"pin": None})  # {'pin': '1234'} or {'pin': None}
stats = load_json(STATS_FILE, {"uploads":0, "runs":0, "deletes":0})

# -------------------------
# Bot + Flask app
# -------------------------
bot = telebot.TeleBot(BOT_TOKEN, threaded=True)
app = Flask(__name__)

# In-memory runtime
running_process = None
running_file = None

# Helper: check admin
def is_admin(user_id):
    return user_id in admins

# Helper: list python files (exclude this main file)
def list_user_files():
    files = [f for f in os.listdir(".") if f.endswith(".py") and f not in ("main.py","main_admin.py")]
    # but include files_meta keys
    for f in files:
        if f not in files_meta:
            files_meta[f] = {"uploader_id":None, "uploaded_at":None, "running":False, "pid":None}
    save_json(FILES_META, files_meta)
    return files

# -------------------------
# Webhook routes
# -------------------------
@app.route("/", methods=["GET"])
def home():
    return "Bot is running with webhook."

@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    json_str = request.get_data().decode("utf-8")
    update = telebot.types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return "OK", 200

# -------------------------
# Commands
# -------------------------
@bot.message_handler(commands=["start"])
def start_cmd(message):
    uid = message.from_user.id
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("رفع ملف 📤", callback_data="upload"),
           types.InlineKeyboardButton("ملفاتي 📁", callback_data="my_files"))
    if is_admin(uid):
        kb.add(types.InlineKeyboardButton("🔧 لوحة الأدمن", callback_data="admin_panel"))
    bot.send_message(uid, "مرحباً! هذا بوت رفع وتشغيل ملفات.\n"
                         "يمكنك رفع ملف .py ثم تشغيله على السيرفر (إذا كانت لديك صلاحيات).\n"
                         "لأمور إدارية اضغط زر لوحة الأدمن (إن كنت أدمن).", reply_markup=kb)

@bot.message_handler(content_types=["document"])
def handle_document(message):
    uid = message.from_user.id
    doc = message.document
    if not doc.file_name.endswith(".py"):
        bot.reply_to(message, "❌ فقط ملفات Python (.py) مسموح بها.")
        return

    # Save file
    file_info = bot.get_file(doc.file_id)
    downloaded = bot.download_file(file_info.file_path)
    filename = doc.file_name
    # prevent overwrite: add suffix if exists
    base = filename
    counter = 1
    while os.path.exists(base):
        base = f"{os.path.splitext(filename)[0]}_{counter}.py"
        counter += 1
    with open(base, "wb") as f:
        f.write(downloaded)

    # persist meta
    files_meta[base] = {"uploader_id": uid, "uploaded_at": message.date, "running": False, "pid": None}
    stats["uploads"] = stats.get("uploads",0) + 1
    save_json(FILES_META, files_meta)
    save_json(STATS_FILE, stats)

    bot.reply_to(message, f"✅ تم رفع الملف: {base}\nيمكنك الآن تشغيله من زر تشغيل أو عبر لوحة الأدمن إذا كانت لديك صلاحيات.")

# -------------------------
# Callback handler for inline buttons
# -------------------------
@bot.callback_query_handler(func=lambda call: True)
def cb_handler(call):
    cid = call.message.chat.id
    uid = call.from_user.id
    data = call.data

    try:
        if data == "upload":
            bot.send_message(cid, "📤 أرسل الملف (.py) الآن كمستند.")
        elif data == "my_files":
            files = [f for f,meta in files_meta.items() if meta.get("uploader_id")==uid]
            if not files:
                bot.send_message(cid, "لا يوجد لديك ملفات مرفوعة.")
                return
            kb = types.InlineKeyboardMarkup()
            for f in files:
                kb.add(types.InlineKeyboardButton(f"تشغيل ▶️ {f}", callback_data=f"run__{f}"),
                       types.InlineKeyboardButton(f"حذف 🗑 {f}", callback_data=f"del__{f}"))
            bot.send_message(cid, "ملفاتك:", reply_markup=kb)
        elif data.startswith("run__"):
            if not is_admin(uid):
                bot.answer_callback_query(call.id, "❌ هذه الميزة للأدمن فقط.", show_alert=True)
                return
            filename = data.split("run__")[1]
            run_file(cid, filename)
        elif data.startswith("del__"):
            filename = data.split("del__")[1]
            # allow uploader or admin
            if files_meta.get(filename,{}).get("uploader_id") != uid and not is_admin(uid):
                bot.answer_callback_query(call.id, "❌ لا يمكنك حذف هذا الملف.", show_alert=True)
                return
            delete_file(cid, filename)
        elif data == "admin_panel":
            if not is_admin(uid):
                bot.answer_callback_query(call.id, "❌ أنت لست أدمن.", show_alert=True)
                return
            show_admin_panel(cid)
        elif data == "list_files":
            files = list_user_files()
            if not files:
                bot.send_message(cid, "لا توجد ملفات في المجلد.")
                return
            kb = types.InlineKeyboardMarkup(row_width=1)
            for f in files:
                kb.add(types.InlineKeyboardButton(f"{f}", callback_data=f"file_info__{f}"))
            bot.send_message(cid, "قائمة الملفات:", reply_markup=kb)
        elif data.startswith("file_info__"):
            fname = data.split("file_info__")[1]
            meta = files_meta.get(fname, {})
            text = f"اسم: {fname}\nمُحمّل بواسطة: {meta.get('uploader_id')}\nمشغّل: {meta.get('running')}\nPID: {meta.get('pid')}"
            kb = types.InlineKeyboardMarkup()
            if is_admin(uid):
                kb.add(types.InlineKeyboardButton("تشغيل ▶️", callback_data=f"adm_run__{fname}"),
                       types.InlineKeyboardButton("إيقاف ⏸", callback_data=f"adm_stop__{fname}"))
                kb.add(types.InlineKeyboardButton("حذف 🗑", callback_data=f"adm_del__{fname}"))
            bot.send_message(cid, text, reply_markup=kb)
        elif data.startswith("adm_run__"):
            if not is_admin(uid):
                bot.answer_callback_query(call.id, "❌ فقط الأدمن يمكنه.", show_alert=True); return
            fname = data.split("adm_run__")[1]
            run_file(cid, fname)
        elif data.startswith("adm_stop__"):
            if not is_admin(uid):
                bot.answer_callback_query(call.id, "❌ فقط الأدمن يمكنه.", show_alert=True); return
            fname = data.split("adm_stop__")[1]
            stop_file(cid, fname)
        elif data.startswith("adm_del__"):
            if not is_admin(uid):
                bot.answer_callback_query(call.id, "❌ فقط الأدمن يمكنه.", show_alert=True); return
            fname = data.split("adm_del__")[1]
            delete_file(cid, fname)
        elif data == "add_admin":
            bot.send_message(cid, "أرسل آيدي المستخدم الذي تريد ترقيته لأدمن (رقم فقط).")
            bot.register_next_step_handler(call.message, process_add_admin)
        elif data == "remove_admin":
            kb = types.InlineKeyboardMarkup()
            for a in admins:
                if a != OWNER_ID:
                    kb.add(types.InlineKeyboardButton(str(a), callback_data=f"remadm__{a}"))
            bot.send_message(cid, "اختر أدمن للحذف:", reply_markup=kb)
        elif data.startswith("remadm__"):
            if not is_admin(uid) or uid != OWNER_ID:
                bot.answer_callback_query(call.id, "❌ فقط المالك يمكنه حذف أدمن.", show_alert=True); return
            adm = int(data.split("remadm__")[1])
            if adm in admins:
                admins.remove(adm)
                save_json(ADMINS_FILE, admins)
                bot.send_message(cid, f"✅ تم حذف الأدمن {adm}")
        elif data == "view_stats":
            st = stats
            text = f"📊 إحصائيات:\nUploads: {st.get('uploads',0)}\nRuns: {st.get('runs',0)}\nDeletes: {st.get('deletes',0)}\nTotal files: {len(files_meta)}"
            bot.send_message(cid, text)
        elif data == "set_pin":
            if uid != OWNER_ID:
                bot.answer_callback_query(call.id, "❌ فقط المالك يمكنه تعيين PIN.", show_alert=True); return
            bot.send_message(cid, "أرسل الـ PIN الجديد (أرقام/حروف):")
            bot.register_next_step_handler(call.message, process_set_pin)
        elif data == "admin_login":
            bot.send_message(cid, "أدخل الـ PIN للدخول كأدمن:")
            bot.register_next_step_handler(call.message, process_admin_login)
        elif data == "broadcast":
            if not is_admin(uid):
                bot.answer_callback_query(call.id, "❌ فقط الأدمن يمكنه.", show_alert=True); return
            bot.send_message(cid, "أرسل الرسالة التي تريد إرسالها للمستخدمين (سيتم توزيعها على كل الراسلين للبوت).")
            bot.register_next_step_handler(call.message, process_broadcast)
        else:
            bot.answer_callback_query(call.id, "تم.", show_alert=False)
    except Exception as e:
        bot.send_message(cid, f"❌ خطأ داخلي: {e}")

# -------------------------
# Admin functions
# -------------------------
def show_admin_panel(chat_id):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("قائمة الملفات 📁", callback_data="list_files"),
        types.InlineKeyboardButton("عرض الإحصائيات 📊", callback_data="view_stats"),
        types.InlineKeyboardButton("إضافة أدمن ➕", callback_data="add_admin"),
        types.InlineKeyboardButton("حذف أدمن ➖", callback_data="remove_admin"),
        types.InlineKeyboardButton("تعيين PIN 🔐", callback_data="set_pin"),
        types.InlineKeyboardButton("دخول بالأدمن PIN 🔑", callback_data="admin_login"),
        types.InlineKeyboardButton("بث رسالة 📢", callback_data="broadcast")
    )
    bot.send_message(chat_id, "🔧 لوحة تحكم الأدمن:", reply_markup=kb)

def process_add_admin(message):
    try:
        uid_text = message.text.strip()
        new_admin = int(uid_text)
        if new_admin in admins:
            bot.send_message(message.chat.id, "❌ هذا الشخص أدمن بالفعل.")
            return
        admins.append(new_admin)
        save_json(ADMINS_FILE, admins)
        bot.send_message(message.chat.id, f"✅ تم ترقية {new_admin} إلى أدمن.")
    except:
        bot.send_message(message.chat.id, "❌ آيدي غير صالح.")

def process_set_pin(message):
    pin = message.text.strip()
    admin_pin["pin"] = pin
    save_json(PIN_FILE, admin_pin)
    bot.send_message(message.chat.id, f"✅ تم تعيين PIN جديد.")

def process_admin_login(message):
    pin = message.text.strip()
    if admin_pin.get("pin") and pin == admin_pin.get("pin"):
        uid = message.from_user.id
        if uid not in admins:
            admins.append(uid)
            save_json(ADMINS_FILE, admins)
        bot.send_message(message.chat.id, "✅ تم تسجيل دخولك كأدمن.")
    else:
        bot.send_message(message.chat.id, "❌ PIN خاطئ.")

def process_broadcast(message):
    text = message.text
    # gather known users from files_meta uploaders
    user_ids = set()
    for meta in files_meta.values():
        if meta.get("uploader_id"):
            user_ids.add(meta["uploader_id"])
    # include admins
    for a in admins:
        user_ids.add(a)
    success = 0
    failed = 0
    for u in user_ids:
        try:
            bot.send_message(u, text)
            success += 1
        except:
            failed += 1
    bot.send_message(message.chat.id, f"✅ تم الإرسال لـ {success}. فشل {failed}.")

# -------------------------
# File operations
# -------------------------
def run_file(chat_id, filename):
    global running_process, running_file
    if not os.path.exists(filename):
        bot.send_message(chat_id, "❌ الملف غير موجود.")
        return
    if running_process is not None:
        bot.send_message(chat_id, f"⚠️ ملف قيد التشغيل: {running_file}")
        return
    try:
        proc = subprocess.Popen(["python3", filename])
        running_process = proc
        running_file = filename
        files_meta.setdefault(filename, {})["running"] = True
        files_meta[filename]["pid"] = proc.pid
        stats["runs"] = stats.get("runs",0) + 1
        save_json(FILES_META, files_meta)
        save_json(STATS_FILE, stats)
        bot.send_message(chat_id, f"✅ تم تشغيل {filename} (PID: {proc.pid})")
    except Exception as e:
        bot.send_message(chat_id, f"❌ فشل تشغيل الملف: {e}")

def stop_file(chat_id, filename):
    global running_process, running_file
    if running_process is None or running_file != filename:
        bot.send_message(chat_id, "❌ لا يوجد هذا الملف قيد التشغيل.")
        return
    try:
        running_process.terminate()
        pid = files_meta.get(filename,{}).get("pid")
        running_process = None
        running_file = None
        files_meta.setdefault(filename, {})["running"] = False
        files_meta[filename]["pid"] = None
        save_json(FILES_META, files_meta)
        bot.send_message(chat_id, f"✅ تم إيقاف {filename}")
    except Exception as e:
        bot.send_message(chat_id, f"❌ فشل الإيقاف: {e}")

def delete_file(chat_id, filename):
    try:
        if os.path.exists(filename):
            if files_meta.get(filename,{}).get("running"):
                bot.send_message(chat_id, "⚠️ الملف قيد التشغيل، أوقفه أولاً.")
                return
            os.remove(filename)
        if filename in files_meta:
            del files_meta[filename]
            stats["deletes"] = stats.get("deletes",0) + 1
            save_json(FILES_META, files_meta)
            save_json(STATS_FILE, stats)
        bot.send_message(chat_id, f"✅ تم حذف {filename}")
    except Exception as e:
        bot.send_message(chat_id, f"❌ فشل الحذف: {e}")

# -------------------------
# Fallback message handler
# -------------------------
@bot.message_handler(func=lambda m: True)
def fallback(m):
    text = m.text or ""
    if text.startswith("/setpin"):
        # only owner allowed via command
        if m.from_user.id != OWNER_ID:
            bot.reply_to(m, "❌ فقط المالك يمكنه تعيين PIN.")
            return
        parts = text.split(maxsplit=1)
        if len(parts) == 2:
            admin_pin["pin"] = parts[1].strip()
            save_json(PIN_FILE, admin_pin)
            bot.reply_to(m, "✅ تم تعيين PIN.")
        else:
            bot.reply_to(m, "❌ أرسل: /setpin 1234")
        return

    if text.startswith("/adminlogin"):
        bot.reply_to(m, "أرسل PIN للدخول كأدمن:")
        bot.register_next_step_handler(m, process_admin_login)
        return

    if text.startswith("/whoami"):
        bot.reply_to(m, f"آيديك: {m.from_user.id}\nأنت أدمن؟ {'نعم' if is_admin(m.from_user.id) else 'لا'}")
        return

    # help
    bot.reply_to(m, "استخدم الأزرار أو أرسل /start للبدء. للمطور: /setpin <pin>")

# -------------------------
# Entry point: DO NOT use polling here in production with webhook.
# Render / Heroku etc. will use gunicorn to run Flask app.
# -------------------------
if __name__ == "__main__":
    print("Run this file with webhook. Do NOT use polling in production.")
    # For local testing only:
    try:
        bot.remove_webhook()
        bot.set_webhook(url=f"https://your-app.onrender.com/{BOT_TOKEN}")
        print("Webhook set (local testing).")
    except Exception as e:
        print("Set webhook manually in production.", e)
