import plistlib
import sqlite3
import datetime as dt
import re
import base64
import xml.etree.ElementTree as ET
from pathlib import Path

from scripts.utils import normalize_text, windows_safe_path

APPLE_MODEL_NAME_MAP = {
    "iPhone16,2": "iPhone 15 Pro Max",
    "iPhone16,1": "iPhone 15 Pro",
    "iPhone15,5": "iPhone 15 Plus",
    "iPhone15,4": "iPhone 15",
    "iPhone15,3": "iPhone 14 Pro Max",
    "iPhone15,2": "iPhone 14 Pro",
    "iPhone14,8": "iPhone 14 Plus",
    "iPhone14,7": "iPhone 14",
    "iPhone14,6": "iPhone SE 3rd gen",
    "iPhone14,5": "iPhone 13",
    "iPhone14,4": "iPhone 13 mini",
    "iPhone14,3": "iPhone 13 Pro Max",
    "iPhone14,2": "iPhone 13 Pro",
    "iPhone13,4": "iPhone 12 Pro Max",
    "iPhone13,3": "iPhone 12 Pro",
    "iPhone13,2": "iPhone 12",
    "iPhone13,1": "iPhone 12 mini",
    "iPhone12,8": "iPhone SE 2nd gen",
    "iPhone12,5": "iPhone 11 Pro Max",
    "iPhone12,3": "iPhone 11 Pro",
    "iPhone12,1": "iPhone 11",
    "iPhone11,8": "iPhone XR",
    "iPhone11,6": "iPhone XS Max",
    "iPhone11,2": "iPhone XS",
    "iPhone10,6": "iPhone X",
    "iPhone10,5": "iPhone 8 Plus",
    "iPhone10,4": "iPhone 8",
    "iPhone9,4": "iPhone 7 Plus",
    "iPhone9,3": "iPhone 7",
    "iPhone8,4": "iPhone SE 1st gen",
    "iPhone8,2": "iPhone 6s Plus",
    "iPhone8,1": "iPhone 6s",
    "iPhone7,2": "iPhone 6",
    "iPhone7,1": "iPhone 6 Plus",
    "iPhone6,2": "iPhone 5S",
    "iPhone6,1": "iPhone 5S",
    "iPhone5,4": "iPhone 5C",
    "iPhone5,3": "iPhone 5C",
    "iPhone5,2": "iPhone 5",
    "iPhone5,1": "iPhone 5",
    "iPad14,6": "iPad Pro (6th gen 12.9\")",
    "iPad14,5": "iPad Pro (6th gen 12.9\")",
    "iPad14,4": "iPad Pro (4th gen 11\")",
    "iPad14,3": "iPad Pro (4th gen 11\")",
    "iPad14,2": "iPad Mini (6th gen)",
    "iPad14,1": "iPad Mini (6th gen)",
    "iPad13,19": "iPad 10th gen",
    "iPad13,18": "iPad 10th gen",
    "iPad13,17": "iPad Air (5th gen)",
    "iPad13,16": "iPad Air (5th gen)",
    "iPad13,11": "iPad Pro (5th gen 12.9\")",
    "iPad13,10": "iPad Pro (5th gen 12.9\")",
    "iPad13,9": "iPad Pro (5th gen 12.9\")",
    "iPad13,8": "iPad Pro (5th gen 12.9\")",
    "iPad13,7": "iPad Pro (5th gen 11\")",
    "iPad13,6": "iPad Pro (5th gen 11\")",
    "iPad13,5": "iPad Pro (5th gen 11\")",
    "iPad13,4": "iPad Pro (5th gen 11\")",
    "iPad13,2": "iPad Air (4th gen)",
    "iPad13,1": "iPad Air (4th gen)",
    "iPad12,2": "iPad 9th gen",
    "iPad12,1": "iPad 9th gen",
    "iPad11,7": "iPad 8th gen",
    "iPad11,6": "iPad 8th gen",
    "iPad11,4": "iPad Air (3rd gen)",
    "iPad11,3": "iPad Air (3rd gen)",
    "iPad11,2": "iPad Mini (5th gen)",
    "iPad11,1": "iPad Mini (5th gen)",
}


def get_apple_model_name(model_id: str) -> str:
    normalized = normalize_text(model_id).strip()
    if not normalized:
        return ""
    return APPLE_MODEL_NAME_MAP.get(normalized, "")


def parse_commcenter_device_specific(path: Path) -> dict[str, str]:
    with open(windows_safe_path(path), "rb") as handle:
        plist = plistlib.load(handle)
    if not isinstance(plist, dict):
        return {}
    return {
        "imeis": normalize_text(plist.get("imeis", "")).strip(),
        "reported_phone_number": normalize_text(plist.get("ReportedPhoneNumber", "")).strip(),
    }


def parse_mobilebluetooth_devices(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with open(windows_safe_path(path), "rb") as handle:
        plist = plistlib.load(handle)
    if not isinstance(plist, dict):
        return rows
    for macaddress, values in plist.items():
        if not isinstance(values, dict):
            continue
        rows.append(
            {
                "name": normalize_text(values.get("Name") or values.get("DefaultName") or values.get("UserNameKey") or macaddress).strip(),
                "identifier": normalize_text(macaddress).strip(),
                "last_seen": normalize_text(values.get("LastSeenTime", "")).strip(),
                "product_id": normalize_text(values.get("DeviceIdProduct", "")).strip(),
            }
        )
    return rows


def _open_sqlite_readonly(path: Path):
    safe_path = str(path.resolve()).replace("\\", "/")
    return sqlite3.connect(f"file:{safe_path}?mode=ro", uri=True)


def parse_mobilebluetooth_paired_le(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    db = _open_sqlite_readonly(path)
    try:
        cursor = db.cursor()
        cursor.execute(
            """
            SELECT Name, COALESCE(ResolvedAddress, Address, Uuid), LastConnectionTime
            FROM PairedDevices
            """
        )
        for name, identifier, last_connection in cursor.fetchall():
            rows.append(
                {
                    "name": normalize_text(name or identifier).strip(),
                    "identifier": normalize_text(identifier).strip(),
                    "last_connection": normalize_text(last_connection).strip(),
                }
            )
    finally:
        db.close()
    return rows


def parse_mobilebluetooth_other_le(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    db = _open_sqlite_readonly(path)
    try:
        cursor = db.cursor()
        cursor.execute(
            """
            SELECT Name, COALESCE(Address, Uuid), LastSeenTime
            FROM OtherDevices
            """
        )
        for name, identifier, last_seen in cursor.fetchall():
            rows.append(
                {
                    "name": normalize_text(name or identifier).strip(),
                    "identifier": normalize_text(identifier).strip(),
                    "last_seen": normalize_text(last_seen).strip(),
                }
            )
    finally:
        db.close()
    return rows


def parse_wifi_known_networks(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with open(windows_safe_path(path), "rb") as handle:
        plist = plistlib.load(handle)

    if isinstance(plist, dict) and "List of known networks" in plist:
        networks = plist.get("List of known networks", [])
    elif isinstance(plist, dict):
        networks = plist.values()
    else:
        networks = []

    for network in networks:
        if not isinstance(network, dict):
            continue
        ssid = network.get("SSID_STR") or network.get("SSID") or ""
        if isinstance(ssid, bytes):
            ssid = ssid.decode("utf-8", errors="ignore")
        os_specific = network.get("__OSSpecific__", {})
        if not isinstance(os_specific, dict):
            os_specific = {}
        bssid = network.get("BSSID") or os_specific.get("BSSID") or ""
        wps_info = network.get("WPS_PROB_RESP_IE", {})
        if not isinstance(wps_info, dict):
            wps_info = {}
        rows.append(
            {
                "ssid": normalize_text(ssid).strip(),
                "bssid": normalize_text(bssid).strip(),
                "device_name": normalize_text(wps_info.get("IE_KEY_WPS_DEV_NAME", "")).strip(),
                "manufacturer": normalize_text(wps_info.get("IE_KEY_WPS_MANUFACTURER", "")).strip(),
                "model_name": normalize_text(wps_info.get("IE_KEY_WPS_MODEL_NAME", "")).strip(),
                "last_joined": normalize_text(
                    network.get("lastJoined")
                    or network.get("JoinedBySystemAt")
                    or network.get("JoinedByUserAt")
                    or os_specific.get("prevJoined")
                    or ""
                ).strip(),
            }
        )
    return rows


def parse_wifinetworkstoremodel(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    db = _open_sqlite_readonly(path)
    try:
        cursor = db.cursor()
        cursor.execute(
            """
            SELECT ZNETWORK.ZSSID, ZGEOTAG.ZBSSID, DATETIME(ZGEOTAG.ZDATE+978307200,'unixepoch')
            FROM ZNETWORK
            LEFT JOIN ZGEOTAG ON ZGEOTAG.Z_PK = ZNETWORK.Z_PK
            ORDER BY ZGEOTAG.ZDATE DESC
            """
        )
        for ssid, bssid, last_connected in cursor.fetchall():
            rows.append(
                {
                    "ssid": normalize_text(ssid).strip(),
                    "bssid": normalize_text(bssid).strip(),
                    "last_connected": normalize_text(last_connected).strip(),
                }
            )
    finally:
        db.close()
    return rows


def parse_consolidated_serials(path: Path) -> list[str]:
    serials: list[str] = []
    db = _open_sqlite_readonly(path)
    try:
        cursor = db.cursor()
        cursor.execute(
            """
            SELECT DISTINCT SerialNumber
            FROM TableInfo
            WHERE SerialNumber IS NOT NULL AND TRIM(SerialNumber) != ''
            """
        )
        for (serial_number,) in cursor.fetchall():
            serial_text = normalize_text(serial_number).strip()
            if serial_text:
                serials.append(serial_text)
    finally:
        db.close()
    return serials


def parse_preferences_plist(path: Path) -> dict[str, str]:
    with open(windows_safe_path(path), "rb") as handle:
        plist = plistlib.load(handle)
    if not isinstance(plist, dict):
        return {}
    result = {"model_id": normalize_text(plist.get("Model", "")).strip()}
    system_value = plist.get("System", {})
    if isinstance(system_value, dict):
        network_value = system_value.get("Network", {})
        if isinstance(network_value, dict):
            host_names = network_value.get("HostNames", {})
            if isinstance(host_names, dict):
                result["local_host_name"] = normalize_text(host_names.get("LocalHostName", "")).strip()
        system_inner = system_value.get("System", {})
        if isinstance(system_inner, dict):
            result["device_name"] = normalize_text(system_inner.get("ComputerName", "")).strip()
            result["host_name"] = normalize_text(system_inner.get("HostName", "")).strip()
    result["model_name"] = get_apple_model_name(result.get("model_id", ""))
    return result


def parse_system_version_plist(path: Path) -> dict[str, str]:
    with open(windows_safe_path(path), "rb") as handle:
        plist = plistlib.load(handle)
    if not isinstance(plist, dict):
        return {}
    return {
        "product_build_version": normalize_text(plist.get("Product Build Version", "")).strip(),
        "product_version": normalize_text(plist.get("ProductVersion", "")).strip(),
        "product_name": normalize_text(plist.get("ProductName", "")).strip(),
        "build_id": normalize_text(plist.get("BuildID", "")).strip(),
        "system_image_id": normalize_text(plist.get("SystemImageID", "")).strip(),
    }


def parse_device_values_plist(path: Path) -> dict[str, str]:
    with open(windows_safe_path(path), "rb") as handle:
        plist = plistlib.load(handle)
    if not isinstance(plist, dict):
        return {}
    product_type = normalize_text(plist.get("ProductType", "")).strip()
    return {
        "product_version": normalize_text(plist.get("ProductVersion", "")).strip(),
        "build_version": normalize_text(plist.get("BuildVersion", "")).strip(),
        "product_type": product_type,
        "product_type_name": get_apple_model_name(product_type),
        "hardware_model": normalize_text(plist.get("HardwareModel", "")).strip(),
        "imei": normalize_text(plist.get("InternationalMobileEquipmentIdentity", "")).strip(),
        "serial_number": normalize_text(plist.get("SerialNumber", "")).strip(),
        "device_name": normalize_text(plist.get("DeviceName", "")).strip(),
        "password_protected": normalize_text(plist.get("PasswordProtected", "")).strip(),
        "time_zone": normalize_text(plist.get("TimeZone", "")).strip(),
    }


def parse_advertising_id(path: Path) -> str:
    with open(windows_safe_path(path), "rb") as handle:
        plist = plistlib.load(handle)
    if not isinstance(plist, dict):
        return ""
    return normalize_text(plist.get("LSAdvertiserIdentifier", "")).strip()


def parse_airdrop_id(path: Path) -> dict[str, str]:
    with open(windows_safe_path(path), "rb") as handle:
        plist = plistlib.load(handle)
    if not isinstance(plist, dict):
        return {}
    return {
        "airdrop_id": normalize_text(plist.get("AirDropID", "")).strip(),
        "discoverable_mode": normalize_text(plist.get("DiscoverableMode", "")).strip(),
    }


def parse_cellular_wireless(path: Path) -> dict[str, str]:
    with open(windows_safe_path(path), "rb") as handle:
        plist = plistlib.load(handle)
    if not isinstance(plist, dict):
        return {}
    return {
        "reported_phone_number": normalize_text(plist.get("ReportedPhoneNumber", "")).strip(),
        "cdma_network_phone_number_iccid": normalize_text(plist.get("CDMANetworkPhoneNumberICCID", "")).strip(),
        "imei": normalize_text(plist.get("imei", "")).strip(),
        "last_known_iccid": normalize_text(plist.get("LastKnownICCID", "")).strip(),
        "meid": normalize_text(plist.get("meid", "")).strip(),
    }


def parse_imei_imsi(path: Path) -> dict[str, str]:
    with open(windows_safe_path(path), "rb") as handle:
        plist = plistlib.load(handle)
    if not isinstance(plist, dict):
        return {}
    result = {
        "last_known_icci": normalize_text(plist.get("LastKnownICCI", "")).strip(),
        "phone_number": normalize_text(plist.get("PhoneNumber", "")).strip(),
    }
    personal_wallet = plist.get("PersonalWallet")
    if isinstance(personal_wallet, dict) and personal_wallet:
        try:
            wallet_value = list(personal_wallet.values())[0]
            carrier = wallet_value.get("CarrierEntitlements", {}) if isinstance(wallet_value, dict) else {}
            if isinstance(carrier, dict):
                result["last_good_imsi"] = normalize_text(carrier.get("lastGoodImsi", "")).strip()
                result["self_registration_update_imsi"] = normalize_text(carrier.get("kEntitlementsSelfRegistrationUpdateImsi", "")).strip()
                result["self_registration_update_imei"] = normalize_text(carrier.get("kEntitlementsSelfRegistrationUpdateImei", "")).strip()
        except Exception:
            pass
    return result


def parse_subscriber_info(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    db = _open_sqlite_readonly(path)
    try:
        cursor = db.cursor()
        cursor.execute(
            """
            SELECT last_update_time, slot_id, subscriber_id, subscriber_mdn
            FROM subscriber_info
            """
        )
        for last_update_time, slot_id, subscriber_id, subscriber_mdn in cursor.fetchall():
            timestamp = ""
            try:
                cocoa = float(last_update_time)
                timestamp = dt.datetime.utcfromtimestamp(cocoa + 978307200).strftime("%Y-%m-%d %H:%M:%S UTC")
            except Exception:
                timestamp = normalize_text(last_update_time).strip()
            rows.append(
                {
                    "last_update_time": timestamp,
                    "slot_id": normalize_text(slot_id).strip(),
                    "iccid": normalize_text(subscriber_id).strip(),
                    "msisdn": normalize_text(subscriber_mdn).strip(),
                }
            )
    finally:
        db.close()
    return rows


def parse_device_activator(path: Path) -> dict[str, str]:
    with open(windows_safe_path(path), "r", encoding="utf-8", errors="replace") as handle:
        alllines = "".join(line.strip() for line in handle)
    found = re.findall(r"<key>ActivationInfoXML</key><data>(.*)</data><key>RKCertification</key><data>", alllines)
    if not found:
        return {}
    data = base64.b64decode(found[0])
    root = ET.fromstring(data)
    values: list[str] = []
    for elem in root:
        for elemx in elem:
            for elemz in elemx:
                values.append(normalize_text(elemz.text).strip())
    pairs = list(zip(values[::2], values[1::2]))
    result: dict[str, str] = {}
    for key, value in pairs:
        if key == "EthernetMacAddress":
            result["ethernet_mac_address"] = value
        elif key == "BluetoothAddress":
            result["bluetooth_address"] = value
        elif key == "WifiAddress":
            result["wifi_address"] = value
        elif key == "ModelNumber":
            result["model_number"] = value
    return result


def parse_device_name(path: Path) -> str:
    with open(windows_safe_path(path), "rb") as handle:
        plist = plistlib.load(handle)
    if not isinstance(plist, dict):
        return ""
    return normalize_text(plist.get("-DeviceName", "")).strip()


def parse_timezone_info(path: Path) -> dict[str, str]:
    with open(windows_safe_path(path), "rb") as handle:
        plist = plistlib.load(handle)
    if not isinstance(plist, dict):
        return {}
    last_bootstrap_date = ""
    raw_date = plist.get("lastBootstrapDate")
    if raw_date not in (None, ""):
        try:
            last_bootstrap_date = dt.datetime.utcfromtimestamp(float(raw_date) + 978307200).strftime("%Y-%m-%d %H:%M:%S UTC")
        except Exception:
            last_bootstrap_date = normalize_text(raw_date).strip()
    return {
        "last_bootstrap_timezone": normalize_text(plist.get("lastBootstrapTimeZone", "")).strip(),
        "last_bootstrap_date": last_bootstrap_date,
    }


def parse_itunes_backup_info(path: Path) -> dict[str, str]:
    with open(windows_safe_path(path), "rb") as handle:
        plist = plistlib.load(handle)
    if not isinstance(plist, dict):
        return {}
    keys = (
        "Product Name",
        "Product Type",
        "Device Name",
        "Product Version",
        "Build Version",
        "Serial Number",
        "MEID",
        "IMEI",
        "IMEI 2",
        "ICCID",
        "Phone Number",
        "Unique Identifier",
        "Last Backup Date",
    )
    result = {key: normalize_text(plist.get(key, "")).strip() for key in keys}
    product_type = result.get("Product Type", "")
    result["Product Type Name"] = get_apple_model_name(product_type)
    return result
