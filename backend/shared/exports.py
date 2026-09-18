import re


def export_stem(seller_name: str, *, ascii_only: bool = False) -> str:
    """Название кабинета для имени выгружаемого файла.

    Кабинеты называются по-русски, а `filename=` в HTTP-заголовке кириллицу не
    переносит: для него — только латиница и цифры, для `filename*` — любые
    буквы. Пустой остаток заменяется на `seller`, чтобы имя не начиналось с
    подчёркивания.
    """
    pattern = r"[^A-Za-z0-9-]+" if ascii_only else r"[^\w-]+"
    return re.sub(pattern, "_", seller_name, flags=re.UNICODE).strip("_") or "seller"
