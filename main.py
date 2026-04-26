import os
import requests
import threading
from flask import Flask, request
from openai import OpenAI
from datetime import datetime

app = Flask(__name__)

# --- КЛЮЧІ З RAILWAY ---
FB_PAGE_ACCESS_TOKEN = os.environ.get("FB_PAGE_ACCESS_TOKEN")
VERIFY_TOKEN = "rozmary2026"
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
BINOTEL_EMAIL = os.environ.get("BINOTEL_EMAIL")
BINOTEL_PASSWORD = os.environ.get("BINOTEL_PASSWORD")

client = OpenAI(api_key=OPENAI_API_KEY)

# --- ПАМ'ЯТЬ ДІАЛОГІВ ---
user_sessions = {}

class BinotelSession:
    """Клас для підтримки «вічної» сесії з Binotel"""
    def __init__(self):
        self.session = requests.Session()
        self.is_logged_in = False

    def login(self):
        """Функція автоматичного входу"""
        login_url = "https://my.binotel.ua/"
        payload = {
            'email': BINOTEL_EMAIL,
            'password': BINOTEL_PASSWORD,
            'remember': 'on'
        }
        headers = {
            'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36'
        }
        try:
            # Робимо POST запит для входу
            response = self.session.post(login_url, data=payload, headers=headers, allow_redirects=True)
            if response.status_code == 200 and ("logout" in response.text.lower() or "bocrm" in response.text.lower()):
                print("=== УСПІШНИЙ АВТОЛОГІН У BINOTEL ===")
                self.is_logged_in = True
                return True
            print(f"Помилка логіну. Статус: {response.status_code}")
            return False
        except Exception as e:
            print(f"Критична помилка при вході: {e}")
            return False

    def get_slots(self):
        """Отримання розкладу з перевіркою авторизації"""
        if not self.is_logged_in:
            self.login()

        today = datetime.now().strftime("%Y-%m-%d")
        url = f"https://my.binotel.ua/b/bocrm/calendar/day?branchId=9970&startDate={today}"
        
        headers = {
            'accept': 'application/json, text/plain, */*',
            'x-requested-with': 'XMLHttpRequest',
            'referer': 'https://my.binotel.ua/f/bookon/'
        }

        try:
            response = self.session.get(url, headers=headers, timeout=7)
            
            # Якщо сесія протухла (401), пробуємо перелогінитись один раз
            if response.status_code == 401:
                print("Сесія протухла, оновлюю вхід...")
                if self.login():
                    response = self.session.get(url, headers=headers, timeout=7)

            if response.status_code == 200:
                data = response.json()
                masters = {}
                # Парсимо імена майстрів
                for key in ["specialists", "employees", "staff", "users"]:
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
                    return f"Сьогодні ({today}) є такі вікна:\n{available_times}"
                return "На сьогодні вільних місць немає."
            
            return "Адміністратор зараз оновлює розклад, зачекайте хвилинку."
        except Exception as e:
            print(f"Помилка CRM: {e}")
            return "Не вдалося отримати графік."

# Створюємо один екземпляр сесії на весь додаток
crm_manager = BinotelSession()

SYSTEM_PROMPT = """
Ти — помічник адміністратора салону "Rozmary". 
Твоя мета: консультувати клієнтів щодо вільного часу.
1. Пропонуй ТІЛЬКИ той час і тих майстрів, які бачиш у системних даних.
2. Не вигадуй імен.
3. Не підтверджуй запис остаточно, кажи: "Передаю дані адміністратору для підтвердження".
4. Спочатку пропонуй ранок (10:00-12:00).
"""

def send_message(recipient_id, text):
    url = f"https://graph.facebook.com/v18.0/me/messages?access_token={FB_PAGE_ACCESS_TOKEN}"
    requests.post(url, json={"recipient": {"id": recipient_id}, "message": {"text": text}})

def process_message(sender_id, user_text):
    if sender_id not in user_sessions:
        crm_data = crm_manager.get_slots()
        full_prompt = f"{SYSTEM_PROMPT}\n\nАКТУАЛЬНИЙ РОЗКЛАД ІЗ БАЗИ:\n{crm_data}"
        user_sessions[sender_id] = [{"role": "system", "content": full_prompt}]
    
    user_sessions[sender_id].append({"role": "user", "content": user_text})
    
    if len(user_sessions[sender_id]) > 11:
        user_sessions[sender_id] = [user_sessions[sender_id][0]] + user_sessions[sender_id][-10:]

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=user_sessions[sender_id],
            temperature=0.4
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
