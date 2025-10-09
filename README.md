# Слайдкасты

Этот проект создает из PDF-файлов со слайдами и диктофонной записи видеофайлы, в которых видеоряд состоит из слайдов, а аудиодорожка - из записанного голоса.

## Требования

Необходимо, чтобы на компьютере были установлены:

- Python 3.8 или выше, с модулем `venv`,
- ffmpeg (для обработки аудио и видео).

## Установка

```bash
git clone https://github.com/pakuula/slidecast.git
cd slidecast
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Использование

```bash
cd slidecast
python3 -m venv .venv
source .venv/bin/activate
slidecast -p path/to/slides.pdf -a path/to/audio.mp3 -o path/to/output.mp4 \
    -t path/to/timeline.json -c path/to/cuts.json -w ./_cast_build
```

Командная строка:

```text
slidecast [-h] -p PDF -a AUDIO -t TIMELINE [-c CUTS] -o OUT [-w WORKDIR] [--dpi DPI] [--fps FPS] [-v] [--dry-run] [--keep-audio] [--keep-pdf]

Создание видеокаста: PDF-слайды + аудио + таймлайн + вырезки.

options:
  -h, --help            show this help message and exit
  -p PDF, --pdf PDF     Путь к PDF со слайдами
  -a AUDIO, --audio AUDIO
                        Путь к исходному аудио
  -t TIMELINE, --timeline TIMELINE
                        JSON-файл с метками времени переключения слайдов (формат: [12.3, 45.0, ...] или [{"t":12.3,"page":2}, ...])
  -c CUTS, --cuts CUTS  JSON-файл с вырезаемыми интервалами [[start,end], ...]
  -o OUT, --out OUT     Путь к финальному видео .mp4
  -w WORKDIR, --workdir WORKDIR
                        Рабочая директория (кэш изображений, очищенное аудио и т.п.)
  --dpi DPI             DPI рендера PDF в изображения (не используется)
  --fps FPS             FPS выходного видео (не используется)
  -v, --verbose         Подробный вывод
  --dry-run             Только проверка параметров, без выполнения
  --keep-audio          Не очищать аудио от вырезок, использовать файлы, сохраненные в workdir
  --keep-pdf            Не рендерить PDF, использовать файлы, сохраненные в workdir
```
