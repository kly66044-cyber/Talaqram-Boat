#!/usr/bin/env python3
# Email Validator Bot — Bulletproof Async Engine
# pip install "python-telegram-bot>=20.0" dnspython email-validator

import asyncio
import csv
import io
import os
import socket
import smtplib
import threading
import time
import dns.resolver
from concurrent.futures import ThreadPoolExecutor
from email_validator import validate_email, EmailNotValidError
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.request import HTTPXRequest
from telegram.error import RetryAfter

# ==========================================
# CONFIGURATION
# ==========================================
BOT_TOKEN = "5741020078:AAHUloa0BhSSSjNT2UZrqoeL2F-I6O5uifQ"
ADMIN_ID = 5870967455 
FROM_ADDRESS = "tjrbhtht888@gmail.com" # يفضل تخليها إيميل حقيقي لو بتستخدم SMTP
SMTP_TIMEOUT = 5
MAX_WORKERS = 50 

# ==========================================
# GLOBAL STATE
# ==========================================
IS_CHECKING = False
STOP_REQUESTED = False
mx_cache = {}
stats = {"checked": 0, "valid": 0, "invalid": 0, "unknown": 0, "bad_syntax": 0}

# ==========================================
# CORE CHECKER LOGIC
# ==========================================
def get_mx_host(domain):
    if domain in mx_cache:
        return mx_cache[domain]
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=5)
        records = sorted(answers, key=lambda r: r.preference)
        host = str(records[0].exchange).rstrip(".")
    except Exception:
        host = None
    mx_cache[domain] = host
    return host

def check_one(email):
    global STOP_REQUESTED
    if STOP_REQUESTED:
        return {"email": email, "syntax_valid": False, "domain": "", "has_mx": False, "smtp_result": "stopped", "notes": ""}

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

    # SMTP Check
    try:
        server = smtplib.SMTP(timeout=SMTP_TIMEOUT)
        server.connect(mx_host, 25)
        server.helo(server.local_hostname)
        server.mail(FROM_ADDRESS)
        code, _ = server.rcpt(email)
        server.quit()
        
        if code == 250:
            row["smtp_result"] = "valid"
        elif code in (550, 551, 553):
            row["smtp_result"] = "invalid"
        else:
            row["smtp_result"] = f"unknown (code {code})"
    except Exception as e:
        row["smtp_result"] = f"unknown ({type(e).__name__})"
    
    return row

# ==========================================
# ASYNC BACKGROUND TASK
# ==========================================
async def process_emails(update: Update, context: ContextTypes.DEFAULT_TYPE, file_bytes: bytes):
    global IS_CHECKING, STOP_REQUESTED, stats
    chat_id = update.effective_chat.id
    
    IS_CHECKING = True
    STOP_REQUESTED = False
    stats = {"checked": 0, "valid": 0, "invalid": 0, "unknown": 0, "bad_syntax": 0}
    
    try:
        text_data = file_bytes.decode('utf-8', errors='ignore')
        emails = list(set([line.strip() for line in text_data.splitlines() if line.strip()]))
    except Exception:
        await context.bot.send_message(chat_id=chat_id, text="❌ فشل في قراءة الملف.")
        IS_CHECKING = False
        return

    total_emails = len(emails)
    if total_emails == 0:
        await context.bot.send_message(chat_id=chat_id, text="⚠️ الملف فاضي.")
        IS_CHECKING = False
        return

    stop_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🛑 إيقاف الفحص", callback_data="stop_check")]
    ])

    status_msg = await context.bot.send_message(
        chat_id=chat_id,
        text=f"⏳ **بدء الفحص...**\n\n📁 الإجمالي: `{total_emails}`",
        reply_markup=stop_kb,
        parse_mode="Markdown"
    )

    results = []
    start_time = time.time()
    loop = asyncio.get_running_loop()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        # نرسل المهام لـ Threads
        tasks = [loop.run_in_executor(pool, check_one, email) for email in emails]
        
        # نستقبل النتائج أول بأول
        for i, future in enumerate(asyncio.as_completed(tasks)):
            if STOP_REQUESTED:
                break
                
            try:
                result = await future
            except Exception as e:
                continue
                
            if result is None or result["smtp_result"] == "stopped":
                continue

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

            # تحديث واجهة تلجرام كل 5 إيميلات عشان ما نضربش حظر
            if stats["checked"] % 5 == 0 or stats["checked"] == total_emails:
                elapsed = max(time.time() - start_time, 0.1)
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
                except RetryAfter as e:
                    await asyncio.sleep(e.retry_after)
                except Exception:
                    pass

    # رسالة النهاية
    if STOP_REQUESTED:
        final_text = f"🛑 **تم إيقاف الفحص يدوياً!**\n\n🔍 تم فحص: `{stats['checked']}` من أصل `{total_emails}`."
    else:
        final_text = f"🏁 **تم الانتهاء!**\n\n🔍 تم فحص: `{total_emails}` إيميل."

    try:
        await status_msg.edit_text(final_text, parse_mode="Markdown")
    except:
        pass
    
    # تجهيز الملف
    if results:
        out_file = io.BytesIO()
        out_file.write("email,syntax_valid,domain,has_mx,smtp_result,notes\n".encode('utf-8'))
        for r in results:
            line = f"{r['email']},{r['syntax_valid']},{r['domain']},{r['has_mx']},{r['smtp_result']},{r['notes']}\n"
            out_file.write(line.encode('utf-8'))
        out_file.seek(0)
        out_file.name = "verify_results.csv"
        
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
    await update.message.reply_text(
        "👁️ **Email Validator Bot**\n\n"
        "أرسل ملف `.txt` فيه إيميلات، وسأفحصهم (Syntax + MX + SMTP).\n"
        "البوت سريع ومقاوم للتهنيج."
    )

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or IS_CHECKING:
        return
        
    doc = update.message.document
    if not doc.file_name.endswith('.txt'):
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
    
    request = HTTPXRequest(connect_timeout=60.0, read_timeout=60.0, write_timeout=60.0, pool_timeout=60.0)
    app = Application.builder().token(BOT_TOKEN).request(request).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(CallbackQueryHandler(stop_check, pattern="^stop_check$"))
    
    app.run_polling(close_loop=False, bootstrap_retries=5)

if __name__ == "__main__":
    main()
