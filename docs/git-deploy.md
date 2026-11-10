# Ручная выкладка из Git

Приложение получает сам VPS из public
`https://github.com/Artem-Ivanov/lavr-crm.git`. Push не запускает выкладку.
Вручную передаётся полный SHA коммита, доступного в `origin/master`:

```sh
./scripts/deploy.sh 1ec0717f5c58e141a08015b204615709d17877b9
```

Без аргумента команда берёт текущий локальный HEAD. Wrapper использует строгую
проверку закреплённого SSH host key и вызывает на VPS
`python3 /home/deploy/lavr-crm-deploy/deploy.py <SHA>`. Приложение и Docker-образы
по SSH не пересылаются. Контроллер устанавливается отдельно из проверенного
`ops/deploy.py`; сам себя из очередного релиза он не обновляет.

## Однократная подготовка

Нужны Python 3.11+, Git, Docker и Compose с уже работающим production-проектом
`lavr-crm`. Runtime-каталог `/home/deploy/lavr-crm` должен быть чистым Git checkout
принятого production SHA с указанным HTTPS origin. Первую привязку исторического
пакетного релиза к Git предварительно проверяет оператор по исходным файлам;
контроллер не угадывает, какой коммит соответствует работающим образам.

Сохраните существующие `.env.docker`, `private/notifier-ca.crt` и
`compose.notifier.json` вне Git; добавьте их в `.git/info/exclude`. Существующие
`compose.yaml`, `compose.https.yaml` и `docker/Caddyfile` должны совпадать с Git.
Ничего не переносите в новый runtime-каталог: существующие bind mounts и volumes
остаются на своих местах. Отдельный каталог `/home/deploy/lavr-crm-deploy`
содержит проверенный `deploy.py`, приватный `state.json` и lock. До первого
успешного запуска `state.json` не создавайте вручную.

## Что делает команда

1. Берёт lock, проверяет чистоту tracked файлов, origin и полный SHA после fetch.
   Коммит вне master, приватные runtime-файлы в Git и изменения
   migrations/bootstrap/entrypoint/production Compose/Caddy отклоняются.
2. Создаёт отдельный detached worktree в приватном
   `/home/deploy/lavr-crm-releases/git-<UTC>-<SHA-prefix>/source` и последовательно
   собирает backend/frontend штатными Dockerfile на VPS. Frontend всегда `api`.
   Worktree получает стандартные Git modes 0644/0755; его родитель остаётся 0700.
   После backend build отдельный контейнер UID 1000 без сети, volumes и записи
   в root filesystem проверяет `create_app()` до maintenance. Он получает только
   Compose backend env через временный mode-0600 файл, удаляемый в `finally`;
   код проверки не подключается к БД и не запускает миграции/bootstrap.
   Используется обычный build cache, без `--pull`/`--no-cache`. Текущий runtime
   продолжает работать. До сборки нужно минимум 5 GiB свободного места; перед
   backup проверяется запас с учётом прежних образов, БД и media.
3. Проверяет сохранность контейнеров и конфигурации. Сохраняет Caddy runtime JSON,
   включает 503 с прежними host matchers и gracefully останавливает только
   `frontend`, `catalog-imports`, `backend`. Imports должны быть idle.
4. Делает приватные резервные копии exact прежних образов, Git source, env/Compose/
   CA/Caddy, PostgreSQL custom dump и media. Проверяет dump через
   `pg_restore --list`, читает media archive и считает inventory содержимого;
   сохраняет hashes/sizes в `receipt.json`.
5. Переключает live Git checkout на SHA, переназначает image aliases и запускает
   только `backend catalog-imports frontend` через три существующих Compose input
   с `--no-deps --no-build --pull never`. PostgreSQL, HTTPS и messaging не
   пересоздаются. Их container ID/start time/restart count, mounts и все
   Compose-provided env значения должны сохраниться. Дополнительные стандартные
   env нового base image, например PATH, в это сравнение не входят.
6. Ждёт healthy backend/frontend и работающий worker с deadline 180 секунд.
   Worker имеет отключённый HTTP healthcheck. На первой успешной выкладке также
   сравнивает содержимое всех public таблиц и media до/после запуска, пока maintenance
   закрывает публичный доступ. Возвращает сохранённый Caddy runtime и проверяет
   HTTPS `/`, `/healthz`, `/api/catalog` без записи данных.

Перед остановкой worker ещё раз проверяется idle imports; его stop deadline
10 секунд, поскольку Python PID 1 может игнорировать SIGTERM. Для frontend/backend
сохраняется 150 секунд на завершение HTTP-запросов. Busy imports блокируют stop.

Первый запуск собирает и выкладывает образы даже при совпадении Git HEAD с SHA.
Повтор последнего **успешного** SHA проверяет состояние/health/HTTPS и возвращает
`unchanged`; сборки и перезапуска не выполняются.

## Ошибки и откат

После начала остановки ошибка вызывает возврат старого Git SHA и exact старых
образов тех же трёх сервисов. Если прежний worker имел более старый образ, он
запускается отдельно до возврата backend alias. Перед остановкой при откате
maintenance включается заново, включая ошибку после открытия публичного трафика.
После health возвращается прежний Caddy runtime. **БД и media автоматически не
восстанавливаются**: новые легитимные данные не должны исчезать при откате кода.

Стандартный вывод содержит короткие progress phases (build, maintenance, rollback),
status/SHA/путь receipt либо короткую причину ошибки. Подробный вывод команд,
включая возможные секреты из Compose, сохраняется
в mode-0600 `commands.log` внутри приватного каталога; не публикуйте этот файл.
`state.json` указывает последний receipt, коммит и exact image IDs. Failed build
оставляет production работающим и сохраняет worktree/log для диагностики.

При `rollback_failed` maintenance повторно включается по возможности. Не
повторяйте deploy вслепую: незавершённое состояние блокирует следующие запуски.
При обрыве процесса/питания проверьте `state.json`, его `receipt.json`, Caddy и
контейнеры. Для ручного code-only recovery используйте `old_commit`, `old_images`
и сохранённый Caddy JSON из receipt; не восстанавливайте dump поверх живой БД.
Обычный SSH-сеанс не даёт гарантии продолжения при потере питания/SIGKILL.

## Границы

- Это code-only путь. Нужна существующая revision `0008` и bootstrap sentinels;
  стандартные Alembic/bootstrap при запуске допускаются только как no-op.
  Изменения схемы/startup/deployment требуют отдельной проверенной процедуры.
- Перезапуск import worker сохраняет обычную retention policy: он может удалять
  старые PDF/строки. Первая строгая проверка данных отклонит такой drift и откатит
  код, но удалённые retention данные автоматически не вернёт. На следующих
  выкладках нормальные фоновые записи не сравниваются с прежними hashes таблиц.
- SHA фиксирует source. Floating Docker `FROM` tags и transitive pip dependencies
  могут дать новый runtime при повторной сборке; гарантированный откат использует
  сохранённые image IDs, не пересборку старого SHA.
- `pg_restore --list` подтверждает читаемость dump, не полное восстановление.
  Backups остаются на том же VPS. Offsite backup и restore rehearsal отдельны.
  Контроллер ничего не удаляет: worktrees/образы/backup со временем требуют
  осмысленной очистки с сохранением нужного rollback.

Локальная проверка контроллера без Docker/SSH:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p test_deploy.py -v
```

Тесты используют настоящий временный Git repository, а Docker/Caddy/БД заменяют
на границе процесса. Они проверяют SHA/master/scope, ENV, maintenance, первый
build/повторный no-op и отказ health с возвратом Git и прежних image IDs.
