"""Переименование без подключения телефона и без изменения рабочих данных."""
import importlib
import os
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

with tempfile.TemporaryDirectory(prefix="pa_names_") as tmp:
    config.BASE = tmp
    config.DB_PATH = os.path.join(tmp, "jobs.db")
    import devices
    import fleet
    import webui

    for serial in ("phone-a", "phone-b"):
        devices.space(serial)
        devices.remember(serial, "Xiaomi")
    service = fleet.Service("phone-a")
    webui.ROLE["hub"] = True
    webui.HUB["devices"] = [{"serial": "phone-a", "state": "device", "name": "Xiaomi"}]
    command = webui._cmd_device_rename
    with patch.object(fleet.FLEET, "ensure", side_effect=AssertionError("no start")), \
         patch.object(webui.adb, "raw", side_effect=AssertionError("no phone")):
        assert command({"serial": "phone-a", "name": "  Работа 🟢  "})["ok"]
        assert webui.HUB["devices"][0]["name"] == "Работа 🟢"
        devices.remember("phone-a", "New model")
        assert devices.name_of("phone-a") == "Работа 🟢"
        assert devices.name_of("phone-b") == "Xiaomi"
        importlib.reload(devices)
        assert devices.name_of("phone-a") == "Работа 🟢"
        assert devices.known([])[0]["name"] == "Работа 🟢"
        for bad in ("x" * 81, "a\nb", ["bad"]):
            assert not command({"serial": "phone-a", "name": bad})["ok"]
        assert not command({"serial": "../unknown", "name": "x"})["ok"]
        assert command({"serial": "phone-a", "name": ""})["ok"]
        assert devices.name_of("phone-a") == "New model"
        assert "device-rename" in webui.HUB_COMMANDS
    print("OK: имена сохраняются, опрос модели не затирает их, сброс и валидация работают")
