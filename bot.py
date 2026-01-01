import os
import re
import random
import asyncio
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

import httpx

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)

from telegram.request import HTTPXRequest

# =====================================================
# KONFIGURASI
# =====================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN")
LOG_BOT_TOKEN = os.environ.get("LOG_BOT_TOKEN")
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "0"))
BOT_NAME = os.environ.get("BOT_NAME", "VETERAN_BOT")

SHEERID_BASE_URL = "https://services.sheerid.com"
STEP_TIMEOUT = 300
EMAIL_CHECK_INTERVAL = 10
EMAIL_CHECK_TIMEOUT = 300

# Custom Tempmail Worker URL
CUSTOM_MAIL_API = "https://bot-emails.pilarjalar.workers.dev"
CUSTOM_DOMAIN = "zzzz.biz.id"

# Military organizations
MIL_ORGS = {
    "Army": {"id": 4070, "name": "Army"},
    "Air Force": {"id": 4073, "name": "Air Force"},
    "Navy": {"id": 4072, "name": "Navy"},
    "Marine Corps": {"id": 4071, "name": "Marine Corps"},
    "Coast Guard": {"id": 4074, "name": "Coast Guard"},
    "Space Force": {"id": 4544268, "name": "Space Force"},
}

ORG_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("Army", callback_data="org_Army"),
     InlineKeyboardButton("Air Force", callback_data="org_Air Force")],
    [InlineKeyboardButton("Navy", callback_data="org_Navy"),
     InlineKeyboardButton("Marine Corps", callback_data="org_Marine Corps")],
    [InlineKeyboardButton("Coast Guard", callback_data="org_Coast Guard"),
     InlineKeyboardButton("Space Force", callback_data="org_Space Force")],
])

STATUS_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("Veteran", callback_data="status_VETERAN")],
    [InlineKeyboardButton("Retired", callback_data="status_RETIRED")],
    [InlineKeyboardButton("Active Duty", callback_data="status_ACTIVE_DUTY")],
])

LOG_API_URL = (
    f"https://api.telegram.org/bot{LOG_BOT_TOKEN}/sendMessage"
    if LOG_BOT_TOKEN
    else None
)

# =====================================================
# STATE CONVERSATION
# =====================================================

(
    V_URL,
    V_STATUS,
    V_ORG,
    V_NAME,
    V_BIRTH,
    V_DISCHARGE,
    V_CONFIRM,
) = range(7)

v_user_data = {}
temp_email_storage = {}

# =====================================================
# CUSTOM TEMPMAIL API FUNCTIONS (KV Worker)
# =====================================================

async def create_temp_email() -> dict:
    """Generate email dengan custom domain"""
    try:
        username = f"veteran{random.randint(1000, 9999)}{random.randint(100, 999)}"
        email = f"{username}@{CUSTOM_DOMAIN}"
        print(f"✅ Generated custom email: {email}")
        return {
            "success": True,
            "email": email,
            "token": email
        }
    except Exception as e:
        print(f"❌ Error generating email: {e}")
        return {"success": False, "message": str(e)}

async def check_inbox(email: str) -> list:
    """Check inbox via custom worker"""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{CUSTOM_MAIL_API}/emails/{email}")
            if resp.status_code == 200:
                data = resp.json()
                return data.get("emails", [])
            return []
    except Exception as e:
        print(f"❌ Error checking inbox: {e}")
        return []

async def get_message_content(email: str, message_id: str) -> dict:
    """Get full message content"""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{CUSTOM_MAIL_API}/inbox/{message_id}")
            if resp.status_code == 200:
                return resp.json()
            return {}
    except Exception as e:
        print(f"❌ Error getting message: {e}")
        return {}

async def delete_email_inbox(email: str) -> bool:
    """Delete email inbox after verification done"""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.delete(f"{CUSTOM_MAIL_API}/emails/{email}")
            return resp.status_code == 200
    except Exception as e:
        print(f"❌ Error deleting inbox: {e}")
        return False

def extract_verification_link(text: str) -> str:
    """Extract complete SheerID verification link from email"""
    patterns = [
        r'(https://services\.sheerid\.com/verify/[^\s\)]+\?[^\s\)]*emailToken=[^\s\)]+)',
        r'(https://services\.sheerid\.com/verify/[^\s\)]+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            link = match.group(1)
            link = re.sub(r'[<>"\'\)]$', '', link)
            print(f"🔗 Extracted complete link: {link}")
            return link
    return None

def extract_email_token_only(text: str) -> str:
    """Extract emailToken parameter dari text email"""
    match = re.search(r'emailToken=([A-Za-z0-9]+)', text, re.IGNORECASE)
    if match:
        token = match.group(1)
        print(f"🎫 Extracted emailToken: {token}")
        return token
    match = re.search(r'[?&]token=([A-Za-z0-9]+)', text, re.IGNORECASE)
    if match:
        token = match.group(1)
        print(f"🎫 Extracted token (alternative): {token}")
        return token
    return None

def build_complete_verification_link(original_url: str, verification_id: str, email_token: str) -> str:
    """Build complete verification link dari original URL + emailToken"""
    base_url = original_url.split('?')[0]
    complete_link = f"{base_url}?verificationId={verification_id}&emailToken={email_token}"
    print(f"🔧 Built complete link: {complete_link}")
    return complete_link

async def click_verification_link(verification_url: str) -> dict:
    """Auto-click verification link untuk konfirmasi"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
        }

        async with httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers=headers
        ) as client:
            print(f"🖱️ Clicking verification link: {verification_url}")
            response = await client.get(verification_url)
            print(f"📊 Response status: {response.status_code}")
            print(f"📍 Final URL: {response.url}")

            if response.status_code == 200:
                response_text = response.text.lower()
                success_indicators = [
                    'verified',
                    'success',
                    'confirmed',
                    'thank you',
                    'complete',
                    'approved'
                ]

                is_success = any(indicator in response_text for indicator in success_indicators)

                return {
                    "success": True,
                    "clicked": True,
                    "status_code": response.status_code,
                    "final_url": str(response.url),
                    "verified": is_success,
                    "response_snippet": response_text[:500]
                }
            else:
                return {
                    "success": False,
                    "clicked": True,
                    "status_code": response.status_code,
                    "message": f"Non-200 response: {response.status_code}"
                }
    except httpx.TimeoutException:
        return {"success": False, "clicked": False, "message": "Timeout clicking link"}
    except Exception as e:
        print(f"❌ Error clicking link: {e}")
        return {"success": False, "clicked": False, "message": str(e)}

# =====================================================
# EMAIL MONITORING JOB
# =====================================================

async def monitor_email_job(context: ContextTypes.DEFAULT_TYPE):
    """Monitor inbox dan auto-click verification link"""
    job = context.job
    user_id = job.user_id
    chat_id = job.chat_id

    if user_id not in temp_email_storage:
        print(f"⚠️ No email storage for user {user_id}")
        return

    email_data = temp_email_storage[user_id]
    check_count = email_data.get("check_count", 0)
    email_data["check_count"] = check_count + 1

    if check_count >= 30:
        await context.bot.send_message(
            chat_id=chat_id,
            text=( 
                "⏰ *Email monitoring timeout*\n\n"
                "Tidak ada email verifikasi masuk dalam 5 menit.\n"
                f"📧 Email: `{email_data.get('email')}`\n\n"
                "❌ *Verification FAILED*\n\n"
                "Kemungkinan:\n"
                "• Data tidak valid\n"
                "• SheerID butuh document upload\n"
                "• Email belum dikirim\n\n"
                "Coba lagi dengan /veteran"
            ),
            parse_mode="Markdown"
        )
        await delete_email_inbox(email_data.get("email"))
        job.schedule_removal()
        temp_email_storage.pop(user_id, None)
        return

    try:
        email = email_data.get("email")
        messages = await check_inbox(email)

        if not messages:
            print(f"📭 No messages yet for {email} (check #{check_count})")
            return

        print(f"📬 Found {len(messages)} messages for {email}")

        for msg in messages:
            msg_from = msg.get("from", "")
            subject = msg.get("subject", "")
            msg_id = msg.get("id")

            print(f"📨 From: {msg_from}, Subject: {subject}")

            if "sheerid" in msg_from.lower() or "verif" in subject.lower():
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=( 
                        "📧 *Email verifikasi diterima!*\n\n"
                        f"From: `{msg_from}`\n"
                        f"Subject: `{subject}`\n\n"
                        "🔄 Mengekstrak verification link..."
                    ),
                    parse_mode="Markdown"
                )

                full_msg = await get_message_content(email, msg_id)
                body_text = full_msg.get("text", "")
                print(f"📄 Email body (first 300 chars): {body_text[:300]}")

                verification_link = extract_verification_link(body_text)

                if not verification_link or "emailToken=" not in verification_link:
                    print("⚠️ Link tidak lengkap, ekstrak emailToken...")
                    email_token = extract_email_token_only(body_text)

                    if email_token:
                        verification_id = email_data.get("verification_id")
                        original_url = email_data.get("original_url")
                        verification_link = build_complete_verification_link(
                            original_url,
                            verification_id,
                            email_token
                        )

                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=(
                                "🔧 *Link tidak lengkap di email!*\n\n"
                                f"✅ emailToken ditemukan: `{email_token}`\n"
                                "🔗 Building complete verification link...\n\n"
                                f"`{verification_link[:80]}...`"
                            ),
                            parse_mode="Markdown"
                        )
                    else:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=( 
                                "❌ *Gagal ekstrak emailToken*\n\n"
                                "Email dari SheerID tidak mengandung token.\n"
                                f"Body preview:\n`{body_text[:200]}`\n\n"
                                "Coba manual atau /veteran untuk restart."
                            ),
                            parse_mode="Markdown"
                        )
                        await delete_email_inbox(email)
                        job.schedule_removal()
                        temp_email_storage.pop(user_id, None)
                        return

                if verification_link:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=( 
                            "🔗 *Verification link ready!*\n\n"
                            "🖱️ Bot auto-clicking untuk konfirmasi...\n"
                            "⏳ Tunggu sebentar..."
                        ),
                        parse_mode="Markdown"
                    )

                    click_result = await click_verification_link(verification_link)

                    if click_result.get("success") and click_result.get("clicked"):
                        await asyncio.sleep(3)
                        verification_id = email_data.get("verification_id")
                        status_check = await check_sheerid_status(verification_id)
                        final_status = status_check.get("status", "unknown")

                        is_verified = (
                            final_status == "success" or
                            click_result.get("verified") or
                            "success" in str(click_result.get("final_url", "")).lower()
                        )

                        if is_verified:
                            await context.bot.send_message(
                                chat_id=chat_id,
                                text=( 
                                    "🎉 *VERIFICATION SUCCESS!*\n\n"
                                    "✅ *Status: APPROVED / VERIFIED*\n\n"
                                    f"📧 Email: `{email}`\n"
                                    f"🎯 SheerID Status: `{final_status}`\n"
                                    f"📊 HTTP Status: `{click_result.get('status_code')}`\n\n"
                                    "🔗 Final URL:\n"
                                    f"`{click_result.get('final_url', 'N/A')[:100]}...`\n\n"
                                    "✨ *Verifikasi veteran berhasil!*\n"
                                    "Sekarang kamu bisa gunakan discount/offer."
                                ),
                                parse_mode="Markdown"
                            )

                            await send_log(
                                f"✅ VERIFICATION SUCCESS ({BOT_NAME})\n\n"
                                f"User ID: {user_id}\n"
                                f"Email: {email}\n"
                                f"Final Status: {final_status}\n"
                                f"Link: {verification_link}"
                            )
                        else:
                            await context.bot.send_message(
                                chat_id=chat_id,
                                text=(
                                    "⚠️ *VERIFICATION CLICKED - PENDING*\n\n"
                                    "🔄 *Status: NOT YET APPROVED*\n\n"
                                    f"📧 Email: `{email}`\n"
                                    f"🎯 SheerID Status: `{final_status}`\n"
                                    f"📊 HTTP Status: `{click_result.get('status_code')}`\n\n"
                                    "📋 *Kemungkinan:*\n"
                                    "• SheerID butuh document upload (DD214)\n"
                                    "• Manual review diperlukan\n"
                                    "• Data tidak cocok database\n\n"
                                    "💡 Cek browser untuk status lengkap."
                                ),
                                parse_mode="Markdown"
                            )

                            await send_log(
                                f"⚠️ VERIFICATION PENDING ({BOT_NAME})\n\n"
                                f"User ID: {user_id}\n"
                                f"Email: {email}\n"
                                f"Final Status: {final_status}"
                            )
                    else:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=( 
                                "❌ *AUTO-CLICK FAILED*\n\n"
                                f"Error: {click_result.get('message', 'Unknown')}\n\n"
                                f"🔗 Link: `{verification_link[:100]}...`\n\n"
                                "Coba klik manual atau /veteran restart."
                            ),
                            parse_mode="Markdown"
                        )

                    await delete_email_inbox(email)
                    job.schedule_removal()
                    temp_email_storage.pop(user_id, None)
                    return

    except Exception as e:
        print(f"❌ Error in monitor_email_job: {e}")

def start_email_monitoring(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int):
    """Start background job to monitor email"""
    if context.job_queue is None:
        print("⚠️ JobQueue is None")
        return

    job_name = f"email_monitor_{user_id}"
    current_jobs = context.job_queue.get_jobs_by_name(job_name)
    for job in current_jobs:
        job.schedule_removal()

    context.job_queue.run_repeating(
        monitor_email_job,
        interval=EMAIL_CHECK_INTERVAL,
        first=EMAIL_CHECK_INTERVAL,
        chat_id=chat_id,
        user_id=user_id,
        name=job_name
    )

    print(f"🔄 Started email monitoring for user {user_id}")
