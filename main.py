import os
from fastapi import FastAPI, Request
import uvicorn
from openai import OpenAI

app = FastAPI()
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

SYSTEM_PROMPT = "Ти — адміністратор салону Rozmary. Відповідай людяно та коротко."

@app.get("/")
def home():
    return {"status": "AI is online"}

@app.post("/webhook")
async def handle_messages(request: Request):
    data = await request.json()
    user_message = data.get("message_text", "")
    user_name = data.get("user_name", "Клієнт")
    
    # Запит до OpenAI
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message}
        ]
    )
    ai_reply = response.choices[0].message.content
    return {"status": "success", "reply": ai_reply}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
