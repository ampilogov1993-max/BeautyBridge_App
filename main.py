import os
from fastapi import FastAPI, Request
import uvicorn
from openai import OpenAI

app = FastAPI()
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

# Твій головний "мозок" - промпт для нейронки
SYSTEM_PROMPT = """
Ти — адміністратор інстаграм-директу салону краси "Rozmary" у Львові. 
Твоє завдання: людяно консультувати клієнтів та допомагати їм записатися на послуги.

ПРАВИЛА СПІЛКУВАННЯ:
1. Пиши коротко, без зайвого офіціозу. Використовуй емодзі, але помірно (1-2 на повідомлення).
2. Мова: за замовчуванням українська. Якщо пишуть англійською - відповідай англійською.
3. ПРІОРИТЕТ: Завжди намагайся закрити ранок (10:00 - 12:00). Кажи: "У нас є чудове віконце на ранок, ідеально для процедури за кавою".

ІНФОРМАЦІЯ ПРО ЗАПИС:
- Вільний час береш з даних: {slots}
- Якщо клієнт вибрав час, кажи, що запис підтверджено і ми вже чекаємо в салоні.
- Якщо не знаєш відповіді, кажи: "Зачекайте хвилинку, адміністратор Ксенія зараз все перевірить і відпише вам".
"""

@app.get("/")
def home():
    return {"status": "BeautyBridge AI is online"}

@app.post("/webhook")
async def handle_messages(request: Request):
    data = await request.json()
    user_message = data.get("message_text", "")
    free_slots = data.get("slots", "потрібно уточнити в адміністратора")
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT.format(slots=free_slots)},
                {"role": "user", "content": user_message}
            ]
        )
        ai_reply = response.choices[0].message.content
        return {"status": "success", "reply": ai_reply}
    except Exception as e:
        return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
