import os
import requests
import threading
import re
import sys
import urllib.parse
from flask import Flask, request
from openai import OpenAI
from datetime import datetime, timedelta

app = Flask(__name__)

def log(msg):
    print(msg, flush=True)
    sys.stdout.flush()

# --- ЕНВІРОНМЕНТ З RAILWAY ---
FB_PAGE_ACCESS_TOKEN = os.environ.get("FB_PAGE_ACCESS_TOKEN")
VERIFY_TOKEN = "rozmary2026"
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
BINOTEL_EMAIL = os.environ.get("BINOTEL_EMAIL")
BINOTEL_PASSWORD = os.environ.get("BINOTEL_PASSWORD")

client = OpenAI(api_key=OPENAI_API_KEY)
user_sessions = {}

class BinotelSession:
    def __init__(self):
        self.session = requests.Session()
        self.is_logged_in = False
        self.xsrf_token = None

    def login(self):
        login_url = "https://my.binotel.ua/"
        ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
        try:
            log("--- СПРОБА ЛОГІНУ ---")
            res = self.session.get(login_url, headers={'user-agent': ua}, timeout=10)
            token = re.search(r'name=["\']_token["\']\s+value=["\'](.+?)["\']', res.text)
            
            payload = {
                'logining[email]': BINOTEL_EMAIL,
                'logining[password]': BINOTEL_PASSWORD,
                'logining[submit]': 'Увійти'
            }
            if token:
                payload['_token'] = token.group(1)

            auth = self.session.post(login_url, data=payload, headers={'user-agent': ua, 'referer': login_url}, allow_redirects=True)
            cookies = self.session.cookies.get_dict()
            
            if 'bocrm_production_session' in cookies:
                log("=== УСПІХ! СЕСІЯ Є ===")
                self.is_logged_in = True
                if 'XSRF-TOKEN' in cookies:
                    self.xsrf_token = urllib.parse.unquote(cookies['XSRF-TOKEN'])
                return True
            log("ПОМИЛКА: Невірні дані в Railway Variables!")
            return False
        except Exception as e:
            log(f"Критична помилка: {e}")
            return False

    def get_slots(self, target_date=None, retry=0):
        if retry > 1: return "Уточнюю графік у адміна..."
        if not self.is_logged_in and not self.login():
            return "База оновлюється, зачекайте!"
        
        if not target_date:
            target_date = datetime.now().strftime("%Y-%m-%d")
            
        url = f"https://my.binotel.ua/b/bocrm/calendar/day?branchId=9970&startDate={target_date}"
        headers = {
            'x-requested-with': 'XMLHttpRequest',
            'x-xsrf-token': self.xsrf_token if self.xsrf_token else '',
            'referer': 'https://my.binotel.ua/f/bookon/',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
        }
        try:
            res = self.session.get(url, headers=headers, timeout=12)
            if res.status_code == 200:
                data = res.json()
                masters = {m['id']: m.get('name', 'Майстер') for m in data.get('specialists', [])}
                if "freeTimes" in data and len(data["freeTimes"]) > 0:
                    slots = []
                    for s in data["freeTimes"]:
                        t = s['startTime'].split(' ')[1]
                        n = masters.get(s.get('specialistId'), "Майстер")
                        slots.append(f"- {t} (Майстер: {n})")
                    return f"На {target_date} є місця:\n" + "\n".join(sorted(list(set(slots))))
                return f"На {target_date} все зайнято."
            if res.status_code == 401:
                self.is_logged_in = False
                return self.get_slots(target_date, retry + 1)
            return "Зараз уточню!"
        except:
            return "База на паузі."

crm_manager = BinotelSession()

def send_message(rid, text):
    url = f"https://graph.facebook.com/v18.0/me/messages?access_token={FB_PAGE_ACCESS_TOKEN}"
    requests.post(url, json={"recipient": {"id": rid}, "message": {"text": text}})

def process_message(sid, text):
    target = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d") if "завтр" in text.lower() else datetime.now().strftime("%Y-%m-%d")
    if sid not in user_sessions:
        data = crm_manager.get_slots(target)
        user_sessions[sid] = [{"role": "system", "content": f"Ти адмін Rozmary. Кажи коротко. Дані:\n{data}"}]
    user_sessions[sid].append({"role": "user", "content": text})
    try:
        res = client.chat.completions.create(model="gpt-4o", messages=user_sessions[sid], temperature=0.3)
        reply = res.choices[0].message.content
        user_sessions[sid].append({"role": "assistant", "content": reply})
        send_message(sid, reply)
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
