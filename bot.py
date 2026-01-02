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
EMAIL_CHECK_TIMEOUT = 300

# Rate limiting untuk avoid 429 error
MAX_RETRIES = 3
RETRY_BASE_BACKOFF = 15  # Base backoff 15 detik (lebih aman)

# Custom Tempmail Worker URL
CUSTOM_MAIL_API = "https://bot-emails.pilarjalar.workers.dev"
CUSTOM_DOMAIN = "zzzz.biz.id"

# =====================================================
# RANDOM DELAY HELPERS
# =====================================================

async def random_delay(min_sec: float = 2.0, max_sec: float = 5.0):
    """Random delay untuk avoid detection"""
    delay = random.uniform(min_sec, max_sec)
    await asyncio.sleep(delay)

def get_random_email_interval() -> float:
    """Random interval untuk email checking (8-12 detik)"""
    return random.uniform(8.0, 12.0)

def get_backoff_delay(attempt: int) -> float:
    """Exponential backoff dengan jitter untuk retry"""
    base_delay = RETRY_BASE_BACKOFF * (2 ** attempt)
    jitter = random.uniform(0, 5)
    return base_delay + jitter

# =====================================================
# RANDOM USER AGENTS - COMPREHENSIVE LIST
# =====================================================

USER_AGENTS = [
    # Chrome Windows
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 11.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36',

    # Chrome Mac
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36',

    # Chrome Linux
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Ubuntu; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',

    # Firefox Windows
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:119.0) Gecko/20100101 Firefox/119.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:118.0) Gecko/20100101 Firefox/118.0',

    # Firefox Mac
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:120.0) Gecko/20100101 Firefox/120.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 13.6; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 14.0; rv:122.0) Gecko/20100101 Firefox/122.0',

    # Firefox Linux
    'Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0',
    'Mozilla/5.0 (X11; Linux x86_64; rv:119.0) Gecko/20100101 Firefox/119.0',

    # Safari Mac
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Safari/605.1.15',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15',

    # Edge Windows
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36 Edg/119.0.0.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36 Edg/118.0.0.0',

    # Edge Mac
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0',

    # Opera
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 OPR/106.0.0.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 OPR/106.0.0.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36 OPR/105.0.0.0',

    # Brave
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Brave/120.0.0.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Brave/120.0.0.0',
]

# Random viewport sizes (common resolutions)
VIEWPORTS = [
    {'width': 1920, 'height': 1080},  # Full HD
    {'width': 1366, 'height': 768},   # Laptop
    {'width': 1536, 'height': 864},   # Laptop HD+
    {'width': 1440, 'height': 900},   # MacBook
    {'width': 2560, 'height': 1440},  # 2K
    {'width': 1600, 'height': 900},   # HD+
    {'width': 1280, 'height': 720},   # HD
    {'width': 1680, 'height': 1050},  # WSXGA+
    {'width': 1920, 'height': 1200},  # WUXGA
]

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
# HUMAN BEHAVIOR SIMULATION
# =====================================================

async def simulate_human_behavior(page):
    """Simulate human-like mouse movement and scrolling"""
    try:
        # Random scroll down
        scroll_amount = random.randint(100, 400)
        await page.evaluate(f"window.scrollBy(0, {scroll_amount})")
        await random_delay(0.5, 1.5)

        # Random mouse movement
        try:
            x = random.randint(100, 800)
            y = random.randint(100, 600)
            await page.mouse.move(x, y)
            await random_delay(0.2, 0.8)
        except:
            pass

        # Sometimes scroll back up a bit
        if random.random() > 0.5:
            scroll_back = random.randint(50, 150)
            await page.evaluate(f"window.scrollBy(0, -{scroll_back})")
            await random_delay(0.3, 1.0)

    except Exception as e:
        print(f"⚠️ Human behavior simulation warning: {e}")

# =====================================================
# BROWSER AUTOMATION - REAL CLICK WITH ANTI-DETECTION!
# =====================================================

async def click_verification_link_with_browser(verification_url: str) -> dict:
    """
    🎯 BROWSER AUTOMATION: Buka browser Chromium dan klik link seperti manusia!
    ✨ Enhanced dengan human behavior simulation
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

            # 🎲 RANDOM USER AGENT & VIEWPORT + California Timezone
            random_ua = random.choice(USER_AGENTS)
            random_viewport = random.choice(VIEWPORTS)

            print(f"🎭 Using random UA: {random_ua[:60]}...")
            print(f"📐 Using random viewport: {random_viewport}")

            # Create browser context dengan random user agent dan viewport + enhanced headers
            context = await browser.new_context(
                user_agent=random_ua,
                viewport=random_viewport,
                locale='en-US',
                timezone_id='America/Los_Angeles',  # ✅ California timezone
                permissions=['geolocation'],
                geolocation={'latitude': 34.0522, 'longitude': -118.2437},  # LA coordinates
                color_scheme='light',
                extra_http_headers={
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'DNT': '1',
                    'Upgrade-Insecure-Requests': '1',
                    'Sec-Fetch-Dest': 'document',
                    'Sec-Fetch-Mode': 'navigate',
                    'Sec-Fetch-Site': 'none',
                }
            )

            # Create new page
            page = await context.new_page()

            print(f"🖱️ Browser opened - simulating human behavior...")

            # ✨ HUMAN BEHAVIOR: Random delay sebelum navigate
            await random_delay(1.0, 2.5)

            # Navigate ke URL - INI YANG BENAR-BENAR KLIK!
            response = await page.goto(
                verification_url,
                wait_until='networkidle',
                timeout=30000
            )

            print(f"📊 Page loaded - Status: {response.status}")
            print(f"📍 Final URL: {page.url}")

            # ✨ HUMAN BEHAVIOR: Simulate scrolling dan mouse movement
            await simulate_human_behavior(page)

            # Wait untuk JavaScript execution dengan random delay
            await random_delay(2.5, 4.5)

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
                'We are unable',
                'sourcesUnavailable',
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
                'Enjoy 1 Year',
                'continue',
                'successfully verified',
                'verification successful',
                'you are verified',
                'Youve been verified',
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
# EMAIL MONITORING JOB WITH RANDOM INTERVALS
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
                            "🌐 Membuka browser...\n"
                            "🖱️ Bot sedang mengklik!\n"
                            "⏳ Tunggu sebentar (30 detik max)..."
                        ),
                        parse_mode="Markdown"
                    )

                    # ✨ Random delay sebelum klik (1-3 detik)
                    await random_delay(1.0, 3.0)

                    # CLICK DENGAN BROWSER ASLI!
                    click_result = await click_verification_link_with_browser(verification_link)

                    if click_result.get("success") and click_result.get("clicked"):
                        # ✨ Random delay setelah klik sebelum check status
                        await random_delay(2.0, 4.0)

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
    """Start background job to monitor email dengan random interval"""
    if context.job_queue is None:
        print("⚠️ JobQueue is None")
        return

    job_name = f"email_monitor_{user_id}"
    current_jobs = context.job_queue.get_jobs_by_name(job_name)
    for job in current_jobs:
        job.schedule_removal()

    # ✨ Gunakan random interval untuk email checking
    random_interval = get_random_email_interval()

    context.job_queue.run_repeating(
        monitor_email_job,
        interval=random_interval,
        first=random_interval,
        chat_id=chat_id,
        user_id=user_id,
        name=job_name
    )

    print(f"🔄 Started email monitoring for user {user_id} (interval: {random_interval:.1f}s)")

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
            print(f"❌ Status check error: {e}")
            return {"success": False, "status": "error"}

async def collect_military_status_with_retry(verification_id: str, status_value: str, org_id: int) -> dict:
    """Call collectMilitaryStatus dengan exponential backoff retry"""
    url = f"{SHEERID_BASE_URL}/rest/v2/verification/{verification_id}/step/collectMilitaryStatus"

    payload = {
        "status": status_value,
        "organization": org_id,
    }

    for attempt in range(MAX_RETRIES):
        try:
            # ✨ Random delay sebelum request
            if attempt > 0:
                backoff = get_backoff_delay(attempt - 1)
                print(f"⏳ Retry {attempt + 1}/{MAX_RETRIES} after {backoff:.1f}s...")
                await asyncio.sleep(backoff)
            else:
                await random_delay(1.5, 3.0)

            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, json=payload)

                if resp.status_code == 200:
                    data = resp.json()
                    return {"success": True, "data": data}
                elif resp.status_code == 429:
                    print(f"⚠️ Rate limited (429) on attempt {attempt + 1}")
                    if attempt < MAX_RETRIES - 1:
                        continue
                    return {"success": False, "message": "Rate limit exceeded", "status_code": 429}
                else:
                    return {"success": False, "message": f"HTTP {resp.status_code}", "status_code": resp.status_code, "response": resp.text}

        except Exception as e:
            print(f"❌ collectMilitaryStatus error (attempt {attempt + 1}): {e}")
            if attempt < MAX_RETRIES - 1:
                continue
            return {"success": False, "message": str(e)}

    return {"success": False, "message": "Max retries exceeded"}

async def collect_personal_info_with_retry(verification_id: str, first_name: str, last_name: str, 
                                          birth_date: str, email: str, discharge_year: int, 
                                          discharge_month: int, metadata: dict) -> dict:
    """Call collectInactiveMilitaryPersonalInfo dengan exponential backoff retry"""
    url = f"{SHEERID_BASE_URL}/rest/v2/verification/{verification_id}/step/collectInactiveMilitaryPersonalInfo"

    payload = {
        "firstName": first_name,
        "lastName": last_name,
        "birthDate": birth_date,
        "email": email,
        "dischargeYear": discharge_year,
        "dischargeMonth": discharge_month,
        "_meta": metadata
    }

    for attempt in range(MAX_RETRIES):
        try:
            # ✨ Random delay sebelum request
            if attempt > 0:
                backoff = get_backoff_delay(attempt - 1)
                print(f"⏳ Retry {attempt + 1}/{MAX_RETRIES} after {backoff:.1f}s...")
                await asyncio.sleep(backoff)
            else:
                await random_delay(2.0, 4.0)

            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, json=payload)

                if resp.status_code == 200:
                    data = resp.json()
                    return {"success": True, "data": data}
                elif resp.status_code == 429:
                    print(f"⚠️ Rate limited (429) on attempt {attempt + 1}")
                    if attempt < MAX_RETRIES - 1:
                        continue
                    return {"success": False, "message": "Rate limit exceeded", "status_code": 429}
                else:
                    return {"success": False, "message": f"HTTP {resp.status_code}", "status_code": resp.status_code, "response": resp.text}

        except Exception as e:
            print(f"❌ collectPersonalInfo error (attempt {attempt + 1}): {e}")
            if attempt < MAX_RETRIES - 1:
                continue
            return {"success": False, "message": str(e)}

    return {"success": False, "message": "Max retries exceeded"}

# =====================================================
# CONVERSATION HANDLERS
# =====================================================

async def veteran_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk /veteran command"""
    user_id = update.effective_user.id

    # Clear existing data
    v_user_data.pop(user_id, None)
    temp_email_storage.pop(user_id, None)
    clear_all_timeouts(context, user_id)

    await log_user_start(update, "VETERAN")

    await update.message.reply_text(
        "🎖️ *VETERAN VERIFICATION BOT*\n\n"
        "Bot ini akan membantu verifikasi status veteran kamu melalui SheerID.\n\n"
        "📋 *Yang dibutuhkan:*\n"
        "• Verification URL (dari SheerID)\n"
        "• Status military (Active/Veteran/Reservist)\n"
        "• Organization/Branch\n"
        "• Nama lengkap\n"
        "• Tanggal lahir\n"
        "• Tanggal discharge (untuk veteran)\n\n"
        "🔗 *Kirimkan verification URL SheerID kamu*\n"
        "Format: `https://services.sheerid.com/verify/...`",
        parse_mode="Markdown"
    )

    set_step_timeout(context, update.effective_chat.id, user_id, "URL")
    return V_URL

async def veteran_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Terima verification URL"""
    user_id = update.effective_user.id
    url_text = update.message.text.strip()

    # Extract verificationId dari URL
    match = re.search(r'verificationId=([a-f0-9]+)', url_text, re.IGNORECASE)
    if not match:
        await update.message.reply_text(
            "❌ *URL tidak valid!*\n\n"
            "Pastikan URL mengandung `verificationId`.\n"
            "Contoh: `https://services.sheerid.com/verify/xxx?verificationId=abc123`",
            parse_mode="Markdown"
        )
        return V_URL

    verification_id = match.group(1)

    v_user_data[user_id] = {
        "verification_id": verification_id,
        "original_url": url_text
    }

    await update.message.reply_text(
        f"✅ *Verification ID diterima!*\n\n"
        f"ID: `{verification_id}`\n\n"
        "🎖️ *Pilih status military kamu:*",
        reply_markup=STATUS_KEYBOARD,
        parse_mode="Markdown"
    )

    clear_all_timeouts(context, user_id)
    set_step_timeout(context, update.effective_chat.id, user_id, "STATUS")
    return V_STATUS

async def veteran_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle status selection"""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    if user_id not in v_user_data:
        await query.message.reply_text("❌ Session expired. Mulai lagi dengan /veteran")
        return ConversationHandler.END

    status_value = query.data.replace("status_", "")
    v_user_data[user_id]["status"] = status_value

    status_name_map = {
        "ACTIVE_DUTY": "Active Duty",
        "VETERAN": "Military Veteran or Retiree",
        "RESERVIST": "Reservist or National Guard"
    }

    await query.message.edit_text(
        f"✅ Status: *{status_name_map.get(status_value, status_value)}*\n\n"
        "🏛️ *Pilih Organization/Branch:*",
        reply_markup=get_org_keyboard(status_value),
        parse_mode="Markdown"
    )

    clear_all_timeouts(context, user_id)
    set_step_timeout(context, query.message.chat_id, user_id, "ORG")
    return V_ORG

async def veteran_org(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle organization selection"""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    if user_id not in v_user_data:
        await query.message.reply_text("❌ Session expired. Mulai lagi dengan /veteran")
        return ConversationHandler.END

    org_name = query.data.replace("org_", "")
    status = v_user_data[user_id].get("status")

    # Get org ID based on status
    if status == "RESERVIST":
        org_data = MIL_ORGS_RESERVIST.get(org_name)
    else:
        org_data = MIL_ORGS_ACTIVE_VETERAN.get(org_name)

    if not org_data:
        await query.message.reply_text("❌ Organization tidak valid")
        return V_ORG

    v_user_data[user_id]["organization"] = org_name
    v_user_data[user_id]["organization_id"] = org_data["id"]

    await query.message.edit_text(
        f"✅ Organization: *{org_name}*\n\n"
        "👤 *Kirim nama lengkap kamu*\n"
        "Format: FirstName LastName\n"
        "Contoh: `John Smith`",
        parse_mode="Markdown"
    )

    clear_all_timeouts(context, user_id)
    set_step_timeout(context, query.message.chat_id, user_id, "NAME")
    return V_NAME

async def veteran_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Terima nama lengkap"""
    user_id = update.effective_user.id

    if user_id not in v_user_data:
        await update.message.reply_text("❌ Session expired. Mulai lagi dengan /veteran")
        return ConversationHandler.END

    full_name = update.message.text.strip()
    name_parts = full_name.split(maxsplit=1)

    if len(name_parts) < 2:
        await update.message.reply_text(
            "❌ *Nama tidak lengkap!*\n\n"
            "Kirim FirstName dan LastName.\n"
            "Contoh: `John Smith`",
            parse_mode="Markdown"
        )
        return V_NAME

    first_name, last_name = name_parts[0], name_parts[1]

    v_user_data[user_id]["first_name"] = first_name
    v_user_data[user_id]["last_name"] = last_name

    await update.message.reply_text(
        f"✅ Nama: *{first_name} {last_name}*\n\n"
        "📅 *Kirim tanggal lahir*\n"
        "Format: YYYY-MM-DD\n"
        "Contoh: `1990-05-15`",
        parse_mode="Markdown"
    )

    clear_all_timeouts(context, user_id)
    set_step_timeout(context, update.effective_chat.id, user_id, "BIRTH")
    return V_BIRTH

async def veteran_birth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Terima birth date"""
    user_id = update.effective_user.id

    if user_id not in v_user_data:
        await update.message.reply_text("❌ Session expired. Mulai lagi dengan /veteran")
        return ConversationHandler.END

    birth_date = update.message.text.strip()

    # Validate format YYYY-MM-DD
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', birth_date):
        await update.message.reply_text(
            "❌ *Format tanggal salah!*\n\n"
            "Gunakan format: YYYY-MM-DD\n"
            "Contoh: `1990-05-15`",
            parse_mode="Markdown"
        )
        return V_BIRTH

    v_user_data[user_id]["birth_date"] = birth_date

    status = v_user_data[user_id].get("status")

    # Jika Active Duty, skip discharge date
    if status == "ACTIVE_DUTY":
        v_user_data[user_id]["discharge_year"] = None
        v_user_data[user_id]["discharge_month"] = None
        return await show_confirmation(update, context)

    await update.message.reply_text(
        f"✅ Birth Date: *{birth_date}*\n\n"
        "📅 *Kirim tanggal discharge*\n"
        "Format: YYYY-MM\n"
        "Contoh: `2023-06`",
        parse_mode="Markdown"
    )

    clear_all_timeouts(context, user_id)
    set_step_timeout(context, update.effective_chat.id, user_id, "DISCHARGE")
    return V_DISCHARGE

async def veteran_discharge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Terima discharge date"""
    user_id = update.effective_user.id

    if user_id not in v_user_data:
        await update.message.reply_text("❌ Session expired. Mulai lagi dengan /veteran")
        return ConversationHandler.END

    discharge_text = update.message.text.strip()

    # Validate format YYYY-MM
    match = re.match(r'^(\d{4})-(\d{2})$', discharge_text)
    if not match:
        await update.message.reply_text(
            "❌ *Format tanggal salah!*\n\n"
            "Gunakan format: YYYY-MM\n"
            "Contoh: `2023-06`",
            parse_mode="Markdown"
        )
        return V_DISCHARGE

    discharge_year = int(match.group(1))
    discharge_month = int(match.group(2))

    v_user_data[user_id]["discharge_year"] = discharge_year
    v_user_data[user_id]["discharge_month"] = discharge_month

    return await show_confirmation(update, context)

async def show_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tampilkan konfirmasi data"""
    user_id = update.effective_user.id
    data = v_user_data[user_id]

    status_map = {
        "ACTIVE_DUTY": "Active Duty",
        "VETERAN": "Military Veteran or Retiree",
        "RESERVIST": "Reservist or National Guard"
    }

    confirmation_text = (
        "📋 *KONFIRMASI DATA*\n\n"
        f"🎖️ Status: {status_map.get(data['status'], data['status'])}\n"
        f"🏛️ Organization: {data['organization']}\n"
        f"👤 Nama: {data['first_name']} {data['last_name']}\n"
        f"📅 Tanggal Lahir: {data['birth_date']}\n"
    )

    if data.get("discharge_year"):
        confirmation_text += f"📅 Discharge: {data['discharge_year']}-{data['discharge_month']:02d}\n"

    confirmation_text += "\n✅ *Data sudah benar?*"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Ya, Lanjutkan", callback_data="confirm_yes"),
            InlineKeyboardButton("❌ Batal", callback_data="confirm_no")
        ]
    ])

    await update.message.reply_text(
        confirmation_text,
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

    clear_all_timeouts(context, user_id)
    return V_CONFIRM

async def veteran_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle konfirmasi"""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    if query.data == "confirm_no":
        v_user_data.pop(user_id, None)
        await query.message.edit_text(
            "❌ *Verifikasi dibatalkan*\n\n"
            "Kirim /veteran untuk mulai lagi.",
            parse_mode="Markdown"
        )
        return ConversationHandler.END

    # Proceed dengan verifikasi
    data = v_user_data[user_id]

    await query.message.edit_text(
        "🔄 *Memproses verifikasi...*\n\n"
        "⏳ Tunggu sebentar...",
        parse_mode="Markdown"
    )

    # ✨ Random delay sebelum mulai proses
    await random_delay(1.0, 2.5)

    # Step 1: collectMilitaryStatus
    await query.message.edit_text(
        "🔄 *Step 1/3: Mengirim military status...*",
        parse_mode="Markdown"
    )

    status_result = await collect_military_status_with_retry(
        data["verification_id"],
        data["status"],
        data["organization_id"]
    )

    if not status_result["success"]:
        error_msg = status_result.get("message", "Unknown error")
        await query.message.edit_text(
            f"❌ *SUBMISSION FAILED*\n\n"
            f"Error: collectMilitaryStatus failed: {error_msg}\n\n"
            "Coba lagi atau /veteran restart.",
            parse_mode="Markdown"
        )
        await log_verification_result(
            user_id,
            f"{data['first_name']} {data['last_name']}",
            "N/A",
            "failed_status",
            False,
            error_msg
        )
        v_user_data.pop(user_id, None)
        return ConversationHandler.END

    # ✨ Random delay antara steps
    await random_delay(2.0, 4.0)

    # Step 2: Generate email
    await query.message.edit_text(
        "🔄 *Step 2/3: Generating temporary email...*",
        parse_mode="Markdown"
    )

    email_result = await create_temp_email()
    if not email_result["success"]:
        await query.message.edit_text(
            "❌ *Email generation failed*\n\nCoba lagi nanti.",
            parse_mode="Markdown"
        )
        v_user_data.pop(user_id, None)
        return ConversationHandler.END

    email = email_result["email"]

    # ✨ Random delay sebelum submit personal info
    await random_delay(2.0, 3.5)

    # Step 3: collectInactiveMilitaryPersonalInfo
    await query.message.edit_text(
        f"🔄 *Step 3/3: Submitting personal info...*\n\n"
        f"📧 Email: `{email}`",
        parse_mode="Markdown"
    )

    # Prepare metadata (extract dari original URL if any)
    metadata = {"locale": "en-US"}

    personal_result = await collect_personal_info_with_retry(
        data["verification_id"],
        data["first_name"],
        data["last_name"],
        data["birth_date"],
        email,
        data.get("discharge_year") or 0,
        data.get("discharge_month") or 0,
        metadata
    )

    if not personal_result["success"]:
        error_msg = personal_result.get("message", "Unknown error")
        await query.message.edit_text(
            f"❌ *SUBMISSION FAILED*\n\n"
            f"Error: {error_msg}\n\n"
            "Coba lagi atau /veteran restart.",
            parse_mode="Markdown"
        )
        await log_verification_result(
            user_id,
            f"{data['first_name']} {data['last_name']}",
            email,
            "failed_personal",
            False,
            error_msg
        )
        v_user_data.pop(user_id, None)
        return ConversationHandler.END

    # Success - start email monitoring
    await query.message.edit_text(
        "✅ *SUBMISSION SUCCESS!*\n\n"
        f"📧 Email: `{email}`\n\n"
        "🔄 *Monitoring email untuk verification link...*\n"
        "⏳ Bot akan otomatis klik link saat email masuk.\n\n"
        "Tunggu maksimal 5 menit...",
        parse_mode="Markdown"
    )

    # Store email data untuk monitoring
    temp_email_storage[user_id] = {
        "email": email,
        "verification_id": data["verification_id"],
        "original_url": data["original_url"],
        "check_count": 0
    }

    # Start email monitoring job
    start_email_monitoring(context, query.message.chat_id, user_id)

    await log_verification_result(
        user_id,
        f"{data['first_name']} {data['last_name']}",
        email,
        "submitted",
        True
    )

    clear_all_timeouts(context, user_id)
    v_user_data.pop(user_id, None)

    return ConversationHandler.END

async def veteran_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel conversation"""
    user_id = update.effective_user.id
    v_user_data.pop(user_id, None)
    temp_email_storage.pop(user_id, None)
    clear_all_timeouts(context, user_id)

    await update.message.reply_text(
        "❌ *Verifikasi dibatalkan*\n\n"
        "Kirim /veteran untuk mulai lagi.",
        parse_mode="Markdown"
    )

    return ConversationHandler.END

# =====================================================
# MAIN FUNCTION
# =====================================================

def main():
    """Main function"""
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN tidak ditemukan!")
        return

    # Custom request dengan timeout lebih besar
    request = HTTPXRequest(
        connection_pool_size=20,
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=30.0,
    )

    app = Application.builder().token(BOT_TOKEN).request(request).build()

    # Conversation handler untuk veteran verification
    veteran_conv = ConversationHandler(
        entry_points=[CommandHandler("veteran", veteran_start)],
        states={
            V_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, veteran_url)],
            V_STATUS: [CallbackQueryHandler(veteran_status, pattern=r"^status_")],
            V_ORG: [CallbackQueryHandler(veteran_org, pattern=r"^org_")],
            V_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, veteran_name)],
            V_BIRTH: [MessageHandler(filters.TEXT & ~filters.COMMAND, veteran_birth)],
            V_DISCHARGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, veteran_discharge)],
            V_CONFIRM: [CallbackQueryHandler(veteran_confirm, pattern=r"^confirm_")],
        },
        fallbacks=[CommandHandler("cancel", veteran_cancel)],
        per_chat=True,
        per_user=True,
        per_message=False,
    )

    app.add_handler(veteran_conv)

    print(f"🚀 {BOT_NAME} is starting...")
    print(f"✅ Anti-detection features enabled:")
    print(f"   • Random User-Agent ({len(USER_AGENTS)} variants)")
    print(f"   • Random Viewport ({len(VIEWPORTS)} sizes)")
    print(f"   • Random delays (2-5s)")
    print(f"   • Human behavior simulation")
    print(f"   • Exponential backoff retry")
    print(f"   • Email monitoring with random intervals")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
