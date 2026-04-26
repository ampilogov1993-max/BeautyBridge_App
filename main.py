import hashlib
import json
import os
import sys
import threading
from datetime import datetime, timedelta

import requests
from flask import Flask, request
from openai import OpenAI


app = Flask(__name__)


def env(name, default=""):
    value = os.environ.get(name, default)
    return value.strip() if isinstance(value, str) else value


def log(message):
    print(message, flush=True)
    sys.stdout.flush()


FB_PAGE_ACCESS_TOKEN = env("FB_PAGE_ACCESS_TOKEN")
VERIFY_TOKEN = env("VERIFY_TOKEN", "rozmary2026")
OPENAI_API_KEY = env("OPENAI_API_KEY")
OPENAI_MODEL = env("OPENAI_MODEL", "gpt-4o")
BINOTEL_KEY = env("BINOTEL_API_KEY")
BINOTEL_SECRET = env("BINOTEL_API_SECRET")
TG_TOKEN = env("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = env("TELEGRAM_CHAT_ID")
PORT = int(env("PORT", "8080"))
BINOTEL_BRANCH_ID = int(env("BINOTEL_BRANCH_ID", "9970"))
SESSION_TTL_MINUTES = int(env("SESSION_TTL_MINUTES", "60"))


client = OpenAI(api_key=OPENAI_API_KEY)

user_sessions = {}
user_locks = {}
locks_guard = threading.Lock()


def get_user_lock(user_id):
    with locks_guard:
        lock = user_locks.get(user_id)
        if lock is None:
            lock = threading.Lock()
            user_locks[user_id] = lock
        return lock


def session_is_expired(session):
    updated_at = session.get("updated_at")
    if not updated_at:
        return True
    return datetime.now() - updated_at > timedelta(minutes=SESSION_TTL_MINUTES)


def trim_messages(messages, keep_last=20):
    if len(messages) <= keep_last + 1:
        return
    system_message = messages[0]
    tail = messages[-keep_last:]
    messages[:] = [system_message] + tail


def send_tg_notification(text):
    if not (TG_TOKEN and TG_CHAT_ID):
        return

    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT_ID, "text": f"BeautyBridge:\n{text}"}

    try:
        requests.post(url, json=payload, timeout=10)
    except requests.RequestException as exc:
        log(f"Telegram error: {exc}")


class BinotelAPI:
    def __init__(self, key, secret, branch_id):
        self.key = key
        self.secret = secret
        self.branch_id = branch_id
        self.base_url = "https://api.binotel.com/api/2.0"

    def _build_request_json(self, date_str):
        request_data = {
            "branchId": self.branch_id,
            "startDate": date_str,
        }
        return json.dumps(request_data, separators=(",", ":"), ensure_ascii=False)

    def _build_signature(self, request_json):
        raw_signature = f"{self.key}{request_json}{self.secret}"
        return hashlib.md5(raw_signature.encode("utf-8")).hexdigest()
