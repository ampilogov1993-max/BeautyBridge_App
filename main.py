        return (today + timedelta(days=1)).strftime("%Y-%m-%d")

    return today.strftime("%Y-%m-%d")


def build_system_prompt(target_date, crm_data):
    if crm_data == "ALL_BUSY":
        schedule_instruction = (
            f"На {target_date} вільних місць немає. "
            "Попроси клієнта обрати іншу дату або зачекати відповіді адміністратора."
        )
    elif crm_data == "NO_DATA":
        schedule_instruction = (
            "Зараз дані розкладу недоступні. "
            "Скажи, що адміністратор напише особисто найближчим часом. Не вигадуй вільний час."
        )
    else:
        schedule_instruction = f"Ось реальний розклад: {crm_data} Пропонуй тільки цей час."

    return (
        "Ти адміністратор салону Rozmary у Львові. "
        "Відповідай коротко, ввічливо і по суті. "
        "Не вигадуй вільні вікна, ціни чи послуги. "
        f"{schedule_instruction}"
    )


def ensure_session(user_id, user_text, target_date):
    session = user_sessions.get(user_id)
    needs_refresh = (
        session is None
        or session.get("target_date") != target_date
        or session_is_expired(session)
    )

    if not needs_refresh:
        return session

    send_tg_notification(f"Клієнт в Instagram: {user_text}")
    log("Тягну дані CRM...")
    crm_data = crm.get_free_slots(target_date)
    system_prompt = build_system_prompt(target_date, crm_data)

    session = {
        "target_date": target_date,
        "updated_at": datetime.now(),
        "messages": [{"role": "system", "content": system_prompt}],
    }
    user_sessions[user_id] = session
    return session


def process_message(sender_id, text):
    user_lock = get_user_lock(sender_id)

    with user_lock:
        target_date = resolve_target_date(text)
        session = ensure_session(sender_id, text, target_date)
        session["updated_at"] = datetime.now()
        session["messages"].append({"role": "user", "content": text})
        trim_messages(session["messages"])

        try:
            response = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=session["messages"],
                temperature=0.0,
            )
            reply = response.choices[0].message.content or (
                "Зараз не можу відповісти коректно. Адміністратор напише вам особисто."
            )
            session["messages"].append({"role": "assistant", "content": reply})
            session["updated_at"] = datetime.now()
            send_instagram_msg(sender_id, reply)
            log("Відповідь надіслана.")
        except Exception as exc:
            log(f"OpenAI error: {exc}")
            fallback_text = "Дякуємо за повідомлення. Адміністратор напише вам особисто найближчим часом."
            send_instagram_msg(sender_id, fallback_text)


@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":
        verify_token = request.args.get("hub.verify_token", "")
        challenge = request.args.get("hub.challenge", "")
        if verify_token == VERIFY_TOKEN:
            return challenge, 200
        return "Forbidden", 403

    data = request.get_json(silent=True) or {}
    log(f"Webhook event: {json.dumps(data, ensure_ascii=False)}")

    if data.get("object") != "instagram":
        return "IGNORED", 200

    for entry in data.get("entry", []):
        for event in entry.get("messaging", []):
            if event.get("message", {}).get("is_echo"):
                continue

            sender = event.get("sender", {})
            message = event.get("message", {})
            sender_id = sender.get("id")
            text = message.get("text")

            if not sender_id or not text:
                continue

            worker = threading.Thread(
                target=process_message,
                args=(sender_id, text),
                daemon=True,
            )
            worker.start()

    return "EVENT_RECEIVED", 200


def log_startup_warnings():
    missing = []
    for name, value in [
        ("FB_PAGE_ACCESS_TOKEN", FB_PAGE_ACCESS_TOKEN),
        ("OPENAI_API_KEY", OPENAI_API_KEY),
        ("BINOTEL_API_KEY", BINOTEL_KEY),
        ("BINOTEL_API_SECRET", BINOTEL_SECRET),
    ]:
        if not value:
            missing.append(name)

    if missing:
        log(f"Startup warning. Missing env vars: {', '.join(missing)}")


if __name__ == "__main__":
    log_startup_warnings()
    app.run(host="0.0.0.0", port=PORT)
