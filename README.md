# Remnawave Happ Routing AutoUpdate

Небольшой updater для нашей схемы **Remnawave 3.x + Happ + PinRouting**.

Он обновляет `routing` **не в глобальных Response Headers**, а только внутри правила ответа Remnawave:

```text
responseRules
└── Happ Clients
    └── responseModifications
        └── headers
            └── routing: happ://routing/onadd/...
```

Источник по умолчанию:

```text
https://raw.githubusercontent.com/pincetgore/PinRouting/refs/heads/main/HAPP/DEFAULT.DEEPLINK
```

## Что делает

При старте и далее раз в `CHECK_INTERVAL` секунд сервис:

1. скачивает актуальный `HAPP/DEFAULT.DEEPLINK` из PinRouting;
2. проверяет, что это `happ://routing/onadd/...`;
3. декодирует Base64 и проверяет JSON-профиль;
4. получает свежие `subscription-settings` из Remnawave;
5. находит Response Rule с именем `Happ Clients`;
6. добавляет или обновляет только заголовок `routing` в этом правиле;
7. сохраняет остальные заголовки правила, например `hide-settings`;
8. включает `applyHeadersToEnd: true`;
9. удаляет старый глобальный `customResponseHeaders.routing`, если он остался;
10. после PATCH повторно читает настройки и проверяет, что изменение сохранилось.

Если PinRouting не изменился — PATCH не выполняется.

Сервис **не трогает INCY, Mihomo, Stash, Sing-box, Clash и другие правила**.

## Требование к Remnawave

В `Subscription → Response Rules` должно существовать правило с именем:

```text
Happ Clients
```

Например:

```json
{
  "name": "Happ Clients",
  "enabled": true,
  "operator": "AND",
  "conditions": [
    {
      "headerName": "user-agent",
      "operator": "REGEX",
      "value": "^happ/",
      "caseSensitive": false
    }
  ],
  "responseType": "XRAY_JSON",
  "responseModifications": {
    "headers": [
      {
        "key": "hide-settings",
        "value": "1"
      }
    ],
    "applyHeadersToEnd": true
  }
}
```

Сам `routing` заранее добавлять не обязательно — updater создаст его.

Для INCY используется отдельное правило и родной autorouting:

```text
incy://autorouting/onadd/https://raw.githubusercontent.com/pincetgore/PinRouting/main/INCY/DEFAULT.JSON
```

Этот сервис INCY не изменяет.

## Установка

На сервере с Remnawave:

```bash
git clone https://github.com/vanitoo/remnawave-routing-autoupdate.git
cd remnawave-routing-autoupdate

cp .env.example .env
nano .env
```

Минимально нужно указать API-токен:

```env
REMNA_BASE_URL=http://remnawave:3000/api
REMNA_TOKEN=YOUR_TOKEN
```

Токен Remnawave должен иметь право читать и изменять `subscription-settings`.

Запуск:

```bash
docker compose up -d --build
```

Логи:

```bash
docker compose logs -f
```

Перезапуск после изменения `.env`:

```bash
docker compose up -d --force-recreate
```

Остановка:

```bash
docker compose down
```

## Настройки

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `REMNA_BASE_URL` | обязательна | API Remnawave, обычно `http://remnawave:3000/api` |
| `REMNA_TOKEN` | обязательна | Bearer API token |
| `GITHUB_RAW_URL` | PinRouting `HAPP/DEFAULT.DEEPLINK` | источник Happ routing |
| `RESPONSE_RULE_NAME` | `Happ Clients` | имя правила Remnawave |
| `CHECK_INTERVAL` | `3600` | интервал проверки в секундах, минимум 60 |
| `REQUEST_TIMEOUT` | `30` | timeout HTTP-запросов |
| `LOG_LEVEL` | `INFO` | уровень логирования |

## Что должно быть в логах

При первом обновлении:

```text
Removing legacy global routing header
Updating 'Happ Clients' routing (...)
Routing updated successfully: PinRouting (LastUpdated=...)
```

Если изменений нет:

```text
No changes: PinRouting is already current (LastUpdated=...)
```

При временной ошибке GitHub или Remnawave сервис не завершается: пишет ошибку в лог и повторяет попытку максимум через 5 минут.

## Почему не глобальный `routing`

Глобальный `customResponseHeaders.routing` Remnawave отправляет всем клиентам подписки. Нам это не подходит: Happ должен получать `happ://...`, а INCY — свой `incy://autorouting/...`.

Поэтому routing Happ хранится непосредственно в Response Rule `Happ Clients`.

## Основа

Проект форкнут из `lifeindarkside/Remnawave-Routing-update` и существенно упрощён под нашу схему: удалена поддержка External Squads и обновление глобального routing-заголовка.
