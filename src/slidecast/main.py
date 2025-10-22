#!/usr/bin/env python3
"""Slidecast: создание видеокаста из PDF-слайдов и аудиодорожки с таймлайном и вырезками.
Пример использования:
 python main.py \
   --pdf slides.pdf \
   --audio talk.mp3 \
   --timeline slides.json \
   --cuts cuts.json \
   --out videocast.mp4 \
   --workdir ./build \
   --verbose"""
# -*- coding: utf-8 -*-

import argparse
import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import List, Optional, Tuple, Union

import pymupdf  # PyMuPDF
from PIL import Image

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


@dataclass
class SlideChange:
    """Событие смены слайда.

    t: время (секунды) на ОРИГИНАЛЬНОЙ шкале (до вырезок)
    page: номер страницы PDF (1-based); None => по порядку
    """

    t: float  # time (seconds) on ORIGINAL timeline (before cuts)
    page: Optional[int]  # 1-based PDF page number; None => sequential


def load_json(path: str):
    """Загрузить JSON-файл."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def normalize_cuts(cuts: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """Sort, merge overlaps, drop zero/negative intervals."""
    segments = [(min(a, b), max(a, b)) for a, b in cuts if max(a, b) > min(a, b)]
    segments.sort()
    merged = []
    for s, e in segments:
        if not merged or s > merged[-1][1] + 1e-9:
            merged.append([s, e])
        else:
            merged[-1][1] = max(merged[-1][1], e)
    return [(s, e) for s, e in merged]


def total_cut_before(t: float, cuts: List[Tuple[float, float]]) -> float:
    """Total duration removed strictly before time t."""
    removed = 0.0
    for s, e in cuts:
        if e <= t:
            removed += e - s
        elif s < t < e:
            removed += t - s
        else:
            pass
    return removed


def adjust_timeline(
    changes: List[SlideChange], cuts: List[Tuple[float, float]], audio_orig_len: float
) -> List[Tuple[float, int]]:
    """
    Преобразовать оригинальные изменения слайдов -> скорректированные (после вырезок).
    - Если изменение попадает внутрь вырезанного интервала, оно «прижимается»
      к началу вырезанного участка (на НОВОЙ шкале).
    - Удаляются дубликаты/нестрого возрастающие моменты после прижатия.
    Returns list of (t_new, page).
    """
    cuts = normalize_cuts(cuts)

    adjusted = []
    for ch in changes:
        t0 = ch.t
        # Clip t0 to [0, audio_orig_len]
        t0 = max(0.0, min(audio_orig_len, float(t0)))

        # If inside a cut, snap to cut start
        for s, e in cuts:
            if s < t0 < e or abs(t0 - s) < 1e-9:
                t0 = s
                break

        shift = total_cut_before(t0, cuts)
        t_new = max(0.0, t0 - shift)
        adjusted.append((t_new, ch.page if ch.page is not None else -1))

    # Ensure strict monotonicity; remove non-increasing duplicates
    adjusted.sort(key=lambda x: x[0])
    cleaned = []
    last_t = -1e9
    for t, p in adjusted:
        if t > last_t + 1e-6:
            cleaned.append((t, p))
            last_t = t
        else:
            # drop or nudge by epsilon; safer to drop
            continue

    return cleaned


def render_pdf_to_images(pdf_path: str, out_dir: str, suffix: str = "png") -> List[str]:
    """Конвертировать страницы PDF в PNG-изображения в указанную директорию.

    Возвращает список путей к изображениям.
    DPI рассчитывается для высоты 720p (1280x720), с учётом реального размера страниц.

    :param pdf_path: путь к PDF-файлу
    :param out_dir: директория для сохранения изображений
    :param suffix: расширение файлов изображений (png, jpg и т.п.)
    :return: список путей к сгенерированным изображениям
    """
    os.makedirs(out_dir, exist_ok=True)
    doc = pymupdf.open(pdf_path)
    paths = []
    for i, page in enumerate(doc, start=1):  # type: ignore
        # Calculate DPI for 720p (1280x720). Assume 16:9 aspect ratio.
        # PDF page size in points (1 pt = 1/72 inch)
        rect = page.rect
        height_inch = rect.height / 72.0
        # Target: height = 720 pixels
        # dpi = pixels / inches
        dpi_720p = int(720 / height_inch)
        dpi = dpi_720p
        # dpi -> zoom: 72 dpi base
        zoom = dpi / 72.0
        mat = pymupdf.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        out_path = os.path.join(out_dir, f"slide_{i:03d}.{suffix}")
        pix.save(out_path)
        # Ensure RGB (MoviePy/PIL friendly)
        img = Image.open(out_path).convert("RGB")
        img.save(out_path, optimize=True, quality=95)
        logger.debug("Страница %d: файл %s; DPI %d", i, out_path, dpi)
        paths.append(out_path)
    doc.close()
    return paths


def parse_time_label(s: Union[int, float, str]) -> float:
    """Время может быть в формате числа секунд или строки SS.s, MM:SS.s или HH:MM:SS.s"""
    if isinstance(s, (int, float)):
        return float(s)

    # Если строка, парсим по формату
    parts = s.split(":")
    if len(parts) == 1:
        # Формат SS.s
        return float(parts[0])
    elif len(parts) == 2:
        # Формат MM:SS.s
        return float(parts[0]) * 60 + float(parts[1])
    elif len(parts) == 3:
        # Формат HH:MM:SS.s
        return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    raise ValueError("Unsupported time format.")


def parse_timeline(obj: Union[List, dict]) -> List[SlideChange]:
    """
    Accepts:
      - [12.3, 45.0, 78.5]
      - [{"t": 12.3, "page": 2}, ...]
    """
    changes: List[SlideChange] = []
    if isinstance(obj, list) and obj and isinstance(obj[0], (int, float, str)):
        # Simple list, pages sequential; page=None meaning sequential
        for i, t in enumerate(obj):
            changes.append(SlideChange(parse_time_label(t), i + 1))
    elif isinstance(obj, list):
        for it in obj:
            t = parse_time_label(it["t"])
            p = int(it["page"]) if "page" in it and it["page"] is not None else None
            changes.append(SlideChange(t, p))
    else:
        raise ValueError("Unsupported timeline JSON format.")
    return changes


def get_audio_length(audio_path: str) -> float:
    """Возвращает длину аудиофайла (секунды) через ffprobe."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        audio_path,
    ]
    result = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False
    )
    if result.returncode != 0:
        logger.error("ffprobe error: %s", result.stderr)
        raise RuntimeError("ffprobe failed")
    return float(result.stdout.strip())


def build_fragmenting_script(
    audio_path: str,
    fragments: List[Tuple[float, float]],
    workdir: str,
    target: Optional[str] = None,
) -> str:
    """
    Builds a shell script to extract and concatenate audio fragments using ffmpeg.
    If target is None, the output will be '<workdir>/cleaned_audio.<ext>'.
    """
    script_lines = ["#!/bin/bash", "set -e", ""]

    fragment_files = []
    file_ext = os.path.splitext(os.path.basename(audio_path))[1].lower().lstrip(".")
    if file_ext not in ["wav", "mp3", "aac", "m4a", "flac", "ogg"]:
        raise ValueError("Unsupported audio format for ffmpeg processing.")

    for i, (start, end) in enumerate(fragments):
        frag_file = os.path.join(workdir, f"frag_{i:03d}.{file_ext}")
        fragment_files.append(frag_file)
        script_lines.append(f"echo 'Extracting fragment {i}: {start:.3f} - {end:.3f}'")
        script_lines.append(
            f"ffmpeg -y -i '{audio_path}' -ss {start:.3f} -to {end:.3f} -c copy '{frag_file}'"
        )

    concat_list_path = os.path.join(workdir, "concat_list.txt")
    with open(concat_list_path, "w", encoding="utf-8") as f:
        for frag_file in fragment_files:
            f.write(f"file '{frag_file}'\n")

    cleaned_audio_path = target or os.path.join(workdir, f"cleaned_audio.{file_ext}")

    script_lines.append("")
    script_lines.append(f"echo 'Concatenating fragments into {cleaned_audio_path}'")
    script_lines.append(
        f"ffmpeg -y -f concat -safe 0 -i '{concat_list_path}' -c copy '{cleaned_audio_path}'"
    )
    script_lines.append("")

    return "\n".join(script_lines)


def cuts_to_fragments(
    cuts: List[Tuple[float, float]], audio_orig_len: float
) -> List[Tuple[float, float]]:
    """Преобразует список удаляемых фрагментов в список сохраняемых фрагментов."""
    fragments = []
    start = 0.0
    for s, e in cuts:
        if start < s:
            fragments.append((start, s))
        start = max(start, e)
        if start >= audio_orig_len:
            break
    if start < audio_orig_len:
        fragments.append((start, audio_orig_len))
    return fragments


def cut_paste_audio_script(
    source_audio: str,
    fragments: List[Tuple[float, float]],
    audio_file: str,
    workdir: str,
) -> str:
    """Создаёт скрипт для вырезки и склейки аудио-фрагментов.
    Возвращает путь к скрипту.

    :param source_audio: путь к исходному аудиофайлу
    :param fragments: список (start, end) фрагментов для сохранения
    :param audio_file: путь к результирующему аудиофайлу
    :param workdir: рабочая директория для временных файлов
    :return: путь к созданному скрипту
    """

    audio_dir = os.path.dirname(audio_file)
    os.makedirs(workdir, exist_ok=True)
    cleaning_script = build_fragmenting_script(
        source_audio, fragments, audio_dir, target=audio_file
    )
    logger.debug("FFmpeg script for cleaning: %s", cleaning_script)
    cleaning_script_path = os.path.join(workdir, "clean_audio.sh")
    with open(cleaning_script_path, "w", encoding="utf-8") as f:
        f.write(cleaning_script)
    os.chmod(cleaning_script_path, 0o755)
    return cleaning_script_path


def build_video_script(
    audio_path: str,
    slide_images: List[str],
    changes: List[Tuple[float, int]],
    target: str,
    workdir: str,
    fps: int = 30,  # pylint: disable=unused-argument
) -> str:
    """
    Создаёт скрипт для создания видео из изображений слайдов и аудио с помощью ffmpeg.
    Возвращает текст скрипта.
    :param audio_path: путь к аудиофайлу
    :param slide_images: список путей к изображениям слайдов
    :param changes: список кортежей (время, страница) для переключения слайдов
    :param target: путь к выходному видеофайлу
    :param workdir: рабочая директория для временных файлов
    :param fps: количество кадров в секунду для выходного видео (не используется)
    :return: текст скрипта
    """

    script_lines = ["#!/bin/bash", "set -e", ""]

    # Create a temporary directory for intermediate files
    temp_dir = workdir
    os.makedirs(temp_dir, exist_ok=True)

    # Create a text file listing the images and their durations
    img_list_path = os.path.join(temp_dir, "img_list.txt")
    with open(img_list_path, "w", encoding="utf-8") as f:
        if changes and changes[0][0] > 0:
            # Initial duration before first change
            f.write(f"file '{slide_images[0]}'\n")
            f.write(f"duration {changes[0][0]:.3f}\n")
        for i, (t, p) in enumerate(changes):
            if p < 1 or p > len(slide_images):
                logger.warning("Invalid page number %d at time %.2f, skipping", p, t)
                continue
            next_time = changes[i + 1][0] if i + 1 < len(changes) else None
            image = slide_images[p - 1]
            if next_time is not None:
                duration = next_time - t
                duration = max(0.0, duration)

                f.write(f"file '{image}'\n")
                f.write(f"duration {duration:.3f}\n")
            else:
                # Last change, no next_time
                f.write(f"file '{image}'\n")
        # Add the last image to cover until the end of the audio
        if changes:
            final_page = changes[-1][1]
            if final_page >= 1 and final_page <= len(slide_images):
                f.write(f"file '{slide_images[final_page-1]}'\n")
    logger.debug("Image list file for ffmpeg created at: %s", img_list_path)
    logger.debug(
        "Image list for ffmpeg:\n%s", open(img_list_path, "r", encoding="utf-8").read()
    )
    script_lines.append("echo 'Creating video from images...'")
    script_lines.append(
        # f"ffmpeg -y -f concat -safe 0 -i '{img_list_path}' -vsync vfr -pix_fmt yuv420p -r {fps} '{os.path.join(temp_dir, 'video_no_audio.mp4')}'" # pylint: disable=line-too-long
        f"ffmpeg -y -f concat -safe 0 -i '{img_list_path}'"
        # f" -vf 'pad=ceil(iw/2)*2:ceil(ih/2)*2' -pix_fmt yuv420p"
        # f" -r {fps}"
        # f"  -vsync vfr"
        " -r 1"
        " -c:v libx264 -preset ultrafast -crf 28 -g 300 -sc_threshold 0 -x264-params 'keyint=300:min-keyint=300:no-scenecut=1'"  # pylint: disable=line-too-long
        " -threads 0"
        f" '{os.path.join(temp_dir, 'video_no_audio.mp4')}'"
    )
    script_lines.append("")
    script_lines.append("echo 'Merging video with audio...'")
    script_lines.append(
        f"ffmpeg -y -i '{os.path.join(temp_dir, 'video_no_audio.mp4')}' "
        f"-i '{audio_path}' -c:v copy -c:a copy '{target}'"
    )
    script_lines.append("")
    return "\n".join(script_lines)


def run_script(script_path: str, verbose: bool = False):
    """Executes a shell script located at script_path."""
    result = subprocess.run(
        ["bash", script_path],
        stdout=sys.stdout if verbose else subprocess.PIPE,
        stderr=sys.stderr if verbose else subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        logger.error(
            "Script %s failed with error: %s\nLog: %s",
            script_path,
            result.stderr,
            result.stdout,
        )
        raise RuntimeError(f"Script {script_path} failed")
    logger.info("Script %s executed successfully.", script_path)
    logger.debug("Script output: %s", result.stdout)


def main():
    """Главная функция.

    Выполняет разбор аргументов, проверяет наличие файлов, загружает таймлайн и вырезки,
    преобразует PDF в PNG, генерирует и выполняет скрипты для удаления фрагментов из аудио
    и наложения изображений слайдов на аудио"""

    # 1) Разбор аргументов
    ap = argparse.ArgumentParser(
        description="Создание видеокаста: PDF-слайды + аудио + таймлайн + вырезки."
    )
    ap.add_argument("-p", "--pdf", required=True, help="Путь к PDF со слайдами")
    ap.add_argument("-a", "--audio", required=True, help="Путь к исходному аудио")
    ap.add_argument(
        "-t",
        "--timeline",
        required=True,
        help=(
            "JSON-файл с метками времени переключения слайдов "
            ' (формат: [12.3, 45.0, ...] или [{"t":12.3,"page":2}, ...])'
        ),
    )
    ap.add_argument(
        "--skew", type=float, default=0.0, help="Смещение всех меток времени (секунды)"
    )
    ap.add_argument(
        "-c",
        "--cuts",
        required=False,
        default=None,
        help="JSON-файл с вырезаемыми интервалами [[start,end], ...]",
    )
    ap.add_argument("-o", "--out", required=True, help="Путь к финальному видео .mp4")
    ap.add_argument(
        "-w",
        "--workdir",
        default="./_cast_build",
        help="Рабочая директория (кэш изображений, очищенное аудио и т.п.)",
    )
    ap.add_argument(
        "--dpi",
        type=int,
        default=200,
        help="DPI рендера PDF в изображения (не используется)",
    )
    ap.add_argument(
        "--fps", type=int, default=30, help="FPS выходного видео (не используется)"
    )
    ap.add_argument("-v", "--verbose", action="store_true", help="Подробный вывод")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Только проверка параметров, без выполнения",
    )
    ap.add_argument(
        "--keep-audio",
        action="store_true",
        help="Не очищать аудио от вырезок, использовать файлы, сохраненные в workdir",
    )
    ap.add_argument(
        "--keep-pdf",
        action="store_true",
        help="Не рендерить PDF, использовать файлы, сохраненные в workdir",
    )

    args = ap.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s:%(levelname)s:%(name)s: %(message)s",
        )
    else:
        logging.basicConfig(
            level=logging.INFO, format="%(asctime)s:%(levelname)s:%(name)s: %(message)s"
        )

    args.audio = os.path.abspath(args.audio)
    if not os.path.isfile(args.audio):
        logger.error("Аудиофайл не найден: %s", args.audio)
        exit(1)
    audio_ext = os.path.splitext(args.audio)[1].lower().lstrip(".")
    if audio_ext not in ["wav", "mp3", "aac", "m4a", "flac", "ogg"]:
        logger.error("Неподдерживаемый формат аудио: %s", audio_ext)
        exit(1)

    args.pdf = os.path.abspath(args.pdf)
    if not os.path.isfile(args.pdf):
        logger.error("PDF-файл не найден: %s", args.pdf)
        exit(1)
    args.timeline = os.path.abspath(args.timeline)
    if not os.path.isfile(args.timeline):
        logger.error("Файл таймлайна не найден: %s", args.timeline)
        exit(1)
    if args.cuts:
        args.cuts = os.path.abspath(args.cuts)
        if not os.path.isfile(args.cuts):
            logger.error("Файл вырезок не найден: %s", args.cuts)
            exit(1)

    args.workdir = os.path.abspath(args.workdir)
    args.out = os.path.abspath(args.out)
    os.makedirs(args.workdir, exist_ok=True)
    logger.info("Рабочий каталог: %s", args.workdir)

    # 2) Загрузка таймлайна
    timeline_raw = load_json(args.timeline)
    logger.debug("Таймлайн (сырой): %s", timeline_raw)
    changes = parse_timeline(timeline_raw)
    if args.skew != 0.0:
        for ch in changes:
            ch.t += args.skew
    changes.sort(key=lambda x: x.t)
    logger.info("Таймлайн загружен: %d слайдов", len(changes))

    # 3) Загрузка и нормализация вырезок
    cuts = []
    if args.cuts:
        cuts_json = load_json(args.cuts)
        cuts = [(parse_time_label(a), parse_time_label(b)) for a, b in cuts_json]
        cuts = normalize_cuts(cuts)
    if args.skew != 0.0:

        def map_cut(cut):
            return (cut[0] + args.skew, cut[1] + args.skew)

        cuts = list(map(map_cut, cuts))
    logger.info("Вырезки загружены: %d интервалов", len(cuts))
    logger.debug("Вырезки: %s", cuts)
    logger.debug("Получаем длину аудио...")
    audio_orig_len = get_audio_length(args.audio)
    logger.info("Длина исходного аудио: %.2f секунд", audio_orig_len)

    # 4) Прочистка аудио
    if cuts:
        fragments = cuts_to_fragments(cuts, audio_orig_len)
        logger.debug("Фрагменты для сохранения: %s", fragments)
        audio_dir = os.path.join(args.workdir, "audio")
        audio_file = os.path.join(audio_dir, f"cleaned_audio.{audio_ext}")
        if args.keep_audio and os.path.isfile(audio_file):
            logger.info("Используется существующий очищенный аудиофайл: %s", audio_file)
            if not os.path.isfile(audio_file):
                logger.error("Файл не найден: %s", audio_file)
                exit(1)
        else:
            os.makedirs(audio_dir, exist_ok=True)
            cleaning_script_path = cut_paste_audio_script(
                args.audio, fragments, audio_file, audio_dir
            )
            logger.info(
                "Выполняется скрипт для очистки аудио: %s", cleaning_script_path
            )
            if not args.dry_run:
                run_script(cleaning_script_path, args.verbose)
    else:
        logger.info("Вырезки не заданы, очистка аудио не требуется.")
        audio_file = args.audio
    if args.dry_run:
        logger.warning("Dry run: аудио не очищается, используется оригинал.")
        clean_audio_duration = audio_orig_len
    elif audio_file != args.audio:
        clean_audio_duration = get_audio_length(audio_file)
    else:
        clean_audio_duration = audio_orig_len
    logger.info("Длина очищенного аудио: %.2f секунд", clean_audio_duration)

    logger.debug("Позиции слайдов до корректировки: %s", changes)
    adjusted_changes = adjust_timeline(changes, cuts, audio_orig_len=audio_orig_len)
    logger.debug("Позиции слайдов после корректировки: %s", adjusted_changes)

    # 5) Рендер PDF в изображения
    logger.info("Запускается обработка слайдов: %s", args.pdf)
    slides_dir = os.path.join(args.workdir, "slides")
    os.makedirs(slides_dir, exist_ok=True)
    if not args.dry_run:
        if args.keep_pdf:
            logger.info("Используются существующие слайды из: %s", slides_dir)
            slide_imgs = sorted(
                [
                    os.path.join(slides_dir, fname)
                    for fname in os.listdir(slides_dir)
                    if fname.lower().endswith(".png")
                ]
            )
        else:
            slide_imgs = render_pdf_to_images(args.pdf, slides_dir)
            logger.info(
                "Слайды обработаны: %d страниц, каталог %s", len(slide_imgs), slides_dir
            )
        logger.debug("Слайды: %s", slide_imgs)
    else:
        # dry run
        slide_imgs = [
            os.path.join(slides_dir, f"slide_{i:03d}.png")
            for i in range(1, len(timeline_raw))
        ]
        logger.info(
            "Dry run: слайды не рендерятся, предполагается %d страниц", len(slide_imgs)
        )

    # 6) создание скрипта для объединения слайдов с аудиодорожкой
    video_dir = os.path.join(args.workdir, "video")
    os.makedirs(video_dir, exist_ok=True)

    target = os.path.join(video_dir, "final_video.mp4")
    video_script = build_video_script(
        audio_file, slide_imgs, adjusted_changes, target, video_dir, args.fps
    )
    video_script_path = os.path.join(video_dir, "build_video.sh")
    logger.debug("FFmpeg script for video building:\n %s", video_script)
    with open(video_script_path, "w", encoding="utf-8") as f:
        f.write(video_script)
    os.chmod(video_script_path, 0o755)
    logger.info("Запускается скрипт для сборки видео: %s", video_script_path)
    if not args.dry_run:
        run_script(video_script_path, verbose=args.verbose)
        if not os.path.isfile(target):
            logger.error("Не удалось создать видео: %s", target)
            exit(1)
        # Перемещаем в целевой файл
        os.replace(target, args.out)
    logger.info("Результат сохранён: %s", args.out)


if __name__ == "__main__":
    # Пример использования
    # python main.py \
    #   --pdf slides.pdf \
    #   --audio talk.mp3 \
    #   --timeline slides.json \
    #   --cuts cuts.json \
    #   --out videocast.mp4 \
    #   --workdir ./build \
    #   --verbose
    main()
