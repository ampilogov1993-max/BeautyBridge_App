import os
from dotenv import load_dotenv

load_dotenv()

GLOBAL_TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GLOBAL_ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")

BRANDS = {
    "rozmary": {
        "enabled": True,
        "name": "Rozmary",
        "city": "Львів",
        "language": "uk",
        "page_id": os.getenv("ROZMARY_PAGE_ID", ""),
        "page_access_token": os.getenv("ROZMARY_PAGE_ACCESS_TOKEN", ""),
        "telegram_chat_id": os.getenv("ROZMARY_ADMIN_CHAT_ID", GLOBAL_ADMIN_CHAT_ID),
        "address": "Вул. Донцова 9, фасадний вхід",
        "phone": "0977646741",
        "wifi": "Internet-5G",
        "wifi_password": "internet1",
        "prepayment_required": True,
        "prepayment_amount": 200,
        "card_number": os.getenv("ROZMARY_CARD_NUMBER", ""),
        "card_name": os.getenv("ROZMARY_CARD_NAME", ""),
        "crm_type": "bookon",
        "crm_config": {
            "widget_id": os.getenv("ROZMARY_WIDGET_ID", ""),
            "branch_id": os.getenv("ROZMARY_BRANCH_ID", ""),
            "bookon_session": os.getenv("ROZMARY_BOOKON_SESSION", ""),
        },
        "block_address_if_not_paid": True,
        "protected_words": [
            "Донцова 9",
            "0977646741",
            "Internet-5G",
            "internet1",
        ],
    },
    "space": {
        "enabled": True,
        "name": "Space",
        "city": "Львів",
        "language": "uk",
        "page_id": os.getenv("SPACE_PAGE_ID", ""),
        "page_access_token": os.getenv("SPACE_PAGE_ACCESS_TOKEN", ""),
        "telegram_chat_id": os.getenv("SPACE_ADMIN_CHAT_ID", GLOBAL_ADMIN_CHAT_ID),
        "address": os.getenv("SPACE_ADDRESS", "Адреса Space"),
        "phone": os.getenv("SPACE_PHONE", ""),
        "wifi": os.getenv("SPACE_WIFI", ""),
        "wifi_password": os.getenv("SPACE_WIFI_PASSWORD", ""),
        "prepayment_required": True,
        "prepayment_amount": int(os.getenv("SPACE_PREPAYMENT_AMOUNT", "200")),
        "card_number": os.getenv("SPACE_CARD_NUMBER", ""),
        "card_name": os.getenv("SPACE_CARD_NAME", ""),
        "crm_type": "bookon",
        "crm_config": {
            "widget_id": os.getenv("SPACE_WIDGET_ID", os.getenv("ROZMARY_WIDGET_ID", "")),
            "branch_id": os.getenv("SPACE_BRANCH_ID", os.getenv("ROZMARY_BRANCH_ID", "")),
            "bookon_session": os.getenv("SPACE_BOOKON_SESSION", os.getenv("ROZMARY_BOOKON_SESSION", "")),
        },
        "block_address_if_not_paid": True,
        "protected_words": [],
    },
}
