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
BTN_REMINDERS = "🔔 Нагадування"
BTN_PROFILES = "👤 Профілі"
BTN_BACK = "⬅️ Повернутись"
BTN_CANCEL = "❌ Скасувати"

# --- Загальні ---
ERR_GENERIC = "⚠️ Сталася помилка. Спробуйте ще раз пізніше."
ERR_NOT_CONNECTED = "⚠️ Акаунт Telegram не підключено. Спочатку підключіть акаунт."
ERR_NO_ACTIVE_KEY = "⚠️ Немає активного API-ключа ШІ. Додайте ключ у розділі «API ключі ШІ»."
SAVED = "✅ Збережено."
CANCELLED = "❌ Дію скасовано."

# --- API ключі ---
API_KEYS_MENU = (
    "🔑 <b>API ключі ШІ</b>\n\n"
    "Додайте ключ провайдера (OpenAI-сумісний, Anthropic або Google Gemini). "
    "Ключі зберігаються локально у SQLite і ніколи не показуються повністю."
)
BTN_KEY_ADD = "➕ Додати ключ"
BTN_KEY_LIST = "📋 Список ключів"
CHOOSE_PROVIDER = (
    "Оберіть провайдера — ключ перевіриться одразу, і моделі покажу списком:\n\n"
    "💡 Безкоштовний варіант — Google Gemini: "
    "<a href=\"https://aistudio.google.com/app/apikey\">aistudio.google.com/app/apikey</a> "
    "(кнопка «Get API key», безкоштовно)."
)
PROVIDER_GOOGLE = "🔵 Google Gemini"
ASK_BASE_URL = (
    "🌐 <b>Базовий URL</b> — адреса API провайдера.\n\n"
    "Для класичного OpenAI надішліть:\n"
    "<code>https://api.openai.com/v1</code>\n\n"
    "Для інших провайдерів (OpenRouter, DeepSeek, Groq тощо) — їхній URL, "
    "наприклад <code>https://openrouter.ai/api/v1</code>.\n\n"
    "ⓘ Обов'язково <code>https://</code> на початку та <code>/v1</code> в кінці "
    "(для більшості провайдерів). Якщо забудете — я сам підставлю https://."
)
ASK_API_KEY = (
    "🔑 Надішліть секретний API-ключ.\n\n"
    "Де створити:\n"
    "• OpenAI — <a href=\"https://platform.openai.com/api-keys\">platform.openai.com/api-keys</a> "
    "(ключ виду <code>sk-...</code>)\n"
    "• OpenRouter — <a href=\"https://openrouter.ai/keys\">openrouter.ai/keys</a> "
    "(ключ виду <code>sk-or-...</code>)\n"
    "• Anthropic — <a href=\"https://console.anthropic.com/settings/keys\">console.anthropic.com</a> "
    "(ключ виду <code>sk-ant-...</code>)\n"
    "• Google Gemini — <a href=\"https://aistudio.google.com/app/apikey\">aistudio.google.com</a> "
    "(ключ виду <code>AIza...</code>)\n\n"
    "⚠️ Ключ — секрет: не публікуйте його і не надсилайте нікому, крім цього бота."
)
ASK_MODEL = (
    "🧠 Надішліть назву моделі.\n\n"
    "Приклади:\n"
    "• OpenAI: <code>gpt-4o-mini</code>, <code>gpt-4o</code>\n"
    "• OpenRouter: <code>openai/gpt-4o-mini</code>, <code>anthropic/claude-sonnet-4</code>\n"
    "• DeepSeek: <code>deepseek-chat</code>\n"
    "• Gemini: <code>gemini-2.0-flash</code>\n"
    "• Anthropic: <code>claude-sonnet-4-20250514</code>"
)
KEY_ADDED_ACTIVE = "✅ Ключ #{key_id} додано ({provider}) — модель {model}. Ключ активний ⭐"
MODELS_FOUND = (
    "✅ Ключ працює! Доступних моделей: {count}.\n\nОберіть модель кнопкою нижче "
    "(або введіть назву вручну):"
)
MODEL_VERIFYING = "⏳ Перевіряю модель «{model}» реальним запитом…"
KEY_LIST_EMPTY = "Ключів ще немає."
KEY_ACTIVATE_DONE = "✅ Ключ #{key_id} тепер активний."
KEY_DELETED = "🗑 Ключ #{key_id} видалено."
KEY_LABEL_FMT = "#{key_id} · {provider} · {model} · …{tail}{active_mark}"

# --- Підключення акаунта ---
CONNECT_MENU = (
    "👤 <b>Підключення акаунта</b>\n\nСтатус: {status}\n\n"
    "Процес: api_id → api_hash → телефон → код → (пароль 2FA, за потреби)."
)
STATUS_CONNECTED = "✅ підключено ({phone})"
STATUS_NOT_CONNECTED = "❌ не підключено"
BTN_CONNECT_START = "🔌 Підключити"
ASK_API_ID = (
    "🔢 <b>Крок 1/3 — api_id</b>\n\n"
    "Надішліть api_id — це <b>число</b>, яке прив'язане до вашого Telegram-акаунта.\n\n"
    "Де взяти:\n"
    "1️⃣ Відкрийте <a href=\"https://my.telegram.org\">my.telegram.org</a> у браузері\n"
    "2️⃣ Увійдіть за номером вашого Telegram (код підтвердження прийде в Telegram)\n"
    "3️⃣ Оберіть <b>«API development tools»</b>\n"
    "4️⃣ Заповніть коротку форму (назва/опис — будь-що, напр. «myapp»)\n"
    "5️⃣ На сторінці з'явиться <b>App api_id</b> — число виду <code>1234567</code>\n\n"
    "⚠️ Ніколи не передавайте api_hash стороннім.\n\n"
    "Надішліть число повідомленням:"
)
ASK_API_HASH = (
    "🔑 <b>Крок 2/3 — api_hash</b>\n\n"
    "Надішліть api_hash — це <b>рядок із 32 символів</b> (літери та цифри), "
    "який лежить поруч із api_id на тій самій сторінці.\n\n"
    "Де взяти: та сама сторінка <a href=\"https://my.telegram.org/apps\">my.telegram.org/apps</a> → "
    "поле <b>App api_hash</b>.\n\n"
    "Якщо ви вже заповнювали форму раніше, сторінка просто покаже api_id та api_hash — "
    "створювати новий застосунок не потрібно.\n\n"
    "⚠️ api_hash — це секрет: той, хто ним володіє, може підключитись до вашого акаунта. "
    "Нікому не надсилайте його, крім цього бота.\n\n"
    "Надішліть рядок повідомленням:"
)
ASK_PHONE = "Надішліть номер телефону у міжнародному форматі (+380...):"
CODE_SENT = "📨 Код надіслано на {phone}. Надішліть код із Telegram (або з SMS):"
ASK_PASSWORD = "🔒 Увімкнено 2FA. Надішліть хмарний пароль (або «/skip» — пропустити, якщо 2FA вимкнено):"
CONNECT_SUCCESS = "✅ Акаунт підключено! Сесію збережено в sessions/{user_id}.session"
CONNECT_FAILED = "⚠️ Не вдалося підключити акаунт: {reason}"
ERR_INVALID_API_ID = "⚠️ api_id має бути числом."
BTN_QR_LOGIN = "📷 Вхід через QR-код"
QR_READY = (
    "📷 <b>QR-код готовий!</b>\n\n"
    "Варіант А: відкрийте Telegram на телефоні → <b>Налаштування → Пристрої → "
    "Пов'язати пристрій</b> → наведіть камеру на QR-код вище.\n\n"
    "Варіант Б: натисніть кнопку нижче з пристрою, де ви залогінені, "
    "і підтвердьте вхід.\n\n"
    "⏳ Чекаю підтвердження до 4 хвилин…"
)
QR_TIMEOUT = "⌛ Час очікування вичерпано. Спробуйте ще раз: 📱 Підключити акаунт → 📷 Вхід через QR-код."
QR_USING_SAVED = "Використовую збережені api_id/api_hash з попереднього підключення…"

# --- Промпт (дві частини: короткий промпт + база знань) ---
PROMPT_SPLIT_MENU = (
    "📝 <b>Промпт</b>\n\n"
    "Оберіть частину:\n"
    "• «✍️ Короткий промпт» — базова інструкція для авто-відповідей;\n"
    "• «🧠 База знань» — довгі записи: факти про вас, правила, довідники."
)
PROMPT_MENU = "📝 <b>Базовий промпт</b>\n\n{prompt}"
BTN_PROMPT_SHORT = "✍️ Короткий промпт"
BTN_PROMPT_EDIT = "✏️ Змінити"
BTN_PROMPT_RESET = "♻️ Скинути до стандартного"
ASK_PROMPT = "Надішліть новий текст базового промпту одним повідомленням:"
PROMPT_RESET_DONE = "♻️ Промпт скинуто до стандартного."

# --- База знань (Obsidian-подібна) ---
BTN_KB = "🧠 База знань"
KB_MAX_CHARS = 15000
KB_MENU = (
    "🧠 <b>База знань</b>\n\n"
    "Записів: {count} · Обсяг: {chars}/{limit} символів.\n\n"
    "Записи додаються до системного промпту авто-відповідей: "
    "факти про вас, правила поведінки, довідники. Один запис — одна тема."
)
BTN_KB_ADD = "➕ Додати запис"
BTN_KB_LIST = "📋 Записи бази"
BTN_KB_EXPORT = "📤 Експорт бази знань"
ASK_KB_CONTENT = (
    "🧠 Надішліть текст запису бази знань — одним довгим повідомленням "
    "або кількома підряд (я зіберу їх в один запис).\n\n"
    "Завершіть командою <code>/done</code>. Перша половина першого рядка "
    "стане назвою запису."
)
KB_CHUNK_SAVED = "✅ Додано {chars} символів (усього {total}). Ще — або <code>/done</code>."
KB_SAVED = "✅ Запис «{title}» додано до бази знань ({chars} симв.)."
KB_EMPTY_BUFFER = "Нічого не надіслано — запис порожній."
KB_LIST_EMPTY = "База знань поки порожня."
KB_TOO_LONG = (
    "⚠️ Ліміт бази знань ({limit} символів) перевищено. "
    "Цей текст не додано: скоротіть запис або видаліть щось із бази."
)
KB_ENTRY_DELETED = "🗑 Запис видалено з бази знань."
KB_ENTRY_FMT = "📄 <b>{title}</b>\n\n{content}"
KB_EXPORT_NAME = "baza-znan.md"
KB_EXPORT_EMPTY = "База знань порожня — експортувати нічого."

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
BTN_CHAT_PROFILE = "👤 Профіль цієї людини"
BTN_STYLE_DONE_MULTI = "✅ Аналізувати обрані"
BTN_MULTI_CLEAR = "🧹 Очистити вибір"
SELECT_ONE_CHAT = "Оберіть чат:"
SELECT_MULTI_CHAT = "Оберіть чати (натискання = вибір/знімання), потім «Аналізувати обрані»:"
NO_DIALOGS = "Не знайдено діалогів."
MULTI_SELECTED = "Обрано: {count}"
ANALYSIS_STARTED = "⏳ Аналізую переписку (до {limit} повідомлень на чат)… Це може тривати до хвилини."
ANALYSIS_DONE = "✅ Профіль стилю оновлено."
STYLE_PROFILE_EMPTY = "Профіль стилю ще не створено."
STYLE_PROFILE_HEADER = "🧠 <b>Профіль стилю</b>\n\n<pre>{profile}</pre>"
CHAT_PROFILE_MENU = (
    "👤 <b>Профіль цієї людини</b>\n\n"
    "Оберіть чат — я проаналізую ВЗАЄМОДІЮ саме з цією людиною: як ви з нею "
    "листуєтесь, як вона пише, які у вас спільні теми. Цей профіль матиме "
    "пріоритет над загальним при відповідях у цьому чаті."
)
CHAT_PROFILE_DONE = "✅ Профіль для чату {title} збережено. Тепер відповіді там підлаштуються саме під цю людину."
CHAT_PROFILE_SHOW_HEADER = "👤 <b>Профіль чату {title}</b>\n\n<pre>{profile}</pre>"
CHAT_PROFILE_EMPTY = "Пер-чат профілів ще немає. Створіть через «👤 Профіль цієї людини»."
CHAT_PROFILE_DELETED = "🗑 Пер-чат профіль видалено — у цьому чаті знову діє загальний профіль."

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
IGNORE_LIST_TITLE = "🚫 <b>Ігнор-лист</b>:\n{items}"
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
AUTOREPLY_PROFILE_LINE = "\n👤 Активний профіль: <b>{name}</b>"

# --- Статус ---
STATUS_HEADER = "📊 <b>Статус TelegramHub</b>\n\n{body}"
STATUS_ACCOUNT = "👤 Акаунт: {status}"
STATUS_KEYS = "🔑 Ключів ШІ: {count} (активний: {active})"
STATUS_AUTOREPLY = "⚙️ Авто-відповідь: {state}"
STATUS_LAST_ERROR = "⚠️ Остання помилка: {error}"
STATUS_NO_ERRORS = "помилок немає ✅"
STATUS_NO_ACTIVE_KEY = "немає"

# --- Нагадування та журнал Q&A ---
JOURNAL_MENU = (
    "🔔 <b>Нагадування</b>\n\n"
    "📜 <b>Журнал запитань і відповідей</b> (останні {count}):\n"
    "{entries}\n\n"
    "{reminders_block}"
)
JOURNAL_ENTRY_FMT = (
    "— <b>{title}</b> [{created_at}]\n"
    "❓ {question}\n"
    "↩️ {answer}"
)
JOURNAL_EMPTY = "Записів ще немає — тут з'являться всі авто-відповіді."
BTN_JOURNAL_CLEAR = "🗑 Очистити журнал"
JOURNAL_CLEARED = "🗑 Журнал очищено."
BTN_REMINDER_ADD = "➕ Нагадування"
REMINDERS_BLOCK = "⏰ <b>Заплановані нагадування</b>:\n{items}"
REMINDERS_EMPTY = "⏰ Запланованих нагадувань немає."
REMINDER_FMT = "• {due_at} — {text}"
ASK_REMIND_TEXT = "⏰ Про що нагадати? Надішліть текст нагадування одним повідомленням:"
ASK_REMIND_TIME = (
    "Коли нагадати? Надішліть час одним повідомленням:\n\n"
    "• <code>25.11.2026 14:30</code> — конкретна дата і час\n"
    "• <code>через 30 хв</code>, <code>через 2 год</code>, <code>через 3 дні</code> — відносно"
)
REMINDER_BAD_TIME = (
    "⚠️ Не зрозумів час. Приклади: «25.11.2026 14:30», «через 30 хв», "
    "«через 2 год», «через 3 дні»."
)
REMINDER_ADDED = "✅ Нагадування збережено на {due_at}."
REMINDER_DELETED = "🗑 Нагадування видалено."
REMINDER_TEXT = "🔔 Нагадування: {text}"
REMINDER_BAD_PAST = "⚠️ Цей час уже минув. Вкажіть майбутній час."
BTN_REM_DELETE = "🗑"

# --- Профілі налаштувань ---
PROFILES_MENU = (
    "👤 <b>Профілі налаштувань</b>\n\n"
    "Профіль — іменований набір: списки чатів, затримки, кулдаун, частини, "
    "контекст, авто-відповідь, короткий промпт і база знань.\n\n"
    "Активний профіль: {active}"
)
BTN_PROFILE_SAVE = "💾 Зберегти поточне як профіль"
BTN_PROFILE_LIST = "📋 Мої профілі"
ASK_PROFILE_NAME = "Надішліть назву профілю (1–40 символів):"
PROFILE_BAD_NAME = "⚠️ Назва має бути від 1 до 40 символів."
PROFILE_SAVED = "✅ Профіль «{name}» збережено."
PROFILES_LIST = "📋 <b>Мої профілі</b>\n\n{items}"
PROFILE_ITEM_FMT = "#{profile_id} · <b>{name}</b> · {created_at}"
PROFILES_EMPTY = "Профілів ще немає. Натисніть «💾 Зберегти поточне як профіль»."
PROFILE_APPLIED = "✅ Профіль «{name}» застосовано."
PROFILE_DELETE_CONFIRM = "🗑 Профіль #{profile_id} «{name}» видалено."
PROFILE_APPLY_FAILED = "⚠️ Профіль не знайдено."
PROFILE_ACTIVE_MARK = " ⬅️ активний"
PROFILE_NONE_ACTIVE = "немає (стандартні налаштування)"
