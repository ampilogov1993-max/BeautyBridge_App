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

# --- ПЕРЕМІННІ З RAILWAY ---
FB_PAGE_ACCESS_TOKEN = os.environ.get("FB_PAGE_ACCESS_TOKEN", "").strip()
VERIFY_TOKEN = "rozmary2026"
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
BINOTEL_KEY = os.environ.get("BINOTEL_API_KEY", "").strip()
BINOTEL_SECRET = os.environ.get("BINOTEL_API_SECRET", "").strip() # ТУТ МАЄ БУТИ API SECRET
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

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
        log(f"--- ЗАПИТ З SIGNATURE (K + J + S) ---")
        url = f"{self.base_url}/bookon/get-free-times-for-day.json"
        
        request_data = {
            "branchId": 9970,
            "startDate": date_str
        }
        
        # 1. Генерируємо JSON суворо БЕЗ пробілів (separators)
        # Важливо: без sort_keys, щоб зберігся порядок як у словнику
        json_data = json.dumps(request_data, separators=(',', ':'))
        
        # 2. ТВІЙ АЛГОРИТМ: MD5(KEY + JSON + SECRET)
        raw_signature = self.key + json_data + self.secret
        signature = hashlib.md5(raw_signature.encode('utf-8')).hexdigest()
        
        log(f"Рядок для підпису: {json_data}")
        log(f"Signature: {signature}")

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
                    return "NO_DATA"

                masters = {m['id']: m.get('name', 'Майстер') for m in data.get('specialists', [])}
                
                if "freeTimes" in data and len(data["freeTimes"]) > 0:
                    slots = []
                    for s in data["freeTimes"]:
                        time = s['startTime'].split(' ')[1]
                        name = masters.get(s.get('specialistId'), "Майстер")
                        slots.append(f"- {time} ({name})")
                    return f"Вільні вікна на {date_str}:\n" + "\n".join(sorted(list(set(slots))))
                return "ALL_BUSY"
            
            log(f"API Error Response: {response.text}")
            return "NO_DATA"
        except Exception as e:
            log(f"Критична помилка API: {e}")
            return "NO_DATA"

crm = BinotelAPI(BINOTEL_KEY, BINOTEL_SECRET)

def send_instagram_msg(rid, text):
    url = f"https://graph.facebook.com/v18.0/me/messages?access_token={FB_PAGE_ACCESS_TOKEN}"
    requests.post(url, json={"recipient": {"id": rid}, "message": {"text": text}})

def process_message(sid, text):
    today = datetime.now()
    target = (today + timedelta(days=1)).strftime("%Y-%m-%d") if "завтр" in text.lower() else today.strftime("%Y-%m-%d")

    if sid not in user_sessions:
        send_tg_notification(f"Клієнт в Instagram: {text}")
        log(f"Тягну дані CRM...")
        crm_data = crm.get_free_slots(target)
        
        # Визначаємо інструкцію для AI залежно від відповіді CRM
        if crm_data == "ALL_BUSY":
            instr = "Вільних місць немає. Попроси клієнта обрати іншу дату або зачекати відповіді адміна."
        elif crm_data == "NO_DATA":
            instr = "Зараз дані оновлюються. Скажи, що адмін напише за хвилину особисто. НЕ ВИГАДУЙ ЧАС."
        else:
            instr = f"Ось реальний розклад: {crm_data}. Пропонуй тільки цей час."

        user_sessions[sid] = [{"role": "system", "content": f"Ти адмін Rozmary у Львові. Відповідай коротко. {instr}"}]
    
    user_sessions[sid].append({"role": "user", "content": text})
    try:
        # Температура 0.0 для максимальної точності, щоб не вигадував час
        res = client.chat.completions.create(model="gpt-4o", messages=user_sessions[sid], temperature=0.0)
        reply = res.choices[0].message.content
        user_sessions[sid].append({"role": "assistant", "content": reply})
        send_instagram_msg(sid, reply)
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
