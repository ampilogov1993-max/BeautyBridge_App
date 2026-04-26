import os
import requests
import threading
import re
import sys
from flask import Flask, request
from openai import OpenAI
from datetime import datetime, timedelta

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

    def login(self):
        login_page_url = "https://my.binotel.ua/"
        headers = {
            'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36',
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'accept-language': 'uk-UA,uk;q=0.9,en-US;q=0.8,en;q=0.7'
        }
        try:
            log("--- ЗАПУСК РОЗУМНОГО ЛОГІНУ ---")
            response = self.session.get(login_page_url, headers=headers)
            html = response.text
            
            # Шукаємо токен різними способами (regex став потужнішим)
            patterns = [
                r'name="_token"\s+value="(.+?)"',
                r'value="(.+?)"\s+name="_token"',
                r'name=\'_token\'\s+value=\'(.+?)\'',
                r'content="(.+?)"\s+name="csrf-token"'
            ]
            
            csrf_token = None
            for pattern in patterns:
                match = re.search(pattern, html)
                if match:
                    csrf_token = match.group(1)
                    break
            
            if not csrf_token:
                log("ПОМИЛКА: Токен не знайдено! Ось перші 500 символів сторінки:")
                log(html[:500].replace('\n', ' ')) # Виводимо шматок коду для аналізу
                return False
            
            log(f"Токен знайдено: {csrf_token[:15]}...")

            payload = {
                '_token': csrf_token,
                'email': BINOTEL_EMAIL,
                'password': BINOTEL_PASSWORD,
                'remember': 'on'
            }
            
            # Намагаємося зайти
            login_res = self.session.post(login_page_url, data=payload, headers=headers, allow_redirects=True)
            
            if "logout" in login_res.text.lower() or "bocrm" in login_res.text.lower():
                log("=== УСПІШНО! БОТ УВІЙШОВ У CRM ===")
                self.is_logged_in = True
                return True
            
            log("ЛОГІН НЕ ВДАВСЯ: Пароль вірний, але система не пустила.")
            return False
        except Exception as e:
            log(f"Критична помилка: {e}")
            return False

    def get_slots(self, target_date=None):
        if not self.is_logged_in:
            if not self.login():
                return "Зараз база на профілактиці, зачекайте."

        if not target_date:
            target_date = datetime.now().strftime("%Y-%m-%d")
            
        url = f"https://my.binotel.ua/b/bocrm/calendar/day?branchId=9970&startDate={target_date}"
        headers = {
            'accept': 'application/json, text/plain, */*',
            'x-requested-with': 'XMLHttpRequest',
            'referer': 'https://my.binotel.ua/f/bookon/'
        }

        try:
            log(f"Йду за даними на {target_date}...")
            res = self.session.get(url, headers=headers, timeout=10)
            
            if res.status_code == 200:
                data = res.json()
                # Зшиваємо час і майстрів
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
                        n = masters.get(s.get('specialistId'), "Спеціаліст")
                        slots.append(f"- {t} (Майстер: {n})")
                    return f"Вільні місця на {target_date}:\n" + "\n".join(sorted(list(set(slots))))
                return f"На {target_date} все зайнято."
            return "База оновлюється."
        except Exception as e:
            log(f"Помилка CRM: {e}")
            return "Технічна пауза."

crm_manager = BinotelSession()

SYSTEM_PROMPT = """
Ти — адмін салону "Rozmary". Відповідай коротко і по суті.
Використовуй ТІЛЬКИ дані з розкладу.
Якщо розкладу немає (пише про профілактику), скажи: "Зараз перевірю розклад і відповім!"
"""

def send_message(recipient_id, text):
    url = f"https://graph.facebook.com/v18.0/me/messages?access_token={FB_PAGE_ACCESS_TOKEN}"
    requests.post(url, json={"recipient": {"id": recipient_id}, "message": {"text": text}})

def process_message(sender_id, user_text):
    today_dt = datetime.now()
    target_date = today_dt.strftime("%Y-%m-%d")
    if "завтр" in user_text.lower():
        target_date = (today_dt + timedelta(days=1)).strftime("%Y-%m-%d")

    if sender_id not in user_sessions:
        log("Новий юзер. Запит у CRM...")
        crm_data = crm_manager.get_slots(target_date)
        full_p = f"{SYSTEM_PROMPT}\n\nРОЗКЛАД ({target_date}):\n{crm_data}"
        user_sessions[sender_id] = [{"role": "system", "content": full_p}]
    
    user_sessions[sender_id].append({"role": "user", "content": user_text})
    
    try:
        res = client.chat.completions.create(model="gpt-4o", messages=user_sessions[sender_id], temperature=0.3)
        reply = res.choices[0].message.content
        user_sessions[sender_id].append({"role": "assistant", "content": reply})
        send_message(sender_id, reply)
        log("Відповідь відправлена.")
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
