import hashlib
import json
import os
import sys
import threading
from datetime import datetime, timedelta

import requests
from flask import Flask, request
from openai import OpenAI

app = Flask(__name__)

# --- ДОПОМІЖНІ ФУНКЦІЇ ---
def env(name, default=""):
    value = os.environ.get(name, default)
    return value.strip() if isinstance(value, str) else value

def log(message):
    print(message, flush=True)
    sys.stdout.flush()

# --- КОНФІГУРАЦІЯ З RAILWAY ---
FB_PAGE_ACCESS_TOKEN = env("FB_PAGE_ACCESS_TOKEN")
VERIFY_TOKEN = env("VERIFY_TOKEN", "rozmary2026")
OPENAI_API_KEY = env("OPENAI_API_KEY")
OPENAI_MODEL = env("OPENAI_MODEL", "gpt-4o")
BINOTEL_KEY = env("BINOTEL_API_KEY")
BINOTEL_SECRET = env("BINOTEL_API_SECRET")
TG_TOKEN = env("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = env("TELEGRAM_CHAT_ID")
BINOTEL_BRANCH_ID = int(env("BINOTEL_BRANCH_ID", "9970"))
SESSION_TTL_MINUTES = int(env("SESSION_TTL_MINUTES", "60"))

client = OpenAI(api_key=OPENAI_API_KEY)

# Сховище сесій та блокувань (locks) для стабільності
user_sessions = {}
user_locks = {}
locks_guard = threading.Lock()

def get_user_lock(user_id):
    with locks_guard:
        lock = user_locks.get(user_id)
        if lock is None:
            lock = threading.Lock()
            user_locks[user_id] = lock
        return lock

def trim_messages(messages, keep_last=15):
    """Тримає історію повідомлень в межах ліміту, не видаляючи системний промпт"""
    if len(messages) <= keep_last + 1:
        return
    system_message = messages[0]
    tail = messages[-keep_last:]
    messages[:] = [system_message] + tail

def send_tg_notification(text):
    if not (TG_TOKEN and TG_CHAT_ID):
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT_ID, "text": f"🔔 BeautyBridge Info:\n{text}"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as exc:
        log(f"Telegram error: {exc}")

# --- КЛАС РОБОТИ З BINOTEL API ---
class BinotelAPI:
    def __init__(self, key, secret, branch_id):
        self.key = key
        self.secret = secret
        self.branch_id = branch_id
        self.base_url = "https://api.binotel.com/api/2.0"

    def _build_request_json(self, date_str):
        request_data = {
            "branchId": self.branch_id,
            "startDate": date_str,
        }
        # КРИТИЧНО: sort_keys=True та separators без пробілів для MD5
        return json.dumps(request_data, separators=(",", ":"), sort_keys=True)

    def _build_signature(self, request_json):
        # ФОРМУЛА: KEY + JSON + SECRET
        raw_signature = f"{self.key}{request_json}{self.secret}"
        return hashlib.md5(raw_signature.encode("utf-8")).hexdigest()

    def get_free_slots(self, date_str):
        request_json = self._build_request_json(date_str)
        signature = self._build_signature(request_json)
        
        payload = {
            "key": self.key,
            "signature": signature,
            "requestData": json.loads(request_json)
        }

        try:
            log(f"--- ЗАПИТ ДО BINOTEL (Дата: {date_str}) ---")
            res = requests.post(f"{self.base_url}/bookon/get-free-times-for-day.json", json=payload, timeout=15)
            
            if res.status_code == 200:
                data = res.json()
                if data.get('status') == 'error':
                    log(f"API Business Error: {data.get('message')}")
                    return "ERROR"

                masters = {m['id']: m.get('name', 'Спеціаліст') for m in data.get('specialists', [])}
                
                if "freeTimes" in data and len(data["freeTimes"]) > 0:
                    slots = []
                    for s in data["freeTimes"]:
                        time = s['startTime'].split(' ')[1]
                        name = masters.get(s.get('specialistId'), "Фахівець")
                        slots.append(f"- {time} ({name})")
                    return "\n".join(sorted(list(set(slots))))
                
                return "EMPTY"
            
            log(f"API HTTP Error {res.status_code}: {res.text}")
            return "ERROR"
        except Exception as e:
            log(f"Connection error: {e}")
            return "ERROR"

crm = BinotelAPI(BINOTEL_KEY, BINOTEL_SECRET, BINOTEL_BRANCH_ID)

# --- ЛОГІКА ОБРОБКИ ПОВІДОМЛЕНЬ ---
def send_instagram_msg(recipient_id, text):
    url = f"https://graph.facebook.com/v18.0/me/messages?access_token={FB_PAGE_ACCESS_TOKEN}"
    requests.post(url, json={"recipient": {"id": recipient_id}, "message": {"text": text}})

def process_message(sender_id, user_text):
    lock = get_user_lock(sender_id)
    with lock:
        today_dt = datetime.now()
        target_date = today_dt.strftime("%Y-%m-%d")
        if "завтр" in user_text.lower():
            target_date = (today_dt + timedelta(days=1)).strftime("%Y-%m-%d")

        # Якщо сесія нова або застаріла
        if sender_id not in user_sessions:
            send_tg_notification(f"Клієнт пише в Instagram: {user_text}")
            log(f"Тягну CRM на {target_date}...")
            crm_data = crm.get_free_slots(target_date)

            # Формуємо інструкцію для AI
            if crm_data == "EMPTY":
                instr = "Вільних місць на цей день немає. Кажи ввічливо і пропонуй іншу дату."
            elif crm_data == "ERROR":
                instr = "Дані зараз оновлюються. Скажи, що адмін напише за хвилину. НЕ ВИГАДУЙ ЧАС."
            else:
                instr = f"Ось реальний розклад: {crm_data}. Пропонуй тільки ці вікна."

            system_prompt = (
                f"Ти — привітна адміністраторка салону Rozmary у Львові. Відповідай коротко. "
                f"{instr} В кінці скажи, що передала заявку адміну."
            )
            user_sessions[sender_id] = [{"role": "system", "content": system_prompt}]
        
        user_sessions[sender_id].append({"role": "user", "content": user_text})
        trim_messages(user_sessions[sender_id])

        try:
            # Температура 0.0 для максимальної точності
            response = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=user_sessions[sender_id],
                temperature=0.0
            )
            ai_reply = response.choices[0].message.content
            user_sessions[sender_id].append({"role": "assistant", "content": ai_reply})
            send_instagram_msg(sender_id, ai_reply)
            log("Відповідь успішно надіслана.")
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
                        sender_id = event["sender"]["id"]
                        text = event["message"]["text"]
                        threading.Thread(target=process_message, args=(sender_id, text)).start()
        return "EVENT_RECEIVED", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
