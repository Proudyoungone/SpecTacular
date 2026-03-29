import argparse
import base64
import datetime as dt
import json
import re
import subprocess
import sys
import tempfile
import time
import threading
import webbrowser
from tkinter import scrolledtext
from dataclasses import asdict
from pathlib import Path
from typing import Any
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

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
from scripts.pipeline import (
    export_media_exif_csv,
    export_hit_files,
    extract_archive_to_temp,
    is_supported_archive,
    locate_exiftool,
    media_record_export_row,
    write_table_xlsx,
    write_csv,
    write_json,
)
from scripts.report import (
    FIELD_REFERENCE_PDF_NAME,
    write_field_reference_pdf,
    write_html_report as render_html_report,
)
from scripts.scan_engine import (
    STRUCTURED_EXTENSIONS,
    collect_detected_devices_summary,
    find_candidate_files,
    list_candidate_files,
    normalize_input_mode,
    path_is_android_specific,
    path_is_apple_specific,
    path_matches_input_mode,
    path_looks_relevant,
    safe_suffix,
    scan_embedded_media_artifact,
    scan_android_artifacts,
    scan_json,
    scan_plist,
    scan_sqlite,
    scan_text,
)
from scripts.artifacts.meta_glasses_android import (
    synthesize_android_case_settings_records,
    synthesize_android_derived_sku_records,
    synthesize_android_device_sync_records,
)
from scripts.utils import external_tool_path, windows_safe_path
from scripts.utils import (
    build_output_folder_name,
    cleanup_temp_dir,
    extract_media_identifiers,
    explain_media_hit,
    is_metaglasses_make_model,
    normalize_text,
    windows_safe_path,
)

APP_NAME = "SpecTacular"
APP_SUBTITLE = "Meta Glasses Parser"
BRAND_DARK = "#08162f"
BRAND_PANEL = "#102544"
BRAND_PANEL_ALT = "#132d52"
BRAND_LINE = "#426890"
BRAND_TEXT = "#f6fbff"
BRAND_MUTED = "#a8bfdc"
BRAND_CYAN = "#27c2ff"
BRAND_CYAN_BRIGHT = "#7ae4ff"
BRAND_GOLD = "#ffcb4d"
LOGO_FILE_NAME = "SpecTacular Logo.png"
LIGHT_BG = "#f3f8ff"
LIGHT_PANEL = "#ffffff"
LIGHT_PANEL_ALT = "#eef5ff"
LIGHT_TEXT = "#10345a"
LIGHT_MUTED = "#4b6f95"
LIGHT_CYAN = "#1182d7"
LIGHT_CYAN_BRIGHT = "#1182d7"
LIGHT_GOLD = "#1182d7"


class ScanCancelledError(Exception):
    """Raised when the user stops an in-progress scan."""
LIGHT_LINE = "#c7ddf3"

THEME_PALETTES = {
    "dark": {
        "bg": BRAND_DARK,
        "panel": BRAND_PANEL,
        "panel_alt": BRAND_PANEL_ALT,
        "text": BRAND_TEXT,
        "muted": BRAND_MUTED,
        "cyan": BRAND_CYAN,
        "cyan_bright": BRAND_CYAN_BRIGHT,
        "gold": BRAND_GOLD,
        "line": BRAND_LINE,
        "button_active_fg": BRAND_DARK,
    },
    "light": {
        "bg": LIGHT_BG,
        "panel": LIGHT_PANEL,
        "panel_alt": LIGHT_PANEL_ALT,
        "text": LIGHT_TEXT,
        "muted": LIGHT_MUTED,
        "cyan": LIGHT_CYAN,
        "cyan_bright": LIGHT_CYAN_BRIGHT,
        "gold": LIGHT_GOLD,
        "line": LIGHT_LINE,
        "button_active_fg": "#ffffff",
    },
}

MEDIA_EXTENSIONS = {
    ".3gp",
    ".aac",
    ".heic",
    ".jpeg",
    ".jpg",
    ".m4a",
    ".mov",
    ".mp3",
    ".mp4",
    ".png",
    ".wav",
    ".webp",
}
CAPTURE_MEDIA_EXTENSIONS = {
    ".3gp",
    ".heic",
    ".jpeg",
    ".jpg",
    ".mov",
    ".mp4",
    ".png",
    ".webp",
}


def read_exif_metadata(media_path: Path, exiftool_path: str | None) -> dict[str, str]:
    if not exiftool_path:
        return {}

    try:
        completed = subprocess.run(
            [
                external_tool_path(exiftool_path),
                "-j",
                "-n",
                "-a",
                "-u",
                "-U",
                "-ee3",
                "-api",
                "RequestAll=3",
                "-api",
                "LargeFileSupport=1",
                "-G1",
                external_tool_path(media_path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        payload = json.loads(completed.stdout)
        if payload and isinstance(payload, list):
            first = payload[0]
            raw_text = json.dumps(first, ensure_ascii=True, default=str)
            extracted_make, extracted_model = extract_media_identifiers(raw_text)
            make_value = (
                normalize_text(first.get("Make", ""))
                or normalize_text(first.get("EXIF:Make", ""))
                or normalize_text(first.get("TIFF:Make", ""))
                or extracted_make
            )
            model_value = (
                normalize_text(first.get("Model", ""))
                or normalize_text(first.get("EXIF:Model", ""))
                or normalize_text(first.get("TIFF:Model", ""))
                or extracted_model
            )
            software_value = (
                normalize_text(first.get("Software", ""))
                or normalize_text(first.get("EXIF:Software", ""))
                or normalize_text(first.get("QuickTime:Software", ""))
            )
            return {
                "Make": make_value,
                "Model": model_value,
                "Software": software_value,
                "RawText": raw_text,
                "DateTime": (
                    normalize_text(first.get("DateTime", ""))
                    or normalize_text(first.get("EXIF:DateTimeOriginal", ""))
                    or normalize_text(first.get("QuickTime:CreateDate", ""))
                ),
            }
    except Exception:
        return {}
    return {}


def score_media(path: Path, exif_data: dict[str, str]) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []

    make_value = exif_data.get("Make", "")
    model_value = exif_data.get("Model", "")
    if is_metaglasses_make_model(make_value, model_value):
        score += 10
        reasons.append("exact_exif_make_model_match")

    raw_text = exif_data.get("RawText", "")
    if raw_text and score == 0:
        extracted_make, extracted_model = extract_media_identifiers(raw_text)
        if is_metaglasses_make_model(extracted_make, extracted_model):
            score += 8
            reasons.append("embedded_exif_make_model_match")

    date_time_value = exif_data.get("DateTime", "")
    if date_time_value:
        reasons.append(f"exif_datetime:{date_time_value}")

    return score, reasons


def scan_media(
    files: list[Path],
    exiftool_path: str | None,
    progress_callback=None,
    progress_start: int = 0,
    progress_total: int = 0,
) -> list[MediaRecord]:
    media_records: list[MediaRecord] = []
    for index, path in enumerate(files, start=1):
        try:
            suffix = safe_suffix(path)
            if suffix not in MEDIA_EXTENSIONS:
                if progress_callback:
                    progress_callback(progress_start + index, progress_total, "Scanning media")
                continue

            exif_data = read_exif_metadata(path, exiftool_path)
            score, reasons = score_media(path, exif_data)
            if score <= 0:
                if progress_callback:
                    progress_callback(progress_start + index, progress_total, "Scanning media")
                continue

            media_records.append(
                MediaRecord(
                    media_path=str(path),
                    extension=suffix,
                    score=score,
                    reasons=", ".join(reasons),
                    exif_make=exif_data.get("Make", ""),
                    exif_model=exif_data.get("Model", ""),
                    exif_software=exif_data.get("Software", ""),
                )
            )
            if progress_callback:
                progress_callback(progress_start + index, progress_total, "Scanning media")
        except Exception:
            if progress_callback:
                progress_callback(progress_start + index, progress_total, "Scanning media")
            continue

    media_records.sort(key=lambda item: (-item.score, item.media_path.lower()))
    return media_records


def write_html_report(
    path: Path,
    summary: dict[str, Any],
    identifier_records: list[IdentifierRecord],
    prompt_records: list[PromptRecord],
    media_records: list[MediaRecord],
    account_records: list[AccountRecord],
    case_settings_records: list[StellaCaseSettingsRecord],
    device_sync_records: list[StellaDeviceSyncRecord],
    derived_sku_records: list[StellaDerivedSkuRecord],
    android_profile_records: list[AndroidMetaAppProfileRecord],
    android_device_records: list[AndroidMetaDeviceRecord],
    android_sync_records: list[AndroidMetaSyncRecord],
):
    render_html_report(
        path,
        summary,
        identifier_records,
        prompt_records,
        media_records,
        account_records,
        case_settings_records,
        device_sync_records,
        derived_sku_records,
        android_profile_records,
        android_device_records,
        android_sync_records,
        app_name=APP_NAME,
        app_subtitle=APP_SUBTITLE,
        logo_data_uri=get_logo_data_uri(),
        brand_dark=BRAND_DARK,
        brand_panel=BRAND_PANEL,
        brand_panel_alt=BRAND_PANEL_ALT,
        brand_line=BRAND_LINE,
        brand_text=BRAND_TEXT,
        brand_muted=BRAND_MUTED,
        brand_cyan=BRAND_CYAN,
        brand_cyan_bright=BRAND_CYAN_BRIGHT,
        brand_gold=BRAND_GOLD,
    )


def write_reference_pdf(output_root: Path, log_callback=None):
    pdf_path = output_root / FIELD_REFERENCE_PDF_NAME
    emit_log(log_callback, f"Writing {FIELD_REFERENCE_PDF_NAME}")
    write_field_reference_pdf(pdf_path)

def emit_log(log_callback, message: str):
    if log_callback:
        log_callback(message)


def get_logo_path() -> Path:
    return Path(__file__).resolve().parent.parent / LOGO_FILE_NAME


def get_logo_data_uri() -> str:
    logo_path = get_logo_path()
    if not logo_path.exists():
        return ""
    try:
        encoded = base64.b64encode(logo_path.read_bytes()).decode("ascii")
        return f"data:image/png;base64,{encoded}"
    except Exception:
        return ""


def build_summary(
    root: Path,
    identifier_records: list[IdentifierRecord],
    prompt_records: list[PromptRecord],
    media_records: list[MediaRecord],
    account_records: list[AccountRecord],
    case_settings_records: list[StellaCaseSettingsRecord],
    device_sync_records: list[StellaDeviceSyncRecord],
    derived_sku_records: list[StellaDerivedSkuRecord],
    android_profile_records: list[AndroidMetaAppProfileRecord],
    android_device_records: list[AndroidMetaDeviceRecord],
    android_sync_records: list[AndroidMetaSyncRecord],
    detected_devices: dict[str, list[dict[str, str]]],
    display_input_root: str | None = None,
    case_metadata: CaseMetadata | None = None,
    output_folder: str | None = None,
    exported_file_counts: dict[str, int] | None = None,
    input_mode: str = "auto",
) -> dict[str, Any]:
    unique_sources = sorted({record.source_path for record in identifier_records})
    high_confidence_media = [record.media_path for record in media_records if record.score >= 5]
    return {
        "input_root": display_input_root or str(root),
        "output_folder": output_folder or "",
        "input_mode": normalize_input_mode(input_mode),
        "identifier_count": len(identifier_records),
        "prompt_count": len(prompt_records),
        "media_count": len(media_records),
        "account_count": len(account_records),
        "case_settings_count": len(case_settings_records),
        "device_sync_count": len(device_sync_records),
        "derived_sku_count": len(derived_sku_records),
        "android_profile_count": len(android_profile_records),
        "android_device_count": len(android_device_records),
        "android_sync_count": len(android_sync_records),
        "high_confidence_media_count": len(high_confidence_media),
        "sources_with_identifier_hits": unique_sources,
        "case_metadata": asdict(case_metadata or CaseMetadata()),
        "detected_devices": detected_devices,
        "exported_file_counts": exported_file_counts or {"prompt_files_copied": 0, "media_files_copied": 0},
    }


def run(
    root: Path,
    output: Path,
    exiftool_path: str | None,
    candidate_files: list[Path] | None = None,
    display_input_root: str | None = None,
    progress_callback=None,
    log_callback=None,
    total_candidate_files: int | None = None,
    case_metadata: CaseMetadata | None = None,
    input_mode: str = "auto",
    should_stop=None,
):
    identifier_records: list[IdentifierRecord] = []
    prompt_records: list[PromptRecord] = []
    media_records: list[MediaRecord] = []
    account_records: list[AccountRecord] = []
    case_settings_records: list[StellaCaseSettingsRecord] = []
    device_sync_records: list[StellaDeviceSyncRecord] = []
    derived_sku_records: list[StellaDerivedSkuRecord] = []
    android_profile_records: list[AndroidMetaAppProfileRecord] = []
    android_device_records: list[AndroidMetaDeviceRecord] = []
    android_sync_records: list[AndroidMetaSyncRecord] = []
    seen_media_keys: set[tuple[str, str, str]] = set()
    exported_file_counts = {"prompt_files_copied": 0, "media_files_copied": 0}
    json_output_dir = output / "JSON"
    csv_output_dir = output / "XLSX"
    processed_files = 0
    last_logged_phase = ""
    output_steps = 13
    input_mode = normalize_input_mode(input_mode)
    candidate_files = candidate_files or list(find_candidate_files(root))
    candidate_files = [path for path in candidate_files if path_matches_input_mode(path, input_mode)]
    total_steps = max((total_candidate_files or len(candidate_files)) + output_steps, 1)
    if progress_callback:
        progress_callback(0, total_steps, "Scanning files")
    emit_log(log_callback, f"Scan started for {display_input_root or str(root)}")

    def ensure_not_stopped():
        if should_stop and should_stop():
            raise ScanCancelledError("Scan stopped by user.")

    def append_media_record(record: MediaRecord):
        record_key = (
            record.media_path.strip().lower(),
            record.exif_make.strip().lower(),
            record.exif_model.strip().lower(),
        )
        if not record_key[0]:
            return
        if record_key in seen_media_keys:
            return
        seen_media_keys.add(record_key)
        media_records.append(record)

    for path in candidate_files:
        ensure_not_stopped()
        identifier_count_before = len(identifier_records)
        prompt_count_before = len(prompt_records)
        media_count_before = len(media_records)
        try:
            suffix = safe_suffix(path)
            if (
                suffix not in STRUCTURED_EXTENSIONS
                and suffix not in MEDIA_EXTENSIONS
                and not path_looks_relevant(path)
            ):
                continue

            processed_files += 1

            relevant_path = path_looks_relevant(path)
            android_specific_path = path_is_android_specific(path)
            apple_specific_path = path_is_apple_specific(path)

            if input_mode == "apple":
                if suffix == ".plist" and apple_specific_path:
                    scan_plist(
                        path,
                        identifier_records,
                        account_records,
                        case_settings_records,
                        device_sync_records,
                        derived_sku_records,
                    )
                elif relevant_path:
                    if suffix == ".json":
                        scan_json(path, identifier_records)
                    elif suffix in {".txt", ".log", ".strings"}:
                        scan_text(path, identifier_records, prompt_records)
                    elif suffix in {".db", ".sqlite", ".sqlite3"} and apple_specific_path:
                        scan_sqlite(path, identifier_records)
            elif input_mode == "android":
                if android_specific_path:
                    scan_android_artifacts(
                        path,
                        account_records,
                        prompt_records,
                        android_profile_records,
                        android_device_records,
                        android_sync_records,
                    )
            else:
                if suffix == ".plist" or relevant_path:
                    if suffix == ".plist":
                        scan_plist(
                            path,
                            identifier_records,
                            account_records,
                            case_settings_records,
                            device_sync_records,
                            derived_sku_records,
                        )
                    elif suffix == ".json":
                        scan_json(path, identifier_records)
                    elif suffix in {".txt", ".log", ".strings"}:
                        scan_text(path, identifier_records, prompt_records)
                    elif suffix in {".db", ".sqlite", ".sqlite3"}:
                        scan_sqlite(path, identifier_records)

                if relevant_path:
                    scan_android_artifacts(
                        path,
                        account_records,
                        prompt_records,
                        android_profile_records,
                        android_device_records,
                        android_sync_records,
                    )

            if suffix in {".db", ".sqlite", ".sqlite3"} and (input_mode == "auto" or apple_specific_path):
                for embedded_media_record in scan_embedded_media_artifact(path):
                    append_media_record(embedded_media_record)

            if suffix in CAPTURE_MEDIA_EXTENSIONS:
                exif_data = read_exif_metadata(path, exiftool_path)
                score, reasons = score_media(path, exif_data)
                if score > 0:
                    append_media_record(
                        MediaRecord(
                            media_path=str(path),
                            extension=suffix,
                            score=score,
                            reasons=", ".join(reasons),
                            exif_make=exif_data.get("Make", ""),
                            exif_model=exif_data.get("Model", ""),
                            exif_software=exif_data.get("Software", ""),
                            exif_datetime=exif_data.get("DateTime", ""),
                            latitude=exif_data.get("Latitude", ""),
                            longitude=exif_data.get("Longitude", ""),
                        )
                    )
        except Exception:
            pass
        finally:
            if progress_callback and (processed_files == 1 or processed_files % 250 == 0):
                hits_found = (
                    len(identifier_records) > identifier_count_before
                    or len(prompt_records) > prompt_count_before
                    or len(media_records) > media_count_before
                )
                phase = "Scanning files"
                if hits_found:
                    phase = "Scanning files (hit found)"
                progress_callback(processed_files, total_steps, phase)
                if phase != last_logged_phase or hits_found:
                    count_parts = []
                    if input_mode != "android":
                        count_parts.extend(
                            [
                                f"{len(case_settings_records):,} case settings",
                                f"{len(device_sync_records):,} sync logs",
                                f"{len(derived_sku_records):,} derived sku",
                            ]
                        )
                    if input_mode != "apple":
                        count_parts.extend(
                            [
                                f"{len(android_profile_records):,} android profiles",
                                f"{len(android_device_records):,} android devices",
                                f"{len(android_sync_records):,} android sync",
                            ]
                        )
                    count_parts.extend(
                        [
                            f"{len(account_records):,} linked accounts",
                            f"{len(identifier_records):,} identifiers",
                            f"{len(prompt_records):,} prompts",
                            f"{len(media_records):,} media hits",
                        ]
                    )
                    emit_log(
                        log_callback,
                        f"{phase}: {processed_files:,} files checked | " + " | ".join(count_parts),
                    )
                    last_logged_phase = phase

    if progress_callback:
        progress_callback(processed_files, total_steps, "Scanning files complete")

    media_records.sort(key=lambda item: (-item.score, item.media_path.lower()))
    ensure_not_stopped()
    detected_devices = collect_detected_devices_summary(
        candidate_files,
        case_metadata,
        media_records,
        android_device_records=android_device_records,
        android_sync_records=android_sync_records,
    )
    if input_mode != "apple":
        synthetic_android_case_settings_records = synthesize_android_case_settings_records(android_profile_records)
        if synthetic_android_case_settings_records:
            existing_case_keys = {
                (
                    item.glasses_device_id,
                    item.case_serial_number,
                    item.case_software_version,
                    item.last_settings_snapshot_time,
                    item.has_completed_voice_oobe,
                    item.meta_ai_opt_in_completed,
                    item.meta_ai_geo_opt_in_completed,
                    item.live_ai_eap_opt_in_status,
                    item.default_provider_backward_compatibility_script_run,
                    item.default_provider_backward_compatibility_script_run_v2,
                    item.show_language_reverted_notification,
                    item.show_language_reverted_push_notification,
                    item.source_path,
                )
                for item in case_settings_records
            }
            for item in synthetic_android_case_settings_records:
                item_key = (
                    item.glasses_device_id,
                    item.case_serial_number,
                    item.case_software_version,
                    item.last_settings_snapshot_time,
                    item.has_completed_voice_oobe,
                    item.meta_ai_opt_in_completed,
                    item.meta_ai_geo_opt_in_completed,
                    item.live_ai_eap_opt_in_status,
                    item.default_provider_backward_compatibility_script_run,
                    item.default_provider_backward_compatibility_script_run_v2,
                    item.show_language_reverted_notification,
                    item.show_language_reverted_push_notification,
                    item.source_path,
                )
                if item_key not in existing_case_keys:
                    existing_case_keys.add(item_key)
                    case_settings_records.append(item)

        synthetic_android_device_sync_records = synthesize_android_device_sync_records(android_sync_records, android_device_records)
        if synthetic_android_device_sync_records:
            existing_sync_keys = {
                (
                    item.glasses_device_id,
                    item.glasses_firmware_version,
                    item.app_version_at_last_sync,
                    item.last_sync_time,
                    item.source_path,
                )
                for item in device_sync_records
            }
            for item in synthetic_android_device_sync_records:
                item_key = (
                    item.glasses_device_id,
                    item.glasses_firmware_version,
                    item.app_version_at_last_sync,
                    item.last_sync_time,
                    item.source_path,
                )
                if item_key not in existing_sync_keys:
                    existing_sync_keys.add(item_key)
                    device_sync_records.append(item)

        synthetic_android_sku_records = synthesize_android_derived_sku_records(android_device_records)
        if synthetic_android_sku_records:
            existing_sku_keys = {
                (
                    item.glasses_serial_number,
                    item.model,
                    item.model_short_name,
                    item.frame_style,
                    item.frame_color_display_name,
                    item.frame_color,
                    item.lens_color_display_name,
                    item.lens_color,
                    item.source_path,
                )
                for item in derived_sku_records
            }
            for item in synthetic_android_sku_records:
                item_key = (
                    item.glasses_serial_number,
                    item.model,
                    item.model_short_name,
                    item.frame_style,
                    item.frame_color_display_name,
                    item.frame_color,
                    item.lens_color_display_name,
                    item.lens_color,
                    item.source_path,
                )
                if item_key not in existing_sku_keys:
                    existing_sku_keys.add(item_key)
                    derived_sku_records.append(item)

    if progress_callback:
        progress_callback(processed_files + 1, total_steps, "Writing output files")
    emit_log(log_callback, "Building summary")
    ensure_not_stopped()
    summary = build_summary(
        root,
        identifier_records,
        prompt_records,
        media_records,
        account_records,
        case_settings_records,
        device_sync_records,
        derived_sku_records,
        android_profile_records,
        android_device_records,
        android_sync_records,
        detected_devices,
        display_input_root,
        case_metadata=case_metadata,
        output_folder=str(output),
        exported_file_counts=exported_file_counts,
        input_mode=input_mode,
    )
    emit_log(log_callback, "Writing summary.json")
    if progress_callback:
        progress_callback(processed_files + 2, total_steps, "Writing summary.json")
    ensure_not_stopped()
    write_json(json_output_dir / "summary.json", summary)
    emit_log(log_callback, "Writing identifiers.json")
    if progress_callback:
        progress_callback(processed_files + 3, total_steps, "Writing identifiers.json")
    ensure_not_stopped()
    write_json(json_output_dir / "identifiers.json", [asdict(item) for item in identifier_records])
    emit_log(log_callback, "Writing metaai_prompts.json")
    if progress_callback:
        progress_callback(processed_files + 4, total_steps, "Writing metaai_prompts.json")
    ensure_not_stopped()
    write_json(json_output_dir / "metaai_prompts.json", [asdict(item) for item in prompt_records])
    if case_settings_records:
        emit_log(log_callback, "Writing stella_case_settings.json")
        if progress_callback:
            progress_callback(processed_files + 4, total_steps, "Writing stella_case_settings.json")
        ensure_not_stopped()
        write_json(json_output_dir / "stella_case_settings.json", [asdict(item) for item in case_settings_records])
    if device_sync_records:
        emit_log(log_callback, "Writing stella_device_sync_log.json")
        if progress_callback:
            progress_callback(processed_files + 4, total_steps, "Writing stella_device_sync_log.json")
        ensure_not_stopped()
        write_json(json_output_dir / "stella_device_sync_log.json", [asdict(item) for item in device_sync_records])
    if derived_sku_records:
        emit_log(log_callback, "Writing stella_derived_sku_info.json")
        if progress_callback:
            progress_callback(processed_files + 5, total_steps, "Writing stella_derived_sku_info.json")
        ensure_not_stopped()
        write_json(json_output_dir / "stella_derived_sku_info.json", [asdict(item) for item in derived_sku_records])
    if input_mode != "apple":
        android_profile_rows = [asdict(item) for item in android_profile_records]
        android_device_rows = [asdict(item) for item in android_device_records]
        android_sync_rows = [asdict(item) for item in android_sync_records]

        emit_log(log_callback, "Writing stella_app_profiles.json")
        ensure_not_stopped()
        write_json(json_output_dir / "stella_app_profiles.json", android_profile_rows)
        emit_log(log_callback, "Writing android_meta_app_profiles.json")
        ensure_not_stopped()
        write_json(json_output_dir / "android_meta_app_profiles.json", android_profile_rows)

        emit_log(log_callback, "Writing stella_device_records.json")
        ensure_not_stopped()
        write_json(json_output_dir / "stella_device_records.json", android_device_rows)
        emit_log(log_callback, "Writing android_meta_devices.json")
        ensure_not_stopped()
        write_json(json_output_dir / "android_meta_devices.json", android_device_rows)

        emit_log(log_callback, "Writing stella_sync_activity.json")
        ensure_not_stopped()
        write_json(json_output_dir / "stella_sync_activity.json", android_sync_rows)
        emit_log(log_callback, "Writing android_meta_sync.json")
        ensure_not_stopped()
        write_json(json_output_dir / "android_meta_sync.json", android_sync_rows)
    emit_log(log_callback, "Writing media_hits.json")
    if progress_callback:
        progress_callback(processed_files + 5, total_steps, "Writing media_hits.json")
    ensure_not_stopped()
    write_json(json_output_dir / "media_hits.json", [media_record_export_row(item) for item in media_records])
    emit_log(log_callback, "Writing stella_linked_accounts.json")
    if progress_callback:
        progress_callback(processed_files + 6, total_steps, "Writing stella_linked_accounts.json")
    ensure_not_stopped()
    write_json(json_output_dir / "stella_linked_accounts.json", [asdict(item) for item in account_records])

    emit_log(log_callback, "Writing identifiers.xlsx")
    if progress_callback:
        progress_callback(processed_files + 7, total_steps, "Writing identifiers.xlsx")
    ensure_not_stopped()
    write_table_xlsx(csv_output_dir / "identifiers.xlsx", [asdict(item) for item in identifier_records], "Identifiers")
    emit_log(log_callback, "Writing metaai_prompts.xlsx")
    if progress_callback:
        progress_callback(processed_files + 8, total_steps, "Writing metaai_prompts.xlsx")
    ensure_not_stopped()
    write_table_xlsx(csv_output_dir / "metaai_prompts.xlsx", [asdict(item) for item in prompt_records], "Prompts")
    if case_settings_records:
        emit_log(log_callback, "Writing stella_case_settings.xlsx")
        if progress_callback:
            progress_callback(processed_files + 8, total_steps, "Writing stella_case_settings.xlsx")
        ensure_not_stopped()
        write_table_xlsx(csv_output_dir / "stella_case_settings.xlsx", [asdict(item) for item in case_settings_records], "Case Settings")
    if device_sync_records:
        emit_log(log_callback, "Writing stella_device_sync_log.xlsx")
        if progress_callback:
            progress_callback(processed_files + 8, total_steps, "Writing stella_device_sync_log.xlsx")
        ensure_not_stopped()
        write_table_xlsx(csv_output_dir / "stella_device_sync_log.xlsx", [asdict(item) for item in device_sync_records], "Device Sync")
    if derived_sku_records:
        emit_log(log_callback, "Writing stella_derived_sku_info.xlsx")
        if progress_callback:
            progress_callback(processed_files + 9, total_steps, "Writing stella_derived_sku_info.xlsx")
        ensure_not_stopped()
        write_table_xlsx(csv_output_dir / "stella_derived_sku_info.xlsx", [asdict(item) for item in derived_sku_records], "Derived SKU")
    if input_mode != "apple":
        android_profile_rows = [asdict(item) for item in android_profile_records]
        android_device_rows = [asdict(item) for item in android_device_records]
        android_sync_rows = [asdict(item) for item in android_sync_records]

        emit_log(log_callback, "Writing stella_app_profiles.xlsx")
        ensure_not_stopped()
        write_table_xlsx(csv_output_dir / "stella_app_profiles.xlsx", android_profile_rows, "App Profiles")
        emit_log(log_callback, "Writing android_meta_app_profiles.xlsx")
        ensure_not_stopped()
        write_table_xlsx(csv_output_dir / "android_meta_app_profiles.xlsx", android_profile_rows, "App Profiles")

        emit_log(log_callback, "Writing stella_device_records.xlsx")
        ensure_not_stopped()
        write_table_xlsx(csv_output_dir / "stella_device_records.xlsx", android_device_rows, "Device Records")
        emit_log(log_callback, "Writing android_meta_devices.xlsx")
        ensure_not_stopped()
        write_table_xlsx(csv_output_dir / "android_meta_devices.xlsx", android_device_rows, "Device Records")

        emit_log(log_callback, "Writing stella_sync_activity.xlsx")
        ensure_not_stopped()
        write_table_xlsx(csv_output_dir / "stella_sync_activity.xlsx", android_sync_rows, "Sync Activity")
        emit_log(log_callback, "Writing android_meta_sync.xlsx")
        ensure_not_stopped()
        write_table_xlsx(csv_output_dir / "android_meta_sync.xlsx", android_sync_rows, "Sync Activity")
    emit_log(log_callback, "Writing media_hits.xlsx")
    if progress_callback:
        progress_callback(processed_files + 9, total_steps, "Writing media_hits.xlsx")
    ensure_not_stopped()
    write_table_xlsx(csv_output_dir / "media_hits.xlsx", [media_record_export_row(item) for item in media_records], "Media Hits")
    emit_log(log_callback, "Writing media_hits_exif_full.xlsx")
    if progress_callback:
        progress_callback(processed_files + 10, total_steps, "Writing media_hits_exif_full.xlsx")
    ensure_not_stopped()
    exif_export_count = export_media_exif_csv(csv_output_dir / "media_hits_exif_full.xlsx", media_records, exiftool_path)
    emit_log(log_callback, f"EXIF workbook columns written: {exif_export_count}")
    emit_log(log_callback, "Writing stella_linked_accounts.xlsx")
    if progress_callback:
        progress_callback(processed_files + 11, total_steps, "Writing stella_linked_accounts.xlsx")
    ensure_not_stopped()
    write_table_xlsx(csv_output_dir / "stella_linked_accounts.xlsx", [asdict(item) for item in account_records], "Linked Accounts")
    if progress_callback:
        progress_callback(processed_files + 12, total_steps, "Exporting File_Hits")
    emit_log(log_callback, "Exporting File_Hits")
    ensure_not_stopped()
    exported_file_counts = export_hit_files(output, prompt_records, media_records, log_callback=log_callback)
    summary["exported_file_counts"] = exported_file_counts
    ensure_not_stopped()
    write_json(json_output_dir / "summary.json", summary)
    if progress_callback:
        progress_callback(processed_files + 13, total_steps, "Building HTML report")
    ensure_not_stopped()
    write_reference_pdf(output, log_callback=log_callback)
    emit_log(log_callback, "Building HTML report")
    ensure_not_stopped()
    write_html_report(
        output / "report.html",
        summary,
        identifier_records,
        prompt_records,
        media_records,
        account_records,
        case_settings_records,
        device_sync_records,
        derived_sku_records,
        android_profile_records,
        android_device_records,
        android_sync_records,
    )
    emit_log(log_callback, "Report generation complete")

    print(f"Scan complete. Output written to: {output}")
    print(f"Case settings hits: {len(case_settings_records)}")
    print(f"Device sync hits: {len(device_sync_records)}")
    print(f"Derived sku hits: {len(derived_sku_records)}")
    if input_mode != "apple":
        print(f"Android profile hits: {len(android_profile_records)}")
        print(f"Android device hits: {len(android_device_records)}")
        print(f"Android sync hits: {len(android_sync_records)}")
    print(f"Linked account hits: {len(account_records)}")
    print(f"Identifiers: {len(identifier_records)}")
    print(f"Prompt hits: {len(prompt_records)}")
    print(f"Media hits: {len(media_records)}")


def execute_scan(
    input_path: str,
    output_path: str,
    exiftool_arg: str | None = None,
    progress_callback=None,
    log_callback=None,
    case_metadata: CaseMetadata | None = None,
    input_mode: str = "auto",
    should_stop=None,
) -> dict[str, Any]:
    input_root = Path(input_path).resolve()
    output_base = Path(output_path).resolve()
    exiftool_path = locate_exiftool(exiftool_arg, REPO_ROOT)
    extracted_temp_dir: tempfile.TemporaryDirectory[str] | None = None

    if not input_root.exists():
        raise FileNotFoundError(f"Input path does not exist: {input_root}")

    try:
        effective_case_metadata = case_metadata or CaseMetadata()
        scan_root = input_root
        timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        output_root = output_base / build_output_folder_name(APP_NAME, effective_case_metadata.case_number, timestamp)
        if input_root.is_file():
            if not is_supported_archive(input_root):
                raise ValueError(
                    "Input file must be a supported archive: .zip, .tar, .tgz, .tar.gz, or .gz"
                )
            if progress_callback:
                progress_callback(0, 0, "Extracting archive")
            emit_log(log_callback, f"Extracting archive: {input_root}")
            scan_root, extracted_temp_dir = extract_archive_to_temp(input_root, progress_callback=progress_callback)
            if should_stop and should_stop():
                raise ScanCancelledError("Scan stopped by user.")

        emit_log(log_callback, "Counting supported files for progress tracking")
        candidate_files = list_candidate_files(scan_root, progress_callback=progress_callback)
        candidate_files = [path for path in candidate_files if path_matches_input_mode(path, input_mode)]
        total_candidate_files = len(candidate_files)
        emit_log(log_callback, f"Found {total_candidate_files:,} supported files to evaluate ({input_mode})")
        run(
            scan_root,
            output_root,
            exiftool_path,
            candidate_files=candidate_files,
            display_input_root=str(input_root),
            progress_callback=progress_callback,
            log_callback=log_callback,
            total_candidate_files=total_candidate_files,
            case_metadata=effective_case_metadata,
            input_mode=input_mode,
            should_stop=should_stop,
        )
        summary_path = output_root / "JSON" / "summary.json"
        if summary_path.exists():
            with open(windows_safe_path(summary_path), "r", encoding="utf-8") as handle:
                summary = json.load(handle)
                summary["report_path"] = str(output_root / "report.html")
                return summary
        return {"report_path": str(output_root / "report.html")}
    finally:
        cleanup_temp_dir(extracted_temp_dir)


def parse_args():
    parser = argparse.ArgumentParser(
        description=f"{APP_NAME} for identifiers and related media."
    )
    parser.add_argument("input", nargs="?", help="Path to the extracted file system or evidence folder")
    parser.add_argument("output", nargs="?", help="Folder where Excel and JSON output will be written")
    parser.add_argument(
        "--exiftool",
        help="Optional path to exiftool executable for stronger media identification",
        default=None,
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Launch the desktop GUI instead of running in command line mode",
    )
    parser.add_argument(
        "--input-mode",
        choices=("apple", "android"),
        default="apple",
        help="Use only the Apple/iOS parser path or only the Android parser path",
    )
    return parser.parse_args()


def launch_gui():
    root = tk.Tk()
    root.title(APP_NAME)
    root.geometry("860x760")
    root.minsize(820, 700)
    root.configure(bg=BRAND_DARK)

    root.columnconfigure(0, weight=1)
    root.rowconfigure(0, weight=1)

    canvas = tk.Canvas(root, bg=BRAND_DARK, highlightthickness=0, bd=0)
    canvas.grid(row=0, column=0, sticky="nsew")
    scrollbar = ttk.Scrollbar(root, orient="vertical", command=canvas.yview)
    scrollbar.grid(row=0, column=1, sticky="ns")
    canvas.configure(yscrollcommand=scrollbar.set)

    content = ttk.Frame(canvas)
    content.columnconfigure(1, weight=1)
    content.rowconfigure(7, weight=1)

    canvas_window = canvas.create_window((0, 0), window=content, anchor="nw")

    def sync_scroll_region(_event=None):
        canvas.configure(scrollregion=canvas.bbox("all"))

    def resize_content_width(event):
        canvas.itemconfigure(canvas_window, width=event.width)

    def on_mousewheel(event):
        delta = 0
        if event.delta:
            delta = int(-event.delta / 120)
        elif getattr(event, "num", None) == 4:
            delta = -1
        elif getattr(event, "num", None) == 5:
            delta = 1
        if delta:
            canvas.yview_scroll(delta, "units")

    content.bind("<Configure>", sync_scroll_region)
    canvas.bind("<Configure>", resize_content_width)
    canvas.bind_all("<MouseWheel>", on_mousewheel)
    canvas.bind_all("<Button-4>", on_mousewheel)
    canvas.bind_all("<Button-5>", on_mousewheel)

    style = ttk.Style()
    try:
        style.theme_use("clam")
    except Exception:
        pass
    current_theme = {"name": "dark"}
    theme_button = None
    logo_label = None
    logo_badge = None
    notes_text = None
    log_text = None
    active_status_label = None
    last_output_root = {"path": None}

    def get_palette() -> dict[str, str]:
        return THEME_PALETTES[current_theme["name"]]

    def draw_logo_badge():
        if logo_badge is None:
            return
        palette = get_palette()
        logo_badge.configure(bg=palette["bg"])
        logo_badge.delete("all")
        logo_badge.create_oval(4, 4, 62, 62, outline=palette["line"], width=2, fill=palette["panel_alt"])
        logo_badge.create_rectangle(16, 28, 50, 40, outline=palette["text"], width=2)
        logo_badge.create_line(27, 34, 39, 34, fill=palette["cyan_bright"], width=3)
        logo_badge.create_oval(51, 11, 57, 17, fill=palette["gold"], outline="")
        logo_badge.create_oval(11, 17, 16, 22, fill=palette["cyan_bright"], outline="")
        logo_badge.create_oval(57, 22, 62, 27, fill=palette["cyan_bright"], outline="")

    def apply_theme(theme_name: str):
        current_theme["name"] = "light" if theme_name == "light" else "dark"
        palette = get_palette()
        root.configure(bg=palette["bg"])
        canvas.configure(bg=palette["bg"])
        style.configure(".", background=palette["bg"], foreground=palette["text"])
        style.configure("TFrame", background=palette["bg"])
        style.configure("TLabel", background=palette["bg"], foreground=palette["text"])
        style.configure("Title.TLabel", background=palette["bg"], foreground=palette["text"], font=("Segoe UI", 20, "bold"))
        style.configure("Subtitle.TLabel", background=palette["bg"], foreground=palette["cyan_bright"], font=("Segoe UI", 10, "bold"))
        style.configure("Muted.TLabel", background=palette["bg"], foreground=palette["muted"])
        style.configure(
            "TButton",
            background=palette["panel_alt"],
            foreground=palette["text"],
            bordercolor=palette["line"],
            focusthickness=1,
            focuscolor=palette["cyan"],
            padding=8,
        )
        style.map(
            "TButton",
            background=[("active", palette["cyan"]), ("disabled", palette["panel"])],
            foreground=[("active", palette["button_active_fg"]), ("disabled", palette["muted"])],
        )
        style.configure(
            "TEntry",
            fieldbackground=palette["panel"],
            foreground=palette["text"],
            insertcolor=palette["text"],
            bordercolor=palette["line"],
        )
        style.configure(
            "TCombobox",
            fieldbackground=palette["panel"],
            background=palette["panel"],
            foreground=palette["text"],
            arrowcolor="#ffffff" if current_theme["name"] == "dark" else LIGHT_TEXT,
            bordercolor=palette["line"],
            insertcolor=palette["text"],
            selectbackground=palette["cyan"],
            selectforeground=palette["button_active_fg"],
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", palette["panel"]), ("disabled", palette["panel"])],
            background=[("readonly", palette["panel"]), ("disabled", palette["panel"])],
            foreground=[("readonly", palette["text"]), ("disabled", palette["muted"])],
            arrowcolor=[("readonly", "#ffffff" if current_theme["name"] == "dark" else LIGHT_TEXT), ("disabled", "#ffffff" if current_theme["name"] == "dark" else LIGHT_TEXT)],
            selectbackground=[("readonly", palette["cyan"])],
            selectforeground=[("readonly", palette["button_active_fg"])],
        )
        style.configure(
            "TLabelframe",
            background=palette["bg"],
            foreground=palette["cyan_bright"],
            bordercolor=palette["line"],
            relief="solid",
        )
        style.configure(
            "TLabelframe.Label",
            background=palette["bg"],
            foreground=palette["cyan_bright"],
            font=("Segoe UI", 10, "bold"),
        )
        style.configure(
            "TProgressbar",
            background=palette["cyan"],
            troughcolor=palette["panel"],
            bordercolor=palette["line"],
            lightcolor=palette["cyan_bright"],
            darkcolor=palette["cyan"],
        )
        if logo_label is not None:
            logo_label.configure(bg=palette["bg"])
        draw_logo_badge()
        if theme_button is not None:
            theme_button.configure(text="Light Mode" if current_theme["name"] == "dark" else "Dark Mode")
        if notes_text is not None:
            notes_text.configure(
                bg=palette["panel"],
                fg=palette["text"],
                insertbackground=palette["text"],
                selectbackground=palette["cyan"],
                selectforeground=palette["button_active_fg"],
                highlightbackground=palette["line"],
                highlightcolor=palette["cyan"],
            )
        if log_text is not None:
            log_text.configure(
                bg=palette["panel"],
                fg=palette["text"],
                insertbackground=palette["text"],
                selectbackground=palette["cyan"],
                selectforeground=palette["button_active_fg"],
                highlightbackground=palette["line"],
                highlightcolor=palette["cyan"],
            )
        if active_status_label is not None:
            active_status_label.configure(
                foreground=palette["cyan_bright"] if active_state_var.get() == "Status: Active" else palette["muted"]
            )

    def toggle_theme():
        apply_theme("light" if current_theme["name"] == "dark" else "dark")

    input_var = tk.StringVar()
    output_var = tk.StringVar()
    exiftool_var = tk.StringVar(value=locate_exiftool(None, REPO_ROOT) or "")
    agency_var = tk.StringVar()
    examiner_var = tk.StringVar()
    case_number_var = tk.StringVar()
    offense_type_var = tk.StringVar()
    item_number_var = tk.StringVar()
    extraction_datetime_var = tk.StringVar()
    input_mode_var = tk.StringVar(value="apple")
    status_var = tk.StringVar(value="Choose an input folder or archive and an output folder, then click Start Scan.")
    summary_var = tk.StringVar(value="No scan has been run yet.")
    active_state_var = tk.StringVar(value="Status: Idle")
    current_time_var = tk.StringVar(value="")
    elapsed_var = tk.StringVar(value="Elapsed: 00:00:00")
    eta_var = tk.StringVar(value="ETA: --")
    progress_label_var = tk.StringVar(value="Progress: 0%")
    progress_value_var = tk.DoubleVar(value=0.0)
    scan_started_at = {"value": None}
    stop_scan_requested = {"value": False}

    def browse_input_archive():
        selected = filedialog.askopenfilename(
            title="Select input archive",
            filetypes=(
                ("Supported archives", "*.zip *.tar *.tgz *.gz"),
                ("Zip files", "*.zip"),
                ("Tar files", "*.tar *.tgz *.tar.gz"),
                ("Gzip files", "*.gz"),
                ("All files", "*.*"),
            ),
        )
        if selected:
            input_var.set(selected)

    def browse_input_folder():
        selected_folder = filedialog.askdirectory(title="Select input folder")
        if selected_folder:
            input_var.set(selected_folder)

    def browse_output():
        selected = filedialog.askdirectory(title="Select output folder")
        if selected:
            output_var.set(selected)

    def browse_exiftool():
        selected = filedialog.askopenfilename(
            title="Select exiftool executable",
            filetypes=(("Executable", "*.exe"), ("All files", "*.*")),
        )
        if selected:
            exiftool_var.set(selected)

    def set_running_state(is_running: bool):
        state = "disabled" if is_running else "normal"
        start_button.config(
            state="normal",
            text="Stop Scan" if is_running else "Start Scan",
            command=request_stop if is_running else run_in_background,
        )
        input_archive_button.config(state=state)
        input_folder_button.config(state=state)
        output_button.config(state=state)
        exiftool_button.config(state=state)
        input_mode_combo.config(state="disabled" if is_running else "readonly")
        agency_entry.config(state=state)
        examiner_entry.config(state=state)
        case_number_entry.config(state=state)
        offense_type_entry.config(state=state)
        item_number_entry.config(state=state)
        extraction_datetime_entry.config(state=state)
        notes_text.config(state="disabled" if is_running else "normal")
        palette = get_palette()
        if is_running:
            active_state_var.set("Status: Active")
            active_status_label.configure(foreground=palette["cyan_bright"])
        else:
            active_state_var.set("Status: Idle")
            active_status_label.configure(foreground=palette["muted"])

    def request_stop():
        if not stop_scan_requested["value"]:
            stop_scan_requested["value"] = True
            status_var.set("Stopping scan...")
            eta_var.set("ETA: Stopping...")
            append_log("Stop requested by user. Finishing current step before canceling.")

    def update_clock():
        started_at = scan_started_at["value"]
        if started_at:
            elapsed_seconds = max(0, int(time.time() - started_at))
            hours, remainder = divmod(elapsed_seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            elapsed_var.set(f"Elapsed: {hours:02d}:{minutes:02d}:{seconds:02d}")
        else:
            elapsed_var.set("Elapsed: 00:00:00")
        current_time_var.set(f"Current time: {dt.datetime.now().strftime('%Y-%m-%d %I:%M:%S %p')}")
        root.after(1000, update_clock)

    def update_progress(processed: int, total: int, phase: str):
        status_var.set(f"{phase}...")
        if total <= 0:
            progress_label_var.set(f"Progress: working... ({phase})")
            eta_var.set("ETA: Calculating...")
            progress_bar.config(mode="indeterminate")
            if not getattr(progress_bar, "_running", False):
                progress_bar.start(40)
                progress_bar._running = True
            return

        if getattr(progress_bar, "_running", False):
            progress_bar.stop()
            progress_bar._running = False
        progress_bar.config(mode="determinate")
        total = max(total, 1)
        percent = (processed / total) * 100
        progress_value_var.set(percent)
        progress_label_var.set(f"Progress: {percent:.1f}% ({phase})")

        started_at = scan_started_at["value"]
        if started_at and processed > 0:
            elapsed = time.time() - started_at
            remaining = max(total - processed, 0)
            eta_seconds = int((elapsed / processed) * remaining)
            eta_time = dt.datetime.now() + dt.timedelta(seconds=eta_seconds)
            eta_var.set(f"ETA: {eta_time.strftime('%Y-%m-%d %I:%M:%S %p')}")
        else:
            eta_var.set("ETA: Calculating...")

    def append_log(message: str):
        timestamp = dt.datetime.now().strftime("%H:%M:%S")
        log_text.configure(state="normal")
        log_text.insert("end", f"[{timestamp}] {message}\n")
        log_text.see("end")
        log_text.configure(state="disabled")

    def run_in_background():
        input_path = input_var.get().strip()
        output_path = output_var.get().strip()
        exiftool_path = exiftool_var.get().strip()
        input_mode = input_mode_var.get().strip().lower() or "apple"
        case_metadata = CaseMetadata(
            agency=agency_var.get().strip(),
            examiner_name=examiner_var.get().strip(),
            case_number=case_number_var.get().strip(),
            offense_type=offense_type_var.get().strip(),
            item_number=item_number_var.get().strip(),
            extraction_datetime=extraction_datetime_var.get().strip(),
            owner="",
            imei="",
            serial_number="",
            notes=notes_text.get("1.0", "end").strip(),
        )

        if not input_path or not output_path:
            messagebox.showerror(
                "Missing Paths",
                "Please choose an input folder or archive and an output parent folder.",
            )
            return

        status_var.set("Scanning files. This can take a while on large evidence folders or archives.")
        summary_var.set("Scan in progress...")
        progress_value_var.set(0.0)
        progress_label_var.set("Progress: starting...")
        scan_started_at["value"] = time.time()
        stop_scan_requested["value"] = False
        elapsed_var.set("Elapsed: 00:00:00")
        eta_var.set("ETA: Calculating...")
        log_text.configure(state="normal")
        log_text.delete("1.0", "end")
        log_text.configure(state="disabled")
        append_log(f"Starting scan for input: {input_path}")
        append_log(f"Output parent folder: {output_path}")
        append_log(f"Input type mode: {input_mode}")
        append_log(
            "Case details: Agency={0} | Case Number={1} | Item Number={2}".format(
                case_metadata.agency or "None",
                case_metadata.case_number or "None",
                case_metadata.item_number or "None",
            )
        )
        if getattr(progress_bar, "_running", False):
            progress_bar.stop()
            progress_bar._running = False
        progress_bar.config(mode="indeterminate")
        progress_bar.start(40)
        progress_bar._running = True
        set_running_state(True)

        def progress_reporter(processed: int, total: int, phase: str):
            root.after(0, lambda p=processed, t=total, ph=phase: update_progress(p, t, ph))

        def log_reporter(message: str):
            root.after(0, lambda msg=message: append_log(msg))

        def worker():
            try:
                summary = execute_scan(
                    input_path,
                    output_path,
                    exiftool_path or None,
                    progress_callback=progress_reporter,
                    log_callback=log_reporter,
                    case_metadata=case_metadata,
                    input_mode=input_mode,
                    should_stop=lambda: stop_scan_requested["value"],
                )
            except ScanCancelledError as exc:
                root.after(0, lambda error=exc: on_cancel(error))
                return
            except Exception as exc:
                root.after(0, lambda error=exc: on_error(error))
                return
            root.after(0, lambda: on_success(summary))

        threading.Thread(target=worker, daemon=True).start()

    def on_success(summary: dict[str, Any]):
        set_running_state(False)
        stop_scan_requested["value"] = False
        if getattr(progress_bar, "_running", False):
            progress_bar.stop()
            progress_bar._running = False
        output_path = summary.get("output_folder", "") or str(Path(summary.get("report_path", "")).parent)
        last_output_root["path"] = output_path or None
        status_var.set(f"Scan complete. Output written to {output_path}")
        progress_value_var.set(100.0)
        progress_label_var.set("Progress: 100.0% (Complete)")
        scan_started_at["value"] = None
        eta_var.set("ETA: Complete")
        mode = normalize_input_mode(summary.get("input_mode", "auto"))
        summary_parts = []
        log_parts = []
        if mode != "android":
            summary_parts.extend(
                [
                    f"iOS Case: {summary.get('case_settings_count', 0)}",
                    f"iOS Sync: {summary.get('device_sync_count', 0)}",
                    f"iOS SKU: {summary.get('derived_sku_count', 0)}",
                ]
            )
            log_parts.extend(
                [
                    f"{summary.get('case_settings_count', 0)} iOS case settings",
                    f"{summary.get('device_sync_count', 0)} iOS sync logs",
                    f"{summary.get('derived_sku_count', 0)} iOS sku records",
                ]
            )
        if mode != "apple":
            summary_parts.extend(
                [
                    f"App Profiles: {summary.get('android_profile_count', 0)}",
                    f"Device Records: {summary.get('android_device_count', 0)}",
                    f"Sync Activity: {summary.get('android_sync_count', 0)}",
                ]
            )
            log_parts.extend(
                [
                    f"{summary.get('android_profile_count', 0)} app profiles",
                    f"{summary.get('android_device_count', 0)} device records",
                    f"{summary.get('android_sync_count', 0)} sync activity records",
                ]
            )
        summary_parts.extend(
            [
                f"Accounts: {summary.get('account_count', 0)}",
                f"Prompts: {summary.get('prompt_count', 0)}",
                f"Media: {summary.get('media_count', 0)}",
            ]
        )
        log_parts.extend(
            [
                f"{summary.get('account_count', 0)} linked accounts",
                f"{summary.get('prompt_count', 0)} prompts",
                f"{summary.get('media_count', 0)} media hits",
            ]
        )
        summary_var.set(" | ".join(summary_parts))
        append_log("Scan complete: " + ", ".join(log_parts))
        report_path = summary.get("report_path", "")
        if report_path and Path(report_path).exists():
            try:
                webbrowser.open_new_tab(Path(report_path).resolve().as_uri())
                append_log(f"Opened report in browser: {report_path}")
            except Exception:
                append_log(f"Report created: {report_path}")
                pass
        messagebox.showinfo(
            "Scan Complete",
            f"Finished.\n\nOutput folder:\n{output_path}\n\nHTML report opened automatically if available.",
        )

    def on_cancel(exc: Exception):
        set_running_state(False)
        stop_scan_requested["value"] = False
        if getattr(progress_bar, "_running", False):
            progress_bar.stop()
            progress_bar._running = False
        status_var.set("Scan stopped.")
        summary_var.set(str(exc))
        scan_started_at["value"] = None
        eta_var.set("ETA: Stopped")
        append_log(f"Scan stopped: {exc}")

    def on_error(exc: Exception):
        set_running_state(False)
        stop_scan_requested["value"] = False
        if getattr(progress_bar, "_running", False):
            progress_bar.stop()
            progress_bar._running = False
        status_var.set("Scan failed.")
        summary_var.set(str(exc))
        scan_started_at["value"] = None
        eta_var.set("ETA: Failed")
        append_log(f"Scan failed: {exc}")
        messagebox.showerror("Scan Failed", str(exc))

    title_frame = ttk.Frame(content)
    title_frame.grid(row=0, column=0, columnspan=3, padx=14, pady=(14, 10), sticky="ew")
    title_frame.columnconfigure(1, weight=1)

    logo_path = get_logo_path()
    logo_image = None
    if logo_path.exists():
        try:
            logo_image = tk.PhotoImage(file=str(logo_path))
            scale = max(1, (logo_image.width() + 219) // 220)
            if scale > 1:
                logo_image = logo_image.subsample(scale, scale)
        except Exception:
            logo_image = None

    if logo_image is not None:
        root._logo_image = logo_image
        logo_label = tk.Label(
            title_frame,
            image=logo_image,
            bg=BRAND_DARK,
            bd=0,
            highlightthickness=0,
        )
        logo_label.grid(row=0, column=0, rowspan=2, padx=(0, 12), sticky="w")
    else:
        logo_badge = tk.Canvas(
            title_frame,
            width=66,
            height=66,
            bg=BRAND_DARK,
            highlightthickness=0,
            bd=0,
        )
        logo_badge.grid(row=0, column=0, rowspan=2, padx=(0, 12), sticky="w")
        logo_badge.create_oval(4, 4, 62, 62, outline=BRAND_LINE, width=2, fill=BRAND_PANEL_ALT)
        logo_badge.create_rectangle(16, 28, 50, 40, outline=BRAND_TEXT, width=2)
        logo_badge.create_line(27, 34, 39, 34, fill=BRAND_CYAN_BRIGHT, width=3)
        logo_badge.create_oval(51, 11, 57, 17, fill=BRAND_GOLD, outline="")
        logo_badge.create_oval(11, 17, 16, 22, fill=BRAND_CYAN_BRIGHT, outline="")
        logo_badge.create_oval(57, 22, 62, 27, fill=BRAND_CYAN_BRIGHT, outline="")

    title_label = ttk.Label(title_frame, text=APP_NAME, style="Title.TLabel")
    title_label.grid(row=0, column=1, sticky="w")
    subtitle_label = ttk.Label(title_frame, text=APP_SUBTITLE, style="Subtitle.TLabel")
    subtitle_label.grid(row=1, column=1, sticky="w")
    theme_button = ttk.Button(title_frame, text="Light Mode", command=toggle_theme)
    theme_button.grid(row=0, column=2, rowspan=2, padx=(12, 0), sticky="e")

    ttk.Label(content, text="Input Folder / Archive").grid(row=1, column=0, padx=14, pady=6, sticky="w")
    ttk.Label(content, text="Output Parent Folder").grid(row=2, column=0, padx=14, pady=6, sticky="w")
    ttk.Label(content, text="ExifTool").grid(row=3, column=0, padx=14, pady=6, sticky="w")
    ttk.Label(content, text="Input Type").grid(row=4, column=0, padx=14, pady=6, sticky="w")

    input_entry = ttk.Entry(content, textvariable=input_var)
    input_entry.grid(row=1, column=1, padx=8, pady=6, sticky="ew")
    output_entry = ttk.Entry(content, textvariable=output_var)
    output_entry.grid(row=2, column=1, padx=8, pady=6, sticky="ew")
    exiftool_entry = ttk.Entry(content, textvariable=exiftool_var)
    exiftool_entry.grid(row=3, column=1, padx=8, pady=6, sticky="ew")
    input_mode_combo = ttk.Combobox(
        content,
        textvariable=input_mode_var,
        values=("apple", "android"),
        state="readonly",
    )
    input_mode_combo.grid(row=4, column=1, padx=8, pady=6, sticky="ew")

    input_button_frame = ttk.Frame(content)
    input_button_frame.grid(row=1, column=2, padx=14, pady=6, sticky="w")
    input_archive_button = ttk.Button(input_button_frame, text="Browse Archive", command=browse_input_archive)
    input_archive_button.grid(row=0, column=0, padx=(0, 6))
    input_folder_button = ttk.Button(input_button_frame, text="Browse Folder", command=browse_input_folder)
    input_folder_button.grid(row=0, column=1)
    output_button = ttk.Button(content, text="Browse", command=browse_output)
    output_button.grid(row=2, column=2, padx=14, pady=6)
    exiftool_button = ttk.Button(content, text="Browse", command=browse_exiftool)
    exiftool_button.grid(row=3, column=2, padx=14, pady=6)
    ttk.Label(content, text="Pick either an extracted folder or a supported archive.", style="Muted.TLabel").grid(
        row=1, column=3, padx=(0, 14), pady=6, sticky="w"
    )
    ttk.Label(content, text="Choose Apple or Android to use only that parser path.", style="Muted.TLabel").grid(
        row=4, column=2, columnspan=2, padx=14, pady=6, sticky="w"
    )

    case_frame = ttk.LabelFrame(content, text="Case Fields")
    case_frame.grid(row=5, column=0, columnspan=3, padx=14, pady=(6, 8), sticky="ew")
    case_frame.columnconfigure(1, weight=1)
    case_frame.columnconfigure(3, weight=1)

    ttk.Label(case_frame, text="Agency").grid(row=0, column=0, padx=10, pady=(10, 6), sticky="w")
    agency_entry = ttk.Entry(case_frame, textvariable=agency_var)
    agency_entry.grid(row=0, column=1, padx=(0, 10), pady=(10, 6), sticky="ew")

    ttk.Label(case_frame, text="Examiner Name").grid(row=0, column=2, padx=10, pady=(10, 6), sticky="w")
    examiner_entry = ttk.Entry(case_frame, textvariable=examiner_var)
    examiner_entry.grid(row=0, column=3, padx=(0, 10), pady=(10, 6), sticky="ew")

    ttk.Label(case_frame, text="Case Number").grid(row=1, column=0, padx=10, pady=6, sticky="w")
    case_number_entry = ttk.Entry(case_frame, textvariable=case_number_var)
    case_number_entry.grid(row=1, column=1, padx=(0, 10), pady=6, sticky="ew")

    ttk.Label(case_frame, text="Offense Type").grid(row=1, column=2, padx=10, pady=6, sticky="w")
    offense_type_entry = ttk.Entry(case_frame, textvariable=offense_type_var)
    offense_type_entry.grid(row=1, column=3, padx=(0, 10), pady=6, sticky="ew")

    ttk.Label(case_frame, text="Item Number").grid(row=2, column=0, padx=10, pady=6, sticky="w")
    item_number_entry = ttk.Entry(case_frame, textvariable=item_number_var)
    item_number_entry.grid(row=2, column=1, padx=(0, 10), pady=6, sticky="ew")

    ttk.Label(case_frame, text="Extraction Date/Time").grid(row=2, column=2, padx=10, pady=6, sticky="w")
    extraction_datetime_entry = ttk.Entry(case_frame, textvariable=extraction_datetime_var)
    extraction_datetime_entry.grid(row=2, column=3, padx=(0, 10), pady=6, sticky="ew")

    ttk.Label(case_frame, text="Notes").grid(row=3, column=0, padx=10, pady=(6, 10), sticky="nw")
    notes_text = scrolledtext.ScrolledText(
        case_frame,
        height=4,
        wrap="word",
        bg=BRAND_PANEL,
        fg=BRAND_TEXT,
        insertbackground=BRAND_TEXT,
        selectbackground=BRAND_CYAN,
        selectforeground=BRAND_DARK,
        relief="solid",
        borderwidth=1,
        highlightthickness=1,
        highlightbackground=BRAND_LINE,
        highlightcolor=BRAND_CYAN,
    )
    notes_text.grid(row=3, column=1, columnspan=3, padx=(0, 10), pady=(6, 10), sticky="ew")

    progress_frame = ttk.LabelFrame(content, text="Progress")
    progress_frame.grid(row=6, column=0, columnspan=3, padx=14, pady=(10, 8), sticky="ew")
    progress_frame.columnconfigure(0, weight=1)

    progress_bar = ttk.Progressbar(progress_frame, variable=progress_value_var, maximum=100)
    progress_bar.grid(row=0, column=0, columnspan=2, padx=10, pady=(10, 8), sticky="ew")
    ttk.Label(progress_frame, textvariable=progress_label_var, style="Muted.TLabel").grid(row=1, column=0, padx=10, pady=(0, 8), sticky="w")
    ttk.Label(progress_frame, textvariable=eta_var, style="Muted.TLabel").grid(row=1, column=1, padx=10, pady=(0, 8), sticky="e")
    active_status_label = ttk.Label(progress_frame, textvariable=active_state_var, style="Muted.TLabel")
    active_status_label.grid(row=2, column=0, padx=10, pady=(0, 8), sticky="w")
    ttk.Label(progress_frame, textvariable=elapsed_var, style="Muted.TLabel").grid(row=3, column=0, columnspan=2, padx=10, pady=(0, 6), sticky="w")
    ttk.Label(progress_frame, textvariable=current_time_var, style="Muted.TLabel").grid(row=4, column=0, columnspan=2, padx=10, pady=(0, 10), sticky="w")

    status_frame = ttk.LabelFrame(content, text="Status")
    status_frame.grid(row=7, column=0, columnspan=3, padx=14, pady=(2, 8), sticky="nsew")
    status_frame.columnconfigure(0, weight=1)
    status_frame.rowconfigure(2, weight=1)

    ttk.Label(status_frame, textvariable=status_var, wraplength=680).grid(
        row=0, column=0, padx=10, pady=(10, 6), sticky="w"
    )
    ttk.Label(status_frame, textvariable=summary_var, wraplength=680, justify="left", style="Muted.TLabel").grid(
        row=1, column=0, padx=10, pady=(0, 10), sticky="nw"
    )
    log_text = scrolledtext.ScrolledText(
        status_frame,
        height=10,
        wrap="word",
        state="disabled",
        bg=BRAND_PANEL,
        fg=BRAND_TEXT,
        insertbackground=BRAND_TEXT,
        selectbackground=BRAND_CYAN,
        selectforeground=BRAND_DARK,
        relief="solid",
        borderwidth=1,
        highlightthickness=1,
        highlightbackground=BRAND_LINE,
        highlightcolor=BRAND_CYAN,
    )
    log_text.grid(row=2, column=0, padx=10, pady=(0, 10), sticky="nsew")

    start_button = ttk.Button(content, text="Start Scan", command=run_in_background)
    start_button.grid(row=8, column=0, padx=14, pady=(4, 14), sticky="w")
    close_button = ttk.Button(content, text="Close", command=root.destroy)
    close_button.grid(row=8, column=2, padx=14, pady=(4, 14), sticky="e")

    apply_theme("dark")
    update_clock()
    root.mainloop()


def main():
    args = parse_args()
    if args.gui or not (args.input and args.output):
        launch_gui()
        return

    execute_scan(args.input, args.output, args.exiftool, input_mode=args.input_mode)


if __name__ == "__main__":
    main()
