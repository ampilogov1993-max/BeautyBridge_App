import os
import requests
import threading
import re
from flask import Flask, request
from openai import OpenAI
from datetime import datetime, timedelta

app = Flask(__name__)

# --- КЛЮЧІ З RAILWAY ---
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
        """Парсимо CSRF-токен та заходимо в CRM"""
        login_page_url = "https://my.binotel.ua/"
        headers = {
            'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36'
        }
        try:
            # 1. Отримуємо сторінку логіну
            response = self.session.get(login_page_url, headers=headers)
            html = response.text
            
            # 2. Шукаємо прихований _token через Regular Expression
            token_search = re.search(r'name="_token" value="(.+?)"', html)
            if not token_search:
                print("Помилка: Не вдалося знайти CSRF токен на сторінці!")
                return False
            
            csrf_token = token_search.group(1)
            print(f"Знайдено секретний токен: {csrf_token[:10]}...")

            # 3. Робимо реальний POST для входу
            payload = {
                '_token': csrf_token,
                'email': BINOTEL_EMAIL,
                'password': BINOTEL_PASSWORD,
                'remember': 'on'
            }
            
            login_response = self.session.post(login_page_url, data=payload, headers=headers, allow_redirects=True)
            
            # Перевіряємо успіх (шукаємо слово logout або b/bocrm)
            if login_response.status_code == 200 and ("logout" in login_response.text.lower() or "bocrm" in login_response.text.lower()):
                print("=== УСПІШНИЙ АВТОЛОГІН У BINOTEL ===")
                self.is_logged_in = True
                return True
            
            print(f"Помилка входу. Перевір BINOTEL_EMAIL та BINOTEL_PASSWORD!")
            return False
        except Exception as e:
            print(f"Критична помилка авторизації: {e}")
            return False

    def get_slots(self, target_date=None):
        """Запит розкладу з автологіном"""
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
            response = self.session.get(url, headers=headers, timeout=10)
            
            # Якщо сесія злетіла (401), пробуємо один раз перелогінитись
            if response.status_code == 401:
                self.login()
                response = self.session.get(url, headers=headers, timeout=10)

            if response.status_code == 200:
                data = response.json()
                masters = {}
                # Шукаємо список майстрів у JSON
                for key in ["specialists", "employees", "staff", "users", "resources"]:
                    if key in data and isinstance(data[key], list):
                        for m in data[key]:
                            masters[m.get("id")] = m.get("name", "Спеціаліст")
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
            
            return "Зараз триває оновлення бази, спробуйте пізніше."
        except Exception as e:
            print(f"Помилка CRM: {e}")
            return "Технічна затримка в базі."

crm_manager = BinotelSession()

SYSTEM_PROMPT = """
Ти — помічник адміністратора салону краси "Rozmary" у Львові. 
Твоя мета: консультувати клієнтів щодо вільного часу.
1. Пропонуй ТІЛЬКИ ті години та тих майстрів, які бачиш у системних даних нижче.
2. Якщо клієнт питає про "завтра", використовуй дані на завтрашню дату.
3. НІКОЛИ не вигадуй майстрів, яких немає в списку.
4. ПРІОРИТЕТ: Спочатку пропонуй ранок (10:00-12:00).
5. Наприкінці кажи: "Передаю заявку адміністратору, він зараз підтвердить ваш запис!"
"""

def send_message(recipient_id, text):
    url = f"https://graph.facebook.com/v18.0/me/messages?access_token={FB_PAGE_ACCESS_TOKEN}"
    requests.post(url, json={"recipient": {"id": recipient_id}, "message": {"text": text}})

def process_message(sender_id, user_text):
    # Визначаємо дату для запиту в CRM
    today_dt = datetime.now()
    target_date = today_dt.strftime("%Y-%m-%d")
    
    if "завтр" in user_text.lower():
        target_date = (today_dt + timedelta(days=1)).strftime("%Y-%m-%d")

    # Якщо новий діалог — підтягуємо свіжий розклад
    if sender_id not in user_sessions:
        crm_data = crm_manager.get_slots(target_date)
        full_prompt = f"{SYSTEM_PROMPT}\n\nАКТУАЛЬНИЙ РОЗКЛАД ({target_date}):\n{crm_data}"
        user_sessions[sender_id] = [{"role": "system", "content": full_prompt}]
    
    user_sessions[sender_id].append({"role": "user", "content": user_text})
    
    # Тримаємо пам'ять діалогу
    if len(user_sessions[sender_id]) > 11:
        user_sessions[sender_id] = [user_sessions[sender_id][0]] + user_sessions[sender_id][-10:]

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=user_sessions[sender_id],
            temperature=0.3
        )
        ai_reply = response.choices[0].message.content
        user_sessions[sender_id].append({"role": "assistant", "content": ai_reply})
        send_message(sender_id, ai_reply)
    except Exception as e:
        print(f"OpenAI Error: {e}")

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
