"""Усі текстові рядки інтерфейсу українською."""
from __future__ import annotations

# --- Головне меню ---
WELCOME = (
    "👋 Вітаю! Це <b>TelegramHub</b> — панель керування авто-відповідями "
    "від вашого імені.\n\nОберіть дію на клавіатурі нижче."
)
BTN_API_KEYS = "🔑 API ключі ШІ"
BTN_CONNECT = "👤 Підключити акаунт"
BTN_PROMPT = "📝 Промпт"
BTN_STYLE = "🧠 Аналіз стилю"
BTN_AUTOREPLY = "⚙️ Налаштування авто-відповідей"
BTN_STATUS = "📊 Статус"
BTN_BACK = "⬅️ Повернутись"
BTN_CANCEL = "❌ Скасувати"

# --- Загальні ---
ERR_GENERIC = "⚠️ Сталася помилка. Спробуйте ще раз пізніше."
ERR_NOT_CONNECTED = "⚠️ Акаунт Telegram не підключено. Спочатку підключіть акаунт."
ERR_NO_ACTIVE_KEY = "⚠️ Немає активного API-ключа ШІ. Додайте ключ у розділі «API ключі ШІ»."
SAVED = "✅ Збережено."

# --- API ключі ---
API_KEYS_MENU = (
    "🔑 <b>API ключі ШІ</b>\n\n"
    "Додайте ключ провайдера (OpenAI-сумісний, Anthropic або Google Gemini). "
    "Ключі зберігаються локально у SQLite і ніколи не показуються повністю."
)
BTN_KEY_ADD = "➕ Додати ключ"
BTN_KEY_LIST = "📋 Список ключів"
CHOOSE_PROVIDER = "Оберіть провайдера:"
PROVIDER_OPENAI = "🟢 OpenAI-сумісний"
PROVIDER_ANTHROPIC = "🟠 Anthropic"
PROVIDER_GOOGLE = "🔵 Google Gemini"
ASK_BASE_URL = "Надішліть базовий URL (наприклад https://api.openai.com/v1):"
ASK_API_KEY = "Надішліть API-ключ:"
ASK_MODEL = "Надішліть назву моделі (наприклад gpt-4o-mini):"
KEY_ADDED = "✅ Ключ #{key_id} додано ({provider}). Активний: ні."
KEY_LIST_EMPTY = "Ключів ще немає."
KEY_ACTIVATE_DONE = "✅ Ключ #{key_id} тепер активний."
KEY_DELETED = "🗑 Ключ #{key_id} видалено."
KEY_ALREADY_ACTIVE = "Цей ключ уже активний."
KEY_LABEL_FMT = "#{key_id} · {provider} · {model} · …{tail}{active_mark}"

# --- Підключення акаунта ---
CONNECT_MENU = (
    "👤 <b>Підключення акаунта</b>\n\nСтатус: {status}\n\n"
    "Процес: api_id → api_hash → телефон → код → (пароль 2FA, за потреби)."
)
STATUS_CONNECTED = "✅ підключено ({phone})"
STATUS_NOT_CONNECTED = "❌ не підключено"
BTN_CONNECT_START = "🔌 Підключити"
ASK_API_ID = "Надішліть api_id (число з my.telegram.org):"
ASK_API_HASH = "Надішліть api_hash:"
ASK_PHONE = "Надішліть номер телефону у міжнародному форматі (+380...):"
CODE_SENT = "📨 Код надіслано на {phone}. Надішліть код із Telegram (або з SMS):"
ASK_PASSWORD = "🔒 Увімкнено 2FA. Надішліть хмарний пароль (або «/skip» — пропустити, якщо 2FA вимкнено):"
CONNECT_SUCCESS = "✅ Акаунт підключено! Сесію збережено в sessions/{user_id}.session"
CONNECT_FAILED = "⚠️ Не вдалося підключити акаунт: {reason}"
ERR_INVALID_API_ID = "⚠️ api_id має бути числом."

# --- Промпт ---
PROMPT_MENU = "📝 <b>Базовий промпт</b>\n\n{prompt}"
BTN_PROMPT_EDIT = "✏️ Змінити"
BTN_PROMPT_RESET = "♻️ Скинути до стандартного"
ASK_PROMPT = "Надішліть новий текст базового промпту одним повідомленням:"
PROMPT_RESET_DONE = "♻️ Промпт скинуто до стандартного."

# --- Аналіз стилю ---
STYLE_MENU = (
    "🧠 <b>Аналіз стилю</b>\n\n"
    "Оберіть чати, з яких зібрати <i>ваші власні</i> повідомлення, "
    "і я створю стислий профіль вашого стилю письма."
)
BTN_STYLE_ONE = "1️⃣ Один чат"
BTN_STYLE_MULTI = "🔢 Кілька чатів"
BTN_STYLE_ALL = "💬 Вся переписка"
BTN_STYLE_SHOW = "👁 Показати профіль"
BTN_STYLE_REGEN = "🔄 Перегенерувати"
BTN_STYLE_DONE_MULTI = "✅ Аналізувати обрані"
BTN_MULTI_CLEAR = "🧹 Очистити вибір"
SELECT_ONE_CHAT = "Оберіть чат:"
SELECT_MULTI_CHAT = "Оберіть чати (натискання = вибір/знімання), потім «Аналізувати обрані»:"
NO_DIALOGS = "Не знайдено діалогів."
MULTI_SELECTED = "Обрано: {count}"
ANALYSIS_STARTED = "⏳ Аналізую переписку (до {limit} повідомлень на чат)… Це може тривати до хвилини."
ANALYSIS_DONE = "✅ Профіль стилю оновлено."
NO_OWN_MESSAGES = "⚠️ Не знайдено ваших вихідних повідомлень у обраних чатах."
STYLE_PROFILE_EMPTY = "Профіль стилю ще не створено."
STYLE_PROFILE_HEADER = "🧠 <b>Профіль стилю</b>\n\n<pre>{profile}</pre>"

# --- Налаштування авто-відповідей ---
AUTOREPLY_MENU = "⚙️ <b>Налаштування авто-відповідей</b>\n\n{summary}"
BTN_TOGGLE_AUTOREPLY = "Авто-відповідь: {state}"
BTN_TOGGLE_MODE = "Режим: {mode}"
BTN_TOGGLE_MENTION = "Лише згадки/приватні: {state}"
MODE_WHITELIST = "лише обрані чати"
MODE_ALL_EXCEPT = "всі, крім ігнорованих"
STATE_ON = "УВІМК ✅"
STATE_OFF = "ВИКЛ ⛔"
BTN_IGNORE_LIST = "🚫 Ігнор-лист"
BTN_WHITELIST = "✅ Білий список"
BTN_REPLY_DELAY = "⏱ Затримка відповіді: {min:.0f}–{max:.0f} с"
BTN_MAX_CHUNKS = "✂️ Макс. частин: {value}"
BTN_CONTEXT_COUNT = "💬 Контекст: {value} повідомлень"
BTN_COOLDOWN = "🌡 Кулдаун: {value} с"
IGNORE_LIST_TITLE = "🚫 <b>Ігнор-лист</b> ({mode_hint}):\n{items}"
WHITELIST_TITLE = "✅ <b>Білий список</b>:\n{items}"
EMPTY_LIST = "порожньо"
BTN_LIST_ADD = "➕ Додати"
BTN_LIST_CLEAR = "🧹 Очистити"
ASK_LIST_ADD = "Надішліть ID чату або @username:"
LIST_ADD_DONE = "✅ Додано: {value}"
LIST_CLEAR_DONE = "🧹 Список очищено."
ASK_REPLY_DELAY = (
    "Надішліть два числа через пробіл: мінімальна та максимальна затримка "
    "відповіді у секундах (наприклад «3 10»)."
)
ASK_NUMBER = "Надішліть число:"
ASK_CONTEXT_COUNT = "Скільки останніх повідомлень брати як контекст (5–50)?"
ASK_COOLDOWN = "Кулдаун на чат у секундах (5–3600)?"
ASK_MAX_CHUNKS = "Максимум частин відповіді (1–5)?"
DELAY_SET_DONE = "✅ Затримка: {min:.0f}–{max:.0f} с."
NUMBER_SET_DONE = "✅ Значення: {value}."

# --- Статус ---
STATUS_HEADER = "📊 <b>Статус TelegramHub</b>\n\n{body}"
STATUS_ACCOUNT = "👤 Акаунт: {status}"
STATUS_KEYS = "🔑 Ключів ШІ: {count} (активний: {active})"
STATUS_AUTOREPLY = "⚙️ Авто-відповідь: {state}"
STATUS_LAST_ERROR = "⚠️ Остання помилка: {error}"
STATUS_NO_ERRORS = "помилок немає ✅"
STATUS_NO_ACTIVE_KEY = "немає"
