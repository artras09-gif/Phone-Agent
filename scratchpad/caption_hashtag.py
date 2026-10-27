"""Подпись, которая кончается хэштегом (живьём 2026-10-22 на Redmi): TikTok
держит поверх формы список подсказок «#videoediting 1.1M публикаций», и
«Опубликовать» под ним не находилось — «не нашёл ФИНАЛЬНУЮ КНОПКУ». После
ввода такой подписи дописывается пробел: хэштег завершён, список уходит.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config  # noqa: E402

config.BASE = tempfile.mkdtemp(prefix="pa_hashtag_")
import device  # noqa: E402
import poster  # noqa: E402

ok = True
typed = []
device.type_text = lambda text: typed.append(text)

for caption, tail in [
    ("7 звуков 🔊\n\nt.me/motion_order\n\n#звукидлямонтажа #sfx #videoediting", [" "]),
    ("Описание и в конце упоминание @ease.motion", [" "]),
    ("Хэштеги в середине #sfx, а в конце ссылка t.me/motion_order", []),
    ("Хэштег и пробел в конце #sfx ", []),
]:
    typed.clear()
    line = poster.run_step({"op": "type", "value": "$caption"}, {"caption": caption},
                           config.BASE, 1)
    # Пробел — тем же вводом, одной строкой: второй ввод подряд переключал
    # клавиатуру ещё раз, и «назад» после него уходило приложению (2026-10-28).
    good = typed == [caption + "".join(tail)]
    ok &= good
    print(f"  {'OK ' if good else 'НЕТ'} {caption[-28:]!r}: одним вводом {len(typed)} раз, "
          f"конец {typed[-1][-3:]!r} — {line}")

print("\nИТОГ:", "всё сошлось" if ok else "ЕСТЬ ОШИБКИ")
sys.exit(0 if ok else 1)
