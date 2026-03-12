# J2ME Reverse Explorer

Веб-приложение для реверс-инжиниринга Java ME (J2ME) игр из `.jar` и `.jad`.

## Возможности

- Загрузка JAR и разбор на интерактивные категории: графика, музыка, классы, текст, спрайты, палитры.
- Поддержка JAD с извлечением метаданных (`MIDlet-Name`, `MIDlet-Version`, `MIDlet-Jar-URL` и т.д.).
- Для JAD можно добавить companion JAR, чтобы сразу увидеть внутренности игры.
- Предпросмотр изображений, аудио и текстовых ресурсов прямо в браузере.
- Автоматическая декомпиляция `.class` через CFR (`/decompile`), с fallback в hex-дамп при ошибке.
- Просмотр бинарных файлов в hex прямо в интерфейсе.
- Аудиоанализ по сигнатурам (MIDI/WAV/MP3/AMR) даже для файлов без расширения.
- Плеер с Play/Stop/Volume/Progress и loop для игровых треков.
- Улучшенный интерфейс: адаптивная верстка для мобильных, подсветка выбранного файла, быстрые действия для `.class`.
- Ручной ввод разрешения экрана в панели 🧠 AI Анализ с пересчетом tilemap-гипотез без полной переразборки.

## Автоматическая подготовка окружения

При декомпиляции приложение автоматически:

1. Проверяет наличие Java (`java -version`).
2. Если Java отсутствует — пытается установить `default-jdk` через `apt-get`.
3. Если `cfr.jar` отсутствует — скачивает его в корень проекта.

Если Java/CFR недоступны, endpoint `/decompile` вернет ошибку и `hex_preview` вместо Java исходника.

## Запуск

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Откройте `http://localhost:8000`.


## J2ME JAR Rebuilder

Добавлен CLI-инструмент `j2me_jar_rebuilder.py` для bytecode-level переименования классов в обфусцированных JAR (без декомпиляции):

```bash
python j2me_jar_rebuilder.py game_obf.jar --output game_clean.jar --resolution 240x320
```

Выходные артефакты:
- `game_clean.jar`
- `mapping.txt`
- `structure.txt`
