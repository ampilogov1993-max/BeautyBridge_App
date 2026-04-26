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

# --- КОНФІГУРАЦІЯ ---
FB_PAGE_ACCESS_TOKEN = env("FB_PAGE_ACCESS_TOKEN")
VERIFY_TOKEN = env("VERIFY_TOKEN", "rozmary2026")
OPENAI_API_KEY = env("OPENAI_API_KEY")
OPENAI_MODEL = env("OPENAI_MODEL", "gpt-4o")
BINOTEL_KEY = env("BINOTEL_API_KEY")
BINOTEL_SECRET = env("BINOTEL_API_SECRET")
TG_TOKEN = env("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = env("TELEGRAM_CHAT_ID")
PORT = int(env("PORT", "8080"))
BINOTEL_BRANCH_ID = env("BINOTEL_BRANCH_ID", "9970")
SESSION_TTL_MINUTES = int(env("SESSION_TTL_MINUTES", "60"))

client = OpenAI(api_key=OPENAI_API_KEY)

user_sessions = {}
user_locks = {}
locks_guard = threading.Lock()

# --- ЛОГІКА СЕСІЙ ТА БЛОКУВАНЬ ---
def get_user_lock(user_id):
    with locks_guard:
        lock = user_locks.get(user_id)
        if lock is None:
            lock = threading.Lock()
            user_locks[user_id] = lock
        return lock

def session_is_expired(session):
    updated_at = session.get("updated_at")
    if not updated_at: return True
    return datetime.now() - updated_at > timedelta(minutes=SESSION_TTL_MINUTES)

def trim_messages(messages, keep_last=15):
    if len(messages) <= keep_last + 1: return
    system_message = messages[0]
    tail = messages[-keep_last:]
    messages[:] = [system_message] + tail

def send_tg_notification(text):
    if not (TG_TOKEN and TG_CHAT_ID): return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TG_CHAT_ID, "text": f"🔔 BeautyBridge:\n{text}"}, timeout=10)
    except Exception as exc: log(f"Telegram error: {exc}")

# --- BINOTEL API КЛАС ---
class BinotelAPI:
    def __init__(self, key, secret, branch_id):
        self.key = key
        self.secret = secret
        self.branch_id = str(branch_id)
        self.base_url = "https://api.binotel.com/api/2.0"

    def get_free_slots(self, date_str):
        url = f"{self.base_url}/bookon/get-free-times-for-day.json"
        request_data = {"branchId": self.branch_id, "startDate": date_str}
        
        # Генерація підпису за твоїм алгоритмом: KEY + JSON + SECRET
        request_json = json.dumps(request_data, separators=(",", ":"), ensure_ascii=False)
        raw_sig = f"{self.key}{request_json}{self.secret}"
        signature = hashlib.md5(raw_sig.encode("utf-8")).hexdigest()
        
        payload = {"key": self.key, "signature": signature, "requestData": request_data}
        
        try:
            res = requests.post(url, json=payload, timeout=15)
            if res.status_code == 200:
                data = res.json()
                if data.get('status') == 'error': return "NO_DATA"
                
                masters = {m['id']: m.get('name', 'Спеціаліст') for m in data.get('specialists', [])}
                if "freeTimes" in data and len(data["freeTimes"]) > 0:
                    slots = []
                    for s in data["freeTimes"]:
                        time = s['startTime'].split(' ')[1]
                        name = masters.get(s.get('specialistId'), "Фахівець")
                        slots.append(f"- {time} ({name})")
                    return "\n".join(sorted(list(set(slots))))
                return "ALL_BUSY"
            return "NO_DATA"
        except: return "NO_DATA"

crm = BinotelAPI(BINOTEL_KEY, BINOTEL_SECRET, BINOTEL_BRANCH_ID)

# --- ЛОГІКА ОБРОБКИ ПОВІДОМЛЕНЬ (Твій новий код) ---
def resolve_target_date(text):
    today = datetime.now()
    if "завтр" in text.lower():
        return (today + timedelta(days=1)).strftime("%Y-%m-%d")
    return today.strftime("%Y-%m-%d")

def build_system_prompt(target_date, crm_data):
    if crm_data == "ALL_BUSY":
        schedule_instruction = f"На {target_date} вільних місць немає. Попроси клієнта обрати іншу дату або зачекати адміністратора."
    elif crm_data == "NO_DATA":
        schedule_instruction = "Зараз дані розкладу недоступні. Скажи, що адміністратор напише особисто найближчим часом. Не вигадуй час."
    else:
        schedule_instruction = f"Ось реальний розклад на {target_date}: {crm_data}. Пропонуй тільки цей час."

    return f"Ти адміністратор салону Rozmary у Львові. Відповідай коротко і ввічливо. {schedule_instruction}"

def ensure_session(user_id, user_text, target_date):
    session = user_sessions.get(user_id)
    if session is None or session.get("target_date") != target_date or session_is_expired(session):
        send_tg_notification(f"Клієнт в Instagram: {user_text}")
        log("Тягну дані CRM...")
        crm_data = crm.get_free_slots(target_date)
        system_prompt = build_system_prompt(target_date, crm_data)
        session = {
            "target_date": target_date,
            "updated_at": datetime.now(),
            "messages": [{"role": "system", "content": system_prompt}],
        }
        user_sessions[user_id] = session
    return session

def send_instagram_msg(recipient_id, text):
    url = f"https://graph.facebook.com/v18.0/me/messages?access_token={FB_PAGE_ACCESS_TOKEN}"
    requests.post(url, json={"recipient": {"id": recipient_id}, "message": {"text": text}})

def process_message(sender_id, text):
    user_lock = get_user_lock(sender_id)
    with user_lock:
        target_date = resolve_target_date(text)
        session = ensure_session(sender_id, text, target_date)
        session["updated_at"] = datetime.now()
        session["messages"].append({"role": "user", "content": text})
        trim_messages(session["messages"])

        try:
            response = client.chat.completions.create(model=OPENAI_MODEL, messages=session["messages"], temperature=0.0)
            reply = response.choices[0].message.content or "Зараз не можу відповісти. Адмін напише вам!"
            session["messages"].append({"role": "assistant", "content": reply})
            send_instagram_msg(sender_id, reply)
            log("Відповідь надіслана.")
        except Exception as exc:
            log(f"OpenAI error: {exc}")
            send_instagram_msg(sender_id, "Дякуємо. Адмін скоро зв'яжеться з вами!")

@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":
        if request.args.get("hub.verify_token") == VERIFY_TOKEN:
            return request.args.get("hub.challenge"), 200
        return "Forbidden", 403

    data = request.get_json(silent=True) or {}
    if data.get("object") != "instagram": return "IGNORED", 200

    for entry in data.get("entry", []):
        for event in entry.get("messaging", []):
            if event.get("message", {}).get("is_echo"): continue
            sender_id = event.get("sender", {}).get("id")
            text = event.get("message", {}).get("text")
            if sender_id and text:
                threading.Thread(target=process_message, args=(sender_id, text), daemon=True).start()
    return "EVENT_RECEIVED", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
