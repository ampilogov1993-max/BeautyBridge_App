import os
import requests
import threading
import re
import sys
from flask import Flask, request
from openai import OpenAI
from datetime import datetime, timedelta
import urllib.parse

app = Flask(__name__)

def log(message):
    print(message, flush=True)
    sys.stdout.flush()

# --- КЛЮЧІ ---
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
        headers = {
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8'
        }
        try:
            log("--- [КРОК 1] ІНІЦІАЛІЗАЦІЯ ---")
            res = self.session.get(login_url, headers=headers, timeout=10)
            
            # Шукаємо токен в коді
            token_match = re.search(r'name=["\']_token["\']\s+value=["\'](.+?)["\']', res.text)
            csrf_token = token_match.group(1) if token_match else None
            
            # Якщо в коді немає, беремо з куків (XSRF-TOKEN)
            if not csrf_token:
                cookies = self.session.cookies.get_dict()
                if 'XSRF-TOKEN' in cookies:
                    csrf_token = urllib.parse.unquote(cookies['XSRF-TOKEN'])
                    log("Токен знайдено в Cookies!")

            log(f"Токен: {'ОК' if csrf_token else 'ВІДСУТНІЙ'}")

            payload = {
                'logining[email]': BINOTEL_EMAIL,
                'logining[password]': BINOTEL_PASSWORD,
                'logining[submit]': 'Увійти'
            }
            if csrf_token:
                payload['_token'] = csrf_token

            log("--- [КРОК 2] ВІДПРАВКА ПАРОЛЯ ---")
            auth_res = self.session.post(login_url, data=payload, headers=headers, allow_redirects=True)
            
            final_cookies = self.session.cookies.get_dict()
            if 'bocrm_production_session' in final_cookies:
                log("=== УСПІХ! ВХІД ВИКОНАНО ===")
                self.is_logged_in = True
                if 'XSRF-TOKEN' in final_cookies:
                    self.xsrf_token = urllib.parse.unquote(final_cookies['XSRF-TOKEN'])
                return True
            
            log("ЛОГІН НЕ ВДАВСЯ. Можливо, Captcha або блок IP.")
            return False
        except Exception as e:
            log(f"Помилка входу: {e}")
            return False

    def get_slots(self, target_date=None, retry=0):
        if retry > 1: return "Уточнюю графік у адміна..."
        
        if not self.is_logged_in:
            if not self.login():
                return "База на оновленні. Залиште номер!"

        if not target_date:
            target_date = datetime.now().strftime("%Y-%m-%d")
            
        url = f"https://my.binotel.ua/b/bocrm/calendar/day?branchId=9970&startDate={target_date}"
        headers = {
            'accept': 'application/json, text/plain, */*',
            'x-requested-with': 'XMLHttpRequest',
            'x-xsrf-token': self.xsrf_token if self.xsrf_token else '',
            'referer': 'https://my.binotel.ua/f/bookon/',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
        }

        try:
            log(f"Запит розкладу на {target_date}...")
            res = self.session.get(url, headers=headers, timeout=12)
            
            if res.status_code == 200:
                data = res.json()
                # Збираємо імена майстрів
                masters = {}
                for key in ["specialists", "employees", "staff"]:
                    if key in data:
                        for m in data[key]:
                            masters[m['id']] = m.get('name', 'Майстер')
                        break

                if "freeTimes" in data and len(data["freeTimes"]) > 0:
                    slots = []
                    for s in data["freeTimes"]:
                        t = s['startTime'].split(' ')[1]
                        n = masters.get(s.get('specialistId'), "Майстер")
                        slots.append(f"- {t} (Майстер: {n})")
                    return f"Вільні місця на {target_date}:\n" + "\n".join(sorted(list(set(slots))))
                return f"На {target_date} все зайнято."
            
            if res.status_code == 401:
                log("401: Сесія злетіла.")
                self.is_logged_in = False
                return self.get_slots(target_date, retry + 1)
            
            return "Зараз уточню!"
        except Exception as e:
            log(f"Помилка CRM: {e}")
            return "База оновлюється..."

crm_manager = BinotelSession()

def send_message(recipient_id, text):
    url = f"https://graph.facebook.com/v18.0/me/messages?access_token={FB_PAGE_ACCESS_TOKEN}"
    requests.post(url, json={"recipient": {"id": recipient_id}, "message": {"text": text}})

def process_message(sender_id, user_text):
    today_dt = datetime.now()
    target_date = today_dt.strftime("%Y-%m-%d")
    if "завтр" in user_text.lower():
        target_date = (today_dt + timedelta(days=1)).strftime("%Y-%m-%d")

    if sender_id not in user_sessions:
        log(f"Новий клієнт. Йду в CRM...")
        crm_data = crm_manager.get_slots(target_date)
        full_p = f"Ти адмін салону Rozmary. Відповідай коротко. РОЗКЛАД ({target_date}):\n{crm_data}"
        user_sessions[sender_id] = [{"role": "system", "content": full_p}]
    
    user_sessions[sender_id].append({"role": "user", "content": user_text})
    
    try:
        res = client.chat.completions.create(model="gpt-4o", messages=user_sessions[sender_id], temperature=0.3)
        reply = res.choices[0].message.content
        user_sessions[sender_id].append({"role": "assistant", "content": reply})
        send_message(sender_id, reply)
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
