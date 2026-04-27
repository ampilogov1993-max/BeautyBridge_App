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

# НОВІ ЗМІННІ ДЛЯ СЕСІЙНОЇ АВТОРИЗАЦІЇ
BINOTEL_EMAIL = env("BINOTEL_EMAIL")
BINOTEL_PASSWORD = env("BINOTEL_PASSWORD")

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
        except Exception as exc: 
            log(f"Telegram error: {exc}")

# --- НОВИЙ КЛАС BINOTEL API (ЧЕРЕЗ СЕСІЮ) ---
class BinotelAPI:
    def __init__(self, email, password, branch_id):
        self.email = email
        self.password = password
        self.branch_id = branch_id
        self.session = requests.Session()
        self.logged_in = False

    def login(self):
        try:
            log(f"Спроба авторизації в BoCRM ({self.email})...")
            res = self.session.post(
                "https://my.binotel.ua/login",
                data={"email": self.email, "password": self.password},
                timeout=10,
                allow_redirects=True
            )
            self.logged_in = res.status_code == 200
            log(f"BoCRM login status: {res.status_code}")
        except Exception as e:
            log(f"Login error: {e}")

    def get_free_slots(self, date_str):
        if not self.logged_in:
            self.login()
            
        try:
            url = f"https://my.binotel.ua/b/bocrm/calendar/day?branchId={self.branch_id}&startDate={date_str}"
            log(f"Тягну слоти: {url}")
            res = self.session.get(url, timeout=10)
            log(f"BoCRM slots status: {res.status_code}")
            
            if res.status_code == 200:
                data = res.json()
                # Поки що повертаємо сирі дані рядком, щоб побачити структуру в логах/відповіді AI
                return str(data)
                
            self.logged_in = False
            return "NO_DATA"
        except Exception as e:
            log(f"Slots error: {e}")
            return "NO_DATA"

crm = BinotelAPI(
    email=BINOTEL_EMAIL,
    password=BINOTEL_PASSWORD,
    branch_id=BINOTEL_BRANCH_ID
)

# --- ЛОГІКА ОБРОБКИ ПОВІДОМЛЕНЬ ---
def resolve_target_date(text):
    t = datetime.now()
    return (t + timedelta(days=1)).strftime("%Y-%m-%d") if "завтр" in text.lower() else t.strftime("%Y-%m-%d")

def build_system_prompt(target_date, crm_data):
    if crm_data == "NO_DATA":
        instr = "Дані оновлюються. Скажи, що адмін напише за хвилину. НЕ ВИГАДУЙ ЧАС."
    else:
        # AI отримає сирий JSON і спробує сам з нього витягнути вільний час
        instr = f"Ось сирі дані розкладу на {target_date}: {crm_data}. Знайди там вільні вікна та запропонуй їх."
        
    return f"Ти адмін салону Rozmary у Львові. Кажи коротко. {instr}"

def process_message(sender_id, text):
    with get_user_lock(sender_id):
        target_date = resolve_target_date(text)
        session = user_sessions.get(sender_id)
        
        if not session or session.get("target_date") != target_date or session_is_expired(session):
            send_tg_notification(f"Клієнт в IG: {text}")
            log(f"Запит до внутрішнього API на {target_date}...")
            crm_data = crm.get_free_slots(target_date)
            
            # Якщо сирі дані занадто великі для логів, обріжемо їх
            log_data = crm_data[:500] + "..." if len(crm_data) > 500 else crm_data
            log(f"Отримані дані: {log_data}")
            
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
            # Даємо AI температуру 0.1, щоб він міг трохи подумати над сирим JSON
            response = client.chat.completions.create(model=OPENAI_MODEL, messages=session["messages"], temperature=0.1)
            reply = response.choices[0].message.content or "Адмін скоро напише!"
            session["messages"].append({"role": "assistant", "content": reply})
            
            url = f"https://graph.facebook.com/v18.0/me/messages?access_token={FB_PAGE_ACCESS_TOKEN}"
            requests.post(url, json={"recipient": {"id": sender_id}, "message": {"text": reply}})
            log("Відповідь успішно надіслана.")
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
    missing = []
    for name, value in [
        ("FB_PAGE_ACCESS_TOKEN", FB_PAGE_ACCESS_TOKEN),
        ("OPENAI_API_KEY", OPENAI_API_KEY),
        ("BINOTEL_EMAIL", BINOTEL_EMAIL),
        ("BINOTEL_PASSWORD", BINOTEL_PASSWORD),
    ]:
        if not value:
            missing.append(name)
    if missing:
        log(f"⚠️ Startup warning. Missing env vars: {', '.join(missing)}")

if __name__ == "__main__":
    log_startup_warnings()
    app.run(host="0.0.0.0", port=PORT)
