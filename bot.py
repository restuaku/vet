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
# SHEERID HELPER FUNCTIONS - FIXED IKUT REFERENCE
# =====================================================

async def check_sheerid_status(verification_id: str) -> dict:
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
    """
    FIXED: Ikut persis reference script paste-2.txt
    Pakai submissionUrl dari step 1 response
    Format tanggal YYYY-MM-DD (bukan MM/DD/YYYY)
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            # Step 1: Submit military status
            step1_url = f"{SHEERID_BASE_URL}/rest/v2/verification/{verification_id}/step/collectMilitaryStatus"
            step1_body = {"status": status}

            print(f"📤 Step 1 URL: {step1_url}")
            print(f"📦 Step 1 Payload: {step1_body}")

            r1 = await client.post(step1_url, json=step1_body)

            print(f"📥 Step 1 Response: {r1.status_code}")
            print(f"📥 Step 1 Body: {r1.text[:500]}")

            if r1.status_code != 200:
                return {
                    "success": False, 
                    "message": f"collectMilitaryStatus failed: {r1.status_code} - {r1.text}"
                }

            d1 = r1.json()

            # PENTING: Pakai submissionUrl dari response (seperti reference script)
            submission_url = d1.get("submissionUrl")

            if not submission_url:
                return {"success": False, "message": "No submissionUrl in step 1 response"}

            print(f"✅ Got submissionUrl: {submission_url}")

            # Step 2: Submit personal info KE submissionUrl
            # Payload sesuai reference (dengan metadata)
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
                "birthDate": birth_date,  # Format YYYY-MM-DD
                "email": email,
                "phoneNumber": "",
                "organization": {
                    "id": org["id"],
                    "name": org["name"]
                },
                "dischargeDate": discharge_date,  # Format YYYY-MM-DD
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
            print(f"📦 Step 2 Payload: {payload2}")

            r2 = await client.post(submission_url, json=payload2)

            print(f"📥 Step 2 Response: {r2.status_code}")
            print(f"📥 Step 2 Body: {r2.text[:500]}")

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
        "• Auto-extract emailToken (handle incomplete links)\n"
        "• Auto-build complete verification link\n"
        "• Auto-click untuk konfirmasi\n"
        "• Auto-report result\n\n"
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
    v_user_data[user_id]["status"] = status

    clear_all_timeouts(context, user_id)
    set_step_timeout(context, chat_id, user_id, "ORG")

    await query.edit_message_text(
        f"✅ Status: `{status}`\n\n"
        "Pilih *branch of service*:",
        parse_mode="Markdown",
        reply_markup=ORG_KEYBOARD,
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
    org = MIL_ORGS.get(org_name)

    if not org:
        await query.edit_message_text("❌ Unknown organization.\n\nKirim /veteran lagi.", parse_mode="Markdown")
        return ConversationHandler.END

    v_user_data[user_id]["organization"] = org

    clear_all_timeouts(context, user_id)
    set_step_timeout(context, chat_id, user_id, "NAME")

    await query.edit_message_text(
        f"✅ Branch: *{org_name}*\n\n"
        "Kirim *nama lengkap* kamu.\n"
        "Contoh: `John Michael Smith`\n\n"
        "*⏰ Kamu punya 5 menit*",
        parse_mode="Markdown",
    )
    return V_NAME

async def veteran_get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    full_name = update.message.text.strip()
    parts = full_name.split()

    if len(parts) < 2:
        await update.message.reply_text(
            "❌ Tolong kirim *first name DAN last name*.\n"
            "Contoh: `John Smith`\n\n"
            "*⏰ Kamu punya 5 menit lagi*",
            parse_mode="Markdown",
        )
        set_step_timeout(context, chat_id, user_id, "NAME")
        return V_NAME

    v_user_data.setdefault(user_id, {})
    v_user_data[user_id]["first_name"] = parts[0]
    v_user_data[user_id]["last_name"] = " ".join(parts[1:])
    v_user_data[user_id]["full_name"] = full_name

    clear_all_timeouts(context, user_id)
    set_step_timeout(context, chat_id, user_id, "BIRTH")

    await update.message.reply_text(
        f"✅ *Name:* {full_name}\n\n"
        "Kirim *tanggal lahir* (format `YYYY-MM-DD`).\n"
        "Contoh: `1985-07-21`\n\n"
        "*⏰ Kamu punya 5 menit*",
        parse_mode="Markdown",
    )
    return V_BIRTH

async def veteran_get_birth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    birth = update.message.text.strip()

    # Format YYYY-MM-DD
    if len(birth) != 10 or birth[4] != "-" or birth[7] != "-":
        await update.message.reply_text(
            "❌ Format tanggal salah.\n"
            "Gunakan format `YYYY-MM-DD`.\n"
            "Contoh: `1985-07-21`\n\n"
            "*⏰ Kamu punya 5 menit lagi*",
            parse_mode="Markdown",
        )
        set_step_timeout(context, chat_id, user_id, "BIRTH")
        return V_BIRTH

    try:
        parts = birth.split("-")
        year = int(parts[0])
        month = int(parts[1])
        day = int(parts[2])

        if not (1900 <= year <= 2010):
            raise ValueError("Invalid year")
        if not (1 <= month <= 12):
            raise ValueError("Invalid month")
        if not (1 <= day <= 31):
            raise ValueError("Invalid day")
    except ValueError:
        await update.message.reply_text(
            "❌ Tanggal tidak valid.\n"
            "Gunakan format `YYYY-MM-DD`.\n"
            "Contoh: `1985-07-21`\n\n"
            "*⏰ Kamu punya 5 menit lagi*",
            parse_mode="Markdown",
        )
        set_step_timeout(context, chat_id, user_id, "BIRTH")
        return V_BIRTH

    v_user_data.setdefault(user_id, {})
    v_user_data[user_id]["birth_date"] = birth

    clear_all_timeouts(context, user_id)
    set_step_timeout(context, chat_id, user_id, "DISCHARGE")

    await update.message.reply_text(
        f"✅ *Birth date:* `{birth}`\n\n"
        "Kirim *discharge date* (format `YYYY-MM-DD`).\n"
        "Contoh: `2020-12-15`\n"
        "Kalau masih aktif, pakai tanggal masuk akal.\n\n"
        "*⏰ Kamu punya 5 menit*",
        parse_mode="Markdown",
    )
    return V_DISCHARGE

async def veteran_get_discharge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    ddate = update.message.text.strip()

    # Format YYYY-MM-DD
    if len(ddate) != 10 or ddate[4] != "-" or ddate[7] != "-":
        await update.message.reply_text(
            "❌ Format tanggal salah.\n"
            "Gunakan format `YYYY-MM-DD`.\n"
            "Contoh: `2020-12-15`\n\n"
            "*⏰ Kamu punya 5 menit lagi*",
            parse_mode="Markdown",
        )
        set_step_timeout(context, chat_id, user_id, "DISCHARGE")
        return V_DISCHARGE

    try:
        parts = ddate.split("-")
        year = int(parts[0])
        month = int(parts[1])
        day = int(parts[2])

        if not (1950 <= year <= 2026):
            raise ValueError("Invalid year")
        if not (1 <= month <= 12):
            raise ValueError("Invalid month")
        if not (1 <= day <= 31):
            raise ValueError("Invalid day")
    except ValueError:
        await update.message.reply_text(
            "❌ Tanggal tidak valid.\n"
            "Gunakan format `YYYY-MM-DD`.\n"
            "Contoh: `2020-12-15`\n\n"
            "*⏰ Kamu punya 5 menit lagi*",
            parse_mode="Markdown",
        )
        set_step_timeout(context, chat_id, user_id, "DISCHARGE")
        return V_DISCHARGE

    v_user_data.setdefault(user_id, {})
    v_user_data[user_id]["discharge_date"] = ddate

    await update.message.reply_text(
        "⏳ *Generating temporary email...*\n"
        "Bot akan auto-create email untuk verifikasi.",
        parse_mode="Markdown"
    )

    email_result = await create_temp_email()

    if not email_result.get("success"):
        await update.message.reply_text(
            "❌ *Failed to generate email*\n\n"
            f"Error: {email_result.get('message')}\n\n"
            "Coba lagi dengan /veteran",
            parse_mode="Markdown"
        )
        return ConversationHandler.END

    temp_email = email_result["email"]
    v_user_data[user_id]["email"] = temp_email

    temp_email_storage[user_id] = {
        "email": temp_email,
        "token": email_result["token"],
        "verification_id": v_user_data[user_id]["verification_id"],
        "original_url": v_user_data[user_id]["original_url"],
        "check_count": 0
    }

    data = v_user_data[user_id]
    summary = (
        "🔎 *Konfirmasi data veteran:*\n\n"
        f"VerificationId: `{data['verification_id']}`\n"
        f"Status: `{data['status']}`\n"
        f"Branch: `{data['organization']['name']}`\n"
        f"Name: `{data['first_name']} {data['last_name']}`\n"
        f"Birth: `{data['birth_date']}`\n"
        f"Discharge: `{data['discharge_date']}`\n"
        f"📧 Email (AUTO): `{temp_email}`\n\n"
        "✅ *Temporary email generated!*\n"
        "🤖 Bot akan:\n"
        "1️⃣ Submit data ke SheerID\n"
        "2️⃣ Monitor email inbox\n"
        "3️⃣ Ekstrak emailToken (handle incomplete link)\n"
        "4️⃣ Build complete verification link\n"
        "5️⃣ Auto-click untuk konfirmasi\n"
        "6️⃣ Report result\n\n"
        "Ketik `OK` untuk mulai, atau `/cancel` untuk batal."
    )

    clear_all_timeouts(context, user_id)
    set_step_timeout(context, chat_id, user_id, "CONFIRM")

    await update.message.reply_text(summary, parse_mode="Markdown")
    return V_CONFIRM

async def veteran_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    text = update.message.text.strip().lower()

    if text != "ok":
        await update.message.reply_text(
            "Ketik `OK` untuk lanjut atau `/cancel` untuk batal.",
            parse_mode="Markdown",
        )
        return V_CONFIRM

    if user_id not in v_user_data:
        await update.message.reply_text("❌ Session hilang.\n\nKirim /veteran lagi.", parse_mode="Markdown")
        return ConversationHandler.END

    data = v_user_data[user_id]
    verification_id = data["verification_id"]

    await update.message.reply_text(
        "🚀 *Mengirim data ke SheerID...*\n"
        "⏳ Mohon tunggu...",
        parse_mode="Markdown",
    )

    result = await submit_military_flow(
        verification_id=verification_id,
        status=data["status"],
        first_name=data["first_name"],
        last_name=data["last_name"],
        birth_date=data["birth_date"],
        email=data["email"],
        org=data["organization"],
        discharge_date=data["discharge_date"],
    )

    status_info = await check_sheerid_status(verification_id)
    status = status_info.get("status", "unknown")

    await log_verification_result(
        user_id=user_id,
        full_name=data["full_name"],
        email=data["email"],
        status=status,
        success=result["success"],
        error_msg=result.get("message", ""),
    )

    if not result["success"]:
        await update.message.reply_text(
            "❌ *SUBMISSION FAILED*\n\n"
            f"Error: {result.get('message')}\n\n"
            "Coba lagi atau /veteran restart.",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            "✅ *Data submitted successfully!*\n\n"
            f"📧 Email: `{data['email']}`\n"
            f"🎯 Current status: `{status}`\n\n"
            "🔄 *Bot monitoring inbox...*\n"
            "🖱️ Akan auto-click begitu email masuk!\n"
            "🔧 Handle incomplete links otomatis!\n\n"
            "⏰ Timeout: 5 menit\n"
            "💡 Tunggu notifikasi hasil...",
            parse_mode="Markdown",
        )

        start_email_monitoring(context, chat_id, user_id)

    v_user_data.pop(user_id, None)
    clear_all_timeouts(context, user_id)
    return ConversationHandler.END

async def veteran_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    v_user_data.pop(user_id, None)
    temp_email_storage.pop(user_id, None)
    clear_all_timeouts(context, user_id)

    await update.message.reply_text(
        "❌ *Operation cancelled*\n\n"
        "Ketik /veteran untuk mulai lagi.",
        parse_mode="Markdown",
    )
    return ConversationHandler.END

# =====================================================
# MAIN
# =====================================================

def main():
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN belum di-set!")
        return

    print("\n" + "=" * 70)
    print(f"🎖 {BOT_NAME} - Veteran Bot FIXED (Ikut Reference)")
    print("=" * 70)
    print(f"🤖 Bot Token: {BOT_TOKEN[:10]}...{BOT_TOKEN[-5:]}")
    print(f"👮 Admin Chat ID: {ADMIN_CHAT_ID}")
    print(f"📨 LOG_BOT_TOKEN: {bool(LOG_BOT_TOKEN)}")
    print(f"📧 Custom Tempmail API: {CUSTOM_MAIL_API}")
    print(f"🌐 Custom Domain: {CUSTOM_DOMAIN}")
    print(f"⏰ Step timeout: {STEP_TIMEOUT}s")
    print(f"📬 Email check interval: {EMAIL_CHECK_INTERVAL}s")
    print(f"🖱️ AUTO-CLICK: ENABLED")
    print(f"🔧 HANDLE INCOMPLETE LINKS: ENABLED")
    print(f"📅 DATE FORMAT: YYYY-MM-DD (ISO)")
    print(f"✅ FIXED: Pakai submissionUrl + format tanggal benar")
    print("=" * 70 + "\n")

    request = HTTPXRequest(
        read_timeout=60,
        write_timeout=60,
        connect_timeout=30,
        pool_timeout=30,
        connection_pool_size=8
    )

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .request(request)
        .build()
    )

    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        print(f"❌ Exception: {context.error}")
        if ADMIN_CHAT_ID and context.error:
            try:
                error_text = (
                    f"⚠️ ERROR ({BOT_NAME})\n\n"
                    f"Error: {str(context.error)[:500]}\n\n"
                    f"Update: {str(update)[:300] if update else 'None'}"
                )
                await context.bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=error_text
                )
            except:
                pass

    app.add_error_handler(error_handler)

    conv_veteran = ConversationHandler(
        entry_points=[CommandHandler("veteran", veteran_start)],
        states={
            V_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, veteran_get_url)],
            V_STATUS: [CallbackQueryHandler(veteran_status_callback, pattern="^status_")],
            V_ORG: [CallbackQueryHandler(veteran_org_callback, pattern="^org_")],
            V_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, veteran_get_name)],
            V_BIRTH: [MessageHandler(filters.TEXT & ~filters.COMMAND, veteran_get_birth)],
            V_DISCHARGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, veteran_get_discharge)],
            V_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, veteran_confirm)],
        },
        fallbacks=[CommandHandler("cancel", veteran_cancel)],
        conversation_timeout=None,
        name="veteran_conv",
        per_message=False,
    )

    app.add_handler(conv_veteran)

    print("✅ Veteran bot starting...")
    print("🔄 Waiting for Telegram connection...")

    try:
        app.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
            close_loop=False
        )
    except KeyboardInterrupt:
        print("\n⚠️ Bot stopped by user")
    except Exception as e:
        print(f"❌ Fatal error: {e}")
        raise

if __name__ == "__main__":
    main()
