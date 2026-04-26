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
        """Логін з витягуванням XSRF-TOKEN"""
        self.is_logged_in = False
        login_url = "https://my.binotel.ua/"
        headers = {
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8'
        }
        try:
            log("--- СПРОБА АВТОРИЗАЦІЇ ---")
            # Отримуємо початкову сторінку та токен форми
            res = self.session.get(login_url, headers=headers, timeout=10)
            token_match = re.search(r'name=["\']_token["\']\s+value=["\'](.+?)["\']', res.text)
            
            payload = {
                'logining[email]': BINOTEL_EMAIL,
                'logining[password]': BINOTEL_PASSWORD,
                'logining[submit]': 'Увійти'
            }
            if token_match:
                payload['_token'] = token_match.group(1)

            # Логінимось
            auth_res = self.session.post(login_url, data=payload, headers=headers, allow_redirects=True)
            
            if "logout" in auth_res.text.lower() or "bocrm" in auth_res.text.lower():
                log("=== ЛОГІН УСПІШНИЙ! ===")
                
                # ВИШУКУЄМО XSRF-TOKEN В КУКАХ (це критично!)
                cookies = self.session.cookies.get_dict()
                if 'XSRF-TOKEN' in cookies:
                    # Декодуємо токен (він приходить зашифрованим у URL форматі)
                    self.xsrf_token = urllib.parse.unquote(cookies['XSRF-TOKEN'])
                    log("XSRF-TOKEN отримано.")
                
                self.is_logged_in = True
                return True
            
            log("Помилка: логін не пройшов. Перевір дані в Railway.")
            return False
        except Exception as e:
            log(f"Помилка входу: {e}")
            return False

    def get_slots(self, target_date=None, retry_count=0):
        """Отримання слотів з використанням XSRF заголовка"""
        if retry_count > 1: # Захист від циклу
            return "Зараз уточнюю графік у майстрів..."

        if not self.is_logged_in:
            if not self.login():
                return "База тимчасово недоступна."

        if not target_date:
            target_date = datetime.now().strftime("%Y-%m-%d")
            
        url = f"https://my.binotel.ua/b/bocrm/calendar/day?branchId=9970&startDate={target_date}"
        
        # ДОДАЄМО X-XSRF-TOKEN В ЗАГОЛОВКИ
        headers = {
            'accept': 'application/json, text/plain, */*',
            'x-requested-with': 'XMLHttpRequest',
            'referer': 'https://my.binotel.ua/f/bookon/',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
        }
        if self.xsrf_token:
            headers['x-xsrf-token'] = self.xsrf_token

        try:
            log(f"Запит розкладу на {target_date}...")
            res = self.session.get(url, headers=headers, timeout=12)
            
            if res.status_code == 200:
                data = res.json()
                masters = {}
                for k in ["specialists", "employees", "staff", "users"]:
                    if k in data and isinstance(data[k], list):
                        for m in data[k]:
                            masters[m.get("id")] = m.get("name", "Спеціаліст")
                        break

                if "freeTimes" in data and len(data["freeTimes"]) > 0:
                    slots = []
                    for s in data["freeTimes"]:
                        t = s['startTime'].split(' ')[1]
                        n = masters.get(s.get('specialistId'), "Майстер")
                        slots.append(f"- {t} (Майстер: {n})")
                    return f"Вільні місця на {target_date}:\n" + "\n".join(sorted(list(set(slots))))
                return f"На жаль, на {target_date} все зайнято."
            
            if res.status_code == 401:
                log("Сесія відхилена (401), роблю перелогін...")
                self.is_logged_in = False
                return self.get_slots(target_date, retry_count + 1)

            return "Зараз уточню розклад!"
        except Exception as e:
            log(f"Помилка CRM: {e}")
            return "Зараз перевірю графік і відповім!"

crm_manager = BinotelSession()

SYSTEM_PROMPT = """
Ти — адміністратор салону "Rozmary". Відповідай коротко.
Пропонуй ТІЛЬКИ час і майстрів з розкладу.
Наприкінці кажи: "Зараз передам вашу заявку адміну!"
"""

def send_message(recipient_id, text):
    url = f"https://graph.facebook.com/v18.0/me/messages?access_token={FB_PAGE_ACCESS_TOKEN}"
    requests.post(url, json={"recipient": {"id": recipient_id}, "message": {"text": text}})

def process_message(sender_id, user_text):
    today_dt = datetime.now()
    target_date = today_dt.strftime("%Y-%m-%d")
    
    if "завтр" in user_text.lower():
        target_date = (today_dt + timedelta(days=1)).strftime("%Y-%m-%d")

    # Якщо новий діалог - йдемо в CRM
    if sender_id not in user_sessions:
        log(f"Запит від {sender_id}. Тягну розклад...")
        crm_data = crm_manager.get_slots(target_date)
        full_p = f"{SYSTEM_PROMPT}\n\nРОЗКЛАД ({target_date}):\n{crm_data}"
        user_sessions[sender_id] = [{"role": "system", "content": full_p}]
    
    user_sessions[sender_id].append({"role": "user", "content": user_text})
    
    try:
        res = client.chat.completions.create(model="gpt-4o", messages=user_sessions[sender_id], temperature=0.3)
        reply = res.choices[0].message.content
        user_sessions[sender_id].append({"role": "assistant", "content": reply})
        send_message(sender_id, reply)
        log("Відповідь надіслана.")
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
