"""Текущее приложение — не из снимка последнего зависания (без телефона).

22.11: TikTok на секунду завис, Android положил ПОЛНУЮ копию `dumpsys window`
на момент зависания первым разделом («LAST ANR»), и `current_app` три
попытки подряд видел TikTok, хотя на экране был Instagram.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import adb  # noqa: E402

ok = True


def say(good, text):
    global ok
    ok &= bool(good)
    print(f"  {'OK ' if good else 'НЕТ'} {text}")


FULL = """WINDOW MANAGER LAST ANR (dumpsys window lastanr)
WINDOW MANAGER DISPLAY CONTENTS (dumpsys window displays)
  mCurrentFocus=Window{a836bbd u0 com.zhiliaoapp.musically/com.ss.SAAActivity}
WINDOW MANAGER DISPLAY CONTENTS (dumpsys window displays)
  mCurrentFocus=Window{fe3fc84 u0 com.instagram.android/com.instagram.android.activity.MainTabActivity}
"""
SECTION = """WINDOW MANAGER DISPLAY CONTENTS (dumpsys window displays)
  mCurrentFocus=Window{fe3fc84 u0 com.instagram.android/com.instagram.android.activity.MainTabActivity}
"""
asked = []


def shell(cmd, *a, **k):
    asked.append(cmd)
    return SECTION if cmd.strip() == "dumpsys window displays" else FULL


adb.shell = shell
say(adb.current_app()[0] == "com.instagram.android", f"на экране Instagram, а не TikTok из снимка ({asked})")
print("ВСЁ ХОРОШО" if ok else "ЕСТЬ ОШИБКИ")
sys.exit(0 if ok else 1)
