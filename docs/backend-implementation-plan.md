# План тонкого клиента и двух режимов

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Backend в задачах 1–4 и обмен/размещение в задаче 6 ещё не реализованы; задача 5 выполнена частично: клиент API и редактор сайта уже подключены, Vue Router и сквозная приёмка с сервером остаются впереди.

**Goal:** перенести рабочие данные из демонстрационного browser storage на FastAPI и дать одному Vue-приложению публичный и административный режимы.

**Architecture:** FastAPI — модульный монолит; PostgreSQL хранит данные и outbox, постоянный media volume — изображения. Vue использует публичные/закрытые API; текущий дизайн и готовые шаблоны сохраняются. 1С подключается отдельным worker/адаптером после согласования конфигурации.

**Tech Stack:** реализованы Vue 3 + Vite и `node:test`; браузерные проверки используют временный Playwright. Целевой backend — FastAPI, PostgreSQL, SQLAlchemy 2, Alembic, pytest; Vue Router ещё предстоит.

**Spec:** [architecture.md](architecture.md), разделы «Тонкий клиент и два режима», «Контент и изображения сайта», «Приём заказа»; HTTP-схемы и ограничения — [api.openapi.yaml](api.openapi.yaml).

## Global Constraints

- Node.js ≥22.12; frontend остаётся Vue 3 + Vite. SEO и оплата отложены.
- Авторитетные данные и права — сервер; локального успешного заказа при ошибке API нет. В UI остаются формы, фильтры, черновик корзины `productId`/`quantity` и технический ключ повтора оформления без тела заказа/контактов.
- Публичный режим без обязательного входа. `manager` — заказы; `admin` — заказы, категории/оформление товара, сайт/публикации/медиа и обмен.
- Cookie `HttpOnly; Secure; SameSite=Strict; Path=/api`; CSRF/Origin для закрытых изменений, `If-Match` для существующих версионируемых записей.
- Медиа: JPG/PNG/WebP, ≤5 242 880 байт, ≤4096×4096, alt ≤240; декодирование и серверное имя. Публичная выдача только по актуальной видимой ссылке.
- Учётные товары, артикулы, цены и остатки принадлежат 1С; тексты/фотографии/категории витрины принадлежат CRM. Конструктора страниц нет.
- Все маршруты, имена полей и ответы берутся из OpenAPI; этот план не вводит альтернативный HTTP-контракт. Имена обработчиков соответствуют `operationId`.

## Review Focus

1. Прямой административный запрос без входа или с ролью `manager` — отказ на сервере, независимо от меню Vue (задача 1).
2. Два администратора меняют одну запись: старый `If-Match` не перезаписывает новую версию (задачи 2–3).
3. Публикация истекает/архивируется: её текст и неиспользуемый публично файл сразу исключаются из публичного API (задача 3).
4. Ошибка сохранения файла/БД или сбой деплоя не повреждают прежнюю фотографию; восстановление БД и файлов согласовано (задачи 3 и 6).
5. Оборвавшийся ответ оформления не создаёт дубль; API недоступен — форма и корзина сохраняются без ложного успеха (задачи 4–5).

## Task 1 Авторизация и граница режимов

**Files:** создать `backend/pyproject.toml`, `backend/app/main.py`, `backend/app/db.py`, `backend/app/auth/models.py`, `backend/app/auth/routes.py`, `backend/app/auth/dependencies.py`, `backend/alembic/`; тест `backend/tests/test_access.py`.

**Interfaces:** зависимости `get_current_staff() -> Staff` и `require_admin(staff: Staff) -> Staff`; `Staff` содержит серверные `id`/`role`. Сессионные HTTP-ответы используют `ManagerSession` из OpenAPI. Сессия и роль проверяются БД, а не параметром клиента.

- [ ] Написать тесты: без cookie закрытые запросы `401`; роль `manager` на `/admin/site` — `403`; `admin` проходит; подменённый browser role не влияет; выход отзывает сессию; неверный CSRF/Origin блокирует изменения. Проверить cookie на путях `/api/manager` и `/api/admin`.
- [ ] Запустить `pytest backend/tests/test_access.py -q`, получить FAIL до реализации.
- [ ] Создать FastAPI/DB/migrations и защищённую сессию по контракту; пароль первого admin передавать при серверном создании учётной записи, без пароля в исходниках.
- [ ] Повторить тест: PASS. Проверить отсутствие паролей, cookie, контактов и секретов 1С в логах/ошибках.

## Task 2 Серверный каталог и оформление товаров

**Files:** создать `backend/app/catalog/models.py`, `backend/app/catalog/routes.py`, `backend/app/catalog/service.py`, миграцию категорий/проекции/оформления; тест `backend/tests/test_catalog.py`.

**Interfaces:** `/categories`, `/catalog`, `/catalog/{productId}` и закрытые `/admin/categories`, `/admin/categories/{categoryId}`, `/admin/products`, `/admin/products/{productId}/presentation` по OpenAPI. Используют зависимости задачи 1 и `CatalogCategory`, `ProductPresentation`, `PublicCategory`, `Product`, `AdminProductSummary`.

- [ ] Тесты: поиск нормализованного артикула; скрытая категория и неактивный товар не выдаются публично, но находятся через `/admin/products` в новой сессии; ID сохраняется при переименовании; неизвестная категория `422`; дубль имени `409`; устаревший `If-Match` `412`; запрос изменения цены/остатка через presentation `422`; публичные `image`/ошибки не пропускают приватные поля/пути. Импорт не меняет website-поля.
- [ ] `pytest backend/tests/test_catalog.py -q` — FAIL до реализации.
- [ ] Реализовать проекцию и редакторские данные отдельно, команды с аудитом и версией, серверные фильтры/пагинацию. До подключения 1С использовать серверные тестовые fixtures с явным обозначением источника.
- [ ] Повторить тест: PASS, включая два конкурентных изменения категории.

## Task 3 Контент сайта и изображения

**Files:** создать `backend/app/site/models.py`, `backend/app/site/routes.py`, `backend/app/site/service.py`, `backend/app/media/models.py`, `backend/app/media/routes.py`, `backend/app/media/storage.py`, миграции и media volume; тесты `backend/tests/test_site.py`, `backend/tests/test_media.py`.

**Interfaces:** `/site`, `/admin/site`, `/admin/publications`, `/admin/publications/{publicationId}`, `/admin/media`, `/media/{mediaId}`, `/admin/media/{mediaId}/file`; схемы `SiteConfiguration`, `PublicSite`, `Publication`, `Media`, `PublicMedia`. Зависимости доступа — задача 1, ссылки на категории/товары — задача 2.

- [ ] Тесты сайта: черновик/архив/запись вне срока не попадают в публичный ответ; граница `startsAt <= now < endsAt`; неверный диапазон дат `422`; stale version `412`; заголовки/CTA возвращаются обычным текстом; скрытая цель меню/публикации не становится публичной ссылкой.
- [ ] Тесты медиа: поддельный MIME/недекодируемый файл и опасное имя не проходят; лимит байт/размеров; предпросмотр только admin; опубликованный файл доступен публично, после снятия последней публичной ссылки или деактивации единственного товара — `404`; ошибка БД/файла сохраняет прежнюю фотографию.
- [ ] `pytest backend/tests/test_site.py backend/tests/test_media.py -q` — FAIL до реализации.
- [ ] Реализовать шаблонные поля и публикацию; файл писать под серверным именем вне frontend build, затем сохранять метаданные/связь. Публичную выдачу проверять через API без обходной static-раздачи каталога загрузок; сироты очищать только после проверки ссылок.
- [ ] Повторить тесты: PASS, включая истечение публикации и файл, используемый другой публичной записью.

## Task 4 Заказ и транзакционная обработка

**Files:** создать `backend/app/orders/models.py`, `backend/app/orders/routes.py`, `backend/app/orders/service.py`, `backend/app/integration/outbox.py`, миграции; тест `backend/tests/test_orders.py`.

**Interfaces:** `/orders`, `/manager/orders`, `/manager/orders/{orderId}`, `/manager/orders/{orderId}/status`; `CreateOrderRequest`, `OrderReceipt`, `ManagerOrder`. Каталог/доступ — задачи 1–2. Заказ, строки, резерв, идемпотентность и outbox фиксируются одной транзакцией PostgreSQL.

- [ ] Тесты: серверная цена и целые копейки; скрытый товар не заказывается; гонка за последней единицей; тот же ключ/тело возвращает исходную квитанцию, иной payload `409`; публичный ответ без контактов; недопустимый переход и stale version; rollback всех записей при ошибке.
- [ ] `pytest backend/tests/test_orders.py -q` — FAIL до реализации.
- [ ] Реализовать правила из архитектуры; `confirmed` в рабочей системе только после подтверждения 1С. Производственный `POST /orders` при отсутствии настроенного обмена/свежей проекции возвращает `503`; для тестов использовать mock адаптера и успешный ответ `new`/`pending_sync` из контракта. `not_connected` остаётся состоянием существующего frontend-демо, не альтернативным производственным успехом.
- [ ] Повторить тест: PASS, включая повтор после потерянного HTTP-ответа.

## Task 5 Vue как клиент API

**Статус:** частично выполнена. API — источник по умолчанию, локальное демо выбирается явно. Готовы клиентские формы входа, каталога и редактор `#crm-content` с вкладками сайта/меню, публикаций и изображений; Vue Router ещё не подключён. Frontend упакован multi-stage Dockerfile и `compose.yaml`: демонстрационный образ запускается без backend, API-образ проксирует same-origin `/api` через Nginx к `API_PROXY_TARGET`. Инструкция переноса — в [README](../README.md#запуск-в-docker-и-перенос-на-другое-устройство). Серверная авторизация и хранение данных остаются в задачах 1–4; наличие форм и контейнера не означает готовность backend.

**Files:** реализованы `src/data/http-api.js`, `src/data/demo-api.js`, `src/data/demo-site.js`, `src/data/demo-content-api.js`, `src/data/demo-media.js`, `src/content.js`, `src/cart-draft.js`, `src/checkout-attempt.js`, `src/order-labels.js`, `src/components/ContentManager.vue`, `ContentTarget.vue`, `MediaSelector.vue`; обновлены `src/main.js`, `src/App.vue`, `src/components/LandingPage.vue`, `CatalogManager.vue`, настройки запуска и README. Проверки — `tests/*api.test.js`, `tests/content.test.js`, `tests/cart-draft.test.js`, `tests/checkout-attempt.test.js`, `tests/order-actions.test.js`, `tests/browser-smoke.mjs`, `tests/browser-api.mjs`. Далее подключить Vue Router по целевой схеме и расширить E2E для настоящего backend.

**Interfaces:** `createHttpApi()` и `createDemoApi()` предоставляют общий асинхронный интерфейс чтения сайта/категорий/каталога/товара, оформления, сессии, CRM и редактирования сайта/публикаций/медиа. HTTP-клиент использует same-origin `/api`, проверяет ответы и отправляет cookie, CSRF, `If-Match`, ключ оформления и multipart-загрузку по OpenAPI; контент ссылается на медиа по ID. `VITE_DATA_MODE=api|demo` необязательно переопределяет источник; `API_PROXY_TARGET` используется только dev-прокси. Только в демо JSON контента хранится в `localStorage` (`lavr.site.v1`), Blob файлов — в IndexedDB (`lavr-media-v1`). Данные задач 1–4 должны храниться на сервере, поля `image`/`websiteTitle` имеют согласованные fallback из `Product`.

- [x] Подключить API по умолчанию в `npm run dev`/`build`, оставить browser storage каталога/заказов только за явными `dev:demo`/`build:demo`. При ошибке API показывать ошибку и повтор, без demo fallback и локального успешного заказа.
- [x] Перенести поиск, фильтры, сортировку и пагинацию на источник API; читать главную через `getSite()` с сохранением дизайна и отдельной demo-фикстурой публикаций.
- [x] Подключить вход/проверку сессии/выход, роль `manager`/`admin`, закрытые заказы и формы создания категории/назначения товара; существующие изменения используют версию. Создание товара оставить только в демо до согласования адаптера 1С.
- [x] В API-режиме записывать в `localStorage` только черновик корзины `productId`/`quantity`, в `sessionStorage` — технический UUID/SHA-256 отпечаток попытки; контакты формы и загруженные API-данные держать в памяти. Очищать форму и отправленный черновик после подтверждённого ответа оформления, сохранять при ошибке.
- [x] Добавить доступный роли `admin` редактор `#crm-content`: настройки главной/меню, черновик/публикация/архив, даты, загрузка и выбор медиа. Подключить HTTP-контракт и локальное демо, сохранить дизайн лендинга, формы при ошибках и предупреждение о несохранённых изменениях.
- [ ] Подключить Vue Router по целевым маршрутам архитектуры. Серверная сессия определяет доступ; опубликованный контент становится виден другой сессии после серверного сохранения.
- [ ] Провести E2E против настоящего backend: публичный поиск/оформление, вход/выход и истечение сессии, роли/CSRF, прямой закрытый запрос, конкурентный `412`, сетевой сбой, отсутствие контактов в browser storage, публикация и изображение в новой сессии.
- [ ] Завершить приёмку с сервером: `npm test`, `npm run check`, обе сборки, браузерные сценарии, мобильная ширина и клавиатура. `tests/browser-smoke.mjs` предназначен для demo-сборки; `tests/browser-api.mjs` проверяет production API-сборку с перехваченными HTTP-ответами и не подтверждает работу FastAPI/PostgreSQL.

## Task 6 Обмен и рабочее размещение

**Files:** создать `backend/app/integration/adapter.py`, `backend/app/integration/worker.py`, тест `backend/tests/test_integration.py`, расширить существующий frontend-only `compose.yaml` серверными сервисами, добавить серверную конфигурацию окружения и резервного копирования; обновить README.

**Interfaces:** согласовать `upsertOrder(externalOrderId, eventId, payload)`/`getOrderByExternalId(externalOrderId)` с выбранной 1С; публичный frontend никогда не вызывает её напрямую. Административные health/sync/retry используют существующий OpenAPI и события задачи 4.

- [ ] Тесты: timeout после записи в 1С, повтор без дубля, истечение lease с неизвестным результатом, полный/частичный импорт, неизменность website-полей, отмена по правилам архитектуры. Проверить сохранность медиа после замены контейнера и восстановление БД вместе с файлами.
- [ ] `pytest backend/tests/test_integration.py -q` — FAIL до реализации адаптера; до выяснения 1С оставить этот шаг невыполненным.
- [ ] Реализовать worker/адаптер против тестовой базы, deploy с постоянными PostgreSQL/media volumes и HTTPS; не подменять отсутствующий контракт 1С набором случайных OData-записей.
- [ ] Полные API/браузерные проверки и пробное восстановление: PASS. Открывать рабочую CRM для реальных данных только после этого этапа.
