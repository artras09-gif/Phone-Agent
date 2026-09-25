"""PhoneAgent — управление Android через ADB. Без нейросетей.

    python main.py doctor            проверить, что всё готово
    python main.py dump              напечатать элементы текущего экрана
    python main.py find Опубликовать найти элемент по тексту
    python main.py unlock / lock     разблокировать / заблокировать
    python main.py post video.mp4 -c "описание" -t tiktok
    python main.py session tiktok    одна живая сессия в ленте
    python main.py add video.mp4 -c "описание"
    python main.py queue             что в очереди
    python main.py serve             служба по расписанию
    python main.py mirror            трансляция экрана телефона на ПК
    python main.py stop / resume     стоп-кран
    python main.py escape            снять помеху с экрана силами модели
    python main.py bot               принимать ссылки и видео из Telegram
    python main.py vision            проверить визуальную модель
    python main.py analyze           разобрать накопленные кадры ленты
    python main.py content           что аккаунту показывает лента
    python main.py remote            подключиться к телефону через туннель
    python main.py interests         вкусы: что смотреть, что листать
    python main.py plan              расписание сессий на неделю

К post, session и serve можно добавить --watch — тогда на время работы
откроется окно с экраном телефона, и видно, что агент делает.
"""
import argparse
import csv
import os
import sys
import time

import adb
import config
import device
import devices
import gate
import interests
import jobs
import mirror as mirror_mod
import plan as plan_mod
import poster
import runlog
import scheduler
import session as session_mod
import telegram_bot
import ui
import vision
import remote


def cmd_doctor(args):
    print("=" * 60)
    ok = True

    print(f"    adb: {config.ADB}")
    devs = adb.devices()
    if not devs:
        print("[!] Устройств нет.")
        print("    Воткни кабель, включи «Отладка по USB» в настройках")
        print("    разработчика и подтверди отпечаток на экране телефона.")
        if config.REMOTE_ENABLED:
            print("    Либо по сети: python main.py remote")
        return 1
    live = [s for s, state in devs if state == "device"]
    for serial, state in devs:
        mark = "OK " if state == "device" else "[!]"
        print(f"{mark} {serial}: {state}")
        if state == "unauthorized":
            print("    Подтверди доступ в диалоге на телефоне.")
        elif state == "offline" and live:
            # Обычное дело при работе по сети: старый адрес остался висеть
            # после смены порта. На работу не влияет, но мешает читать вывод.
            print(f"    мёртвая запись, убрать: adb disconnect {serial}")
        elif state != "device":
            ok = False

    if not live:
        return 1
    adb.prefer(live[0] if len(live) > 1 else None)

    w, h = adb.screen_size()
    print(f"OK  экран: {w}x{h}")
    print(f"OK  Android: {adb.shell('getprop ro.build.version.release').strip()}")
    print(f"OK  модель: {adb.shell('getprop ro.product.model').strip()}")
    print(f"    состояние экрана: {device.screen_state()}")

    pin = device.read_pin()
    print(f"    PIN: {'задан в pin.txt' if pin else 'НЕТ (создай pin.txt, если есть блокировка)'}")

    kb = device.has_adb_keyboard()
    print(f"{'OK ' if kb else '[!]'} ADBKeyboard: {'установлен' if kb else 'НЕТ — русский текст вводиться не будет'}")
    if not kb:
        print("    Ставится так: python main.py install-keyboard")

    nodes = ui.dump(retries=2, tolerant=True)
    if nodes:
        print(f"OK  дерево интерфейса читается ({len(nodes)} элементов)")
    else:
        # Не считаем это поломкой: в ленте дерево не снимается никогда,
        # потому что uiautomator ждёт покоя интерфейса, а видео играет.
        print("[i] дерево сейчас не читается — норма, если экран погашен")
        print("    или на нём играет видео. Проверь на статичном экране.")

    by_cable = [s for s in live if ":" not in s]
    print(f"    подключение: {'кабель' if by_cable else 'по сети'}")
    if config.REMOTE_ENABLED:
        net_address = remote.saved()
        if net_address:
            up = net_address in remote.wireless_devices()
            print(f"    запасной путь по сети: {net_address}"
                  f"{'' if up else ' — сейчас не подключён'}")

    vis_ok, vis_info = vision.available()
    print(f"{'OK ' if vis_ok else '[i]'} зрение ({vision.where()}): {vis_info}")
    if vis_ok:
        print(f"    разбор в ленте: {'на лету' if config.VISION_REALTIME else 'пакетный (analyze)'}"
              f", тапы по подсказке: {'РАЗРЕШЕНЫ' if config.VISION_MAY_TAP else 'запрещены'}")

    print(f"    сухой прогон: {'ДА' if config.DRY_RUN else 'НЕТ — публикует по-настоящему'}")
    print(f"    стоп-кран: {'ВЗВЕДЁН' if config.stop_requested() else 'снят'}")
    print("=" * 60)
    return 0 if ok else 1


def cmd_dump(args):
    nodes = ui.dump()
    pkg, act = adb.current_app()
    print(f"# {pkg} / {act}\n")
    print(ui.describe(nodes, limit=args.limit))
    print(f"\n# всего элементов: {len(nodes)}")
    return 0


def cmd_comments_probe(args):
    """Проверить селекторы комментариев на живом экране.

    Нужна потому, что в ленте `uiautomator` дерево не снимает — пока играет
    видео, интерфейс никогда не приходит в покой. Отсюда весь порядок
    действий: сначала одиночный тап ставит ролик на паузу, и только тогда
    дерево становится доступным, а кнопку можно найти селектором.

    Команда повторяет ровно те шаги, что делает сессия, но НИЧЕГО не нажимает
    дальше — только показывает, что нашлось.
    """
    import human
    import session as sess

    app = args.app
    cfg = sess._feed_config(app)
    w, h = adb.screen_size()

    print(f"== {cfg.get('title', app)} ==")
    print("открываю ленту...")
    log = runlog.Log(live=True)
    if not sess._open_feed(cfg, log):
        print("не удалось открыть ленту")
        return 1
    # Ждём дольше и снимаем терпеливее: лента догружается не мгновенно, и на
    # второй секунде дерево бывает пустым там, где на пятой лежит сотня
    # узлов. Стенд, который торопится, выносит приговор «не заработает» на
    # ровном месте — так и случилось с Shorts 2026-08-30.
    time.sleep(5)

    print("\nдерево в играющей ленте:")
    tree = ui.dump(retries=2, tolerant=True, timeout=9)
    print(f"  элементов: {len(tree)} "
          f"({'ожидаемо пусто — видео играет' if not tree else 'снялось'})")

    print("\nставлю на паузу одиночным тапом...")
    x, y = human.like_point(w, h)
    adb.tap(x, y)
    time.sleep(1.2)

    tree = ui.dump(retries=2, tolerant=True, timeout=10)
    print(f"  элементов после паузы: {len(tree)}")
    if not tree:
        print("  дерево так и не снялось — значит тап не остановил ролик.")
        print("  Заход в комментарии на этом приложении не заработает.")
        return 1

    found = sess._pick(tree, cfg.get("comment_selectors") or [])
    if found is None:
        print("\n  КНОПКА НЕ НАЙДЕНА по селекторам из recipes.json:")
        for sel in cfg.get("comment_selectors") or []:
            print(f"    {sel}")
        print("\n  Что есть на экране (ищи строку про комментарии):")
        print(ui.describe(tree, limit=args.limit))
        print("\n  Впиши подходящий desc в _feed_apps -> "
              f"{app} -> comment_selectors")
        rc = 1
    else:
        print(f"\n  кнопка найдена: {found}  clickable={found.clickable}")
        rc = 0

    print("\nвозвращаю ролик...")
    adb.tap(*human.like_point(w, h))
    return rc


def cmd_links(args):
    """Сохранённые ссылки: и лайкнутое с телефона, и присланное в чат."""
    if getattr(args, "add", None):
        # Тот же путь, что и у бота, только с клавиатуры: удобно проверить
        # разбор адреса, не трогая телефон и не поднимая бота.
        import weblink

        text = " ".join(args.add)
        urls = weblink.find_urls(text)
        if not urls:
            print("в тексте нет ни одного адреса")
            return 1
        note = weblink.strip_urls(text)
        for url in urls:
            app = weblink.network_of(url)
            fresh = jobs.add_link(url, app or "ссылка", 0, caption=note,
                                  session="руками")
            state = "сохранил" if fresh else "уже было"
            print(f"{state}  {app or 'сеть не узнал':12}  {url}")
        return 0

    rows = jobs.links(limit=args.limit)
    if not rows:
        print("ссылок пока нет.\n"
              "Берутся они из двух мест:\n"
              f"  * агент лайкнул ролик от {config.LINK_MIN_LIKES} лайков "
              "(порог LINK_MIN_LIKES в config.py; живьём проверено в TikTok);\n"
              "  * ссылку прислали боту в Telegram — любая соцсеть.\n"
              "Добавить руками: python main.py links --add <адрес>")
        return 0

    if args.export:
        with open(args.export, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow(["когда", "лента", "лайков", "автор",
                             "описание", "ссылка"])
            for r in rows:
                writer.writerow([
                    time.strftime("%Y-%m-%d %H:%M", time.localtime(r["at"])),
                    r["app"], r["likes"], r["author"],
                    (r["caption"] or "").replace("\n", " "), r["url"]])
        print(f"выгружено {len(rows)} строк: {args.export}")
        return 0

    for r in rows:
        when = time.strftime("%d.%m %H:%M", time.localtime(r["at"]))
        author = r["author"] or "—"
        print(f"{when}  {r['likes']:>9}  {author[:24]:24}  {r['url']}")
        caption = (r["caption"] or "").replace("\n", " ").strip()
        if caption:
            print(f"{'':>26}{caption[:70]}")
    print(f"\nвсего показано: {len(rows)}   "
          f"(выгрузить: python main.py links --export ссылки.csv)")
    return 0


def _probe_points(app, cfg, w, h, log):
    """Показать, КУДА агент будет тапать в лентах без кнопок в дереве.

    Shorts и Reels не выставляют правую колонку в дерево, поэтому «поделиться»
    там — координата, а координата либо попадает, либо молча промахивается.
    Чтобы это не выяснялось живьём, снимок экрана раскладывается в HTML с
    крестиками на всех заданных точках: видно сразу, стоит ли крест на
    самолётике или на пустом месте.

    HTML, а не картинка с нарисованными крестами: рисовать в PNG нечем —
    проект держится на одной стандартной библиотеке, — а браузер есть везде.
    """
    import base64
    import webbrowser

    import session as sess

    marks = []
    pt = cfg.get("share_point")
    if pt:
        marks.append((pt, "«поделиться»", "share_point"))
    for i, pt in enumerate(cfg.get("search_points") or [], 1):
        marks.append((pt, f"путь к поиску, шаг {i}", f"search_points[{i - 1}]"))
    pt = cfg.get("home_point")
    if pt:
        marks.append((pt, "возврат в ленту", "home_point"))

    png = adb.exec_out("screencap -p", timeout=20)
    if not png:
        print("экран не снялся — проверять точки не на чем")
        return 1
    frames = os.path.join(config.BASE, "frames")
    os.makedirs(frames, exist_ok=True)
    png_path = os.path.join(frames, f"probe-{app}.png")
    with open(png_path, "wb") as f:
        f.write(png)

    dots = "".join(
        f'<div class="m" style="left:{x * 100:.2f}%;top:{y * 100:.2f}%">'
        f'<b></b><span>{title}<br><i>{key}</i></span></div>'
        for (x, y), title, key in marks)
    html = (
        "<!doctype html><meta charset='utf-8'>"
        f"<title>Точки: {cfg.get('title', app)}</title>"
        "<style>body{margin:0;background:#141010;color:#eee;"
        "font:14px/1.5 system-ui,sans-serif;display:flex;gap:24px;padding:20px}"
        ".shot{position:relative;flex:none}"
        "img{display:block;height:88vh;border-radius:8px}"
        ".m{position:absolute;transform:translate(-50%,-50%)}"
        ".m b{display:block;width:26px;height:26px;border:2px solid #FF8A5B;"
        "border-radius:50%;box-shadow:0 0 0 2px rgba(0,0,0,.5)}"
        ".m span{position:absolute;left:32px;top:0;white-space:nowrap;"
        "background:rgba(0,0,0,.72);padding:3px 7px;border-radius:6px;"
        "font-size:12px}"
        ".m i{color:#FF8A5B;font-style:normal}"
        "ul{max-width:46ch}li{margin-bottom:10px}code{color:#FF8A5B}"
        "</style>"
        f"<div class='shot'><img src='data:image/png;base64,"
        f"{base64.b64encode(png).decode()}'>{dots}</div>"
        "<div><h2>Крестик должен стоять на кнопке</h2><ul>"
        + "".join(f"<li><code>{key}</code> — {title}: "
                  f"<b>{x}, {y}</b></li>" for (x, y), title, key in marks)
        + "</ul><p>Если крестик не там, поправьте доли в "
          f"<code>recipes.json → _feed_apps → {app}</code> "
          "и запустите проверку снова. Первое число — доля ширины, "
          "второе — доля высоты, считая от левого верхнего угла.</p></div>")

    html_path = os.path.join(frames, f"probe-{app}.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\nснимок: {png_path}")
    print(f"разметка: {html_path}")
    for (x, y), title, key in marks:
        print(f"  {key:18} {title:22} доли {x}, {y}  = пиксели "
              f"{int(x * w)}, {int(y * h)}")

    print("\nчисло лайков (его здесь читает зрение, а не дерево):")
    likes = sess._likes_by_vision(log)
    if likes is None:
        print("  не прочиталось — ссылку в этой ленте агент бы не забрал")
    else:
        beyond = likes >= config.LINK_MIN_LIKES
        print(f"  {likes}  (порог {config.LINK_MIN_LIKES} — "
              f"{'забрали бы' if beyond else 'пропустили бы'})")

    try:
        webbrowser.open(f"file:///{html_path.replace(os.sep, '/')}")
    except Exception:
        pass
    return 0 if likes is not None else 1


def cmd_link_probe(args):
    """Проверить на живом экране всё, что нужно для сохранения ссылок.

    Повторяет шаги сессии, но НИЧЕГО не нажимает: ставит ролик на паузу и
    показывает, читается ли число лайков и находятся ли кнопки. Нужна по той
    же причине, что и `comments-probe`: подписи кнопок у приложений меняются,
    а гадать по координатам этот проект принципиально не хочет.
    """
    import human
    import session as sess

    app = args.app
    cfg = sess._feed_config(app)
    w, h = adb.screen_size()

    print(f"== {cfg.get('title', app)} ==")
    if not (cfg.get("share_selectors") or cfg.get("share_point")):
        print("для этой ленты сохранение ссылок выключено "
              "(в recipes.json нет ни share_selectors, ни share_point)")
        return 1

    device.ensure_awake()
    log = runlog.Log(live=True)
    if not sess._open_feed(cfg, log):
        print("не удалось открыть ленту")
        return 1
    time.sleep(2)

    # Ленты без кнопок в дереве (Shorts, Reels) проверяются иначе: там нечего
    # искать селекторами, зато надо своими глазами увидеть, куда попадают
    # точки. Пауза при этом не нужна — их дерево снимается и на ходу.
    if cfg.get("share_point"):
        return _probe_points(app, cfg, w, h, log)

    # Признак ленты у TikTok один: дерево НЕ снимается, потому что играет
    # видео. Пока снимается — мы не в ленте, и тапать вслепую нельзя:
    # на чужом профиле тап открывает ролик из сетки.
    for _ in range(5):
        tree = ui.dump(retries=1, tolerant=True, timeout=6)
        if not tree:
            break
        tab = sess._pick(tree, cfg.get("feed_tabs") or [])
        if tab is not None:
            ui.tap_node(tab)
        else:
            device.back()
        time.sleep(1.5)
    else:
        print("в ленту попасть не удалось")
        return 1

    print("ставлю на паузу...")
    tree = []
    for _ in range(2):
        adb.tap(*human.like_point(w, h))
        time.sleep(1.3)
        tree = ui.dump(retries=2, tolerant=True, timeout=10)
        if tree:
            break
    if not tree:
        print("дерево не снялось — значит тап не остановил ролик")
        return 1

    checks = [("число лайков", "like_count_selectors"),
              ("кнопка «поделиться»", "share_selectors"),
              ("вкладка «Интересное»", "discover_tabs"),
              ("вкладка «Главная»", "home_tabs"),
              ("автор", "author_selectors")]
    rc = 0
    for title, key in checks:
        node = sess._pick(tree, cfg.get(key) or [])
        if node is None:
            print(f"  [нет] {title}  ({key})")
            rc = 1
        else:
            print(f"  [ок]  {title}: {node.desc or node.text!r}")
        if key == "like_count_selectors" and node is not None:
            likes = sess._parse_count(node.desc)
            print(f"        разобрано: {likes}  "
                  f"(порог {config.LINK_MIN_LIKES} — "
                  f"{'забрали бы' if likes and likes >= config.LINK_MIN_LIKES else 'пропустили бы'})")

    if rc:
        print("\nчто есть на экране:")
        print(ui.describe(tree, limit=args.limit))
        print("\nНедостающее вписать в recipes.json -> _feed_apps -> " + app)
    else:
        print("\nпункт «Ссылка» в листе «поделиться» этой командой не "
              "проверяется: чтобы его увидеть, лист пришлось бы открыть.")

    print("\nвозвращаю ролик...")
    adb.tap(*human.like_point(w, h))
    return rc


def cmd_find(args):
    nodes = ui.dump()
    hits = ui.find(nodes, contains=args.text)
    if not hits:
        print(f"не найдено: {args.text!r}")
        return 1
    for n in hits:
        print(f"{n}  id={n.rid}  clickable={n.clickable}")
    if args.tap:
        ui.tap_node(hits[0])
        print(f"тапнул -> {hits[0]}")
    return 0


def cmd_escape(args):
    """Выпустить модель на текущий экран: пусть сама уберёт помеху.

    Ради проверки и сделано: открываешь на телефоне то самое окно
    («Подпишитесь на друзей», запрос прав, туториал) и запускаешь команду —
    видно каждый шаг, что модель увидела и что нажала.
    """
    import escape as escape_mod

    cfg = session_mod._feed_config(args.app)
    ok, info = vision.available()
    if not ok:
        print(f"[!] зрение недоступно: {info}")
        return 1
    if not adb.connected():
        print("[!] телефон не подключён")
        return 1
    print(f"модель: {info} ({vision.where()})")

    log = runlog.Log(live=True)
    t0 = time.time()
    # Признак ленты берём тот же, что и у сессии: у Shorts и Reels дерево
    # снимается и в ленте, поэтому им нужны маркеры из рецепта.
    markers = cfg.get("feed_markers") or []
    labels = [sel.get("desc") or sel.get("text") or ""
              for sel in (cfg.get("feed_tabs") or [])]
    report = escape_mod.escape(
        goal=args.goal, package=cfg["package"], log=log, max_steps=args.steps,
        feed_labels=[x for x in labels if x] or None,
        done=(lambda: session_mod._ensure_feed(cfg, log)) if markers else None,
        allow_done=bool(markers))
    print(f"\n{'ВЫБРАЛСЯ' if report['ok'] else 'НЕ ВЫБРАЛСЯ'} за "
          f"{time.time() - t0:.1f} с: {report['почему']}")
    if report["steps"]:
        print("шаги: " + " -> ".join(report["steps"]))
    return 0 if report["ok"] else 1


def cmd_bot(args):
    """Только приёмник Telegram: принимать ссылки и видео, ничего не публикуя.

    Служба (`serve`) поднимает бота сама, но она же занимает телефон. Для
    «просто складывай ссылки» телефон не нужен вовсе, поэтому приёмник умеет
    работать в одиночку.
    """
    cfg = telegram_bot.load_settings()
    if not cfg["token"]:
        print("[!] токена нет. Положи его в telegram.json или PA_TG_TOKEN.")
        return 1
    who = cfg["owner"] or "ещё никто не писал — первый написавший станет хозяином"
    print(f"бот слушает. Хозяин: {who}")
    print("Ctrl+C — выход")
    try:
        telegram_bot.poll_forever()
    except KeyboardInterrupt:
        print("\nостановлен")
    return 0


def cmd_unlock(args):
    ok = device.unlock()
    print("разблокирован" if ok else "не удалось разблокировать")
    return 0 if ok else 1


def cmd_lock(args):
    device.lock()
    print("заблокирован")
    return 0


def _record_path(tag):
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    return os.path.join(config.LOG_DIR, f"{stamp}_{tag}.mp4")


def cmd_post(args):
    if args.watch:
        with mirror_mod.watch(title=f"PhoneAgent — публикация ({args.target})",
                              record=_record_path(f"post_{args.target}") if args.record else None):
            ok, report = poster.post(args.video, args.caption, args.target)
    else:
        ok, report = poster.post(args.video, args.caption, args.target)
    if not config.LIVE_LOG:
        print(report)
    return 0 if ok else 1


def cmd_session(args):
    if args.watch:
        with mirror_mod.watch(title=f"PhoneAgent — сессия ({args.app})",
                              record=_record_path(f"session_{args.app}") if args.record else None):
            report = session_mod.browse(args.app, args.duration)
    else:
        report = session_mod.browse(args.app, args.duration)
    # При живом журнале строки уже на экране — печатать их ещё раз незачем.
    if not config.LIVE_LOG:
        print(report)
    return 0


def cmd_mirror(args):
    if args.simple:
        print("Простой просмотрщик. Закрой окно, чтобы выйти.")
        mirror_mod.simple_viewer(fps=args.fps, scale=args.scale)
        return 0

    record = _record_path("mirror") if args.record else None
    m = mirror_mod.Mirror(
        title="PhoneAgent — экран телефона",
        control=args.control,
        record=record,
        max_size=args.size,
    )
    try:
        m.start()
    except RuntimeError as e:
        print(e)
        return 1

    print("Трансляция запущена." + (f" Запись: {record}" if record else ""))
    print("Управление мышью: " + ("ВКЛючено" if args.control else "выключено (--control чтобы включить)"))
    print("Закрой окно scrcpy или нажми Ctrl+C здесь.")
    try:
        while m.alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        m.stop()
    return 0


def cmd_add(args):
    job_id = jobs.add(os.path.abspath(args.video), args.caption, args.target)
    print(f"задача #{job_id} добавлена")
    return 0


def cmd_queue(args):
    rows = jobs.pending()
    if not rows:
        print("очередь пуста")
    for r in rows:
        when = time.strftime("%d.%m %H:%M", time.localtime(r["run_at"]))
        print(f"#{r['id']:3d} {when}  {r['targets']:12} {os.path.basename(r['video_path'])}")
    print(f"\nсегодня опубликовано: {jobs.count_today('done')}/{config.MAX_POSTS_PER_DAY}")
    print(f"сессий сегодня: {jobs.sessions_today()}/{config.MAX_SESSIONS_PER_DAY}")
    return 0


def cmd_serve(args):
    import fleet

    serial = config.SERIAL or ""

    # Один телефон — одна служба. Запустить её можно и кнопкой в окне, и
    # руками из консоли; двое, ведущие один телефон, перемешали бы свои
    # маршруты на одном экране, и разобрать это по логам почти невозможно.
    lock = fleet.device_gate(serial)
    try:
        lock.acquire(timeout=1)
    except gate.Timeout:
        print(f"Телефон {devices.name_of(serial)} уже ведёт другая служба. "
              "Останови её или выбери другой телефон.")
        return 1

    # Бота поднимает тот, кто успел: у Telegram читатель ровно один,
    # второй получил бы 409 и сломал бы обоих.
    bot = fleet.telegram_gate()
    try:
        bot.acquire(timeout=0.5)
        notify = telegram_bot.start_background()
        if telegram_bot.running():
            print("Telegram-бот принимает видео в эту очередь")
    except gate.Timeout:
        notify = lambda text: None      # noqa: E731 — бот уже занят другим
        print("Telegram-бота ведёт другая служба — здесь он не поднимается")

    print(f"служба запущена ({devices.name_of(serial) or 'телефон по умолчанию'}), "
          "Ctrl+C для выхода")
    if config.DRY_RUN:
        print("ВНИМАНИЕ: сухой прогон, финальные кнопки не нажимаются")
    if args.watch:
        print("трансляция будет открываться на время каждого действия")

    # Просьба выйти приходит файлом от надзирателя: убивать процесс нельзя,
    # он может держать телефон посреди публикации.
    should_stop = fleet.install_child_stopper(serial)
    try:
        scheduler.serve(notify, watch=args.watch, record=args.record,
                        should_stop=should_stop)
    except KeyboardInterrupt:
        print("\nостановлено")
    finally:
        lock.release()
    return 0


def cmd_fleet(args):
    """Несколько телефонов разом: у каждого своя служба в своём процессе."""
    import fleet

    chosen = args.devices or [d["serial"] for d in devices.known() if d["live"]]
    if not chosen and args.action in ("start", "restart"):
        print("Ни одного подключённого телефона не вижу.")
        return 1

    if args.action == "status":
        rows = devices.known()
        if not rows:
            print("телефонов не найдено")
            return 0
        for d in rows:
            busy = fleet.busy_elsewhere(d["serial"])
            mark = "работает" if busy else "стоит"
            live = d["link"] if d["live"] else "нет связи"
            print(f"{d['name']:24} {d['serial']:22} {live:10} служба: {mark}")
        return 0

    if args.action in ("stop", "restart"):
        for serial in chosen:
            path = fleet.stop_path(serial)
            os.makedirs(fleet.CONTROL, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write("остановлено командой fleet\n")
            print(f"{devices.name_of(serial)}: попросил остановиться")
        if args.action == "stop":
            return 0
        # Дать прежним выйти, прежде чем занимать телефон заново.
        deadline = time.time() + fleet.STOP_GRACE
        for serial in chosen:
            while time.time() < deadline and fleet.busy_elsewhere(serial):
                time.sleep(0.5)

    started = []
    for serial in chosen:
        if fleet.busy_elsewhere(serial):
            print(f"{devices.name_of(serial)}: уже работает, пропускаю")
            continue
        if fleet.FLEET.start(serial):
            started.append(serial)
            print(f"{devices.name_of(serial)}: служба запущена")
        else:
            svc = fleet.FLEET.service(serial)
            print(f"{devices.name_of(serial)}: не запустилась — {svc.stopped_reason}")

    if not started:
        return 1

    print("\nработают: " + ", ".join(devices.name_of(s) for s in started))
    print("Ctrl+C — остановить все службы\n")
    try:
        while True:
            time.sleep(1)
            for svc in fleet.FLEET.services.values():
                total, lines = svc.tail(getattr(svc, "_shown", 0))
                svc._shown = total
                for line in lines:
                    print(f"[{svc.name}] {line}", flush=True)
    except KeyboardInterrupt:
        print("\nостанавливаю службы, это может занять до "
              f"{fleet.STOP_GRACE:.0f} с...")
        fleet.FLEET.stop_all()
        print("все остановлены")
    return 0


def cmd_stop(args):
    config.set_stop(True)
    print("стоп-кран взведён, агент ничего не делает")
    return 0


def cmd_resume(args):
    config.set_stop(False)
    print("стоп-кран снят")
    return 0


def cmd_bench(args):
    """Разложить решение по фазам. Нужна, чтобы сравнивать сборку с исходниками.

    Без неё «стало медленнее» упирается в догадки: одно и то же решение
    складывается из ужатия кадра, ответа модели и сверки темы, и виновата
    может быть любая часть.
    """
    import glob
    import statistics
    import time as _time

    import vision

    frames = sorted(glob.glob(os.path.join(config.FRAMES_DIR, "*", "*.png")))
    if not frames:
        print("нет сохранённых кадров — сначала прогони сессию")
        return 1
    with open(frames[-1], "rb") as f:
        png = f.read()
    print(f"кадр: {os.path.basename(frames[-1])}, {len(png) // 1024} КБ")
    print(f"режим: {'СБОРКА exe' if getattr(sys, 'frozen', False) else 'исходники'}")

    def measure(name, fn, runs=5):
        times = []
        for _ in range(runs):
            started = _time.time()
            fn()
            times.append(_time.time() - started)
        print(f"  {name:22} медиана {statistics.median(times):5.2f}с  "
              f"мин {min(times):5.2f}")
        return statistics.median(times)

    print(f"зрение: {vision.where()}")
    measure("ужатие кадра", lambda: vision.shrink_png(png))
    small = vision.shrink_png(png)
    print(f"  (после ужатия {len(small) // 1024} КБ)")
    if vision.provider() == vision.API and config.API_JPEG:
        # В облако кадр уходит по сети, поэтому его вес — часть времени
        # ответа, а не мелочь. Меряем и перекодировку, и что из неё вышло.
        measure("кадр в JPEG", lambda: vision.to_jpeg(small))
        jpg = vision.to_jpeg(small)
        print(f"  (в JPEG {len(jpg) // 1024 if jpg else '—'} КБ)")
    measure("ответ модели", lambda: vision.ask(small, "Опиши кадр одним словом.",
                                               max_tokens=16), runs=3)
    measure("сверка темы", lambda: vision.judge_topic("женщина готовит суши",
                                                      "кулинария"), runs=3)

    if not adb.connected():
        print("  (телефон не подключён — съёмку кадра не мерю)")
        return 0

    measure("снимок экрана", lambda: adb.exec_out("screencap -p", timeout=20),
            runs=3)

    import stream as stream_mod
    print(f"  ffmpeg: {stream_mod.ffmpeg_exe() or 'НЕ НАЙДЕН — поток недоступен'}")
    try:
        feed = stream_mod.Stream().start()
    except RuntimeError as e:
        print(f"  поток не поднялся: {e}")
        return 0
    try:
        feed.frame()                       # первый кадр греет запись
        measure("кадр из потока", feed.frame, runs=5)
    finally:
        feed.stop()
    return 0


def cmd_ui(args):
    """Окно приложения: то же самое, но мышкой."""
    import webui

    return webui.run(port=args.port, open_browser=not args.no_window)


def cmd_install_keyboard(args):
    """Скачать ADBKeyboard и поставить на телефон. Нужен для кириллицы."""
    import setup as setup_mod

    print("ADBKeyboard нужен для ввода кириллицы и эмодзи:")
    print("штатный `adb shell input text` умеет только латиницу.\n")
    if args.yes:
        setup_mod.assume_yes()
    return 0 if setup_mod.step_keyboard(adb.connected()) else 1


def cmd_remote(args):
    """Подключение к телефону по сети. Запасной путь: основной — кабель."""
    if not config.REMOTE_ENABLED and args.action not in ("status", "off"):
        print("[i] Работа по сети выключена: config.REMOTE_ENABLED = False.")
        print("    Основной способ — кабель, он быстрее и ничего не требует.")
        print("    Чтобы включить запасной путь, поставь REMOTE_ENABLED = True")
        print("    в config.py и повтори команду.")
        return 1

    if args.action == "status":
        print(remote.status())
        return 0

    if args.action == "off":
        address = remote.saved()
        if address:
            print(remote.disconnect(address))
        remote.forget()
        adb.raw("usb", check=False, timeout=20)
        print("сетевой режим выключен, работаем по кабелю")
        return 0

    if args.action == "pair":
        if not args.address or not args.code:
            print("Первое подключение, кабель не нужен. На телефоне:")
            print("  Настройки -> Для разработчиков -> Беспроводная отладка")
            print("  -> включить -> «Подключение с помощью кода сопряжения»")
            print("Там будут адрес с портом и шестизначный код. Дальше:")
            print("  python main.py remote pair 192.168.1.5:37129 123456")
            print("Если в той же сети мы не окажемся, добавь порт подключения")
            print("(строка «IP-адрес и порт» на том же экране): --port 41234")
            return 1
        ok, report = remote.pair_and_settle(
            args.address, args.code, connect_port=args.port, name_hint=args.name)
        for line in report:
            print(f"    {line}")
        if ok:
            print("OK  готово, кабель больше не нужен")
            print("    проверка: python main.py doctor")
        return 0 if ok else 1

    if args.action == "resume":
        if not args.address:
            print("После перезагрузки телефона порт меняется. На телефоне:")
            print("  Для разработчиков -> Беспроводная отладка -> включить,")
            print("  посмотреть строку «IP-адрес и порт» и указать порт здесь:")
            print("  python main.py remote resume 41234")
            return 1
        ok, info = remote.resume(args.address.split(":")[-1], name_hint=args.name)
        print(("OK  " if ok else "[!] ") + info)
        return 0 if ok else 1

    if args.action == "scan":
        own, hosts = remote.zerotier_subnet()
        if not own:
            print("[!] ПК не состоит ни в одной сети ZeroTier.")
            print("    Присоединиться: zerotier-cli join <ID сети>")
            print("    и подтвердить устройство в Members на my.zerotier.com")
            return 1
        port = args.port or remote.DEFAULT_PORT
        print(f"    наш адрес в сети: {own}")
        print(f"    ищу телефон в подсети по порту {port}...")
        found = remote.scan_subnet(hosts, port=port)
        if not found:
            print("[!] никого с открытым портом adb не нашёл.")
            print("    Включи на телефоне беспроводную отладку и сделай")
            print("    первое подключение: python main.py remote pair <адрес>:<порт> <код>")
            return 1
        for host in found:
            print(f"OK  найден: {host}:{port}")
        address = f"{found[0]}:{port}"
        remote.remember(address)
        ok, out = remote.connect(address)
        print(f"    {out}")
        print(f"{'OK  подключён' if ok else '[i] адрес запомнен'}: {address}")
        return 0 if ok else 1

    if args.action == "set":
        if not args.address:
            print("Так: python main.py remote set 10.8.1.14")
            print("Адрес телефона в твоём туннеле. Порт можно не писать,")
            print(f"по умолчанию {remote.DEFAULT_PORT}.")
            return 1
        address = args.address if ":" in args.address \
            else f"{args.address}:{args.port or remote.DEFAULT_PORT}"
        remote.remember(address)
        ok, out = remote.connect(address)
        print(f"    {out}")
        if ok:
            print(f"OK  адрес запомнен и подключён: {address}")
            return 0
        print(f"[i] адрес запомнен: {address}")
        print("    подключиться пока не вышло — включи на телефоне беспроводную")
        print("    отладку и выполни: python main.py remote pair <адрес>:<порт> <код>")
        return 1

    if not remote.saved() and not remote.tailscale_exe():
        print("[!] Не знаю адреса телефона.")
        print("    Свой туннель (WireGuard/ZeroTier): python main.py remote set <адрес>")
        print("    Или поставь Tailscale: winget install -e --id Tailscale.Tailscale")
        return 1

    ok, info = remote.setup(name_hint=args.name,
                            port=args.port or remote.DEFAULT_PORT)
    if not ok:
        print(f"[!] {info}")
        return 1

    print(f"OK  телефон доступен: {info}")
    print("    проверка: python main.py doctor")
    print("    порт открыт на всех интерфейсах — в недоверенной сети "
          "выключай командой remote off")
    return 0


def cmd_vision(args):
    """Проверка зрения: жив ли сервер, какая модель, что она видит сейчас."""
    changes = {}
    if getattr(args, "where", None):
        changes["vision_provider"] = args.where
    if getattr(args, "key", None):
        changes["api_key"] = args.key.strip()
    if getattr(args, "url", None):
        import webui as webui_mod

        field = "api_url" if (changes.get("vision_provider")
                              or vision.provider()) == vision.API else "vision_url"
        changes[field] = webui_mod._clean_url(args.url)
    if getattr(args, "model", None):
        changes["api_model" if (changes.get("vision_provider")
                                or vision.provider()) == vision.API
                else "vision_model"] = args.model
    if changes:
        import prefs

        prefs.save(**changes)
        print(f"настройки записаны: {vision.where()}")

    ok, info = vision.available()
    if not ok:
        print(f"[!] зрение недоступно: {info}")
        if vision.provider() == vision.API:
            print("    Адрес, ключ и модель — на вкладке «Настройки» в окне")
            print("    или: python main.py vision --where api --url ... --key ...")
        else:
            print("    Запусти LM Studio, включи Developer -> Start Server (порт 1234)")
            print("    и загрузи визуальную модель (*-VL, gemma-3, minicpm-v).")
        return 1
    print(f"OK  модель: {info} ({vision.where()})")

    if not adb.connected():
        # Без телефона проверять нечего, но живой ли ключ — узнать стоит:
        # ради этого команду обычно и запускают сразу после его правки.
        if vision.provider() == vision.API:
            print("    телефона нет — проверяю только связь с сервисом...")
            print("OK  сервис отвечает" if vision.warm_up()
                  else "[!] сервис не ответил — проверь адрес, ключ и связь")
        else:
            print("    телефон не подключён — проверю только модель")
        return 0

    print("    снимаю кадр и спрашиваю модель...")
    png = adb.exec_out("screencap -p", timeout=25)
    t0 = time.time()
    try:
        data = vision.describe_frame(png)
    except vision.VisionError as e:
        print(f"[!] {e}")
        return 1
    print(f"OK  ответ за {time.time() - t0:.1f} с")
    for k, v in data.items():
        print(f"    {k}: {v}")
    return 0


def cmd_analyze(args):
    """Разобрать кадры, снятые в сессиях. Уже разобранные пропускаются."""
    ok, info = vision.available()
    if not ok:
        print(f"[!] зрение недоступно: {info}")
        return 1

    done = jobs.analyzed_frames()
    todo = []
    for root, _, files in os.walk(config.FRAMES_DIR):
        for name in sorted(files):
            if not name.endswith(".png"):
                continue
            path = os.path.join(root, name)
            if path not in done:
                todo.append(path)

    if not todo:
        print("нечего разбирать: новых кадров нет")
        return 0

    todo = todo[:args.limit] if args.limit else todo
    print(f"кадров к разбору: {len(todo)}  (модель {info})")

    failed = 0
    for i, path in enumerate(todo, start=1):
        session_id = os.path.basename(os.path.dirname(path))
        with open(path, "rb") as f:
            png = f.read()
        try:
            data = vision.describe_frame(png)
        except vision.VisionError as e:
            failed += 1
            print(f"  {i:3d}/{len(todo)}  сбой: {str(e)[:80]}")
            if failed >= 3:
                print("    три сбоя подряд — прекращаю")
                return 1
            continue
        failed = 0
        jobs.add_content(session_id, args.app, path, data)
        print(f"  {i:3d}/{len(todo)}  {data.get('категория', '?'):12s} "
              f"{str(data.get('тема', ''))[:60]}")

    print("готово. Сводка: python main.py content")
    return 0


def cmd_content(args):
    """Сводка: что лента скармливает аккаунту."""
    st = jobs.content_stats(days=args.days)
    if not st["total"]:
        print(f"за {args.days} дн. разобранных кадров нет.")
        print("Сними сессию (python main.py session) и разбери: python main.py analyze")
        return 0

    print("=" * 60)
    print(f"Разобрано кадров за {args.days} дн.: {st['total']}")
    print(f"Реклама: {st['ads']} ({st['ads'] * 100 // st['total']}%)")
    if st["langs"]:
        langs = ", ".join(f"{r['lang']} {r['c']}" for r in st["langs"])
        print(f"Языки: {langs}")

    print("\nКатегории:")
    for row in st["cats"]:
        share = row["c"] * 100 // st["total"]
        bar = "#" * max(1, share // 3)
        print(f"  {row['category']:12s} {row['c']:4d}  {share:3d}%  {bar}")

    if st["verdicts"]:
        print("\nРешения по вкусам:")
        for row in st["verdicts"]:
            print(f"  {row['verdict']:12s} {row['c']:4d}  из них пролистано "
                  f"{row['s'] or 0}")

    print("\nПоследнее:")
    for row in st["recent"]:
        stamp = time.strftime("%d.%m %H:%M", time.localtime(row["at"]))
        mark = "листнул" if row["skipped"] else ("смотрел" if row["verdict"] else "")
        print(f"  {stamp}  {row['category']:12s} {mark:8s} {str(row['tema'])[:52]}")
    print("=" * 60)
    return 0


def cmd_stats(args):
    """Полный отчёт о работе алгоритма."""
    import stats

    print("\n".join(stats.report(days=args.days)))
    return 0


def cmd_plan(args):
    """Правила сессий: во сколько, сколько минут и по каким дням."""
    try:
        if args.add:
            if args.minutes is None:
                print("[!] с --add нужно и --minutes: сколько смотреть")
                return 1
            minutes = args.minutes
            if "-" in str(minutes):
                low, high = str(minutes).split("-", 1)
                minutes = [float(low), float(high)]
            else:
                minutes = float(minutes)
            plan_mod.add(args.add, minutes, args.days, args.app)
            print("добавлено\n")
        elif args.remove is not None:
            dropped = plan_mod.remove(args.remove)
            print(f"убрано: {dropped.get('окно')} / {dropped.get('дни')}\n")
        elif args.off is not None:
            plan_mod.enable(args.off, False)
            print("выключено\n")
        elif args.on is not None:
            plan_mod.enable(args.on, True)
            print("включено\n")
    except ValueError as e:
        print(f"[!] {e}")
        return 1

    print(plan_mod.describe())
    if args.edit:
        os.startfile(plan_mod.PATH)
        print("\nфайл открыт; служба перечитает план на смене суток")
    return 0


def cmd_interests(args):
    """Показать или изменить настройку: одна тема и один язык."""
    if args.topic is not None or args.lang is not None:
        if args.lang is not None and args.lang not in vision.LANGUAGES + ("", "-"):
            print(f"[!] «{args.lang}» — не язык. Выбирай из: "
                  f"{', '.join(vision.LANGUAGES)}")
            return 1
        # Прочерк целиком — «убрать правило». Дефис внутри слова не трогаем:
        # «IT-новости» должны остаться темой, а не превратиться в «ITновости».
        clear = lambda v: None if v is None else ("" if v.strip() == "-" else v)
        interests.set_choice(topic=clear(args.topic), lang=clear(args.lang))
        print("сохранено:\n")
        print(interests.describe())
        return 0

    if args.pick:
        if interests.pick(vision.LANGUAGES):
            print("\nсохранено:\n")
            print(interests.describe())
        return 0

    print(interests.describe())
    if args.edit:
        os.startfile(config.INTERESTS)
        print("\nфайл открыт; после сохранения ничего перезапускать не надо")
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="main.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)

    d = sub.add_parser("dump")
    d.add_argument("--limit", type=int, default=60)
    d.set_defaults(fn=cmd_dump)

    f = sub.add_parser("find")
    f.add_argument("text")
    f.add_argument("--tap", action="store_true", help="тапнуть по первому найденному")
    f.set_defaults(fn=cmd_find)

    es = sub.add_parser("escape",
                        help="пусть модель сама уберёт помеху с текущего экрана")
    es.add_argument("--app", default="tiktok", choices=config.FEED_APPS,
                    help="в какой ленте дело — из неё берётся пакет приложения")
    es.add_argument("--goal", default="поверх ленты висит окно",
                    help="что мешает, своими словами — для журнала")
    es.add_argument("--steps", type=int, default=config.ESCAPE_MAX_STEPS,
                    help="сколько действий разрешить")
    es.set_defaults(fn=cmd_escape)

    sub.add_parser("bot", help="только приёмник Telegram: ссылки и видео").set_defaults(fn=cmd_bot)

    sub.add_parser("unlock").set_defaults(fn=cmd_unlock)
    sub.add_parser("lock").set_defaults(fn=cmd_lock)

    po = sub.add_parser("post")
    po.add_argument("video")
    po.add_argument("-c", "--caption", default="")
    po.add_argument("-t", "--target", default="tiktok")
    po.add_argument("--watch", action="store_true", help="показать экран телефона")
    po.add_argument("--record", action="store_true", help="записать видео прогона в logs/")
    po.set_defaults(fn=cmd_post)

    s = sub.add_parser("session")
    s.add_argument("app", nargs="?", default="tiktok")
    s.add_argument("--duration", type=float, default=None, help="секунд")
    s.add_argument("--watch", action="store_true", help="показать экран телефона")
    s.add_argument("--record", action="store_true", help="записать видео прогона в logs/")
    s.set_defaults(fn=cmd_session)

    mi = sub.add_parser("mirror")
    mi.add_argument("--control", action="store_true",
                    help="разрешить управление мышью и клавиатурой")
    mi.add_argument("--record", action="store_true", help="писать видео в logs/")
    mi.add_argument("--size", type=int, default=800, help="размер окна по длинной стороне")
    mi.add_argument("--simple", action="store_true",
                    help="встроенный просмотрщик на tkinter, если нет scrcpy")
    mi.add_argument("--fps", type=int, default=2, help="только для --simple")
    mi.add_argument("--scale", type=int, default=2, help="только для --simple")
    mi.set_defaults(fn=cmd_mirror)

    a = sub.add_parser("add")
    a.add_argument("video")
    a.add_argument("-c", "--caption", default="")
    a.add_argument("-t", "--target", default="tiktok")
    a.set_defaults(fn=cmd_add)

    sub.add_parser("queue").set_defaults(fn=cmd_queue)

    cp = sub.add_parser("comments-probe",
                        help="проверить селекторы комментариев на живом экране")
    cp.add_argument("app", nargs="?", default="tiktok",
                    help="какая лента: tiktok, shorts, reels")
    cp.add_argument("--limit", type=int, default=60)
    cp.set_defaults(fn=cmd_comments_probe)

    lk = sub.add_parser("links", help="ссылки на понравившиеся ролики")
    lk.add_argument("-n", "--limit", type=int, default=50)
    lk.add_argument("--export", metavar="ФАЙЛ.csv",
                    help="выгрузить в таблицу вместо вывода на экран")
    lk.add_argument("--add", nargs="+", metavar="АДРЕС",
                    help="сохранить ссылку (или несколько) руками")
    lk.set_defaults(fn=cmd_links)

    lp = sub.add_parser("link-probe",
                        help="проверить, читается ли число лайков и кнопки")
    lp.add_argument("app", nargs="?", default="tiktok",
                    help="какая лента: tiktok, shorts, reels")
    lp.add_argument("--limit", type=int, default=60)
    lp.set_defaults(fn=cmd_link_probe)

    sv = sub.add_parser("serve")
    sv.add_argument("--watch", action="store_true",
                    help="открывать трансляцию на время каждого действия")
    sv.add_argument("--record", action="store_true", help="писать видео действий в logs/")
    sv.set_defaults(fn=cmd_serve)

    fl = sub.add_parser("fleet", help="несколько телефонов сразу: по службе на каждый")
    fl.add_argument("action", nargs="?", default="start",
                    choices=["start", "stop", "restart", "status"],
                    help="start — поднять службы (по умолчанию), stop — "
                         "попросить их выйти, restart — перезапустить, "
                         "status — кто работает")
    fl.add_argument("--devices", nargs="*", metavar="СЕРИЙНИК",
                    help="какие телефоны; по умолчанию все подключённые")
    fl.set_defaults(fn=cmd_fleet)

    rm = sub.add_parser("remote")
    rm.add_argument("action", nargs="?", default="tunnel",
                    choices=["tunnel", "pair", "resume", "scan", "set", "off",
                             "status"],
                    help="tunnel — подключиться (по умолчанию), pair — первое "
                         "подключение по коду, resume — после перезагрузки "
                         "телефона, set — задать адрес в своём туннеле")
    rm.add_argument("address", nargs="?",
                    help="для pair: адрес с экрана телефона; для resume: порт; "
                         "для set: адрес телефона в туннеле")
    rm.add_argument("code", nargs="?", help="для pair: код сопряжения")
    rm.add_argument("--port", type=int, default=None, help="порт подключения")
    rm.add_argument("--name", default=None,
                    help="имя телефона в туннеле, если Android там не один")
    rm.set_defaults(fn=cmd_remote)

    vis = sub.add_parser("vision")
    # Те же три настройки, что и на вкладке «Настройки», — чтобы включить
    # облако можно было, не открывая окно.
    vis.add_argument("--where", choices=["local", "api"],
                     help="где считать: свой сервер или сервис по API")
    vis.add_argument("--key", help="ключ сервиса (ляжет в settings.json)")
    vis.add_argument("--url", help="адрес сервера (…/v1)")
    vis.add_argument("--model", help="имя модели")
    vis.set_defaults(fn=cmd_vision)

    an = sub.add_parser("analyze")
    an.add_argument("--limit", type=int, default=0, help="разобрать не больше N кадров")
    an.add_argument("--app", default="tiktok")
    an.set_defaults(fn=cmd_analyze)

    co = sub.add_parser("content")
    co.add_argument("--days", type=int, default=7)
    co.set_defaults(fn=cmd_content)

    st = sub.add_parser("stats")
    st.add_argument("--days", type=int, default=7)
    st.set_defaults(fn=cmd_stats)

    it = sub.add_parser("interests")
    it.add_argument("--pick", action="store_true", help="выбрать тематику в диалоге")
    it.add_argument("--edit", action="store_true", help="открыть interests.json")
    it.add_argument("--topic", default=None,
                    help="тема своими словами; «-» убрать")
    it.add_argument("--lang", default=None,
                    help="язык: ru, en, другой; «-» не проверять")
    it.set_defaults(fn=cmd_interests)

    pl = sub.add_parser("plan")
    pl.add_argument("--add", metavar="ОКНО",
                    help="окно начала: «21:00-22:00» или «21:00»")
    pl.add_argument("--minutes", default=None,
                    help="сколько смотреть: 30 или «20-40»")
    pl.add_argument("--days", default="каждый день",
                    help="пн-пт, пн,ср,сб, будни, выходные, каждый день")
    pl.add_argument("--app", default=None, help=f"из {', '.join(config.FEED_APPS)}")
    pl.add_argument("--remove", type=int, metavar="N", help="убрать правило №N")
    pl.add_argument("--off", type=int, metavar="N", help="выключить правило №N")
    pl.add_argument("--on", type=int, metavar="N", help="включить правило №N")
    pl.add_argument("--edit", action="store_true", help="открыть plan.json")
    pl.set_defaults(fn=cmd_plan)

    sub.add_parser("bench").set_defaults(fn=cmd_bench)

    ui_cmd = sub.add_parser("ui")
    ui_cmd.add_argument("--port", type=int, default=8765)
    ui_cmd.add_argument("--no-window", action="store_true",
                        help="не открывать окно, только поднять сервер")
    ui_cmd.set_defaults(fn=cmd_ui)

    sub.add_parser("stop").set_defaults(fn=cmd_stop)
    sub.add_parser("resume").set_defaults(fn=cmd_resume)
    ik = sub.add_parser("install-keyboard")
    ik.add_argument("-y", "--yes", action="store_true", help="не спрашивать")
    ik.set_defaults(fn=cmd_install_keyboard)
    return p


def main():
    argv = sys.argv[1:]
    # Собранное приложение запускают двойным щелчком, без аргументов. Печатать
    # ему справку по командам бессмысленно — человек ждёт окна.
    if not argv and getattr(sys, "frozen", False):
        argv = ["ui"]

    args = build_parser().parse_args(argv)

    # То, что выбрано мышкой в окне (модель, ключ от облака, телефон), должно
    # действовать и в командах: иначе `doctor` показывает одно, а окно
    # работает по-другому. Окно применяет настройки у себя, при запуске сервера.
    if args.cmd != "ui":
        import devices
        import prefs

        prefs.apply()
        # И пространство телефона: у каждого своя очередь, расписание и вкусы.
        # Без этого консоль работала бы в общих папках, а окно — в папке
        # телефона, и это были бы две разные программы.
        chosen = config.SERIAL or ""
        if not chosen:
            live = [s for s, state in adb.devices() if state == "device"]
            chosen = live[0] if live else ""
        devices.use(chosen)

    # Команды, которым телефон не нужен: разбор кадров и сводки работают
    # с уже снятым, а vision сам решает, что делать без устройства.
    # Надзиратель телефон сам не трогает — он раздаёт их своим службам, и
    # каждая проверяет связь у себя. Без этого `fleet status` не отвечал бы
    # при отключённом кабеле, хотя как раз тогда он и нужен.
    if args.cmd not in ("doctor", "stop", "resume", "queue", "add",
                        "install-keyboard", "analyze", "content", "vision",
                        "interests", "remote", "stats", "plan", "ui", "bench",
                        "fleet"):
        if not adb.ensure():
            print("Телефон не отвечает. Проверь кабель и отладку по USB.")
            return 1
    try:
        return args.fn(args)
    except adb.AdbError as e:
        print(f"Ошибка ADB: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
