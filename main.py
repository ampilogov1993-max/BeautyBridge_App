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
BINOTEL_KEY = env("BINOTEL_KEY") or env("BINOTEL_API_KEY")
BINOTEL_SECRET = env("BINOTEL_SECRET") or env("BINOTEL_API_SECRET")
TG_TOKEN = env("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = env("TELEGRAM_CHAT_ID")
PORT = int(env("PORT", "8080"))
# Залишаємо як число, це стандарт для JSON
BINOTEL_BRANCH_ID = int(env("BINOTEL_BRANCH_ID", "9970"))
SESSION_TTL_MINUTES = int(env("SESSION_TTL_MINUTES", "60"))

client = OpenAI(api_key=OPENAI_API_KEY)
user_sessions = {}
user_locks = {}
locks_guard = threading.Lock()

def get_user_lock(user_id):
    with locks_guard:
        lock = user_locks.get(user_id)
        if lock is None:
            lock = threading.Lock(); user_locks[user_id] = lock
        return lock

def session_is_expired(session):
    t = session.get("updated_at")
    return not t or (datetime.now() - t > timedelta(minutes=SESSION_TTL_MINUTES))

def trim_messages(m, k=15):
    if len(m) > k+1:
        sys_msg = m[0]; tail = m[-k:]; m[:] = [sys_msg] + tail

def send_tg_notification(text):
    if TG_TOKEN and TG_CHAT_ID:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        try: requests.post(url, json={"chat_id": TG_CHAT_ID, "text": f"🔔 BeautyBridge:\n{text}"}, timeout=10)
        except: pass

class BinotelAPI:
    def __init__(self, key, secret, branch_id):
        self.key = key
        self.secret = secret
        self.branch_id = branch_id
        self.base_url = "https://api.binotel.com/api/2.0"

    def get_free_slots(self, date_str):
        url = f"{self.base_url}/bookon/get-free-times-for-day.json"
        request_data = {"branchId": self.branch_id, "startDate": date_str}
        
        # Строгий JSON без пробелов
        request_json = json.dumps(request_data, separators=(",", ":"), ensure_ascii=False)
        
        # --- ГЕНИАЛЬНАЯ ИДЕЯ: ПЕРЕБОР ФОРМУЛ ---
        variants = {
            "key_json_secret": f"{self.key}{request_json}{self.secret}",
            "secret_key_json": f"{self.secret}{self.key}{request_json}",
            "json_secret": f"{request_json}{self.secret}",
            "secret_json": f"{self.secret}{request_json}",
            "key_secret_json": f"{self.key}{self.secret}{request_json}"
        }

        log(f"--- СТАРТ ТЕСТУВАННЯ ПІДПИСІВ ({date_str}) ---")
        
        for name, raw_sig in variants.items():
            signature = hashlib.md5(raw_sig.encode("utf-8")).hexdigest()
            payload = {
                "key": self.key,
                "signature": signature,
                "requestData": request_data
            }
            
            try:
                res = requests.post(url, json=payload, timeout=5)
                log(f"Тест [{name}]: HTTP {res.status_code}")
                
                if res.status_code == 200:
                    data = res.json()
                    if data.get('status') == 'error':
                        log(f"  -> Помилка Binotel: {data.get('message')}")
                        continue # Пробуємо наступний варіант
                    
                    log(f"🔥🔥🔥 WINNER FOUND: {name} 🔥🔥🔥")
                    masters = {m['id']: m.get('name', 'Фахівець') for m in data.get('specialists', [])}
                    if "freeTimes" in data and len(data["freeTimes"]) > 0:
                        slots = [f"- {s['startTime'].split(' ')[1]} ({masters.get(s.get('specialistId'), 'Майстер')})" for s in data["freeTimes"]]
                        return "\n".join(sorted(list(set(slots))))
                    return "ALL_BUSY"
                else:
                    log(f"  -> Відповідь: {res.text}")
                    
            except Exception as e:
                log(f"  -> Помилка з'єднання: {e}")

        log("--- ЖОДЕН ПІДПИС НЕ ПІДІЙШОВ ---")
        return "NO_DATA"

crm = BinotelAPI(BINOTEL_KEY, BINOTEL_SECRET, BINOTEL_BRANCH_ID)

def resolve_target_date(text):
    t = datetime.now()
    return (t + timedelta(days=1)).strftime("%Y-%m-%d") if "завтр" in text.lower() else t.strftime("%Y-%m-%d")

def build_system_prompt(target_date, crm_data):
    if crm_data == "ALL_BUSY":
        instr = f"На {target_date} вільних місць немає. Попроси клієнта обрати іншу дату."
    elif crm_data == "NO_DATA":
        instr = "Дані оновлюються. Скажи, що адмін напише за хвилину. НЕ ВИГАДУЙ ЧАС."
    else:
        instr = f"Вільні вікна на {target_date}: {crm_data}."
    return f"Ти адмін Rozmary у Львові. Кажи коротко. {instr}"

def process_message(sender_id, text):
    with get_user_lock(sender_id):
        target_date = resolve_target_date(text)
        session = user_sessions.get(sender_id)
        
        if not session or session.get("target_date") != target_date or session_is_expired(session):
            send_tg_notification(f"Клієнт в IG: {text}")
            log(f"Оновлюю CRM...")
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
            reply = response.choices[0].message.content or "Адмін скоро напише!"
            session["messages"].append({"role": "assistant", "content": reply})
            
            url = f"https://graph.facebook.com/v18.0/me/messages?access_token={FB_PAGE_ACCESS_TOKEN}"
            requests.post(url, json={"recipient": {"id": sender_id}, "message": {"text": reply}})
            log("OK.")
        except Exception as e: log(f"AI error: {e}")

@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":
        if request.args.get("hub.verify_token") == VERIFY_TOKEN: return request.args.get("hub.challenge"), 200
        return "403", 403
    data = request.get_json(silent=True) or {}
    for entry in data.get("entry", []):
        for event in entry.get("messaging", []):
            if not event.get("message", {}).get("is_echo") and event.get("sender", {}).get("id"):
                threading.Thread(target=process_message, args=(event["sender"]["id"], event.get("message", {}).get("text", "")), daemon=True).start()
    return "OK", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
