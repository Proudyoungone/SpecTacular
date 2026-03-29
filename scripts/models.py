from dataclasses import dataclass


@dataclass
class IdentifierRecord:
    artifact_type: str
    key: str
    value: str
    source_path: str
    context: str


@dataclass
class MediaRecord:
    media_path: str
    extension: str
    score: int
    reasons: str
    exif_make: str
    exif_model: str
    exif_software: str
    exif_datetime: str = ""
    latitude: str = ""
    longitude: str = ""
    report_copy_path: str = ""
    report_preview_path: str = ""


@dataclass
class PromptRecord:
    prompt_timestamp: str
    response_timestamp: str
    prompt_text: str
    response_text: str
    prompt_line_number: int
    response_line_number: int
    source_path: str


@dataclass
class AccountRecord:
    field: str
    value: str
    source_path: str
    context: str


@dataclass
class CaseMetadata:
    agency: str = ""
    examiner_name: str = ""
    case_number: str = ""
    offense_type: str = ""
    item_number: str = ""
    extraction_datetime: str = ""
    owner: str = ""
    imei: str = ""
    serial_number: str = ""
    notes: str = ""


@dataclass
class StellaCaseSettingsRecord:
    glasses_device_id: str
    case_serial_number: str
    case_software_version: str
    last_settings_snapshot_time: str
    has_completed_voice_oobe: str
    meta_ai_opt_in_completed: str
    meta_ai_geo_opt_in_completed: str
    live_ai_eap_opt_in_status: str
    default_provider_backward_compatibility_script_run: str
    default_provider_backward_compatibility_script_run_v2: str
    show_language_reverted_notification: str
    show_language_reverted_push_notification: str
    source_path: str


@dataclass
class StellaDeviceSyncRecord:
    glasses_device_id: str
    glasses_firmware_version: str
    app_version_at_last_sync: str
    last_sync_time: str
    source_path: str


@dataclass
class StellaDerivedSkuRecord:
    glasses_serial_number: str
    model: str
    model_short_name: str
    frame_style: str
    frame_color_display_name: str
    frame_color: str
    lens_color_display_name: str
    lens_color: str
    source_path: str


@dataclass
class AndroidMetaAppProfileRecord:
    fetched: str
    user_id: str
    user_name: str
    short_name: str
    social_username: str
    social_display_name: str
    social_profile_id: str
    imported_data_source: str
    constellation_group_id: str
    abra_id: str
    abra_messaging_user_id: str
    eligibility_subscription: str
    source_path: str


@dataclass
class AndroidMetaDeviceRecord:
    device_codename: str
    source: str
    pairing_id: str
    device_id: str
    serial: str
    capture_type: str
    example_capture_id: str
    attributes: str
    source_path: str


@dataclass
class AndroidMetaSyncRecord:
    capture_time: str
    fetch_completed: str
    import_completed: str
    auto_saved: str
    session_id: str
    capture_id: str
    media_type: str
    import_trigger: str
    processing_state: str
    thumbnail_state: str
    full_media_state: str
    shared_media_global_id: str
    wifi_scan_data: str
    source_path: str
