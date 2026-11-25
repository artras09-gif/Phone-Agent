"""Строгая проверка «это экран статистики?» на НАСТОЯЩИХ снимках — с моделью.

25.11 TikTok переделал TikTok Studio, маршрут снял главную «за 7 дней», и
самопочинка промолчала: модель на вопрос «есть число просмотров?» честно
ответила «да». Теперь статистика — только с переключателем периода, а
похожие экраны (главная Studio, профиль) названы модели прямо.

Запуск вручную, не в all_tests: нужны сеть, ключ модели и снимки с живого
телефона (они лежат в данных программы на ПК, в репозиторий их не кладём —
там цифры аккаунтов).

    PA_API_KEY=... python scratchpad/look_real_screens.py [папка analytics]

Чего нет на этом ПК — пропускается.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config  # noqa: E402

if not os.environ.get("PA_API_KEY"):
    print("нет PA_API_KEY — пропускаю")
    sys.exit(0)
config.VISION_ENABLED = True
config.VISION_PROVIDER = "api"
config.API_URL = os.environ.get("PA_API_URL", "https://api.deepseek.com/v1")
config.API_KEY = os.environ["PA_API_KEY"]
config.API_MODEL = os.environ.get("PA_API_MODEL", "deepseek-flash")

import statnav  # noqa: E402

ROOT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.environ.get("LOCALAPPDATA", ""), "PhoneAgent", "devices", "74eed241", "analytics")
CASES = [  # (что, файл, сеть, статистика ли)
    ("TikTok: главная Studio после переделки 25.11", "day/2026-11-24/tiktok.png", "tiktok", False),
    ("TikTok: аналитика за день", "day/2026-11-22/tiktok.png", "tiktok", True),
    ("TikTok: аналитика за день", "2026-11-20/tiktok.png", "tiktok", True),
    ("YouTube Studio: аналитика", "day/2026-11-24/youtube.png", "shorts", True),
    ("Instagram: статистика рабочего аккаунта", "day/2026-11-24/instagram.png", "reels", True),
    ("Instagram: профиль вместо статистики", "2026-11-20/instagram.png", "reels", False),
]
ok, seen = True, 0
for what, rel, net, want in CASES:
    path = os.path.join(ROOT, *rel.split("/"))
    if not os.path.isfile(path):
        print(f"  —   {what}: снимка нет ({rel})")
        continue
    seen += 1
    got = statnav.look(path, statnav.GOALS[net][0], net)
    good = got.get("статистика") == want
    ok &= good
    print(f"  {'OK ' if good else 'НЕТ'} {what}: {got}")
print("ВСЁ ХОРОШО" if ok else "ЕСТЬ ОШИБКИ", f"(снимков: {seen})")
sys.exit(0 if ok else 1)
