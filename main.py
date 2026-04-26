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
Ти — помічник адміністратора інстаграм-директу салону краси "Rozmary" у Львові. 
Твоя мета: привітно консультувати клієнтів і допомагати їм обрати вільний час та майстра.

ЖОРСТКІ ПРАВИЛА (КРИТИЧНО ВАЖЛИВО):
1. ТИ БАЧИШ РЕАЛЬНІ ІМЕНА МАЙСТРІВ У СИСТЕМНИХ ДАНИХ. Пропонуй час і називай ім'я майстра, яке вказано поруч із часом.
2. НІКОЛИ не вигадуй імена майстрів, яких немає в списку на сьогодні.
3. НІКОЛИ не кажи клієнту "Я вас записала" або "Запис підтверджено". У тебе немає прав створювати запис у CRM. 
4. ФІНАЛ ДІАЛОГУ: Коли клієнт обрав послугу, час і майстра, ти МАЄШ сказати: "Супер! Передаю заявку адміністратору. Вона зараз перевірить розклад і напише вам для остаточного підтвердження запису ⏳".
5. Не вітайся в кожному повідомленні. 
6. ПРІОРИТЕТ: Завжди спочатку пропонуй ранкові зміни (10:00-12:00), які є в списку вільних.

ІНФОРМАЦІЯ ПРО ЗАПИС:
Нижче ти отримаєш список реальних вільних місць та імена майстрів. Пропонуй ТІЛЬКИ ті години та тих майстрів, які є у списку.
"""

def get_crm_slots():
    """Функція для запиту в Binotel Bookon (Парсинг майстрів)"""
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
        response = requests.get(url, headers=headers, timeout=5)
        print("=== ВІДПОВІДЬ ВІД BINOTEL ===")
        print(f"Статус: {response.status_code}")
        
        if response.status_code == 200:
            try:
                data = response.json()
            except ValueError:
                return "База оновлюється. Запропонуй ранок (10:00 або 11:00)."

            # 1. ШУКАЄМО ІМЕНА МАЙСТРІВ
            masters = {}
            for key in ["specialists", "employees", "staff", "users", "workers", "resources"]:
                if key in data and isinstance(data[key], list):
                    for item in data[key]:
                        m_id = item.get("id")
                        m_name = item.get("name", item.get("firstName", "Спеціаліст"))
                        if m_id:
                            masters[m_id] = m_name
                    break
            
            # 2. ЗВ'ЯЗУЄМО ЧАС З МАЙСТРАМИ
            if "freeTimes" in data and len(data["freeTimes"]) > 0:
                slots_by_time = {}
                for slot in data["freeTimes"]:
                    time_only = slot['startTime'].split(' ')[1]
                    spec_id = slot.get('specialistId')
                    
                    master_name = masters.get(spec_id, "Майстер")
                    
                    if time_only not in slots_by_time:
                        slots_by_time[time_only] = []
                    if master_name not in slots_by_time[time_only]:
                        slots_by_time[time_only].append(master_name)
                
                # 3. ФОРМУЄМО ТЕКСТ ДЛЯ AI
                result_lines = []
                for t in sorted(slots_by_time.keys()):
                    masters_str = ", ".join(slots_by_time[t])
                    result_lines.append(f"- {t} (Майстри: {masters_str})")
                
                available_times = "\n".join(result_lines)
                print(f"Знайдено слоти: \n{available_times}")
                return f"Сьогодні ({today}) є такі вільні години та майстри:\n{available_times}"
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
    """Фонова обробка повідомлення"""
    if sender_id not in user_sessions:
        crm_data = get_crm_slots()
        full_prompt = f"{SYSTEM_PROMPT}\n\nСИСТЕМНІ ДАНІ ПРО ВІЛЬНИЙ ЧАС:\n{crm_data}"
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
        
        if data.get("object") == "instagram":
            for entry in data.get("entry", []):
                for messaging_event in entry.get("messaging", []):
                    if "message" in messaging_event and "text" in messaging_event["message"]:
                        sender_id = messaging_event["sender"]["id"]
                        user_text = messaging_event["message"]["text"]
                        
                        thread = threading.Thread(target=process_message, args=(sender_id, user_text))
                        thread.start()
                        
        return "EVENT_RECEIVED", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
