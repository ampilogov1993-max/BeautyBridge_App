import os
from fastapi import FastAPI, Request
import uvicorn

app = FastAPI()

@app.get("/")
def home():
    return {"message": "BeautyBridge_App запущен и готов к работе, бро!"}

@app.post("/webhook")
async def handle_messages(request: Request):
    data = await request.json()
    print(f"Отримано нове повідомлення: {data}")
    return {"status": "success", "received_data": data}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
