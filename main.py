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
    """Функція для запиту в Binotel Bookon (з найсвіжішими куками)"""
    today = datetime.now().strftime("%Y-%m-%d")
    url = f"https://my.binotel.ua/b/bocrm/calendar/day?branchId=9970&startDate={today}"
    
    # ТВОЇ НОВІ КУКИ ТА ХЕДЕРИ
    headers = {
        'accept': 'application/json, text/plain, */*',
        'cookie': 'remember_web_59ba36addc2b2f9401580f014c7f58ea4e30989d=eyJpdiI6IlcyVEFBQzRpVXl3TlVJTmlZZE5rbGc9PSIsInZhbHVlIjoiMXpBckRvbVRKODNIRXJQWGVYWjJ6NnhFdkkrTFBlZVFtT3NCQkNWZy9Pd0c5Y0lydjkvRGlRZ282SHRwYnVxWWtrMURob0JSUXpFOVlGZWpiQ0hPcWc9PSIsIm1hYyI6IjQyMmY1MjAwOTU5Mzc1OTBjMjZhYzU2YmQ1OGExYTFiMzJkMGFjNTg3NDZmODA1MjQ5YzI0NzI5MjQwZjg1YmMiLCJ0YWciOiIifQ%3D%3D; XSRF-TOKEN=eyJpdiI6IkY3WGplaEtaQ0lQeWNNbVJSOGZhbUE9PSIsInZhbHVlIjoiWGE5R1c2OE1lZFgyUnNWRlp3RUIrc0EzcGx0VS9CUHRCZ2trenQvdXI1MVhQODh2MW1OL2Zkdk1JNGVENlU3TnF6dXJFN2pldHUxMVcyU2c1SjlLSEV5eFFzQnIwa3llYUFIOUZMQ2RRTDlkMUVhdnZJc3hpNXIzTE9TMGFuNEkiLCJtYWMiOiI3M2E1NmI1NDQ0OTFjYzE4YmMwNjNjMGU2YjMzODg1ZTM2NzM2NGVmZWVkYjA3ZWU3ODRlZWNiMGMyOWRkMzIzIiwidGFnIjoiIn0%3D; bocrm_production_session=eyJpdiI6Ikw5VGpDcW9kRzVnTXFkYzAyK0gyMnc9PSIsInZhbHVlIjoiT67FcVVDcUV5b0tnZUhlaGgxU2lLZUdvYnlsN1ZmbDE5SWdKL3VvRGV0cnlET1VRVUJ3MkZaZVVjY2lNako1eFJUbVhlazN5UlJuZGxvUnVIL3A4Vzk3MDFzcVVKSHlRck96b2xvZ0xObGVMZ3RKcGZyK21Gd0dWNlZsUGJIa1giLCJtYWMiOiI1OTYxODBiZWZlYmUwN2MyOGM1N2M2NzUwNDhhMDFiZjk3NzhhM2NhYjBjYTY0Mzk5MzVkYzViZjgyNzYyMWNlIiwidGFnIjoiIn0%3D; _fbp=fb.1.1775386406793.63895127954870116; _ga=GA1.1.447468764.1775386470; PHPSESSID=mljg9ajmjtgbqofjidama1fr6i; _gfhdez=sR2xJBdT',
        'referer': 'https://my.binotel.ua/f/bookon/',
        'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36',
        'x-requested-with': 'XMLHttpRequest'
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=5)
        print(f"=== ВІДПОВІДЬ ВІД BINOTEL. СТАТУС: {response.status_code} ===")
        
        if response.status_code == 200:
            data = response.json()
            # Збираємо майстрів
            masters = {}
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
                print(f"Знайдено слоти: \n{available_times}")
                return f"На сьогодні ({today}) вільні: \n{available_times}"
            else:
                return "На сьогодні вільних місць немає."
        return "Доступ до бази тимчасово обмежений."
    except Exception as e:
        print(f"Помилка CRM: {e}")
        return "Не вдалося отримати графік."

def send_message(recipient_id, text):
    url = f"https://graph.facebook.com/v18.0/me/messages?access_token={FB_PAGE_ACCESS_TOKEN}"
    payload = {"recipient": {"id": recipient_id}, "message": {"text": text}}
    requests.post(url, json=payload)

def process_message(sender_id, user_text):
    if sender_id not in user_sessions:
        crm_data = get_crm_slots()
        full_prompt = f"{SYSTEM_PROMPT}\n\nАКТУАЛЬНИЙ РОЗКЛАД:\n{crm_data}"
        user_sessions[sender_id] = [{"role": "system", "content": full_prompt}]
    
    user_sessions[sender_id].append({"role": "user", "content": user_text})
    
    # Пам'ять на 10 повідомлень
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
