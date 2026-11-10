# Отдельный Telegram relay

Relay принимает уведомление по TLS gRPC, сохраняет его в собственной SQLite и отдельным sender отправляет в Telegram. Используется самостоятельный образ `internal-notifier:local` и команда `python -m internal_notifier.server`; PostgreSQL CRM, пароли БД и ключи ПДн ему не нужны. Протокол: [notifications.proto](../backend/app/notification_protocol/notifications.proto), ограничения: [дизайн relay](superpowers/specs/2026-10-28-telegram-relay-design.md), [контракт внешнего сервиса](superpowers/specs/2026-10-29-internal-notifier-transition-design.md).

На VM сайта `157.228.139.156` исходящий Telegram недоступен. Relay необходимо разместить на другом хосте с доступом к Bot API и доступным сайту TLS gRPC endpoint. Эти файлы подготавливают запуск; production-хост не создан, отправка и переключение не выполнялись. Не включайте очередь и не запускайте технические сообщения без отдельного согласования: включение отправит также накопленный backlog.

Сервер работает из отдельного репозитория `../internal-notifier`: пакет `internal_notifier`, собственный Dockerfile, SQLite ledger и серверные тесты. В CRM остаются типизированный порт `NotifierClient`, TLS `RelayClient`, protobuf V1 и builder доверенных ссылок. Wire-контракт `notifications.v1.NotificationRelay`, snapshots и параметры `NOTIFICATION_*` не меняются; серверные модули здесь удалены. Новые автоматические уведомления не добавлены. Допустимые transport — только `direct` и `relay`; неизвестные значения останавливают запуск API и не разрешают worker/CLI прямую отправку. Для безопасной локальной проверки используйте [develop/README.md](../develop/README.md): только тестовая БД и fake Bot API, ноль реальных отправок Telegram.

## Подготовка отдельного хоста

Соберите самостоятельный образ из отдельного репозитория, находясь в корне CRM:

```sh
docker build -t internal-notifier:local ../internal-notifier
```

Перенесите [compose.relay.yaml](../compose.relay.yaml), образ `internal-notifier:local` и приватные operator-файлы на relay-хост. Образ можно передать через `docker save`/`docker load`; backend-образ CRM для запуска сервера больше не подходит. Compose не запускает CRM. Не масштабируйте сервис: одна инстанция, один sender, один постоянный том `relay_data`. Каталог `/data/relay` в образе и новый том принадлежат UID/GID `1000:1000`; при восстановлении сохраните этого владельца. Не удаляйте том при обновлении.

Подготовьте сертификат с DNS-именем endpoint, цепочку и приватный ключ. TLS обязателен даже при loopback-публикации; нужны HTTP/2/gRPC, а не обычный HTTP/1 reverse proxy. По умолчанию порт опубликован только на `127.0.0.1:50051`. Открывайте нужный адрес через `RELAY_BIND` после настройки сертификата, firewall и допустимого доступа от хоста сайта. Имя в `NOTIFICATION_RELAY_TARGET` должно совпадать с сертификатом.

Создайте **новый случайный токен каждого source**, минимум 32 символа. В website env хранится сырой токен; на relay — только его SHA-256 verifier. Пример локальной генерации направляет результат в приватный файл; не публикуйте его:

```sh
umask 077
python3 - <<'PY' > relay-source.private
import hashlib, secrets
token = secrets.token_urlsafe(48)
print('NOTIFICATION_RELAY_TOKEN=' + token)
print('token_sha256=' + hashlib.sha256(token.encode()).hexdigest())
PY
```

Скопируйте [relay.config.example.json](../docker/relay.config.example.json) в приватный config и замените `REPLACE_WITH_SHA256_OF_A_NEW_PRIVATE_SOURCE_TOKEN` полученным verifier. Placeholder намеренно не проходит validation: готового рабочего credential в репозитории нет. `sources` — список с уникальными `id` и verifier; `aliases` — map. Source `mirzap67` может отправлять только через `mirzap67.staff`, который настроен на `-1003744016989`, с origin `https://mirzap67.ru`. Проверьте ID и права бота на отправку в этот чат до согласованного включения. Токен бота в JSON не помещайте.

В приватном `.env.relay` задайте:

```dotenv
RELAY_IMAGE=internal-notifier:local
RELAY_BIND=127.0.0.1
RELAY_PORT=50051
RELAY_LISTEN_ADDRESS=[::]:50051
RELAY_CONFIG_FILE=/absolute/private/relay.config.json
RELAY_TLS_CERT_FILE=/absolute/private/fullchain.pem
RELAY_TLS_KEY_FILE=/absolute/private/privkey.pem
TELEGRAM_BOT_TOKEN=REPLACE_PRIVATELY_WITH_THE_BOT_TOKEN
```

Не используйте этот placeholder как токен. Env-файл — `0600`; JSON и TLS-файлы должны читаться контейнерным UID 1000, приватный ключ — только этим владельцем/уполномоченной группой (например, UID 1000 и `0400`). Родительские каталоги должны разрешать проход. Compose монтирует три конкретных файла read-only и не создаёт отсутствующие пути. `RELAY_CONFIG_FILE` и TLS-пути в `.env.relay` относятся к хосту; внутри контейнера выставлены фиксированные пути `/run/relay/*`. `RELAY_DATABASE_PATH=/data/relay/notifications.sqlite` задаётся Compose, не указывает на CRM. Image работает с cap-drop ALL, no-new-privileges и read-only root filesystem.

Проверка конфигурации без запуска и отправок:

```sh
docker compose --env-file .env.relay -f compose.relay.yaml config --quiet
```

Команда проверяет Compose и подстановки; JSON/certificate/доступность Telegram она не проверяет. Не выводите полный `config` с настоящими секретами в общий журнал. Проверить конфигурацию штатным загрузчиком без запуска sender можно в образе, не импортируя env сайта:

```sh
docker compose --env-file .env.relay -f compose.relay.yaml run --rm --no-deps --entrypoint python relay -c 'from internal_notifier.server import load_config; load_config(); print("config valid")'
```

`load_config()` читает JSON из `RELAY_CONFIG_FILE`, определяет внутренний `bot_id` из `TELEGRAM_BOT_TOKEN` и проверяет конфигурацию. Эта проверка не запускает sender или TLS listener и не проверяет credentials у Telegram. Сам запуск сервиса **начинает обработку сохранённой очереди**; выполняйте только в согласованном окне:

```sh
docker compose --env-file .env.relay -f compose.relay.yaml up -d --no-build --pull never --wait
docker compose --env-file .env.relay -f compose.relay.yaml exec relay python -m internal_notifier.server stats
```

Healthcheck `check-ledger` проверяет возможность записи SQLite; это не подтверждение доставки в Telegram и не end-to-end TLS-проверка. `stats` показывает только счётчики, возраст backlog, время последнего успеха и паузу, без payload. Не запускайте второй relay с тем же bot/томом.

## ACL и изменения конфигурации

Сырой source credential передаётся только в gRPC authorization metadata по TLS. По нему сервер определяет source; передать чужое source ID в запросе нельзя. `destinations` разрешает только перечисленные alias; `allowed_origins` разрешает только точные HTTPS-origin без path/query/fragment. URL с userinfo/credentials и небезопасными символами запрещены. У `mirzap67` разрешены только канонические маршруты и параметры navigation-v1 с настроенным origin, включая прежний `/#crm-feedback?case=<UUID>`. Одних совпадения origin и похожего префикса недостаточно: неизвестные маршруты, лишние/повторяющиеся параметры и небезопасные идентификаторы отклоняются. Для других source в v1 допустим обычный HTTPS URL с path, без query и fragment. Текст — plain text, без parse_mode; превью выключены. Ни chat ID, ни bot token source в Submit не задаёт.

`revision` alias — явная версия назначения, например `mirzap67-staff-v1`. При осознанной смене чата меняйте её: принятые записи сохраняют прежние chat/revision/bot ID, новые используют новую конфигурацию. Изменение bot token на токен другого бота останавливает старые записи с `TELEGRAM_BOT_CHANGED`, не переадресует их автоматически. JSON читается на старте; изменения требуют пересоздания/перезапуска. Для ротации source credential `token_sha256` может временно быть списком старого и нового verifier; после обновления source и проверки удалите старый. Не переиспользуйте verifier между source. Смена website credential/target при незавершённых snapshots может остановить их с безопасным кодом внимания: сначала согласуйте завершение старой очереди, а не создавайте новые request IDs.

## Явное переключение сайта direct → relay

`NOTIFICATION_TRANSPORT=direct` сохраняет прежнюю доставку по умолчанию. Relay-режим никогда не откатывается на прямой Bot API при ошибке настройки/сети. Отсутствие Telegram на VM не блокирует приём обращений: они сохраняются в outbox.

1. Подготовьте и проверьте отдельный relay, TLS и ACL. Остановите website `notifications`; установите `TELEGRAM_NOTIFICATIONS_ENABLED=false` также у backend, чтобы служебная готовность не обещала работающую отправку. Оставьте HTTP-приём обращений доступным только если согласовано накопление новых событий. Сделайте согласованные защищённые backup CRM/outbox и relay.
2. Проверьте старый backlog и состояния `running/uncertain`. Прерванный прямой запрос мог уже попасть в Telegram: relay не знает его истории. Не обнуляйте состояния/попытки и не переписывайте ID, чтобы искусственно получить «чистую» очередь. Согласуйте, какие ожидающие события отправлять; включение обработает и старые необработанные обращения. Самостоятельного endpoint массового удаления/ручного подтверждения доставки нет.
3. В website `.env.docker` установите `NOTIFICATION_TRANSPORT=relay`, `NOTIFICATION_RELAY_TARGET=relay.example.ru:50051` (без `https://`), приватный `NOTIFICATION_RELAY_TOKEN`, `NOTIFICATION_DESTINATION_ALIAS=mirzap67.staff`, `PUBLIC_SITE_URL=https://mirzap67.ru`. Backend и notifications получают одинаковые настройки. При доверенном публичном сертификате оставьте `NOTIFICATION_RELAY_CA_FILE` пустым.
4. Для частного CA нужен **явный** локальный override с read-only mount существующего CA-файла в оба сервиса и `NOTIFICATION_RELAY_CA_FILE`, указывающим на контейнерный путь. Пустой host path автоматически не монтируется. Например, только для этого случая:

   ```yaml
   services:
     backend:
       volumes:
         - /absolute/private/relay-ca.pem:/run/relay-ca.pem:ro
       environment:
         NOTIFICATION_RELAY_CA_FILE: /run/relay-ca.pem
     notifications:
       volumes:
         - /absolute/private/relay-ca.pem:/run/relay-ca.pem:ro
       environment:
         NOTIFICATION_RELAY_CA_FILE: /run/relay-ca.pem
   ```

   Сохраните override отдельно и включайте `-f <override>` при проверках/обновлениях. CA не заменяет проверку имени endpoint. Не монтируйте приватный TLS-ключ relay на сайт.
5. Пересоздайте backend с прежними HTTPS-файлами/override, сохранив sending=false. Проверка `python -m app.telegram_cli check` не отправляет сообщение и показывает готовность выбранного транспорта. Технический `test` и повторная отправка требуют отдельного разрешения; успешный Submit ещё не подтверждает Telegram. После согласованного end-to-end теста и проверки квитанции включите `TELEGRAM_NOTIFICATIONS_ENABLED=true` и пересоздайте backend и notifications. Следите за backlog/ошибками; source помечает `done` только по `TELEGRAM_ACCEPTED` с положительным `telegram_message_id`.
6. После завершения прямых попыток удалите bot token/chat ID с website-хоста из runtime env, оставив bot token только на relay; сохраните защищённые архивные конфигурации по правилам backup. Если токен был раскрыт, отзовите его через управление ботом и согласуйте судьбу принятых записей. Не меняйте destination/bot незаметно для старого backlog.

Source сохраняет неизменяемые request UUID, timestamp, target и payload до Submit. Потерянный ответ повторяется с тем же snapshot; конфликт тела под тем же `(source, request_id)` отклоняется. Принятый relay snapshot не отправляется direct даже при последующей смене режима. Ошибка Get не является разрешением переслать сообщение напрямую. Перед обратным переключением сначала остановите sender и согласуйте pending/uncertain; автоматического бесшовного rollback нет.

## Квитанции, неопределённость и срок хранения

`QUEUED`, `SENDING`, `RETRY_WAIT` означают ожидание/попытку, а не доставку. `UNCERTAIN` означает, что Telegram мог принять запрос без сохранённого подтверждения; после повтора возможен дубль. `duplicate_possible` остаётся true даже при последующем успехе. Только `TELEGRAM_ACCEPTED` с message ID подтверждает принятие Telegram, но не прочтение человеком. Отказ/истечение — `FAILED`. Website сохраняет существующие HTTP-состояния `queued/running/done/error/uncertain/not_configured`, без выдачи токенов или сырых ошибок.

Окно новой команды/доставки — 7 дней, допустимое будущее время — 5 минут. Знакомый request ID возвращает сохранённую квитанцию; неизвестная просроченная команда отклоняется. После завершения payload хранится 30 дней, затем ещё 30 дней остаётся tombstone квитанции/дедупликации. После окончания этого срока нет обещания бесконечной дедупликации; не создавайте новый request ID автоматически после неопределённости или истечения. Sender делает не более одной попытки в секунду; `429` даёт общую паузу, lease — 60 секунд, timeout Telegram — 10 секунд. Падение после отправки до записи результата не даёт гарантии exactly-once.

## Backup и восстановление SQLite

Не копируйте только живой `.sqlite`: WAL может содержать последние commit. Используйте реализованный SQLite backup API через CLI; он даёт согласованный snapshot и не запускает sender:

```sh
docker compose --env-file .env.relay -f compose.relay.yaml exec relay python -m internal_notifier.server backup /data/relay/notifications.backup.sqlite
umask 077
mkdir -p backups
docker compose --env-file .env.relay -f compose.relay.yaml cp relay:/data/relay/notifications.backup.sqlite backups/notifications.sqlite
chmod 600 backups/notifications.sqlite
```

Защитите backup, private JSON, source credential, bot token и TLS-ключ отдельно; даже минимальный payload может содержать внутренние ссылки. Проверьте восстановление на изолированном хосте без Telegram-доступа. Для реального восстановления остановите relay, сохраните текущий том и замените ledger согласованным backup в `/data/relay/notifications.sqlite`, с UID/GID 1000 и правами `0600`; WAL/SHM старой базы не переносите к восстановленному файлу. Сначала проверьте integrity и `check-ledger`, затем запускайте единственную инстанцию. Отдельный backup source outbox должен быть согласован по времени: старый snapshot ledger может потерять уже доставленное подтверждение и привести к дублю; это требует ручного разбора перед запуском sender.

Перед проверкой восстановленной копии без сети можно выполнить `PRAGMA integrity_check` стандартным Python sqlite3; CLI `check-ledger` проверяет возможность транзакционной записи, но не историю доставки. Обычный `down` сохраняет том; `down -v` запрещён для обновлений. При росте backlog/возрасте ожидающих/долгом отсутствии last_success проверьте TLS, права файлов, ACL и Telegram egress. Журналы содержат только notification ID, source, alias и безопасный result; не включайте логирование authorization, исходного текста или raw provider responses.
