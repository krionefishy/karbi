DEFAULT_CHAT_URL_TEMPLATE = "https://seller.wildberries.ru/chat-with-clients?chatId={chat_id}"


def chat_url(template: str, chat_id: str) -> str | None:
    """Ссылка по шаблону; пустой шаблон отключает ссылки."""
    if not template:
        return None
    identifier = chat_id.split(":", 1)[-1]
    return template.replace("{chat_id}", identifier)
