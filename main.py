import os
import re
import json
import time
import sqlite3
import logging
import threading
import tempfile

try:
    import fcntl
except ImportError:
    fcntl = None

import requests
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import unquote
from flask import Flask, request
from dotenv import load_dotenv
from openai import OpenAI
from bocrm_playwright import BOCRMManualAdapter
from config import BRANDS
from states import BotState, can_transition

# ======================================================
# UNIVERSAL INSTAGRAM BOOKING BOT
# Multi-client / multi-brand / multi-CRM architecture
#
# Supported CRM modes:
# - bookon: Binotel Bookon / BOCRM widget
# - google_calendar: placeholder adapter for future implementation
# - easyweek: placeholder adapter for future implementation
# - manual: no CRM, sends request to admin Telegram
#
# Main idea:
# One codebase. New client = new config block in BRANDS.
# ======================================================

# ======================================================
# 1. APP INIT
# ======================================================
load_dotenv()
app = Flask(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)

try:
    LOCAL_TZ = ZoneInfo(os.getenv("LOCAL_TZ", "Europe/Kyiv"))
except Exception:
    LOCAL_TZ = ZoneInfo("Europe/Kiev")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "verify_token")
GLOBAL_TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GLOBAL_ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
PORT = int(os.getenv("PORT", "5000"))
DB_PATH = os.getenv("DB_PATH", "conversations.db")

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY is missing")

client = OpenAI(api_key=OPENAI_API_KEY)

# ======================================================
# 3. SERVICES, MASTERS, PRICES PER BRAND
# ======================================================
# For Bookon CRM, service IDs and master IDs must match Bookon.
# For Google Calendar or Manual, you can use human-readable service keys.

BRAND_MASTERS = {
    "rozmary": {
        "36644": "Юля",
        "36647": "Віра",
        "36997": "Богдана",
        "41492": "Тетяна",
        "41498": "Софія (молодший спеціаліст)",
    },
    "space": {
        # Fill when Space is ready.
        # "anna": "Анна",
        # "ira": "Іра",
    },
}

BRAND_SERVICES = {
    "rozmary": {
        "543048": {"name": "корекція та фарбування брів", "duration": 60, "requires_photo": False},
        "543042": {"name": "корекція брів", "duration": 30, "requires_photo": False},
        "543046": {"name": "довготривала укладка брів + корекція + фарбування", "duration": 90, "requires_photo": False},
        "543047": {"name": "довготривала укладка брів без фарбування", "duration": 60, "requires_photo": False},
        "543045": {"name": "ламінування вій", "duration": 90, "requires_photo": False},
        "543043": {"name": "ламінування вій + корекція і фарбування брів", "duration": 120, "requires_photo": False},
        "543044": {"name": "ламінування вій + довготривала укладка брів", "duration": 120, "requires_photo": False},
        "643390": {"name": "нарощення вій", "duration": 120, "requires_photo": False},
        "543063": {"name": "манікюр комплекс", "duration": 120, "requires_photo": True},
        "543033": {"name": "манікюр комплекс короткі нігті", "duration": 120, "requires_photo": True},
        "543059": {"name": "манікюр комплекс молодший спеціаліст", "duration": 150, "requires_photo": True},
        "543034": {"name": "манікюр комплекс короткі нігті молодший спеціаліст", "duration": 150, "requires_photo": True},
        "543061": {"name": "педикюр комплекс", "duration": 120, "requires_photo": True},
        "543054": {"name": "педикюр пальчики + покриття", "duration": 90, "requires_photo": True},
        "637624": {"name": "перманентний макіяж", "duration": 120, "requires_photo": False},
        "637623": {"name": "мікронідлінг", "duration": 60, "requires_photo": False},
        "543036": {"name": "макіяж", "duration": 90, "requires_photo": False},
        "543035": {"name": "зачіска", "duration": 90, "requires_photo": False},
    },
    "space": {
        # Example. Replace with real Space services.
        # "consultation": {"name": "консультація", "duration": 60, "requires_photo": False},
    },
}

BRAND_MASTER_SERVICES = {
    "rozmary": {
        "36644": [],
        "36647": [],
        "36997": [],
        "41492": [],
        "41498": ["543059", "543034"],
    },
    "space": {},
}

BRAND_PRICE_TEXT = {
    "rozmary": """
ПОСЛУГИ ТА ЦІНИ ROZMARY
Ціни вказані в гривні.
Формат 650 | 800 означає: молодший спеціаліст | майстер.

━━━ БРОВИ ━━━
• Корекція + фарбування фарба/хна: 550 грн
• Корекція брів: 300 грн
• Фарбування брів/вій: 300 грн
• Освітлення і тонування брів: 550 грн
• Довготривала укладка брів: 550 грн
• ДУ + корекція + фарбування: 750 грн
• Депіляція над губою: 200/100 грн
• Ламінування вій: 750 грн
• Ламінування вій + довготривала укладка брів: 1300 грн
  Корекція і фарбування входять.
• Ламінування вій + корекція і фарбування брів: 1100 грн

Зволоження ботоксом входить у вартість всіх послуг.

━━━ МАНІКЮР КОМПЛЕКС ━━━
Зняття входить у вартість.
• Чистка, покриття на короткі нігті під 0, без вільного краю: 650 | 800 грн
• Чистка, укріплення і покриття гелем: 700 | 850 грн
• Чистка, укріплення і покриття гель-лаком: 750 | 950 грн
• Чистка, укріплення і покриття френч на кольоровий гель/базу: 800 | 1000 грн
• Доглядова процедура “СПА для рук” одночасно з манікюром: +200 грн

Важливо: натуральні нігті від 3 довжини рахуються як корекція нарощених нігтів, тому що потребують повноцінного моделювання гелем.

━━━ МАНІКЮР ОКРЕМО ━━━
• Чистка без зняття, надання форми + шліфування: 550 грн
• Зняття, чистка, надання форми + шліфування: 600 грн
• Зняття + надання форми, шліфування: 250 грн
• Зняття, надання форми + покриття: 650 грн

━━━ ДИЗАЙН ━━━
• Простий дизайн: від 100 грн
• Складний дизайн: від 250 грн
• Френч/омбре: 150 грн
• Обʼємні фігурки: 25 грн/шт
Якщо клієнт хоче дизайн, попроси прислати фото бажаного дизайну в Direct, щоб ми розрахували вартість.

━━━ НАРОЩЕННЯ НІГТІВ, ДОВЖИНА 1–2 ━━━
Нарощення кольоровим гелем + чистка. Зняття гель-лаку враховано.
• Довжина 1–2: 850 | 950 грн
• Довжина 3–4: 1050 грн
• Довжина 5–6: 1150 грн
• Довжина 7–8: 1250 грн

Нарощення гелем + покриття гель-лак + чистка. Зняття гель-лаку враховано.
• Довжина 1–2: 900 | 1050 грн
• Довжина 3–4: 1150 грн
• Довжина 5–6: 1250 грн
• Довжина 7–8: 1350 грн

Додатково до нарощення:
• Дизайн френч / омбре / простий дизайн: +150 грн
• Зняття нарощених для перенарощення: +150 грн
• Зняття без подальшого нарощення: 300 грн
• Нарощення 1 нігтика: 50/100 грн

━━━ КОРЕКЦІЯ НАРОЩЕНИХ НІГТІВ ━━━
Корекція кольоровим гелем + чистка.
• Довжина 1–2: 700 | 850 грн
• Довжина 3–4: 950 грн
• Довжина 5–6: 1050 грн
• Довжина 7–8: 1150 грн

Корекція гелем + покриття гель-лак + чистка.
• Довжина 1–2: 750 | 950 грн
• Довжина 3–4: 1050 грн
• Довжина 5–6: 1150 грн
• Довжина 7–8: 1250 грн

━━━ ПЕДИКЮР КОМПЛЕКС ━━━
• Зняття, повна чистка стопи + пальчики, покриття однотон: 1000 грн
• Зняття, повна чистка стопи + пальчики, покриття френч або дизайн: 1100 грн

━━━ ПЕДИКЮР ОКРЕМО ━━━
• Зняття, чистка лише пальчики + покриття: 800 грн
• Зняття, повна чистка стопи + пальчики без покриття: 700 грн
• Зняття, чистка лише пальчики без покриття: 600 грн
• Зняття гель-лаку без подальшого покриття: 250 грн

Всі послуги у студії надаються стерильним інструментом, одноразовими пилочками, одноразовими розхідниками і якісними гіпоалергенними матеріалами.
""",
    "space": """
ПОСЛУГИ ТА ЦІНИ SPACE:
Заповніть цей блок під реальні послуги Space.
""",
}

# ======================================================
# 4. DATABASE
# ======================================================
def db_connect():
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    # Better SQLite behavior under threads/processes.
    # WAL reduces "database is locked" errors for concurrent reads/writes.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def table_columns(conn, table_name):
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row[1] for row in rows}


def add_column_if_missing(conn, table_name, column_name, column_def):
    cols = table_columns(conn, table_name)
    if column_name not in cols:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}")


def init_db():
    with db_connect() as conn:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT,
                brand TEXT,
                sender_id TEXT,
                role TEXT,
                content TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS processed_events (
                message_id TEXT PRIMARY KEY,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS user_state (
                conversation_id TEXT PRIMARY KEY,
                brand TEXT,
                sender_id TEXT,
                state TEXT DEFAULT 'START',
                nails_photo_received INTEGER DEFAULT 0,
                receipt_received INTEGER DEFAULT 0,
                active_appointment_id INTEGER,
                selected_service_id TEXT,
                selected_employee_id TEXT,
                selected_date TEXT,
                selected_time TEXT,
                client_name TEXT,
                client_phone TEXT,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS appointments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT,
                brand TEXT,
                sender_id TEXT,
                name TEXT,
                phone TEXT,
                service_id TEXT,
                service_name TEXT,
                appointment_date TEXT,
                appointment_time TEXT,
                employee_id TEXT,
                master_name TEXT,
                crm_visit_id TEXT,
                paid INTEGER DEFAULT 0,
                reminder_sent INTEGER DEFAULT 0,
                reinvite_sent INTEGER DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS crm_customers (
                brand TEXT,
                phone TEXT,
                customer_id TEXT,
                name TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (brand, phone)
            )
        """)

        # Migrations for older databases.
        for table, columns in {
            "messages": {"conversation_id": "TEXT", "brand": "TEXT"},
            "user_state": {"brand": "TEXT", "sender_id": "TEXT"},
            "appointments": {"conversation_id": "TEXT", "brand": "TEXT"},
        }.items():
            for col, col_def in columns.items():
                add_column_if_missing(conn, table, col, col_def)

        conn.commit()


init_db()

# ======================================================
# 5. HELPERS
# ======================================================
def now_local():
    return datetime.now(LOCAL_TZ)


def conversation_id(brand, sender_psid):
    return f"{brand}:{sender_psid}"


def raw_sender_id(conversation_id_or_sender):
    return str(conversation_id_or_sender).split(":", 1)[-1]


def normalize_phone(phone):
    digits = "".join(filter(str.isdigit, str(phone or "")))
    if digits.startswith("0") and len(digits) == 10:
        digits = "38" + digits
    elif len(digits) == 9:
        digits = "380" + digits
    return digits


def is_valid_ua_phone(phone_norm):
    return bool(re.fullmatch(r"380\d{9}", phone_norm or ""))


def is_duplicate_event(message_id):
    if not message_id:
        return False
    try:
        with db_connect() as conn:
            conn.execute("INSERT INTO processed_events (message_id) VALUES (?)", (message_id,))
            conn.commit()
        return False
    except sqlite3.IntegrityError:
        return True


def get_brand_by_page_id(page_id):
    page_id = str(page_id or "")
    for key, cfg in BRANDS.items():
        if cfg.get("enabled") and str(cfg.get("page_id") or "") == page_id:
            return key
    return None


def get_brand_cfg(brand):
    if brand not in BRANDS or not BRANDS[brand].get("enabled"):
        raise ValueError(f"Unknown or disabled brand: {brand}")
    return BRANDS[brand]


def brand_services(brand):
    return BRAND_SERVICES.get(brand, {})


def brand_masters(brand):
    return BRAND_MASTERS.get(brand, {})


def service_name(brand, service_id):
    svc = brand_services(brand).get(str(service_id), {})
    return svc.get("name", str(service_id))


def master_name(brand, employee_id):
    return brand_masters(brand).get(str(employee_id), str(employee_id))


def service_requires_photo(brand, service_id):
    svc = brand_services(brand).get(str(service_id), {})
    return bool(svc.get("requires_photo"))


def service_duration(brand, service_id):
    svc = brand_services(brand).get(str(service_id), {})
    return int(svc.get("duration", 60))

# ======================================================
# 6. STATE AND HISTORY
# ======================================================
def get_user_state(conv_id):
    with db_connect() as conn:
        row = conn.execute(
            """
            SELECT state, nails_photo_received, receipt_received, active_appointment_id,
                   selected_service_id, selected_employee_id, selected_date, selected_time,
                   client_name, client_phone, updated_at
            FROM user_state WHERE conversation_id=?
            """,
            (conv_id,),
        ).fetchone()

    if not row:
        return {
            "state": "START",
            "nails": False,
            "receipt": False,
            "active_appointment_id": None,
            "selected_service_id": None,
            "selected_employee_id": None,
            "selected_date": None,
            "selected_time": None,
            "client_name": None,
            "client_phone": None,
            "updated_at": None,
        }

    state = {
        "state": row[0] or "START",
        "nails": bool(row[1]),
        "receipt": bool(row[2]),
        "active_appointment_id": row[3],
        "selected_service_id": row[4],
        "selected_employee_id": row[5],
        "selected_date": row[6],
        "selected_time": row[7],
        "client_name": row[8],
        "client_phone": row[9],
        "updated_at": row[10],
    }

    try:
        if state["updated_at"]:
            updated = datetime.fromisoformat(str(state["updated_at"]))
            if datetime.now() - updated > timedelta(hours=48):
                reset_user_state(conv_id)
                return get_user_state(conv_id)
    except Exception:
        pass

    return state


def update_user_state(brand, sender_psid, **kwargs):
    conv_id = conversation_id(brand, sender_psid)
    current = get_user_state(conv_id)

    data = {
        "state": kwargs.get("state", current["state"]),
        "nails": kwargs.get("nails", current["nails"]),
        "receipt": kwargs.get("receipt", current["receipt"]),
        "active_appointment_id": kwargs.get("active_appointment_id", current["active_appointment_id"]),
        "selected_service_id": kwargs.get("selected_service_id", current["selected_service_id"]),
        "selected_employee_id": kwargs.get("selected_employee_id", current["selected_employee_id"]),
        "selected_date": kwargs.get("selected_date", current["selected_date"]),
        "selected_time": kwargs.get("selected_time", current["selected_time"]),
        "client_name": kwargs.get("client_name", current["client_name"]),
        "client_phone": kwargs.get("client_phone", current["client_phone"]),
    }

    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO user_state (
                conversation_id, brand, sender_id, state, nails_photo_received,
                receipt_received, active_appointment_id, selected_service_id,
                selected_employee_id, selected_date, selected_time,
                client_name, client_phone, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(conversation_id) DO UPDATE SET
                brand=excluded.brand,
                sender_id=excluded.sender_id,
                state=excluded.state,
                nails_photo_received=excluded.nails_photo_received,
                receipt_received=excluded.receipt_received,
                active_appointment_id=excluded.active_appointment_id,
                selected_service_id=excluded.selected_service_id,
                selected_employee_id=excluded.selected_employee_id,
                selected_date=excluded.selected_date,
                selected_time=excluded.selected_time,
                client_name=excluded.client_name,
                client_phone=excluded.client_phone,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                conv_id, brand, sender_psid, data["state"], int(bool(data["nails"])),
                int(bool(data["receipt"])), data["active_appointment_id"],
                data["selected_service_id"], data["selected_employee_id"],
                data["selected_date"], data["selected_time"], data["client_name"],
                data["client_phone"],
            ),
        )
        conn.commit()


def reset_user_state(conv_id):
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO user_state (conversation_id, state, nails_photo_received, receipt_received, updated_at)
            VALUES (?, 'START', 0, 0, CURRENT_TIMESTAMP)
            ON CONFLICT(conversation_id) DO UPDATE SET
                state='START', nails_photo_received=0, receipt_received=0,
                active_appointment_id=NULL, selected_service_id=NULL,
                selected_employee_id=NULL, selected_date=NULL, selected_time=NULL,
                client_name=NULL, client_phone=NULL, updated_at=CURRENT_TIMESTAMP
            """,
            (conv_id,),
        )
        conn.commit()


def save_message(brand, sender_psid, role, content):
    conv_id = conversation_id(brand, sender_psid)
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO messages (conversation_id, brand, sender_id, role, content)
            VALUES (?, ?, ?, ?, ?)
            """,
            (conv_id, brand, sender_psid, role, str(content)[:8000]),
        )
        conn.commit()


def get_history(brand, sender_psid, limit=14):
    conv_id = conversation_id(brand, sender_psid)
    with db_connect() as conn:
        rows = conn.execute(
            """
            SELECT role, content FROM messages
            WHERE conversation_id=? AND role IN ('user','assistant')
            ORDER BY id DESC LIMIT ?
            """,
            (conv_id, limit),
        ).fetchall()
    history = [{"role": r, "content": c} for r, c in reversed(rows)]
    # Видаляємо згадки майстрів з історії щоб GPT не тягнув старих майстрів
    masters = brand_masters(brand)
    for msg in history:
        if msg["role"] == "assistant":
            for mid, mname in masters.items():
                msg["content"] = msg["content"].replace(mname, "[майстер]")
    return history

# ======================================================
# 7. TELEGRAM AND INSTAGRAM
# ======================================================
def send_telegram(brand, text, photo_url=None):
    token = GLOBAL_TELEGRAM_BOT_TOKEN
    chat_id = get_brand_cfg(brand).get("telegram_chat_id") or GLOBAL_ADMIN_CHAT_ID
    if not token or not chat_id:
        logging.warning("Telegram not configured for brand=%s", brand)
        return

    try:
        if photo_url:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendPhoto",
                json={"chat_id": chat_id, "photo": photo_url, "caption": str(text)[:1000]},
                timeout=10,
            )
        else:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": str(text)[:4000]},
                timeout=10,
            )
    except Exception as e:
        logging.error("Telegram error: %s", e)


def send_instagram_message(brand, sender_psid, text):
    if not text:
        return
    cfg = get_brand_cfg(brand)
    access_token = cfg.get("page_access_token")
    if not access_token:
        logging.error("Missing page_access_token for brand=%s", brand)
        return

    try:
        r = requests.post(
            f"https://graph.facebook.com/v21.0/me/messages?access_token={access_token}",
            json={"recipient": {"id": sender_psid}, "message": {"text": str(text)[:2000]}},
            timeout=10,
        )
        if r.status_code != 200:
            logging.error("Instagram send error %s: %s", r.status_code, r.text[:1000])
    except Exception as e:
        logging.error("Instagram send exception: %s", e)

# ======================================================
# 8. CRM ADAPTERS
# ======================================================
class CRMResult:
    def __init__(self, ok, message, crm_id=None):
        self.ok = ok
        self.message = message
        self.crm_id = crm_id


class BaseCRMAdapter:
    def __init__(self, brand):
        self.brand = brand
        self.cfg = get_brand_cfg(brand)

    def get_available_slots(self, service_id, date_str):
        raise NotImplementedError

    def create_visit(self, sender_psid, name, phone, date_str, time_str, service_id, employee_id):
        raise NotImplementedError


class ManualCRMAdapter(BaseCRMAdapter):
    def get_available_slots(self, service_id, date_str):
        # Manual mode cannot know real availability. Bot should collect request and notify admin.
        return (
            "У цьому салоні запис підтверджує адміністратор. "
            "Попроси клієнта написати бажаний час, ім'я та телефон, і скажи, що адміністратор підтвердить запис."
        )

    def create_visit(self, sender_psid, name, phone, date_str, time_str, service_id, employee_id):
        svc_name = service_name(self.brand, service_id)
        m_name = master_name(self.brand, employee_id) if employee_id else "не обрано"
        phone_norm = normalize_phone(phone)

        with db_connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO appointments (
                    conversation_id, brand, sender_id, name, phone, service_id, service_name,
                    appointment_date, appointment_time, employee_id, master_name, crm_visit_id, paid
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    conversation_id(self.brand, sender_psid), self.brand, sender_psid,
                    name, phone_norm, service_id, svc_name, date_str, time_str,
                    employee_id, m_name, None,
                ),
            )
            appt_id = cur.lastrowid
            conn.commit()

        update_user_state(
            self.brand, sender_psid,
            state="WAITING_ADMIN_CONFIRMATION",
            active_appointment_id=appt_id,
            selected_service_id=service_id,
            selected_employee_id=employee_id,
            selected_date=date_str,
            selected_time=time_str,
            client_name=name,
            client_phone=phone_norm,
        )

        send_telegram(
            self.brand,
            f"📝 НОВА ЗАЯВКА MANUAL\n"
            f"Салон: {self.cfg['name']}\n"
            f"Клієнт: {name} ({phone_norm})\n"
            f"Бажаний час: {date_str} {time_str}\n"
            f"Послуга: {svc_name}\n"
            f"Майстер: {m_name}\n"
            f"Потрібно підтвердити вручну."
        )
        return CRMResult(True, "MANUAL_SUCCESS", crm_id=None)


# Cache for Bookon slots to reduce CRM HTTP requests.
_slots_cache = {}  # {"brand:service:date": (result_text, timestamp)}
_SLOTS_TTL = 300


class BookonCRMAdapter(BaseCRMAdapter):
    def __init__(self, brand):
        super().__init__(brand)
        c = self.cfg["crm_config"]
        self.widget_id = c.get("widget_id")
        self.branch_id = c.get("branch_id")
        self.bookon_session = c.get("bookon_session")
        self.base_bookon = f"https://widgets.binotel.com/b/bocrm/web-widget/{self.widget_id}"

    def make_session(self):
        s = requests.Session()
        s.headers.update({
            "x-binotel-bookon-widget-token": self.bookon_session,
            "Referer": "https://bookon.ua/",
            "Origin": "https://bookon.ua",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/plain, */*",
        })
        s.cookies.set(
            "bocrm_widget_session",
            self.bookon_session,
            domain="widgets.binotel.com",
            path=f"/b/bocrm/web-widget/{self.widget_id}/",
        )
        return s

    def init_xsrf(self, s):
        try:
            r = s.get(f"{self.base_bookon}/get-branches-list", timeout=10)
            xsrf = s.cookies.get("XSRF-TOKEN")
            if xsrf:
                s.headers["X-XSRF-TOKEN"] = unquote(xsrf)
            return r.status_code == 200
        except Exception as e:
            logging.error("Bookon XSRF init error: %s", e)
            return False

    @staticmethod
    def safe_json(resp):
        try:
            return resp.json()
        except Exception:
            return {}

    @staticmethod
    def extract_customer_id(data):
        if not isinstance(data, dict):
            return None
        return (
            data.get("id") or data.get("customerId") or data.get("customer_id")
            or (data.get("customer") or {}).get("id")
            or (data.get("data") or {}).get("id")
            or (data.get("data") or {}).get("customerId")
        )

    @staticmethod
    def extract_visit_id(data):
        if not isinstance(data, dict):
            return None
        return (
            data.get("id") or data.get("visitId") or data.get("visit_id")
            or (data.get("visit") or {}).get("id")
            or (data.get("data") or {}).get("id")
            or (data.get("data") or {}).get("visitId")
        )

    @staticmethod
    def is_crm_success(resp, data):
        if resp.status_code not in (200, 201):
            return False
        if isinstance(data, dict):
            if data.get("success") is False:
                return False
            if data.get("error") or data.get("errors"):
                return False
            if str(data.get("status", "")).lower() in {"error", "failed", "fail"}:
                return False
        return True

    def get_or_create_customer_id(self, phone_norm, name):
        if not is_valid_ua_phone(phone_norm):
            return None

        with db_connect() as conn:
            row = conn.execute(
                "SELECT customer_id FROM crm_customers WHERE brand=? AND phone=?",
                (self.brand, phone_norm),
            ).fetchone()
            if row and row[0]:
                return row[0]

        s = self.make_session()
        self.init_xsrf(s)

        try:
            r = s.post(
                f"{self.base_bookon}/get-customer-by-phone",
                data={"phone": phone_norm, "branchId": self.branch_id},
                timeout=10,
            )
            logging.info("Bookon customer search: %s | %s", r.status_code, r.text[:1000])
            if r.status_code == 200:
                data = self.safe_json(r)
                cid = self.extract_customer_id(data)
                if cid:
                    with db_connect() as conn:
                        conn.execute(
                            "INSERT OR REPLACE INTO crm_customers (brand, phone, customer_id, name) VALUES (?, ?, ?, ?)",
                            (self.brand, phone_norm, str(cid), name),
                        )
                        conn.commit()
                    return str(cid)
        except Exception as e:
            logging.error("Bookon customer search error: %s", e)

        try:
            r = s.post(
                f"{self.base_bookon}/create-customer",
                data={"branchId": self.branch_id, "name": name, "phone": phone_norm},
                timeout=10,
            )
            logging.info("Bookon customer create: %s | %s", r.status_code, r.text[:1000])
            if r.status_code in (200, 201):
                data = self.safe_json(r)
                cid = self.extract_customer_id(data)
                if cid:
                    with db_connect() as conn:
                        conn.execute(
                            "INSERT OR REPLACE INTO crm_customers (brand, phone, customer_id, name) VALUES (?, ?, ?, ?)",
                            (self.brand, phone_norm, str(cid), name),
                        )
                        conn.commit()
                    return str(cid)
        except Exception as e:
            logging.error("Bookon customer create error: %s", e)

        return None

    def get_available_slots(self, service_id, date_str):
        service_id = str(service_id or "")
        services = brand_services(self.brand)
        masters = brand_masters(self.brand)

        logging.info("Bookon get_available_slots called: brand=%s service_id=%s date=%s", self.brand, service_id, date_str)
        logging.info("Known services: %s", list(services.keys()))
        if service_id not in services:
            logging.error("Unknown service_id: %s", service_id)
            return "Помилка: невідомий service_id. Використай тільки ID зі списку послуг."

        try:
            datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            logging.error("Bad date format: %s", date_str)
            return "Помилка: дата має бути у форматі YYYY-MM-DD."

        cache_key = f"{self.brand}:{service_id}:{date_str}"
        cached = _slots_cache.get(cache_key)
        if cached and time.time() - cached[1] < _SLOTS_TTL:
            logging.info("Using cached Bookon slots: %s", cache_key)
            return cached[0]
        logging.info("No cache, fetching from CRM: %s", cache_key)

        s = self.make_session()
        logging.info("Bookon slots: widget_id=%s branch_id=%s session=%s", self.widget_id, self.branch_id, bool(self.bookon_session))
        xsrf_ok = self.init_xsrf(s)
        logging.info("Bookon XSRF init result: %s", xsrf_ok)

        try:
            res = s.get(
                f"{self.base_bookon}/get-available-work-times",
                params={
                    "branchId": self.branch_id,
                    "visitDate": date_str,
                    "serviceIds[0]": service_id,
                },
                timeout=10,
            )
            logging.info("Bookon slots response: %s | %s", res.status_code, res.text[:1000])
            if res.status_code != 200:
                return "Помилка отримання слотів з CRM. Запропонуй адміністратору перевірити вручну."

            data = self.safe_json(res)
            if not isinstance(data, dict):
                if isinstance(data, list) and len(data) == 0:
                    data = {}  # порожній список = немає слотів, продовжуємо
                else:
                    return "CRM повернула некоректний формат слотів."

            lines = []
            for spec_id, dates in data.items():
                master = masters.get(str(spec_id), f"Майстер {spec_id}")
                if not isinstance(dates, dict):
                    continue
                for d_str, blocks in dates.items():
                    if not isinstance(blocks, list):
                        continue
                    for b in blocks:
                        try:
                            start_raw = str(b.get("startTime"))
                            stop_raw = str(b.get("stopTime"))
                            start = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
                            stop = datetime.fromisoformat(stop_raw.replace("Z", "+00:00"))
                            if start.tzinfo:
                                start = start.astimezone(LOCAL_TZ)
                            if stop.tzinfo:
                                stop = stop.astimezone(LOCAL_TZ)
                            lines.append(f"{master} ({spec_id}) | {d_str} | {start.strftime('%H:%M')}–{stop.strftime('%H:%M')}")
                        except Exception:
                            continue

            def slot_sort_key(line):
                m = re.search(r"\|\s*(\d{2}:\d{2})", line)
                if not m:
                    return (99, line)
                t = m.group(1)
                hour = int(t.split(":")[0])
                morning_priority = 0 if 10 <= hour < 12 else 1
                return (morning_priority, t, line)

            lines = sorted(lines, key=slot_sort_key)
            if lines:
                result = "\n".join(lines[:30])
            else:
                # Шукаємо наступну доступну дату до 7 днів вперед
                from datetime import datetime as _dt, timedelta as _td
                result = None
                base_date = _dt.strptime(date_str, "%Y-%m-%d")
                for delta in range(1, 31):
                    next_date = (base_date + _td(days=delta)).strftime("%Y-%m-%d")
                    try:
                        r2 = s.get(
                            f"{self.base_bookon}/get-available-work-times",
                            params={"branchId": self.branch_id, "visitDate": next_date, "serviceIds[0]": service_id},
                            timeout=10,
                        )
                        if r2.status_code != 200:
                            continue
                        d2 = self.safe_json(r2)
                        if not isinstance(d2, dict):
                            continue
                        next_lines = []
                        for spec_id2, dates2 in d2.items():
                            master2 = masters.get(str(spec_id2), f"Майстер {spec_id2}")
                            if not isinstance(dates2, dict):
                                continue
                            for d_str2, blocks2 in dates2.items():
                                if not isinstance(blocks2, list):
                                    continue
                                for b2 in blocks2:
                                    try:
                                        start2 = datetime.fromisoformat(str(b2.get("startTime")).replace("Z", "+00:00")).astimezone(LOCAL_TZ)
                                        stop2 = datetime.fromisoformat(str(b2.get("stopTime")).replace("Z", "+00:00")).astimezone(LOCAL_TZ)
                                        next_lines.append(f"{master2} ({spec_id2}) | {d_str2} | {start2.strftime('%H:%M')}–{stop2.strftime('%H:%M')}")
                                    except Exception:
                                        continue
                        if next_lines:
                            next_lines = sorted(next_lines, key=slot_sort_key)
                            result = f"На {date_str} місць немає. Найближча доступна дата:\n" + "\n".join(next_lines[:15])
                            _slots_cache[f"{self.brand}:{service_id}:{next_date}"] = (result, time.time())
                            break
                    except Exception:
                        continue
                if not result:
                    result = "На цю дату вільних місць немає і найближчі 7 днів зайняті."
            _slots_cache[cache_key] = (result, time.time())
            return result
        except Exception as e:
            import traceback
            logging.error("Bookon slots error: %s\n%s", e, traceback.format_exc())
            return "Системна помилка CRM при отриманні слотів."

    def check_slot_still_available(self, service_id, employee_id, date_str, time_str):
        text = self.get_available_slots(service_id, date_str)
        if "Помилка" in text or "немає" in text:
            return False
        try:
            check_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
            duration = service_duration(self.brand, service_id)
            check_end = check_dt + timedelta(minutes=duration)
        except ValueError:
            return False
        for line in text.splitlines():
            if f"({employee_id})" not in line:
                continue
            m = re.search(r"(\d{2}:\d{2})–(\d{2}:\d{2})", line)
            if not m:
                continue
            try:
                slot_start = datetime.strptime(f"{date_str} {m.group(1)}", "%Y-%m-%d %H:%M")
                slot_end = datetime.strptime(f"{date_str} {m.group(2)}", "%Y-%m-%d %H:%M")
                if slot_start <= check_dt and check_end <= slot_end:
                    return True
            except ValueError:
                continue
        return False

    def create_visit(self, sender_psid, name, phone, date_str, time_str, service_id, employee_id):
        phone_norm = normalize_phone(phone)
        services = brand_services(self.brand)
        masters = brand_masters(self.brand)
        state = get_user_state(conversation_id(self.brand, sender_psid))

        service_id = str(service_id or "")
        employee_id = str(employee_id or "")

        if service_id not in services:
            return CRMResult(False, "Помилка: невідомий ID послуги.")
        if employee_id not in masters:
            return CRMResult(False, "Помилка: невідомий ID майстра.")
        if service_requires_photo(self.brand, service_id) and not state["nails"]:
            return CRMResult(False, "Помилка: для цієї послуги потрібно фото нігтів перед записом.")

        allowed_services = BRAND_MASTER_SERVICES.get(self.brand, {}).get(employee_id, [])
        if allowed_services and service_id not in allowed_services:
            return CRMResult(False, "Помилка: цей майстер не виконує обрану послугу.")

        if not is_valid_ua_phone(phone_norm):
            return CRMResult(False, "Помилка: невірний формат телефону. Попроси 380XXXXXXXXX або 0XXXXXXXXX.")

        try:
            datetime.strptime(date_str, "%Y-%m-%d")
            datetime.strptime(time_str, "%H:%M")
        except ValueError:
            return CRMResult(False, "Помилка: дата або час у неправильному форматі.")

        if not self.check_slot_still_available(service_id, employee_id, date_str, time_str):
            return CRMResult(False, "Помилка: цей час вже може бути зайнятий або недоступний.")

        # Спочатку пробуємо знайти/створити customer_id
        # Але НЕ блокуємо запис якщо не вдалось — Bookon сам створить клієнта по телефону
        # Використовуємо Playwright BOCRM API замість віджету
        adapter = BOCRMManualAdapter(
            email=os.getenv("BOCRM_EMAIL"),
            password=os.getenv("BOCRM_PASSWORD"),
            branch_id=self.branch_id
        )
        
        logging.info(f"Creating visit via Playwright: {name}, {phone_norm}, {date_str} {time_str}")
        result = adapter.create_visit_sync(
            specialist_id=employee_id,
            service_id=service_id,
            date_str=date_str,
            time_str=time_str,
            client_name=name,
            client_phone=phone_norm
        )

        if not result["ok"]:
            logging.error(f"Playwright error: {result['message']}")
            send_telegram(self.brand, f"⚠️ Ошибка CRM: {result['message']}")
            return CRMResult(False, "Помилка при записі. Адміністратор отримав повідомлення.")

        visit_id = result.get("crm_id")

        with db_connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO appointments (
                    conversation_id, brand, sender_id, name, phone, service_id, service_name,
                    appointment_date, appointment_time, employee_id, master_name, crm_visit_id, paid
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    conversation_id(self.brand, sender_psid), self.brand, sender_psid,
                    name, phone_norm, service_id, service_name(self.brand, service_id),
                    date_str, time_str, employee_id, master_name(self.brand, employee_id),
                    str(visit_id),
                ),
            )
            appointment_id = cur.lastrowid
            conn.commit()

        update_user_state(
            self.brand, sender_psid,
            state="WAITING_PAYMENT",
            active_appointment_id=appointment_id,
            client_name=name,
            client_phone=phone_norm,
        )

        send_telegram(
            self.brand,
            f"✅ НОВИЙ ЗАПИС (Playwright)\nКлієнт: {name} ({phone_norm})\n"
            f"Послуга: {service_name(self.brand, service_id)}\n"
            f"Дата: {date_str} о {time_str}\n"
            f"Майстер: {master_name(self.brand, employee_id)}\n"
            f"CRM ID: {visit_id}"
        )

        return CRMResult(True, "SUCCESS", crm_id=str(visit_id))


class GoogleCalendarCRMAdapter(BaseCRMAdapter):
    def get_available_slots(self, service_id, date_str):
        # Placeholder for future implementation via Google Calendar API.
        return (
            "Google Calendar adapter ще не підключений. "
            "Попроси клієнта написати бажаний час, ім'я та телефон. "
            "Адміністратор підтвердить запис вручну."
        )

    def create_visit(self, sender_psid, name, phone, date_str, time_str, service_id, employee_id):
        return ManualCRMAdapter(self.brand).create_visit(sender_psid, name, phone, date_str, time_str, service_id, employee_id)


class EasyWeekCRMAdapter(BaseCRMAdapter):
    def get_available_slots(self, service_id, date_str):
        # Placeholder. EasyWeek API credentials/endpoints should be added after access is received.
        return (
            "EasyWeek adapter ще не підключений. "
            "Попроси клієнта написати бажаний час, ім'я та телефон. "
            "Адміністратор підтвердить запис вручну."
        )

    def create_visit(self, sender_psid, name, phone, date_str, time_str, service_id, employee_id):
        return ManualCRMAdapter(self.brand).create_visit(sender_psid, name, phone, date_str, time_str, service_id, employee_id)


def get_crm_adapter(brand):
    crm_type = get_brand_cfg(brand).get("crm_type", "manual")
    if crm_type == "bookon":
        return BookonCRMAdapter(brand)
    if crm_type == "google_calendar":
        return GoogleCalendarCRMAdapter(brand)
    if crm_type == "easyweek":
        return EasyWeekCRMAdapter(brand)
    if crm_type == "manual":
        return ManualCRMAdapter(brand)
    raise ValueError(f"Unknown CRM type: {crm_type}")

# ======================================================
# 9. GPT TOOLS
# ======================================================
GPT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_available_slots",
            "description": "Отримати вільні слоти з CRM для обраної послуги на певну дату.",
            "parameters": {
                "type": "object",
                "properties": {
                    "service_id": {"type": "string"},
                    "date_str": {"type": "string", "description": "Дата у форматі YYYY-MM-DD"},
                },
                "required": ["service_id", "date_str"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_visit",
            "description": "Створити запис клієнта після збору послуги, дати, часу, майстра, імені та телефону.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "phone": {"type": "string"},
                    "date_str": {"type": "string"},
                    "time_str": {"type": "string"},
                    "service_id": {"type": "string"},
                    "employee_id": {"type": "string"},
                },
                "required": ["name", "phone", "date_str", "time_str", "service_id", "employee_id"],
            },
        },
    },
]


def services_prompt(brand):
    services = brand_services(brand)
    if not services:
        return "Послуги ще не заповнені. Якщо клієнт хоче запис, запропонуй найближчий вільний час."
    return "\n".join([f"{sid} — {data.get('name')}" for sid, data in services.items()])


def masters_prompt(brand):
    masters = brand_masters(brand)
    if not masters:
        return "Майстри ще не заповнені. Якщо клієнт хоче запис, запропонуй найближчий вільний час."
    return "\n".join([f"{mid} — {name}" for mid, name in masters.items()])


def build_system_prompt(brand, state):
    cfg = get_brand_cfg(brand)
    now = now_local()
    receipt_allowed = state["receipt"] is True
    prepay_amount = cfg.get("prepayment_amount", 0)

    address_text = f"{cfg.get('address')} 📍"
    if cfg.get("phone"):
        address_text += f" 📱{cfg.get('phone')}"
    if cfg.get("wifi"):
        address_text += f" Wi-Fi: {cfg.get('wifi')} пароль: {cfg.get('wifi_password', '')}"

    if receipt_allowed:
        address_rule = (
            f"Чек отримано. ОДРАЗУ напиши клієнту ДОСЛІВНО:\n"
            f"Чекаємо вас! 🌸\n"
            f"Адреса: {cfg.get('address')} 📍\n"
            f"Телефон: {cfg.get('phone')} 📱\n"
            f"Wi-Fi: {cfg.get('wifi')} | Пароль: {cfg.get('wifi_password')}\n"
            f"До зустрічі! 💅"
        )
    else:
        address_rule = (
            f"Чек ще НЕ отримано. КАТЕГОРИЧНО заборонено писати адресу, телефон салону або Wi-Fi. "
            f"Після отримання чеку одразу надай адресу."
        )

    prepay_rule = ""
    if cfg.get("prepayment_required"):
        prepay_rule = (
            f"Після успішного запису ОДРАЗУ в тому ж повідомленні напиши:\n"
            f"1) Підтвердження запису (ім\'я, послуга, дата, час, майстер)\n"
            f"2) Текст передплати ДОСЛІВНО:\n"
            f"Записую вас 🌷 внесіть будь ласка передплату {prepay_amount} грн як гарантію, що ви прийдете ❤️ "
            f"ця сума повертається при скасуванні запису завчасно або відмінусовується при розрахунку.\n"
            f"Передплата не повертається в разі скасування/переносу менше ніж за добу або якщо ви не прийшли на запис.\n"
            f"Не забудьте скинути квитанцію з печаткою, вона підтверджує ваш запис 🫶🏼\n"
            f"3) Номер картки: {cfg.get('card_number')} ({cfg.get('card_name')})"
        )
    else:
        prepay_rule = "Передплата не потрібна, після запису одразу підтверди деталі клієнту."

    crm_rule = ""
    if cfg.get("crm_type") != "bookon":
        crm_rule = "\n12. CRM у ручному режимі. Збери заявку та скажи, що адміністратор підтвердить."

    return f"""Ти — привітна адміністраторка салону {cfg['name']} у місті {cfg.get('city', '')}.
Відповідай коротко, природно, українською, з легкими емодзі.
Сьогодні: {now.strftime('%d.%m.%Y')}.

СТАТУС КЛІЄНТА:
- Стан: {state['state']}
- Фото нігтів: {'отримано' if state['nails'] else 'ще не отримано'}
- Чек передплати: {'отримано' if state['receipt'] else 'ще не отримано'}
- Активний запис ID: {state['active_appointment_id']}

ЖОРСТКІ ПРАВИЛА (ADMIN-SALES MODE):
1. Твоя ціль — продати запис.
   КАТЕГОРИЧНО ЗАБОРОНЕНО писати "не можу перевірити" або "передам адміністратору" —
   ЦЕ ПОМИЛКА. Ти ЗАВЖДИ маєш реальні слоти з CRM у повідомленні від інструменту.
   - Після get_available_slots: запропонуй ОДИН конкретний час з 10:00–12:00.
     Якщо таких немає — перший вільний з результату. Жди підтвердження клієнта.
   - Після згоди клієнта — збери імя + телефон якщо не маєш, потім create_visit.
   - НЕ ПЕРЕДАВАЙ АДМІНУ поки не запропонував хоча б 2 варіанти і клієнт відмовився.
2. Якщо клієнт не обрав майстра — бери першого вільного.
3. Перед create_visit: збери всі 6 даних. Як тільки дані є — НЕГАЙНО ВИКЛИКАЙ create_visit().
   ВАЖЛИВО: якщо клієнт каже просто "манікюр" без уточнення — ОБОВ'ЯЗКОВО запитай:
   "Який саме манікюр вас цікавить?
   💅 Манікюр комплекс (гель-лак, укріплення) — від 800 грн
   💅 Манікюр комплекс короткі нігті — від 650 грн
   💅 Манікюр молодший спеціаліст — від 700 грн"
   Тільки після відповіді клієнта обирай service_id.
4. Після запису: обов'язково нагадуй про передплату.
5. Ретеншн: якщо клієнт вже був у нас (дивись історію), через 21 день привітно нагадуй: "Маріє, пройшло 3 тижні, ваші нігтики вже сумують за оновленням. Давайте запишемось?"

ID ПОСЛУГ:
{services_prompt(brand)}

ID МАЙСТРІВ:
{masters_prompt(brand)}

ПРАЙС:
{BRAND_PRICE_TEXT.get(brand, '')}
"""

def block_address_if_not_paid(brand, bot_reply, state):
    if state.get("receipt"):
        return bot_reply

    cfg = get_brand_cfg(brand)
    if not cfg.get("block_address_if_not_paid", True):
        return bot_reply
    protected_words = list(cfg.get("protected_words", [])) + [
        cfg.get("address", ""), cfg.get("phone", ""), cfg.get("wifi", ""), cfg.get("wifi_password", ""),
    ]

    cleaned = bot_reply
    blocked = False
    for word in protected_words:
        if not word:
            continue
        pattern = re.escape(word)
        new_cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
        if new_cleaned != cleaned:
            blocked = True
        cleaned = new_cleaned

    if blocked:
        cleaned = cleaned.strip()
        if cleaned:
            cleaned += "\n\nПісля фото чеку одразу надішлю адресу ❤️"
        else:
            cleaned = "Після фото чеку одразу надішлю адресу ❤️"
    return cleaned

# ======================================================
# 10. BOT LOGIC
# ======================================================
def process_bot_logic(brand, sender_psid, text_content):
    save_message(brand, sender_psid, "user", text_content)
    state = get_user_state(conversation_id(brand, sender_psid))
    history = get_history(brand, sender_psid)
    messages = [{"role": "system", "content": build_system_prompt(brand, state)}] + history

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            tools=GPT_TOOLS,
            tool_choice="auto",
            temperature=0.5,
            max_tokens=700,
        )
        resp_msg = response.choices[0].message

        logging.info(f"GPT tool_calls: {resp_msg.tool_calls}")
        if resp_msg.tool_calls:
            messages.append(resp_msg)
            crm = get_crm_adapter(brand)

            for call in resp_msg.tool_calls:
                try:
                    args = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}

                if call.function.name == "get_available_slots":
                    logging.info("Calling crm.get_available_slots: brand=%s crm_type=%s service=%s date=%s", brand, type(crm).__name__, args.get("service_id"), args.get("date_str"))
                    raw_slots = crm.get_available_slots(args.get("service_id"), args.get("date_str"))
                    logging.info("raw_slots result: %s", raw_slots[:200] if raw_slots else "EMPTY")
                    morning_slots = [l for l in raw_slots.splitlines() if any(f"{h}:" in l for h in ["10:","11:"])]
                    other_slots = [l for l in raw_slots.splitlines() if l not in morning_slots and "|" in l]
                    # Витягуємо employee_id з реальних слотів
                    import re as _re
                    available_employees = list(dict.fromkeys(
                        m.group(1) for line in raw_slots.splitlines()
                        for m in [_re.search(r"\((\d+)\)", line)] if m
                    ))
                    employees_str = ", ".join(available_employees) if available_employees else "невідомо"

                    if morning_slots:
                        res = (
                            f"СЛОТИ З CRM:\n{raw_slots}\n\n"
                            f"ІНСТРУКЦІЯ: Запропонуй клієнту ТІЛЬКИ ці ранкові слоти (10:00-12:00): {'; '.join(morning_slots[:3])}. "
                            f"Скажи що це найкращий час. Запитай чи підходить. НЕ пропонуй інші поки не відмовиться. "
                            f"НЕ пиши що не можеш перевірити. НЕ передавай адміну. "
                            f"ВАЖЛИВО: employee_id для create_visit брати ТІЛЬКИ з цього списку: {employees_str}"
                        )
                    elif other_slots:
                        res = (
                            f"СЛОТИ З CRM:\n{raw_slots}\n\n"
                            f"ІНСТРУКЦІЯ: Ранкових слотів немає. Запропонуй найближчий вільний час: {other_slots[0]}. "
                            f"Скажи що це єдине зручне віконце. НЕ пиши що не можеш перевірити. НЕ передавай адміну. "
                            f"ВАЖЛИВО: employee_id для create_visit брати ТІЛЬКИ з цього списку: {employees_str}"
                        )
                    else:
                        res = (
                            f"{raw_slots}\n\n"
                            f"ІНСТРУКЦІЯ: Слотів немає. Запропонуй іншу дату або скажи що адміністратор підбере час."
                        )
                elif call.function.name == "create_visit":
                    result = crm.create_visit(
                        sender_psid=sender_psid,
                        name=args.get("name"),
                        phone=args.get("phone"),
                        date_str=args.get("date_str"),
                        time_str=args.get("time_str"),
                        service_id=args.get("service_id"),
                        employee_id=args.get("employee_id"),
                    )
                    if result.ok:
                        cfg = get_brand_cfg(brand)
                        if cfg.get("prepayment_required"):
                            res = f"SUCCESS: Запис створено. Попроси клієнта внести передплату {cfg.get('prepayment_amount')} грн на картку."
                        else:
                            res = "SUCCESS: Запис створено. Підтверди клієнту запис."
                    else:
                        res = result.message
                else:
                    res = "Unknown tool"

                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": call.function.name,
                    "content": str(res),
                })

            state = get_user_state(conversation_id(brand, sender_psid))
            messages[0] = {"role": "system", "content": build_system_prompt(brand, state)}

            final = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=messages,
                temperature=0.5,
                max_tokens=700,
            )
            bot_reply = final.choices[0].message.content or ""

            bad_phrases = [
                "не можу автоматично перевірити",
                "передам ваш запит адміністратору",
                "адміністратор зв'яжеться",
            ]
            if any(p in bot_reply.lower() for p in bad_phrases):
                logging.warning("GPT дав заглушку попри наявність слотів. Retry...")
                messages[0]["content"] += "\n\n⚠️ УВАГА: Ти щойно написав що не можеш перевірити — це ПОМИЛКА. У тебе є реальні слоти вище в tool result. Запропонуй конкретний час прямо зараз."
                retry = client.chat.completions.create(
                    model=OPENAI_MODEL,
                    messages=messages,
                    temperature=0.3,
                    max_tokens=400,
                )
                bot_reply = retry.choices[0].message.content or bot_reply
        else:
            bot_reply = resp_msg.content or ""

        state = get_user_state(conversation_id(brand, sender_psid))
        bot_reply = block_address_if_not_paid(brand, bot_reply, state)

        if bot_reply:
            save_message(brand, sender_psid, "assistant", bot_reply)
            send_instagram_message(brand, sender_psid, bot_reply)

        complaint_words = ["скарга", "жахливо", "погано", "незадоволена", "незадоволений", "зламали", "порізали"]
        if any(w in text_content.lower() for w in complaint_words):
            send_telegram(brand, f"⚠️ Можлива скарга від {sender_psid}:\n{text_content[:500]}")

    except Exception as e:
        logging.exception("GPT processing error: %s", e)
        send_instagram_message(brand, sender_psid, "Вибачте, технічна затримка 🙏 Адміністратор скоро відповість.")
        send_telegram(brand, f"⚠️ GPT помилка для {sender_psid}: {e}")

# ======================================================
# 11. PHOTO CLASSIFICATION AND DEBOUNCE BUFFER
# ======================================================
user_buffers = {}
user_timers = {}
buffer_lock = threading.Lock()


def classify_and_apply_photos(brand, sender_psid, full_text, img_urls):
    if not img_urls:
        return full_text

    state = get_user_state(conversation_id(brand, sender_psid))
    lower_text = full_text.lower()
    receipt_words = [
        "чек", "оплат", "передплат", "переказ", "скинула", "скинув",
        "відправила", "відправив", "заплатила", "заплатив", "оплатила", "оплатив",
    ]

    waiting_payment = state["state"] in {"WAITING_PAYMENT", "BOOKED_PENDING_PAYMENT"}
    has_active_appointment = bool(state.get("active_appointment_id"))
    has_receipt_word = any(w in lower_text for w in receipt_words)
    photo_without_text = not full_text.strip()
    is_receipt = waiting_payment and has_active_appointment and (has_receipt_word or photo_without_text)

    if is_receipt:
        update_user_state(brand, sender_psid, receipt=True, state="BOOKED_CONFIRMED")
        new_state = get_user_state(conversation_id(brand, sender_psid))
        active_id = new_state.get("active_appointment_id")
        if active_id:
            with db_connect() as conn:
                conn.execute("UPDATE appointments SET paid=1 WHERE id=?", (active_id,))
                conn.commit()
        send_telegram(brand, f"💳 Клієнт {sender_psid} надіслав ЧЕК:", photo_url=img_urls[0])
        return (full_text + " [Клієнт надіслав фото чеку]").strip()

    update_user_state(brand, sender_psid, nails=True)
    send_telegram(brand, f"💅 Клієнт {sender_psid} надіслав фото:", photo_url=img_urls[0])
    return (full_text + " [Клієнт надіслав фото нігтів/референсу]").strip()


def process_user_buffer(brand, sender_psid):
    key = conversation_id(brand, sender_psid)
    with buffer_lock:
        data = user_buffers.pop(key, [])
        user_timers.pop(key, None)

    if not data:
        return

    text_parts = [item.get("text", "") for item in data if item.get("text")]
    img_urls = [item.get("url") for item in data if item.get("url")]
    full_text = " ".join(text_parts).strip()
    full_text = classify_and_apply_photos(brand, sender_psid, full_text, img_urls)

    if full_text.strip():
        process_bot_logic(brand, sender_psid, full_text.strip())


def add_to_user_buffer(brand, sender_psid, text=None, img_url=None):
    key = conversation_id(brand, sender_psid)
    with buffer_lock:
        if key not in user_buffers:
            user_buffers[key] = []
        user_buffers[key].append({"text": text or "", "url": img_url})

        old_timer = user_timers.get(key)
        if old_timer:
            old_timer.cancel()

        timer = threading.Timer(3.0, process_user_buffer, args=[brand, sender_psid])
        user_timers[key] = timer
        timer.daemon = True
        timer.start()

# ======================================================
# 12. WEBHOOK
# ======================================================
@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":
        if request.args.get("hub.verify_token") == VERIFY_TOKEN:
            return request.args.get("hub.challenge"), 200
        return "Forbidden", 403

    data = request.json or {}

    for entry in data.get("entry", []):
        entry_page_id = str(entry.get("id") or "")

        for event in entry.get("messaging", []):
            msg = event.get("message", {})
            if msg.get("is_echo"):
                continue

            recipient_id = str((event.get("recipient") or {}).get("id") or entry_page_id)
            brand = get_brand_by_page_id(recipient_id) or get_brand_by_page_id(entry_page_id)

            if not brand:
                logging.warning("Unknown brand/page. entry_id=%s recipient_id=%s", entry_page_id, recipient_id)
                continue

            msg_id = f"{brand}:{msg.get('mid')}"
            if is_duplicate_event(msg_id):
                continue

            sender = event.get("sender", {})
            sender_psid = str(sender.get("id") or "")
            if not sender_psid:
                continue

            text = msg.get("text", "") or ""
            img_url = None
            for att in msg.get("attachments", []) or []:
                if att.get("type") == "image":
                    img_url = (att.get("payload") or {}).get("url")
                    break

            if text or img_url:
                add_to_user_buffer(brand, sender_psid, text=text, img_url=img_url)

    return "OK", 200


@app.route("/health", methods=["GET"])
def health():
    return {"status": "ok", "time": now_local().isoformat(), "brands": list(BRANDS.keys())}, 200

# ======================================================
# 13. DAILY TASKS
# ======================================================
def daily_tasks():
    while True:
        now = now_local()
        target = now.replace(hour=11, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)

        sleep_seconds = max(1, (target - now).total_seconds())
        logging.info("Daily tasks sleeping until %s", target.isoformat())
        time.sleep(sleep_seconds)

        try:
            tomorrow = (now_local() + timedelta(days=1)).strftime("%Y-%m-%d")
            with db_connect() as conn:
                c = conn.cursor()
                c.execute(
                    """
                    SELECT id, brand, sender_id, appointment_time, service_name, master_name
                    FROM appointments
                    WHERE appointment_date=? AND reminder_sent=0 AND paid=1
                    """,
                    (tomorrow,),
                )
                for appt_id, brand, sender_psid, appt_time, service, master in c.fetchall():
                    cfg = get_brand_cfg(brand)
                    msg = (
                        f"Нагадуємо про ваш запис завтра о {appt_time} 💅\n"
                        f"Послуга: {service}\nМайстер: {master}\n"
                        f"Адреса: {cfg.get('address')} 📍"
                    )
                    send_instagram_message(brand, sender_psid, msg)
                    c.execute("UPDATE appointments SET reminder_sent=1 WHERE id=?", (appt_id,))

                c.execute(
                    """
                    SELECT id, brand, sender_id, name, phone, appointment_time, service_name, master_name
                    FROM appointments
                    WHERE appointment_date=? AND reminder_sent=0 AND paid=0
                    """,
                    (tomorrow,),
                )
                for appt_id, brand, sender_psid, name, phone, appt_time, service, master in c.fetchall():
                    send_telegram(
                        brand,
                        f"⚠️ Завтра є запис без підтвердженої передплати:\n"
                        f"Клієнт: {name} ({phone})\nЧас: {appt_time}\nПослуга: {service}\nМайстер: {master}\n"
                        f"Перевірте вручну."
                    )
                    c.execute("UPDATE appointments SET reminder_sent=1 WHERE id=?", (appt_id,))


                # --- RETENTION (ЛТВ ДОЖИМ 21 ДЕНЬ) ---
                three_weeks_ago = (now_local() - timedelta(days=21)).strftime("%Y-%m-%d")
                c.execute("""
                    SELECT DISTINCT brand, sender_id, name 
                    FROM appointments 
                    WHERE appointment_date = ?
                """, (three_weeks_ago,))
                
                for brand, sender_psid, name in c.fetchall():
                    # Проверяем, нет ли у них будущих записей
                    c.execute("""
                        SELECT id FROM appointments 
                        WHERE sender_id = ? AND brand = ? AND appointment_date > date('now')
                    """, (sender_psid, brand))
                    if not c.fetchone():
                        msg = f"Привіт, {name}! 👋 Пройшло 3 тижні, ваші нігтики вже сумують за оновленням. Запрошуємо на запис! ✨"
                        send_instagram_message(brand, sender_psid, msg)
                        logging.info(f"Retention message sent to {sender_psid}")
                # ------------------------------------
                conn.commit()
        except Exception as e:
            logging.error("Daily tasks error: %s", e)

# ======================================================
# 14. START
# ======================================================
_scheduler_lock_handle = None


def start_daily_scheduler_once():
    """Start scheduler only once across gunicorn workers when fcntl is available."""
    global _scheduler_lock_handle

    if fcntl is None:
        # Fallback for non-Linux/local development.
        threading.Thread(target=daily_tasks, daemon=True).start()
        logging.info("Daily tasks scheduler started without process lock.")
        return

    lock_path = os.path.join(tempfile.gettempdir(), f"{os.path.basename(DB_PATH)}.daily_scheduler.lock")
    lock_file = open(lock_path, "w")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        logging.info("Daily tasks scheduler already started in another worker.")
        lock_file.close()
        return

    _scheduler_lock_handle = lock_file
    threading.Thread(target=daily_tasks, daemon=True).start()
    logging.info("Daily tasks scheduler started.")


start_daily_scheduler_once()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
