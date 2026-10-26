#!/usr/bin/env python3
# Fast Email Validator Bot — Live Dashboard & Stop Button
# pip install python-telegram-bot dnspython email-validator

import asyncio
import csv
import io
import os
import socket
import smtplib
import threading
import time
import dns.resolver
from concurrent.futures import ThreadPoolExecutor, as_completed
from email_validator import validate_email, EmailNotValidError
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.request import HTTPXRequest

# ==========================================
# CONFIGURATION
# ==========================================
BOT_TOKEN = "5741020078:AAHUloa0BhSSSjNT2UZrqoeL2F-I6O5uifQ"
ADMIN_ID = 5870967455  # ضع الآيدي بتاعك
FROM_ADDRESS = "verify@yourdomain.com"
SMTP_TIMEOUT = 6
MAX_WORKERS = 20

# ==========================================
# GLOBAL STATE
# ==========================================
IS_CHECKING = False
STOP_REQUESTED = False
mx_cache = {}
mx_cache_lock = threading.Lock()
stats = {"checked": 0, "valid": 0, "invalid": 0, "unknown": 0, "bad_syntax": 0}
total_emails = 0
start_time = 0

# ==========================================
# CORE CHECKER LOGIC
# ==========================================
def get_mx_host(domain):
    with mx_cache_lock:
        if domain in mx_cache:
            return mx_cache[domain]
    try:
        answers = dns.resolver.resolve(domain, "MX")
        records = sorted(answers, key=lambda r: r.preference)
        host = str(records[0].exchange).rstrip(".")
    except Exception:
        host = None
    with mx_cache_lock:
        mx_cache[domain] = host
    return host

def smtp_check(email, mx_host):
    global STOP_REQUESTED
    if STOP_REQUESTED:
        return "skipped (stopped)"
    try:
        server = smtplib.SMTP(timeout=SMTP_TIMEOUT)
        server.connect(mx_host)
        server.helo(server.local_hostname)
        server.mail(FROM_ADDRESS)
        code, _ = server.rcpt(email)
        server.quit()
        if code == 250:
            return "valid"
        elif code in (550, 551, 553):
            return "invalid"
        return "unknown"
    except (socket.timeout, smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError):
        return "unknown (blocked/timeout)"
    except Exception:
        return "unknown"

def check_one(email):
    global STOP_REQUESTED
    if STOP_REQUESTED:
        return None

    row = {"email": email, "syntax_valid": False, "domain": "", "has_mx": False, "smtp_result": "skipped", "notes": ""}
    try:
        valid = validate_email(email, check_deliverability=False)
        row["syntax_valid"] = True
        domain = valid.domain
    except EmailNotValidError as e:
        row["notes"] = str(e)
        return row

    row["domain"] = domain
    mx_host = get_mx_host(domain)
    row["has_mx"] = mx_host is not None

    if not mx_host:
        row["smtp_result"] = "invalid (no MX record)"
        return row

    result = smtp_check(email, mx_host)
    row["smtp_result"] = result
    return row

# ==========================================
# ASYNC BACKGROUND TASK
# ==========================================
async def process_emails(update: Update, context: ContextTypes.DEFAULT_TYPE, file_bytes: bytes):
    global IS_CHECKING, STOP_REQUESTED, stats, total_emails, start_time
    chat_id = update.effective_chat.id
    
    IS_CHECKING = True
    STOP_REQUESTED = False
    stats = {"checked": 0, "valid": 0, "invalid": 0, "unknown": 0, "bad_syntax": 0}
    
    try:
        text_data = file_bytes.decode('utf-8', errors='ignore')
        emails = [line.strip() for line in text_data.splitlines() if line.strip()]
    except Exception:
        await context.bot.send_message(chat_id=chat_id, text="❌ فشل في قراءة الملف.")
        IS_CHECKING = False
        return

    total_emails = len(emails)
    start_time = time.time()
    results = []
    
    stop_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🛑 إيقاف الفحص", callback_data="stop_check")]
    ])

    status_msg = await context.bot.send_message(
        chat_id=chat_id,
        text=f"⏳ **جاري تجهيز الفحص...**\n\n📁 الإجمالي: `{total_emails}`",
        reply_markup=stop_kb,
        parse_mode="Markdown"
    )

    # تنفيذ الفحص
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(check_one, email): email for email in emails}
        
        last_update = time.time()
        for future in as_completed(futures):
            if STOP_REQUESTED:
                break
                
            result = future.result()
            if result is not None:
                results.append(result)
                
                # تحديث الإحصائيات
                if not result["syntax_valid"]:
                    stats["bad_syntax"] += 1
                elif not result["has_mx"]:
                    stats["invalid"] += 1
                else:
                    res = result["smtp_result"]
                    if res == "valid":
                        stats["valid"] += 1
                    elif res == "invalid":
                        stats["invalid"] += 1
                    else:
                        stats["unknown"] += 1
                
                stats["checked"] += 1

            # تحديث واجهة تلجرام كل 2 ثانية
            if time.time() - last_update > 2.0:
                elapsed = max(time.time() - start_time, 0.001)
                rate = stats["checked"] / elapsed
                remaining = total_emails - stats["checked"]
                eta = remaining / rate if rate > 0 else 0
                
                dash = (
                    f"⏳ **جاري الفحص...**\n\n"
                    f"📁 الإجمالي: `{total_emails}`\n"
                    f"🔍 تم الفحص: `{stats['checked']}` ({(stats['checked']/total_emails*100):.1f}%)\n"
                    f"✅ صحيح: `{stats['valid']}`\n"
                    f"❌ خاطئ: `{stats['invalid']}`\n"
                    f"⚠️ غير معروف: `{stats['unknown']}`\n"
                    f"⚡ السرعة: `{rate:.1f}` إيميل/ثانية\n"
                    f"⏱️ الوقت المتبقي: `{eta:.0f}` ثانية"
                )
                
                try:
                    await status_msg.edit_text(dash, reply_markup=stop_kb, parse_mode="Markdown")
                except Exception:
                    pass # تجاهل أخطاء الفلود
                
                last_update = time.time()

    # إنشاء ملف CSV النهائي
    out_file = io.BytesIO()
    out_file.write("email,syntax_valid,domain,has_mx,smtp_result,notes\n".encode('utf-8'))
    for r in results:
        line = f"{r['email']},{r['syntax_valid']},{r['domain']},{r['has_mx']},{r['smtp_result']},{r['notes']}\n"
        out_file.write(line.encode('utf-8'))
    out_file.seek(0)
    out_file.name = "verify_results.csv"

    # رسالة النهاية
    if STOP_REQUESTED:
        final_text = f"🛑 **تم إيقاف الفحص يدوياً!**\n\n🔍 تم فحص: `{stats['checked']}` من أصل `{total_emails}`."
    else:
        final_text = f"🏁 **تم الانتهاء!**\n\n🔍 تم فحص: `{total_emails}` إيميل."

    await status_msg.edit_text(final_text, parse_mode="Markdown")
    
    # إرسال الملف
    await context.bot.send_document(
        chat_id=chat_id,
        document=out_file,
        filename="verify_results.csv",
        caption=f"✅ **النتائج:**\nصحيح: {stats['valid']} | خاطئ: {stats['invalid']} | غير معروف: {stats['unknown']}"
    )
    
    IS_CHECKING = False

# ==========================================
# TELEGRAM HANDLERS
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    text = (
        "👁️ **Email Validator Bot**\n\n"
        "أرسل ملف `.txt` فيه قائمة إيميلات، وسأقوم بفحصهم (Syntax + MX + SMTP).\n"
        "ستظهر لك لوحة التحكم مباشرة في الشات مع زر إيقاف."
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global IS_CHECKING
    if update.effective_user.id != ADMIN_ID:
        return
        
    if IS_CHECKING:
        await update.message.reply_text("⚠️ يوجد فحص قيد التشغيل حالياً. أوقفه أولاً.")
        return
        
    doc = update.message.document
    if not doc.file_name.endswith('.txt'):
        await update.message.reply_text("❌ أرسل ملف `.txt` فقط.")
        return

    msg = await update.message.reply_text("⏳ جاري تحميل الملف...")
    
    try:
        new_file = await context.bot.get_file(doc.file_id)
        file_bytes = await new_file.download_as_bytearray()
    except Exception as e:
        await msg.edit_text(f"❌ فشل التحميل: {e}")
        return

    await msg.delete()
    asyncio.create_task(process_emails(update, context, bytes(file_bytes)))

async def stop_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global STOP_REQUESTED
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != ADMIN_ID:
        return

    if IS_CHECKING:
        STOP_REQUESTED = True
        await query.answer("🛑 جاري إيقاف الفحص...", show_alert=False)
    else:
        await query.answer("⚠️ لا يوجد فحص قيد التشغيل.", show_alert=True)

# ==========================================
# LAUNCHER
# ==========================================
def main():
    os.system('clear')
    print("[*] Email Validator Bot is starting...")
    print("[*] Waiting for files via Telegram...")
    
    request = HTTPXRequest(connect_timeout=60.0, read_timeout=60.0, write_timeout=60.0, pool_timeout=60.0)
    app = Application.builder().token(BOT_TOKEN).request(request).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(CallbackQueryHandler(stop_check, pattern="^stop_check$"))
    
    app.run_polling(close_loop=False, bootstrap_retries=5)

if __name__ == "__main__":
    main()
