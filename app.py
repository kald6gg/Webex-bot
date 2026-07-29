from flask import Flask, request
from webexteamssdk import WebexTeamsAPI
from datetime import datetime, date
from zoneinfo import ZoneInfo
import requests
import os
import re


# =========================================================
# الإعدادات
# =========================================================

BOT_TOKEN = os.environ.get("WEBEX_BOT_TOKEN")

if not BOT_TOKEN:
    raise RuntimeError("WEBEX_BOT_TOKEN is missing")

api = WebexTeamsAPI(access_token=BOT_TOKEN)

GOOGLE_API = (
    "https://script.google.com/macros/s/"
    "AKfycbyBwxlPrnk8qUI0NMfpwy7DflFr90UtYgvHsy9Jksxbc8-3QhcgozOlA8s6ZXavismJ"
    "/exec"
)

UAE_TIMEZONE = ZoneInfo("Asia/Dubai")

app = Flask(__name__)


# يحفظ آخر عبارة بحث عندما يكون الاسم موجودًا كسائق ومؤسسة.
# المفتاح هو رقم المستخدم في Webex.
pending_searches = {}


# =========================================================
# الصفحة الرئيسية
# =========================================================

@app.route("/")
def home():
    return "Webex Bot is Running"


# =========================================================
# استقبال رسائل Webex
# =========================================================

@app.route("/webhook", methods=["POST"])
def webhook():
    webhook_data = request.get_json(silent=True) or {}
    message_data = webhook_data.get("data", {})

    message_id = message_data.get("id")
    sender_email = message_data.get("personEmail", "")
    sender_id = message_data.get("personId", "")

    if not message_id:
        return "OK"

    # تجاهل رسائل البوت نفسه.
    try:
        bot_email = api.people.me().emails[0]

        if sender_email.lower() == bot_email.lower():
            return "OK"
    except Exception:
        pass

    try:
        message = api.messages.get(message_id)
    except Exception as error:
        print("Unable to read Webex message:", error)
        return "OK"

    room_id = message.roomId
    user_text = clean_text(message.text)

    if not user_text:
        send_message(
            room_id,
            "يرجى إرسال رقم المركبة أو اسم السائق أو اسم المؤسسة."
        )
        return "OK"

    normalized_text = normalize_arabic(user_text)

    # الردود الحوارية.
    conversational_reply = get_conversational_reply(normalized_text)

    if conversational_reply:
        send_message(room_id, conversational_reply)
        return "OK"

    # معالجة اختيار المستخدم عند تشابه اسم السائق والمؤسسة.
    if sender_id in pending_searches:
        selected_type = detect_search_type_choice(normalized_text)

        if selected_type:
            previous_query = pending_searches.pop(sender_id)

            search_and_reply(
                room_id=room_id,
                query=previous_query,
                search_type=selected_type,
                sender_id=sender_id
            )

            return "OK"

    # إزالة عبارات البحث الشائعة من الرسالة.
    query = extract_search_query(user_text)

    if not query:
        send_message(
            room_id,
            "يرجى إرسال رقم المركبة أو اسم السائق أو اسم المؤسسة."
        )
        return "OK"

    search_and_reply(
        room_id=room_id,
        query=query,
        search_type="",
        sender_id=sender_id
    )

    return "OK"


# =========================================================
# إرسال البحث إلى Google Apps Script
# =========================================================

def search_and_reply(room_id, query, search_type="", sender_id=""):
    params = {"query": query}

    if search_type:
        params["type"] = search_type

    try:
        response = requests.get(
            GOOGLE_API,
            params=params,
            timeout=20
        )

        response.raise_for_status()

    except requests.RequestException as error:
        print("Google API request error:", error)

        send_message(
            room_id,
            "⚠️ تعذر الاتصال بقاعدة البيانات حاليًا. يرجى المحاولة مرة أخرى."
        )
        return

    try:
        result = response.json()

    except ValueError:
        print("Invalid Google API response:", response.text)

        send_message(
            room_id,
            "⚠️ وصلت استجابة غير صحيحة من قاعدة البيانات."
        )
        return

    status = result.get("status")

    # الاسم موجود في السائق والمؤسسة معًا.
    if status == "choose":
        if sender_id:
            pending_searches[sender_id] = query

        reply = f"""
🔎 تم العثور على أكثر من نوع من النتائج لعبارة "{query}".

يرجى تحديد نوع البحث:

1️⃣ 👤 السائق
2️⃣ 🏢 المؤسسة

أرسل رقم الخيار أو اكتب:
السائق
أو
المؤسسة
""".strip()

        send_message(room_id, reply)
        return

    if status == "not_found":
        send_message(
            room_id,
            "❌ لم يتم العثور على مركبة أو سائق أو مؤسسة مطابقة."
        )
        return

    if status == "error":
        send_message(
            room_id,
            result.get(
                "message",
                "⚠️ تعذر تنفيذ البحث."
            )
        )
        return

    if status != "found":
        send_message(
            room_id,
            "⚠️ حدث خطأ غير متوقع أثناء البحث."
        )
        return

    results = result.get("results", [])
    search_result_type = result.get("search_type", "")

    if not results:
        send_message(
            room_id,
            "❌ لم يتم العثور على نتائج مطابقة."
        )
        return

    # نتيجة واحدة: عرض التفاصيل كاملة.
    if len(results) == 1:
        send_message(
            room_id,
            build_vehicle_reply(results[0])
        )
        return

    # عدة نتائج: عرض قائمة مختصرة.
    send_message(
        room_id,
        build_multiple_results_reply(
            query=query,
            results=results,
            search_type=search_result_type
        )
    )


# =========================================================
# إنشاء نتيجة المركبة
# =========================================================

def build_vehicle_reply(vehicle):
    lines = []

    plate = clean_text(vehicle.get("plate"))
    plate_source = clean_text(vehicle.get("plate_source"))
    category = clean_text(vehicle.get("category"))

    # العنوان.
    header_parts = []

    if plate:
        header_parts.append(plate)

    if plate_source:
        header_parts.append(plate_source)

    if category:
        header_parts.append(category)

    if header_parts:
        lines.append(
            "🚘 رقم المركبة: " + " | ".join(header_parts)
        )

    add_field(lines, "👤 السائق", vehicle.get("driver"))
    add_field(lines, "🏢 المؤسسة", vehicle.get("organization"))

    add_field(lines, "👥 مرافق السائق", vehicle.get("companion1"))
    add_field(lines, "👥 المرافق الثاني", vehicle.get("companion2"))
    add_field(lines, "👥 المرافق الثالث", vehicle.get("companion3"))

    # مصدر اللوحة والفئة موجودان في العنوان،
    # لذلك لا نكررهما مرة أخرى.
    add_field(lines, "🎨 لون المركبة", vehicle.get("color"))
    add_field(lines, "🚙 نوع المركبة", vehicle.get("vehicle_type"))
    add_field(lines, "🅿️ مكان الوقوف", vehicle.get("parking"))

    from_date = clean_text(vehicle.get("from_date"))
    to_date = clean_text(vehicle.get("to_date"))

    date_lines = build_permit_date_lines(from_date, to_date)

    if date_lines:
        lines.append("")
        lines.extend(date_lines)

    permit_status = get_permit_status(from_date, to_date)

    if permit_status:
        lines.append("")
        lines.append(permit_status)

    entry_time = clean_text(vehicle.get("entry_time_from"))
    exit_time = clean_text(vehicle.get("exit_time_to"))

    time_status = get_entry_time_status(entry_time, exit_time)

    if time_status:
        lines.append("")
        lines.extend(time_status)

    action = clean_text(vehicle.get("action"))

    if action:
        lines.append("")
        lines.append(f"📌 الإجراءات المطلوبة: {action}")

    return "\n".join(lines).strip()


def add_field(lines, label, value):
    value = clean_text(value)

    if value:
        lines.append(f"{label}: {value}")


# =========================================================
# حالة تاريخ التصريح
# =========================================================

def build_permit_date_lines(from_date, to_date):
    if not from_date and not to_date:
        return []

    if from_date and to_date and from_date == to_date:
        return [
            f"📅 التاريخ: {from_date}",
            "ℹ️ التصريح صالح ليوم واحد فقط"
        ]

    lines = []

    if from_date:
        lines.append(f"📅 من: {from_date}")

    if to_date:
        lines.append(f"📅 إلى: {to_date}")

    return lines


def get_permit_status(from_date, to_date):
    start_date = parse_sheet_date(from_date)
    end_date = parse_sheet_date(to_date)

    today = datetime.now(UAE_TIMEZONE).date()

    if not start_date and not end_date:
        return ""

    if start_date and today < start_date:
        remaining = (start_date - today).days

        if remaining == 1:
            return "⏳ التصريح يبدأ غدًا."

        return f"⏳ التصريح لم يبدأ بعد، ويتبقى {remaining} أيام."

    if end_date:
        if today > end_date:
            return "❌ انتهت صلاحية التصريح."

        if today == end_date:
            return "⚠️ التصريح ينتهي اليوم."

        remaining = (end_date - today).days

        if remaining == 1:
            return "✅ التصريح ساري، وينتهي غدًا."

        return f"✅ التصريح ساري، ويتبقى {remaining} أيام."

    return "✅ التصريح ساري."


def parse_sheet_date(value):
    value = clean_text(value)

    if not value:
        return None

    formats = (
        "%d/%m/%Y",
        "%Y-%m-%d",
        "%d-%m-%Y"
    )

    for date_format in formats:
        try:
            return datetime.strptime(value, date_format).date()
        except ValueError:
            continue

    return None


# =========================================================
# حالة فترة الدخول
# =========================================================

def get_entry_time_status(entry_time, exit_time):
    if not entry_time and not exit_time:
        return ["✅ الدخول مسموح طوال اليوم."]

    lines = ["🕒 فترة السماح بالدخول:"]

    if entry_time:
        lines.append(f"من {entry_time}")

    if exit_time:
        lines.append(f"إلى {exit_time}")

    start_time = parse_time(entry_time)
    end_time = parse_time(exit_time)
    now = datetime.now(UAE_TIMEZONE).time().replace(second=0, microsecond=0)

    if start_time and end_time:
        # فترة عادية مثل 08:00 إلى 17:00.
        if start_time <= end_time:
            if now < start_time:
                lines.append("⏳ لم يبدأ وقت الدخول المسموح بعد.")
            elif now > end_time:
                lines.append("❌ انتهى وقت الدخول المسموح.")
            else:
                lines.append("✅ الدخول مسموح الآن.")

        # فترة تمتد بعد منتصف الليل، مثل 20:00 إلى 02:00.
        else:
            if now >= start_time or now <= end_time:
                lines.append("✅ الدخول مسموح الآن.")
            else:
                lines.append("❌ الدخول غير مسموح في الوقت الحالي.")

    elif start_time:
        if now >= start_time:
            lines.append("✅ الدخول مسموح الآن.")
        else:
            lines.append("⏳ لم يبدأ وقت الدخول المسموح بعد.")

    elif end_time:
        if now <= end_time:
            lines.append("✅ الدخول مسموح الآن.")
        else:
            lines.append("❌ انتهى وقت الدخول المسموح.")

    return lines


def parse_time(value):
    value = clean_text(value)

    if not value:
        return None

    formats = (
        "%H:%M",
        "%H:%M:%S",
        "%I:%M %p"
    )

    for time_format in formats:
        try:
            return datetime.strptime(value, time_format).time()
        except ValueError:
            continue

    return None


# =========================================================
# عرض عدة نتائج
# =========================================================

def build_multiple_results_reply(query, results, search_type):
    if search_type == "organization":
        title = f'🏢 نتائج المؤسسة المطابقة لعبارة "{query}"'
    elif search_type == "driver":
        title = f'👤 نتائج السائق المطابقة لعبارة "{query}"'
    else:
        title = f'🔎 النتائج المطابقة لعبارة "{query}"'

    lines = [
        title,
        "",
        f"تم العثور على {len(results)} مركبات:"
    ]

    # منع الرسالة من أن تصبح طويلة جدًا.
    maximum_results = 20

    for vehicle in results[:maximum_results]:
        plate = clean_text(vehicle.get("plate")) or "بدون رقم"
        driver = clean_text(vehicle.get("driver"))
        organization = clean_text(vehicle.get("organization"))

        details = []

        if driver:
            details.append(f"السائق: {driver}")

        if organization:
            details.append(f"المؤسسة: {organization}")

        if details:
            lines.append(
                f"🚘 {plate} | " + " | ".join(details)
            )
        else:
            lines.append(f"🚘 {plate}")

    if len(results) > maximum_results:
        hidden_count = len(results) - maximum_results
        lines.append("")
        lines.append(
            f"ℹ️ توجد {hidden_count} نتائج إضافية لم تُعرض."
        )

    lines.append("")
    lines.append(
        "يرجى إرسال رقم المركبة المطلوبة لعرض تفاصيلها."
    )

    return "\n".join(lines)


# =========================================================
# الردود الحوارية
# =========================================================

def get_conversational_reply(text):
    greeting_phrases = {
        "السلام عليكم": "وعليكم السلام ورحمة الله وبركاته 🌷\nكيف يمكنني مساعدتك؟",
        "مرحبا": "مرحبًا بك 👋\nأرسل رقم المركبة أو اسم السائق أو اسم المؤسسة.",
        "هلا": "هلا بك 👋\nأرسل رقم المركبة أو اسم السائق أو اسم المؤسسة.",
        "صباح الخير": "صباح النور والسرور ☀️\nكيف يمكنني مساعدتك؟",
        "مساء الخير": "مساء النور والسرور 🌙\nكيف يمكنني مساعدتك؟",
        "شكرا": "العفو، حاضر دائمًا 🌷",
        "يعطيك العافيه": "الله يعافيك ويسلمك 🌷"
    }

    if text in greeting_phrases:
        return greeting_phrases[text]

    usage_triggers = {
        "طريقه الاستخدام",
        "كيف استخدم",
        "كيف استخدمك",
        "الاستخدام"
    }

    if text in usage_triggers:
        return """
📖 طريقة الاستخدام

يمكنك البحث عن بيانات التصريح بإحدى الطرق التالية:

🚘 إرسال رقم المركبة
مثال:
1234

👤 إرسال اسم السائق
مثال:
محمد أحمد

🏢 إرسال اسم المؤسسة
مثال:
مؤسسة النور

سيعرض النظام بيانات المركبة والمؤسسة التابعة لها، وحالة التصريح وإمكانية الدخول.
""".strip()

    help_triggers = {
        "مساعده",
        "المساعده",
        "ساعدني",
        "help",
        "?"
    }

    if text in help_triggers:
        return """
🤖 المساعدة

يمكنني مساعدتك في:

🚘 البحث برقم المركبة.
👤 البحث باسم السائق.
🏢 البحث باسم المؤسسة.
📋 عرض بيانات المركبة.
✅ التحقق من حالة التصريح.
🕒 التحقق من السماح بالدخول.

ما عليك سوى إرسال رقم المركبة أو اسم السائق أو اسم المؤسسة.
""".strip()

    about_triggers = {
        "عني",
        "عنك",
        "من انت",
        "عرفني بنفسك",
        "شو اسمك",
        "ما وظيفتك"
    }

    if text in about_triggers:
        return """
🤖 عني

أنا «خالد»، مساعد إلكتروني تابع لإدارة الحراسات.

أعمل على تسهيل الاستعلام عن تصاريح المركبات، والتحقق من بيانات المركبة والسائق والمؤسسة التابعة لها، بالإضافة إلى عرض حالة التصريح وفترة السماح بالدخول بسرعة ودقة.

تم تطويري لدعم أعمال إدارة الحراسات، والمساهمة في رفع كفاءة إجراءات التحقق وتوفير المعلومات المطلوبة بصورة فورية وموثوقة.

🎯 مهمتي: سرعة الوصول إلى المعلومة، ودقة التحقق، ودعم فرق العمل بكفاءة واحترافية.
""".strip()

    return None


# =========================================================
# فهم اختيار السائق أو المؤسسة
# =========================================================

def detect_search_type_choice(text):
    driver_choices = {
        "1",
        "١",
        "السائق",
        "سائق",
        "اسم السائق",
        "الخيار 1",
        "الخيار الاول"
    }

    organization_choices = {
        "2",
        "٢",
        "المؤسسه",
        "مؤسسه",
        "اسم المؤسسه",
        "الشركه",
        "شركه",
        "الخيار 2",
        "الخيار الثاني"
    }

    if text in driver_choices:
        return "driver"

    if text in organization_choices:
        return "organization"

    return ""


# =========================================================
# تنظيف عبارة البحث
# =========================================================

def extract_search_query(text):
    text = clean_text(text)

    patterns = [
        r"^ابحث\s+عن\s+",
        r"^ابحث\s+",
        r"^رقم\s+المركبه\s+",
        r"^رقم\s+السياره\s+",
        r"^لوحه\s+",
        r"^السائق\s+",
        r"^اسم\s+السائق\s+",
        r"^المؤسسه\s+",
        r"^اسم\s+المؤسسه\s+"
    ]

    normalized = normalize_arabic(text)

    for pattern in patterns:
        normalized = re.sub(pattern, "", normalized).strip()

    return normalized


def normalize_arabic(value):
    value = clean_text(value).lower()

    replacements = {
        "أ": "ا",
        "إ": "ا",
        "آ": "ا",
        "ى": "ي",
        "ة": "ه",
        "ؤ": "و",
        "ئ": "ي"
    }

    for original, replacement in replacements.items():
        value = value.replace(original, replacement)

    value = re.sub(r"[\u064B-\u065F\u0670]", "", value)
    value = value.replace("ـ", "")
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def clean_text(value):
    if value is None:
        return ""

    return re.sub(r"\s+", " ", str(value)).strip()


# =========================================================
# إرسال رسالة إلى Webex
# =========================================================

def send_message(room_id, text):
    try:
        api.messages.create(
            roomId=room_id,
            text=text
        )
    except Exception as error:
        print("Unable to send Webex message:", error)


# =========================================================
# تشغيل التطبيق
# =========================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))

    app.run(
        host="0.0.0.0",
        port=port
    )