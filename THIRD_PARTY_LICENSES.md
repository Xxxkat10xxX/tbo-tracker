# Лицензии сторонних компонентов

Код tbo-tracker распространяется по лицензии MIT ([LICENSE](LICENSE)).
Ниже перечислены сторонние компоненты, их назначение и лицензии. Все лицензии допускают использование и распространение
в составе tbo-tracker, в том числе в коммерческом продукте. Версии сервера закреплены в [requirements.lock](requirements.lock).

## Сервер (Python)

| Пакет | Версия | Назначение | Лицензия |
|---|---|---|---|
| fastapi | 0.162.2 | HTTP API и схемы данных | MIT |
| starlette | 1.10.0 | основа FastAPI: маршруты, middleware, файлы | BSD-3-Clause |
| pydantic, pydantic_core | 2.14.3 / 2.50.4 | проверка входных данных | MIT |
| uvicorn | 0.54.0 | ASGI-сервер | BSD-3-Clause |
| SQLAlchemy | 2.1.8 | работа с базой данных (ORM) | MIT |
| psycopg, psycopg-binary | 3.3.8 | драйвер PostgreSQL | LGPL-3.0-only (подключается как библиотека без изменений) |
| python-multipart | 0.0.33 | загрузка файлов журналов | Apache-2.0 |
| openpyxl, et_xmlfile | 3.1.5 / 2.0.0 | чтение и запись Excel | MIT |
| pdfplumber, pdfminer.six | 0.11.10 / 20260107 | чтение таблиц из PDF | MIT |
| pypdfium2 | 5.15.1 | отрисовка PDF для pdfplumber | BSD-3-Clause, Apache-2.0 |
| pillow | 13.0.0 | зависимость pdfplumber и reportlab (изображения страниц) | MIT-CMU |
| reportlab | 4.5.1 | создание демонстрационного журнала движения узлов в PDF | BSD |
| argon2-cffi, argon2-cffi-bindings | 25.1.0 / 26.1.0 | хеширование паролей Argon2id | MIT |
| cryptography | 50.0.2 | криптографические примитивы | Apache-2.0 OR BSD-3-Clause |
| cffi, pycparser | 2.1.1 / 3.12 | привязки к C-библиотекам | MIT-0 / BSD-3-Clause |
| anyio, h11, idna, click, charset-normalizer | – | зависимости FastAPI и uvicorn | MIT / MIT / BSD-3-Clause / BSD-3-Clause / MIT |
| annotated-types, annotated-doc, typing-inspection, typing_extensions | – | типы для pydantic и FastAPI | MIT / MIT / MIT / PSF-2.0 |
| opentelemetry-api | 1.45.2 | зависимость FastAPI (интерфейс телеметрии, не используется) | Apache-2.0 |

Для разработки и тестов ([requirements-dev.txt](requirements-dev.txt)): pytest – MIT, httpx – BSD-3-Clause.

## Бортовой агент

Агент (`agent/`) написан на стандартной библиотеке Python 3.8+ и не устанавливает пакетов. На борту он использует ПО,
которое уже входит в образ Skyris Technic:

| Компонент | Назначение | Лицензия |
|---|---|---|
| ROS Noetic (rospy) | связь с MAVROS | BSD-3-Clause |
| MAVROS | мост ROS ⇄ MAVLink к PX4 | BSD-3-Clause / LGPL-3.0 / GPL-3.0 (по выбору) |
| simple_offboard (образ Technic) | команды `navigate`, `land` для программы на борту | открытый код Skyris, см. [docs.skyris.ru/technic6S](https://docs.skyris.ru/technic6S/) |
| PX4 Autopilot | полётный контроллер | BSD-3-Clause |

Скрипт `scripts/run_mission_mavlink.py` (необязательный) использует pymavlink – LGPL-3.0.

## Шрифты и медиа

| Компонент | Где | Лицензия |
|---|---|---|
| DejaVu Sans | `app/assets/fonts/` – кириллица в PDF | лицензия Bitstream Vera / DejaVu, текст – [app/assets/fonts/DejaVu-LICENSE.txt](app/assets/fonts/DejaVu-LICENSE.txt) |
| Иллюстрации бортов | `app/static/drones/*.svg` | созданы скриптом проекта `scripts/make_drone_images.py` |

Интерфейс не загружает внешние скрипты, стили, шрифты или карты: всё отдаётся самим сервером.
