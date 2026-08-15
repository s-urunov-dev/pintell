"""Language negotiation and the localised message catalogue.

Every message the API can put in front of a user exists here in Uzbek, English
and Russian, keyed by the same stable code the error envelope already carries::

    {"error": {"code": "not_found", "message": "...", "status": 404}}

Why a plain dict rather than gettext: the catalogue is keyed by *code*, which
lets it cover DRF's own built-in messages ("Not found.", "Invalid page.") as
well as ours. Neither Django nor DRF ships an Uzbek catalogue, so gettext alone
would leave the default language in English — and it would add a ``.mo``
compilation step to every build. Both frontends map the same codes locally, so
the message here is the fallback for a client that does not know a code, and
the single source of truth for any non-browser consumer.
"""

from __future__ import annotations

from typing import Any

UZBEK = "uz"
ENGLISH = "en"
RUSSIAN = "ru"

SUPPORTED_LANGUAGES: tuple[str, ...] = (UZBEK, ENGLISH, RUSSIAN)

#: Uzbek is the product default, so it is also the fallback.
DEFAULT_LANGUAGE = UZBEK


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------
# Keys are error codes (or ``field.<name>`` for serializer validation codes).
# ``{...}`` placeholders are filled with ``str.format``.
MESSAGES: dict[str, dict[str, str]] = {
    # -- generic HTTP failures ---------------------------------------------
    "invalid": {
        "uz": "So‘rov noto‘g‘ri deb rad etildi.",
        "en": "The request was rejected as invalid.",
        "ru": "Запрос отклонён как некорректный.",
    },
    "parse_error": {
        "uz": "So‘rov tanasini o‘qib bo‘lmadi.",
        "en": "The request body could not be read.",
        "ru": "Не удалось прочитать тело запроса.",
    },
    "not_authenticated": {
        "uz": "Davom etish uchun tizimga kiring.",
        "en": "Please sign in to continue.",
        "ru": "Войдите в систему, чтобы продолжить.",
    },
    "authentication_failed": {
        "uz": "Kirish ma’lumotlari noto‘g‘ri.",
        "en": "The supplied credentials were not accepted.",
        "ru": "Предоставленные учётные данные не приняты.",
    },
    "permission_denied": {
        "uz": "Bu amalni bajarishga ruxsatingiz yo‘q.",
        "en": "You do not have permission for this action.",
        "ru": "У вас нет прав на это действие.",
    },
    "not_found": {
        "uz": "So‘ralgan yozuv topilmadi.",
        "en": "The requested record was not found.",
        "ru": "Запрошенная запись не найдена.",
    },
    "method_not_allowed": {
        "uz": "Bu so‘rov usuli bu yerda qo‘llab-quvvatlanmaydi.",
        "en": "This request method is not supported here.",
        "ru": "Этот метод запроса здесь не поддерживается.",
    },
    "not_acceptable": {
        "uz": "So‘ralgan javob formati qo‘llab-quvvatlanmaydi.",
        "en": "The requested response format is not supported.",
        "ru": "Запрошенный формат ответа не поддерживается.",
    },
    "unsupported_media_type": {
        "uz": "So‘rovning media turi qo‘llab-quvvatlanmaydi.",
        "en": "The request media type is not supported.",
        "ru": "Тип содержимого запроса не поддерживается.",
    },
    "throttled": {
        "uz": "So‘rovlar juda ko‘p. Bir oz kutib, qayta urinib ko‘ring.",
        "en": "Too many requests. Please wait a moment and try again.",
        "ru": "Слишком много запросов. Подождите немного и повторите попытку.",
    },
    "service_unavailable": {
        "uz": "Bog‘liq xizmat ishlamayapti. Birozdan so‘ng qayta urinib ko‘ring.",
        "en": "A dependent service is unavailable. Try again shortly.",
        "ru": "Зависимый сервис недоступен. Повторите попытку чуть позже.",
    },
    "error": {
        "uz": "Serverda kutilmagan xatolik yuz berdi. Birozdan so‘ng urinib ko‘ring.",
        "en": "The server hit an unexpected error. Try again shortly.",
        "ru": "На сервере произошла непредвиденная ошибка. Повторите попытку позже.",
    },
    # -- console authentication --------------------------------------------
    "invalid_credentials": {
        "uz": "Foydalanuvchi nomi yoki parol noto‘g‘ri.",
        "en": "Incorrect username or password.",
        "ru": "Неверное имя пользователя или пароль.",
    },
    "not_staff": {
        "uz": "Bu hisobda operator paneliga kirish huquqi yo‘q.",
        "en": "This account does not have operator access.",
        "ru": "У этой учётной записи нет доступа к консоли оператора.",
    },
    "staff_required": {
        "uz": "Operator paneli faqat xodim hisoblari uchun.",
        "en": "Staff access is required for the operator console.",
        "ru": "Для консоли оператора требуется учётная запись сотрудника.",
    },
    "csrf_cookie_set": {
        "uz": "CSRF cookie o‘rnatildi.",
        "en": "CSRF cookie set.",
        "ru": "CSRF-cookie установлен.",
    },
    # -- console operations -------------------------------------------------
    "task_dispatch_failed": {
        "uz": "Vazifani navbatga qo‘yib bo‘lmadi — broker ishlayaptimi? ({detail})",
        "en": "Could not queue the job — is the broker reachable? ({detail})",
        "ru": "Не удалось поставить задачу в очередь — брокер доступен? ({detail})",
    },
    "unknown_partition": {
        "uz": "«{partition}» bo‘limi topilmadi.",
        "en": "Unknown partition {partition!r}.",
        "ru": "Неизвестный раздел «{partition}».",
    },
    # -- serializer field validation ---------------------------------------
    "field.required": {
        "uz": "Bu maydon to‘ldirilishi shart.",
        "en": "This field is required.",
        "ru": "Это поле обязательно.",
    },
    "field.null": {
        "uz": "Bu maydon bo‘sh (null) bo‘lishi mumkin emas.",
        "en": "This field may not be null.",
        "ru": "Это поле не может быть пустым (null).",
    },
    "field.blank": {
        "uz": "Bu maydon bo‘sh bo‘lishi mumkin emas.",
        "en": "This field may not be blank.",
        "ru": "Это поле не может быть пустым.",
    },
    "field.invalid": {
        "uz": "Qiymat noto‘g‘ri.",
        "en": "The value is not valid.",
        "ru": "Значение недопустимо.",
    },
    "field.invalid_choice": {
        "uz": "Ruxsat etilgan variantlardan birini tanlang.",
        "en": "Choose one of the permitted options.",
        "ru": "Выберите один из допустимых вариантов.",
    },
    "field.min_value": {
        "uz": "Qiymat ruxsat etilgan eng kichik qiymatdan kichik.",
        "en": "The value is below the permitted minimum.",
        "ru": "Значение меньше допустимого минимума.",
    },
    "field.max_value": {
        "uz": "Qiymat ruxsat etilgan eng katta qiymatdan katta.",
        "en": "The value is above the permitted maximum.",
        "ru": "Значение превышает допустимый максимум.",
    },
    "field.min_length": {
        "uz": "Qiymat juda qisqa.",
        "en": "The value is too short.",
        "ru": "Значение слишком короткое.",
    },
    "field.max_length": {
        "uz": "Qiymat juda uzun.",
        "en": "The value is too long.",
        "ru": "Значение слишком длинное.",
    },
}


def normalize_language(value: str | None) -> str | None:
    """``"ru-RU"`` → ``"ru"``; ``None`` when the tag is not one of ours."""
    if not value:
        return None
    base = value.strip().lower().split("-")[0]
    return base if base in SUPPORTED_LANGUAGES else None


def parse_accept_language(header: str) -> str | None:
    """First supported language in an ``Accept-Language`` header, by q-value."""
    candidates: list[tuple[float, int, str]] = []
    for index, part in enumerate(header.split(",")):
        piece, _, params = part.strip().partition(";")
        language = normalize_language(piece)
        if not language:
            continue
        quality = 1.0
        if params.strip().startswith("q="):
            try:
                quality = float(params.strip()[2:])
            except ValueError:
                quality = 0.0
        # `index` keeps the header's own order as the tie-break.
        candidates.append((-quality, index, language))
    if not candidates:
        return None
    return min(candidates)[2]


def resolve_language(request: Any) -> str:
    """Pick the response language for ``request``.

    ``?lang=`` wins so a link can pin a language, then ``Accept-Language``,
    then Uzbek.
    """
    if request is None:
        return DEFAULT_LANGUAGE

    query = getattr(request, "GET", None)
    if query is not None:
        explicit = normalize_language(query.get("lang"))
        if explicit:
            return explicit

    header = ""
    meta = getattr(request, "META", None)
    if isinstance(meta, dict):
        header = meta.get("HTTP_ACCEPT_LANGUAGE", "") or ""
    return parse_accept_language(header) or DEFAULT_LANGUAGE


def translate(code: str, language: str = DEFAULT_LANGUAGE, **params: object) -> str | None:
    """The message for ``code``, or ``None`` when the code is not catalogued."""
    entry = MESSAGES.get(code)
    if entry is None:
        return None
    template = entry.get(language) or entry.get(DEFAULT_LANGUAGE) or entry.get(ENGLISH)
    if template is None:
        return None
    if not params:
        return template
    try:
        return template.format(**params)
    except (KeyError, IndexError, ValueError):
        # A malformed placeholder must never turn an error into a 500.
        return template
