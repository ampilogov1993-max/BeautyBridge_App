import os
import requests
import threading
import sys
from flask import Flask, request
from openai import OpenAI
from datetime import datetime, timedelta

app = Flask(__name__)

def log(msg):
    print(msg, flush=True)
    sys.stdout.flush()

# --- ПЕРЕМІННІ З RAILWAY ---
FB_PAGE_ACCESS_TOKEN = os.environ.get("FB_PAGE_ACCESS_TOKEN")
VERIFY_TOKEN = "rozmary2026"
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
BINOTEL_KEY = os.environ.get("BINOTEL_API_KEY")
BINOTEL_SECRET = os.environ.get("BINOTEL_API_SECRET")
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

client = OpenAI(api_key=OPENAI_API_KEY)
user_sessions = {}

def send_tg_notification(text):
    """Сповіщення адміна в Телеграм"""
    if TG_TOKEN and TG_CHAT_ID:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        try:
            res = requests.post(url, json={"chat_id": TG_CHAT_ID, "text": f"🔔 BeautyBridge Info:\n{text}"})
            log(f"TG Status: {res.status_code}")
        except Exception as e:
            log(f"TG Error: {e}")

class BinotelAPI:
    """Робота з офіційним API Binotel Bookon (Версія 2.0)"""
    def __init__(self, key, secret):
        self.key = key
        self.secret = secret
        # Офіційна базова адреса Binotel API 2.0
        self.base_url = "https://api.binotel.com/api/2.0"

    def get_free_slots(self, date_str):
        log(f"--- ЗАПИТ ДО API BINOTEL 2.0 (Дата: {date_str}) ---")
        
        # Ендпоінт обов'язково має закінчуватися на .json
        url = f"{self.base_url}/bookon/get-free-times-for-day.json"
        
        payload = {
            "key": self.key,
            "password": self.secret, # Binotel приймає secret як password у простих запитах
            "requestData": {
                "branchId": 9970,
                "startDate": date_str
            }
        }
        
        try:
            response = requests.post(url, json=payload, timeout=12)
            log(f"Binotel API Status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                
                # Binotel повертає 'status': 'success' або 'error'
                if data.get('status') == 'error':
                    log(f"Binotel API Business Error: {data}")
                    return "Зараз уточню розклад у адміністратора!"

                # Збираємо майстрів
                masters = {m['id']: m.get('name', 'Майстер') for m in data.get('specialists', [])}
                
                if "freeTimes" in data and len(data["freeTimes"]) > 0:
                    slots = []
                    for s in data["freeTimes"]:
                        time = s['startTime'].split(' ')[1]
                        master_name = masters.get(s.get('specialistId'), "Спеціаліст")
                        slots.append(f"- {time} (Майстер: {master_name})")
                    
                    result = "\n".join(sorted(list(set(slots))))
                    return f"На {date_str} є такі вільні місця:\n{result}"
                
                return f"На {date_str} вільних місць не знайдено."
            
            log(f"Помилка API (404/500): {response.text}")
            return "Зараз уточню розклад у адміністратора!"
            
        except Exception as e:
            log(f"Критична помилка API: {e}")
            return "Трохи зачекайте, оновлюю графік."

crm = BinotelAPI(BINOTEL_KEY, BINOTEL_SECRET)

def send_instagram_msg(recipient_id, text):
    url = f"https://graph.facebook.com/v18.0/me/messages?access_token={FB_PAGE_ACCESS_TOKEN}"
    requests.post(url, json={"recipient": {"id": recipient_id}, "message": {"text": text}})

def process_message(sender_id, user_text):
    today_dt = datetime.now()
    target_date = today_dt.strftime("%Y-%m-%d")
    if "завтр" in user_text.lower():
        target_date = (today_dt + timedelta(days=1)).strftime("%Y-%m-%d")

    if sender_id not in user_sessions:
        # Відправляємо сповіщення в ТГ про нового клієнта
        send_tg_notification(f"Новий клієнт в Instagram!\nТекст: {user_text}")
        
        log(f"Запит розкладу на {target_date}...")
        crm_data = crm.get_free_slots(target_date)
        
        user_sessions[sender_id] = [
            {"role": "system", "content": f"Ти адміністратор Rozmary. Відповідай коротко. РОЗКЛАД:\n{crm_data}"}
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
        log("Відповідь успішно відправлена.")
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
