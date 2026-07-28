from flask import Flask, request
from webexteamssdk import WebexTeamsAPI
import requests

BOT_TOKEN = "test"

api = WebexTeamsAPI(access_token=BOT_TOKEN)

GOOGLE_API = "https://script.google.com/macros/s/AKfycbyBwxlPrnk8qUI0NMfpwy7DflFr90UtYgvHsy9Jksxbc8-3QhcgozOlA8s6ZXavismJ/exec"

app = Flask(__name__)

@app.route("/")
def home():
    return "Webex Bot is Running"

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json

    if data["data"]["personEmail"] == api.people.me().emails[0]:
        return "OK"

    message = api.messages.get(data["data"]["id"])
    plate = message.text.strip()

    response = requests.get(GOOGLE_API, params={"plate": plate})

    if response.text == "Not Found":
        api.messages.create(
            roomId=message.roomId,
            text="❌ رقم السيارة غير موجود."
        )
        return "OK"

    vehicle = response.json()

    reply = f"""
🚗 رقم السيارة: {vehicle['plate']}
👤 السائق: {vehicle['driver']}
👥 المرافق الأول: {vehicle['companion1']}
👥 المرافق الثاني: {vehicle['companion2']}
👥 المرافق الثالث: {vehicle['companion3']}
📍 مصدر اللوحة: {vehicle['plate_source']}
🏷️ الفئة: {vehicle['category']}
🎨 اللون: {vehicle['color']}
🚙 نوع المركبة: {vehicle['vehicle_type']}
🅿️ موقع الوقوف: {vehicle['parking']}
📅 من: {vehicle['from_date']}
📅 إلى: {vehicle['to_date']}
📌 الإجراءات: {vehicle['action']}
"""

    api.messages.create(
        roomId=message.roomId,
        text=reply
    )

    return "OK"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)