from typing import Any

from scripts.models import StellaCaseSettingsRecord, StellaDerivedSkuRecord, StellaDeviceSyncRecord
from scripts.utils import device_id_from_path, normalize_meta_timestamp, normalize_text, suffix_key


def source_is_stella_account_artifact(path) -> bool:
    return "com.stellaapp.fxlinkedaccountsstore" in str(path).lower()


def source_is_stella_case_settings_artifact(path) -> bool:
    lowered = str(path).lower()
    return "com.meta.mwa.glasses.usersettingsstore" in lowered or "com.meta.mwa.glasses.usersettingstore" in lowered


def source_is_stella_sync_log_artifact(path) -> bool:
    return "com.meta.mwa.dmcsynclog" in str(path).lower()


def source_is_stella_derived_sku_artifact(path) -> bool:
    return "com.meta.mwa.derivedskuinfo" in str(path).lower()


def extract_stella_case_settings(data: Any, path, records: list[StellaCaseSettingsRecord]):
    if not isinstance(data, dict):
        return
    normalized = {suffix_key(key): value for key, value in data.items()}
    records.append(
        StellaCaseSettingsRecord(
            glasses_device_id=device_id_from_path(path),
            case_serial_number=normalize_text(normalized.get("caseSerial", "")).strip(),
            case_software_version=normalize_text(normalized.get("caseVersion", "")).strip(),
            last_settings_snapshot_time=normalize_meta_timestamp(normalized.get("lastSettingsSnapshotTime", "")),
            has_completed_voice_oobe=normalize_text(normalized.get("hasCompletedVoiceOOBE", "")).strip(),
            meta_ai_opt_in_completed=normalize_text(normalized.get("metaAIOptInCompleted", "")).strip(),
            meta_ai_geo_opt_in_completed=normalize_text(normalized.get("metaAIGeoOptInCompleted", "")).strip(),
            live_ai_eap_opt_in_status=normalize_text(normalized.get("liveAIEAPOptInStatus", "")).strip(),
            default_provider_backward_compatibility_script_run=normalize_text(
                normalized.get("hasDefaultProviderBackwardCompatibilityScriptRun", "")
            ).strip(),
            default_provider_backward_compatibility_script_run_v2=normalize_text(
                normalized.get("hasDefaultProviderBackwardCompatibilityScriptRunV2", "")
            ).strip(),
            show_language_reverted_notification=normalize_text(
                normalized.get("shouldShowLanguageRevertedNotification", "")
            ).strip(),
            show_language_reverted_push_notification=normalize_text(
                normalized.get("shouldShowLanguageRevertedPushNotification", "")
            ).strip(),
            source_path=str(path),
        )
    )


def extract_stella_device_sync(data: Any, path, records: list[StellaDeviceSyncRecord]):
    if not isinstance(data, dict):
        return
    normalized = {suffix_key(key): value for key, value in data.items()}
    records.append(
        StellaDeviceSyncRecord(
            glasses_device_id=device_id_from_path(path),
            glasses_firmware_version=normalize_text(normalized.get("lastSyncFirmware", "")).strip(),
            app_version_at_last_sync=normalize_text(normalized.get("lastSyncAppVersion", "")).strip(),
            last_sync_time=normalize_meta_timestamp(normalized.get("lastSyncTime", "")),
            source_path=str(path),
        )
    )


def extract_stella_derived_sku(data: Any, path, records: list[StellaDerivedSkuRecord]):
    if not isinstance(data, dict):
        return
    normalized = {suffix_key(key): value for key, value in data.items()}
    records.append(
        StellaDerivedSkuRecord(
            glasses_serial_number=device_id_from_path(path),
            model=normalize_text(normalized.get("frameTypeDisplayName", "")).strip(),
            model_short_name=normalize_text(normalized.get("frameTypeShortDisplayName", "")).strip(),
            frame_style=normalize_text(normalized.get("frameStyle", "")).strip(),
            frame_color_display_name=normalize_text(normalized.get("frameColorDisplayName", "")).strip(),
            frame_color=normalize_text(normalized.get("frameColor", "")).strip(),
            lens_color_display_name=normalize_text(normalized.get("lensColorDisplayName", "")).strip(),
            lens_color=normalize_text(normalized.get("lensColor", "")).strip(),
            source_path=str(path),
        )
    )
