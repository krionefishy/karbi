"""Регионы подсорта и к какому из них относится заказ и склад WB.

Регионы — те, что в кабинете WB показывает отчёт «География заказов»: округа
склеены так же, как их склеивает WB (Юг с Северным Кавказом, Дальний Восток
с Сибирью), страны ЕАЭС идут отдельно. «Россия» — заказы, у которых WB не
назвал округ.
"""

CENTRAL = "Центральный"
FAR_EAST_SIBERIA = "Дальневосточный и Сибирский"
VOLGA = "Приволжский"
SOUTH_CAUCASUS = "Южный и Северо-Кавказский"
NORTH_WEST = "Северо-Западный"
URAL = "Уральский"
KAZAKHSTAN = "Казахстан"
ARMENIA = "Армения"
BELARUS = "Беларусь"
KYRGYZSTAN = "Кыргызстан"
GEORGIA = "Грузия"
UZBEKISTAN = "Узбекистан"
TAJIKISTAN = "Таджикистан"
RUSSIA_UNKNOWN = "Россия"

# Порядок столбцов в книге — как в листах «Подсорт <кабинет>» образца.
REGIONS: tuple[str, ...] = (
    CENTRAL,
    FAR_EAST_SIBERIA,
    VOLGA,
    SOUTH_CAUCASUS,
    NORTH_WEST,
    URAL,
    KAZAKHSTAN,
    ARMENIA,
    BELARUS,
    KYRGYZSTAN,
    GEORGIA,
    UZBEKISTAN,
    TAJIKISTAN,
    RUSSIA_UNKNOWN,
)
# Куда можно везти: у «Россия без округа» складов нет.
TARGET_REGIONS: tuple[str, ...] = tuple(region for region in REGIONS if region != RUSSIA_UNKNOWN)

_OKRUGS = {
    "центральный федеральный округ": CENTRAL,
    "южный федеральный округ": SOUTH_CAUCASUS,
    "северо-кавказский федеральный округ": SOUTH_CAUCASUS,
    "приволжский федеральный округ": VOLGA,
    "северо-западный федеральный округ": NORTH_WEST,
    "уральский федеральный округ": URAL,
    "сибирский федеральный округ": FAR_EAST_SIBERIA,
    "дальневосточный федеральный округ": FAR_EAST_SIBERIA,
}

# Строки отчёта об остатках, которые не склад: товар в дороге и итог. Склад
# «Склад WB РФ» — настоящий остаток, но WB не говорит, где он лежит, поэтому
# ни к одному региону его не относим.
SERVICE_WAREHOUSES = frozenset(
    {
        "в пути до получателей",
        "в пути возвраты на склад wb",
        "всего находится на складах",
    }
)
UNPLACED_WAREHOUSES = frozenset({"склад wb рф"})

# Склад WB → регион по городу в названии. Длинные ключи раньше коротких:
# «нижний новгород» не должен уйти в «новгород» Северо-Запада.
_WAREHOUSE_CITIES: tuple[tuple[str, str], ...] = (
    ("нижний новгород", VOLGA),
    ("великий новгород", NORTH_WEST),
    ("набережные челны", VOLGA),
    ("белые столбы", CENTRAL),
    ("белая дача", CENTRAL),
    ("новосемейкино", VOLGA),
    ("новосибирск", FAR_EAST_SIBERIA),
    ("новокузнецк", FAR_EAST_SIBERIA),
    ("новороссийск", SOUTH_CAUCASUS),
    ("невинномысск", SOUTH_CAUCASUS),
    ("екатеринбург", URAL),
    ("санкт-петербург", NORTH_WEST),
    ("южно-сахалинск", FAR_EAST_SIBERIA),
    ("благовещенск", FAR_EAST_SIBERIA),
    ("владивосток", FAR_EAST_SIBERIA),
    ("владикавказ", SOUTH_CAUCASUS),
    ("электросталь", CENTRAL),
    ("солнечногорск", CENTRAL),
    ("петрозаводск", NORTH_WEST),
    ("калининград", NORTH_WEST),
    ("архангельск", NORTH_WEST),
    ("симферополь", SOUTH_CAUCASUS),
    ("севастополь", SOUTH_CAUCASUS),
    ("магнитогорск", URAL),
    ("нижневартовск", URAL),
    ("ставрополь", SOUTH_CAUCASUS),
    ("махачкала", SOUTH_CAUCASUS),
    ("пятигорск", SOUTH_CAUCASUS),
    ("краснодар", SOUTH_CAUCASUS),
    ("красноярск", FAR_EAST_SIBERIA),
    ("хабаровск", FAR_EAST_SIBERIA),
    ("сыктывкар", NORTH_WEST),
    ("череповец", NORTH_WEST),
    ("волгоград", SOUTH_CAUCASUS),
    ("астрахань", SOUTH_CAUCASUS),
    ("ульяновск", VOLGA),
    ("чебоксары", VOLGA),
    ("йошкар-ола", VOLGA),
    ("оренбург", VOLGA),
    ("тольятти", VOLGA),
    ("челябинск", URAL),
    ("кемерово", FAR_EAST_SIBERIA),
    ("щербинка", CENTRAL),
    ("домодедово", CENTRAL),
    ("голицыно", CENTRAL),
    ("никольское", CENTRAL),
    ("коледино", CENTRAL),
    ("подольск", CENTRAL),
    ("пушкино", CENTRAL),
    ("радумля", CENTRAL),
    ("сабурово", CENTRAL),
    ("софьино", CENTRAL),
    ("обухово", CENTRAL),
    ("внуково", CENTRAL),
    ("иваново", CENTRAL),
    ("владимир", CENTRAL),
    ("ярославль", CENTRAL),
    ("воронеж", CENTRAL),
    ("белгород", CENTRAL),
    ("кострома", CENTRAL),
    ("коломна", CENTRAL),
    ("серпухов", CENTRAL),
    ("котовск", CENTRAL),
    ("рязань", CENTRAL),
    ("липецк", CENTRAL),
    ("брянск", CENTRAL),
    ("калуга", CENTRAL),
    ("смоленск", CENTRAL),
    ("тамбов", CENTRAL),
    ("москва", CENTRAL),
    ("чехов", CENTRAL),
    ("вешки", CENTRAL),
    ("истра", CENTRAL),
    ("лобня", CENTRAL),
    ("тверь", CENTRAL),
    ("курск", CENTRAL),
    ("тула", CENTRAL),
    ("орел", CENTRAL),
    ("клин", CENTRAL),
    ("казань", VOLGA),
    ("самара", VOLGA),
    ("сарапул", VOLGA),
    ("саратов", VOLGA),
    ("саранск", VOLGA),
    ("энгельс", VOLGA),
    ("ижевск", VOLGA),
    ("пенза", VOLGA),
    ("пермь", VOLGA),
    ("киров", VOLGA),
    ("уфа", VOLGA),
    ("ростов", SOUTH_CAUCASUS),
    ("грозный", SOUTH_CAUCASUS),
    ("нальчик", SOUTH_CAUCASUS),
    ("шахты", SOUTH_CAUCASUS),
    ("сочи", SOUTH_CAUCASUS),
    ("шушары", NORTH_WEST),
    ("уткина", NORTH_WEST),
    ("спб", NORTH_WEST),
    ("вологда", NORTH_WEST),
    ("мурманск", NORTH_WEST),
    ("псков", NORTH_WEST),
    ("тагил", URAL),
    ("тюмень", URAL),
    ("сургут", URAL),
    ("курган", URAL),
    ("барнаул", FAR_EAST_SIBERIA),
    ("иркутск", FAR_EAST_SIBERIA),
    ("улан-удэ", FAR_EAST_SIBERIA),
    ("абакан", FAR_EAST_SIBERIA),
    ("якутск", FAR_EAST_SIBERIA),
    ("томск", FAR_EAST_SIBERIA),
    ("омск", FAR_EAST_SIBERIA),
    ("чита", FAR_EAST_SIBERIA),
    ("атакент", KAZAKHSTAN),
    ("алматы", KAZAKHSTAN),
    ("астана", KAZAKHSTAN),
    ("актобе", KAZAKHSTAN),
    ("шымкент", KAZAKHSTAN),
    ("караганда", KAZAKHSTAN),
    ("костанай", KAZAKHSTAN),
    ("ереван", ARMENIA),
    ("минск", BELARUS),
    ("брест", BELARUS),
    ("гродно", BELARUS),
    ("гомель", BELARUS),
    ("могилев", BELARUS),
    ("витебск", BELARUS),
    ("бишкек", KYRGYZSTAN),
    ("тбилиси", GEORGIA),
    ("ташкент", UZBEKISTAN),
    ("душанбе", TAJIKISTAN),
)


def _normalize(text: str) -> str:
    return " ".join(text.lower().replace("ё", "е").split())


def order_region(country: str, okrug: str) -> str:
    """Регион заказа по стране и округу из статистики WB.

    Заграничный заказ — страна как есть: новые страны WB появляются сами, и
    выдумывать им место среди известных незачем.
    """
    country = country.strip()
    if country and _normalize(country) != "россия":
        return country
    return _OKRUGS.get(_normalize(okrug), RUSSIA_UNKNOWN)


def is_service_warehouse(name: str) -> bool:
    """Строка отчёта об остатках, которая не склад: в пути или итог."""
    return _normalize(name) in SERVICE_WAREHOUSES


def is_unplaced_warehouse(name: str) -> bool:
    """Остаток есть, но WB не говорит, в каком он регионе."""
    return _normalize(name) in UNPLACED_WAREHOUSES


def guess_warehouse_region(name: str) -> str | None:
    """Регион склада WB по городу в названии; None — не узнали, решает человек."""
    normalized = _normalize(name)
    if normalized in SERVICE_WAREHOUSES or normalized in UNPLACED_WAREHOUSES:
        return None
    for city, region in _WAREHOUSE_CITIES:
        if city in normalized:
            return region
    return None
