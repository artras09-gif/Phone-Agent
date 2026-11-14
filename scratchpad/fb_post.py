"""Публикация в Facebook Reels без телефона (маршрут снят живьём 2026-11-04).

Проверяет: Facebook — сеть постинга наравне с остальными («все» и «без фб»),
кнопка черновика бота подписана «Facebook», телефон без Facebook пропускает
пост с понятной строкой, а не сбоем; «Next» по месту не жмётся, пока на экране
галерея; в маршруте нет шагов истории и финальная кнопка одна.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config  # noqa: E402

config.BASE = tempfile.mkdtemp(prefix="pa_fbpost_")
import adb  # noqa: E402
import poster  # noqa: E402
import postspec  # noqa: E402
import scheduler  # noqa: E402
import telegram_bot  # noqa: E402
import ui  # noqa: E402

ok = True


def check(what, cond):
    global ok
    print(("  ок  " if cond else "  ОШИБКА  ") + what)
    ok = ok and bool(cond)


print("Facebook как сеть постинга")
check("«все сети» включают Facebook", "facebook" in postspec.parse("описание: текст")["targets"])
check("«без фб» убирает только Facebook",
      postspec.parse("куда: все кроме фб\nописание: текст")["targets"] == ["tiktok", "shorts", "reels", "threads"])
check("«в фейсбук» — только Facebook", postspec.parse("куда: фейсбук\nописание: x")["targets"] == ["facebook"])
check("окно знает сеть", scheduler.normalize_targets("facebook,tiktok") == "tiktok,facebook")
check("кнопка черновика бота — «Facebook», а не вторая «Reels»",
      telegram_bot.BUTTON_TITLES["facebook"] == "Facebook"
      and len(set(telegram_bot.BUTTON_TITLES.values())) == len(telegram_bot.TARGET_ORDER))

recipe = poster.load_recipes()["facebook"]
ops = [s["op"] for s in recipe["steps"]]
check("маршрут начинается с главной", ops[0] == "home")
final = recipe["steps"][ops.index("publish")]
check("финальная кнопка одна — «Share now»",
      ops.count("publish") == 1 and final["any"][0]["desc_exact"] == "Share now")
share = ui.Node({"content-desc": "Share now", "enabled": "true", "clickable": "false",
                 "bounds": "[260,2538][1228,2681]"})
story = ui.Node({"content-desc": "Share now to your story", "enabled": "true", "clickable": "true",
                 "bounds": "[0,2133][1280,2319]"})
check("видимая «Share now» без clickable находится по точной подписи", ui.find_any([story, share], final["any"]) is share)
check("«Share now to your story» не подходит для финальной кнопки", ui.find_any([story], final["any"]) is None)
words = " ".join(str(s) for s in recipe["steps"]
                 if s["op"] in ("tap", "optional_tap", "tap_first")).lower()
check("нигде, кроме финала, не жмётся «share» и «story»", "share now" not in words and "story" not in words)
xy = recipe["steps"][ops.index("tap_xy")]
check("«Next» по месту — только когда галерея ушла", xy.get("unless"))
check("Facebook помечен как необязательное приложение", recipe.get("optional_app") is True)

print("\nтелефон без Facebook")
answers = {}
adb.shell = lambda cmd, **k: answers.get(cmd.split()[-1], "")
answers["com.facebook.katana"] = ""
line = scheduler._missing_app("facebook")
check(f"приложения нет — пропуск: {line!r}", "пропускаю" in line)
answers["com.facebook.katana"] = "package:/data/app/base.apk"
check("приложение есть — публикуем", scheduler._missing_app("facebook") == "")
answers["com.zhiliaoapp.musically"] = ""
check("TikTok так не пропускается — его отсутствие остаётся сбоем", scheduler._missing_app("tiktok") == "")

print("\n«Next» по месту, пока на экране галерея")
gallery = [ui.Node({"bounds": "[0,698][426,1263]", "content-desc": "Create reel with Video, item 1, 00:09",
                    "class": "android.widget.Button", "clickable": "true"})]
taps = []
poster.ui.dump = lambda **k: gallery
poster.adb.tap = lambda x, y: taps.append((x, y))
poster.adb.screen_size = lambda: (1280, 2772)
poster.human.pause = lambda *a: None
poster._snap = lambda *a: None
poster._ensure_front = lambda *a: None
step = dict(xy, timeout=1)
try:
    poster.run_step(step, {"package": "com.facebook.katana"}, tempfile.mkdtemp(), 7)
    failed = False
except poster.StepFailed:
    failed = True
check("галерея не ушла — по месту НЕ жму, ошибка", failed and not taps)
poster.ui.dump = lambda **k: []
poster.run_step(step, {"package": "com.facebook.katana"}, tempfile.mkdtemp(), 7)
check("галерея ушла — жму «Next»", taps == [(1175, 2516)])

print("\nВСЁ СОШЛОСЬ" if ok else "\nЕСТЬ ОШИБКИ")
sys.exit(0 if ok else 1)
