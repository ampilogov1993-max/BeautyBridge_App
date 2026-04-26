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

# --- ПЕРЕМІННІ ---
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
    if TG_TOKEN and TG_CHAT_ID:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        try:
            requests.post(url, json={"chat_id": TG_CHAT_ID, "text": f"🔔 BeautyBridge:\n{text}"})
        except: pass

class BinotelAPI:
    def __init__(self, key, secret):
        self.key = key
        self.secret = secret
        self.base_url = "https://api.binotel.com/api/2.0"

    def get_free_slots(self, date_str):
        log(f"--- ЗАПИТ З ПІДПИСОМ (Дата: {date_str}) ---")
        url = f"{self.base_url}/bookon/get-free-times-for-day.json"
        
        request_data = {
            "branchId": 9970,
            "startDate": date_str
        }
        
        # 1. Перетворюємо дані в JSON-рядок без пробілів
        json_data = json.dumps(request_data, separators=(',', ':'))
        
        # 2. Створюємо підпис (Signature): MD5(json_data + secret)
        signature = hashlib.md5((json_data + self.secret).encode('utf-8')).hexdigest()
        
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
                    log(f"API Error: {data.get('message')}")
                    return "Зараз уточню розклад!"

                masters = {m['id']: m.get('name', 'Майстер') for m in data.get('specialists', [])}
                
                if "freeTimes" in data and len(data["freeTimes"]) > 0:
                    slots = []
                    for s in data["freeTimes"]:
                        time = s['startTime'].split(' ')[1]
                        name = masters.get(s.get('specialistId'), "Майстер")
                        slots.append(f"- {time} ({name})")
                    return f"Вільні місця на {date_str}:\n" + "\n".join(sorted(list(set(slots))))
                return f"На {date_str} вільних місць немає."
            
            log(f"API Status {response.status_code}: {response.text}")
            return "Зараз перевірю графік!"
        except Exception as e:
            log(f"Критична помилка: {e}")
            return "Оновлюю базу, зачекайте."

crm = BinotelAPI(BINOTEL_KEY, BINOTEL_SECRET)

def send_instagram_msg(rid, text):
    url = f"https://graph.facebook.com/v18.0/me/messages?access_token={FB_PAGE_ACCESS_TOKEN}"
    requests.post(url, json={"recipient": {"id": rid}, "message": {"text": text}})

def process_message(sid, text):
    today = datetime.now()
    target = (today + timedelta(days=1)).strftime("%Y-%m-%d") if "завтр" in text.lower() else today.strftime("%Y-%m-%d")

    if sid not in user_sessions:
        send_tg_notification(f"Новий клієнт!\n{text}")
        log(f"Тягну дані на {target}...")
        crm_data = crm.get_free_slots(target)
        user_sessions[sid] = [{"role": "system", "content": f"Ти адмін Rozmary. Дані розкладу:\n{crm_data}"}]
    
    user_sessions[sid].append({"role": "user", "content": text})
    try:
        res = client.chat.completions.create(model="gpt-4o", messages=user_sessions[sid], temperature=0.3)
        reply = res.choices[0].message.content
        user_sessions[sid].append({"role": "assistant", "content": reply})
        send_instagram_msg(sid, reply)
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
