import os
import requests
import threading
import re
import sys
from flask import Flask, request
from openai import OpenAI
from datetime import datetime, timedelta

app = Flask(__name__)

# Вимикаємо буферизацію логів, щоб бачити все миттєво
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
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8'
        }
        try:
            log("--- СПРОБА ЛОГІНУ ---")
            # 1. Отримуємо сторінку
            response = self.session.get(login_page_url, headers=headers)
            log(f"Сторінка логіну завантажена. Статус: {response.status_code}")
            
            # 2. Шукаємо токен
            token_search = re.search(r'name="_token" value="(.+?)"', response.text)
            if not token_search:
                log("ПОМИЛКА: CSRF токен не знайдено в HTML!")
                return False
            
            csrf_token = token_search.group(1)
            log(f"Токен знайдено: {csrf_token[:10]}...")

            # 3. Відправляємо пароль
            payload = {
                '_token': csrf_token,
                'email': BINOTEL_EMAIL,
                'password': BINOTEL_PASSWORD,
                'remember': 'on'
            }
            
            login_response = self.session.post(login_page_url, data=payload, headers=headers, allow_redirects=True)
            log(f"Відповідь після логіну: {login_response.url}")
            
            # Перевірка успіху
            final_html = login_response.text.lower()
            if "logout" in final_html or "bocrm" in final_html or "f/bookon" in final_html:
                log("=== УСПІШНИЙ ВХІД В CRM! ===")
                self.is_logged_in = True
                return True
            
            log("ЛОГІН НЕ ВДАВСЯ: Система знову викинула на сторінку входу.")
            return False
        except Exception as e:
            log(f"Критична помилка авторизації: {e}")
            return False

    def get_slots(self, target_date=None):
        if not self.is_logged_in:
            if not self.login():
                return "Доступ до бази тимчасово закритий адміністратором."

        if not target_date:
            target_date = datetime.now().strftime("%Y-%m-%d")
            
        url = f"https://my.binotel.ua/b/bocrm/calendar/day?branchId=9970&startDate={target_date}"
        headers = {
            'accept': 'application/json, text/plain, */*',
            'x-requested-with': 'XMLHttpRequest',
            'referer': 'https://my.binotel.ua/f/bookon/'
        }

        try:
            log(f"Запит розкладу на {target_date}...")
            response = self.session.get(url, headers=headers, timeout=10)
            log(f"Статус CRM: {response.status_code}")

            if response.status_code == 200:
                data = response.json()
                # Збираємо майстрів
                masters = {}
                for key in ["specialists", "employees", "staff", "users"]:
                    if key in data and isinstance(data[key], list):
                        for m in data[key]:
                            masters[m.get("id")] = m.get("name", "Майстер")
                        break

                if "freeTimes" in data and len(data["freeTimes"]) > 0:
                    slots_info = []
                    for slot in data["freeTimes"]:
                        time = slot['startTime'].split(' ')[1]
                        name = masters.get(slot.get('specialistId'), "Майстер")
                        slots_info.append(f"- {time} (Майстер: {name})")
                    
                    available_times = "\n".join(sorted(list(set(slots_info))))
                    return f"На дату {target_date} є такі вільні місця:\n{available_times}"
                return f"На {target_date} вільних місць не знайдено."
            
            return "Зараз триває оновлення бази."
        except Exception as e:
            log(f"Помилка CRM: {e}")
            return "Технічна затримка."

crm_manager = BinotelSession()

SYSTEM_PROMPT = """
Ти — адміністратор салону "Rozmary". Відповідай коротко.
Використовуй ТІЛЬКИ дані про вільні місця з системного повідомлення.
Якщо даних немає, кажи, що зараз уточниш у адміністратора.
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
        log(f"Новий діалог з {sender_id}. Йду за розкладом...")
        crm_data = crm_manager.get_slots(target_date)
        full_prompt = f"{SYSTEM_PROMPT}\n\nАКТУАЛЬНИЙ РОЗКЛАД ({target_date}):\n{crm_data}"
        user_sessions[sender_id] = [{"role": "system", "content": full_prompt}]
    
    user_sessions[sender_id].append({"role": "user", "content": user_text})
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=user_sessions[sender_id],
            temperature=0.3
        )
        ai_reply = response.choices[0].message.content
        user_sessions[sender_id].append({"role": "assistant", "content": ai_reply})
        send_message(sender_id, ai_reply)
        log(f"Відповідь відправлена клієнту.")
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
