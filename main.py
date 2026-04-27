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

# ОФІЦІЙНІ API КЛЮЧІ
BINOTEL_API_KEY = env("BINOTEL_API_KEY")
BINOTEL_API_SECRET = env("BINOTEL_API_SECRET")

TG_TOKEN = env("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = env("TELEGRAM_CHAT_ID")
PORT = int(env("PORT", "8080"))
BINOTEL_BRANCH_ID = env("BINOTEL_BRANCH_ID", "9970")
SESSION_TTL_MINUTES = int(env("SESSION_TTL_MINUTES", "60"))

client = OpenAI(api_key=OPENAI_API_KEY)
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

def session_is_expired(session):
    t = session.get("updated_at")
    return not t or (datetime.now() - t > timedelta(minutes=SESSION_TTL_MINUTES))

def trim_messages(m, k=15):
    if len(m) > k+1:
        sys_msg = m[0]
        tail = m[-k:]
        m[:] = [sys_msg] + tail

def send_tg_notification(text):
    if TG_TOKEN and TG_CHAT_ID:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        try: 
            requests.post(url, json={"chat_id": TG_CHAT_ID, "text": f"🔔 BeautyBridge:\n{text}"}, timeout=10)
        except Exception as exc: pass

# --- ТВІЙ ІДЕАЛЬНИЙ API КЛАС З ДЕБАГОМ ---
class BinotelAPI:
    def __init__(self, key, secret, branch_id):
        self.key = key or ""
        self.secret = secret or ""
        self.branch_id = branch_id
        self.base_url = "https://api.binotel.com/api/2.0"

    def generate_signature(self, data):
        # JSON суворо без пробілів
        json_data = json.dumps(data, separators=(',', ':'))
        
        # ОБОВ'ЯЗКОВО ТАК: Формуємо рядок через f-string
        raw = f"{self.key}{json_data}{self.secret}"
        
        signature = hashlib.md5(raw.encode('utf-8')).hexdigest()
        return signature, raw, json_data

    def get_free_slots(self, date_str):
        url = f"{self.base_url}/bookon/get-free-times-for-day.json"

        # Сувора типізація
        request_data = {
            "branchId": int(self.branch_id),
            "startDate": str(date_str)
        }

        signature, raw_string, json_string = self.generate_signature(request_data)

        payload = {
            "key": self.key,
            "signature": signature,
            "requestData": request_data
        }

        try:
            log(f"--- API REQUEST TO BINOTEL ({date_str}) ---")
            # ВИВОДИМО КЛЮЧІ ДЛЯ ПЕРЕВІРКИ (в лапках, щоб бачити пустоту)
            log(f"API KEY: '{self.key}'")
            log(f"API SECRET: '{self.secret}'")
            log(f"Signature base string: {raw_string}")
            
            res = requests.post(
                url, 
                json=payload, 
                headers={"Content-Type": "application/json"},
                timeout=10
            )
            
            log(f"Binotel status: {res.status_code}")
            log(f"Response: {res.text}")

            if res.status_code == 200:
                data = res.json()
                if data.get('status') == 'error':
                    return "NO_DATA"
                
                masters = {m['id']: m.get('name', 'Майстер') for m in data.get('specialists', [])}
                if "freeTimes" in data and len(data["freeTimes"]) > 0:
                    slots = [f"- {s['startTime'].split(' ')[1]} ({masters.get(s.get('specialistId'), 'Спеціаліст')})" for s in data["freeTimes"]]
                    return "\n".join(sorted(list(set(slots))))
                return "ALL_BUSY"
            
            return "ERROR"

        except Exception as e:
            log(f"Binotel error: {e}")
            return "ERROR"

crm = BinotelAPI(
    key=BINOTEL_API_KEY,
    secret=BINOTEL_API_SECRET,
    branch_id=BINOTEL_BRANCH_ID
)

# --- ЛОГІКА ОБРОБКИ ПОВІДОМЛЕНЬ ---
def resolve_target_date(text):
    t = datetime.now()
    return (t + timedelta(days=1)).strftime("%Y-%m-%d") if "завтр" in text.lower() else t.strftime("%Y-%m-%d")

def build_system_prompt(target_date, crm_data):
    if crm_data == "ALL_BUSY":
        instr = f"На {target_date} вільних місць немає. Попроси клієнта обрати іншу дату."
    elif crm_data in ["ERROR", "NO_DATA"]:
        instr = "Дані зараз оновлюються. Скажи, що адмін напише особисто. НЕ ВИГАДУЙ ЧАС."
    else:
        instr = f"Ось реальний розклад на {target_date}:\n{crm_data}\nПропонуй тільки цей час."
        
    return f"Ти адміністратор салону Rozmary у Львові. Відповідай коротко і ввічливо. {instr}"

def process_message(sender_id, text):
    with get_user_lock(sender_id):
        target_date = resolve_target_date(text)
        session = user_sessions.get(sender_id)
        
        if not session or session.get("target_date") != target_date or session_is_expired(session):
            send_tg_notification(f"Клієнт в IG: {text}")
            log(f"Тягну розклад на {target_date}...")
            crm_data = crm.get_free_slots(target_date)
            
            session = {
                "target_date": target_date,
                "updated_at": datetime.now(),
                "messages": [{"role": "system", "content": build_system_prompt(target_date, crm_data)}],
            }
            user_sessions[sender_id] = session
        
        session["updated_at"] = datetime.now()
        session["messages"].append({"role": "user", "content": text})
        trim_messages(session["messages"])
        
        try:
            response = client.chat.completions.create(model=OPENAI_MODEL, messages=session["messages"], temperature=0.0)
            reply = response.choices[0].message.content or "Адміністратор скоро напише!"
            session["messages"].append({"role": "assistant", "content": reply})
            
            url = f"https://graph.facebook.com/v18.0/me/messages?access_token={FB_PAGE_ACCESS_TOKEN}"
            requests.post(url, json={"recipient": {"id": sender_id}, "message": {"text": reply}})
            log("Відповідь надіслана.")
        except Exception as e: 
            log(f"AI error: {e}")

@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":
        if request.args.get("hub.verify_token") == VERIFY_TOKEN: 
            return request.args.get("hub.challenge"), 200
        return "403", 403
        
    data = request.get_json(silent=True) or {}
    for entry in data.get("entry", []):
        for event in entry.get("messaging", []):
            if not event.get("message", {}).get("is_echo") and event.get("sender", {}).get("id"):
                threading.Thread(target=process_message, args=(event["sender"]["id"], event.get("message", {}).get("text", "")), daemon=True).start()
    return "OK", 200

def log_startup_warnings():
    missing = [name for name, val in [("FB_PAGE_ACCESS_TOKEN", FB_PAGE_ACCESS_TOKEN), ("OPENAI_API_KEY", OPENAI_API_KEY), ("BINOTEL_API_KEY", BINOTEL_API_KEY), ("BINOTEL_API_SECRET", BINOTEL_API_SECRET)] if not val]
    if missing:
        log(f"⚠️ Startup warning. Missing env vars: {', '.join(missing)}")

if __name__ == "__main__":
    log_startup_warnings()
    app.run(host="0.0.0.0", port=PORT)
