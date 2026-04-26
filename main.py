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
    """Надсилає сповіщення в твій Telegram"""
    if TG_TOKEN and TG_CHAT_ID:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        try:
            requests.post(url, json={"chat_id": TG_CHAT_ID, "text": f"🔔 BeautyBridge Notification:\n{text}"})
        except Exception as e:
            log(f"TG Error: {e}")

class BinotelAPI:
    """Робота з офіційним API Binotel 2.0 (Bookon)"""
    def __init__(self, key, secret):
        self.key = key
        self.secret = secret
        self.base_url = "https://api.binotel.com/api/2.0"

    def get_free_slots(self, date_str):
        log(f"--- ЗАПИТ ДО API BINOTEL 2.0 (Дата: {date_str}) ---")
        url = f"{self.base_url}/bookon/get-free-times-for-day.json"
        
        # КРИТИЧНО: branchId як рядок "9970" для правильного MD5
        request_data = {
            "branchId": "9970",
            "startDate": date_str
        }
        
        # 1. Формуємо JSON без пробілів і з відсортованими ключами
        json_data = json.dumps(request_data, separators=(',', ':'), sort_keys=True)
        log(f"Рядок для підпису: {json_data}")
        
        # 2. Створюємо підпис: MD5(json_data + secret)
        signature = hashlib.md5((json_data + self.secret).encode('utf-8')).hexdigest()
        
        payload = {
            "key": self.key,
            "signature": signature,
            "requestData": request_data
        }
        
        try:
            response = requests.post(url, json=payload, timeout=15)
            log(f"Binotel API Status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                if data.get('status') == 'error':
                    log(f"Binotel Business Error: {data.get('message')}")
                    return "Зараз адміністратор уточнює розклад, зачекайте хвилинку!"

                # Збираємо імена майстрів
                masters = {m['id']: m.get('name', 'Спеціаліст') for m in data.get('specialists', [])}
                
                if "freeTimes" in data and len(data["freeTimes"]) > 0:
                    slots_info = []
                    for s in data["freeTimes"]:
                        time = s['startTime'].split(' ')[1]
                        master_name = masters.get(s.get('specialistId'), "Майстер")
                        slots_info.append(f"- {time} (Майстер: {master_name})")
                    
                    # Прибираємо дублі та сортуємо
                    available_times = "\n".join(sorted(list(set(slots_info))))
                    return f"На {date_str} є такі вільні віконця:\n{available_times}"
                
                return f"На {date_str} вільних місць не знайдено."
            
            log(f"API Error Response: {response.text}")
            return "Зараз адміністратор перевірить графік і напише вам!"
            
        except Exception as e:
            log(f"Критична помилка API: {e}")
            return "Оновлюю базу даних, зачекайте хвилину."

# Ініціалізація CRM
crm = BinotelAPI(BINOTEL_KEY, BINOTEL_SECRET)

SYSTEM_PROMPT = """
Ти — привітна адміністраторка салону краси "Rozmary" у Львові. 
Твоя мета: консультувати клієнтів щодо вільного часу.
1. Використовуй ТІЛЬКИ ті дані про вільний час, які надані в системі.
2. Пропонуй спочатку ранок (10:00-12:00).
3. Якщо клієнт питає про "завтра", дивись дані на завтрашню дату.
4. Наприкінці обов'язково кажи: "Я передала вашу заявку адміністратору, вона зараз зв'яжеться з вами для підтвердження!"
"""

def send_instagram_msg(recipient_id, text):
    """Надсилає повідомлення в Instagram Direct"""
    url = f"https://graph.facebook.com/v18.0/me/messages?access_token={FB_PAGE_ACCESS_TOKEN}"
    requests.post(url, json={"recipient": {"id": recipient_id}, "message": {"text": text}})

def process_message(sender_id, user_text):
    """Обробка вхідного повідомлення"""
    today_dt = datetime.now()
    target_date = today_dt.strftime("%Y-%m-%d")
    
    if "завтр" in user_text.lower():
        target_date = (today_dt + timedelta(days=1)).strftime("%Y-%m-%d")

    # Якщо новий діалог — сповіщаємо в ТГ і тягнемо розклад
    if sender_id not in user_sessions:
        send_tg_notification(f"Новий клієнт в Instagram!\nПитання: {user_text}")
        
        log(f"Запит розкладу на {target_date}...")
        crm_data = crm.get_free_slots(target_date)
        
        user_sessions[sender_id] = [
            {"role": "system", "content": f"{SYSTEM_PROMPT}\n\nРОЗКЛАД З БАЗИ:\n{crm_data}"}
        ]
    
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
        log("Відповідь надіслана клієнту.")
        
    except Exception as e:
        log(f"OpenAI Error: {e}")

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
