import os
from fastapi import FastAPI, Request, Query
from fastapi.responses import PlainTextResponse
import uvicorn
from openai import OpenAI

app = FastAPI()
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

SYSTEM_PROMPT = """
Ти — адміністратор інстаграм-директу салону краси "Rozmary" у Львові.
Твоє завдання: людяно консультувати клієнтів та допомагати їм записатися 
на послуги.

ПРАВИЛА СПІЛКУВАННЯ:
1. Пиши коротко, без зайвого офіціозу. Використовуй емодзі, але помірно 
(1-2 на повідомлення).
2. Мова: за замовчуванням українська. Якщо пишуть англійською - відповідай 
англійською.
3. ПРІОРИТЕТ: Завжди намагайся закрити ранок (10:00 - 12:00).

ВІЛЬНІ СЛОТИ:
{slots}
"""

@app.get("/")
def home():
    return {"status": "AI is online"}

@app.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(alias="hub.mode"),
    hub_verify_token: str = Query(alias="hub.verify_token"),
    hub_challenge: str = Query(alias="hub.challenge")
):
    if hub_verify_token == "rozmary2026":
        return PlainTextResponse(hub_challenge)
    return PlainTextResponse("Forbidden", status_code=403)

@app.post("/webhook")
async def handle_messages(request: Request):
    data = await request.json()
    user_message = data.get("message_text", "")
    free_slots = data.get("slots", "потрібно уточнити в адміністратора")

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": 
SYSTEM_PROMPT.format(slots=free_slots)},
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
