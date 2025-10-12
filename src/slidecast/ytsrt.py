#!/bin/env python3

# Скрипт для преобразования субтитров Ютюба из JSON в массив предложений с метками времени.
# Каждое предложение имеет время начала и текст.
# Предложения разбиваются по знакам окончания предложения: точка, вопросительный и восклицательный знаки.
# Если предложение не заканчивается знаком окончания предложения, то оно продолжается до конца видео.
# Если предложение начинается с \n, то этот символ игнорируется.
# Если сегмент содержит знак окончания предложения в непоследнем сегменте, то последующие сегменты 
# включаются в следующее предложение. Необходимо вычислить начало такого предложения из смещения 
# первого сегмента, относившегося к этому предложению.
# Если сегмент содержит только \n, то этот сегмент игнорируется.
# Если сегмент не содержит знаков окончания предложения, то предложение продолжается.

# Файл субтитров Ютюба имеет формат:
# {
#   "wireMagic": "pb3",
#   "pens": [ {
#
#   } ],
#   "wsWinStyles": [ {
#   
#   }, {
#     "mhModeHint": 2,
#     "juJustifCode": 0,
#     "sdScrollDir": 3
#   } ],
#   "wpWinPositions": [ {
#   
#   }, {
#     "apPoint": 6,
#     "ahHorPos": 20,
#     "avVerPos": 100,
#     "rcRows": 2,
#     "ccCols": 40
#   } ],
#   "events": [ {
#     "tStartMs": 0,
#     "dDurationMs": 11398180,
#     "id": 1,
#     "wpWinPosId": 1,
#     "wsWinStyleId": 1
#   }, {
#     "tStartMs": 11000,
#     "dDurationMs": 3719,
#     "wWinId": 1,
#     "segs": [ {
#       "utf8": "И",
#       "acAsrConf": 0
#     }, {
#       "utf8": " снова",
#       "tOffsetMs": 120,
#       "acAsrConf": 0
#     }, {
#       "utf8": " добрый",
#       "tOffsetMs": 440,
#       "acAsrConf": 0
#     }, {
#       "utf8": " вечер.",
#       "tOffsetMs": 719,
#       "acAsrConf": 0
#     } ]
#   }, {
#     "tStartMs": 18830,
#     "wWinId": 1,
#     "aAppend": 1,
#     "segs": [ {
#       "utf8": "\n"
#     } ]
#   }

# Выходной формат:
# [
#   {"t": 1234.5, "time": "00:20:34", "text": "Предложение."},
#   ...
# ]

from ast import List
import json
import sys
import re
import argparse
import time

def process_events(events):
    current_sentence: list[str] = []
    current_start_time: int = -1

    for event in events:
        if 'segs' not in event:
            continue
        
        tStartMs = event.get('tStartMs', 0)
        for seg in event['segs']:
            text = seg.get('utf8', '').strip()
            tOffsetMs = seg.get('tOffsetMs', 0)

            if text == "\n":
                continue

            if current_start_time == -1:
                current_start_time = tStartMs + tOffsetMs

            if text:
                current_sentence.append(text)

            if re.search(r'[.!?]', text):
                yield {
                    "t": current_start_time / 1000.0,
                    "time": time.strftime("%H:%M:%S", time.gmtime(current_start_time / 1000.0)),
                    "text": " ".join(current_sentence).strip()
                }
                current_sentence = []
                current_start_time = -1

    if current_sentence:
        yield {
            "t": current_start_time / 1000.0 if current_start_time != -1 else 0,
            "time": time.strftime("%H:%M:%S", time.gmtime(current_start_time / 1000.0)),
            "text": " ".join(current_sentence).strip()
        }

_help_string = """Process YouTube SRT JSON file.

Outputs sentences with start times in JSON format: list of records {t: float, time: 'HH:MM:SS'-str, text: str}."""

def main():
    parser = argparse.ArgumentParser(description=_help_string)
    parser.add_argument("input_file", help="Path to the input SRT JSON file.")
    args = parser.parse_args()

    with open(args.input_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    events = data.get('events', [])
    sentences = list(process_events(events))
    print(json.dumps(sentences, ensure_ascii=False, indent=2))
    
if __name__ == "__main__":
    main()