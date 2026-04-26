import os
import httpx
from fastapi import FastAPI, Request, Query
from fastapi.responses import PlainTextResponse
import uvicorn
from openai import OpenAI

app = FastAPI()
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

# Токен для связи с Meta (должен совпадать с тем, что в кабинете FB)
VERIFY_TOKEN = "rozmary2026" 
# Тот самый "вечный" токен страницы из Railway
FB_PAGE_ACCESS_TOKEN = os.environ.get("FB_PAGE_ACCESS_TOKEN")

SYSTEM_PROMPT = """
Ти — адміністратор інстаграм-директу салону краси "Rozmary" у Львові. 
Твоя мета: людяно та привітно консультувати клієнтів і допомагати їм визначитися з часом візиту.

ПРАВИЛА СПІЛКУВАННЯ (КРИТИЧНО ВАЖЛИВО):
1. БЕЗ ПОСТІЙНИХ ПРИВІТАНЬ. Клієнт вже веде з тобою діалог. НІКОЛИ не пиши "Привіт", "Доброго дня" або "Вітаю" у своїх відповідях. Одразу переходь до суті.
2. Пиши коротко, як жива людина в месенджері. Відкинь зайвий офіціоз. Використовуй емодзі дуже помірно (1-2 на повідомлення).
3. Мова: за замовчуванням українська. Якщо пишуть англійською — відповідай англійською.
4. ПРІОРИТЕТ: Твоє головне завдання — заповнити ранкові зміни. Завжди першим ділом пропонуй час з 10:00 до 12:00.

ЯК ПРАЦЮВАТИ З ГРАФІКОМ (У ТЕБЕ НЕМАЄ БАЗИ):
Зараз ти не бачиш реального розкладу майстрів у CRM. Тому твій алгоритм такий:
- Запитай, яка послуга цікавить (якщо клієнт ще не сказав).
- Запитай бажаний день і одразу запропонуй ранковий час (10:00-12:00).
- Якщо клієнт просить конкретного майстра або час, скажи, що зрозумів його побажання.
- ФІНАЛ ДІАЛОГУ: Як тільки клієнт обрав послугу, день та орієнтовний час, НЕ ПІДТВЕРДЖУЙ запис остаточно. Напиши: "Супер! Зараз я перевірю розклад майстрів на цей час у системі та повернуся до вас за хвилинку для підтвердження. ⏳"
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
    if hub_verify_token == VERIFY_TOKEN:
        return PlainTextResponse(hub_challenge)
    return PlainTextResponse("Forbidden", status_code=403)

@app.post("/webhook")
async def handle_messages(request: Request):
    data = await request.json()
    
    try:
        entry = data.get("entry", [])
        for e in entry:
            messaging = e.get("messaging", [])
            for m in messaging:
                sender_id = m.get("sender", {}).get("id")
                message = m.get("message", {})
                
                # КРИТИЧНО: Захист від того, щоб бот не відповідав сам собі
                if message.get("is_echo"):
                    continue

                text = message.get("text", "")
                if text and sender_id:
                    # 1. Генеруємо відповідь через OpenAI
                    response = client.chat.completions.create(
                        model="gpt-4o",
                        messages=[
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": text}
                        ]
                    )
                    ai_reply = response.choices[0].message.content

                    # 2. ВІДПРАВЛЯЄМО ВІДПОВІДЬ В INSTAGRAM (Це те, чого не було!)
                    async with httpx.AsyncClient() as ac:
                        await ac.post(
                            f"https://graph.facebook.com/v25.0/me/messages?access_token={FB_PAGE_ACCESS_TOKEN}",
                            json={
                                "recipient": {"id": sender_id},
                                "message": {"text": ai_reply}
                            }
                        )
                    print(f"Відповідь надіслана клієнту {sender_id}")

    except Exception as e:
        print(f"Помилка: {e}")

    return {"status": "ok"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
