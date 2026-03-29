import datetime
import re
import sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from scripts.aleapp_abx import abxread, checkabx
from scripts.utils import normalize_meta_timestamp, normalize_text, windows_safe_path


def _normalize_android_epoch(value: Any) -> str:
    text = normalize_text(value).strip()
    if not text:
        return ""
    try:
        number = int(text)
    except Exception:
        return text
    if number <= 0:
        return ""
    if number > 9999999999:
        return normalize_meta_timestamp(number / 1000)
    return normalize_meta_timestamp(number)


def _parse_android_xml(path: Path):
    try:
        if checkabx(str(path)):
            return abxread(str(path), False).getroot()
    except Exception:
        pass
    try:
        return ET.parse(windows_safe_path(path)).getroot()
    except Exception:
        return None


def parse_settings_secure(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    root = _parse_android_xml(path)
    if root is None:
        return values
    for setting in root.iter("setting"):
        name = normalize_text(setting.get("name")).strip()
        value = normalize_text(setting.get("value")).strip()
        if not name or not value:
            continue
        if name in {"android_id", "bluetooth_name", "bluetooth_address"}:
            values[name] = value
    return values


def parse_build_prop(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    with open(windows_safe_path(path), "r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or "=" not in line or line.startswith("#"):
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key:
                values[key] = value.strip()
    return values


def parse_bluetooth_connections(path: Path) -> tuple[dict[str, str], list[dict[str, str]]]:
    adapter_info: dict[str, str] = {}
    connections: list[dict[str, str]] = []
    current_name = ""
    current_timestamp = ""
    current_linkkey = ""
    current_mac = ""
    seen: set[tuple[str, str]] = set()

    with open(windows_safe_path(path), "r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            mac_matches = re.findall(r"(\[[0-9a-f]{2}(?::[0-9a-f]{2}){5}\])", line, re.IGNORECASE)
            if mac_matches:
                if current_mac:
                    key = (current_mac.lower(), current_name.lower())
                    if key not in seen:
                        seen.add(key)
                        connections.append(
                            {
                                "name": current_name,
                                "mac": current_mac,
                                "timestamp": current_timestamp,
                                "linkkey": current_linkkey,
                            }
                        )
                current_mac = mac_matches[0].strip("[]").upper()
                current_name = ""
                current_timestamp = ""
                current_linkkey = ""
                continue
            if " = " not in line:
                continue
            key_name, value = line.split(" = ", 1)
            key_name = key_name.strip()
            value = value.strip()
            if not current_mac:
                adapter_info[key_name] = value
            if key_name == "Name":
                current_name = value
            elif key_name == "Timestamp":
                current_timestamp = _normalize_android_epoch(value)
            elif key_name == "LinkKey":
                current_linkkey = value

    if current_mac:
        key = (current_mac.lower(), current_name.lower())
        if key not in seen:
            connections.append(
                {
                    "name": current_name,
                    "mac": current_mac,
                    "timestamp": current_timestamp,
                    "linkkey": current_linkkey,
                }
            )
    return adapter_info, connections


def parse_wifi_configstore2(path: Path) -> list[dict[str, str]]:
    root = _parse_android_xml(path)
    rows: list[dict[str, str]] = []
    if root is None:
        return rows

    for network in root.iter("Network"):
        values: dict[str, str] = {}
        for elem in network.iter():
            field_name = normalize_text(elem.attrib.get("name", "")).strip()
            if not field_name:
                continue
            field_value = normalize_text(elem.attrib.get("value", "")).strip() or normalize_text(elem.text).strip()
            values[field_name] = field_value
        if values.get("SSID") or values.get("BSSID"):
            rows.append(values)
    return rows


def parse_wifi_profiles(path: Path) -> list[dict[str, str]]:
    root = _parse_android_xml(path)
    rows: list[dict[str, str]] = []
    if root is None:
        return rows

    for network in root.iter("Network"):
        row: dict[str, str] = {}
        for elem in network.iter():
            field_name = normalize_text(elem.attrib.get("name", "")).strip()
            if not field_name:
                continue
            field_value = normalize_text(elem.attrib.get("value", "")).strip() or normalize_text(elem.text).strip()
            if field_value:
                row[field_name] = field_value
        if row.get("SSID") or row.get("ConfigKey"):
            rows.append(row)
    return rows


def parse_android_auto(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    safe_path = str(path.resolve()).replace("\\", "/")
    db = sqlite3.connect(f"file:{safe_path}?mode=ro", uri=True)
    try:
        cursor = db.cursor()
        cursor.execute(
            """
            SELECT connectiontime, manufacturer, model, modelyear, bluetoothaddress, wifissid, wifibssid, wifipassword
            FROM allowedcars
            """
        )
        for connectiontime, manufacturer, model, modelyear, bluetoothaddress, wifissid, wifibssid, wifipassword in cursor.fetchall():
            rows.append(
                {
                    "connectiontime": _normalize_android_epoch(connectiontime),
                    "manufacturer": normalize_text(manufacturer).strip(),
                    "model": normalize_text(model).strip(),
                    "modelyear": normalize_text(modelyear).strip(),
                    "bluetoothaddress": normalize_text(bluetoothaddress).strip(),
                    "wifissid": normalize_text(wifissid).strip(),
                    "wifibssid": normalize_text(wifibssid).strip(),
                    "wifipassword": normalize_text(wifipassword).strip(),
                }
            )
    finally:
        db.close()
    return rows


def parse_siminfo(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    safe_path = str(path.resolve()).replace("\\", "/")
    db = sqlite3.connect(f"file:{safe_path}?mode=ro", uri=True)
    try:
        cursor = db.cursor()
        try:
            cursor.execute(
                """
                SELECT number, imsi, display_name, carrier_name, iso_country_code, carrier_id, icc_id
                FROM siminfo
                """
            )
        except Exception:
            cursor.execute(
                """
                SELECT number, card_id, display_name, carrier_name, carrier_name, carrier_name, icc_id
                FROM siminfo
                """
            )
        for number, imsi, display_name, carrier_name, iso_country_code, carrier_id, icc_id in cursor.fetchall():
            rows.append(
                {
                    "number": normalize_text(number).strip(),
                    "imsi": normalize_text(imsi).strip(),
                    "display_name": normalize_text(display_name).strip(),
                    "carrier_name": normalize_text(carrier_name).strip(),
                    "iso_country_code": normalize_text(iso_country_code).strip(),
                    "carrier_id": normalize_text(carrier_id).strip(),
                    "icc_id": normalize_text(icc_id).strip(),
                }
            )
    finally:
        db.close()
    return rows


def parse_adb_hosts(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with open(windows_safe_path(path), "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                adb_host = line.split(" ", 1)[1].strip()
            except Exception:
                continue
            if not adb_host:
                continue
            username, _, hostname = adb_host.partition("@")
            rows.append(
                {
                    "username": normalize_text(username).strip(),
                    "hostname": normalize_text(hostname).strip(),
                    "host": normalize_text(adb_host).strip(),
                }
            )
    return rows


def parse_usagestats_version(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    with open(windows_safe_path(path), "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            splits = line.strip().split(";")
            if len(splits) >= 3:
                result["android_version"] = normalize_text(splits[0]).strip()
                result["codename"] = normalize_text(splits[1]).strip()
                result["build_version"] = normalize_text(splits[2]).strip()
            if len(splits) == 5:
                result["country_specific_code"] = normalize_text(splits[3]).strip()
            if result:
                break
    return result


def parse_alex_device_info(path: Path) -> list[dict[str, str]]:
    import json

    rows: list[dict[str, str]] = []
    with open(windows_safe_path(path), "r", encoding="utf-8", errors="replace") as handle:
        info_data = json.load(handle)
    if isinstance(info_data, list) and info_data:
        info_data = info_data[1:]
    for pair in info_data or []:
        if not isinstance(pair, dict):
            continue
        for key, value in pair.items():
            value_text = normalize_text(value).strip()
            if value_text and value_text != "-":
                rows.append({"key": normalize_text(key).strip(), "value": value_text})
    return rows
