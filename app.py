from flask import Flask, request
from webexteamssdk import WebexTeamsAPI
from datetime import datetime
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


# =========================================================
# حفظ حالة المحادثات
# =========================================================

# يحفظ عبارة البحث عندما يكون الاسم موجودًا
# كسائق ومؤسسة في الوقت نفسه.
pending_searches = {}


# يحفظ خطوات التحقق من دخول الشخص إلى البوابة.
#
# مثال:
# {
#     "WEBEX_PERSON_ID": {
#         "step": "choose_gate"
#     }
# }
pending_gate_checks = {}


# =========================================================
# بيانات البوابات
# =========================================================

GATES = {
    "1": {
        "code": "main",
        "name": "البوابة الرئيسية"
    },
    "2": {
        "code": "employees",
        "name": "بوابة الموظفين"
    },
    "3": {
        "code": "misdemeanors",
        "name": "بوابة الجنح"
    },
    "4": {
        "code": "women",
        "name": "بوابة النساء"
    },
    "5": {
        "code": "precautionary",
        "name": "بوابة التدابير الاحترازية"
    },
    "6": {
        "code": "central",
        "name": "البوابة المركزية"
    }
}


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

    # تجاهل الرسائل التي يرسلها البوت نفسه.
    try:
        bot_person = api.people.me()
        bot_emails = bot_person.emails or []

        if (
            sender_email
            and bot_emails
            and sender_email.lower() == bot_emails[0].lower()
        ):
            return "OK"

    except Exception as error:
        print("Unable to read bot information:", error)

    # قراءة نص الرسالة من Webex.
    try:
        message = api.messages.get(message_id)

    except Exception as error:
        print("Unable to read Webex message:", error)
        return "OK"

    room_id = message.roomId
    user_text = clean_text(message.text)
    normalized_text = normalize_arabic(user_text)

    if not user_text:
        send_message(
            room_id,
            "يرجى إرسال رقم المركبة أو اسم السائق أو اسم المؤسسة."
        )
        return "OK"

    # =====================================================
    # إلغاء العملية الحالية
    # =====================================================

    if normalized_text in {
        "الغاء",
        "الغي",
        "خروج",
        "انهاء",
        "cancel"
    }:
        pending_gate_checks.pop(sender_id, None)
        pending_searches.pop(sender_id, None)

        send_message(
            room_id,
            "✅ تم إلغاء العملية الحالية."
        )
        return "OK"

    # =====================================================
    # متابعة خطوات التحقق من البوابة
    # =====================================================

    if sender_id in pending_gate_checks:
        gate_state = pending_gate_checks.get(sender_id, {})
        current_step = gate_state.get("step", "")

        # الخطوة الأولى: اختيار البوابة.
        if current_step == "choose_gate":
            selected_gate = detect_gate_choice(normalized_text)

            if not selected_gate:
                send_message(
                    room_id,
                    build_gate_menu(
                        "⚠️ الاختيار غير صحيح. اختر رقمًا من 1 إلى 6."
                    )
                )
                return "OK"

            pending_gate_checks[sender_id] = {
                "step": "enter_person",
                "gate_code": selected_gate["code"],
                "gate_name": selected_gate["name"]
            }

            send_message(
                room_id,
                (
                    f"✅ تم اختيار {selected_gate['name']}.\n\n"
                    "👤 اكتب اسم الشخص الذي تريد التحقق منه."
                )
            )
            return "OK"

        # الخطوة الثانية: إدخال اسم الشخص.
        if current_step == "enter_person":
            person_name = clean_text(user_text)

            if not person_name:
                send_message(
                    room_id,
                    "👤 يرجى كتابة اسم الشخص."
                )
                return "OK"

            gate_code = gate_state.get("gate_code", "")
            gate_name = gate_state.get("gate_name", "")

            # نحذف الحالة قبل إرسال الطلب،
            # حتى لا يظل المستخدم عالقًا في الخطوة نفسها.
            pending_gate_checks.pop(sender_id, None)

            check_person_gate_and_reply(
                room_id=room_id,
                person_name=person_name,
                gate_code=gate_code,
                gate_name=gate_name
            )
            return "OK"

        # إذا كانت الحالة غير صحيحة نحذفها.
        pending_gate_checks.pop(sender_id, None)

    # =====================================================
    # بدء خدمة التحقق من البوابات
    # =====================================================

    if is_gate_service_trigger(normalized_text):
        pending_searches.pop(sender_id, None)

        pending_gate_checks[sender_id] = {
            "step": "choose_gate"
        }

        send_message(
            room_id,
            build_gate_menu("👋 هلا بك.")
        )
        return "OK"

    # =====================================================
    # اختيار السائق أو المؤسسة عند تشابه النتائج
    # =====================================================

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

    # =====================================================
    # الردود الحوارية
    # =====================================================

    conversational_reply = get_conversational_reply(normalized_text)

    if conversational_reply:
        send_message(
            room_id,
            conversational_reply
        )
        return "OK"

    # =====================================================
    # البحث العادي
    # =====================================================

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
# تشغيل خدمة البوابات
# =========================================================

def is_gate_service_trigger(text):
    triggers = {
        "هلا",
        "البوابات",
        "بوابه",
        "دخول شخص",
        "التحقق من شخص",
        "تحقق من شخص",
        "تصريح شخص",
        "فحص شخص"
    }

    return text in triggers


def build_gate_menu(prefix=""):
    lines = []

    if prefix:
        lines.append(prefix)
        lines.append("")

    lines.extend([
        "🚪 اختر البوابة:",
        "",
        "1️⃣ البوابة الرئيسية",
        "2️⃣ بوابة الموظفين",
        "3️⃣ بوابة الجنح",
        "4️⃣ بوابة النساء",
        "5️⃣ بوابة التدابير الاحترازية",
        "6️⃣ البوابة المركزية",
        "",
        "أرسل رقم البوابة من 1 إلى 6.",
        "للإلغاء اكتب: إلغاء"
    ])

    return "\n".join(lines)


def detect_gate_choice(text):
    normalized = normalize_arabic(text)

    gate_aliases = {
        "1": "1",
        "الرئيسيه": "1",
        "البوابه الرئيسيه": "1",
        "بوابه الرئيسيه": "1",

        "2": "2",
        "الموظفين": "2",
        "بوابه الموظفين": "2",
        "البوابه الموظفين": "2",

        "3": "3",
        "الجنح": "3",
        "بوابه الجنح": "3",
        "البوابه الجنح": "3",

        "4": "4",
        "النساء": "4",
        "بوابه النساء": "4",
        "البوابه النساء": "4",

        "5": "5",
        "التدابير": "5",
        "التدابير الاحترازيه": "5",
        "بوابه التدابير الاحترازيه": "5",
        "البوابه التدابير الاحترازيه": "5",

        "6": "6",
        "المركزيه": "6",
        "البوابه المركزيه": "6",
        "بوابه المركزيه": "6"
    }

    gate_number = gate_aliases.get(normalized)

    if not gate_number:
        return None

    return GATES.get(gate_number)


# =========================================================
# إرسال طلب التحقق من الشخص إلى Google Apps Script
# =========================================================

def check_person_gate_and_reply(
    room_id,
    person_name,
    gate_code,
    gate_name
):
    if not gate_code or not gate_name:
        send_message(
            room_id,
            "⚠️ لم يتم تحديد البوابة بصورة صحيحة."
        )
        return

    try:
        response = requests.get(
            GOOGLE_API,
            params={
                "person": person_name,
                "gate": gate_code
            },
            timeout=20
        )

        response.raise_for_status()

    except requests.RequestException as error:
        print("Person gate request error:", error)

        send_message(
            room_id,
            (
                "⚠️ تعذر الاتصال بقاعدة البيانات حاليًا.\n"
                "يرجى المحاولة مرة أخرى."
            )
        )
        return

    try:
        result = response.json()

    except ValueError:
        print(
            "Invalid person gate response:",
            response.text
        )

        send_message(
            room_id,
            "⚠️ وصلت استجابة غير صحيحة من قاعدة البيانات."
        )
        return

    status = clean_text(result.get("status")).lower()

    if status == "not_found":
        send_message(
            room_id,
            f'❌ لم يتم العثور على شخص مطابق للاسم "{person_name}".'
        )
        return

    if status == "error":
        send_message(
            room_id,
            result.get(
                "message",
                "⚠️ تعذر التحقق من تصريح الشخص."
            )
        )
        return

    if status != "found":
        send_message(
            room_id,
            "⚠️ حدث خطأ غير متوقع أثناء التحقق."
        )
        return

    results = result.get("results") or []

    if not results:
        send_message(
            room_id,
            f'❌ لم يتم العثور على شخص مطابق للاسم "{person_name}".'
        )
        return

    # نتيجة واحدة.
    if len(results) == 1:
        send_message(
            room_id,
            build_person_gate_reply(
                person=results[0],
                gate_name=gate_name
            )
        )
        return

    # أكثر من نتيجة للاسم نفسه.
    send_message(
        room_id,
        build_multiple_person_gate_reply(
            person_name=person_name,
            results=results,
            gate_name=gate_name
        )
    )


# =========================================================
# إنشاء رد التحقق من البوابة
# =========================================================

def build_person_gate_reply(person, gate_name):
    lines = []

    driver = clean_text(
        person.get("driver")
        or person.get("person")
        or person.get("name")
    )

    organization = clean_text(
        person.get("organization")
    )

    plate = clean_text(
        person.get("plate")
    )

    allowed = to_boolean(
        person.get("allowed")
    )

    general_access = to_boolean(
        person.get("general_access")
        or person.get("general_gate_access")
    )

    allowed_gates = person.get("allowed_gates") or []

    commands = clean_text(
        person.get("commands")
    )

    if driver:
        lines.append(
            f"👤 الاسم: {driver}"
        )

    if organization:
        lines.append(
            f"🏢 المؤسسة: {organization}"
        )

    if plate:
        lines.append(
            f"🚘 رقم المركبة: {plate}"
        )

    if lines:
        lines.append("")

    if allowed:
        lines.append(
            f"✅ نعم، مسموح له بالدخول إلى {gate_name}."
        )

        if general_access:
            lines.append(
                "📍 لديه تصريح عام لجميع البوابات."
            )

        elif allowed_gates:
            lines.append(
                "🚪 البوابات المسموح بها: "
                + join_arabic_items(allowed_gates)
            )

    else:
        lines.append(
            f"❌ لا، غير مسموح له بالدخول إلى {gate_name}."
        )

        if general_access:
            lines.append(
                "📍 لديه تصريح عام لجميع البوابات."
            )

        elif allowed_gates:
            lines.append(
                "🚪 البوابات المسموح بها: "
                + join_arabic_items(allowed_gates)
            )

    if commands:
        lines.append("")
        lines.append(
            f"⚠️ الأوامر: {commands}"
        )

    return "\n".join(lines).strip()


def build_multiple_person_gate_reply(
    person_name,
    results,
    gate_name
):
    lines = [
        f'🔎 تم العثور على أكثر من نتيجة للاسم "{person_name}".',
        ""
    ]

    maximum_results = 15

    for person in results[:maximum_results]:
        driver = clean_text(
            person.get("driver")
            or person.get("person")
            or person.get("name")
        ) or "بدون اسم"

        organization = clean_text(
            person.get("organization")
        )

        plate = clean_text(
            person.get("plate")
        )

        allowed = to_boolean(
            person.get("allowed")
        )

        status_icon = "✅" if allowed else "❌"
        status_text = "مسموح" if allowed else "غير مسموح"

        details = []

        if organization:
            details.append(
                f"المؤسسة: {organization}"
            )

        if plate:
            details.append(
                f"المركبة: {plate}"
            )

        line = f"{status_icon} {driver}"

        if details:
            line += " | " + " | ".join(details)

        line += f" | {status_text} إلى {gate_name}"

        lines.append(line)

    if len(results) > maximum_results:
        hidden_count = len(results) - maximum_results

        lines.append("")
        lines.append(
            f"ℹ️ توجد {hidden_count} نتائج إضافية لم تُعرض."
        )

    lines.append("")
    lines.append(
        "للحصول على نتيجة أدق، أعد المحاولة باستخدام الاسم الكامل."
    )

    return "\n".join(lines)


def to_boolean(value):
    if isinstance(value, bool):
        return value

    if isinstance(value, (int, float)):
        return value != 0

    normalized = normalize_arabic(value)

    return normalized in {
        "true",
        "1",
        "yes",
        "نعم",
        "مسموح",
        "allowed"
    }


# =========================================================
# إرسال البحث العادي إلى Google Apps Script
# =========================================================

def search_and_reply(
    room_id,
    query,
    search_type="",
    sender_id=""
):
    params = {
        "query": query
    }

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
            (
                "⚠️ تعذر الاتصال بقاعدة البيانات حاليًا.\n"
                "يرجى المحاولة مرة أخرى."
            )
        )
        return

    try:
        result = response.json()

    except ValueError:
        print(
            "Invalid Google API response:",
            response.text
        )

        send_message(
            room_id,
            "⚠️ وصلت استجابة غير صحيحة من قاعدة البيانات."
        )
        return

    status = clean_text(
        result.get("status")
    ).lower()

    # الاسم موجود كسائق ومؤسسة.
    if status == "choose":
        if sender_id:
            pending_searches[sender_id] = query

        reply = (
            f'🔎 تم العثور على أكثر من نوع من النتائج لعبارة "{query}".\n\n'
            "يرجى تحديد نوع البحث:\n\n"
            "1️⃣ السائق\n"
            "2️⃣ المؤسسة\n\n"
            "أرسل رقم الخيار أو اكتب: السائق أو المؤسسة."
        )

        send_message(
            room_id,
            reply
        )
        return

    if status == "not_found":
        send_message(
            room_id,
            (
                "❌ لم يتم العثور على مركبة أو سائق "
                "أو مؤسسة مطابقة."
            )
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

    results = result.get("results") or []

    result_search_type = clean_text(
        result.get("search_type")
    )

    if not results:
        send_message(
            room_id,
            "❌ لم يتم العثور على نتائج مطابقة."
        )
        return

    # نتيجة واحدة فقط.
    if len(results) == 1:
        send_message(
            room_id,
            build_vehicle_reply(results[0])
        )
        return

    # أكثر من نتيجة.
    send_message(
        room_id,
        build_multiple_results_reply(
            query=query,
            results=results,
            search_type=result_search_type
        )
    )


# =========================================================
# إنشاء رد بيانات المركبة
# =========================================================

def build_vehicle_reply(vehicle):
    lines = []

    plate = clean_text(
        vehicle.get("plate")
        or vehicle.get("vehicle_number")
    )

    plate_source = clean_text(
        vehicle.get("plate_source")
    )

    category = clean_text(
        vehicle.get("category")
    )

    header_parts = []

    if plate:
        header_parts.append(plate)

    if plate_source:
        header_parts.append(plate_source)

    if category:
        header_parts.append(category)

    if header_parts:
        lines.append(
            "🚘 رقم المركبة: "
            + " | ".join(header_parts)
        )

    add_field(
        lines,
        "🚙 نوع المركبة",
        vehicle.get("vehicle_type")
        or vehicle.get("type")
    )

    add_field(
        lines,
        "🎨 لون المركبة",
        vehicle.get("color")
    )

    add_field(
        lines,
        "👤 السائق",
        vehicle.get("driver")
    )

    add_field(
        lines,
        "🏢 المؤسسة",
        vehicle.get("organization")
    )

    add_field(
        lines,
        "👥 مرافق السائق",
        vehicle.get("companion1")
        or vehicle.get("companion_1")
    )

    add_field(
        lines,
        "👥 المرافق الثاني",
        vehicle.get("companion2")
        or vehicle.get("companion_2")
    )

    add_field(
        lines,
        "👥 المرافق الثالث",
        vehicle.get("companion3")
        or vehicle.get("companion_3")
    )

    add_field(
        lines,
        "🅿️ مكان الوقوف",
        vehicle.get("parking")
        or vehicle.get("parking_location")
    )

    from_date = clean_text(
        vehicle.get("from_date")
    )

    to_date = clean_text(
        vehicle.get("to_date")
    )

    date_lines = build_permit_date_lines(
        from_date,
        to_date
    )

    if date_lines:
        lines.append("")
        lines.extend(date_lines)

    permit_status = get_permit_status(
        from_date,
        to_date
    )

    if permit_status:
        lines.append("")
        lines.append(permit_status)

    entry_time = clean_text(
        vehicle.get("entry_time_from")
        or vehicle.get("entry_time")
    )

    exit_time = clean_text(
        vehicle.get("exit_time_to")
        or vehicle.get("exit_time")
    )

    time_status_lines = get_entry_time_status(
        entry_time,
        exit_time
    )

    if time_status_lines:
        lines.append("")
        lines.extend(time_status_lines)

    action = clean_text(
        vehicle.get("action")
        or vehicle.get("required_action")
    )

    if action:
        lines.append("")
        lines.append(
            f"📌 الإجراءات المطلوبة: {action}"
        )

    gate_summary = build_vehicle_gate_summary(
        vehicle
    )

    if gate_summary:
        lines.append("")
        lines.append(gate_summary)

    commands = clean_text(
        vehicle.get("commands")
    )

    if commands:
        lines.append("")
        lines.append(
            f"⚠️ الأوامر: {commands}"
        )

    if not lines:
        return "⚠️ لا توجد بيانات متاحة لهذه النتيجة."

    return "\n".join(lines).strip()


def add_field(lines, label, value):
    cleaned_value = clean_text(value)

    if cleaned_value:
        lines.append(
            f"{label}: {cleaned_value}"
        )


# =========================================================
# عرض صلاحيات البوابات داخل بيانات المركبة
# =========================================================

def build_vehicle_gate_summary(vehicle):
    general_access = to_boolean(
        vehicle.get("general_access")
        or vehicle.get("general_gate_access")
    )

    allowed_gates = (
        vehicle.get("allowed_gates")
        or []
    )

    if general_access:
        return (
            "🚪 مسموح له بالدخول إلى جميع البوابات."
        )

    if allowed_gates:
        return (
            "🚪 البوابات المسموح بها: "
            + join_arabic_items(allowed_gates)
        )

    # يدعم أيضًا البيانات إذا أعاد Apps Script
    # صلاحيات البوابات كل واحدة في حقل منفصل.
    gate_fields = [
        (
            "البوابة الرئيسية",
            vehicle.get("main_gate")
            or vehicle.get("gate_main")
        ),
        (
            "بوابة الموظفين",
            vehicle.get("employees_gate")
            or vehicle.get("gate_employees")
        ),
        (
            "بوابة الجنح",
            vehicle.get("misdemeanors_gate")
            or vehicle.get("gate_misdemeanors")
        ),
        (
            "بوابة النساء",
            vehicle.get("women_gate")
            or vehicle.get("gate_women")
        ),
        (
            "بوابة التدابير الاحترازية",
            vehicle.get("precautionary_gate")
            or vehicle.get("gate_precautionary")
        ),
        (
            "البوابة المركزية",
            vehicle.get("central_gate")
            or vehicle.get("gate_central")
        )
    ]

    available_gates = []

    for gate_name, gate_value in gate_fields:
        if is_non_empty_permission(gate_value):
            available_gates.append(gate_name)

    if available_gates:
        return (
            "🚪 البوابات المسموح بها: "
            + join_arabic_items(available_gates)
        )

    return ""


def is_non_empty_permission(value):
    """
    حسب قاعدة الجدول:
    أي قيمة غير فارغة في عمود البوابة تعتبر سماحًا.
    """

    return bool(clean_text(value))


# =========================================================
# إنشاء سطور تاريخ التصريح
# =========================================================

def build_permit_date_lines(
    from_date,
    to_date
):
    if not from_date and not to_date:
        return []

    if (
        from_date
        and to_date
        and from_date == to_date
    ):
        return [
            f"📅 التاريخ: {from_date}",
            "ℹ️ التصريح صالح ليوم واحد فقط."
        ]

    lines = []

    if from_date:
        lines.append(
            f"📅 من تاريخ: {from_date}"
        )

    if to_date:
        lines.append(
            f"📅 إلى تاريخ: {to_date}"
        )

    return lines


# =========================================================
# التحقق من حالة تاريخ التصريح
# =========================================================

def get_permit_status(
    from_date,
    to_date
):
    start_date = parse_sheet_date(
        from_date
    )

    end_date = parse_sheet_date(
        to_date
    )

    today = datetime.now(
        UAE_TIMEZONE
    ).date()

    if not start_date and not end_date:
        return ""

    if start_date and today < start_date:
        remaining_days = (
            start_date - today
        ).days

        if remaining_days == 1:
            return (
                "⏳ التصريح لم يبدأ بعد، "
                "ويبدأ غدًا."
            )

        return (
            "⏳ التصريح لم يبدأ بعد، "
            f"ويتبقى {remaining_days} أيام."
        )

    if end_date:
        if today > end_date:
            expired_days = (
                today - end_date
            ).days

            if expired_days == 1:
                return (
                    "❌ انتهت صلاحية التصريح منذ يوم واحد."
                )

            return (
                "❌ انتهت صلاحية التصريح "
                f"منذ {expired_days} أيام."
            )

        if today == end_date:
            return (
                "⚠️ التصريح ساري، لكنه ينتهي اليوم."
            )

        remaining_days = (
            end_date - today
        ).days

        if remaining_days == 1:
            return (
                "✅ التصريح ساري، وينتهي غدًا."
            )

        return (
            "✅ التصريح ساري، "
            f"ويتبقى {remaining_days} أيام."
        )

    return "✅ التصريح ساري."


def parse_sheet_date(value):
    text = clean_text(value)

    if not text:
        return None

    # إزالة الوقت إذا أعاد Google التاريخ مع وقت.
    if "T" in text:
        text = text.split("T")[0]

    if " " in text:
        first_part = text.split(" ")[0]

        if re.search(
            r"\d",
            first_part
        ):
            text = first_part

    date_formats = (
        "%d/%m/%Y",
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%m/%d/%Y",
        "%Y/%m/%d",
        "%d.%m.%Y"
    )

    for date_format in date_formats:
        try:
            return datetime.strptime(
                text,
                date_format
            ).date()

        except ValueError:
            continue

    return None


# =========================================================
# التحقق من وقت الدخول والخروج
# =========================================================

def get_entry_time_status(
    entry_time,
    exit_time
):
    if not entry_time and not exit_time:
        return [
            "✅ الدخول مسموح طوال اليوم."
        ]

    lines = [
        "🕒 فترة السماح بالدخول:"
    ]

    if entry_time:
        lines.append(
            f"من {entry_time}"
        )

    if exit_time:
        lines.append(
            f"إلى {exit_time}"
        )

    start_time = parse_time(
        entry_time
    )

    end_time = parse_time(
        exit_time
    )

    now = datetime.now(
        UAE_TIMEZONE
    ).time().replace(
        second=0,
        microsecond=0
    )

    if start_time and end_time:
        # فترة عادية داخل اليوم نفسه.
        if start_time <= end_time:
            if now < start_time:
                lines.append(
                    "⏳ لم يبدأ وقت الدخول المسموح بعد."
                )

            elif now > end_time:
                lines.append(
                    "❌ انتهى وقت الدخول المسموح."
                )

            else:
                lines.append(
                    "✅ الدخول مسموح الآن."
                )

        # فترة تمتد بعد منتصف الليل.
        else:
            if (
                now >= start_time
                or now <= end_time
            ):
                lines.append(
                    "✅ الدخول مسموح الآن."
                )

            else:
                lines.append(
                    "❌ الدخول غير مسموح في الوقت الحالي."
                )

    elif start_time:
        if now >= start_time:
            lines.append(
                "✅ الدخول مسموح الآن."
            )

        else:
            lines.append(
                "⏳ لم يبدأ وقت الدخول المسموح بعد."
            )

    elif end_time:
        if now <= end_time:
            lines.append(
                "✅ الدخول مسموح الآن."
            )

        else:
            lines.append(
                "❌ انتهى وقت الدخول المسموح."
            )

    return lines


def parse_time(value):
    text = clean_text(value)

    if not text:
        return None

    # توحيد بعض صيغ الوقت العربية.
    normalized = text.upper()

    normalized = normalized.replace(
        "ص",
        "AM"
    )

    normalized = normalized.replace(
        "م",
        "PM"
    )

    normalized = re.sub(
        r"\s+",
        " ",
        normalized
    ).strip()

    time_formats = (
        "%H:%M",
        "%H:%M:%S",
        "%I:%M %p",
        "%I:%M%p",
        "%I %p",
        "%H"
    )

    for time_format in time_formats:
        try:
            return datetime.strptime(
                normalized,
                time_format
            ).time()

        except ValueError:
            continue

    return None


# =========================================================
# عرض عدة نتائج
# =========================================================

def build_multiple_results_reply(
    query,
    results,
    search_type
):
    normalized_search_type = normalize_arabic(
        search_type
    )

    if normalized_search_type == "organization":
        title = (
            f'🏢 نتائج المؤسسة المطابقة لعبارة "{query}"'
        )

    elif normalized_search_type == "driver":
        title = (
            f'👤 نتائج السائق المطابقة لعبارة "{query}"'
        )

    else:
        title = (
            f'🔎 النتائج المطابقة لعبارة "{query}"'
        )

    lines = [
        title,
        "",
        f"تم العثور على {len(results)} مركبة:"
    ]

    maximum_results = 20

    for vehicle in results[:maximum_results]:
        plate = clean_text(
            vehicle.get("plate")
            or vehicle.get("vehicle_number")
        ) or "بدون رقم"

        driver = clean_text(
            vehicle.get("driver")
        )

        organization = clean_text(
            vehicle.get("organization")
        )

        plate_source = clean_text(
            vehicle.get("plate_source")
        )

        category = clean_text(
            vehicle.get("category")
        )

        plate_parts = [
            plate
        ]

        if plate_source:
            plate_parts.append(
                plate_source
            )

        if category:
            plate_parts.append(
                category
            )

        details = []

        if driver:
            details.append(
                f"السائق: {driver}"
            )

        if organization:
            details.append(
                f"المؤسسة: {organization}"
            )

        line = (
            "🚘 "
            + " | ".join(plate_parts)
        )

        if details:
            line += (
                "\n"
                + " | ".join(details)
            )

        lines.append(line)
        lines.append("")

    if len(results) > maximum_results:
        hidden_count = (
            len(results) - maximum_results
        )

        lines.append(
            f"ℹ️ توجد {hidden_count} نتائج إضافية لم تُعرض."
        )

        lines.append("")

    lines.append(
        "أرسل رقم المركبة المطلوبة لعرض تفاصيلها."
    )

    return "\n".join(lines).strip()


# =========================================================
# الردود الحوارية
# =========================================================

def get_conversational_reply(text):
    greeting_phrases = {
        "السلام عليكم": (
            "وعليكم السلام ورحمة الله وبركاته 🌷\n"
            "كيف يمكنني مساعدتك؟"
        ),

        "مرحبا": (
            "مرحبًا بك 👋\n"
            "أرسل رقم المركبة أو اسم السائق أو اسم المؤسسة."
        ),

        "مرحبا بك": (
            "مرحبًا بك 👋\n"
            "كيف يمكنني مساعدتك؟"
        ),

        "صباح الخير": (
            "صباح النور والسرور ☀️\n"
            "كيف يمكنني مساعدتك؟"
        ),

        "مساء الخير": (
            "مساء النور والسرور 🌙\n"
            "كيف يمكنني مساعدتك؟"
        ),

        "شكرا": (
            "العفو، حاضر دائمًا 🌷"
        ),

        "مشكور": (
            "العفو، حاضر دائمًا 🌷"
        ),

        "يعطيك العافيه": (
            "الله يعافيك ويسلمك 🌷"
        ),

        "الله يعطيك العافيه": (
            "الله يعافيك ويسلمك 🌷"
        )
    }

    if text in greeting_phrases:
        return greeting_phrases[text]

    usage_triggers = {
        "طريقه الاستخدام",
        "كيف استخدم",
        "كيف استخدمك",
        "الاستخدام",
        "شرح الاستخدام"
    }

    if text in usage_triggers:
        return (
            "📖 طريقة الاستخدام\n\n"
            "🚪 للتحقق من دخول شخص:\n"
            "اكتب: هلا\n"
            "ثم اختر البوابة واكتب اسم الشخص.\n\n"
            "🚘 للبحث عن بيانات تصريح مركبة:\n"
            "أرسل رقم المركبة.\n\n"
            "👤 للبحث باسم السائق:\n"
            "أرسل اسم السائق.\n\n"
            "🏢 للبحث باسم المؤسسة:\n"
            "أرسل اسم المؤسسة."
        )

    help_triggers = {
        "مساعده",
        "المساعده",
        "ساعدني",
        "help",
        "?"
    }

    if text in help_triggers:
        return (
            "🤖 المساعدة\n\n"
            "يمكنني مساعدتك في:\n\n"
            "🚪 التحقق من دخول شخص إلى بوابة محددة.\n"
            "🚘 البحث برقم المركبة.\n"
            "👤 البحث باسم السائق.\n"
            "🏢 البحث باسم المؤسسة.\n"
            "📋 عرض بيانات المركبة.\n"
            "✅ التحقق من حالة التصريح.\n"
            "🕒 التحقق من وقت السماح بالدخول.\n\n"
            "لبدء خدمة البوابات اكتب: هلا"
        )

    about_triggers = {
        "عني",
        "عنك",
        "من انت",
        "عرفني بنفسك",
        "شو اسمك",
        "ما اسمك",
        "ما وظيفتك"
    }

    if text in about_triggers:
        return (
            "🤖 عني\n\n"
            "أنا «خالد»، مساعد إلكتروني تابع لإدارة الحراسات.\n\n"
            "أعمل على تسهيل الاستعلام عن تصاريح المركبات، "
            "والتحقق من بيانات المركبة والسائق والمؤسسة التابعة لها، "
            "بالإضافة إلى التحقق من دخول الأشخاص إلى البوابات المختلفة "
            "بسرعة ودقة.\n\n"
            "🎯 مهمتي: سرعة الوصول إلى المعلومة، "
            "ودقة التحقق، ودعم فرق العمل بكفاءة واحترافية."
        )

    return None


# =========================================================
# فهم اختيار السائق أو المؤسسة
# =========================================================

def detect_search_type_choice(text):
    normalized = normalize_arabic(
        text
    )

    driver_choices = {
        "1",
        "السائق",
        "سائق",
        "اسم السائق",
        "الخيار 1",
        "الخيار الاول"
    }

    organization_choices = {
        "2",
        "المؤسسه",
        "مؤسسه",
        "اسم المؤسسه",
        "الشركه",
        "شركه",
        "الخيار 2",
        "الخيار الثاني"
    }

    if normalized in driver_choices:
        return "driver"

    if normalized in organization_choices:
        return "organization"

    return ""


# =========================================================
# تنظيف عبارة البحث
# =========================================================

def extract_search_query(text):
    cleaned = clean_text(text)

    if not cleaned:
        return ""

    normalized = normalize_arabic(cleaned)

    patterns = [
        r"^ابحث\s+عن\s+",
        r"^ابحث\s+",
        r"^بحث\s+عن\s+",
        r"^بحث\s+",
        r"^رقم\s+المركبه\s+",
        r"^رقم\s+السياره\s+",
        r"^المركبه\s+",
        r"^السياره\s+",
        r"^لوحه\s+",
        r"^رقم\s+اللوحه\s+",
        r"^السائق\s+",
        r"^اسم\s+السائق\s+",
        r"^المؤسسه\s+",
        r"^اسم\s+المؤسسه\s+",
        r"^الشركه\s+",
        r"^اسم\s+الشركه\s+"
    ]

    for pattern in patterns:
        normalized = re.sub(
            pattern,
            "",
            normalized
        ).strip()

    return normalized


# =========================================================
# تنظيف وتوحيد النص العربي
# =========================================================

def normalize_arabic(value):
    text = clean_text(value).lower()

    replacements = {
        "أ": "ا",
        "إ": "ا",
        "آ": "ا",
        "ٱ": "ا",
        "ى": "ي",
        "ة": "ه",
        "ؤ": "و",
        "ئ": "ي"
    }

    for original, replacement in replacements.items():
        text = text.replace(
            original,
            replacement
        )

    # إزالة التشكيل.
    text = re.sub(
        r"[\u064B-\u065F\u0670]",
        "",
        text
    )

    # إزالة التطويل.
    text = text.replace(
        "ـ",
        ""
    )

    # توحيد علامات الفصل الشائعة.
    text = re.sub(
        r"[،,؛;]+",
        " ",
        text
    )

    # إزالة المسافات الزائدة.
    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


# =========================================================
# تحويل الأرقام العربية إلى إنجليزية
# =========================================================

def convert_to_english_digits(value):
    if value is None:
        return ""

    translation_table = str.maketrans(
        "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹",
        "01234567890123456789"
    )

    return str(value).translate(
        translation_table
    )


# =========================================================
# تنظيف النصوص العامة
# =========================================================

def clean_text(value):
    if value is None:
        return ""

    text = convert_to_english_digits(
        value
    )

    text = str(text).replace(
        "\u200f",
        ""
    )

    text = text.replace(
        "\u200e",
        ""
    )

    text = text.replace(
        "\u00a0",
        " "
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


# =========================================================
# دمج العناصر بصياغة عربية
# =========================================================

def join_arabic_items(items):
    if not isinstance(
        items,
        (list, tuple, set)
    ):
        items = [
            items
        ]

    cleaned_items = []

    for item in items:
        cleaned_item = clean_text(item)

        if (
            cleaned_item
            and cleaned_item not in cleaned_items
        ):
            cleaned_items.append(
                cleaned_item
            )

    if not cleaned_items:
        return ""

    if len(cleaned_items) == 1:
        return cleaned_items[0]

    if len(cleaned_items) == 2:
        return (
            f"{cleaned_items[0]} "
            f"و{cleaned_items[1]}"
        )

    return (
        "، ".join(cleaned_items[:-1])
        + " و"
        + cleaned_items[-1]
    )


# =========================================================
# إرسال الرسائل إلى Webex
# =========================================================

def send_message(room_id, text):
    cleaned_message = str(
        text or ""
    ).strip()

    if not room_id or not cleaned_message:
        return

    try:
        api.messages.create(
            roomId=room_id,
            text=cleaned_message
        )

    except Exception as error:
        print(
            "Unable to send Webex message:",
            error
        )


# =========================================================
# معالجة أخطاء Flask
# =========================================================

@app.errorhandler(404)
def page_not_found(error):
    return "Not Found", 404


@app.errorhandler(405)
def method_not_allowed(error):
    return "Method Not Allowed", 405


@app.errorhandler(500)
def internal_server_error(error):
    print(
        "Internal server error:",
        error
    )

    return "Internal Server Error", 500

    # =========================================================
# فحص جاهزية الخدمة
# =========================================================

@app.route("/health", methods=["GET"])
def health():
    return {
        "status": "ok",
        "service": "webex-vehicle-bot"
    }, 200


# =========================================================
# تشغيل التطبيق
# =========================================================

if __name__ == "__main__":
    port = int(
        os.environ.get(
            "PORT",
            5001
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )