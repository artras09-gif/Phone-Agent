"""Агент НЕ МОЖЕТ ничего отправить или опубликовать вне маршрута публикации.

Написано 2026-08-30 после того, как в личном аккаунте появилась история,
которую никто не выкладывал. Доказать конкретный тап не удалось, но дыр было
две, обе мои:

* фиксированная координата «поделиться» у Reels попадала на живом экране
  то в «Комментарий», то в «Ещё» — правая колонка съезжает от длины подписи;
* запасной выбор строки «по месту» работал во всех сетях, а не только в
  YouTube, где он и проверен.

Здесь закрепляются обе преграды: список запретных слов и признак экрана
публикации, на котором нельзя тыкать вслепую.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import session  # noqa: E402
import ui  # noqa: E402

ok = True


def check(name, cond, got=""):
    global ok
    ok = ok and bool(cond)
    print(("  OK  " if cond else " ПРОВАЛ ") + name + (f"  [{got}]" if got else ""))


def node(label, click=True, box=(0, 0, 100, 100)):
    x1, y1, x2, y2 = box
    return ui.Node({"content-desc": label, "text": label,
                    "clickable": "true" if click else "false",
                    "enabled": "true", "bounds": f"[{x1},{y1}][{x2},{y2}]"})


# Экран публикации Reels — снят живьём 2026-08-30 (шаг 15 сухого прогона).
COMPOSER = [
    node("Новое видео Reels", False, (170, 120, 570, 170)),
    node("OK", True, (800, 120, 860, 170)),
    node("Отметить людей", True, (44, 480, 900, 540)),
    node("Ваша история", True, (44, 1680, 860, 1720)),
    node("Сохранить черновик", True, (36, 1750, 400, 1840)),
    node("Поделиться", True, (467, 1750, 865, 1840)),
]


def main():
    print("== запретные кнопки ==")
    for label in ("Добавить в историю", "Ваша история", "Отправить",
                  "Опубликовать", "Репост", "Поделиться в ленте",
                  "Эльмар Чат не выбран", "Add to story", "Send"):
        check(f"не жмём «{label}»", session._is_sending(node(label)))

    print("\n== разрешённые ==")
    for label in ("Копировать ссылку", "Коп. ссылку", "Ссылка", "Copy link", ""):
        check(f"жать можно «{label or 'без подписи'}»",
              not session._is_sending(node(label)))

    print("\n== экран публикации ==")
    check("опознан как экран составления", ui.looks_like_composer(COMPOSER))
    tapped = []
    real_tap = ui.tap_node
    ui.tap_node = lambda n: tapped.append(n)
    try:
        got = ui.dismiss_popup(COMPOSER)
    finally:
        ui.tap_node = real_tap
    check("dismiss_popup на нём НИЧЕГО не нажал", got is False and not tapped,
          f"вернул {got}, нажатий {len(tapped)}")

    print("\n== обычное окно по-прежнему закрывается ==")
    popup = [node("Разрешить доступ к контактам?", False, (100, 900, 980, 960)),
             node("Не разрешать", True, (100, 1000, 500, 1080)),
             node("Разрешить", True, (520, 1000, 980, 1080))]
    tapped = []
    ui.tap_node = lambda n: tapped.append(n)
    try:
        got = ui.dismiss_popup(popup)
    finally:
        ui.tap_node = real_tap
    check("окно закрыто", got is True and len(tapped) == 1)
    check("нажали именно ОТКАЗ, а не «Разрешить»",
          tapped and (tapped[0].text or "") == "Не разрешать",
          tapped[0].text if tapped else "ничего")

    print("\n== «ОК» больше не жмётся вслепую ==")
    check("«ОК» убрано из списка автозакрытия",
          "ОК" not in ui.DISMISS_LABELS and "OK" not in ui.DISMISS_LABELS)
    check("«Понятно» и «Закрыть» остались",
          "Понятно" in ui.DISMISS_LABELS and "Закрыть" in ui.DISMISS_LABELS)

    print("\n== escape на экране публикации ==")
    import escape as esc
    check("экран признан опасным", esc.risky_screen(COMPOSER))
    menu = esc._menu(COMPOSER, 1080, 2400)
    names = [(n.desc or n.text or "") for n in menu]
    check("модели не предлагается «Поделиться»",
          not any("Поделиться" in n for n in names), ", ".join(names) or "пусто")
    check("не предлагается «Ваша история»",
          not any("история" in n.lower() for n in names))
    check("не предлагается «Сохранить черновик»",
          not any("черновик" in n.lower() for n in names))
    check("не предлагается «OK»",
          not any(n.strip().lower() == "ok" for n in names))

    print("\nИТОГ:", "всё зелёное" if ok else "ЕСТЬ ПРОВАЛЫ")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
