import os
import requests
import threading
import sys
import json
import hashlib
from flask import Flask, request
from openai import OpenAI
from datetime import datetime, timedelta

app = Flask(__name__)

def log(msg):
    print(msg, flush=True)
    sys.stdout.flush()

# --- ПЕРЕМІННІ З RAILWAY (чистимо від пробілів) ---
FB_PAGE_ACCESS_TOKEN = os.environ.get("FB_PAGE_ACCESS_TOKEN", "").strip()
VERIFY_TOKEN = "rozmary2026"
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
BINOTEL_KEY = os.environ.get("BINOTEL_API_KEY", "").strip()
BINOTEL_SECRET = os.environ.get("BINOTEL_API_SECRET", "").strip()
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

client = OpenAI(api_key=OPENAI_API_KEY)
user_sessions = {}

def send_tg_notification(text):
    """Сповіщення в твій Telegram"""
    if TG_TOKEN and TG_CHAT_ID:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        try:
            requests.post(url, json={"chat_id": TG_CHAT_ID, "text": f"🔔 BeautyBridge Info:\n{text}"})
        except Exception as e:
            log(f"TG Error: {e}")

class BinotelAPI:
    """Офіційна робота з Binotel API 2.0 (Bookon)"""
    def __init__(self, key, secret):
        self.key = key
        self.secret = secret
        self.base_url = "https://api.binotel.com/api/2.0"

    def get_free_slots(self, date_str):
        log(f"--- ЗАПИТ З ПІДПИСОМ (S + J) ---")
        url = f"{self.base_url}/bookon/get-free-times-for-day.json"
        
        # Данні запиту: branchId як число (integer)
        request_data = {
            "branchId": 9970,
            "startDate": date_str
        }
        
        # 1. Формуємо JSON без пробілів, ключі відсортовані за алфавітом
        json_data = json.dumps(request_data, separators=(',', ':'), sort_keys=True)
        
        # 2. Створюємо підпис: MD5(SECRET + JSON)
        # Це найбільш імовірний варіант для Binotel 2.0
        signature = hashlib.md5((self.secret + json_data).encode('utf-8')).hexdigest()
        
        log(f"Рядок для підпису: {json_data}")
        log(f"MD5 (S+J): {signature}")

        payload = {
            "key": self.key,
            "signature": signature,
            "requestData": request_data
        }
        
        try:
            response = requests.post(url, json=payload, timeout=12)
            log(f"Binotel API Status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                if data.get('status') == 'error':
                    log(f"API Error Message: {data.get('message')}")
                    return "Зараз адміністратор уточнює розклад у майстрів..."

                # Отримуємо імена майстрів
                masters = {}
                if "specialists" in data:
                    masters = {m['id']: m.get('name', 'Майстер') for m in data['specialists']}
                
                if "freeTimes" in data and len(data["freeTimes"]) > 0:
                    slots = []
                    for s in data["freeTimes"]:
                        time = s['startTime'].split(' ')[1]
                        name = masters.get(s.get('specialistId'), "Спеціаліст")
                        slots.append(f"- {time} ({name})")
                    return f"На {date_str} є такі вікна:\n" + "\n".join(sorted(list(set(slots))))
                return f"На {date_str} вільних місць не знайдено."
            
            log(f"API Error Response: {response.text}")
            return "Зараз адміністратор перевірить розклад і напише вам!"
        except Exception as e:
            log(f"Критична помилка API: {e}")
            return "Зараз уточнюємо графік у майстрів."

# Ініціалізація
crm = BinotelAPI(BINOTEL_KEY, BINOTEL_SECRET)

def send_instagram_msg(recipient_id, text):
    """Надсилає повідомлення в Instagram Direct"""
    url = f"https://graph.facebook.com/v18.0/me/messages?access_token={FB_PAGE_ACCESS_TOKEN}"
    requests.post(url, json={"recipient": {"id": recipient_id}, "message": {"text": text}})

def process_message(sender_id, user_text):
    """Обробка повідомлення від клієнта"""
    today_dt = datetime.now()
    target_date = today_dt.strftime("%Y-%m-%d")
    
    if "завтр" in user_text.lower():
        target_date = (today_dt + timedelta(days=1)).strftime("%Y-%m-%d")

    if sender_id not in user_sessions:
        # Сповіщення в Telegram
        send_tg_notification(f"Новий клієнт в Instagram питає:\n\"{user_text}\"")
        
        log(f"Тягну дані з CRM на {target_date}...")
        crm_data = crm.get_free_slots(target_date)
        
        # Системний промпт для ШІ
        system_content = (
            f"Ти адміністраторка салону краси Rozmary у Львові. "
            f"Ось дані про вільний час: {crm_data}. "
            f"Відповідай коротко. Якщо є вільний час — пропонуй. "
            f"В кінці скажи, що передала заявку адміну."
        )
        user_sessions[sender_id] = [{"role": "system", "content": system_content}]
    
    user_sessions[sender_id].append({"role": "user", "content": user_text})
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o", 
            messages=user_sessions[sender_id], 
            temperature=0.3
        )
        ai_reply = response.choices[0].message.content
        user_sessions[sender_id].append({"role": "assistant", "content": ai_reply})
        send_instagram_msg(sender_id, ai_reply)
        log("Відповідь надіслана.")
    except Exception as e:
        log(f"AI Error: {e}")

@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":
        if request.args.get("hub.verify_token") == VERIFY_TOKEN:
            return request.args.get("hub.challenge"), 200
        return "Forbidden", 403
    
    if request.method == "POST":
        data = request.json
        if data.get("object") == "instagram":
            for entry in data.get("entry", []):
                for event in entry.get("messaging", []):
                    if "message" in event and "text" in event["message"]:
                        threading.Thread(target=process_message, args=(event["sender"]["id"], event["message"]["text"])).start()
        return "EVENT_RECEIVED", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
