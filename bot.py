import os
import re
import random
from datetime import datetime, timedelta

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
BOT_TOKEN = os.environ.get("BOT_TOKEN")        # bot utama
LOG_BOT_TOKEN = os.environ.get("LOG_BOT_TOKEN")  # bot logger (opsional)
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "0"))
BOT_NAME = os.environ.get("BOT_NAME", "VETERAN_BOT")

SHEERID_BASE_URL = "https://services.sheerid.com"  # [web:2]
STEP_TIMEOUT = 300  # 5 menit

# Military organizations (dari spek kamu) [web:6][web:73]
MIL_ORGS = {
    "Army":       {"id": 4070, "name": "Army"},
    "Air Force":  {"id": 4073, "name": "Air Force"},
    "Navy":       {"id": 4072, "name": "Navy"},
    "Marine Corps": {"id": 4071, "name": "Marine Corps"},
    "Coast Guard":  {"id": 4074, "name": "Coast Guard"},
    "Space Force":  {"id": 4544268, "name": "Space Force"},
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

# Bot logger API URL
LOG_API_URL = (
    f"https://api.telegram.org/bot{LOG_BOT_TOKEN}/sendMessage"
    if LOG_BOT_TOKEN
    else None
)

# =====================================================
# STATE CONVERSATION /veteran
# =====================================================
(
    V_URL,          # minta link SheerID
    V_STATUS,       # pilih status military
    V_NAME,         # nama lengkap
    V_BIRTH,        # tanggal lahir
    V_EMAIL,        # email
    V_PHONE,        # phone (optional)
    V_ORG,          # pilih branch
    V_DISCHARGE,    # tanggal discharge
    V_CONFIRM,      # konfirmasi data
) = range(9)

# storage sederhana
v_user_data = {}  # per user_id

# =====================================================
# LOGGING VIA BOT LOGGER
# =====================================================

async def send_log(text: str):
    if not LOG_BOT_TOKEN or ADMIN_CHAT_ID == 0 or not LOG_API_URL:
        print("⚠️ LOG_BOT_TOKEN atau ADMIN_CHAT_ID belum diset, skip log")
        return

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            payload = {
                "chat_id": ADMIN_CHAT_ID,
                "text": text,
            }
            resp = await client.post(LOG_API_URL, json=payload)
            if resp.status_code != 200:
                print(f"❌ Log send failed: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        print(f"❌ Exception sending log: {e}")


async def log_user_start(update: Update, command_name: str):
    user = update.effective_user
    chat = update.effective_chat
    text = (
        f"📥 NEW USER FLOW {command_name} ({BOT_NAME})\n\n"
        f"ID: {user.id}\n"
        f"Name: {user.full_name}\n"
        f"Username: @{user.username or '-'}\n"
        f"Chat ID: {chat.id}\n"
        f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    await send_log(text)


async def log_verification_result(
    user_id: int,
    full_name: str,
    email: str,
    status: str,
    success: bool,
    error_msg: str = "",
):
    status_emoji = "✅" if success else "❌"
    status_text = "SUCCESS" if success else "FAILED"
    text = (
        f"{status_emoji} VETERAN VERIFICATION {status_text} ({BOT_NAME})\n\n"
        f"ID: {user_id}\n"
        f"Name: {full_name}\n"
        f"Email: {email}\n"
        f"SheerID currentStep: {status}\n"
    )
    if not success:
        text += f"\nError: {error_msg}"
    await send_log(text)

# =====================================================
# TIMEOUT PER STEP
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
                "Silakan kirim /veteran untuk mengulang dari awal."
            ),
            parse_mode="Markdown",
        )
    except Exception as e:
        print(f"❌ Failed to send timeout message: {e}")

    print(f"⏰ Timeout {step_name} untuk user {user_id}")


def set_step_timeout(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int, step: str
):
    if context.job_queue is None:
        print("⚠️ JobQueue is None, skip set_step_timeout")
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
        print("⚠️ JobQueue is None, skip clear_all_timeouts")
        return

    for step in ["URL", "STATUS", "NAME", "BIRTH", "EMAIL", "PHONE", "ORG", "DISCHARGE"]:
        job_name = f"timeout_veteran_{step}_{user_id}"
        for job in context.job_queue.get_jobs_by_name(job_name):
            job.schedule_removal()

# =====================================================
# HELPER SHEERID
# =====================================================

async def check_sheerid_status(verification_id: str) -> dict:
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            url = f"{SHEERID_BASE_URL}/rest/v2/verification/{verification_id}"
            resp = await client.get(url)
            if resp.status_code != 200:
                msg = f"Status check failed: {resp.status_code}"
                print("❌", msg)
                return {"success": False, "status": "unknown", "message": msg}

            data = resp.json()
            step = data.get("currentStep", "unknown")
            print(f"🔎 SheerID currentStep: {step}")
            return {"success": True, "status": step, "data": data}
        except httpx.TimeoutException:
            msg = "Status check timeout"
            print("❌", msg)
            return {"success": False, "status": "unknown", "message": msg}
        except Exception as e:
            msg = f"Status check error: {str(e)}"
            print("❌", msg)
            return {"success": False, "status": "unknown", "message": msg}

async def submit_military_flow(
    verification_id: str,
    status: str,
    first_name: str,
    last_name: str,
    birth_date: str,
    email: str,
    phone: str,
    org: dict,
    discharge_date: str,
) -> dict:
    """
    Flow:
    1) POST collectMilitaryStatus
    2) POST collectInactiveMilitaryPersonalInfo
    (tanpa bearer token, nebeng verificationId dari link user, sama konsep dengan teacher bot kamu). [web:2][web:8][web:75]
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            # Step 1: collectMilitaryStatus
            step1_url = (
                f"{SHEERID_BASE_URL}/rest/v2/verification/"
                f"{verification_id}/step/collectMilitaryStatus"
            )
            step1_body = {"status": status}
            print("🚀 Step 1 collectMilitaryStatus:", step1_url, step1_body)
            r1 = await client.post(step1_url, json=step1_body)
            if r1.status_code != 200:
                msg = f"collectMilitaryStatus failed: {r1.status_code}"
                print("❌", msg, r1.text[:200])
                return {"success": False, "message": msg}

            d1 = r1.json()
            submission_url = d1.get("submissionUrl")
            if not submission_url:
                msg = "No submissionUrl from collectMilitaryStatus"
                print("❌", msg, d1)
                return {"success": False, "message": msg}

            # Step 2: collectInactiveMilitaryPersonalInfo
            submission_opt_in = (
                "By submitting the personal information above, I acknowledge that my personal "
                "information is being collected under the privacy policy of the business from "
                "which I am seeking a discount, and I understand that my personal information "
                "will be shared with SheerID as a processor/third-party service provider in "
                "order for SheerID to confirm my eligibility for a special offer."
            )  # disingkat dari teks resmi untuk bot. [web:18]

            payload2 = {
                "firstName": first_name,
                "lastName": last_name,
                "birthDate": birth_date,
                "email": email,
                "phoneNumber": phone,
                "organization": {
                    "id": org["id"],
                    "name": org["name"],
                },
                "dischargeDate": discharge_date,
                "locale": "en-US",
                "country": "US",
                "metadata": {
                    "marketConsentValue": False,
                    "refererUrl": "",
                    "verificationId": verification_id,
                    "flags": "{\"doc-upload-considerations\":\"default\",\"doc-upload-may24\":\"default\",\"doc-upload-redesign-use-legacy-message-keys\":false,\"docUpload-assertion-checklist\":\"default\",\"include-cvec-field-france-student\":\"not-labeled-optional\",\"org-search-overlay\":\"default\",\"org-selected-display\":\"default\"}",
                    "submissionOptIn": submission_opt_in,
                },
            }
            print("🚀 Step 2 collectInactiveMilitaryPersonalInfo:", submission_url)
            r2 = await client.post(submission_url, json=payload2)
            if r2.status_code != 200:
                msg = f"collectInactiveMilitaryPersonalInfo failed: {r2.status_code}"
                print("❌", msg, r2.text[:200])
                return {"success": False, "message": msg}

            return {"success": True, "message": "Military info submitted"}

        except httpx.TimeoutException:
            msg = "Request timeout to SheerID - please try again"
            print("❌", msg)
            return {"success": False, "message": msg}
        except Exception as e:
            msg = f"Submission error: {str(e)}"
            print("❌", msg)
            return {"success": False, "message": msg}

# =====================================================
# HANDLER /veteran
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
        "Kirim SheerID verification URL militer kamu:\n\n"
        "`https://services.sheerid.com/verify/...verificationId=...`\n\n"
        "Contoh:\n"
        "`https://services.sheerid.com/verify/abcd...?verificationId=1234abcd...`\n\n"
        "*⏰ Kamu punya 5 menit untuk kirim link*",
        parse_mode="Markdown",
    )
    return V_URL

async def veteran_get_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    url = update.message.text.strip()

    # bisa disesuaikan pattern-nya; di contoh teacher pakai 24 hex
    match = re.search(r"verificationId=([A-Za-z0-9\-]+)", url)
    if not match:
        await update.message.reply_text(
            "❌ *Invalid URL!*\n\n"
            "Harus ada parameter `verificationId=...` di URL.\n\n"
            "*⏰ Kamu punya 5 menit lagi*",
            parse_mode="Markdown",
        )
        set_step_timeout(context, chat_id, user_id, "URL")
        return V_URL

    verification_id = match.group(1)
    v_user_data[user_id] = {"verification_id": verification_id}

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
        await query.edit_message_text(
            "❌ *Session expired*\n\n"
            "Silakan kirim /veteran lagi.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    data = query.data  # status_VETERAN / status_RETIRED / status_ACTIVE_DUTY
    if not data.startswith("status_"):
        await query.edit_message_text(
            "❌ Invalid status.\n\nKirim /veteran lagi.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    status = data.split("_", 1)[1]
    v_user_data[user_id]["status"] = status

    clear_all_timeouts(context, user_id)
    set_step_timeout(context, chat_id, user_id, "NAME")

    await query.edit_message_text(
        f"✅ Status: `{status}`\n\n"
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

    if len(birth) != 10 or birth[4] != "-" or birth[7] != "-":
        await update.message.reply_text(
            "❌ Format tanggal salah.\n"
            "Gunakan format `YYYY-MM-DD`.\n\n"
            "*⏰ Kamu punya 5 menit lagi*",
            parse_mode="Markdown",
        )
        set_step_timeout(context, chat_id, user_id, "BIRTH")
        return V_BIRTH

    v_user_data.setdefault(user_id, {})
    v_user_data[user_id]["birth_date"] = birth

    clear_all_timeouts(context, user_id)
    set_step_timeout(context, chat_id, user_id, "EMAIL")

    await update.message.reply_text(
        f"✅ *Birth date:* `{birth}`\n\n"
        "Kirim *email* kamu.\n\n"
        "*⏰ Kamu punya 5 menit*",
        parse_mode="Markdown",
    )
    return V_EMAIL

async def veteran_get_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    email = update.message.text.strip()

    if "@" not in email or "." not in email:
        await update.message.reply_text(
            "❌ Format email tidak valid.\n\n"
            "*⏰ Kamu punya 5 menit lagi*",
            parse_mode="Markdown",
        )
        set_step_timeout(context, chat_id, user_id, "EMAIL")
        return V_EMAIL

    v_user_data.setdefault(user_id, {})
    v_user_data[user_id]["email"] = email

    clear_all_timeouts(context, user_id)
    set_step_timeout(context, chat_id, user_id, "PHONE")

    await update.message.reply_text(
        f"✅ *Email:* `{email}`\n\n"
        "Kirim *nomor telepon* (boleh kosong, kirim `-` kalau tidak ada).\n\n"
        "*⏰ Kamu punya 5 menit*",
        parse_mode="Markdown",
    )
    return V_PHONE

async def veteran_get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    phone = update.message.text.strip()
    if phone == "-":
        phone = ""

    v_user_data.setdefault(user_id, {})
    v_user_data[user_id]["phone"] = phone

    clear_all_timeouts(context, user_id)
    set_step_timeout(context, chat_id, user_id, "ORG")

    await update.message.reply_text(
        "✅ *Phone saved*\n\n"
        "Pilih *branch / organization* kamu:",
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
        await query.edit_message_text(
            "❌ *Session expired*\n\n"
            "Silakan /veteran lagi.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    data = query.data  # org_Army / org_Navy / etc
    if not data.startswith("org_"):
        await query.edit_message_text(
            "❌ Invalid organization.\n\nKirim /veteran lagi.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    org_name = data.split("_", 1)[1]
    org = MIL_ORGS.get(org_name)
    if not org:
        await query.edit_message_text(
            "❌ Unknown organization.\n\nKirim /veteran lagi.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    v_user_data[user_id]["organization"] = org

    clear_all_timeouts(context, user_id)
    set_step_timeout(context, chat_id, user_id, "DISCHARGE")

    await query.edit_message_text(
        f"✅ Branch: *{org_name}*\n\n"
        "Kirim *discharge date* (tanggal keluar / pensiun) format `YYYY-MM-DD`.\n"
        "Kalau masih aktif, pakai tanggal yang masuk akal.\n\n"
        "*⏰ Kamu punya 5 menit*",
        parse_mode="Markdown",
    )
    return V_DISCHARGE

async def veteran_get_discharge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    ddate = update.message.text.strip()

    if len(ddate) != 10 or ddate[4] != "-" or ddate[7] != "-":
        await update.message.reply_text(
            "❌ Format tanggal salah.\n"
            "Gunakan format `YYYY-MM-DD`.\n\n"
            "*⏰ Kamu punya 5 menit lagi*",
            parse_mode="Markdown",
        )
        set_step_timeout(context, chat_id, user_id, "DISCHARGE")
        return V_DISCHARGE

    v_user_data.setdefault(user_id, {})
    v_user_data[user_id]["discharge_date"] = ddate

    data = v_user_data[user_id]
    summary = (
        "🔎 *Konfirmasi data veteran:*\n\n"
        f"VerificationId: `{data['verification_id']}`\n"
        f"Status: `{data['status']}`\n"
        f"Name: `{data['full_name']}`\n"
        f"Birth date: `{data['birth_date']}`\n"
        f"Email: `{data['email']}`\n"
        f"Phone: `{data['phone']}`\n"
        f"Branch: `{data['organization']['name']}` (id={data['organization']['id']})\n"
        f"Discharge date: `{data['discharge_date']}`\n\n"
        "Ketik `OK` untuk submit ke SheerID, atau `/cancel` untuk batal."
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
            "Ketik `OK` untuk lanjut submit atau `/cancel` untuk batal.",
            parse_mode="Markdown",
        )
        return V_CONFIRM

    if user_id not in v_user_data:
        await update.message.reply_text(
            "❌ Session hilang.\nSilakan /veteran lagi.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    data = v_user_data[user_id]
    verification_id = data["verification_id"]

    await update.message.reply_text(
        "🚀 Mengirim data ke SheerID...\n"
        "`collectMilitaryStatus` → `collectInactiveMilitaryPersonalInfo`",
        parse_mode="Markdown",
    )

    result = await submit_military_flow(
        verification_id=verification_id,
        status=data["status"],
        first_name=data["first_name"],
        last_name=data["last_name"],
        birth_date=data["birth_date"],
        email=data["email"],
        phone=data["phone"],
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
            "Silakan coba lagi nanti atau /veteran untuk mulai ulang.",
            parse_mode="Markdown",
        )
    else:
        if status == "success":
            await update.message.reply_text(
                "✅ *Military info submitted & status success*\n\n"
                "Kamu seharusnya melihat halaman `Status verified` di browser.",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(
                "✅ *Military info submitted*\n\n"
                f"Current SheerID step: `{status}`\n"
                "Tunggu beberapa menit dan cek lagi halaman verifikasi di browser.",
                parse_mode="Markdown",
            )

    v_user_data.pop(user_id, None)
    clear_all_timeouts(context, user_id)

    await update.message.reply_text(
        "Ketik /veteran kalau mau verifikasi lagi.",
        parse_mode="Markdown",
    )
    return ConversationHandler.END

async def veteran_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    v_user_data.pop(user_id, None)
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
    print(f"🎖 {BOT_NAME} - Veteran Flow")
    print("=" * 70)
    print(f"🤖 Bot Token: {BOT_TOKEN[:10]}...{BOT_TOKEN[-5:]}")
    print(f"👮 Admin Chat ID: {ADMIN_CHAT_ID}")
    print(f"📨 LOG_BOT_TOKEN set: {bool(LOG_BOT_TOKEN)}")
    print(f"⏰ Step timeout: {STEP_TIMEOUT} detik")
    print("=" * 70 + "\n")

    request = HTTPXRequest(
        read_timeout=30,
        write_timeout=30,
        connect_timeout=10,
        pool_timeout=10,
    )  # [web:66][web:70]

    app = Application.builder().token(BOT_TOKEN).request(request).build()

    conv_veteran = ConversationHandler(
        entry_points=[CommandHandler("veteran", veteran_start)],
        states={
            V_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, veteran_get_url)],
            V_STATUS: [CallbackQueryHandler(veteran_status_callback, pattern="^status_")],
            V_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, veteran_get_name)],
            V_BIRTH: [MessageHandler(filters.TEXT & ~filters.COMMAND, veteran_get_birth)],
            V_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, veteran_get_email)],
            V_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, veteran_get_phone)],
            V_ORG: [CallbackQueryHandler(veteran_org_callback, pattern="^org_")],
            V_DISCHARGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, veteran_get_discharge)],
            V_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, veteran_confirm)],
        },
        fallbacks=[CommandHandler("cancel", veteran_cancel)],
        conversation_timeout=None,
        name="veteran_conv",
    )

    app.add_handler(conv_veteran)

    print("🚀 Veteran bot is starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
