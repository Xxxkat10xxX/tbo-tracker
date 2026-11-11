# Развёртывание на сервере (Docker)

Этот документ – для обычного сервера (Linux, amd64 или arm64): Docker Compose, PostgreSQL или SQLite, HTTPS через Caddy.

Для Raspberry Pi без Docker есть отдельная инструкция: [RASPBERRY_PI.md](RASPBERRY_PI.md).

Модель безопасности описана в [SECURITY.md](SECURITY.md).

## Варианты

| Вариант | Команда | Когда |
|---|---|---|
| SQLite, только локально | `docker compose up -d --build` | проба, один сервер без внешнего доступа |
| **PostgreSQL + HTTPS** (рекомендуется) | `docker compose -f compose.yaml -f compose.postgres.yaml -f compose.https.yaml up -d --build` | рабочая эксплуатация |
| SQLite + HTTPS | `docker compose -f compose.yaml -f compose.https.yaml up -d --build` | небольшой парк, один сервер |

Что есть во всех вариантах:

- **Контейнер приложения:**
  - работает от непривилегированного пользователя `uid 10001`;
  - корневая файловая система только для чтения;
  - все capabilities сброшены, `no-new-privileges`;
  - ограничения памяти и числа процессов.
- **Данные** – в именованных томах: `tbo-data` (SQLite, фото, копии, настройки сервера) и `tbo-pg` (PostgreSQL).
- **База PostgreSQL** не публикуется наружу.
- **Пароль базы** – Docker secret; в переменных окружения и в URL его нет.

## 1. Подготовка

На сервере нужны Docker Engine 24+ и плагин `docker compose`.

```bash
git clone https://github.com/DanilTeplo/tbo-tracker.git && cd tbo-tracker
```

```bash
sh deploy/docker/init.sh
```

`init.sh` делает две вещи:

- создаёт `.env` из `.env.example`;
- кладёт случайный пароль PostgreSQL в `secrets/db_password.txt`. Каталог `secrets/` закрыт правами 700 и не попадает в git.

Откройте `.env` и задайте свои значения:

| Переменная | Значение |
|---|---|
| `TBO_DOMAIN` | доменное имя сервера, например `tbo.example.com` |
| `TBO_TLS` | `acme` – бесплатный сертификат Let's Encrypt. Порты 80 и 443 должны быть доступны из интернета, у домена должна быть A-запись на сервер |
| | `internal` – собственный центр сертификации Caddy, для локальной сети без публичного домена |
| `TBO_DEMO_MODE` | `0` для рабочей эксплуатации. `1` включает демо-данные, сценарии и кнопку «Сбросить демо-данные», которая стирает базу |

## 2. Запуск

```bash
docker compose -f compose.yaml -f compose.postgres.yaml -f compose.https.yaml up -d --build
```

Через 30–60 секунд все контейнеры должны быть в состоянии `healthy`:

```bash
docker compose -f compose.yaml -f compose.postgres.yaml -f compose.https.yaml ps
```

## 3. Первый администратор

Паролей по умолчанию нет. При первом запуске пустой базы приложение печатает **одноразовый код настройки**:

```bash
docker compose -f compose.yaml -f compose.postgres.yaml -f compose.https.yaml logs app | grep -A1 "код настройки"
```

Дальше:

1. Откройте `https://<ваш домен>`.
2. Введите код, имя пользователя и пароль администратора.
3. Остальных сотрудников добавьте в разделе «Пользователи». Им задаётся временный пароль, при первом входе каждый задаёт свой.

Есть два других способа создать администратора.

**Из командной строки.** Пароль вводится скрыто:

```bash
docker compose -f compose.yaml -f compose.postgres.yaml exec app python -m app.admin create-user ivanov --role admin
```

**Из файла-секрета** (автоматизация). Добавьте в `.env` строку `TBO_ADMIN_PASSWORD_FILE=/run/secrets/admin_password` и подключите секрет к сервису `app`. Администратор `TBO_ADMIN_USERNAME` (по умолчанию `admin`) будет создан при первом запуске пустой базы. После этого файл удалите.

## 4. Повседневное администрирование

Короткий префикс для команд (подставьте свои файлы compose):

```bash
alias tbo='docker compose -f compose.yaml -f compose.postgres.yaml -f compose.https.yaml'
```

| Задача | Команда |
|---|---|
| Состояние | `tbo ps`, `tbo exec app python -m app.admin status` |
| Журнал | `tbo logs -f app` |
| Пользователи | `tbo exec app python -m app.admin users` |
| Забытый пароль | `tbo exec app python -m app.admin passwd ИМЯ --temporary` |
| Разблокировка после неудачных входов | `tbo exec app python -m app.admin unlock ИМЯ` |
| Резервная копия сейчас | `tbo exec app python -m app.admin backup` |
| Скачать копию на компьютер | страница «Сервер» → «Резервные копии» → «⇩» |
| Восстановление | «Сервер» → «Резервные копии» → «Восстановить» (или `tbo exec app python -m app.admin restore ИМЯ.zip`) |
| Обновление | `git pull && tbo up -d --build` (перед этим – резервная копия) |

Резервные копии:

- **Ежедневные** – автоматически в 03:00 (меняется на странице «Сервер»).
- **Формат** – zip с таблицами в JSON и фото. Он не зависит от СУБД, поэтому копия с Raspberry Pi на SQLite восстанавливается в PostgreSQL на сервере.
- **Хранение** – копии лежат в томе `tbo-data`. Регулярно скачивайте их на другой носитель.
- **Защита** – в копии есть хеши паролей, поэтому файлы созданы с правами 600, а скачивать их может только администратор.

## 5. Без HTTPS, в локальной сети

Задайте в `.env` значение `TBO_BIND=0.0.0.0` и запустите без `compose.https.yaml`. Сервис будет доступен по `http://<IP>:8000`.

> Пароли и данные при этом передаются по сети без шифрования. Страница «Сервер» напоминает об этом.

## 6. Перенос с Raspberry Pi или SQLite на PostgreSQL

1. На старом сервере: «Сервер» → «+ Создать копию» → «⇩».
2. На новом сервере: «Сервер» → «⇪ Загрузить .zip» → «Восстановить».
3. Все пользователи выйдут из системы. Учётные записи станут такими, какими были в копии.

## 7. Корпоративный прокси с подменой TLS

Если `pip` при сборке образа сообщает `CERTIFICATE_VERIFY_FAILED`, передайте корневой сертификат прокси как build-секрет. В образ он не попадает:

```bash
docker build --secret id=pip_ca,src=/путь/к/proxy-ca.pem -t tbo-tracker:latest .
```

После сборки запускайте `docker compose … up -d` без `--build`.

## Переменные окружения

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `TBO_DATABASE_URL` | SQLite в `TBO_DATA_DIR` | `postgresql+psycopg://user@host:5432/db` (без пароля) |
| `TBO_DATABASE_PASSWORD_FILE` | – | файл с паролем БД (Docker secret) |
| `TBO_PUBLIC_URL` | – | внешний адрес `https://…`: включает Secure-cookie, HSTS, проверку Origin |
| `TBO_TRUSTED_PROXIES` | – | IP или сети обратных прокси, которым доверяем `X-Forwarded-For` / `-Proto` |
| `TBO_DRONE_MAX_ALT_M` / `TBO_DRONE_MAX_DIST_M` | 120 / 2000 | пределы реального полёта: высота точек и удаление от точки взлёта (агент на борту проверяет и свои) |
| `TBO_DRONE_MAX_HOME_OFFSET_M` / `TBO_DRONE_MIN_BATTERY_PCT` | 50 / 50 | борт не дальше от точки взлёта миссии; минимальный заряд для старта |
| `TBO_DRONE_LOCAL_MAX_ALT_M` / `TBO_DRONE_LOCAL_MAX_DIST_M` | 3 / 20 | пределы локальной миссии в помещении (Optical Flow / ArUco): высота и удаление от точки взлёта; скорость – до 2 м/с |
| `TBO_DRONE_VIDEO_PORT` | 8080 | порт web_video_server на борту для «Видео с борта» (сервер берёт поток с адреса, с которого подключён агент); `0` – видео выключено |
| `TBO_DRONE_VIDEO_MAX` / `TBO_DRONE_VIDEO_MAX_MIN` | 3 / 30 | одновременных соединений с видеосерверами бортов (зрители одного потока – одно соединение); длительность одного просмотра, мин |
| `TBO_ALLOWED_HOSTS` | – (любые) | имена сайта через запятую, допустимые в заголовке `Host` (защита от DNS rebinding); `localhost` и имя из `TBO_PUBLIC_URL` разрешены всегда |
| `TBO_DEMO_MODE` | `0` | демо-данные, сценарии, «Сбросить демо-данные» |
| `TBO_SESSION_IDLE_MIN` / `TBO_SESSION_MAX_HOURS` | 120 / 12 | выход после бездействия / предельный срок сессии |
| `TBO_LOGIN_MAX_FAILS` / `TBO_LOGIN_LOCK_MIN` | 5 / 15 | блокировка после неудачных входов; срок удваивается при повторах |
| `TBO_AUDIT_RETENTION_DAYS` | 365 | хранение журнала аудита |
| `TBO_MAX_UPLOAD_MB` / `TBO_MAX_BACKUP_UPLOAD_MB` | 25 / 512 | лимиты загрузки |
| `TBO_ADMIN_PASSWORD_FILE`, `TBO_ADMIN_USERNAME` | –, `admin` | первый администратор из файла-секрета |
| `TBO_API_DOCS` | `0` | `/docs` (Swagger) – только администратору |
| `TBO_COOKIE_SECURE` | `auto` | `1` – всегда Secure-cookie (если HTTPS обеспечен иначе) |
| `TBO_HOST`, `TBO_PORT`, `TBO_DATA_DIR`, `TBO_LOG_LEVEL` | – | заданы в образе: `0.0.0.0`, `8000`, `/data`, `info` |

## Проверено

Сценарии проверены в Docker на amd64:

- SQLite;
- PostgreSQL 16;
- PostgreSQL + Caddy (`TBO_TLS=internal`);
- перенос копии SQLite → PostgreSQL;
- перезапуск контейнеров с сохранением данных.

Что проверялось в каждом из них:

- первичная настройка по коду;
- вход, CSRF, роли;
- резервная копия и восстановление;
- заголовки безопасности;
- в варианте с Caddy: cookie `__Host-…; Secure`, HSTS и правильный IP клиента в аудите.

Образ собирается из `python:3.12-slim-bookworm`. Для arm64 все зависимости ставятся из готовых пакетов.
