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
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

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

# Rate limiting untuk avoid 429 error
REQUEST_DELAY = 2  # Delay 2 detik antar request
MAX_RETRIES = 3
RETRY_BACKOFF = 5  # Initial backoff 5 detik

# Custom Tempmail Worker URL
CUSTOM_MAIL_API = "https://bot-emails.pilarjalar.workers.dev"
CUSTOM_DOMAIN = "zzzz.biz.id"

# =====================================================
# MILITARY ORGANIZATIONS - BERDASARKAN STATUS
# =====================================================

# Organizations untuk Active Duty & Veteran/Retiree
MIL_ORGS_ACTIVE_VETERAN = {
    "Army": {"id": 4070, "name": "Army"},
    "Air Force": {"id": 4073, "name": "Air Force"},
    "Navy": {"id": 4072, "name": "Navy"},
    "Marine Corps": {"id": 4071, "name": "Marine Corps"},
    "Coast Guard": {"id": 4074, "name": "Coast Guard"},
    "Space Force": {"id": 4544268, "name": "Space Force"},
}

# Organizations untuk Reservist or National Guard
MIL_ORGS_RESERVIST = {
    "Army National Guard": {"id": 4075, "name": "Army National Guard"},
    "Army Reserve": {"id": 4076, "name": "Army Reserve"},
    "Air National Guard": {"id": 4079, "name": "Air National Guard"},
    "Air Force Reserve": {"id": 4080, "name": "Air Force Reserve"},
    "Navy Reserve": {"id": 4078, "name": "Navy Reserve"},
    "Marine Corps Forces Reserve": {"id": 4077, "name": "Marine Corps Forces Reserve"},
    "Coast Guard Reserve": {"id": 4081, "name": "Coast Guard Reserve"},
}

# Status keyboard - sesuai form SheerID asli
STATUS_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("Active Duty", callback_data="status_ACTIVE_DUTY")],
    [InlineKeyboardButton("Military Veteran or Retiree", callback_data="status_VETERAN")],
    [InlineKeyboardButton("Reservist or National Guard", callback_data="status_RESERVIST")],
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
# HELPER: DYNAMIC ORGANIZATION KEYBOARD
# =====================================================

def get_org_keyboard(status: str) -> InlineKeyboardMarkup:
    """Generate organization keyboard berdasarkan status"""

    if status == "RESERVIST":
        # Gunakan organizations untuk Reservist/Guard
        orgs = MIL_ORGS_RESERVIST
        buttons = [
            [InlineKeyboardButton("Army National Guard", callback_data="org_Army National Guard")],
            [InlineKeyboardButton("Army Reserve", callback_data="org_Army Reserve")],
            [InlineKeyboardButton("Air National Guard", callback_data="org_Air National Guard")],
            [InlineKeyboardButton("Air Force Reserve", callback_data="org_Air Force Reserve")],
            [InlineKeyboardButton("Navy Reserve", callback_data="org_Navy Reserve")],
            [InlineKeyboardButton("Marine Corps Forces Reserve", callback_data="org_Marine Corps Forces Reserve")],
            [InlineKeyboardButton("Coast Guard Reserve", callback_data="org_Coast Guard Reserve")],
        ]
    else:
        # Active Duty & Veteran gunakan org regular
        buttons = [
            [InlineKeyboardButton("Army", callback_data="org_Army"),
             InlineKeyboardButton("Air Force", callback_data="org_Air Force")],
            [InlineKeyboardButton("Navy", callback_data="org_Navy"),
             InlineKeyboardButton("Marine Corps", callback_data="org_Marine Corps")],
            [InlineKeyboardButton("Coast Guard", callback_data="org_Coast Guard"),
             InlineKeyboardButton("Space Force", callback_data="org_Space Force")],
        ]

    return InlineKeyboardMarkup(buttons)

# =====================================================
# CUSTOM TEMPMAIL API FUNCTIONS
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

# =====================================================
# EMAIL LINK EXTRACTION
# =====================================================

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

# =====================================================
# BROWSER AUTOMATION - REAL CLICK!
# =====================================================

async def click_verification_link_with_browser(verification_url: str) -> dict:
    """
    🎯 BROWSER AUTOMATION: Buka browser Chromium dan klik link seperti manusia!
    """
    browser = None

    try:
        print(f"🌐 Starting browser automation for: {verification_url}")

        async with async_playwright() as p:
            # Launch Chromium browser
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-blink-features=AutomationControlled',
                    '--disable-gpu',
                    '--no-first-run',
                    '--no-zygote',
                    '--single-process',
                    '--disable-background-networking',
                ]
            )

            # Create browser context dengan user agent real
            context = await browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                viewport={'width': 1920, 'height': 1080},
                locale='en-US',
                timezone_id='America/New_York'
            )

            # Create new page
            page = await context.new_page()

            print(f"🖱️ Browser opened - navigating to verification link...")

            # Navigate ke URL - INI YANG BENAR-BENAR KLIK!
            response = await page.goto(
                verification_url,
                wait_until='networkidle',
                timeout=30000
            )

            print(f"📊 Page loaded - Status: {response.status}")
            print(f"📍 Final URL: {page.url}")

            # Wait untuk JavaScript execution
            await asyncio.sleep(3)

            # Get visible text di page
            try:
                visible_text = await page.inner_text('body')
                visible_text_lower = visible_text.lower()
                print(f"📄 Visible text preview: {visible_text[:300]}")
            except:
                page_content = await page.content()
                visible_text_lower = page_content.lower()
                visible_text = visible_text_lower

            final_url = page.url.lower()

            # DETEKSI STATUS dari page content
            not_approved_indicators = [
                'not approved',
                'not verified',
                'could not verify',
                'unable to verify',
                'verification failed',
                'Try again',
                'Error',
                'source error',
                'cannot verify',
                'no match found',
                'could not be verified'
            ]

            success_indicators = [
                'verified successfully',
                'verification complete',
                'countinue',
                'successfully verified',
                'verification successful',
                'you are verified',
                'you've been verified',
                'approved',
                'congratulations',
                'eligibility confirmed'
            ]

            pending_indicators = [
                'pending review',
                'under review',
                'being reviewed',
                'manual review'
            ]

            document_indicators = [
                'upload document',
                'document required',
                'please upload',
                'provide documentation'
            ]

            # Check URL patterns
            is_error_url = any(x in final_url for x in ['error', 'failed', 'notapproved', 'unable'])
            is_success_url = any(x in final_url for x in ['success', 'verified', 'complete', 'approved'])

            # Check visible text
            has_error = any(indicator in visible_text_lower for indicator in not_approved_indicators)
            has_success = any(indicator in visible_text_lower for indicator in success_indicators)
            has_pending = any(indicator in visible_text_lower for indicator in pending_indicators)
            has_document = any(indicator in visible_text_lower for indicator in document_indicators)

            # Determine final status
            if has_error or is_error_url:
                verification_status = "not_approved"
                is_verified = False
                status_msg = "NOT APPROVED - Data tidak cocok atau ditolak"
            elif has_success or is_success_url:
                verification_status = "approved"
                is_verified = True
                status_msg = "APPROVED - Verifikasi berhasil!"
            elif has_document:
                verification_status = "document_required"
                is_verified = False
                status_msg = "DOCUMENT REQUIRED - Butuh upload dokumen"
            elif has_pending:
                verification_status = "pending_review"
                is_verified = False
                status_msg = "PENDING REVIEW - Sedang direview manual"
            else:
                verification_status = "unknown"
                is_verified = False
                status_msg = "UNKNOWN - Status tidak dapat dideteksi"

            print(f"🎯 Detection Result: {verification_status}")
            print(f"📝 Status Message: {status_msg}")

            await browser.close()

            return {
                "success": True,
                "clicked": True,
                "status_code": response.status,
                "final_url": page.url,
                "verified": is_verified,
                "verification_status": verification_status,
                "status_message": status_msg,
                "response_snippet": visible_text[:800]
            }

    except PlaywrightTimeout:
        if browser:
            await browser.close()
        return {
            "success": False,
            "clicked": False,
            "message": "Browser timeout - page tidak load dalam 30 detik",
            "verification_status": "timeout"
        }
    except Exception as e:
        if browser:
            try:
                await browser.close()
            except:
                pass
        print(f"❌ Browser automation error: {e}")
        import traceback
        traceback.print_exc()
        return {
            "success": False,
            "clicked": False,
            "message": f"Browser error: {str(e)}",
            "verification_status": "error"
        }

# =====================================================
# EMAIL MONITORING JOB
# =====================================================

async def monitor_email_job(context: ContextTypes.DEFAULT_TYPE):
    """Monitor inbox dan auto-click verification link dengan REAL BROWSER"""
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
                            "🌐 Membuka ...\n"
                            "🖱️ Bot sedang berusaha!\n"
                            "⏳ Tunggu sebentar (30 detik max)..."
                        ),
                        parse_mode="Markdown"
                    )

                    # CLICK DENGAN BROWSER ASLI!
                    click_result = await click_verification_link_with_browser(verification_link)

                    if click_result.get("success") and click_result.get("clicked"):
                        await asyncio.sleep(2)

                        verification_id = email_data.get("verification_id")
                        status_check = await check_sheerid_status(verification_id)
                        sheerid_status = status_check.get("status", "unknown")

                        verification_status = click_result.get("verification_status", "unknown")
                        status_message = click_result.get("status_message", "")

                        # NOTIFIKASI BERDASARKAN STATUS
                        if verification_status == "approved":
                            await context.bot.send_message(
                                chat_id=chat_id,
                                text=(
                                    "✅ *VERIFICATION APPROVED!*\n\n"
                                    "🎉 *Status: SUCCESSFULLY VERIFIED*\n\n"
                                    f"📧 Email: `{email}`\n"
                                    f"🎯 SheerID Status: `{sheerid_status}`\n"
                                    f"📊 HTTP Status: `{click_result.get('status_code')}`\n"
                                    f"✨ Message: {status_message}\n\n"
                                    "🔗 Final URL:\n"
                                    f"`{click_result.get('final_url', 'N/A')[:100]}...`\n\n"
                                    "✨ *Verifikasi veteran berhasil!*\n"
                                    "Sekarang kamu bisa gunakan discount/offer yang tersedia."
                                ),
                                parse_mode="Markdown"
                            )

                            await send_log(
                                f"✅ VERIFICATION APPROVED ({BOT_NAME})\n\n"
                                f"User ID: {user_id}\n"
                                f"Email: {email}\n"
                                f"Status: {verification_status}\n"
                                f"SheerID: {sheerid_status}\n"
                                f"Link: {verification_link}"
                            )

                        elif verification_status == "not_approved":
                            await context.bot.send_message(
                                chat_id=chat_id,
                                text=(
                                    "❌ *VERIFICATION NOT APPROVED*\n\n"
                                    "⚠️ *Status: NOT APPROVED / REJECTED*\n\n"
                                    f"📧 Email: `{email}`\n"
                                    f"🎯 SheerID Status: `{sheerid_status}`\n"
                                    f"📊 HTTP Status: `{click_result.get('status_code')}`\n"
                                    f"💬 Message: {status_message}\n\n"
                                    "📋 *Alasan kemungkinan:*\n"
                                    "• Data tidak cocok dengan database SheerID\n"
                                    "• Informasi veteran tidak valid\n"
                                    "• Tanggal lahir/discharge tidak sesuai\n"
                                    "• Branch/status tidak match\n\n"
                                    "💡 *Saran:*\n"
                                    "• Cek kembali data yang diinput\n"
                                    "• Gunakan data veteran yang valid\n"
                                    "• Coba dengan data berbeda\n\n"
                                    "Ketik /veteran untuk mencoba lagi."
                                ),
                                parse_mode="Markdown"
                            )

                            await send_log(
                                f"❌ VERIFICATION NOT APPROVED ({BOT_NAME})\n\n"
                                f"User ID: {user_id}\n"
                                f"Email: {email}\n"
                                f"Status: NOT APPROVED\n"
                                f"SheerID: {sheerid_status}"
                            )

                        elif verification_status == "document_required":
                            await context.bot.send_message(
                                chat_id=chat_id,
                                text=(
                                    "📄 *DOCUMENT UPLOAD REQUIRED*\n\n"
                                    "⚠️ *Status: PENDING - DOCUMENT NEEDED*\n\n"
                                    f"📧 Email: `{email}`\n"
                                    f"🎯 SheerID Status: `{sheerid_status}`\n\n"
                                    "📋 *SheerID membutuhkan dokumen:*\n"
                                    "• DD214 (discharge papers)\n"
                                    "• Military ID\n"
                                    "• Veteran ID card\n\n"
                                    "💡 Akses link ini di browser untuk upload dokumen:\n"
                                    f"`{click_result.get('final_url', 'N/A')}`\n\n"
                                    "Bot tidak bisa auto-upload dokumen."
                                ),
                                parse_mode="Markdown"
                            )

                        elif verification_status == "pending_review":
                            await context.bot.send_message(
                                chat_id=chat_id,
                                text=(
                                    "🔄 *VERIFICATION PENDING REVIEW*\n\n"
                                    "⏳ *Status: UNDER MANUAL REVIEW*\n\n"
                                    f"📧 Email: `{email}`\n"
                                    f"🎯 SheerID Status: `{sheerid_status}`\n\n"
                                    "📋 *Kemungkinan:*\n"
                                    "• SheerID sedang melakukan review manual\n"
                                    "• Data membutuhkan validasi tambahan\n"
                                    "• Proses verifikasi memakan waktu lebih lama\n\n"
                                    "💡 Cek email atau link verifikasi nanti untuk update status."
                                ),
                                parse_mode="Markdown"
                            )

                        else:
                            await context.bot.send_message(
                                chat_id=chat_id,
                                text=(
                                    "⚠️ *VERIFICATION STATUS UNCLEAR*\n\n"
                                    "🔄 *Status: UNKNOWN / AMBIGUOUS*\n\n"
                                    f"📧 Email: `{email}`\n"
                                    f"🎯 SheerID Status: `{sheerid_status}`\n"
                                    f"📊 HTTP Status: `{click_result.get('status_code')}`\n\n"
                                    "💡 Akses link ini di browser untuk cek status:\n"
                                    f"`{click_result.get('final_url', 'N/A')}`\n\n"
                                    "Response preview:\n"
                                    f"`{click_result.get('response_snippet', '')[:200]}...`"
                                ),
                                parse_mode="Markdown"
                            )
                    else:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=(
                                "❌ *BROWSER AUTO-CLICK FAILED*\n\n"
                                f"Error: {click_result.get('message', 'Unknown')}\n\n"
                                f"🔗 Link: `{verification_link[:100]}...`\n\n"
                                "Kemungkinan:\n"
                                "• Browser timeout\n"
                                "• Network error\n"
                                "• Page tidak dapat diload\n\n"
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
        import traceback
        traceback.print_exc()

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

# =====================================================
# LOGGING FUNCTIONS
# =====================================================

async def send_log(text: str):
    """Send log to admin"""
    if not LOG_BOT_TOKEN or ADMIN_CHAT_ID == 0 or not LOG_API_URL:
        return

    max_retries = 3
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                payload = {"chat_id": ADMIN_CHAT_ID, "text": text}
                resp = await client.post(LOG_API_URL, json=payload)
                if resp.status_code == 200:
                    return
        except Exception as e:
            print(f"❌ Log error (attempt {attempt + 1}): {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(2)

async def log_user_start(update: Update, command_name: str):
    user = update.effective_user
    text = (
        f"📥 NEW USER FLOW {command_name} ({BOT_NAME})\n\n"
        f"ID: {user.id}\n"
        f"Name: {user.full_name}\n"
        f"Username: @{user.username or '-'}\n"
        f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    await send_log(text)

async def log_verification_result(user_id: int, full_name: str, email: str, status: str, success: bool, error_msg: str = ""):
    status_emoji = "✅" if success else "❌"
    status_text = "SUCCESS" if success else "FAILED"
    text = (
        f"{status_emoji} VETERAN VERIFICATION {status_text} ({BOT_NAME})\n\n"
        f"ID: {user_id}\n"
        f"Name: {full_name}\n"
        f"Email: {email}\n"
        f"SheerID Status: {status}\n"
    )
    if not success:
        text += f"\nError: {error_msg}"
    await send_log(text)

# =====================================================
# TIMEOUT FUNCTIONS
# =====================================================

async def step_timeout_job(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    chat_id = job.chat_id
    user_id = job.user_id
    step_name = job.data.get("step", "UNKNOWN")

    if user_id in v_user_data:
        del v_user_data[user_id]

    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"⏰ *Timeout di step {step_name}*\n\n"
                "Kamu tidak merespon dalam 5 menit.\n"
                "Kirim /veteran untuk mengulang."
            ),
            parse_mode="Markdown",
        )
    except Exception as e:
        print(f"❌ Failed to send timeout: {e}")

def set_step_timeout(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int, step: str):
    if context.job_queue is None:
        return

    job_name = f"timeout_veteran_{step}_{user_id}"
    current_jobs = context.job_queue.get_jobs_by_name(job_name)
    for job in current_jobs:
        job.schedule_removal()

    context.job_queue.run_once(
        step_timeout_job,
        when=STEP_TIMEOUT,
        chat_id=chat_id,
        user_id=user_id,
        name=job_name,
        data={"step": step},
    )

def clear_all_timeouts(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    if context.job_queue is None:
        return
    for step in ["URL", "STATUS", "ORG", "NAME", "BIRTH", "DISCHARGE"]:
        job_name = f"timeout_veteran_{step}_{user_id}"
        for job in context.job_queue.get_jobs_by_name(job_name):
            job.schedule_removal()

# =====================================================
# SHEERID HELPER FUNCTIONS WITH RATE LIMITING
# =====================================================

async def check_sheerid_status(verification_id: str) -> dict:
    """Check current status dari SheerID verification"""
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            url = f"{SHEERID_BASE_URL}/rest/v2/verification/{verification_id}"
            resp = await client.get(url)
            if resp.status_code != 200:
                return {"success": False, "status": "unknown"}
            data = resp.json()
            return {"success": True, "status": data.get("currentStep", "unknown"), "data": data}
        except Exception as e:
            return {"success": False, "status": "unknown", "message": str(e)}

async def submit_military_flow_with_retry(
    verification_id: str,
    status: str,
    first_name: str,
    last_name: str,
    birth_date: str,
    email: str,
    org: dict,
    discharge_date: str,
) -> dict:
    """Submit dengan retry logic dan exponential backoff untuk handle 429"""

    for attempt in range(MAX_RETRIES):
        try:
            # Delay antar request untuk avoid rate limit
            if attempt > 0:
                backoff_delay = RETRY_BACKOFF * (2 ** (attempt - 1))
                print(f"⏳ Waiting {backoff_delay}s before retry (attempt {attempt + 1}/{MAX_RETRIES})...")
                await asyncio.sleep(backoff_delay)
            else:
                await asyncio.sleep(REQUEST_DELAY)

            result = await submit_military_flow(
                verification_id, status, first_name, last_name,
                birth_date, email, org, discharge_date
            )

            # Jika 429, retry
            if not result.get("success"):
                error_msg = result.get("message", "")
                if "429" in error_msg or "rate limit" in error_msg.lower() or "limit" in error_msg.lower():
                    print(f"⚠️ Rate limit hit (429), will retry...")
                    continue
                else:
                    return result  # Error lain, langsung return

            return result

        except Exception as e:
            print(f"❌ Attempt {attempt + 1} failed: {e}")
            if attempt == MAX_RETRIES - 1:
                return {"success": False, "message": f"All {MAX_RETRIES} attempts failed: {str(e)}"}

    return {"success": False, "message": "Max retries exceeded"}

async def submit_military_flow(
    verification_id: str,
    status: str,
    first_name: str,
    last_name: str,
    birth_date: str,
    email: str,
    org: dict,
    discharge_date: str,
) -> dict:
    """Submit military info ke SheerID"""
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            step1_url = f"{SHEERID_BASE_URL}/rest/v2/verification/{verification_id}/step/collectMilitaryStatus"
            step1_body = {"status": status}

            print(f"📤 Step 1 URL: {step1_url}")
            print(f"📦 Step 1 Payload: {step1_body}")

            r1 = await client.post(step1_url, json=step1_body)

            print(f"📥 Step 1 Response: {r1.status_code}")
            print(f"📥 Step 1 Body: {r1.text[:500]}")

            if r1.status_code == 429:
                return {
                    "success": False, 
                    "message": f"Rate limit (429) at step 1: {r1.text}"
                }

            if r1.status_code != 200:
                return {
                    "success": False, 
                    "message": f"collectMilitaryStatus failed: {r1.status_code} - {r1.text}"
                }

            d1 = r1.json()
            submission_url = d1.get("submissionUrl")

            if not submission_url:
                return {"success": False, "message": "No submissionUrl in step 1 response"}

            print(f"✅ Got submissionUrl: {submission_url}")

            # Delay sebelum step 2
            await asyncio.sleep(REQUEST_DELAY)

            submission_opt_in = (
                "By submitting the personal information above, I acknowledge that my personal "
                "information is being collected under the privacy policy of the business from "
                "which I am seeking a discount, and I understand that my personal information "
                "will be shared with SheerID as a processor/third-party service provider in "
                "order for SheerID to confirm my eligibility for a special offer."
            )

            payload2 = {
                "firstName": first_name,
                "lastName": last_name,
                "birthDate": birth_date,
                "email": email,
                "phoneNumber": "",
                "organization": {
                    "id": org["id"],
                    "name": org["name"]
                },
                "dischargeDate": discharge_date,
                "locale": "en-US",
                "country": "US",
                "metadata": {
                    "marketConsentValue": False,
                    "refererUrl": "",
                    "verificationId": verification_id,
                    "submissionOptIn": submission_opt_in,
                },
            }

            print(f"📤 Step 2 URL (submissionUrl): {submission_url}")

            r2 = await client.post(submission_url, json=payload2)

            print(f"📥 Step 2 Response: {r2.status_code}")

            if r2.status_code == 429:
                return {
                    "success": False, 
                    "message": f"Rate limit (429) at step 2: {r2.text}"
                }

            if r2.status_code != 200:
                return {
                    "success": False, 
                    "message": f"collectInactiveMilitaryPersonalInfo failed: {r2.status_code} - {r2.text}"
                }

            return {"success": True, "message": "Military info submitted successfully"}

        except Exception as e:
            print(f"❌ Exception in submit_military_flow: {e}")
            return {"success": False, "message": str(e)}

# =====================================================
# CONVERSATION HANDLERS
# =====================================================

async def veteran_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    await log_user_start(update, "/veteran")
    v_user_data.pop(user_id, None)
    clear_all_timeouts(context, user_id)
    set_step_timeout(context, chat_id, user_id, "URL")

    await update.message.reply_text(
        "🎖 *Military / Veteran Verification Helper*\n\n"
        "✨ *FULLY AUTOMATED BOT*\n"
        "• Auto-generate temporary email\n"
        "• Auto-extract emailToken\n"
        "• 🌐 **REAL BROWSER**\n"
        "• 🖱️ **REAL CLICK** like human!\n"
        "• Auto-detect approval status\n"
        "• Clear notifications (Approved/Not Approved)\n"
        "• ⚡ Rate limiting protection\n\n"
        "Kirim SheerID verification URL:\n\n"
        "`https://services.sheerid.com/verify/...?verificationId=...`\n\n"
        "Contoh:\n"
        "`https://services.sheerid.com/verify/abcd/?verificationId=1234`\n\n"
        "📅 *Note: Format tanggal YYYY-MM-DD*\n\n"
        "*⏰ Kamu punya 5 menit*",
        parse_mode="Markdown",
    )
    return V_URL

async def veteran_get_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    url = update.message.text.strip()

    match = re.search(r"verificationId=([A-Za-z0-9\-]+)", url)
    if not match:
        await update.message.reply_text(
            "❌ *Invalid URL!*\n\n"
            "Harus ada parameter `verificationId=...`\n\n"
            "*⏰ Kamu punya 5 menit lagi*",
            parse_mode="Markdown",
        )
        set_step_timeout(context, chat_id, user_id, "URL")
        return V_URL

    verification_id = match.group(1)
    v_user_data[user_id] = {
        "verification_id": verification_id,
        "original_url": url
    }

    clear_all_timeouts(context, user_id)
    set_step_timeout(context, chat_id, user_id, "STATUS")

    await update.message.reply_text(
        f"✅ *Verification ID:* `{verification_id}`\n\n"
        "Pilih *military status* kamu:",
        parse_mode="Markdown",
        reply_markup=STATUS_KEYBOARD,
    )
    return V_STATUS

async def veteran_status_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    chat_id = query.message.chat_id

    if user_id not in v_user_data:
        await query.edit_message_text("❌ *Session expired*\n\nKirim /veteran lagi.", parse_mode="Markdown")
        return ConversationHandler.END

    data = query.data
    if not data.startswith("status_"):
        await query.edit_message_text("❌ Invalid status.\n\nKirim /veteran lagi.", parse_mode="Markdown")
        return ConversationHandler.END

    status = data.split("_", 1)[1]

    # Map status ke display name
    status_display = {
        "ACTIVE_DUTY": "Active Duty",
        "VETERAN": "Military Veteran or Retiree",
        "RESERVIST": "Reservist or National Guard"
    }

    v_user_data[user_id]["status"] = status

    clear_all_timeouts(context, user_id)
    set_step_timeout(context, chat_id, user_id, "ORG")

    # Generate organization keyboard berdasarkan status
    org_keyboard = get_org_keyboard(status)

    await query.edit_message_text(
        f"✅ Status: *{status_display.get(status, status)}*\n\n"
        "Pilih *branch of service*:",
        parse_mode="Markdown",
        reply_markup=org_keyboard,
    )
    return V_ORG

async def veteran_org_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    chat_id = query.message.chat_id

    if user_id not in v_user_data:
        await query.edit_message_text("❌ *Session expired*\n\nKirim /veteran lagi.", parse_mode="Markdown")
        return ConversationHandler.END

    data = query.data
    if not data.startswith("org_"):
        await query.edit_message_text("❌ Invalid organization.\n\nKirim /veteran lagi.", parse_mode="Markdown")
        return ConversationHandler.END

    org_name = data.split("_", 1)[1]

    # Get organization dari dict yang sesuai dengan status
    status = v_user_data[user_id].get("status")
    if status == "RESERVIST":
        org = MIL_ORGS_RESERVIST.get(org_name)
    else:
        org = MIL_ORGS_ACTIVE_VETERAN.get(org_name)

    if not org:
        await query.edit_message_text("❌ Unknown organization.\n\nKirim /veteran lagi.", parse_mode="Markdown")
        return ConversationHandler.END

    v_user_data[user_id]["organization"] = org

    clear_all_timeouts(context, user_id)
    set_step_timeout(context, chat_id, user_id, "NAME")

    await query.edit_message_text(
        f"✅ Branch: *{org['name']}*\n\n"
        "Kirim *nama lengkap* kamu:\n"
        "Format: `FirstName LastName`\n\n"
        "Contoh: `John Doe`\n\n"
        "*⏰ Kamu punya 5 menit*",
        parse_mode="Markdown",
    )
    return V_NAME

async def veteran_get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    name_input = update.message.text.strip()

    parts = name_input.split(maxsplit=1)
    if len(parts) < 2:
        await update.message.reply_text(
            "❌ *Format nama salah!*\n\n"
            "Harus ada First Name dan Last Name.\n"
            "Format: `FirstName LastName`\n\n"
            "Contoh: `John Doe`\n\n"
            "*⏰ Kamu punya 5 menit lagi*",
            parse_mode="Markdown",
        )
        set_step_timeout(context, chat_id, user_id, "NAME")
        return V_NAME

    first_name = parts[0]
    last_name = parts[1]

    v_user_data[user_id]["first_name"] = first_name
    v_user_data[user_id]["last_name"] = last_name

    clear_all_timeouts(context, user_id)
    set_step_timeout(context, chat_id, user_id, "BIRTH")

    await update.message.reply_text(
        f"✅ Nama: *{first_name} {last_name}*\n\n"
        "Kirim *tanggal lahir* kamu:\n"
        "Format: `YYYY-MM-DD`\n\n"
        "Contoh: `1990-05-15`\n\n"
        "*⏰ Kamu punya 5 menit*",
        parse_mode="Markdown",
    )
    return V_BIRTH

async def veteran_get_birth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    birth_date = update.message.text.strip()

    # Validate format YYYY-MM-DD
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', birth_date):
        await update.message.reply_text(
            "❌ *Format tanggal salah!*\n\n"
            "Harus format `YYYY-MM-DD`\n\n"
            "Contoh: `1990-05-15`\n\n"
            "*⏰ Kamu punya 5 menit lagi*",
            parse_mode="Markdown",
        )
        set_step_timeout(context, chat_id, user_id, "BIRTH")
        return V_BIRTH

    v_user_data[user_id]["birth_date"] = birth_date

    clear_all_timeouts(context, user_id)
    set_step_timeout(context, chat_id, user_id, "DISCHARGE")

    await update.message.reply_text(
        f"✅ Birth date: *{birth_date}*\n\n"
        "Kirim *discharge date* (tanggal keluar dari dinas):\n"
        "Format: `YYYY-MM-DD`\n\n"
        "Contoh: `2020-08-30`\n\n"
        "*⏰ Kamu punya 5 menit*",
        parse_mode="Markdown",
    )
    return V_DISCHARGE

async def veteran_get_discharge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    discharge_date = update.message.text.strip()

    # Validate format YYYY-MM-DD
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', discharge_date):
        await update.message.reply_text(
            "❌ *Format tanggal salah!*\n\n"
            "Harus format `YYYY-MM-DD`\n\n"
            "Contoh: `2020-08-30`\n\n"
            "*⏰ Kamu punya 5 menit lagi*",
            parse_mode="Markdown",
        )
        set_step_timeout(context, chat_id, user_id, "DISCHARGE")
        return V_DISCHARGE

    v_user_data[user_id]["discharge_date"] = discharge_date

    clear_all_timeouts(context, user_id)

    # Show confirmation
    data = v_user_data[user_id]

    status_display = {
        "ACTIVE_DUTY": "Active Duty",
        "VETERAN": "Military Veteran or Retiree",
        "RESERVIST": "Reservist or National Guard"
    }

    confirm_text = (
        "📋 *Konfirmasi Data*\n\n"
        f"Status: `{status_display.get(data['status'], data['status'])}`\n"
        f"Branch: `{data['organization']['name']}`\n"
        f"Name: `{data['first_name']} {data['last_name']}`\n"
        f"Birth Date: `{data['birth_date']}`\n"
        f"Discharge Date: `{discharge_date}`\n\n"
        "Apakah data sudah benar?"
    )

    confirm_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Benar, Lanjutkan", callback_data="confirm_yes")],
        [InlineKeyboardButton("❌ Batal", callback_data="confirm_no")],
    ])

    await update.message.reply_text(
        confirm_text,
        parse_mode="Markdown",
        reply_markup=confirm_keyboard,
    )
    return V_CONFIRM

async def veteran_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    chat_id = query.message.chat_id

    if user_id not in v_user_data:
        await query.edit_message_text("❌ *Session expired*\n\nKirim /veteran lagi.", parse_mode="Markdown")
        return ConversationHandler.END

    if query.data == "confirm_no":
        await query.edit_message_text(
            "❌ *Verification dibatalkan*\n\n"
            "Kirim /veteran untuk mulai lagi.",
            parse_mode="Markdown"
        )
        v_user_data.pop(user_id, None)
        return ConversationHandler.END

    # confirm_yes - Process verification
    await query.edit_message_text(
        "⏳ *Processing verification...*\n\n"
        "🔄 Step 1: Generating temp email...\n"
        "⏳ Step 2: Submitting to SheerID...\n"
        "⏳ Step 3: Monitoring email...\n\n"
        "Tunggu sebentar...",
        parse_mode="Markdown"
    )

    data = v_user_data[user_id]

    # Generate temp email
    email_result = await create_temp_email()
    if not email_result.get("success"):
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "❌ *Gagal generate email*\n\n"
                f"Error: {email_result.get('message')}\n\n"
                "Coba lagi dengan /veteran"
            ),
            parse_mode="Markdown"
        )
        v_user_data.pop(user_id, None)
        return ConversationHandler.END

    email = email_result["email"]

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"✅ *Email generated:* `{email}`\n\n"
            "🔄 Submitting data ke SheerID API...\n"
            "⏳ Please wait..."
        ),
        parse_mode="Markdown"
    )

    # Submit to SheerID dengan retry logic
    submit_result = await submit_military_flow_with_retry(
        verification_id=data["verification_id"],
        status=data["status"],
        first_name=data["first_name"],
        last_name=data["last_name"],
        birth_date=data["birth_date"],
        email=email,
        org=data["organization"],
        discharge_date=data["discharge_date"]
    )

    if not submit_result.get("success"):
        error_msg = submit_result.get("message", "Unknown error")
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "❌ *SUBMISSION FAILED*\n\n"
                f"Error: {error_msg}\n\n"
                "Coba lagi atau /veteran restart."
            ),
            parse_mode="Markdown"
        )
        await delete_email_inbox(email)
        v_user_data.pop(user_id, None)
        await log_verification_result(
            user_id, 
            f"{data['first_name']} {data['last_name']}", 
            email, 
            "error", 
            False, 
            error_msg
        )
        return ConversationHandler.END

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "✅ *Data submitted successfully!*\n\n"
            f"📧 Monitoring email: `{email}`\n"
            "🔄 Checking inbox setiap 10 detik...\n\n"
            "⏳ Bot akan otomatis klik verification link jika email masuk.\n"
            "Max wait time: 5 menit."
        ),
        parse_mode="Markdown"
    )

    # Store email data untuk monitoring job
    temp_email_storage[user_id] = {
        "email": email,
        "verification_id": data["verification_id"],
        "original_url": data["original_url"],
        "check_count": 0
    }

    # Start email monitoring
    start_email_monitoring(context, chat_id, user_id)

    await log_verification_result(
        user_id,
        f"{data['first_name']} {data['last_name']}",
        email,
        "submitted",
        True
    )

    v_user_data.pop(user_id, None)
    return ConversationHandler.END

async def cancel_veteran(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    v_user_data.pop(user_id, None)
    clear_all_timeouts(context, user_id)

    await update.message.reply_text(
        "❌ *Verification cancelled*\n\n"
        "Kirim /veteran untuk mulai lagi.",
        parse_mode="Markdown"
    )
    return ConversationHandler.END

# =====================================================
# MAIN FUNCTION
# =====================================================

def main():
    """Start the bot"""

    # Custom request dengan timeout lebih panjang
    request = HTTPXRequest(connection_pool_size=8, read_timeout=30, write_timeout=30, connect_timeout=30)

    # Build application dengan JobQueue
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .request(request)
        .build()
    )

    # Veteran conversation handler
    veteran_conv = ConversationHandler(
        entry_points=[CommandHandler("veteran", veteran_start)],
        states={
            V_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, veteran_get_url)],
            V_STATUS: [CallbackQueryHandler(veteran_status_callback, pattern="^status_")],
            V_ORG: [CallbackQueryHandler(veteran_org_callback, pattern="^org_")],
            V_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, veteran_get_name)],
            V_BIRTH: [MessageHandler(filters.TEXT & ~filters.COMMAND, veteran_get_birth)],
            V_DISCHARGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, veteran_get_discharge)],
            V_CONFIRM: [CallbackQueryHandler(veteran_confirm_callback, pattern="^confirm_")],
        },
        fallbacks=[CommandHandler("cancel", cancel_veteran)],
        conversation_timeout=STEP_TIMEOUT,
        per_message=False,
        per_chat=True,
        per_user=True,
    )

    application.add_handler(veteran_conv)

    print(f"🤖 Bot started: {BOT_NAME}")
    print(f"🔄 Request delay: {REQUEST_DELAY}s")
    print(f"🔁 Max retries: {MAX_RETRIES}")
    print(f"✅ JobQueue enabled for email monitoring")

    # Run bot
    application.run_polling(
    allowed_updates=Update.ALL_TYPES,
    drop_pending_updates=True  # 
    )
   

if __name__ == "__main__":
    main()
