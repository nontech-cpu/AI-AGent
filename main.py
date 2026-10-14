import os
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Header, HTTPException

load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SECRET = os.getenv("WEBHOOK_SECRET")

app = FastAPI()

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str = Header(None),
):
    if x_telegram_bot_api_secret_token != SECRET:
        raise HTTPException(status_code=403)

    update = await request.json()
    message = update.get("message")

    if message and "text" in message:
        chat_id = message["chat"]["id"]
        text = message["text"]
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": "hello"},
            )
    return {"ok": True}