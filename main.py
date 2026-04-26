import os
import requests
from flask import Flask, request
from openai import OpenAI
from datetime import datetime

app = Flask(__name__)

# --- КЛЮЧІ З RAILWAY ---
FB_PAGE_ACCESS_TOKEN = os.environ.get("FB_PAGE_ACCESS_TOKEN")
VERIFY_TOKEN = "rozmary2026"
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
BOOKON_API_KEY = os.environ.get("BOOKON_API_KEY")
BOOKON_API_SECRET = os.environ.get("BOOKON_API_SECRET")

client = OpenAI(api_key=OPENAI_API_KEY)

# --- ПАМ'ЯТЬ ДІАЛОГІВ ---
# Тут зберігається історія листування для кожного клієнта
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
    """Тестова функція для запиту в Binotel Bookon"""
    today = datetime.now().strftime("%Y-%m-%d")
    url = f"https://my.binotel.ua/b/bocrm/calendar/day?branchId=9970&startDate={today}"
    
    try:
        # Пробуємо відправити GET запит
        response = requests.get(url)
        print("=== ВІДПОВІДЬ ВІД BINOTEL ===")
        print(f"Статус: {response.status_code}")
        # Виводимо перші 200 символів відповіді в логи Railway
        print(response.text[:200]) 
        
        if response.status_code == 200 and "freeTimes" in response.text:
            return f"Сьогодні ({today}) є такі вільні години: [Система ще налаштовується, запропонуй 10:00 або 11:00]"
        else:
            return f"Розклад на {today} тимчасово прихований. Запропонуй ранковий час (10:00-12:00), а потім скажи, що перевіриш у базі."
            
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
                        
                        # 1. ІНІЦІАЛІЗАЦІЯ ПАМ'ЯТІ
                        if sender_id not in user_sessions:
                            # Якщо клієнт пише вперше, додаємо системний промпт і дані з CRM
                            crm_data = get_crm_slots()
                            full_prompt = f"{SYSTEM_PROMPT}\n\nСИСТЕМНІ ДАНІ ПРО ВІЛЬНИЙ ЧАС:\n{crm_data}"
                            user_sessions[sender_id] = [{"role": "system", "content": full_prompt}]
                        
                        # 2. ДОДАЄМО ПОВІДОМЛЕННЯ КЛІЄНТА В ІСТОРІЮ
                        user_sessions[sender_id].append({"role": "user", "content": user_text})
                        
                        # Обрізаємо історію, щоб не переповнювати пам'ять OpenAI (залишаємо промпт + останні 10 повідомлень)
                        if len(user_sessions[sender_id]) > 11:
                            user_sessions[sender_id] = [user_sessions[sender_id][0]] + user_sessions[sender_id][-10:]

                        # 3. ЗАПИТ ДО OPENAI З УСІЄЮ ІСТОРІЄЮ
                        try:
                            response = client.chat.completions.create(
                                model="gpt-4o",
                                messages=user_sessions[sender_id],
                                temperature=0.7
                            )
                            ai_reply = response.choices[0].message.content
                            
                            # Зберігаємо відповідь бота в історію
                            user_sessions[sender_id].append({"role": "assistant", "content": ai_reply})
                            
                            # 4. ВІДПРАВКА В ІНСТАГРАМ
                            send_message(sender_id, ai_reply)
                            print(f"AI відповідь відправлена: {ai_reply}")
                            
                        except Exception as e:
                            print(f"Помилка OpenAI: {e}")
                            
        return "EVENT_RECEIVED", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
