# Чат «Экономист» через n8n

Запрос с страницы `/economist` отправляется на ваш webhook в n8n. Ответ из n8n возвращается в чат.

## Что нужно от вас

### 1. Рабочий n8n

- Свой сервер n8n **или** облако [n8n.io](https://n8n.io)
- Workflow **включён** (Active)

### 2. Workflow в n8n

Минимальная схема:

```text
[Webhook] → … ваша логика (LLM, Google Sheets, 1С) … → [Respond to Webhook]
```

**Узел Webhook**

- HTTP Method: `POST`
- Path: например `economist` (как вам удобно)
- Response Mode: **Using 'Respond to Webhook' Node** (ответ отдельным узлом в конце)

**Узел Respond to Webhook** (в конце цепочки)

- Respond With: `JSON`
- Response Body, пример:

```json
{
  "answer": "Текст ответа пользователю на русском"
}
```

Допустимы также поля: `response`, `output`, `text`, `message`, `result` — приложение попробует их прочитать.

**Факт по статье (таблица в чате)** — массив с одной или несколькими статьями:

```json
[
  {
    "output": [
      {
        "Приоритет": "Приоритетная статья",
        "Код статьи": "2214",
        "Наименование статьи": "Газоснабжение",
        "Годовой лимит": 1736,
        "Текущий период": "3 месяца",
        "Текущий лимит": 738
      },
      {
        "Приоритет": "Альтернатива",
        "Код статьи": "11224",
        "Наименование статьи": "Газоснабжение",
        "Годовой лимит": 1736,
        "Текущий период": "3 месяца",
        "Текущий лимит": 738
      }
    ]
  }
]
```

`output` может быть **одним объектом** или **массивом** статей — на странице Экономиста отобразится таблица.

**Отчёт по объекту** (доходы, расходы, итоги):

```json
[
  {
    "output": {
      "Объект": "Санкт-Петербург, ул. Гороховая д.2/6",
      "Статьи доходов и расходов": {
        "Доход": {
          "items": [
            {
              "Код статьи": "11221",
              "Наименование статьи": "…",
              "Годовой лимит": 2168,
              "Текущий период": "Январь-Июнь",
              "Текущий лимит": 1323
            }
          ],
          "Всего доходов": { "Годовой лимит": 20924, "Текущий лимит": 10611 }
        },
        "Расход": {
          "items": [ { "Код статьи": "2312а", "…": "…" } ],
          "Всего расходов": { "Годовой лимит": 61597, "Текущий лимит": 30902 }
        },
        "Финансовый результат": { "Годовой лимит": -40673, "Текущий лимит": -20291 }
      }
    }
  }
]
```

В чате: название объекта, таблицы **Доход** и **Расход** (строки + итог), блок **Финансовый результат**.

### 3. Production URL webhook

В n8n откройте узел Webhook и скопируйте **Production URL**, например:

```text
https://n8n.example.com/webhook/economist
```

или для теста:

```text
https://n8n.example.com/webhook-test/economist
```

> Для постоянной работы используйте **Production URL**, не Test URL.

### 4. Запись в `.env` проекта

```env
N8N_ECONOMIST_WEBHOOK_URL=https://ваш-n8n.example/webhook/economist
N8N_ECONOMIST_WEBHOOK_METHOD=POST
N8N_ECONOMIST_TIMEOUT=120

ECONOMIST_FACT_SHEET_URL=https://docs.google.com/spreadsheets/d/ВАШ_ID/edit
```

Перезапустите приложение после изменения `.env`.

### 5. (Рекомендуется) Google-таблица факта

В workflow можно читать ту же таблицу, что открывается кнопкой на странице. В webhook приходит поле `fact_sheet_url` — ссылка из `.env`.

### 6. Доступность с сервера приложения

Сервер, где крутится `uvicorn` (ДИД_ассистент), должен **достучаться до URL n8n** по HTTPS. Если n8n только в локальной сети — приложение тоже должно быть в этой сети или нужен публичный URL n8n.

---

## Что приходит в n8n (тело POST, JSON)

```json
{
  "message": "Хочу посмотреть факт по статье 2221",
  "query": "Хочу посмотреть факт по статье 2221",
  "module": "economist",
  "fact_sheet_url": "https://docs.google.com/spreadsheets/d/.../edit",
  "session_id": "economist"
}
```

В n8n: `{{ $json.message }}` или `{{ $json.query }}`.

---

## Проверка

1. В n8n: **Execute workflow** / тест webhook с тем же JSON.
2. В приложении: откройте `/economist`, отправьте вопрос в чат.
3. При ошибке смотрите логи uvicorn и Executions в n8n.

### Тест из командной строки

```bash
curl -X POST "https://ваш-n8n.example/webhook/economist" ^
  -H "Content-Type: application/json" ^
  -d "{\"message\":\"тест\",\"module\":\"economist\"}"
```

В ответе должен быть JSON с полем `answer`.

---

## Частые проблемы

| Симптом | Что проверить |
|--------|----------------|
| «N8N_ECONOMIST_WEBHOOK_URL не задан» | Строка в `.env`, перезапуск сервера |
| 502 / таймаут | Workflow долгий — увеличьте `N8N_ECONOMIST_TIMEOUT` |
| Пустой ответ / «[object Object]» | Для таблицы: Expression `={{ [{ output: $json.output }] }}`, не `"answer": "{{ $json.output }}"` |
| 404 «not registered for POST» | В n8n у Webhook указан **GET** — смените на **POST** или `N8N_ECONOMIST_WEBHOOK_METHOD=GET` |
| 404 на webhook | Workflow не **Active** или неверный URL (Test вместо Production) |
| 500 «Unused Respond to Webhook» | Лишний узел Respond to Webhook — оставьте **один** в конце цепочки; удалите дубликаты |
| OAuth: *invalid, expired, revoked* | Переподключите credential Google/Microsoft в n8n (см. ниже) |

### OAuth: «authorization grant … invalid, expired, revoked»

Так пишет n8n, когда **протухли или сбросились** учётные данные узла (часто **Google Sheets**, **Google Drive**, Gmail).

**Что сделать в n8n:**

1. Откройте workflow **Экономист** → узел с Google (Sheets / Drive) → **Credential**.
2. **Credentials** (меню слева) → найдите нужный Google OAuth2 → **Reconnect** / удалите и создайте заново.
3. При создании credential в Google Cloud Console:
   - тип **OAuth client ID** → **Web application**;
   - **Authorized redirect URIs** — скопируйте **точный** Redirect URL из окна n8n (для self-hosted часто `https://ваш-n8n/oauth/callback`).
4. После переподключения: **Execute workflow** на тестовом сообщении — узел Google должен стать зелёным.
5. Убедитесь, что в конце цепочки срабатывает **Respond to Webhook** с JSON `answer` или Expression для таблицы.

Пока OAuth в n8n красный, приложение получит **HTTP 200 с пустым телом** — это следствие, а не ошибка Python.

### Ошибка «Unused Respond to Webhook node»

n8n нашёл узел **Respond to Webhook**, который не участвует в выполнении. Исправление:

1. **Webhook** (первый узел): *Response Mode* → **Using 'Respond to Webhook' Node** (не *Immediately*).
2. Цепочка: `[Webhook] → … ваша логика … → [Respond to Webhook]` — **ровно один** Respond в конце.
3. Удалите второй/третий Respond to Webhook, если они остались от старых веток или тестов.
4. Если есть IF/Switch — каждая **активная** ветка должна сходиться в один Respond to Webhook (или в каждой ветке свой, но тогда неиспользуемые узлы удалить).

**Respond to Webhook** (последний узел) — **текстовый** ответ (строка):

```json
{
  "answer": "{{ $json.text }}"
}
```

Подставьте поле с **текстом** из LLM (не массив и не объект).

**Respond to Webhook** — **таблица статей** (массив в `output`):

1. Respond With: `JSON`
2. Response Body → переключите в режим **Expression** (иконка `fx`)
3. Выражение:

```text
={{ [{ output: $json.output }] }}
```

Если предыдущий узел уже отдаёт `{ "output": [ {...}, {...} ] }`, можно короче:

```text
={{ $json }}
```

> **Не делайте** `"answer": "{{ $json.output }}"` — JavaScript превратит объекты в строку `[object Object]` и таблица в чате будет пустой.

---

## Опционально позже

- Авторизация webhook (Header Auth в n8n + заголовок в коде)
- `session_id` для памяти диалога в n8n
- Отдельные webhook для Секретаря / Юриста
