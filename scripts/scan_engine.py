import csv
import io
import json
import plistlib
import re
import sqlite3
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

try:
    import nska_deserialize
except Exception:  # pragma: no cover - optional dependency in some environments
    nska_deserialize = None

from scripts.artifacts.stella import (
    extract_stella_case_settings,
    extract_stella_derived_sku,
    extract_stella_device_sync,
    source_is_stella_account_artifact,
    source_is_stella_case_settings_artifact,
    source_is_stella_derived_sku_artifact,
    source_is_stella_sync_log_artifact,
)
from scripts.artifacts.meta_glasses_android import (
    dedupe_prompt_records,
    extract_android_meta_app_profiles,
    extract_android_meta_devices_and_sync,
    extract_android_prompts_from_graphql_cache,
    extract_android_prompts_from_interaction_log,
    extract_android_prompts_from_sqlite_fallback,
    source_is_android_graphql_cache,
    source_is_android_interaction_log_db,
    source_is_android_stella_db,
)
from scripts.aleapp_device_parsers import (
    parse_adb_hosts,
    parse_alex_device_info,
    parse_android_auto,
    parse_bluetooth_connections,
    parse_build_prop,
    parse_siminfo,
    parse_settings_secure,
    parse_usagestats_version,
    parse_wifi_configstore2,
    parse_wifi_profiles,
)
from scripts.ileapp_device_parsers import (
    parse_advertising_id,
    parse_airdrop_id,
    parse_cellular_wireless,
    parse_commcenter_device_specific,
    parse_consolidated_serials,
    parse_device_activator,
    parse_device_name,
    parse_device_values_plist,
    parse_imei_imsi,
    parse_itunes_backup_info,
    parse_mobilebluetooth_devices,
    parse_mobilebluetooth_other_le,
    parse_mobilebluetooth_paired_le,
    parse_preferences_plist,
    parse_subscriber_info,
    parse_system_version_plist,
    parse_timezone_info,
    parse_wifinetworkstoremodel,
    parse_wifi_known_networks,
)
from scripts.models import (
    AccountRecord,
    AndroidMetaAppProfileRecord,
    AndroidMetaDeviceRecord,
    AndroidMetaSyncRecord,
    CaseMetadata,
    IdentifierRecord,
    MediaRecord,
    PromptRecord,
    StellaCaseSettingsRecord,
    StellaDerivedSkuRecord,
    StellaDeviceSyncRecord,
)
from scripts.pipeline import MEDIA_EXTENSIONS, archive_member_is_relevant
from scripts.utils import (
    basename,
    clean_log_string,
    device_entry,
    extract_media_identifiers,
    is_metaglasses_make_model,
    normalize_gps_coordinate,
    normalize_meta_timestamp,
    normalize_phone_identifier_value,
    normalize_text,
    suffix_key,
    windows_safe_path,
)


TEXT_EXTENSIONS = {
    ".csv",
    ".json",
    ".log",
    ".plist",
    ".strings",
    ".tsv",
    ".txt",
}

STRUCTURED_EXTENSIONS = TEXT_EXTENSIONS | {
    ".db",
    ".sqlite",
    ".sqlite3",
}

PATH_KEYWORDS = (
    "meta",
    "meta ai",
    "rayban",
    "ray-ban",
    "smart glasses",
    "stella",
    "wearable",
)

IDENTIFIER_KEYWORDS = (
    "account",
    "appversion",
    "case",
    "device",
    "firmware",
    "frame",
    "glasses",
    "instagram",
    "lens",
    "model",
    "serial",
    "sku",
    "sync",
    "username",
    "version",
)

KNOWN_FILE_HINTS = (
    "com.facebook.stellaapp",
    "com.meta.mwa",
    "glasses",
    "metaai-log-",
    "rayban",
    "ray-ban",
    "stella",
)

GLASSES_IDENTIFIER_KEYWORDS = (
    "appversion",
    "firmware",
    "frame",
    "glasses",
    "lens",
    "model",
    "rayban",
    "ray-ban",
    "serial",
    "sku",
    "stella",
    "wearable",
)

GLASSES_VALUE_HINTS = (
    "firmware",
    "frame",
    "glasses",
    "meta",
    "rayban",
    "ray-ban",
    "smart glasses",
    "stella",
    "wearable",
)

PHONE_EXCLUSION_KEYWORDS = (
    "android",
    "baseband",
    "cellular",
    "handset",
    "icc",
    "imei",
    "imsi",
    "ios",
    "ipad",
    "iphone",
    "meid",
    "mobile",
    "phone",
    "sim",
    "subscriber",
    "telephony",
)

PROMPT_RE = re.compile(
    r'SilverstoneModels\.SLVPostTitle\(text:\s*Optional\("(?P<prompt>.*?)"\),\s*mediaItems:'
)
RESPONSE_RE = re.compile(r"Last Response agent option:\s*(?P<response>.*)$")
TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d{3})?")
GENERIC_ID_RE = re.compile(
    r"\b(?:[A-Z0-9]{6,}|[0-9]{5,}|[A-F0-9]{8,}|[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,})\b",
    re.IGNORECASE,
)
ACCOUNT_INCLUDE_KEYWORDS = (
    "account",
    "user",
    "username",
    "email",
    "name",
    "handle",
    "profile",
    "provider",
    "service",
    "platform",
    "instagram",
    "facebook",
    "messenger",
    "whatsapp",
    "meta",
    "id",
)
ACCOUNT_EXCLUDE_KEYWORDS = (
    "access",
    "auth",
    "credential",
    "password",
    "refresh",
    "secret",
    "token",
)
ANDROID_DEVICE_ARTIFACT_FILENAMES = {
    "settings_secure.xml",
    "bt_config.conf",
    "wificonfigstore.xml",
    "build.prop",
    "carservicedata.db",
}


def normalize_input_mode(input_mode: str = "auto") -> str:
    mode = str(input_mode or "auto").strip().lower()
    if mode not in {"auto", "apple", "android"}:
        return "auto"
    return mode


def path_is_android_specific(path: Path) -> bool:
    lowered = str(path).replace("\\", "/").lower()
    return (
        source_is_android_stella_db(path)
        or source_is_android_graphql_cache(path)
        or source_is_android_interaction_log_db(path)
        or source_is_android_device_artifact(path)
        or "/shared_prefs/" in lowered
        or "/graphql_response_cache/" in lowered
        or "/system/users/" in lowered
        or "/misc/wifi/" in lowered
        or "/apexdata/com.android.wifi/" in lowered
        or lowered.endswith("/vendor/build.prop")
    )


def path_is_apple_specific(path: Path) -> bool:
    lowered = str(path).replace("\\", "/").lower()
    return (
        lowered.endswith(".plist")
        or "com.apple." in lowered
        or "/private/var/" in lowered
        or "/mobile/containers/" in lowered
        or "photodata/" in lowered
        or basename(path).lower() in {
            "photos.sqlite",
            "wifinetworkstoremodel.sqlite",
            "consolidated.db",
            "com.apple.commcenter.device_specific_nobackup.plist",
            "com.apple.mobilebluetooth.devices.plist",
            "com.apple.mobilebluetooth.ledevices.paired.db",
            "com.apple.wifi.plist",
            "com.apple.wifi.known-networks.plist",
        }
    )


def flatten_object(value: Any, prefix: str = ""):
    if isinstance(value, dict):
        for key, item in value.items():
            next_prefix = f"{prefix}.{key}" if prefix else str(key)
            yield from flatten_object(item, next_prefix)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            next_prefix = f"{prefix}[{index}]"
            yield from flatten_object(item, next_prefix)
    else:
        yield prefix, value


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


def source_is_android_device_artifact(path: Path | str) -> bool:
    return Path(str(path)).name.lower() in ANDROID_DEVICE_ARTIFACT_FILENAMES


def path_matches_input_mode(path: Path, input_mode: str = "auto") -> bool:
    mode = normalize_input_mode(input_mode)
    if mode == "auto":
        return True

    is_android_specific = path_is_android_specific(path)
    is_apple_specific = path_is_apple_specific(path)

    if mode == "apple":
        return not is_android_specific
    if mode == "android":
        if is_android_specific:
            return True
        if is_apple_specific:
            return False
        return safe_suffix(path) in MEDIA_EXTENSIONS
    return True


def key_looks_interesting(key: str) -> bool:
    lowered = key.lower()
    return any(keyword in lowered for keyword in IDENTIFIER_KEYWORDS)


def value_looks_interesting(value: str) -> bool:
    lowered = value.lower()
    return any(keyword in lowered for keyword in PATH_KEYWORDS) or bool(GENERIC_ID_RE.search(value))


def text_contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in keywords)


def looks_like_phone_identifier(key: str, value: str, source_path: Path, context: str = "") -> bool:
    combined = " ".join((key, value, str(source_path), context))
    return text_contains_any(combined, PHONE_EXCLUSION_KEYWORDS)


def looks_like_glasses_identifier(key: str, value: str, source_path: Path, context: str = "") -> bool:
    if looks_like_phone_identifier(key, value, source_path, context):
        return False

    path_text = str(source_path)
    if text_contains_any(path_text, KNOWN_FILE_HINTS):
        if text_contains_any(key, GLASSES_IDENTIFIER_KEYWORDS):
            return True
        if text_contains_any(value, GLASSES_VALUE_HINTS):
            return True
        if text_contains_any(context, GLASSES_VALUE_HINTS):
            return True

    return (
        text_contains_any(key, GLASSES_IDENTIFIER_KEYWORDS)
        or text_contains_any(value, GLASSES_VALUE_HINTS)
        or text_contains_any(context, GLASSES_VALUE_HINTS)
    )


def collect_detected_devices_summary(
    candidate_files: list[Path],
    case_metadata: CaseMetadata | None,
    media_records: list[MediaRecord],
    android_device_records: list[AndroidMetaDeviceRecord] | None = None,
    android_sync_records: list[AndroidMetaSyncRecord] | None = None,
) -> dict[str, list[dict[str, str]]]:
    summary = {
        "Phone Identifiers": [],
        "Bluetooth": [],
        "Wi-Fi": [],
        "Media / Companion": [],
    }
    seen = {key: set() for key in summary}

    def add_entry(category: str, name: str, identifier: str = "", source: str = "", previously_connected: str = "Unknown", details: str = ""):
        key = (
            str(name or "").strip().lower(),
            str(identifier or "").strip().lower(),
            str(source or "").strip().lower(),
        )
        if not key[0] and not key[1]:
            return
        if key in seen[category]:
            return
        seen[category].add(key)
        summary[category].append(device_entry(name, identifier, source, previously_connected, details))

    def add_phone_identifier(name: str, identifier: Any, source: str, details: str):
        normalized = normalize_phone_identifier_value(identifier)
        if not normalized:
            return
        add_entry("Phone Identifiers", name, normalized, source, "Observed", details)

    def open_sqlite_readonly(path: Path):
        try:
            safe_path = str(path.resolve()).replace("\\", "/")
            return sqlite3.connect(f"file:{safe_path}?mode=ro", uri=True)
        except Exception:
            return None

    metadata = case_metadata or CaseMetadata()
    if metadata.owner:
        add_entry("Phone Identifiers", "Owner", metadata.owner, "Case Metadata", "Supplied", "Examiner-entered device owner")
    if metadata.imei:
        add_entry("Phone Identifiers", "IMEI", metadata.imei, "Case Metadata", "Supplied", "Examiner-entered phone identifier")
    if metadata.serial_number:
        add_entry("Phone Identifiers", "Serial Number", metadata.serial_number, "Case Metadata", "Supplied", "Examiner-entered phone identifier")

    for record in media_records:
        make_name = record.exif_make.strip()
        model_name = record.exif_model.strip()
        software_name = record.exif_software.strip()
        date_time_value = record.exif_datetime.strip()
        if not make_name and not model_name and not software_name:
            continue
        device_name = " ".join(part for part in (make_name, model_name) if part).strip() or Path(record.media_path).name
        details_bits = [f"Asset: {Path(record.media_path).name}"]
        if make_name:
            details_bits.append(f"Make: {make_name}")
        if model_name:
            details_bits.append(f"Model: {model_name}")
        if software_name:
            details_bits.append(f"Software: {software_name}")
        if date_time_value:
            details_bits.append(f"Date/Time: {date_time_value}")
        add_entry("Media / Companion", device_name, Path(record.media_path).name, "Embedded media metadata", "Observed", " | ".join(details_bits))

    seen_android_media: set[tuple[str, str]] = set()
    for device_record in android_device_records or []:
        identifier = (
            normalize_text(device_record.device_id).strip()
            or normalize_text(device_record.pairing_id).strip()
            or normalize_text(device_record.serial).strip()
        )
        display_name = (
            normalize_text(device_record.device_codename).strip()
            or normalize_text(device_record.source).strip()
            or identifier
            or "Android Meta device"
        )
        detail_parts = [
            part for part in (
                f"Source: {normalize_text(device_record.source).strip()}" if normalize_text(device_record.source).strip() else "",
                f"Serial: {normalize_text(device_record.serial).strip()}" if normalize_text(device_record.serial).strip() else "",
                f"Capture Type: {normalize_text(device_record.capture_type).strip()}" if normalize_text(device_record.capture_type).strip() else "",
            ) if part
        ]
        media_key = (display_name.lower(), identifier.lower())
        if media_key not in seen_android_media:
            seen_android_media.add(media_key)
            add_entry("Media / Companion", display_name, identifier, "Scanned Android device (Meta app records)", "Observed", " | ".join(detail_parts))

        source_blob = " | ".join(
            normalize_text(value).strip()
            for value in (
                device_record.device_codename,
                device_record.source,
                device_record.device_id,
                device_record.pairing_id,
                device_record.serial,
                device_record.attributes,
            )
            if normalize_text(value).strip()
        )
        for mac_match in re.findall(r"(?i)\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b", source_blob):
            add_entry("Bluetooth", display_name, mac_match.upper(), "Scanned Android device (Meta app records)", "Observed", " | ".join(detail_parts))

    for sync_record in android_sync_records or []:
        wifi_blob = normalize_text(sync_record.wifi_scan_data).strip()
        if not wifi_blob:
            continue
        timestamp_text = (
            normalize_text(sync_record.import_completed).strip()
            or normalize_text(sync_record.fetch_completed).strip()
            or normalize_text(sync_record.capture_time).strip()
        )
        ssid_matches = re.findall(r'(?i)(?:ssid|wifi[_ ]?ssid)["=: ]+["\']?([^,"\'}\]\|]+)', wifi_blob)
        bssid_matches = re.findall(r'(?i)(?:bssid|wifi[_ ]?bssid)["=: ]+["\']?((?:[0-9a-f]{2}:){5}[0-9a-f]{2})', wifi_blob)
        if not ssid_matches and not bssid_matches:
            generic_mac_matches = re.findall(r"(?i)\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b", wifi_blob)
            bssid_matches.extend(generic_mac_matches[:3])
        if not ssid_matches and "ssid" not in wifi_blob.lower():
            continue
        if not ssid_matches:
            ssid_matches = [""]
        if not bssid_matches:
            bssid_matches = [""]
        for ssid in ssid_matches[:5]:
            normalized_ssid = normalize_text(ssid).strip().strip('"')
            for bssid in bssid_matches[:5]:
                normalized_bssid = normalize_text(bssid).strip().upper()
                details = " | ".join(
                    part for part in (
                        f"Meta Capture ID: {normalize_text(sync_record.capture_id).strip()}" if normalize_text(sync_record.capture_id).strip() else "",
                        f"Session ID: {normalize_text(sync_record.session_id).strip()}" if normalize_text(sync_record.session_id).strip() else "",
                        f"Observed: {timestamp_text}" if timestamp_text else "",
                    ) if part
                )
                add_entry(
                    "Wi-Fi",
                    normalized_ssid or normalized_bssid or "Android Wi-Fi scan",
                    normalized_bssid,
                    "Scanned Android device (Meta app Wi-Fi scan)",
                    "Observed",
                    details,
                )

    for file_path in candidate_files:
        file_name = basename(file_path).lower()

        if file_name.endswith(".tsv") or file_name.endswith(".csv"):
            delimiter = "\t" if file_name.endswith(".tsv") else ","
            try:
                with open(windows_safe_path(file_path), "r", encoding="utf-8-sig", newline="") as handle:
                    reader = csv.DictReader(handle, delimiter=delimiter)
                    for row in reader:
                        if not isinstance(row, dict):
                            continue
                        make_name = ""
                        model_name = ""
                        for key in (
                            "CMzCldMastMedData-Data_plist_TIFF-24",
                            "AAAzCldMastMedData-Data_plist_TIFF-18",
                            "CMzCldMastMedData-Data_plist_TIFF-23",
                            "AAAzCldMastMedData-Data_plist_TIFF-17",
                        ):
                            value = str(row.get(key) or "").strip()
                            if not value:
                                continue
                            extracted_make, extracted_model = extract_media_identifiers(value)
                            if extracted_make and not make_name:
                                make_name = extracted_make
                            if extracted_model and not model_name:
                                model_name = extracted_model
                            if make_name and model_name:
                                break
                        if not is_metaglasses_make_model(make_name, model_name):
                            continue
                        file_value = (
                            row.get("zAsset-Filename-2")
                            or row.get("zAddAssetAttr- Original Filename-3")
                            or row.get("zCldMast- Original Filename-4")
                            or "Embedded asset"
                        )
                        directory_path = row.get("zAsset-Directory-Path-1", "")
                        details_bits = [f"Asset: {file_value}", f"Make: {make_name}", f"Model: {model_name}"]
                        if directory_path:
                            details_bits.append(f"Path: {directory_path}")
                        add_entry(
                            "Media / Companion",
                            f"{make_name} {model_name}".strip(),
                            str(file_value),
                            "Assets have embedded files",
                            "Embedded media metadata",
                            " | ".join(details_bits),
                        )
            except Exception:
                pass

        if file_name == "com.apple.commcenter.device_specific_nobackup.plist":
            try:
                commcenter = parse_commcenter_device_specific(file_path)
                add_phone_identifier("IMEIs", commcenter.get("imeis", ""), "CommCenter Device Specific", "Recovered from copied iLEAPP parser")
                add_phone_identifier("Reported Phone Number", commcenter.get("reported_phone_number", ""), "CommCenter Device Specific", "Recovered from copied iLEAPP parser")
            except Exception:
                pass

        elif file_name == "com.apple.lsdidentifiers.plist":
            try:
                advertising_id = parse_advertising_id(file_path)
                add_phone_identifier("Apple Advertising Identifier", advertising_id, "Advertising Identifier", "Recovered from copied iLEAPP advertisingID.py parser")
            except Exception:
                pass

        elif file_name == "com.apple.sharingd.plist":
            try:
                airdrop = parse_airdrop_id(file_path)
                add_phone_identifier("AirDrop ID", airdrop.get("airdrop_id", ""), "AirDrop", "Recovered from copied iLEAPP airdropId.py parser")
                if airdrop.get("discoverable_mode"):
                    add_entry("Phone Identifiers", "AirDrop Discoverable Mode", airdrop.get("discoverable_mode", ""), "AirDrop", "Observed", "Recovered from copied iLEAPP airdropId.py parser")
            except Exception:
                pass

        elif file_name == "com.apple.commcenter.plist":
            try:
                cellular = parse_cellular_wireless(file_path)
                add_phone_identifier("Reported Phone Number", cellular.get("reported_phone_number", ""), "Cellular Wireless", "Recovered from copied iLEAPP celWireless.py parser")
                add_phone_identifier("CDMA Network Phone Number ICCID", cellular.get("cdma_network_phone_number_iccid", ""), "Cellular Wireless", "Recovered from copied iLEAPP celWireless.py parser")
                add_phone_identifier("IMEI", cellular.get("imei", ""), "Cellular Wireless", "Recovered from copied iLEAPP celWireless.py parser")
                add_phone_identifier("Last Known ICCID", cellular.get("last_known_iccid", ""), "Cellular Wireless", "Recovered from copied iLEAPP celWireless.py parser")
                add_phone_identifier("MEID", cellular.get("meid", ""), "Cellular Wireless", "Recovered from copied iLEAPP celWireless.py parser")
                imei_imsi = parse_imei_imsi(file_path)
                add_phone_identifier("Last Good IMSI", imei_imsi.get("last_good_imsi", ""), "IMEI - IMSI", "Recovered from copied iLEAPP imeiImsi.py parser")
                add_phone_identifier("Self Registration Update IMSI", imei_imsi.get("self_registration_update_imsi", ""), "IMEI - IMSI", "Recovered from copied iLEAPP imeiImsi.py parser")
                add_phone_identifier("Self Registration Update IMEI", imei_imsi.get("self_registration_update_imei", ""), "IMEI - IMSI", "Recovered from copied iLEAPP imeiImsi.py parser")
                add_phone_identifier("Last Known ICCI", imei_imsi.get("last_known_icci", ""), "IMEI - IMSI", "Recovered from copied iLEAPP imeiImsi.py parser")
                add_phone_identifier("Phone Number", imei_imsi.get("phone_number", ""), "IMEI - IMSI", "Recovered from copied iLEAPP imeiImsi.py parser")
            except Exception:
                pass

        elif file_name == "cellularusage.db":
            try:
                for row in parse_subscriber_info(file_path):
                    details = " | ".join(
                        part for part in (
                            f"Slot: {row.get('slot_id')}" if row.get("slot_id") else "",
                            f"Last Update: {row.get('last_update_time')}" if row.get("last_update_time") else "",
                            "Recovered from copied iLEAPP subscriberInfo.py parser",
                        ) if part
                    )
                    add_phone_identifier("ICCID", row.get("iccid", ""), "Subscriber Info", details)
                    add_phone_identifier("MSISDN", row.get("msisdn", ""), "Subscriber Info", details)
            except Exception:
                pass

        elif file_name == "ucrt_oob_request.txt":
            try:
                activator = parse_device_activator(file_path)
                add_phone_identifier("Ethernet MAC Address", activator.get("ethernet_mac_address", ""), "iOS Device Activator Data", "Recovered from copied iLEAPP deviceActivator.py parser")
                add_phone_identifier("Bluetooth Address", activator.get("bluetooth_address", ""), "iOS Device Activator Data", "Recovered from copied iLEAPP deviceActivator.py parser")
                add_phone_identifier("WiFi Address", activator.get("wifi_address", ""), "iOS Device Activator Data", "Recovered from copied iLEAPP deviceActivator.py parser")
                add_phone_identifier("Model Number", activator.get("model_number", ""), "iOS Device Activator Data", "Recovered from copied iLEAPP deviceActivator.py parser")
            except Exception:
                pass

        elif file_name == "data_ark.plist":
            try:
                device_name = parse_device_name(file_path)
                add_entry("Phone Identifiers", "Device Name", device_name, "Device Name", "Observed", "Recovered from copied iLEAPP deviceName.py parser")
            except Exception:
                pass

        elif file_name == "com.apple.appstore.plist":
            try:
                timezone = parse_timezone_info(file_path)
                add_phone_identifier("Last Bootstrap Timezone", timezone.get("last_bootstrap_timezone", ""), "Timezone Information", "Recovered from copied iLEAPP timezoneInfo.py parser")
                add_phone_identifier("Last Bootstrap Date", timezone.get("last_bootstrap_date", ""), "Timezone Information", "Recovered from copied iLEAPP timezoneInfo.py parser")
            except Exception:
                pass

        elif file_name == "info.plist":
            try:
                backup_info = parse_itunes_backup_info(file_path)
                add_phone_identifier("Apple Product Name", backup_info.get("Product Name", ""), "iTunes Backup Information", "Recovered from copied iLEAPP iTunesBackupInfo.py parser")
                add_phone_identifier("Apple Product Type", backup_info.get("Product Type", ""), "iTunes Backup Information", "Recovered from copied iLEAPP iTunesBackupInfo.py parser")
                add_phone_identifier("Apple Model Name", backup_info.get("Product Type Name", ""), "iTunes Backup Information", "Resolved from copied iLEAPP model-name mapping")
                add_entry("Phone Identifiers", "Device Name", backup_info.get("Device Name", ""), "iTunes Backup Information", "Observed", "Recovered from copied iLEAPP iTunesBackupInfo.py parser")
                add_phone_identifier("Product Version", backup_info.get("Product Version", ""), "iTunes Backup Information", "Recovered from copied iLEAPP iTunesBackupInfo.py parser")
                add_phone_identifier("Build Version", backup_info.get("Build Version", ""), "iTunes Backup Information", "Recovered from copied iLEAPP iTunesBackupInfo.py parser")
                add_phone_identifier("Serial Number", backup_info.get("Serial Number", ""), "iTunes Backup Information", "Recovered from copied iLEAPP iTunesBackupInfo.py parser")
                add_phone_identifier("MEID", backup_info.get("MEID", ""), "iTunes Backup Information", "Recovered from copied iLEAPP iTunesBackupInfo.py parser")
                add_phone_identifier("IMEI", backup_info.get("IMEI", ""), "iTunes Backup Information", "Recovered from copied iLEAPP iTunesBackupInfo.py parser")
                add_phone_identifier("IMEI 2", backup_info.get("IMEI 2", ""), "iTunes Backup Information", "Recovered from copied iLEAPP iTunesBackupInfo.py parser")
                add_phone_identifier("ICCID", backup_info.get("ICCID", ""), "iTunes Backup Information", "Recovered from copied iLEAPP iTunesBackupInfo.py parser")
                add_phone_identifier("Phone Number", backup_info.get("Phone Number", ""), "iTunes Backup Information", "Recovered from copied iLEAPP iTunesBackupInfo.py parser")
                add_phone_identifier("Unique Identifier", backup_info.get("Unique Identifier", ""), "iTunes Backup Information", "Recovered from copied iLEAPP iTunesBackupInfo.py parser")
                add_phone_identifier("Last Backup Date", backup_info.get("Last Backup Date", ""), "iTunes Backup Information", "Recovered from copied iLEAPP iTunesBackupInfo.py parser")
            except Exception:
                pass

        elif file_name == "preferences.plist" and "systemconfiguration" in str(file_path).replace("\\", "/").lower():
            try:
                preferences = parse_preferences_plist(file_path)
                model_id = preferences.get("model_id", "")
                model_name = preferences.get("model_name", "")
                if model_id:
                    model_details = "Recovered from copied iLEAPP preferencesPlist.py parser"
                    if model_name:
                        model_details += f" | Resolved Model: {model_name}"
                    add_phone_identifier("Apple Model ID", model_id, "Preferences PList", model_details)
                if model_name:
                    add_phone_identifier("Apple Model Name", model_name, "Preferences PList", "Resolved from copied iLEAPP model-name mapping")
                if preferences.get("device_name"):
                    add_entry("Phone Identifiers", "Device Name", preferences.get("device_name", ""), "Preferences PList", "Observed", "Recovered from copied iLEAPP preferencesPlist.py parser")
                if preferences.get("local_host_name"):
                    add_entry("Phone Identifiers", "Local Host Name", preferences.get("local_host_name", ""), "Preferences PList", "Observed", "Recovered from copied iLEAPP preferencesPlist.py parser")
                if preferences.get("host_name"):
                    add_entry("Phone Identifiers", "Host Name", preferences.get("host_name", ""), "Preferences PList", "Observed", "Recovered from copied iLEAPP preferencesPlist.py parser")
            except Exception:
                pass

        elif file_name == "systemversion.plist":
            try:
                system_version = parse_system_version_plist(file_path)
                if system_version.get("product_name"):
                    add_phone_identifier("Apple Product Name", system_version.get("product_name", ""), "System Version plist", "Recovered from copied iLEAPP systemVersionPlist.py parser")
                if system_version.get("product_version"):
                    add_phone_identifier("iOS Version", system_version.get("product_version", ""), "System Version plist", "Recovered from copied iLEAPP systemVersionPlist.py parser")
                if system_version.get("product_build_version"):
                    add_phone_identifier("Product Build Version", system_version.get("product_build_version", ""), "System Version plist", "Recovered from copied iLEAPP systemVersionPlist.py parser")
                if system_version.get("build_id"):
                    add_phone_identifier("Build ID", system_version.get("build_id", ""), "System Version plist", "Recovered from copied iLEAPP systemVersionPlist.py parser")
                if system_version.get("system_image_id"):
                    add_phone_identifier("System Image ID", system_version.get("system_image_id", ""), "System Version plist", "Recovered from copied iLEAPP systemVersionPlist.py parser")
            except Exception:
                pass

        elif file_name == "device_values.plist":
            try:
                device_values = parse_device_values_plist(file_path)
                if device_values.get("product_type"):
                    details = "Recovered from copied iLEAPP Ph100UFEDdevcievaluesplist.py parser"
                    if device_values.get("product_type_name"):
                        details += f" | Resolved Model: {device_values.get('product_type_name')}"
                    add_phone_identifier("Apple Product Type", device_values.get("product_type", ""), "UFED device_values.plist", details)
                if device_values.get("product_type_name"):
                    add_phone_identifier("Apple Model Name", device_values.get("product_type_name", ""), "UFED device_values.plist", "Resolved from copied iLEAPP model-name mapping")
                if device_values.get("hardware_model"):
                    add_phone_identifier("Hardware Model", device_values.get("hardware_model", ""), "UFED device_values.plist", "Recovered from copied iLEAPP Ph100UFEDdevcievaluesplist.py parser")
                if device_values.get("imei"):
                    add_phone_identifier("IMEI", device_values.get("imei", ""), "UFED device_values.plist", "Recovered from copied iLEAPP Ph100UFEDdevcievaluesplist.py parser")
                if device_values.get("serial_number"):
                    add_phone_identifier("Serial Number", device_values.get("serial_number", ""), "UFED device_values.plist", "Recovered from copied iLEAPP Ph100UFEDdevcievaluesplist.py parser")
                if device_values.get("device_name"):
                    add_entry("Phone Identifiers", "Device Name", device_values.get("device_name", ""), "UFED device_values.plist", "Observed", "Recovered from copied iLEAPP Ph100UFEDdevcievaluesplist.py parser")
                if device_values.get("product_version"):
                    add_phone_identifier("Product Version", device_values.get("product_version", ""), "UFED device_values.plist", "Recovered from copied iLEAPP Ph100UFEDdevcievaluesplist.py parser")
                if device_values.get("build_version"):
                    add_phone_identifier("Build Version", device_values.get("build_version", ""), "UFED device_values.plist", "Recovered from copied iLEAPP Ph100UFEDdevcievaluesplist.py parser")
            except Exception:
                pass

        elif file_name == "com.apple.mobilebluetooth.devices.plist":
            try:
                for row in parse_mobilebluetooth_devices(file_path):
                    details_bits = []
                    if row.get("product_id"):
                        details_bits.append(f"Product ID: {row.get('product_id')}")
                    if row.get("last_seen"):
                        details_bits.append(f"Last seen: {row.get('last_seen')}")
                    details_bits.append("Parser: copied iLEAPP parser")
                    add_entry("Bluetooth", row.get("name", ""), row.get("identifier", ""), "Bluetooth Paired", "Yes", " | ".join(details_bits))
            except Exception:
                pass

        elif file_name == "com.apple.mobilebluetooth.ledevices.paired.db":
            try:
                for row in parse_mobilebluetooth_paired_le(file_path):
                    details = " | ".join(
                        part for part in (
                            f"Last connection: {row.get('last_connection')}" if row.get("last_connection") else "",
                            "Parser: copied iLEAPP parser",
                        ) if part
                    )
                    add_entry("Bluetooth", row.get("name", ""), row.get("identifier", ""), "Bluetooth Paired LE", "Yes", details)
            except Exception:
                pass

        elif file_name.endswith("com.apple.mobilebluetooth.ledevices.other.db"):
            try:
                for row in parse_mobilebluetooth_other_le(file_path):
                    details = " | ".join(
                        part for part in (
                            f"Last seen: {row.get('last_seen')}" if row.get("last_seen") else "",
                            "Parser: copied iLEAPP parser",
                        ) if part
                    )
                    add_entry("Bluetooth", row.get("name", ""), row.get("identifier", ""), "Bluetooth Other LE", "Observed", details)
            except Exception:
                pass

        elif file_name in ("com.apple.wifi.plist", "com.apple.wifi.known-networks.plist", "com.apple.wifi-networks.plist.backup"):
            try:
                for row in parse_wifi_known_networks(file_path):
                    display_name = row.get("device_name") or row.get("ssid") or row.get("bssid") or "Unknown network"
                    details_bits = []
                    if row.get("ssid") and display_name != row.get("ssid"):
                        details_bits.append(f"SSID: {row.get('ssid')}")
                    if row.get("manufacturer"):
                        details_bits.append(f"Manufacturer: {row.get('manufacturer')}")
                    if row.get("model_name"):
                        details_bits.append(f"Model: {row.get('model_name')}")
                    if row.get("last_joined"):
                        details_bits.append(f"Last joined: {row.get('last_joined')}")
                    details_bits.append("Parser: copied iLEAPP parser")
                    add_entry("Wi-Fi", display_name, row.get("bssid", ""), "WiFi Known Networks", "Yes", " | ".join(details_bits))
            except Exception:
                pass

        elif file_name == "wifinetworkstoremodel.sqlite":
            try:
                for row in parse_wifinetworkstoremodel(file_path):
                    details = " | ".join(
                        part for part in (
                            f"Last connected: {row.get('last_connected')}" if row.get("last_connected") else "",
                            "Parser: copied iLEAPP parser",
                        ) if part
                    )
                    add_entry("Wi-Fi", row.get("ssid", "") or row.get("bssid", "") or "Unknown network", row.get("bssid", ""), "WiFi Network Store Model", "Yes", details)
            except Exception:
                pass

        elif file_name == "consolidated.db":
            try:
                for serial_number in parse_consolidated_serials(file_path):
                    add_phone_identifier("Serial Number", serial_number, "LocationD TableInfo", "Recovered from consolidated.db")
            except Exception:
                pass

        elif file_name == "settings_secure.xml":
            try:
                settings_values = parse_settings_secure(file_path)
                if settings_values.get("android_id"):
                    add_phone_identifier("Android ID", settings_values.get("android_id", ""), "Android Settings Secure", "Recovered from copied ALEAPP settingsSecure.py parser")
                if settings_values.get("bluetooth_name"):
                    add_entry("Phone Identifiers", "Bluetooth Name", settings_values.get("bluetooth_name", ""), "Android Settings Secure", "Observed", "Recovered from copied ALEAPP settingsSecure.py parser")
                if settings_values.get("bluetooth_address"):
                    add_entry("Phone Identifiers", "Bluetooth Address", settings_values.get("bluetooth_address", ""), "Android Settings Secure", "Observed", "Recovered from copied ALEAPP settingsSecure.py parser")
            except Exception:
                pass

        elif file_name == "build.prop":
            try:
                build_values = parse_build_prop(file_path)
                manufacturer = build_values.get("ro.product.vendor.manufacturer", "") or build_values.get("ro.product.manufacturer", "")
                brand = build_values.get("ro.product.vendor.brand", "") or build_values.get("ro.product.brand", "")
                model = build_values.get("ro.product.vendor.model", "") or build_values.get("ro.product.model", "")
                device = build_values.get("ro.product.vendor.device", "") or build_values.get("ro.product.device", "")
                android_version = build_values.get("ro.vendor.build.version.release", "") or build_values.get("ro.system.build.version.release", "")
                sdk = build_values.get("ro.vendor.build.version.sdk", "") or build_values.get("ro.build.version.sdk", "")
                build_id = build_values.get("ro.build.id", "")
                fingerprint = build_values.get("ro.build.fingerprint", "")
                product_name = build_values.get("ro.product.vendor.name", "") or build_values.get("ro.product.name", "")

                if manufacturer or brand or model:
                    phone_name = " ".join(part for part in (manufacturer, model) if part).strip() or brand or device
                    details = " | ".join(
                        part for part in (
                            f"Brand: {brand}" if brand else "",
                            f"Device: {device}" if device else "",
                            f"Android Version: {android_version}" if android_version else "",
                            f"SDK: {sdk}" if sdk else "",
                        ) if part
                    )
                    add_entry("Phone Identifiers", "Android Device", phone_name, "Android build.prop", "Observed", f"{details} | Parser: copied ALEAPP build.py".strip(" |"))
                if manufacturer:
                    add_phone_identifier("Manufacturer", manufacturer, "Android build.prop", "Recovered from copied ALEAPP build.py parser")
                if brand:
                    add_phone_identifier("Brand", brand, "Android build.prop", "Recovered from copied ALEAPP build.py parser")
                if model:
                    add_phone_identifier("Model", model, "Android build.prop", "Recovered from copied ALEAPP build.py parser")
                if device:
                    add_phone_identifier("Device Codename", device, "Android build.prop", "Recovered from copied ALEAPP build.py parser")
                if product_name:
                    add_phone_identifier("Product Name", product_name, "Android build.prop", "Recovered from copied ALEAPP build.py parser")
                if android_version:
                    add_phone_identifier("Android Version", android_version, "Android build.prop", "Recovered from copied ALEAPP build.py parser")
                if sdk:
                    add_phone_identifier("SDK", sdk, "Android build.prop", "Recovered from copied ALEAPP build.py parser")
                if build_id:
                    add_phone_identifier("Build ID", build_id, "Android build.prop", "Recovered from copied ALEAPP build.py parser")
                if fingerprint:
                    add_phone_identifier("Build Fingerprint", fingerprint, "Android build.prop", "Recovered from copied ALEAPP build.py parser")
            except Exception:
                pass

        elif file_name == "bt_config.conf":
            try:
                adapter_info, bluetooth_connections = parse_bluetooth_connections(file_path)
                for connection in bluetooth_connections:
                    details = " | ".join(
                        part for part in (
                            f"Last connected: {connection.get('timestamp', '')}" if connection.get("timestamp") else "",
                            f"Link Key: {connection.get('linkkey', '')}" if connection.get("linkkey") else "",
                            "Parser: copied ALEAPP bluetoothConnections.py",
                        ) if part
                    )
                    add_entry("Bluetooth", connection.get("name") or connection.get("mac") or "", connection.get("mac", ""), "Android bt_config.conf", "Yes", details)
                adapter_name = normalize_text(adapter_info.get("Name", "")).strip()
                adapter_address = normalize_text(adapter_info.get("Address", "")).strip() or normalize_text(adapter_info.get("BD_ADDR", "")).strip()
                adapter_scan_mode = normalize_text(adapter_info.get("ScanMode", "")).strip()
                adapter_discoverable_timeout = normalize_text(adapter_info.get("DiscoverableTimeout", "")).strip()
                if adapter_name:
                    adapter_details = "Recovered from copied ALEAPP bluetoothConnections.py parser"
                    if adapter_scan_mode:
                        adapter_details += f" | Scan Mode: {adapter_scan_mode}"
                    if adapter_discoverable_timeout:
                        adapter_details += f" | Discoverable Timeout: {adapter_discoverable_timeout}"
                    add_entry("Phone Identifiers", "Bluetooth Adapter Name", adapter_name, "Android bt_config.conf", "Observed", adapter_details)
                if adapter_address:
                    add_entry("Phone Identifiers", "Bluetooth Adapter Address", adapter_address, "Android bt_config.conf", "Observed", "Recovered from copied ALEAPP bluetoothConnections.py parser")
            except Exception:
                pass

        elif file_name == "wificonfigstore.xml":
            try:
                wifi_rows = parse_wifi_configstore2(file_path)
                wifi_profile_rows = parse_wifi_profiles(file_path)
                wifi_profile_map: dict[tuple[str, str], dict[str, str]] = {}
                for row in wifi_profile_rows:
                    key = (
                        normalize_text(row.get("SSID", "")).strip().strip('"').lower(),
                        normalize_text(row.get("BSSID", "")).strip().lower(),
                    )
                    wifi_profile_map[key] = row
                for values in wifi_rows:
                    ssid = values.get("SSID", "").strip('"')
                    bssid = values.get("BSSID", "")
                    profile_values = wifi_profile_map.get((ssid.lower(), bssid.lower()), {})
                    merged_values = dict(profile_values)
                    merged_values.update(values)
                    config_key = merged_values.get("ConfigKey", "")
                    randomized_mac = merged_values.get("RandomizedMacAddress", "")
                    has_ever_connected = merged_values.get("HasEverConnected", "")
                    default_gw = merged_values.get("DefaultGwMacAddress", "")
                    hidden_ssid = merged_values.get("HiddenSSID", "")
                    creator_name = merged_values.get("CreatorName", "")
                    pre_shared_key = merged_values.get("PreSharedKey", "")
                    wep_keys = merged_values.get("WEPKeys", "")
                    password = merged_values.get("Password", "")
                    identity = merged_values.get("Identity", "")
                    captive_portal = merged_values.get("CaptivePortal", "")
                    login_url = merged_values.get("LoginUrl", "")
                    ip_assignment = merged_values.get("IpAssignment", "")
                    proxy_settings = merged_values.get("ProxySettings", "")
                    creation_time = _normalize_android_epoch(merged_values.get("CreationTime", ""))
                    sem_creation_time = _normalize_android_epoch(merged_values.get("semCreationTime", ""))
                    sem_update_time = _normalize_android_epoch(merged_values.get("semUpdateTime", ""))
                    connect_choice = merged_values.get("ConnectChoice", "")
                    last_connected = _normalize_android_epoch(merged_values.get("LastConnectedTime", "") or merged_values.get("ConnectChoiceTimeStamp", "") or merged_values.get("semUpdateTime", ""))
                    if not ssid and not bssid:
                        continue
                    details = " | ".join(
                        part for part in (
                            f"ConfigKey: {config_key}" if config_key else "",
                            f"Pre-Shared Key: {pre_shared_key}" if pre_shared_key else "",
                            f"WEP Keys: {wep_keys}" if wep_keys else "",
                            f"Password: {password}" if password else "",
                            f"Identity: {identity}" if identity else "",
                            f"Randomized MAC: {randomized_mac}" if randomized_mac else "",
                            f"Gateway MAC: {default_gw}" if default_gw else "",
                            f"Hidden SSID: {hidden_ssid}" if hidden_ssid else "",
                            f"Captive Portal: {captive_portal}" if captive_portal else "",
                            f"Login URL: {login_url}" if login_url else "",
                            f"IP Assignment: {ip_assignment}" if ip_assignment else "",
                            f"Proxy Settings: {proxy_settings}" if proxy_settings else "",
                            f"Creator: {creator_name}" if creator_name else "",
                            f"Created: {creation_time}" if creation_time else "",
                            f"Samsung Created: {sem_creation_time}" if sem_creation_time else "",
                            f"Samsung Updated: {sem_update_time}" if sem_update_time else "",
                            f"Connect choice: {connect_choice}" if connect_choice else "",
                            f"Last connected: {last_connected}" if last_connected else "",
                            f"Has ever connected: {has_ever_connected}" if has_ever_connected else "",
                            "Parser: copied ALEAPP wifiConfigstore2.py + wifiProfiles.py",
                        ) if part
                    )
                    add_entry("Wi-Fi", ssid or bssid or "Unknown network", bssid, "Android WifiConfigStore", "Yes" if has_ever_connected.lower() == "true" else "Observed", details)
            except Exception:
                pass

        elif file_name == "carservicedata.db":
            try:
                for row in parse_android_auto(file_path):
                    car_name = " ".join(part for part in (row.get("manufacturer", ""), row.get("model", "")) if part).strip()
                    if row.get("modelyear"):
                        car_name = f"{car_name} ({row.get('modelyear')})".strip() if car_name else row.get("modelyear", "")
                    connection_text = row.get("connectiontime", "")
                    if row.get("bluetoothaddress"):
                        bluetooth_details = " | ".join(
                            part for part in (
                                f"Connection time: {connection_text}" if connection_text else "",
                                "Parser: copied ALEAPP androidauto.py",
                            ) if part
                        )
                        add_entry("Bluetooth", car_name or row.get("bluetoothaddress", ""), row.get("bluetoothaddress", ""), "Android Auto allowedcars", "Yes", bluetooth_details)
                    if row.get("wifissid") or row.get("wifibssid"):
                        wifi_details = " | ".join(
                            part for part in (
                                f"Car: {car_name}" if car_name else "",
                                f"Connection time: {connection_text}" if connection_text else "",
                                f"Wi-Fi Password: {row.get('wifipassword', '')}" if row.get("wifipassword") else "",
                                "Parser: copied ALEAPP androidauto.py",
                            ) if part
                        )
                        add_entry("Wi-Fi", row.get("wifissid", "") or row.get("wifibssid", "") or "Unknown network", row.get("wifibssid", ""), "Android Auto allowedcars", "Yes", wifi_details)
            except Exception:
                pass

        elif file_name == "telephony.db":
            try:
                for row in parse_siminfo(file_path):
                    carrier_bits = " | ".join(
                        part for part in (
                            f"Display Name: {row.get('display_name')}" if row.get("display_name") else "",
                            f"Carrier: {row.get('carrier_name')}" if row.get("carrier_name") else "",
                            f"ISO Country Code: {row.get('iso_country_code')}" if row.get("iso_country_code") else "",
                            f"Carrier ID: {row.get('carrier_id')}" if row.get("carrier_id") else "",
                            "Parser: copied ALEAPP siminfo.py",
                        ) if part
                    )
                    if row.get("number"):
                        add_phone_identifier("SIM Number", row.get("number", ""), "Android telephony.db", carrier_bits)
                    if row.get("imsi"):
                        add_phone_identifier("IMSI", row.get("imsi", ""), "Android telephony.db", carrier_bits)
                    if row.get("icc_id"):
                        add_phone_identifier("ICC ID", row.get("icc_id", ""), "Android telephony.db", carrier_bits)
                    if row.get("display_name"):
                        add_entry("Phone Identifiers", "SIM Display Name", row.get("display_name", ""), "Android telephony.db", "Observed", "Recovered from copied ALEAPP siminfo.py parser")
            except Exception:
                pass

        elif file_name == "adb_keys":
            try:
                for row in parse_adb_hosts(file_path):
                    details = "Parser: copied ALEAPP adb_hosts.py"
                    if row.get("hostname"):
                        details += f" | Hostname: {row.get('hostname')}"
                    add_entry("Phone Identifiers", "ADB Host", row.get("host", ""), "Android adb_keys", "Observed", details)
            except Exception:
                pass

        elif file_name == "version" and "usagestats" in str(file_path).replace("\\", "/").lower():
            try:
                version_info = parse_usagestats_version(file_path)
                if version_info.get("android_version"):
                    add_phone_identifier("Android Version", version_info.get("android_version", ""), "Android usagestats/version", "Recovered from copied ALEAPP usagestatsVersion.py parser")
                if version_info.get("codename"):
                    add_phone_identifier("Android Codename", version_info.get("codename", ""), "Android usagestats/version", "Recovered from copied ALEAPP usagestatsVersion.py parser")
                if version_info.get("build_version"):
                    add_phone_identifier("Build Version", version_info.get("build_version", ""), "Android usagestats/version", "Recovered from copied ALEAPP usagestatsVersion.py parser")
                if version_info.get("country_specific_code"):
                    add_phone_identifier("Country Specific Code", version_info.get("country_specific_code", ""), "Android usagestats/version", "Recovered from copied ALEAPP usagestatsVersion.py parser")
            except Exception:
                pass

        elif file_name == "device_info_alex.json":
            try:
                for row in parse_alex_device_info(file_path):
                    add_entry("Phone Identifiers", row.get("key", "") or "ALEX Device Info", row.get("value", ""), "ALEX device_info_alex.json", "Observed", "Recovered from copied ALEAPP alexDeviceInfo.py parser")
            except Exception:
                pass

    return summary


def extract_account_records(data: Any, path: Path, records: list[AccountRecord], context: str):
    if not isinstance(data, (dict, list)):
        return
    seen: set[tuple[str, str]] = set()
    for key, value in flatten_object(data):
        if not key:
            continue
        text_value = normalize_text(value).strip()
        if not text_value:
            continue
        lowered_key = key.lower()
        lowered_value = text_value.lower()
        if any(keyword in lowered_key for keyword in ACCOUNT_EXCLUDE_KEYWORDS):
            continue
        if not (
            any(keyword in lowered_key for keyword in ACCOUNT_INCLUDE_KEYWORDS)
            or any(keyword in lowered_value for keyword in ("instagram", "facebook", "messenger", "whatsapp", "meta"))
            or bool(re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", text_value, re.IGNORECASE))
        ):
            continue
        display_key = suffix_key(key.split(".")[-1]).strip() or key
        record_key = (key, text_value)
        if record_key in seen:
            continue
        seen.add(record_key)
        records.append(AccountRecord(field=display_key, value=text_value[:1000], source_path=str(path), context=context))


def find_candidate_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if archive_member_is_relevant(str(path)):
            yield path


def list_candidate_files(root: Path, progress_callback=None) -> list[Path]:
    candidates: list[Path] = []
    count = 0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        count += 1
        if archive_member_is_relevant(str(path)):
            candidates.append(path)
        if progress_callback and (count == 1 or count % 1000 == 0):
            progress_callback(0, 0, f"Counting files ({count:,} checked)")
    return candidates


def count_supported_files(root: Path, progress_callback=None) -> int:
    return len(list_candidate_files(root, progress_callback=progress_callback))


def path_looks_relevant(path: Path) -> bool:
    lowered = str(path).lower()
    return (
        any(keyword in lowered for keyword in PATH_KEYWORDS)
        or any(keyword in lowered for keyword in KNOWN_FILE_HINTS)
        or source_is_android_stella_db(path)
        or source_is_android_graphql_cache(path)
        or source_is_android_interaction_log_db(path)
        or source_is_android_device_artifact(path)
    )


def safe_suffix(path: Path) -> str:
    return path.suffix.lower()


def _parse_embedded_metadata_blob(blob: bytes) -> dict[str, Any]:
    try:
        return plistlib.loads(blob)
    except Exception:
        return {}


def _extract_embedded_make_model(metadata: dict[str, Any]) -> tuple[str, str, str, str, str, str]:
    if not isinstance(metadata, dict):
        return "", "", "", "", "", ""

    tiff = metadata.get("{TIFF}") or metadata.get("TIFF") or {}
    exif = metadata.get("{Exif}") or metadata.get("Exif") or {}
    gps = metadata.get("{GPS}") or metadata.get("GPS") or {}
    raw_text = normalize_text(metadata)

    make_value = normalize_text(
        tiff.get("Make")
        or metadata.get("Make")
        or metadata.get("TIFF:Make")
    ).strip()
    model_value = normalize_text(
        tiff.get("Model")
        or metadata.get("Model")
        or metadata.get("TIFF:Model")
    ).strip()
    software_value = normalize_text(
        tiff.get("Software")
        or exif.get("Software")
        or metadata.get("Software")
    ).strip()
    date_time_value = normalize_text(
        tiff.get("DateTime")
        or exif.get("DateTimeOriginal")
        or metadata.get("DateTime")
    ).strip()
    latitude_value = normalize_gps_coordinate(gps.get("Latitude") or metadata.get("GPSLatitude"), "lat")
    longitude_value = normalize_gps_coordinate(gps.get("Longitude") or metadata.get("GPSLongitude"), "lon")

    if not make_value or not model_value:
        extracted_make, extracted_model = extract_media_identifiers(raw_text)
        make_value = make_value or extracted_make
        model_value = model_value or extracted_model

    return make_value, model_value, software_value, date_time_value, latitude_value, longitude_value


def _resolve_asset_path_from_photos_db(db_path: Path, directory_value: str, filename_value: str) -> Path:
    normalized_directory = str(directory_value or "").strip().strip("/\\")
    normalized_filename = str(filename_value or "").strip()

    if db_path.parent.name.lower() == "photodata":
        media_root = db_path.parent.parent
    else:
        media_root = db_path.parent

    if normalized_directory and normalized_filename:
        return media_root / Path(normalized_directory) / normalized_filename
    if normalized_filename:
        return media_root / normalized_filename
    return db_path


def scan_embedded_media_artifact(path: Path) -> list[MediaRecord]:
    if basename(path).lower() != "photos.sqlite":
        return []

    db = None
    records: list[MediaRecord] = []
    seen: set[tuple[str, str, str]] = set()
    try:
        safe_path = str(path.resolve()).replace("\\", "/")
        db = sqlite3.connect(f"file:{safe_path}?mode=ro", uri=True)
        cursor = db.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        asset_table = "ZASSET" if "ZASSET" in tables else "ZGENERICASSET" if "ZGENERICASSET" in tables else ""
        required_tables = {asset_table, "ZADDITIONALASSETATTRIBUTES", "ZCLOUDMASTER", "ZCLOUDMASTERMEDIAMETADATA"}
        if not asset_table or not required_tables.issubset(tables):
            return []

        cursor.execute(
            f"""
            SELECT
                COALESCE(zAsset.ZDIRECTORY, '') AS directory_path,
                COALESCE(zAsset.ZFILENAME, '') AS filename,
                COALESCE(zAddAssetAttr.ZORIGINALFILENAME, '') AS addl_original_filename,
                COALESCE(zCldMast.ZORIGINALFILENAME, '') AS cloud_original_filename,
                COALESCE(zAddAssetAttr.ZEXIFTIMESTAMPSTRING, '') AS exif_timestamp,
                aaa.ZDATA AS addl_metadata_blob,
                cmm.ZDATA AS cloud_metadata_blob
            FROM {asset_table} zAsset
            LEFT JOIN ZADDITIONALASSETATTRIBUTES zAddAssetAttr
                ON zAddAssetAttr.Z_PK = zAsset.ZADDITIONALATTRIBUTES
            LEFT JOIN ZCLOUDMASTER zCldMast
                ON zAsset.ZMASTER = zCldMast.Z_PK
            LEFT JOIN ZCLOUDMASTERMEDIAMETADATA aaa
                ON aaa.Z_PK = zAddAssetAttr.ZMEDIAMETADATA
            LEFT JOIN ZCLOUDMASTERMEDIAMETADATA cmm
                ON cmm.Z_PK = zCldMast.ZMEDIAMETADATA
            WHERE aaa.ZDATA IS NOT NULL OR cmm.ZDATA IS NOT NULL
            """
        )

        for directory_path, filename, addl_original_filename, cloud_original_filename, exif_timestamp, addl_blob, cloud_blob in cursor.fetchall():
            for blob_source, blob in (("additional_asset_attributes", addl_blob), ("cloud_master", cloud_blob)):
                if not blob:
                    continue
                metadata = _parse_embedded_metadata_blob(blob)
                if not metadata:
                    continue

                make_value, model_value, software_value, date_time_value, latitude_value, longitude_value = _extract_embedded_make_model(metadata)
                if not is_metaglasses_make_model(make_value, model_value):
                    continue

                asset_filename = (
                    normalize_text(filename).strip()
                    or normalize_text(addl_original_filename).strip()
                    or normalize_text(cloud_original_filename).strip()
                    or "Embedded asset"
                )
                logical_asset_path = _resolve_asset_path_from_photos_db(
                    path,
                    normalize_text(directory_path).strip(),
                    asset_filename,
                )
                logical_asset_str = str(logical_asset_path)
                if safe_suffix(logical_asset_path) not in MEDIA_EXTENSIONS or safe_suffix(logical_asset_path) in {".aac", ".m4a", ".mp3", ".wav"}:
                    continue
                record_key = (
                    logical_asset_str.lower(),
                    make_value.strip().lower(),
                    model_value.strip().lower(),
                )
                if record_key in seen:
                    continue
                seen.add(record_key)

                reasons = [
                    "embedded_media_metadata_match",
                    f"embedded_metadata_source:{basename(path)}",
                    f"embedded_metadata_blob:{blob_source}",
                ]
                effective_datetime = date_time_value or normalize_text(exif_timestamp).strip()
                if effective_datetime:
                    reasons.append(f"embedded_exif_datetime:{effective_datetime}")

                records.append(
                    MediaRecord(
                        media_path=logical_asset_str,
                        extension=safe_suffix(logical_asset_path),
                        score=9,
                        reasons=", ".join(reasons),
                        exif_make=make_value,
                        exif_model=model_value,
                        exif_software=software_value,
                        exif_datetime=effective_datetime,
                        latitude=latitude_value,
                        longitude=longitude_value,
                    )
                )
    except Exception:
        return records
    finally:
        if db:
            db.close()

    records.sort(key=lambda item: (-item.score, item.media_path.lower()))
    return records


def parse_plist_file(path: Path) -> Any:
    with open(windows_safe_path(path), "rb") as handle:
        plist_data = handle.read()
    plist_content = plistlib.loads(plist_data)
    if isinstance(plist_content, dict) and plist_content.get("$archiver", "") == "NSKeyedArchiver" and nska_deserialize:
        try:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                return nska_deserialize.deserialize_plist_from_string(plist_data)
        except Exception:
            return plist_content
    return plist_content


def parse_json_file(path: Path) -> Any:
    with open(windows_safe_path(path), "r", encoding="utf-8", errors="replace") as handle:
        return json.load(handle)


def iter_text_lines(path: Path):
    with open(windows_safe_path(path), "r", encoding="utf-8", errors="replace") as handle:
        yield from handle


def file_might_contain_terms(path: Path, terms: tuple[str, ...], chunk_size: int = 65536) -> bool:
    lowered_terms = tuple(term.lower() for term in terms)
    try:
        with open(windows_safe_path(path), "rb") as handle:
            while True:
                chunk = handle.read(chunk_size)
                if not chunk:
                    return False
                lowered = chunk.decode("utf-8", errors="ignore").lower()
                if any(term in lowered for term in lowered_terms):
                    return True
    except Exception:
        return True
    return False


def parse_embedded_plist_bytes(blob: bytes) -> Any:
    try:
        plist_content = plistlib.loads(blob)
        if isinstance(plist_content, dict) and plist_content.get("$archiver", "") == "NSKeyedArchiver" and nska_deserialize:
            try:
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    return nska_deserialize.deserialize_plist_from_string(blob)
            except Exception:
                return plist_content
        return plist_content
    except Exception:
        return None


def scan_flattened_data(data: Any, artifact_type: str, path: Path, records: list[IdentifierRecord]):
    for key, value in flatten_object(data):
        if not key:
            continue
        text_value = normalize_text(value)
        if not text_value:
            continue
        if (key_looks_interesting(key) or value_looks_interesting(text_value)) and looks_like_glasses_identifier(key, text_value, path, "structured_data"):
            records.append(
                IdentifierRecord(
                    artifact_type=artifact_type,
                    key=key,
                    value=text_value[:1000],
                    source_path=str(path),
                    context="structured_data",
                )
            )


def scan_plist(
    path: Path,
    identifier_records: list[IdentifierRecord],
    account_records: list[AccountRecord],
    case_settings_records: list[StellaCaseSettingsRecord],
    device_sync_records: list[StellaDeviceSyncRecord],
    derived_sku_records: list[StellaDerivedSkuRecord],
):
    try:
        data = parse_plist_file(path)
    except Exception:
        return

    scan_flattened_data(data, "plist", path, identifier_records)
    if source_is_stella_case_settings_artifact(path):
        extract_stella_case_settings(data, path, case_settings_records)
    if source_is_stella_sync_log_artifact(path):
        extract_stella_device_sync(data, path, device_sync_records)
    if source_is_stella_derived_sku_artifact(path):
        extract_stella_derived_sku(data, path, derived_sku_records)
    if source_is_stella_account_artifact(path):
        extract_account_records(data, path, account_records, "stella_plist")

    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, bytes) and ("linkedaccounts" in key.lower() or "account" in key.lower()):
                embedded = parse_embedded_plist_bytes(value)
                if embedded is not None:
                    scan_flattened_data(embedded, "embedded_plist", path, identifier_records)
                    if source_is_stella_account_artifact(path):
                        extract_account_records(embedded, path, account_records, f"embedded:{key}")


def scan_json(path: Path, identifier_records: list[IdentifierRecord]):
    precheck_terms = tuple(term.lower() for term in PATH_KEYWORDS + IDENTIFIER_KEYWORDS)
    if not file_might_contain_terms(path, precheck_terms):
        return

    try:
        data = parse_json_file(path)
    except Exception:
        return

    scan_flattened_data(data, "json", path, identifier_records)


def extract_timestamp(line: str) -> str:
    match = TIMESTAMP_RE.match(line)
    return match.group(0) if match else ""


def scan_text(path: Path, identifier_records: list[IdentifierRecord], prompt_records: list[PromptRecord]):
    try:
        lines = iter_text_lines(path)
    except Exception:
        return

    pending_prompts: list[tuple[str, int, str]] = []

    for line_number, line in enumerate(lines, start=1):
        line_stripped = line.strip()
        lower_line = line_stripped.lower()
        if any(keyword in lower_line for keyword in PATH_KEYWORDS):
            if looks_like_glasses_identifier("line", line_stripped, path, "keyword_hit"):
                identifier_records.append(
                    IdentifierRecord(
                        artifact_type="text",
                        key="line",
                        value=line_stripped[:1000],
                        source_path=str(path),
                        context="keyword_hit",
                    )
                )

        prompt_match = PROMPT_RE.search(line)
        if prompt_match:
            pending_prompts.append((extract_timestamp(line), line_number, clean_log_string(prompt_match.group("prompt"))))
            continue

        response_match = RESPONSE_RE.search(line)
        if response_match:
            prompt_ts = ""
            prompt_line_number = ""
            prompt_text = ""
            if pending_prompts:
                prompt_ts, prompt_line_number, prompt_text = pending_prompts.pop(0)
            prompt_records.append(
                PromptRecord(
                    prompt_timestamp=prompt_ts,
                    response_timestamp=extract_timestamp(line),
                    prompt_text=prompt_text,
                    response_text=clean_log_string(response_match.group("response").strip()),
                    prompt_line_number=str(prompt_line_number) if prompt_line_number else "",
                    response_line_number=str(line_number),
                    source_path=str(path),
                )
            )

        if "id" in lower_line or "serial" in lower_line or "account" in lower_line:
            for match in GENERIC_ID_RE.findall(line_stripped):
                if looks_like_glasses_identifier("regex_match", match, path, line_stripped[:240]):
                    identifier_records.append(
                        IdentifierRecord(
                            artifact_type="text",
                            key="regex_match",
                            value=match,
                            source_path=str(path),
                            context=line_stripped[:240],
                        )
                    )

    for prompt_ts, prompt_line_number, prompt_text in pending_prompts:
        prompt_records.append(
            PromptRecord(
                prompt_timestamp=prompt_ts,
                response_timestamp="",
                prompt_text=prompt_text,
                response_text="",
                prompt_line_number=str(prompt_line_number) if prompt_line_number else "",
                response_line_number="",
                source_path=str(path),
            )
        )


def scan_sqlite(path: Path, identifier_records: list[IdentifierRecord]):
    try:
        safe_path = windows_safe_path(path).replace("\\", "/")
        connection = sqlite3.connect(f"file:{safe_path}?mode=ro", uri=True)
    except Exception:
        return

    try:
        cursor = connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        table_names = [row[0] for row in cursor.fetchall()]
        for table_name in table_names:
            lowered = table_name.lower()
            if not any(keyword.replace(" ", "") in lowered for keyword in PATH_KEYWORDS) and "account" not in lowered:
                continue

            identifier_records.append(
                IdentifierRecord(
                    artifact_type="sqlite",
                    key="table",
                    value=table_name,
                    source_path=str(path),
                    context="table_name_hit",
                )
            )

            try:
                rows = connection.execute(f'SELECT * FROM "{table_name}" LIMIT 25').fetchall()
                columns = [item[1] for item in connection.execute(f'PRAGMA table_info("{table_name}")').fetchall()]
            except Exception:
                continue

            for row in rows:
                for column, value in zip(columns, row):
                    text_value = normalize_text(value)
                    if not text_value:
                        continue
                    if (key_looks_interesting(column) or value_looks_interesting(text_value)) and looks_like_glasses_identifier(
                        column,
                        text_value,
                        path,
                        table_name,
                    ):
                        identifier_records.append(
                            IdentifierRecord(
                                artifact_type="sqlite",
                                key=f"{table_name}.{column}",
                                value=text_value[:1000],
                                source_path=str(path),
                                context="row_value_hit",
                            )
                        )
    except sqlite3.DatabaseError:
        return
    finally:
        connection.close()


def scan_android_artifacts(
    path: Path,
    account_records: list[AccountRecord],
    prompt_records: list[PromptRecord],
    android_profile_records: list[AndroidMetaAppProfileRecord],
    android_device_records: list[AndroidMetaDeviceRecord],
    android_sync_records: list[AndroidMetaSyncRecord],
):
    if source_is_android_stella_db(path):
        extract_android_meta_app_profiles(path, android_profile_records, account_records)
        extract_android_meta_devices_and_sync(path, android_device_records, android_sync_records)
        extract_android_prompts_from_sqlite_fallback(path, prompt_records)
    elif source_is_android_interaction_log_db(path):
        extract_android_prompts_from_interaction_log(path, prompt_records)
        extract_android_prompts_from_sqlite_fallback(path, prompt_records)
    elif source_is_android_graphql_cache(path):
        extract_android_prompts_from_graphql_cache(path, prompt_records)
    elif safe_suffix(path) in {".db", ".sqlite", ".sqlite3"} and path_is_android_specific(path):
        extract_android_prompts_from_sqlite_fallback(path, prompt_records)

    if prompt_records:
        prompt_records[:] = dedupe_prompt_records(prompt_records)
