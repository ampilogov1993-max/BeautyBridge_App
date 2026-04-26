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

client = OpenAI(api_key=OPENAI_API_KEY)

# --- ПАМ'ЯТЬ ДІАЛОГІВ ---
user_sessions = {}

SYSTEM_PROMPT = """
Ти — адміністратор інстаграм-директу салону краси "Rozmary" у Львові. 
Твоя мета: людяно та привітно консультувати клієнтів і допомагати їм визначитися з часом візиту.

ПРАВИЛА СПІЛКУВАННЯ:
1. Клієнт вже веде з тобою діалог. НІКОЛИ не пиши "Привіт", "Доброго дня" або "Вітаю", якщо це не перше повідомлення клієнта. Одразу переходь до суті.
2. Пиши коротко, як жива людина в месенджері. 
3. ПРІОРИТЕТ: Твоє головне завдання — заповнити ранкові зміни з 10:00 до 12:00.

ІНФОРМАЦІЯ ПРО ЗАПИС:
Ти отримуєш список реальних вільних місць із CRM системи. 
Пропонуй ТІЛЬКИ ті години, які є у списку вільних слотів нижче. Якщо слотів немає, скажи, що зараз уточнюєш інформацію.
"""

def get_crm_slots():
    """Функція для запиту в Binotel Bookon (з імітацією браузера)"""
    today = datetime.now().strftime("%Y-%m-%d")
    url = f"https://my.binotel.ua/b/bocrm/calendar/day?branchId=9970&startDate={today}"
    
    headers = {
        'accept': 'application/json, text/plain, */*',
        'cookie': 'bocrm_production_session=eyJpdiI6IkNoZGdwa1B2ZlpudEV1a2NGSVpTNUE9PSIsInZhbHVlIjoiSVNUZndCUVAvRzFEclZCRDkwY0x4WlVmL0hXRnE5cm1qZGY3K3B2bkRBNjNrb3BXRGhPVGNSRlJpUVlzSmpZa1ZaNEdHa1ZiR2QraDhwZjNrZHBtbExPMVEyTTRjOHZCM05KMVZTN2ZDWG4rM29pUGN1U2NMb1VEaU5URlZSRVQiLCJtYWMiOiI5ZDI4ZjFiMjVmYjJkZTI2NWIwMTg3NDI4MTllOGRjYTZiYmZmMGFhZGM0Y2QwMDViNjM1ZTZjMDQ4YTQ4YjVkIiwidGFnIjoiIn0%3D; pbx_production_session=eyJpdiI6ImtrS3JRVlBXRnBMU25QclE2cDh6dVE9PSIsInZhbHVlIjoiTmlTTGdPckczeEhlMU5ZdG5RRDh1Q0J4Nk1SZjVjcjQ3SElMYzJJbTBYVlRIcmt6K2dTcDlqdTB4QTlGL00rbVU0SW9aTnE3Zm9LSXByRzlBZWpvTUtZci9NaGpoQnhTYmU3dmw3WXpTaWpMcW81dlQ3QUNzY0tMZmxZai9zdlEiLCJtYWMiOiIwNTg2ZGRjYzQ4NWM5YzEwZGVkNjhiMzdhODRmNTM5MDVjMzg4NDA5MmVhMjM0YzI1YWVkYzUzMTA1YThlMmJkIiwidGFnIjoiIn0%3D;',
        'referer': 'https://my.binotel.ua/f/bookon/',
        'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36',
        'x-requested-with': 'XMLHttpRequest'
    }
    
    try:
        # Додано таймаут 5 секунд, щоб запит не висів вічно
        response = requests.get(url, headers=headers, timeout=5)
        print("=== ВІДПОВІДЬ ВІД BINOTEL ===")
        print(f"Статус: {response.status_code}")
        
        if response.status_code == 200:
            try:
                data = response.json()
            except ValueError:
                print("Помилка: Binotel повернув HTML замість JSON. Можливо, кукі застаріли.")
                return "База оновлюється. Запропонуй ранок (10:00 або 11:00)."

            if "freeTimes" in data and len(data["freeTimes"]) > 0:
                slots = []
                for slot in data["freeTimes"]:
                    time_only = slot['startTime'].split(' ')[1] 
                    slots.append(time_only)
                
                unique_slots = sorted(list(set(slots)))
                available_times = ", ".join(unique_slots)
                return f"Сьогодні ({today}) є такі вільні години: {available_times}."
            else:
                return f"На сьогодні ({today}) вільних вікон уже немає."
        else:
            return "Немає доступу до бази. Запропонуй 10:00 або 11:00."
            
    except Exception as e:
        print(f"Помилка CRM: {e}")
        return "Не вдалося отримати графік."

def send_message(recipient_id, text):
    """Відправка повідомлення в Instagram"""
    url = f"https://graph.facebook.com/v18.0/me/messages?access_token={FB_PAGE_ACCESS_TOKEN}"
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": text}
    }
    requests.post(url, json=payload)

def process_message(sender_id, user_text):
    """Фонова обробка повідомлення (CRM + OpenAI)"""
    # 1. ІНІЦІАЛІЗАЦІЯ ПАМ'ЯТІ ТА ЗАПИТ У CRM
    if sender_id not in user_sessions:
        crm_data = get_crm_slots()
        full_prompt = f"{SYSTEM_PROMPT}\n\nСИСТЕМНІ ДАНІ ПРО ВІЛЬНИЙ ЧАС:\n{crm_data}"
        user_sessions[sender_id] = [{"role": "system", "content": full_prompt}]
    
    # 2. ДОДАЄМО ПОВІДОМЛЕННЯ КЛІЄНТА В ІСТОРІЮ
    user_sessions[sender_id].append({"role": "user", "content": user_text})
    
    if len(user_sessions[sender_id]) > 11:
        user_sessions[sender_id] = [user_sessions[sender_id][0]] + user_sessions[sender_id][-10:]

    # 3. ЗАПИТ ДО OPENAI
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=user_sessions[sender_id],
            temperature=0.7
        )
        ai_reply = response.choices[0].message.content
        
        user_sessions[sender_id].append({"role": "assistant", "content": ai_reply})
        send_message(sender_id, ai_reply)
        print(f"AI відповідь відправлена: {ai_reply}")
        
    except Exception as e:
        print(f"Помилка OpenAI: {e}")

@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")
        if mode == "subscribe" and token == VERIFY_TOKEN:
            return challenge, 200
        return "Forbidden", 403

    if request.method == "POST":
        data = request.json
        print("Отримано дані від Meta!")
        
        if data.get("object") == "instagram":
            for entry in data.get("entry", []):
                for messaging_event in entry.get("messaging", []):
                    if "message" in messaging_event and "text" in messaging_event["message"]:
                        sender_id = messaging_event["sender"]["id"]
                        user_text = messaging_event["message"]["text"]
                        
                        # ЗАПУСКАЄМО В ФОНОВОМУ ПОТОЦІ
                        # Це дозволяє миттєво повернути "EVENT_RECEIVED" для Meta
                        thread = threading.Thread(target=process_message, args=(sender_id, user_text))
                        thread.start()
                        
        return "EVENT_RECEIVED", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
