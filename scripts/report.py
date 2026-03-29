import html
import json
import os
import textwrap
import base64
from datetime import datetime
from urllib.parse import quote
from dataclasses import fields as dataclass_fields
from pathlib import Path
from typing import Any

from scripts.model_reference import MODEL_REFERENCES, ModelReference, get_model_assets_dir, normalize_model_text
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
from scripts.utils import explain_media_hit, sanitize_windows_component, windows_safe_path

FIELD_REFERENCE_PDF_NAME = "SpecTacular_Field_Definitions.pdf"


def _dataclass_field_rows(record_type, descriptions: dict[str, str], default_source: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for field in dataclass_fields(record_type):
        rows.append((field.name, descriptions.get(field.name, f"Structured output field captured from {default_source}.")))
    return rows


def _field_reference_sections() -> list[tuple[str, list[tuple[str, str]]]]:
    case_metadata_rows = _dataclass_field_rows(
        CaseMetadata,
        {
            "agency": "Examiner-supplied agency value entered in the GUI before the scan begins. It identifies who conducted or owns the examination and helps preserve administrative context in reporting.",
            "examiner_name": "Examiner-supplied examiner name. This is important for attribution, chain-of-custody style reporting, and later clarification about how the data was processed.",
            "case_number": "Examiner-supplied case number used in the report metadata and output naming. It ties the parser results back to the investigative matter and helps prevent evidence from different matters being confused.",
            "offense_type": "Examiner-supplied offense or incident type. This helps readers understand investigative context and may explain why certain artifacts were of interest.",
            "item_number": "Examiner-supplied item number. This links the parsed results to the physical evidence item or extraction source in lab workflows.",
            "extraction_datetime": "Examiner-supplied extraction date and time. This helps distinguish when the device was acquired or processed from timestamps found inside the artifacts themselves.",
            "owner": "Examiner-supplied device owner or attributed owner. This provides a quick reference identity for the device and can be compared with recovered profile and account information.",
            "imei": "Examiner-supplied IMEI when known. It helps correlate the report to a specific handset and can be used to cross-check subscriber, carrier, or seizure records.",
            "serial_number": "Examiner-supplied phone serial number when known. This is useful for tying the report to a specific physical device and validating hardware identity.",
            "notes": "Examiner-supplied notes stored with the case metadata. These notes capture analyst observations or handling context that may not exist in the parsed artifacts themselves.",
        },
        "case metadata input",
    )
    apple_case_rows = _dataclass_field_rows(
        StellaCaseSettingsRecord,
        {
            "glasses_device_id": "Device identifier inferred from the iOS Stella user settings store artifact path. It shows which specific pair of glasses the settings belong to and is important when one phone has been paired with multiple devices over time.",
            "case_serial_number": "Recovered from `caseSerial` in the iOS Stella user settings store. It can identify the associated charging case or hardware bundle and helps connect app records back to a real-world device set.",
            "case_software_version": "Recovered from `caseVersion` in the iOS Stella user settings store. It tells you which software build the case-related environment was using, which can explain differences in available features, prompts, and artifact behavior.",
            "last_settings_snapshot_time": "Recovered from `lastSettingsSnapshotTime` and normalized to a readable timestamp. It shows when the app last preserved this settings state and helps place configuration changes on the examination timeline.",
            "has_completed_voice_oobe": "Recovered from `hasCompletedVoiceOOBE`. It shows whether initial voice setup was completed, which is important because it can indicate the device was far enough through onboarding to support voice-driven use.",
            "meta_ai_opt_in_completed": "Recovered from `metaAIOptInCompleted`. It indicates whether the user enabled Meta AI participation, which matters when assessing whether AI prompt artifacts should reasonably exist in the dataset.",
            "meta_ai_geo_opt_in_completed": "Recovered from `metaAIGeoOptInCompleted`. It shows whether location-related consent was granted and can help explain why certain context-aware or regional AI behaviors were available or absent.",
            "live_ai_eap_opt_in_status": "Recovered from `liveAIEAPOptInStatus`. It reflects enrollment status for an early-access or experimental AI workflow and may explain beta-only artifacts or unexpected feature behavior.",
            "default_provider_backward_compatibility_script_run": "Recovered from `hasDefaultProviderBackwardCompatibilityScriptRun`. It indicates that the app ran compatibility or migration logic, which is important because some settings may have been transformed after an update rather than manually changed by the user.",
            "default_provider_backward_compatibility_script_run_v2": "Recovered from `hasDefaultProviderBackwardCompatibilityScriptRunV2`. It provides evidence of a later migration path and helps explain why newer schema or configuration values appear in older user environments.",
            "show_language_reverted_notification": "Recovered from `shouldShowLanguageRevertedNotification`. It suggests the app detected a language-related change or rollback and considered it significant enough to notify the user, which can be useful when reviewing localization or account-region issues.",
            "show_language_reverted_push_notification": "Recovered from `shouldShowLanguageRevertedPushNotification`. It indicates that the app intended to push an alert about a language reversion event, which may support a finding that the user was meant to be informed of a configuration change.",
            "source_path": "Path to the source plist where the record was parsed. It is important because it lets the examiner verify the exact artifact, confirm parsing accuracy, and preserve traceability for reporting or testimony.",
        },
        "the iOS Stella case settings plist",
    )
    apple_sync_rows = _dataclass_field_rows(
        StellaDeviceSyncRecord,
        {
            "glasses_device_id": "Device identifier inferred from the Stella sync artifact path. It shows which paired glasses the sync history belongs to and is useful when separating activity from multiple devices.",
            "glasses_firmware_version": "Recovered from `lastSyncFirmware`. It identifies the firmware level on the glasses, which helps explain hardware capability, app compatibility, and differences in expected artifact generation.",
            "app_version_at_last_sync": "Recovered from `lastSyncAppVersion`. It shows which companion-app version performed the sync and is important because app-version differences can affect storage format, prompts, and supported features.",
            "last_sync_time": "Recovered from `lastSyncTime` and normalized to a readable timestamp. It provides a last-known interaction point between the phone and glasses and helps determine recency of use.",
            "source_path": "Path to the source plist where the record was parsed. It preserves artifact traceability so the examiner can validate the sync evidence directly.",
        },
        "the iOS Stella sync plist",
    )
    apple_sku_rows = _dataclass_field_rows(
        StellaDerivedSkuRecord,
        {
            "glasses_serial_number": "Device identifier or serial inferred from the Stella derived SKU artifact path. It helps associate model and cosmetic details with a particular hardware unit and can support device-level attribution.",
            "model": "Recovered from `frameTypeDisplayName`. It gives the most readable model name and is important because it helps the examiner understand what hardware family and feature set the parsed records likely relate to.",
            "model_short_name": "Recovered from `frameTypeShortDisplayName`. It provides a shorter label used for matching, display, and family grouping when multiple related models share a common platform.",
            "frame_style": "Recovered from `frameStyle`. It describes the physical style family of the glasses and can help distinguish devices that may share internals but differ in outward appearance.",
            "frame_color_display_name": "Recovered from `frameColorDisplayName`. It provides the user-facing frame color and can help corroborate witness descriptions, photographs, or seized-device observations.",
            "frame_color": "Recovered from `frameColor`. It is a normalized internal color value that supports more consistent searching, filtering, and cross-record comparison than free-text labels alone.",
            "lens_color_display_name": "Recovered from `lensColorDisplayName`. It gives the readable lens color, which may help distinguish visually similar units and support physical-device comparison.",
            "lens_color": "Recovered from `lensColor`. It is the normalized internal lens-color value used for structured comparison across exports and related records.",
            "source_path": "Path to the source plist where the record was parsed. It is important because it allows the model and appearance data to be traced back to the exact artifact.",
        },
        "the iOS Stella derived SKU plist",
    )
    android_profile_rows = _dataclass_field_rows(
        AndroidMetaAppProfileRecord,
        {
            "fetched": "Recovered from `user_profile.fetch_timestamp_ms` and normalized to a readable timestamp. It shows when the profile information was last refreshed by the app and helps gauge how current the stored account details are.",
            "user_id": "Recovered from `user_profile.user_id`. It is a core account identifier that can be used to correlate the same user across multiple records, tables, or extractions even when names change.",
            "user_name": "Recovered from `user_profile.user_name`. It may show the primary account-holder name or display identity associated with the Meta environment and helps humanize the account attribution.",
            "short_name": "Recovered from `user_profile.short_name`. It provides a shorter identity label often seen in the app interface and can help tie UI-level references back to a full account record.",
            "social_username": "Recovered from `user_profile.social_profile_user_name`. It may connect the Meta glasses environment to a social-media identity and can be important when linking device use to an online presence.",
            "social_display_name": "Recovered from `user_profile.social_profile_display_name`. It provides the human-readable display name for reporting and helps interpret the account when usernames alone are unclear.",
            "social_profile_id": "Recovered from `user_profile.social_profile_id`. It gives a more precise social-account identifier and is important when multiple users share similar names or aliases.",
            "imported_data_source": "Recovered from `user_profile.imported_data_source`. It indicates how the profile data entered the app environment, which can help explain whether information was locally entered, synced, or imported from another Meta service.",
            "constellation_group_id": "Recovered from `user_profile.constellation_group_id`. It may represent a broader backend grouping and can help analysts understand whether multiple records belong to the same account ecosystem.",
            "abra_id": "Recovered from `user_profile.abra_id`. It is an internal service identifier that may assist with cross-record correlation when the same user appears under different visible labels.",
            "abra_messaging_user_id": "Recovered from `user_profile.abra_messaging_user_id`. It may tie the account to messaging-related services and can be useful when exploring whether communications features were linked to the glasses account.",
            "eligibility_subscription": "JSON bundle of Android profile eligibility and subscription-related fields from the `user_profile` table. It can show what features, experiments, or service tiers the account was entitled to, which helps explain why certain capabilities were present.",
            "source_path": "Path to the Android Stella database source. It preserves traceability to the exact artifact so the account evidence can be independently reviewed.",
        },
        "the Android Stella database `user_profile` table",
    )
    android_device_rows = _dataclass_field_rows(
        AndroidMetaDeviceRecord,
        {
            "device_codename": "Recovered from `capture.device_codename`. It can identify the underlying hardware family or internal model class and helps distinguish between device generations or product lines.",
            "source": "Recovered from `capture.source`. It indicates where in the app workflow the record originated and can help explain whether the device entry came from capture, sync, import, or another process.",
            "pairing_id": "Recovered from `capture.pairing_id`. It identifies the pairing relationship between the phone and glasses, which is important when tracking one handset paired with different devices over time.",
            "device_id": "Recovered from `capture.device_id`. It is a key hardware identifier used to correlate multiple records belonging to the same paired glasses.",
            "serial": "Recovered from `capture.device_serial`. It can connect the Android app evidence to a specific physical unit and is often valuable for inventory, seizure, or attribution work.",
            "capture_type": "Recovered from `capture.type`. It describes the kind of record or event represented, which helps the examiner understand whether the entry refers to a device artifact, media event, or another action.",
            "example_capture_id": "Recovered from `capture.capture_id` as a representative linked capture. It provides an example event or media identifier tied to the device and can be used as a pivot into related records.",
            "attributes": "Recovered from `capture.attributes_json`. It may contain additional structured details about the device or event and is important because it can surface context not exposed in the normalized columns.",
            "source_path": "Path to the Android Stella database source. It supports traceability and lets the examiner validate the record in the original database.",
        },
        "the Android Stella database `capture` table",
    )
    android_sync_rows = _dataclass_field_rows(
        AndroidMetaSyncRecord,
        {
            "capture_time": "Recovered from `capture.capture_timestamp_ms` and normalized to a readable timestamp. It shows when the underlying event or media capture occurred and is central to timeline reconstruction.",
            "fetch_completed": "Recovered from `media_item.fetch_completed_timestamp_ms`. It indicates when the app finished retrieving the media, which helps distinguish original capture time from later synchronization time.",
            "import_completed": "Recovered from `media_item.import_completed_timestamp_ms`. It shows when the item became fully ingested into the app environment and can help explain delays between capture and availability.",
            "auto_saved": "Recovered from `media_item.auto_saved_timestamp_ms`. It may indicate that the media was automatically persisted by workflow logic rather than by a deliberate manual action.",
            "session_id": "Recovered from `media_item.session_id`. It can group related sync or import events together and is useful when reconstructing a single transfer session.",
            "capture_id": "Recovered from the linked capture/media record key. It is a primary pivot value for correlating sync state, media details, and associated device activity.",
            "media_type": "Recovered from `media_item.type`. It identifies the kind of content involved, such as photo or video, which helps prioritize review and interpret downstream processing.",
            "import_trigger": "Recovered from `media_item.import_trigger`. It explains why an import happened, such as an automatic rule or user action, and can be important when distinguishing intentional behavior from background sync.",
            "processing_state": "Recovered from `media_item.processing_state`. It shows whether the item was pending, completed, failed, or incomplete, which helps determine whether expected media should exist or may have been interrupted.",
            "thumbnail_state": "Recovered from `media_item.thumbnail_state`. It indicates progress in generating preview assets and can help show whether the app only partially processed the media.",
            "full_media_state": "Recovered from `media_item.full_media_state`. It reflects whether the complete media payload was available, which matters when deciding if the absence of a file is meaningful or merely incomplete sync.",
            "shared_media_global_id": "Recovered from `media_item.shared_media_global_id`. It may connect the content to a broader cloud or multi-device identifier and can support cross-system correlation.",
            "wifi_scan_data": "Recovered from `media_item.wifi_scan_data`. It may provide surrounding network context that helps explain when, where, or under what connectivity conditions syncing occurred.",
            "source_path": "Path to the Android Stella database source. It preserves traceability so the sync evidence can be validated in the original artifact.",
        },
        "the Android Stella database `capture` and `media_item` tables",
    )
    prompt_rows = _dataclass_field_rows(
        PromptRecord,
        {
            "prompt_timestamp": "Recovered prompt timestamp from iOS Meta AI text logs or Android interaction logs when available. It places the user request on a timeline and is important for sequence-of-events analysis.",
            "response_timestamp": "Recovered response timestamp from iOS Meta AI text logs when available. It helps show when the system answered and can be compared to the prompt time to understand response order or latency.",
            "prompt_text": "Recovered user prompt text from iOS text-log parsing, Android interaction logs, or Android GraphQL cache. It shows what the user asked the AI system and can be highly significant for intent, topic, or task reconstruction.",
            "response_text": "Recovered assistant response text from iOS text-log parsing when available. It shows what information or guidance the system returned and can help explain subsequent user actions or decisions.",
            "prompt_line_number": "Source line number for the prompt in iOS text logs when available. It helps the examiner quickly validate the extracted text in the original artifact and review surrounding context.",
            "response_line_number": "Source line number for the response in iOS text logs when available. It supports verification of the recovered response and makes it easier to inspect neighboring lines for additional context.",
            "source_path": "Path to the source log or cache file where the prompt was detected. It is important for traceability, independent verification, and evidentiary support.",
        },
        "prompt-related artifacts",
    )
    media_rows = _dataclass_field_rows(
        MediaRecord,
        {
            "media_path": "Resolved path to a related media hit or an embedded asset reconstructed from Photos.sqlite metadata. It identifies the exact file the parser believes is relevant and gives the examiner a direct review target.",
            "extension": "File extension for the media hit. It quickly indicates whether the artifact is likely an image, video, audio file, or another media type, which helps triage review.",
            "score": "Internal ranking value used only to sort likely media hits before display. It is not intended to be examiner-facing evidence on its own.",
            "reasons": "Internal match tokens captured by the parser. Examiner-facing outputs convert these tokens into a readable explanation of why the media was flagged.",
            "exif_make": "Recovered make value from EXIF or embedded metadata. It can identify the manufacturer context of the media and helps assess whether the file is likely tied to Meta glasses or another source.",
            "exif_model": "Recovered model value from EXIF or embedded metadata. It is often one of the strongest indicators of which device family created the media and can directly support model attribution.",
            "exif_software": "Recovered software value from EXIF or embedded metadata. It may show the app, firmware, or processing environment involved with the file, which helps explain post-capture handling.",
            "exif_datetime": "Recovered EXIF date/time value. It provides a capture or processing time reference and is important for timeline work, though it should be compared with other timestamps and artifact context.",
            "latitude": "Recovered latitude from embedded metadata when available. It can contribute location context for where the media was captured or later processed.",
            "longitude": "Recovered longitude from embedded metadata when available. Together with latitude, it can help place the media geographically and support movement or scene reconstruction.",
            "report_copy_path": "Path to the exported media copy inside the report output. It gives the examiner a stable preserved copy to review without altering the original evidence location.",
            "report_preview_path": "Path to the generated preview image when available. It supports quick visual triage inside the report and helps the examiner review many assets efficiently.",
        },
        "media detection",
    )
    identifier_rows = _dataclass_field_rows(
        IdentifierRecord,
        {
            "artifact_type": "Normalized source type such as `plist`, `embedded_plist`, `json`, `text`, or `sqlite`. It tells the examiner what kind of artifact produced the hit and helps judge how the value should be interpreted.",
            "key": "Structured field key, table.column reference, or synthetic key like `line`, `regex_match`, or `table`. It shows exactly where in the source structure the identifier was found, which is important for interpretation and validation.",
            "value": "Recovered value that matched the glasses-oriented identifier heuristics. It is the substantive content that may indicate device, account, model, or Meta-related relevance.",
            "source_path": "Path to the source file where the identifier was found. It provides the trace-back point needed for verification and deeper artifact review.",
            "context": "Detection context such as `structured_data`, `keyword_hit`, `table_name_hit`, or `row_value_hit`. It explains how the parser encountered the value and helps the examiner weigh the evidentiary strength of the hit.",
        },
        "generic structured-data parsing",
    )
    account_rows = _dataclass_field_rows(
        AccountRecord,
        {
            "field": "Normalized account-related field name. It tells the examiner what category of account information was found, such as username, email, handle, or linked platform value.",
            "value": "Recovered account-related value, such as user, email, handle, or platform-linked data. This is important because it can connect the device environment to a person, profile, or service account.",
            "source_path": "Path to the source artifact containing the account record. It allows the account linkage to be reviewed directly in the underlying evidence.",
            "context": "Context showing where the account data came from, such as `stella_plist` or an embedded plist key. It helps explain how direct or indirect the recovered account linkage is.",
        },
        "linked-account parsing",
    )
    return [
        (
            "Apple / iOS Parser Coverage",
            [
                ("Scope", "This section covers the Apple/iOS-specific artifacts, parsed fields, and summary detections."),
            ],
        ),
        (
            "Case Metadata Input Fields",
            case_metadata_rows,
        ),
        (
            "Apple / iOS Stella Case Settings Fields",
            apple_case_rows,
        ),
        (
            "Apple / iOS Stella Device Sync Fields",
            apple_sync_rows,
        ),
        (
            "Apple / iOS Stella Derived SKU Fields",
            apple_sku_rows,
        ),
        (
            "Apple / iOS Device-Summary Detections",
            [
                ("CaseMetadata.owner", "Adds a Phone Identifiers entry named `Owner` from examiner-supplied metadata."),
                ("CaseMetadata.imei", "Adds a Phone Identifiers entry named `IMEI` from examiner-supplied metadata."),
                ("CaseMetadata.serial_number", "Adds a Phone Identifiers entry named `Serial Number` from examiner-supplied metadata."),
                ("com.apple.commcenter.device_specific_nobackup.plist", "Detects Phone Identifiers values for `IMEIs` and `Reported Phone Number`."),
                ("com.apple.mobilebluetooth.devices.plist", "Detects Bluetooth device name, MAC address, optional product ID, and last seen time."),
                ("com.apple.mobilebluetooth.ledevices.paired.db", "Detects paired Bluetooth LE device name/identifier and last connection time."),
                ("com.apple.mobilebluetooth.ledevices.other.db", "Detects observed Bluetooth LE device name/identifier and last seen time."),
                ("com.apple.wifi.plist", "Detects Wi-Fi SSID/BSSID plus optional manufacturer, model, and last joined data."),
                ("com.apple.wifi.known-networks.plist", "Detects Wi-Fi SSID/BSSID plus optional manufacturer, model, and last joined data."),
                ("com.apple.wifi-networks.plist.backup", "Detects Wi-Fi SSID/BSSID plus optional manufacturer, model, and last joined data."),
                ("wifinetworkstoremodel.sqlite", "Detects Wi-Fi SSID/BSSID and last connected time from the WiFi network store model."),
                ("consolidated.db", "Detects phone Serial Number values from the `TableInfo.SerialNumber` field."),
                ("Photos-related TSV/CSV exports", "Detects Meta glasses make/model from embedded asset metadata and surfaces Media / Companion entries."),
                ("Photos.sqlite embedded metadata", "Detects embedded media make, model, software, EXIF date/time, latitude, and longitude for Meta glasses assets."),
            ],
        ),
        (
            "Android Parser Coverage",
            [
                ("Scope", "This section covers the Android-specific artifacts, parsed fields, and summary detections."),
            ],
        ),
        (
            "Android App Profile Fields",
            android_profile_rows,
        ),
        (
            "Android Device Fields",
            android_device_rows,
        ),
        (
            "Android Sync Fields",
            android_sync_rows,
        ),
        (
            "Android Device-Summary Detections",
            [
                ("settings_secure.xml", "Detects `Android ID`, `Bluetooth Name`, and `Bluetooth Address` into the device summary."),
                ("build.prop", "Detects Android phone manufacturer, brand, model, device, Android version, and SDK into a Phone Identifiers entry named `Android Device`."),
                ("bt_config.conf", "Detects Bluetooth device name, MAC address, last connected time, and link key."),
                ("wificonfigstore.xml", "Detects Wi-Fi SSID/BSSID plus randomized MAC, gateway MAC, last connected time, and ever-connected state."),
                ("carservicedata.db", "Detects Android Auto Bluetooth vehicles and Wi-Fi vehicle networks, including car name, model year, and connection time."),
                ("Android Stella database capture/media tables", "Detects device, pairing, serial, capture, and sync fields listed in the Android structured sections above."),
                ("interaction_log.db", "Detects Android prompt/transcription text from `entries` rows where `event` is `Transcription`."),
                ("GraphQL cache companion-ar files", "Detects Android prompt text from GraphQL cache turn content and snippets."),
            ],
        ),
        (
            "Shared / Cross-Platform Coverage",
            [
                ("Scope", "This section covers shared detections such as prompts, media, identifiers, accounts, and heuristic matching logic."),
            ],
        ),
        (
            "Prompt Detection Fields",
            prompt_rows,
        ),
        (
            "Media Detection Fields",
            media_rows,
        ),
        (
            "Generic Identifier Fields",
            identifier_rows,
        ),
        (
            "Linked Account Fields",
            account_rows,
        ),
        (
            "Generic Structured-Data and Heuristic Detections",
            [
                ("PATH_KEYWORDS", "Looks for Meta/Ray-Ban/Stella/wearable path and value keywords: meta, meta ai, rayban, ray-ban, smart glasses, stella, wearable."),
                ("IDENTIFIER_KEYWORDS", "Flags keys containing: account, appversion, case, device, firmware, frame, glasses, instagram, lens, model, serial, sku, sync, username, version."),
                ("KNOWN_FILE_HINTS", "Treats files with names or paths containing `com.facebook.stellaapp`, `com.meta.mwa`, `glasses`, `metaai-log-`, `rayban`, `ray-ban`, or `stella` as especially relevant."),
                ("GLASSES_IDENTIFIER_KEYWORDS", "Accepts keys tied to glasses concepts: appversion, firmware, frame, glasses, lens, model, rayban, ray-ban, serial, sku, stella, wearable."),
                ("GLASSES_VALUE_HINTS", "Accepts values mentioning glasses-related concepts: firmware, frame, glasses, meta, rayban, ray-ban, smart glasses, stella, wearable."),
                ("PHONE_EXCLUSION_KEYWORDS", "Suppresses phone-centric hits containing android, baseband, cellular, ICC, IMEI, IMSI, iOS, iPad, iPhone, MEID, mobile, phone, SIM, subscriber, or telephony."),
                ("GENERIC_ID_RE", "Matches long alphanumeric tokens, long hex strings, numeric identifiers, and email-like values as possible identifiers."),
                ("scan_flattened_data", "Flattens plist, JSON, and embedded plist structures and records any keys/values that satisfy the glasses heuristics."),
                ("scan_text", "Parses text/log/strings files for prompt patterns, response patterns, keyword-hit lines, and regex identifier matches."),
                ("scan_sqlite", "Records SQLite table names containing relevant keywords and scans up to 25 rows for matching table.column values."),
                ("extract_account_records", "Recovers account-like fields/values while excluding auth, password, token, secret, and refresh-related keys."),
            ],
        ),
        (
            "Prompt Pattern and Source Detections",
            [
                ("iOS text prompt pattern", "Detects prompts matching `SilverstoneModels.SLVPostTitle(text: Optional(\"...\"), mediaItems:`."),
                ("iOS text response pattern", "Detects responses matching `Last Response agent option:`."),
                ("Android interaction prompt source", "Detects `entries.timestamp`, `entries.interaction`, `entries.event`, and `entries.event_data` rows where event is `Transcription`."),
                ("Android GraphQL cache prompt source", "Detects USER-role turns or prompt-like snippets from GraphQL cache files in `graphql_response_cache/companion-ar/`."),
                ("Prompt de-duplication", "Deduplicates prompt hits by normalized timestamp, prompt text, and source path."),
            ],
        ),
        (
            "Media Detection and Scoring Sources",
            [
                ("EXIF/media scoring", "Scores direct media files using EXIF make/model/software and Meta-glasses heuristics."),
                ("Embedded Photos.sqlite metadata", "Extracts TIFF, Exif, GPS, and text-derived make/model values from Photos.sqlite metadata blobs."),
                ("Report export helpers", "Exports media copies and preview images into the report folder when media hits are present."),
            ],
        ),
    ]


def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _pdf_wrap_line(text: str, width: int = 88) -> list[str]:
    stripped = text.strip()
    if not stripped:
        return [""]
    return textwrap.wrap(stripped, width=width, break_long_words=False, break_on_hyphens=False) or [""]


def _pdf_field_lines(field_name: str, description: str) -> list[tuple[str, str]]:
    lines: list[tuple[str, str]] = [(field_name, "bold")]
    wrapped_description = _pdf_wrap_line(description, width=82)
    lines.extend((f"  {line}", "regular") for line in wrapped_description)
    return lines


def write_field_reference_pdf(path: Path):
    intro_lines = [
        "This reference lists the current fields, artifact-specific values, and heuristic detections",
        "that the SpecTacular parser is built to surface from Apple/iOS, Android, and shared sources.",
        "Definitions are intended as quick examiner guidance and should be validated against source artifacts.",
    ]
    page_width = 612
    page_height = 792
    margin_x = 40
    top_y = 752
    bottom_margin = 42
    section_width = page_width - (margin_x * 2)
    title_line_height = 22
    body_line_height = 13
    section_top_padding = 18
    section_bottom_padding = 16
    section_gap = 16
    intro_gap = 18
    blue_text = "0.06 0.33 0.63"
    blue_border = "0.07 0.44 0.76"
    blue_fill = "0.95 0.98 1.00"
    body_text = "0.10 0.22 0.35"

    section_blocks: list[tuple[str, list[tuple[str, str]], float]] = []
    for section_title, fields in _field_reference_sections():
        body_lines: list[tuple[str, str]] = []
        for field_name, description in fields:
            body_lines.extend(_pdf_field_lines(field_name, description))
        block_height = (
            section_top_padding
            + title_line_height
            + (len(body_lines) * body_line_height)
            + section_bottom_padding
        )
        section_blocks.append((section_title, body_lines, block_height))

    pages_layout: list[list[tuple[str, list[tuple[str, str]], float]]] = []
    current_page: list[tuple[str, list[tuple[str, str]], float]] = []
    used_height = 0.0
    first_page_available = top_y - bottom_margin - 78 - intro_gap - (len(intro_lines) * 14)
    other_pages_available = top_y - bottom_margin

    for block in section_blocks:
        block_height = block[2]
        available_height = first_page_available if not pages_layout else other_pages_available
        if current_page and used_height + section_gap + block_height > available_height:
            pages_layout.append(current_page)
            current_page = []
            used_height = 0.0
            available_height = other_pages_available
        if current_page:
            used_height += section_gap
        current_page.append(block)
        used_height += block_height
    if current_page:
        pages_layout.append(current_page)

    objects: list[bytes] = []
    page_object_numbers: list[int] = []

    def add_object(data: bytes) -> int:
        objects.append(data)
        return len(objects)

    catalog_obj = add_object(b"<< /Type /Catalog /Pages 2 0 R >>")
    pages_obj = add_object(b"<< /Type /Pages /Kids [] /Count 0 >>")
    font_regular_obj = add_object(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    font_bold_obj = add_object(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")

    for page_index, page_sections in enumerate(pages_layout):
        content_lines: list[bytes] = []
        current_y = top_y

        if page_index == 0:
            content_lines.extend(
                [
                    b"BT",
                    f"{blue_text} rg".encode("ascii"),
                    b"/F2 20 Tf",
                    f"{margin_x} {current_y} Td".encode("ascii"),
                    f"({_pdf_escape('SpecTacular Parsed Field Definitions')}) Tj".encode("latin-1", errors="replace"),
                    b"ET",
                ]
            )
            current_y -= 30
            content_lines.extend(
                [
                    b"BT",
                    f"{body_text} rg".encode("ascii"),
                    b"/F1 11 Tf",
                    f"{margin_x} {current_y} Td".encode("ascii"),
                    b"14 TL",
                ]
            )
            first_intro = True
            for line in intro_lines:
                if not first_intro:
                    content_lines.append(b"T*")
                content_lines.append(f"({_pdf_escape(line)}) Tj".encode("latin-1", errors="replace"))
                first_intro = False
            content_lines.append(b"ET")
            current_y -= 48

        for section_title, body_lines, block_height in page_sections:
            rect_y = current_y - block_height
            content_lines.extend(
                [
                    b"q",
                    f"{blue_fill} rg".encode("ascii"),
                    f"{blue_border} RG".encode("ascii"),
                    b"1.2 w",
                    f"{margin_x} {rect_y:.2f} {section_width} {block_height:.2f} re B".encode("ascii"),
                    b"Q",
                ]
            )

            title_y = current_y - section_top_padding
            content_lines.extend(
                [
                    b"BT",
                    f"{blue_text} rg".encode("ascii"),
                    b"/F2 13 Tf",
                    f"{margin_x + 14} {title_y:.2f} Td".encode("ascii"),
                    f"({_pdf_escape(section_title)}) Tj".encode("latin-1", errors="replace"),
                    b"ET",
                ]
            )

            body_start_y = title_y - 20
            content_lines.extend(
                [
                    b"BT",
                    f"{body_text} rg".encode("ascii"),
                    b"/F1 10 Tf",
                    f"{margin_x + 14} {body_start_y:.2f} Td".encode("ascii"),
                    f"{body_line_height} TL".encode("ascii"),
                ]
            )
            first_body = True
            for line, font_style in body_lines:
                if not first_body:
                    content_lines.append(b"T*")
                font_token = b"/F2 10 Tf" if font_style == "bold" else b"/F1 10 Tf"
                content_lines.append(font_token)
                content_lines.append(f"({_pdf_escape(line)}) Tj".encode("latin-1", errors="replace"))
                first_body = False
            content_lines.append(b"ET")
            current_y = rect_y - section_gap

        stream_data = b"\n".join(content_lines) + b"\n"
        content_obj = add_object(
            b"<< /Length " + str(len(stream_data)).encode("ascii") + b" >>\nstream\n" + stream_data + b"endstream"
        )
        page_obj = add_object(
            (
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 {font_regular_obj} 0 R /F2 {font_bold_obj} 0 R >> >> "
                f"/Contents {content_obj} 0 R >>"
            ).encode("ascii")
        )
        page_object_numbers.append(page_obj)

    kids = " ".join(f"{page_obj} 0 R" for page_obj in page_object_numbers)
    objects[pages_obj - 1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_object_numbers)} >>".encode("ascii")

    pdf_parts = [b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"]
    offsets = [0]
    current_offset = len(pdf_parts[0])

    for index, obj in enumerate(objects, start=1):
        offsets.append(current_offset)
        obj_block = f"{index} 0 obj\n".encode("ascii") + obj + b"\nendobj\n"
        pdf_parts.append(obj_block)
        current_offset += len(obj_block)

    xref_offset = current_offset
    xref_lines = [f"xref\n0 {len(objects) + 1}\n".encode("ascii"), b"0000000000 65535 f \n"]
    for offset in offsets[1:]:
        xref_lines.append(f"{offset:010d} 00000 n \n".encode("ascii"))
    trailer = (
        f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_obj} 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n"
    ).encode("ascii")

    with open(windows_safe_path(path), "wb") as handle:
        for part in pdf_parts:
            handle.write(part)
        for line in xref_lines:
            handle.write(line)
        handle.write(trailer)


def build_table(headers: list[str], rows: list[list[str]]) -> str:
    header_html = "".join(f"<th>{html.escape(header)}</th>" for header in headers)
    body_parts = []
    for row in rows:
        cells = "".join(f"<td>{html.escape(str(value))}</td>" for value in row)
        body_parts.append(f"<tr>{cells}</tr>")
    body_html = "".join(body_parts) if body_parts else f"<tr><td colspan='{len(headers)}'>No data</td></tr>"
    return f"<div class='table-wrap'><table><thead><tr>{header_html}</tr></thead><tbody>{body_html}</tbody></table></div>"


def _image_to_data_uri(path: Path) -> str:
    try:
        suffix = path.suffix.lower()
        mime = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
            ".svg": "image/svg+xml",
        }.get(suffix)
        if not mime or not path.exists():
            return ""
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{encoded}"
    except Exception:
        return ""


def detect_glasses_model(
    derived_sku_records: list[StellaDerivedSkuRecord],
    media_records: list[MediaRecord],
    android_device_records: list[AndroidMetaDeviceRecord] | None = None,
) -> dict[str, Any] | None:
    candidates: list[tuple[str, str]] = []
    for record in derived_sku_records:
        for value, source in (
            (record.model, "Derived SKU model"),
            (record.model_short_name, "Derived SKU short name"),
            (record.frame_style, "Derived SKU frame style"),
        ):
            text = str(value or "").strip()
            if text:
                candidates.append((text, source))
    for record in media_records[:50]:
        for value, source in (
            (record.exif_model, "Media EXIF model"),
            (record.exif_make, "Media EXIF make"),
            (record.exif_software, "Media EXIF software"),
        ):
            text = str(value or "").strip()
            if text:
                candidates.append((text, source))
    for record in (android_device_records or [])[:100]:
        for value, source in (
            (record.device_codename, "Android device codename"),
            (record.source, "Android device source"),
            (record.capture_type, "Android capture type"),
            (record.attributes, "Android device attributes"),
            (record.serial, "Android device serial"),
        ):
            text = str(value or "").strip()
            if text:
                candidates.append((text, source))

    normalized_candidates = [(text, normalize_model_text(text), source) for text, source in candidates]
    for reference in MODEL_REFERENCES:
        normalized_aliases = tuple(normalize_model_text(alias) for alias in reference.aliases)
        for text, normalized, source in normalized_candidates:
            if any(alias and alias in normalized for alias in normalized_aliases):
                image_uri = _image_to_data_uri(get_model_assets_dir() / reference.image_filename)
                return {
                    "name": reference.canonical_name,
                    "description": reference.description,
                    "capabilities": list(reference.capabilities),
                    "image_uri": image_uri,
                    "image_filename": reference.image_filename,
                    "source": source,
                    "matched_value": text,
                }

    if derived_sku_records:
        record = derived_sku_records[0]
        fallback_name = record.model_short_name or record.model or record.frame_style
        if fallback_name:
            return {
                "name": str(fallback_name),
                "description": "A likely Meta glasses model was detected from parsed application records.",
                "capabilities": [],
                "image_uri": "",
                "image_filename": "",
                "source": "Derived SKU data",
                "matched_value": str(fallback_name),
            }
    return None


def build_detected_model_card(model_info: dict[str, Any] | None) -> str:
    if not model_info:
        return (
            "<div class='hero-model-card'>"
            "<div class='hero-model-label'>Detected Glasses Model</div>"
            "<div class='hero-model-stack'>"
            "<div class='hero-model-evidence'>No matched model image was identified from the local model assets.</div>"
            "</div>"
            "</div>"
        )

    image_uri = model_info.get("image_uri", "")
    image_html = ""
    if image_uri:
        image_html = (
            f"<img class='hero-model-image' src='{html.escape(image_uri, quote=True)}' "
            f"alt='{html.escape(model_info.get('name', 'Detected glasses model'), quote=True)}'>"
        )
    source_text = model_info.get("source", "")
    matched_value = model_info.get("matched_value", "")
    evidence_html = ""
    if source_text or matched_value:
        evidence_bits = [bit for bit in (f"Source: {source_text}" if source_text else "", f"Matched value: {matched_value}" if matched_value else "") if bit]
        evidence_html = f"<div class='hero-model-evidence'>{html.escape(' | '.join(evidence_bits))}</div>"
    return (
        "<div class='hero-model-card'>"
        "<div class='hero-model-label'>Detected Glasses Model</div>"
        "<div class='hero-model-stack'>"
        f"{image_html}"
        f"{evidence_html}"
        "</div></div>"
    )


def describe_report_target(summary: dict[str, Any]) -> str:
    android_hits = sum(
        int(summary.get(key, 0) or 0)
        for key in ("android_profile_count", "android_device_count", "android_sync_count")
    )
    apple_hits = sum(
        int(summary.get(key, 0) or 0)
        for key in ("case_settings_count", "device_sync_count", "derived_sku_count")
    )

    if android_hits > apple_hits:
        return "Android device report generated from the parsed scan results."
    return "Apple device report generated from the parsed scan results."


def build_record_selector(
    section_id: str,
    headers: list[str],
    rows: list[list[str]],
    title_column: int | None = None,
    record_labels: list[str] | None = None,
) -> str:
    if not rows:
        return "<p>No records were available for this section.</p>"

    safe_id = sanitize_windows_component(section_id, 60).replace("_", "-")
    options: list[str] = []
    panels: list[str] = []

    for index, row in enumerate(rows):
        record_label = (
            str(record_labels[index]).strip()
            if record_labels and index < len(record_labels) and str(record_labels[index]).strip()
            else f"Record {index + 1}"
        )
        preview = ""
        if title_column is not None and 0 <= title_column < len(row):
            preview = str(row[title_column]).strip()
        if not preview:
            preview_parts = [str(value).strip() for value in row[:3] if str(value).strip()]
            preview = " | ".join(preview_parts[:2]) or f"Record {index + 1}"
        if len(preview) > 90:
            preview = preview[:87] + "..."
        options.append(
            f"<option value='{html.escape(str(index))}'>{html.escape(record_label)}: {html.escape(preview)}</option>"
        )
        field_rows = [[header, row[col_index] if col_index < len(row) else ""] for col_index, header in enumerate(headers)]
        panels.append(
            f"<div class='record-dropdown-panel{' active' if index == 0 else ''}' "
            f"data-record-dropdown-panel='{html.escape(safe_id)}' "
            f"data-record-dropdown-value='{html.escape(str(index))}' "
            f"data-print-label='{html.escape(f'{record_label}: {preview}')}' >"
            f"{build_table(['Field', 'Value'], field_rows)}"
            "</div>"
        )

    return (
        "<div class='record-selector-wrap'>"
        f"<label class='summary-dropdown-label' for='record-dropdown-{html.escape(safe_id)}'>Select record</label>"
        f"<select class='summary-dropdown-select' id='record-dropdown-{html.escape(safe_id)}' data-record-dropdown='{html.escape(safe_id)}'>"
        f"{''.join(options)}"
        "</select>"
        f"{''.join(panels)}"
        "</div>"
    )


def _parse_report_timestamp(value: str) -> tuple[int, datetime]:
    text = str(value or "").strip()
    if not text:
        return (0, datetime.min)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return (1, datetime.strptime(text, fmt))
        except ValueError:
            continue
    return (0, datetime.min)


def to_file_uri(path_text: str) -> str:
    try:
        return Path(path_text).resolve().as_uri()
    except Exception:
        return ""


def to_folder_uri(path_text: str) -> str:
    try:
        path = Path(path_text).resolve()
        folder = path if path.is_dir() else path.parent
        return folder.as_uri()
    except Exception:
        return ""


def to_report_href(target_path: str, report_path: Path) -> str:
    try:
        relative_path = os.path.relpath(Path(target_path), report_path.parent).replace("\\", "/")
        return html.escape(quote(relative_path, safe="/:._-"), quote=True)
    except Exception:
        return html.escape(to_file_uri(target_path), quote=True)


def build_media_viewer(record: MediaRecord, report_path: Path) -> str:
    image_exts = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
    video_exts = {".mp4", ".mov", ".3gp"}
    audio_exts = {".aac", ".m4a", ".mp3", ".wav"}
    media_source = record.report_copy_path or record.media_path
    preview_source = record.report_preview_path or media_source
    media_href = html.escape(to_file_uri(media_source), quote=True) or to_report_href(media_source, report_path)
    preview_href = html.escape(to_file_uri(preview_source), quote=True) or to_report_href(preview_source, report_path)
    preview_file_uri = html.escape(to_file_uri(preview_source), quote=True)
    filename = Path(media_source).name
    title = html.escape(filename, quote=True)
    extension = record.extension.lower()

    if extension in video_exts:
        return (
            '<video controls preload="metadata" width="320" height="240">'
            f'<source src="{media_href}">'
            "Your browser cannot preview this video."
            "</video>"
        )
    if extension in image_exts:
        return (
            f'<a href="{media_href}" target="_blank" rel="noopener noreferrer">'
            f'<img src="{preview_href}" alt="{title}" title="{title}" loading="lazy" '
            f'onerror="if(this.dataset.fallbackTried){{return;}} this.dataset.fallbackTried=\'1\'; this.src=\'{preview_file_uri}\';">'
            "</a>"
        )
    if extension in audio_exts:
        return (
            '<audio controls preload="metadata">'
            f'<source src="{media_href}">'
            "Your browser cannot preview this audio."
            "</audio>"
        )
    return (
        f'<a href="{media_href}" target="_blank" rel="noopener noreferrer">'
        f"Open {html.escape(filename)}"
        "</a>"
    )


def build_media_gallery(media_records: list[MediaRecord], report_path: Path, limit: int = 24) -> str:
    cards: list[str] = []

    for item in media_records[:limit]:
        preview_html = build_media_viewer(item, report_path)
        cards.append(
            "<div class='media-card'>"
            f"<div class='media-preview'>{preview_html}</div>"
            f"<div class='media-meta'><div class='media-name'>{html.escape(Path(item.media_path).name)}</div>"
            f"<div class='media-detail'>{html.escape(item.extension)}</div>"
            f"<div class='media-detail'>{html.escape(explain_media_hit(item.reasons, make=item.exif_make, model=item.exif_model, software=item.exif_software, exif_datetime=item.exif_datetime))}</div></div>"
            "</div>"
        )

    if not cards:
        return "<p>No media previews were available for the media hits.</p>"
    return f"<div class='media-gallery'>{''.join(cards)}</div>"


def build_media_selector(media_records: list[MediaRecord], report_path: Path, limit: int = 250) -> str:
    selector_items: list[dict[str, str]] = []

    for item in media_records[:limit]:
        viewer_html = build_media_viewer(item, report_path)
        filename = Path(item.media_path).name
        exported_media_path = item.report_copy_path or item.media_path
        selector_items.append(
            {
                "label": f"{filename} | {item.extension}",
                "viewer_html": viewer_html,
                "name": filename,
                "details": explain_media_hit(item.reasons, make=item.exif_make, model=item.exif_model, software=item.exif_software, exif_datetime=item.exif_datetime),
                "path": exported_media_path,
                "file_href": html.escape(to_file_uri(exported_media_path), quote=True) or to_report_href(exported_media_path, report_path),
                "folder_href": html.escape(to_folder_uri(exported_media_path), quote=True),
            }
        )

    if not selector_items:
        return "<p>No media hits were available to preview.</p>"

    options_html = "".join(
        f"<option value='{index}'>{html.escape(item['label'])}</option>"
        for index, item in enumerate(selector_items)
    )
    payload_json = html.escape(json.dumps(selector_items), quote=True)

    return (
        "<div class='media-selector' data-media-selector "
        f"data-media-items=\"{payload_json}\">"
        "<label class='media-selector-label' for='media-hit-select'>Select media hit</label>"
        f"<select id='media-hit-select' class='media-selector-input'>{options_html}</select>"
        "<div class='media-single-viewer'>"
        "<div class='media-single-preview' data-media-preview></div>"
        "<div class='media-single-meta'>"
        "<div class='media-name' data-media-name></div>"
        "<div class='media-detail' data-media-details></div>"
        "<div class='media-detail' data-media-path></div>"
        "<div class='source-links'>"
        "<a href='#' target='_blank' rel='noopener noreferrer' data-media-open-file>Open file</a>"
        " | "
        "<a href='#' target='_blank' rel='noopener noreferrer' data-media-open-folder>Open folder</a>"
        "</div>"
        "</div>"
        "</div>"
        "</div>"
    )


def build_detected_devices_tabs(detected_devices: dict[str, list[dict[str, str]]]) -> str:
    categories = ["Phone Identifiers", "Bluetooth", "Wi-Fi", "Media / Companion"]
    buttons: list[str] = []
    panels: list[str] = []

    for index, category in enumerate(categories):
        tab_id = sanitize_windows_component(category.lower().replace(" ", "-"), 40)
        active_class = " active" if index == 0 else ""
        selected = "true" if index == 0 else "false"
        entries = detected_devices.get(category, [])
        buttons.append(
            f"<button class='summary-tab-button{active_class}' type='button' "
            f"data-device-tab='{html.escape(tab_id)}' aria-selected='{selected}' "
            f"aria-controls='device-panel-{html.escape(tab_id)}' id='device-tab-{html.escape(tab_id)}'>"
            f"{html.escape(category)} ({len(entries)})</button>"
        )

        if entries:
            rows = [
                [
                    entry.get("name", ""),
                    entry.get("identifier", ""),
                    entry.get("previously_connected", ""),
                    entry.get("source", ""),
                    entry.get("details", ""),
                ]
                for entry in entries
            ]
            content = build_table(["Name", "Identifier", "Previously Connected", "Source", "Details"], rows)
        else:
            content = "<p class='text-muted'>No devices detected in this category.</p>"

        panels.append(
            f"<div class='summary-tab-panel{active_class}' id='device-panel-{html.escape(tab_id)}' "
            f"role='tabpanel' aria-labelledby='device-tab-{html.escape(tab_id)}' "
            f"data-print-label='{html.escape(category)}'>{content}</div>"
        )

    return (
        "<div class='summary-tabs'>"
        "<div class='summary-tab-list' role='tablist' aria-label='Detected device categories'>"
        f"{''.join(buttons)}"
        "</div>"
        "<div class='summary-panel-wrap'>"
        f"{''.join(panels)}"
        "</div>"
        "</div>"
    )


def build_results_panel(results_html: str) -> str:
    return f"<div class='summary-panel-wrap'>{results_html}</div>"


def build_summary_dropdown_cards(section_id: str, items: list[tuple[str, Any]]) -> str:
    safe_id = sanitize_windows_component(section_id, 60).replace("_", "-")
    options_html = "".join(
        f"<option value='{html.escape(str(index))}'>{html.escape(str(label))}</option>"
        for index, (label, _value) in enumerate(items)
    )
    panels_html = "".join(
        (
            f"<div class='summary-dropdown-panel{' active' if index == 0 else ''}' "
            f"data-summary-dropdown-panel='{html.escape(safe_id)}' "
            f"data-summary-dropdown-value='{html.escape(str(index))}' "
            f"data-print-label='{html.escape(str(label))}'>"
            f"<div class='card'><div class='label'>{html.escape(str(label))}</div><div class='value'>{html.escape(str(value))}</div></div>"
            f"</div>"
        )
        for index, (label, value) in enumerate(items)
    )
    return (
        "<div class='summary-dropdown-wrap'>"
        f"<label class='summary-dropdown-label' for='summary-dropdown-{html.escape(safe_id)}'>Select result</label>"
        f"<select class='summary-dropdown-select' id='summary-dropdown-{html.escape(safe_id)}' data-summary-dropdown='{html.escape(safe_id)}'>"
        f"{options_html}"
        "</select>"
        f"{panels_html}"
        "</div>"
    )


def build_summary_grid_cards(items: list[tuple[str, Any]]) -> str:
    if not items:
        return "<p>No results were available for this section.</p>"
    return "<div class='grid'>{}</div>".format(
        "".join(
            f"<div class='card'><div class='label'>{html.escape(str(label))}</div><div class='value'>{html.escape(str(value))}</div></div>"
            for label, value in items
        )
    )


def build_section_dropdown(section_id: str, items: list[tuple[str, str]]) -> str:
    items = [item for item in items if item and item[1]]
    if not items:
        return "<p>No result sections were available for this report.</p>"
    safe_id = sanitize_windows_component(section_id, 60).replace("_", "-")
    options_html = "".join(
        f"<option value='{html.escape(str(index))}'>{html.escape(label)}</option>"
        for index, (label, _content) in enumerate(items)
    )
    panels_html = "".join(
        f"<div class='section-dropdown-panel{' active' if index == 0 else ''}' "
        f"data-section-dropdown-panel='{html.escape(safe_id)}' "
        f"data-section-dropdown-value='{html.escape(str(index))}' "
        f"data-print-label='{html.escape(label)}'>{content}</div>"
        for index, (label, content) in enumerate(items)
    )
    return (
        "<div class='summary-dropdown-wrap section-dropdown-wrap'>"
        f"<label class='summary-dropdown-label' for='section-dropdown-{html.escape(safe_id)}'>Select section</label>"
        f"<select class='summary-dropdown-select' id='section-dropdown-{html.escape(safe_id)}' data-section-dropdown='{html.escape(safe_id)}'>"
        f"{options_html}"
        "</select>"
        f"{panels_html}"
        "</div>"
    )


def build_pdf_button(field_reference_href: str = FIELD_REFERENCE_PDF_NAME) -> str:
    return (
        "<div class='report-action-row report-action-row-top no-print'>"
        "<button class='report-action-button report-action-button-print' type='button' data-print-report>"
        "Export PDF"
        "</button>"
        f"<a class='report-action-button report-action-button-reference' href='{html.escape(field_reference_href, quote=True)}' "
        "target='_blank' rel='noopener noreferrer'>"
        "Field Guide PDF"
        "</a>"
        "<button class='report-action-button report-action-button-theme' type='button' data-toggle-theme>"
        "Light Mode"
        "</button>"
        "</div>"
    )


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
    *,
    app_name: str,
    app_subtitle: str,
    logo_data_uri: str,
    brand_dark: str,
    brand_panel: str,
    brand_panel_alt: str,
    brand_line: str,
    brand_text: str,
    brand_muted: str,
    brand_cyan: str,
    brand_cyan_bright: str,
    brand_gold: str,
):
    case_metadata = summary.get("case_metadata", {}) or {}
    exported_file_counts = summary.get("exported_file_counts", {}) or {}
    detected_devices = summary.get("detected_devices", {}) or {}
    input_mode = str(summary.get("input_mode", "auto") or "auto").strip().lower()
    detected_devices_html = build_detected_devices_tabs(detected_devices)
    report_target_description = describe_report_target(summary)
    detected_model_info = detect_glasses_model(derived_sku_records, media_records, android_device_records)
    detected_model_html = build_detected_model_card(detected_model_info)
    results_items = []
    case_settings_hits = summary.get("case_settings_count", 0) if input_mode != "android" else summary.get("android_profile_count", 0)
    device_sync_hits = summary.get("device_sync_count", 0) if input_mode != "android" else summary.get("android_sync_count", 0)
    derived_sku_hits = summary.get("derived_sku_count", 0) if input_mode != "android" else (
        summary.get("derived_sku_count", 0) or summary.get("android_device_count", 0)
    )
    results_items.extend(
        [
            ("Case Settings Hits", case_settings_hits),
            ("Device Sync Hits", device_sync_hits),
            ("Derived SKU Hits", derived_sku_hits),
        ]
    )
    results_items.extend(
        [
            ("Linked Account Records", summary.get("account_count", 0)),
            ("Prompt Hits", summary.get("prompt_count", 0)),
            ("Additional Identifiers", summary.get("identifier_count", 0)),
            ("Media Hits", summary.get("media_count", 0)),
            ("High Confidence Media Hits", summary.get("high_confidence_media_count", 0)),
            ("Prompt Files Copied", exported_file_counts.get("prompt_files_copied", 0)),
            ("Media Files Copied", exported_file_counts.get("media_files_copied", 0)),
        ]
    )
    summary_tabs = [
        (
            "overview",
            "Overview",
            [
                ("Input Root", summary.get("input_root", "")),
                ("Output Folder", summary.get("output_folder", "")),
                ("Scan Mode", input_mode.title()),
            ],
        ),
        (
            "device",
            "Device",
            [],
        ),
        (
            "results",
            "Results",
            results_items,
        ),
    ]

    summary_tab_buttons = "".join(
        (
            f"<button class='summary-tab-button{' active' if index == 0 else ''}' "
            f"type='button' data-summary-tab='{html.escape(tab_id)}' "
            f"role='tab' aria-selected='{'true' if index == 0 else 'false'}' "
            f"aria-controls='summary-panel-{html.escape(tab_id)}' id='summary-tab-{html.escape(tab_id)}'>"
            f"{html.escape(tab_label)}</button>"
        )
        for index, (tab_id, tab_label, _items) in enumerate(summary_tabs)
    )
    summary_tab_panels = "".join(
        (
            f"<div class='summary-tab-panel{' active' if index == 0 else ''}' "
            f"id='summary-panel-{html.escape(tab_id)}' role='tabpanel' "
            f"aria-labelledby='summary-tab-{html.escape(tab_id)}' "
            f"data-print-label='{html.escape(tab_label)}'>"
            f"{cards_html}"
            f"</div>"
        )
        for index, (tab_id, tab_label, items) in enumerate(summary_tabs)
        for cards_html in [
            (
                build_summary_grid_cards(items)
                if tab_id == "results"
                else (
                    "<div class='summary-device-tab'>"
                    "<p>Phone identifiers, Wi-Fi, Bluetooth, and media or companion-device detections identified during the scan.</p>"
                    f"{detected_devices_html}"
                    "</div>"
                )
                if tab_id == "device"
                else "<div class='grid'>{}</div>".format(
                    "".join(
                        f"<div class='card'><div class='label'>{html.escape(str(label))}</div><div class='value'>{html.escape(str(value))}</div></div>"
                        for label, value in items
                    )
                )
            )
        ]
    )
    case_info_rows = [
        ["Agency", case_metadata.get("agency", "")],
        ["Examiner Name", case_metadata.get("examiner_name", "")],
        ["Case Number", case_metadata.get("case_number", "")],
        ["Offense Type", case_metadata.get("offense_type", "")],
        ["Item Number", case_metadata.get("item_number", "")],
        ["Extraction Date and Time", case_metadata.get("extraction_datetime", "")],
    ]

    prompt_rows = [
        [
            item.prompt_timestamp,
            item.prompt_text,
            item.prompt_line_number,
            item.source_path,
        ]
        for item in prompt_records[:250]
    ]
    media_rows = [
        [
            item.media_path,
            item.extension,
            explain_media_hit(item.reasons, make=item.exif_make, model=item.exif_model, software=item.exif_software, exif_datetime=item.exif_datetime),
            item.exif_make,
            item.exif_model,
            item.exif_software,
        ]
        for item in media_records[:250]
    ]
    account_rows = [
        [item.field, item.value, item.source_path, item.context]
        for item in account_records[:250]
    ]
    ordered_case_settings_records = sorted(
        case_settings_records[:250],
        key=lambda item: _parse_report_timestamp(item.last_settings_snapshot_time),
        reverse=True,
    )
    case_settings_rows = [
        [
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
        ]
        for item in ordered_case_settings_records
    ]
    case_settings_record_labels = [
        "Latest Snapshot" if index == 0 and _parse_report_timestamp(item.last_settings_snapshot_time)[0] else f"Record {index + 1}"
        for index, item in enumerate(ordered_case_settings_records)
    ]
    device_sync_rows = [
        [
            item.glasses_device_id,
            item.glasses_firmware_version,
            item.app_version_at_last_sync,
            item.last_sync_time,
            item.source_path,
        ]
        for item in device_sync_records[:250]
    ]
    derived_sku_rows = [
        [
            item.glasses_serial_number,
            item.model,
            item.model_short_name,
            item.frame_style,
            item.frame_color_display_name,
            item.frame_color,
            item.lens_color_display_name,
            item.lens_color,
            item.source_path,
        ]
        for item in derived_sku_records[:250]
    ]
    derived_sku_record_labels = [
        "Primary Candidate" if index == 0 else f"Record {index + 1}"
        for index, _item in enumerate(derived_sku_records[:250])
    ]
    android_profile_rows = [
        [
            item.fetched,
            item.user_id,
            item.user_name,
            item.short_name,
            item.social_username,
            item.social_display_name,
            item.social_profile_id,
            item.imported_data_source,
            item.constellation_group_id,
            item.abra_id,
            item.abra_messaging_user_id,
            item.eligibility_subscription,
            item.source_path,
        ]
        for item in android_profile_records[:250]
    ]
    android_device_rows = [
        [
            item.device_codename,
            item.source,
            item.pairing_id,
            item.device_id,
            item.serial,
            item.capture_type,
            item.example_capture_id,
            item.attributes,
            item.source_path,
        ]
        for item in android_device_records[:250]
    ]
    android_sync_rows = [
        [
            item.capture_time,
            item.fetch_completed,
            item.import_completed,
            item.auto_saved,
            item.session_id,
            item.capture_id,
            item.media_type,
            item.import_trigger,
            item.processing_state,
            item.thumbnail_state,
            item.full_media_state,
            item.shared_media_global_id,
            item.wifi_scan_data,
            item.source_path,
        ]
        for item in android_sync_records[:250]
    ]
    media_selector_html = build_media_selector(media_records, path)
    media_gallery_html = build_media_gallery(media_records, path)
    pdf_button_html = build_pdf_button()
    case_settings_results_html = build_record_selector(
        "case-settings-records",
        ["Glasses Device ID", "Case Serial Number", "Case Software Version", "Last Settings Snapshot Time", "Has Completed Voice OOBE", "Meta AI Opt-In Completed", "Meta AI Geo Opt-In Completed", "Live AI EAP Opt-In Status", "Default Provider Backward Compatibility Script Run", "Default Provider Backward Compatibility Script Run V2", "Show Language Reverted Notification", "Show Language Reverted Push Notification", "Source"],
        case_settings_rows,
        record_labels=case_settings_record_labels,
    )
    device_sync_results_html = build_record_selector(
        "device-sync-records",
        ["Glasses Device ID", "Glasses Firmware Version", "App Version at Last Sync", "Last Sync Time", "Source"],
        device_sync_rows,
    )
    derived_sku_results_html = build_record_selector(
        "derived-sku-records",
        ["Glasses Serial Number", "Model", "Model Short Name", "Frame Style", "Frame Color Display Name", "Frame Color", "Lens Color Display Name", "Lens Color", "Source"],
        derived_sku_rows,
        record_labels=derived_sku_record_labels,
    )
    android_profile_results_html = build_record_selector(
        "android-profile-records",
        ["Fetched", "User ID", "User Name", "Short Name", "Social Username", "Social Display Name", "Social Profile ID", "Imported Data Source", "Constellation Group ID", "Abra ID", "Abra Messaging User ID", "Eligibility / Subscription", "Source"],
        android_profile_rows,
        title_column=4,
    )
    android_device_results_html = build_record_selector(
        "android-device-records",
        ["Device Codename", "Source", "Pairing ID", "Device ID", "Serial", "Capture Type", "Example Capture ID", "Attributes", "Source Path"],
        android_device_rows,
        title_column=0,
    )
    android_sync_results_html = build_record_selector(
        "android-sync-records",
        ["Capture Time", "Fetch Completed", "Import Completed", "Auto Saved", "Session ID", "Capture ID", "Media Type", "Import Trigger", "Processing State", "Thumbnail State", "Full Media State", "Shared Media Global ID", "WiFi Scan Data", "Source"],
        android_sync_rows,
        title_column=5,
    )
    account_results_html = build_record_selector(
        "linked-account-records",
        ["Field", "Value", "Source Path", "Context"],
        account_rows,
        title_column=0,
    )
    prompt_results_html = build_record_selector(
        "metaai-prompt-records",
        ["Prompt Timestamp", "Prompt Text", "Prompt Line Number", "Source"],
        prompt_rows,
        title_column=1,
    )
    case_settings_report_html = case_settings_results_html if case_settings_records else android_profile_results_html
    device_sync_report_html = device_sync_results_html if device_sync_records else android_sync_results_html
    derived_sku_report_html = derived_sku_results_html if derived_sku_records else android_device_results_html
    media_results_html = (
        f"{media_selector_html}"
        f"{media_gallery_html}"
        "<p>Showing up to the first 250 media records.</p>"
        f"{build_table(['Media Path', 'Extension', 'Why Flagged', 'EXIF Make', 'EXIF Model', 'EXIF Software'], media_rows)}"
        "<div class='footnote'>Full results are still available in the Excel and JSON exports in this same output folder.</div>"
    )
    section_items: list[tuple[str, str]] = []
    show_all_shared_sections = True
    if show_all_shared_sections or case_settings_records or android_profile_records:
        section_items.append(
            (
                "Meta Glasses Case Settings",
                "<div class='section section-embedded'>"
                "<h2>Meta Glasses Case Settings</h2>"
                f"<p>{'Structured Stella case settings records extracted from Meta Glasses application data. Multiple records can reflect different glasses, separate settings snapshots over time, or repeated app-state saves. When a snapshot time is available, the newest record is labeled Latest Snapshot.' if case_settings_records else 'Structured Android Meta app profile records presented in the same report position as Apple case-settings data for visual consistency across reports.'}</p>"
                f"{build_results_panel(case_settings_report_html)}"
                "</div>",
            )
        )
    if show_all_shared_sections or device_sync_records or android_sync_records:
        section_items.append(
            (
                "Meta Glasses Device Sync Log",
                "<div class='section section-embedded'>"
                "<h2>Meta Glasses Device Sync Log</h2>"
                f"<p>{'Structured Stella sync and firmware records extracted from Meta Glasses application data.' if device_sync_records else 'Structured Android sync and import activity records presented in the same report position as Apple device-sync data for visual consistency across reports.'}</p>"
                f"{build_results_panel(device_sync_report_html)}"
                "</div>",
            )
        )
    if show_all_shared_sections or derived_sku_records or android_device_records:
        section_items.append(
            (
                "Meta Glasses Derived SKU Info",
                "<div class='section section-embedded'>"
                "<h2>Meta Glasses Derived SKU Info</h2>"
                f"<p>{'Structured model, frame, and lens details extracted or inferred from Meta Glasses application data. Multiple records can reflect separate glasses, repeated snapshots of the same glasses, or overlapping values recovered from more than one artifact. The first row is labeled Primary Candidate because it is the same leading SKU record used when no stronger model match is available for the detected-model card.' if derived_sku_records else 'Structured Android device and capture records presented in the same report position as Apple derived-SKU data for visual consistency across reports.'}</p>"
                f"{build_results_panel(derived_sku_report_html)}"
                "</div>",
            )
        )
    if show_all_shared_sections or account_records:
        section_items.append(
            (
                "Meta Glasses Linked Accounts",
                "<div class='section section-embedded'>"
                "<h2>Meta Glasses Linked Accounts</h2>"
                "<p>Structured account information extracted from Stella-linked account artifacts and Android Meta app profile data.</p>"
                f"{build_results_panel(account_results_html)}"
                "</div>",
            )
        )
    if show_all_shared_sections or prompt_records:
        section_items.append(
            (
                "Meta Glasses Meta AI Prompts",
                "<div class='section section-embedded'>"
                "<h2>Meta Glasses Meta AI Prompts</h2>"
                "<p>Showing up to the first 250 prompt records extracted from iOS Stella logs and Android Meta AI app sources.</p>"
                "<div class='footnote'>Warning: Validate the prompt and response timestamps before relying on them in reporting or analysis.</div>"
                f"{build_results_panel(prompt_results_html)}"
                "</div>",
            )
        )
    if show_all_shared_sections or media_records:
        section_items.append(
            (
                "Related Media Hits",
                "<div class='section section-embedded'>"
                "<h2>Related Media Hits</h2>"
                "<p>Select a media hit from the dropdown to view it one at a time.</p>"
                f"{build_results_panel(media_results_html)}"
                "</div>",
            )
        )

    additional_sections_html = build_section_dropdown("report-sections", section_items)

    html_output = f"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{app_name} Report</title>
  <style>
    :root {{
      --bg: {brand_dark};
      --panel: {brand_panel};
      --panel-alt: {brand_panel_alt};
      --ink: {brand_text};
      --muted: {brand_muted};
      --accent: {brand_cyan};
      --accent-bright: {brand_cyan_bright};
      --gold: {brand_gold};
      --line: {brand_line};
    }}
    body {{
      margin: 0;
      font-family: "Segoe UI", Tahoma, sans-serif;
      background:
        radial-gradient(circle at top center, rgba(39, 194, 255, 0.18), transparent 28%),
        linear-gradient(180deg, #071225 0%, var(--bg) 100%);
      color: var(--ink);
      transition: background 0.2s ease, color 0.2s ease;
    }}
    html[data-theme="light"] {{
      --bg: #f3f8ff;
      --panel: #ffffff;
      --panel-alt: #eef5ff;
      --ink: #10345a;
      --muted: #4b6f95;
      --accent: #1182d7;
      --accent-bright: #1182d7;
      --gold: #1182d7;
      --line: rgba(17, 130, 215, 0.22);
    }}
    html[data-theme="light"] body {{
      background:
        radial-gradient(circle at top center, rgba(17, 130, 215, 0.12), transparent 30%),
        linear-gradient(180deg, #ffffff 0%, var(--bg) 100%);
    }}
    .wrap {{
      width: 100%;
      max-width: 1200px;
      margin: 0 auto;
      padding: 24px;
      box-sizing: border-box;
    }}
    .hero {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 24px;
      padding: 28px;
      box-shadow: 0 24px 70px rgba(2, 10, 26, 0.45);
      position: relative;
      overflow: hidden;
      display: block;
      width: 100%;
      max-width: 100%;
      box-sizing: border-box;
    }}
    .hero::after {{
      content: "";
      position: absolute;
      inset: auto -10% -35% auto;
      width: 420px;
      height: 420px;
      border-radius: 50%;
      background: radial-gradient(circle, rgba(39, 194, 255, 0.2), transparent 65%);
      pointer-events: none;
    }}
    html[data-theme="light"] .hero::after {{
      background: radial-gradient(circle, rgba(17, 130, 215, 0.12), transparent 65%);
    }}
    .brand-row {{
      display: flex;
      align-items: flex-start;
      justify-content: flex-start;
      gap: 18px;
      margin-bottom: 18px;
    }}
    .brand-logo {{
      max-width: 260px;
      width: 100%;
      height: auto;
      display: block;
      margin: 0;
      filter: drop-shadow(0 18px 36px rgba(0, 0, 0, 0.38));
      flex: 0 0 auto;
    }}
    html[data-theme="light"] .brand-logo {{
      filter: drop-shadow(0 18px 30px rgba(17, 130, 215, 0.12));
    }}
    .brand-fallback {{
      display: flex;
      align-items: center;
      gap: 16px;
      flex-wrap: wrap;
    }}
    .hero-copy {{
      flex: 1 1 auto;
      min-width: 280px;
    }}
    .hero-copy > p {{
      margin-top: 0;
      margin-bottom: 18px;
    }}
    .hero-model-card {{
      margin-top: 18px;
      border: 1px solid rgba(122, 228, 255, 0.24);
      background: rgba(255, 255, 255, 0.04);
      border-radius: 18px;
      padding: 16px;
      box-sizing: border-box;
      max-width: 720px;
    }}
    html[data-theme="light"] .hero-model-card {{
      background: rgba(17, 130, 215, 0.04);
      border-color: rgba(17, 130, 215, 0.2);
    }}
    .hero-model-label {{
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted);
      margin-bottom: 10px;
    }}
    .hero-model-body {{
      display: grid;
      grid-template-columns: minmax(150px, 190px) minmax(0, 1fr);
      gap: 16px;
      align-items: start;
    }}
    .hero-model-stack {{
      display: flex;
      flex-direction: column;
      gap: 10px;
      width: 100%;
      max-width: 100%;
    }}
    .hero-model-image {{
      width: 100%;
      max-width: 100%;
      aspect-ratio: auto;
      object-fit: contain;
      border-radius: 12px;
      border: 0;
      background: transparent;
      display: block;
      padding: 0;
      box-sizing: border-box;
    }}
    .hero-model-image-placeholder {{
      display: flex;
      align-items: center;
      justify-content: center;
      text-align: center;
      padding: 12px;
      color: var(--muted);
      font-size: 13px;
    }}
    html[data-theme="light"] .hero-model-image {{
      background: transparent;
      border-color: transparent;
    }}
    .hero-model-copy {{
      min-width: 0;
    }}
    .hero-model-name {{
      font-size: 22px;
      font-weight: 800;
      color: #ffffff;
      margin-bottom: 8px;
      line-height: 1.2;
    }}
    html[data-theme="light"] .hero-model-name {{
      color: #10345a;
    }}
    .hero-model-description {{
      color: var(--muted);
      font-size: 14px;
      line-height: 1.5;
      margin-bottom: 10px;
    }}
    .hero-model-capabilities {{
      margin: 0;
      padding-left: 18px;
      color: #ffffff;
      display: grid;
      gap: 6px;
    }}
    html[data-theme="light"] .hero-model-capabilities {{
      color: #10345a;
    }}
    .hero-model-capabilities li {{
      line-height: 1.4;
    }}
    .hero-model-evidence {{
      font-size: 12px;
      color: var(--muted);
      word-break: break-word;
    }}
    .brand-mark {{
      width: 68px;
      height: 68px;
      border-radius: 18px;
      background: linear-gradient(180deg, rgba(122, 228, 255, 0.22), rgba(39, 194, 255, 0.08));
      border: 1px solid rgba(122, 228, 255, 0.4);
      position: relative;
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.08), 0 16px 30px rgba(0, 0, 0, 0.28);
    }}
    .brand-copy {{
      min-width: 280px;
    }}
    .brand-name {{
      margin: 0;
      font-size: clamp(42px, 8vw, 72px);
      font-style: italic;
      font-weight: 900;
      letter-spacing: -0.04em;
      line-height: 0.95;
      text-shadow: 0 10px 34px rgba(0, 0, 0, 0.28);
    }}
    .brand-name .light {{
      color: #ffffff;
    }}
    html[data-theme="light"] .brand-name .light {{
      color: #10345a;
    }}
    .brand-name .accent {{
      color: var(--accent);
    }}
    .brand-tag {{
      display: inline-block;
      margin-top: 10px;
      padding: 10px 18px;
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.06);
      border: 1px solid rgba(122, 228, 255, 0.28);
      font-size: 13px;
      font-weight: 800;
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: #ffffff;
    }}
    html[data-theme="light"] .brand-tag {{
      background: rgba(17, 130, 215, 0.08);
      border-color: rgba(17, 130, 215, 0.25);
      color: #0f6fc2;
    }}
    h1, h2 {{
      margin: 0 0 12px;
      color: #ffffff;
    }}
    html[data-theme="light"] h1,
    html[data-theme="light"] h2 {{
      color: #10345a;
    }}
    p {{
      color: var(--muted);
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
      margin-top: 18px;
      width: 100%;
      align-items: stretch;
      grid-auto-rows: 1fr;
    }}
    .summary-tabs {{
      margin-top: 20px;
      width: 100%;
    }}
    .summary-tab-list {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-bottom: 16px;
    }}
    .summary-tab-button {{
      appearance: none;
      border: 1px solid rgba(122, 228, 255, 0.2);
      background: rgba(255, 255, 255, 0.04);
      color: var(--muted);
      border-radius: 999px;
      padding: 10px 16px;
      font-size: 13px;
      font-weight: 700;
      letter-spacing: 0.04em;
      cursor: pointer;
      transition: background 0.2s ease, border-color 0.2s ease, color 0.2s ease, transform 0.2s ease;
    }}
    .summary-tab-button:hover {{
      color: #ffffff;
      border-color: rgba(122, 228, 255, 0.45);
      transform: translateY(-1px);
    }}
    html[data-theme="light"] .summary-tab-button {{
      background: #ffffff;
      color: var(--muted);
      border-color: rgba(17, 130, 215, 0.22);
    }}
    html[data-theme="light"] .summary-tab-button:hover {{
      color: #0f6fc2;
      border-color: rgba(17, 130, 215, 0.45);
    }}
    .summary-tab-button.active {{
      background: linear-gradient(180deg, rgba(39, 194, 255, 0.22), rgba(39, 194, 255, 0.08));
      border-color: rgba(122, 228, 255, 0.52);
      color: #ffffff;
      box-shadow: 0 10px 24px rgba(2, 10, 26, 0.25);
    }}
    html[data-theme="light"] .summary-tab-button.active {{
      background: linear-gradient(180deg, rgba(17, 130, 215, 0.14), rgba(17, 130, 215, 0.06));
      border-color: rgba(17, 130, 215, 0.4);
      color: #0f6fc2;
      box-shadow: 0 8px 18px rgba(17, 130, 215, 0.12);
    }}
    .summary-panel-wrap {{
      background: rgba(255, 255, 255, 0.02);
      border: 1px solid rgba(66, 104, 144, 0.45);
      border-radius: 18px;
      padding: 14px;
      width: 100%;
      box-sizing: border-box;
      max-width: 100%;
      overflow: auto;
    }}
    html[data-theme="light"] .summary-panel-wrap {{
      background: rgba(17, 130, 215, 0.03);
      border-color: rgba(17, 130, 215, 0.16);
    }}
    .summary-tab-panel {{
      display: none;
    }}
    .summary-tab-panel.active {{
      display: block;
    }}
    .summary-tab-panel .grid {{
      margin-top: 0;
    }}
    .summary-tab-panel .grid .card {{
      min-height: 132px;
      max-height: 132px;
      height: 132px;
    }}
    .summary-dropdown-wrap {{
      display: grid;
      gap: 12px;
      width: 100%;
    }}
    .summary-dropdown-label {{
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted);
    }}
    .summary-dropdown-select {{
      appearance: none;
      -webkit-appearance: none;
      -moz-appearance: none;
      width: 100%;
      max-width: 340px;
      border: 1px solid rgba(122, 228, 255, 0.28);
      border-radius: 12px;
      background-color: #050b14;
      background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 12 8'%3E%3Cpath fill='%23ffffff' d='M1.41.59 6 5.17 10.59.59 12 2l-6 6-6-6z'/%3E%3C/svg%3E");
      background-position: right 14px center;
      background-repeat: no-repeat;
      background-size: 12px 8px;
      color: #ffffff;
      padding: 12px 44px 12px 14px;
      font-size: 14px;
      font-weight: 600;
      outline: none;
    }}
    .summary-dropdown-select option {{
      background: #050b14;
      color: #ffffff;
    }}
    .summary-dropdown-select:focus {{
      border-color: rgba(122, 228, 255, 0.58);
      box-shadow: 0 0 0 3px rgba(39, 194, 255, 0.14);
    }}
    .summary-dropdown-panel {{
      display: none;
      width: 100%;
    }}
    .summary-dropdown-panel.active {{
      display: block;
      width: 100%;
    }}
    .record-selector-wrap {{
      display: grid;
      gap: 12px;
    }}
    .record-dropdown-panel {{
      display: none;
    }}
    .record-dropdown-panel.active {{
      display: block;
    }}
    .card {{
      background: rgba(255, 255, 255, 0.03);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 14px;
      backdrop-filter: blur(8px);
      width: 100%;
      box-sizing: border-box;
      min-height: 132px;
      height: 100%;
      display: flex;
      flex-direction: column;
      justify-content: flex-start;
    }}
    .summary-dropdown-panel .card {{
      min-height: 132px;
      max-height: 132px;
    }}
    html[data-theme="light"] .card {{
      background: rgba(17, 130, 215, 0.03);
    }}
    .label {{
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted);
      margin-bottom: 8px;
    }}
    .value {{
      font-size: 18px;
      font-weight: 700;
      word-break: break-word;
      overflow: auto;
      flex: 1 1 auto;
      min-height: 0;
    }}
    .section {{
      margin-top: 22px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 20px;
      box-shadow: 0 18px 44px rgba(2, 10, 26, 0.32);
      display: block;
      width: 100%;
      max-width: 100%;
      box-sizing: border-box;
      overflow: hidden;
    }}
    html[data-theme="light"] .section {{
      box-shadow: 0 18px 34px rgba(17, 130, 215, 0.08);
    }}
    .section-embedded {{
      margin-top: 0;
      box-shadow: none;
    }}
    .section-dropdown-wrap {{
      margin-top: 22px;
      display: block;
      width: 100%;
      max-width: 100%;
      box-sizing: border-box;
    }}
    .section-dropdown-panel {{
      display: none;
      width: 100%;
      max-width: 100%;
      box-sizing: border-box;
    }}
    .section-dropdown-panel.active {{
      display: block;
      width: 100%;
      max-width: 100%;
      box-sizing: border-box;
    }}
    .section-dropdown-panel .section,
    .section-dropdown-panel .section-embedded {{
      width: 100%;
      max-width: 100%;
      box-sizing: border-box;
    }}
    .section-dropdown-panel .summary-panel-wrap,
    .section-dropdown-panel .record-selector-wrap,
    .section-dropdown-panel .table-wrap {{
      max-width: 100%;
      overflow: auto;
    }}
    .section-dropdown-panel .section-embedded {{
      max-height: 72vh;
      overflow: auto;
    }}
    .report-action-row {{
      margin-top: 14px;
      margin-bottom: 8px;
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
    }}
    .report-action-row-top {{
      margin-top: 0;
      margin-bottom: 16px;
    }}
    .report-action-button {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 10px 16px;
      border-radius: 999px;
      border: 1px solid rgba(122, 228, 255, 0.38);
      background: linear-gradient(180deg, rgba(39, 194, 255, 0.22), rgba(39, 194, 255, 0.08));
      color: #ffffff;
      text-decoration: none;
      font-size: 13px;
      font-weight: 700;
      letter-spacing: 0.04em;
      cursor: pointer;
    }}
    .report-action-button:hover {{
      border-color: rgba(122, 228, 255, 0.58);
    }}
    html[data-theme="light"] .report-action-button {{
      background: linear-gradient(180deg, rgba(17, 130, 215, 0.12), rgba(17, 130, 215, 0.05));
      border-color: rgba(17, 130, 215, 0.28);
      color: #0f6fc2;
    }}
    .report-action-button-print {{
      font-family: inherit;
    }}
    .table-wrap {{
      width: 100%;
      margin-top: 12px;
      overflow-x: auto;
      overflow-y: hidden;
      box-sizing: border-box;
    }}
    table {{
      width: max-content;
      min-width: 100%;
      border-collapse: collapse;
      font-size: 14px;
      background: rgba(7, 18, 37, 0.62);
      border-radius: 12px;
      overflow: hidden;
      table-layout: auto;
    }}
    html[data-theme="light"] table {{
      background: #ffffff;
    }}
    th, td {{
      border-bottom: 1px solid rgba(66, 104, 144, 0.5);
      text-align: left;
      vertical-align: top;
      padding: 10px 12px;
      min-width: 120px;
      white-space: nowrap;
    }}
    th {{
      background: rgba(39, 194, 255, 0.12);
      color: var(--accent-bright);
      position: sticky;
      top: 0;
    }}
    html[data-theme="light"] th {{
      background: rgba(17, 130, 215, 0.1);
    }}
    tr:nth-child(even) td {{
      background: rgba(255, 255, 255, 0.02);
    }}
    html[data-theme="light"] tr:nth-child(even) td {{
      background: rgba(17, 130, 215, 0.03);
    }}
    .footnote {{
      margin-top: 10px;
      font-size: 12px;
      color: var(--muted);
    }}
    .media-gallery {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 220px));
      gap: 12px;
      margin-top: 14px;
      width: 100%;
      max-width: 100%;
      box-sizing: border-box;
      align-items: stretch;
      grid-auto-rows: 1fr;
    }}
    .media-selector {{
      margin-top: 14px;
      margin-bottom: 18px;
      padding: 16px;
      border: 1px solid var(--line);
      border-radius: 16px;
      background: rgba(255, 255, 255, 0.03);
      width: 100%;
      max-width: 100%;
      box-sizing: border-box;
    }}
    html[data-theme="light"] .media-selector {{
      background: rgba(17, 130, 215, 0.03);
    }}
    .media-selector-label {{
      display: block;
      margin-bottom: 8px;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted);
    }}
    .media-selector-input {{
      appearance: none;
      -webkit-appearance: none;
      -moz-appearance: none;
      width: 100%;
      padding: 12px 44px 12px 14px;
      border-radius: 12px;
      border: 1px solid rgba(122, 228, 255, 0.28);
      background-color: #050b14;
      background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 12 8'%3E%3Cpath fill='%23ffffff' d='M1.41.59 6 5.17 10.59.59 12 2l-6 6-6-6z'/%3E%3C/svg%3E");
      background-position: right 14px center;
      background-repeat: no-repeat;
      background-size: 12px 8px;
      color: #ffffff;
      font-size: 14px;
      margin-bottom: 14px;
    }}
    .media-selector-input option {{
      background: #050b14;
      color: #ffffff;
    }}
    .media-single-viewer {{
      display: grid;
      grid-template-columns: minmax(0, 1.6fr) minmax(240px, 0.9fr);
      gap: 16px;
      align-items: start;
      width: 100%;
      max-width: 100%;
      overflow: hidden;
    }}
    .media-single-preview {{
      background: rgba(7, 18, 37, 0.9);
      border: 1px solid rgba(66, 104, 144, 0.5);
      border-radius: 16px;
      min-height: min(34vw, 320px);
      max-height: min(60vh, 560px);
      display: flex;
      align-items: center;
      justify-content: center;
      overflow: hidden;
      padding: 8px;
      width: 100%;
      max-width: 100%;
      box-sizing: border-box;
    }}
    html[data-theme="light"] .media-single-preview {{
      background: #f7fbff;
      border-color: rgba(17, 130, 215, 0.18);
    }}
    .media-single-preview img,
    .media-single-preview video {{
      width: auto;
      max-width: 100%;
      height: auto;
      max-height: min(56vh, 520px);
      object-fit: contain;
      display: block;
      border-radius: 12px;
    }}
    .media-single-preview audio {{
      width: 100%;
      max-width: 100%;
    }}
    .media-single-meta {{
      padding: 4px 2px;
      min-width: 0;
      max-width: 100%;
      overflow-wrap: anywhere;
    }}
    .media-card {{
      background: rgba(255, 255, 255, 0.03);
      border: 1px solid var(--line);
      border-radius: 16px;
      overflow: hidden;
      box-shadow: 0 18px 36px rgba(2, 10, 26, 0.25);
      height: 100%;
      display: flex;
      flex-direction: column;
    }}
    html[data-theme="light"] .media-card {{
      background: #ffffff;
      box-shadow: 0 14px 28px rgba(17, 130, 215, 0.08);
    }}
    .media-preview {{
      background: rgba(7, 18, 37, 0.9);
      aspect-ratio: 4 / 3;
      display: flex;
      align-items: center;
      justify-content: center;
    }}
    html[data-theme="light"] .media-preview {{
      background: #f7fbff;
    }}
    .media-preview img,
    .media-preview video {{
      width: 100%;
      height: 100%;
      object-fit: cover;
      display: block;
    }}
    @media (max-width: 860px) {{
      .hero-model-body {{
        grid-template-columns: 1fr;
      }}
      .hero-model-image {{
        max-width: 100%;
      }}
      .media-single-viewer {{
        grid-template-columns: 1fr;
      }}
      .media-single-preview {{
        min-height: min(52vw, 280px);
        max-height: min(46vh, 420px);
      }}
      .media-single-preview img,
      .media-single-preview video {{
        max-height: min(42vh, 360px);
      }}
    }}
    .media-meta {{
      padding: 10px 12px 12px;
      display: flex;
      flex-direction: column;
      gap: 3px;
      flex: 1 1 auto;
    }}
    .media-name {{
      font-weight: 700;
      color: #ffffff;
      word-break: break-word;
      margin-bottom: 4px;
      font-size: 13px;
      line-height: 1.35;
      min-height: 2.7em;
    }}
    html[data-theme="light"] .media-name {{
      color: #10345a;
    }}
    .media-detail {{
      font-size: 11px;
      color: var(--muted);
      word-break: break-word;
      margin-top: 0;
      line-height: 1.35;
    }}
    .source-list {{
      list-style: none;
      padding: 0;
      margin: 0;
      display: grid;
      gap: 12px;
    }}
    .source-list li {{
      border: 1px solid rgba(66, 104, 144, 0.45);
      border-radius: 12px;
      padding: 12px 14px;
      background: rgba(255, 255, 255, 0.03);
    }}
    html[data-theme="light"] .source-list li {{
      background: #ffffff;
      border-color: rgba(17, 130, 215, 0.18);
    }}
    .source-path {{
      font-family: Consolas, "Courier New", monospace;
      font-size: 13px;
      color: #ffffff;
      word-break: break-all;
      margin-bottom: 8px;
    }}
    html[data-theme="light"] .source-path {{
      color: #10345a;
    }}
    .source-links a {{
      color: var(--accent-bright);
      text-decoration: none;
    }}
    .source-links a:hover {{
      text-decoration: underline;
    }}
    @page {{
      size: auto;
      margin: 0.5in;
    }}
    @media print {{
      :root {{
        --bg: #ffffff;
        --panel: #ffffff;
        --panel-alt: #f4f8ff;
        --ink: #10345a;
        --muted: #476b91;
        --accent: #0f6fc2;
        --accent-bright: #0f6fc2;
        --gold: #0f6fc2;
        --line: rgba(15, 111, 194, 0.28);
      }}
      body {{
        background: #ffffff !important;
        color: #10345a !important;
      }}
      .wrap {{
        max-width: none;
        padding: 0;
      }}
      .hero,
      .section,
      .summary-panel-wrap,
      .media-selector,
      .card,
      .source-list li {{
        background: #ffffff !important;
        box-shadow: none !important;
        break-inside: avoid-page;
      }}
      .summary-tab-list,
      .summary-dropdown-wrap > label,
      .summary-dropdown-select,
      .media-selector-label,
      .media-selector-input,
      .report-action-row,
      .no-print {{
        display: none !important;
      }}
      .media-selector {{
        display: none !important;
      }}
      .summary-tab-panel,
      .summary-dropdown-panel,
      .record-dropdown-panel,
      .section-dropdown-panel {{
        display: block !important;
      }}
      .summary-tab-panel,
      .summary-dropdown-panel,
      .record-dropdown-panel,
      .section-dropdown-panel {{
        page-break-inside: avoid;
        break-inside: avoid-page;
        margin-bottom: 14px;
      }}
      .summary-tab-panel[data-print-label]::before,
      .summary-dropdown-panel[data-print-label]::before,
      .record-dropdown-panel[data-print-label]::before,
      .section-dropdown-panel[data-print-label]::before {{
        content: attr(data-print-label);
        display: block;
        margin-bottom: 10px;
        font-size: 14px;
        font-weight: 700;
        color: #0f6fc2;
        text-transform: none;
      }}
      .hero::after {{
        display: none !important;
      }}
      .brand-logo {{
        filter: none !important;
      }}
      h1, h2, .media-name, .value, .source-path {{
        color: #10345a !important;
      }}
      p, .label, .summary-dropdown-label, .media-detail, .footnote {{
        color: #476b91 !important;
      }}
      .summary-tab-button,
      .report-action-button,
      .summary-dropdown-select,
      .media-selector-input {{
        background: #ffffff !important;
        color: #10345a !important;
        border-color: rgba(15, 111, 194, 0.45) !important;
        box-shadow: none !important;
      }}
      .summary-tab-button.active {{
        background: #eaf4ff !important;
        color: #0f6fc2 !important;
      }}
      .summary-dropdown-select,
      .media-selector-input {{
        background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 12 8'%3E%3Cpath fill='%230f6fc2' d='M1.41.59 6 5.17 10.59.59 12 2l-6 6-6-6z'/%3E%3C/svg%3E") !important;
      }}
      table {{
        background: #ffffff !important;
        display: table !important;
        overflow: visible !important;
      }}
      th {{
        background: #eaf4ff !important;
        color: #0f6fc2 !important;
      }}
      tr:nth-child(even) td {{
        background: #f8fbff !important;
      }}
      .media-single-preview,
      .media-preview {{
        background: #f8fbff !important;
        border-color: rgba(15, 111, 194, 0.22) !important;
      }}
      a, a:visited {{
        color: #0f6fc2 !important;
        text-decoration: none !important;
      }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hero">
      <div class="brand-row">
        {f'<img class="brand-logo" src="{logo_data_uri}" alt="{app_name} logo">' if logo_data_uri else ''}
        <div class="hero-copy">
          <div class="brand-fallback" style="display:{'none' if logo_data_uri else 'flex'};">
            <div class="brand-mark"></div>
            <div class="brand-copy">
              <div class="brand-name"><span class="light">Spec</span><span class="accent">Tacular</span></div>
              <div class="brand-tag">{app_subtitle}</div>
            </div>
          </div>
          <p>{html.escape(report_target_description)}</p>
          {detected_model_html}
        </div>
      </div>
      {pdf_button_html}
      <div class="summary-tabs">
        <div class="summary-tab-list" role="tablist" aria-label="Report summary sections">
          {summary_tab_buttons}
        </div>
        <div class="summary-panel-wrap">
          {summary_tab_panels}
        </div>
      </div>
    </div>

    <div class="section">
      <h2>Case Information</h2>
      {build_table(["Field", "Value"], case_info_rows)}
    </div>
    {additional_sections_html}
  </div>
  <script>
    const summaryButtons = document.querySelectorAll("[data-summary-tab]");
    const summaryPanels = document.querySelectorAll("[id^='summary-panel-']");
    summaryButtons.forEach((button) => {{
      button.addEventListener("click", () => {{
        const targetId = button.getAttribute("data-summary-tab");
        summaryButtons.forEach((item) => {{
          const isActive = item === button;
          item.classList.toggle("active", isActive);
          item.setAttribute("aria-selected", isActive ? "true" : "false");
        }});
        summaryPanels.forEach((panel) => {{
          panel.classList.toggle("active", panel.id === `summary-panel-${{targetId}}`);
        }});
      }});
    }});

    const summaryDropdowns = document.querySelectorAll("[data-summary-dropdown]");
    summaryDropdowns.forEach((select) => {{
      const syncSummaryDropdown = () => {{
        const groupId = select.getAttribute("data-summary-dropdown");
        const selectedValue = select.value;
        document.querySelectorAll(`[data-summary-dropdown-panel="${{groupId}}"]`).forEach((panel) => {{
          panel.classList.toggle("active", panel.getAttribute("data-summary-dropdown-value") === selectedValue);
        }});
      }};
      select.addEventListener("change", syncSummaryDropdown);
      syncSummaryDropdown();
    }});

    const recordDropdowns = document.querySelectorAll("[data-record-dropdown]");
    recordDropdowns.forEach((select) => {{
      const syncRecordDropdown = () => {{
        const groupId = select.getAttribute("data-record-dropdown");
        const selectedValue = select.value;
        document.querySelectorAll(`[data-record-dropdown-panel="${{groupId}}"]`).forEach((panel) => {{
          panel.classList.toggle("active", panel.getAttribute("data-record-dropdown-value") === selectedValue);
        }});
      }};
      select.addEventListener("change", syncRecordDropdown);
      syncRecordDropdown();
    }});

    const sectionDropdowns = document.querySelectorAll("[data-section-dropdown]");
    sectionDropdowns.forEach((select) => {{
      const syncSectionDropdown = () => {{
        const groupId = select.getAttribute("data-section-dropdown");
        const selectedValue = select.value;
        document.querySelectorAll(`[data-section-dropdown-panel="${{groupId}}"]`).forEach((panel) => {{
          panel.classList.toggle("active", panel.getAttribute("data-section-dropdown-value") === selectedValue);
        }});
      }};
      select.addEventListener("change", syncSectionDropdown);
      syncSectionDropdown();
    }});

    const deviceButtons = document.querySelectorAll("[data-device-tab]");
    const devicePanels = document.querySelectorAll("[id^='device-panel-']");
    deviceButtons.forEach((button) => {{
      button.addEventListener("click", () => {{
        const targetId = button.getAttribute("data-device-tab");
        deviceButtons.forEach((item) => {{
          const isActive = item === button;
          item.classList.toggle("active", isActive);
          item.setAttribute("aria-selected", isActive ? "true" : "false");
        }});
        devicePanels.forEach((panel) => {{
          panel.classList.toggle("active", panel.id === `device-panel-${{targetId}}`);
        }});
      }});
    }});

    const mediaSelectorRoot = document.querySelector("[data-media-selector]");
    if (mediaSelectorRoot) {{
      const mediaItems = JSON.parse(mediaSelectorRoot.getAttribute("data-media-items") || "[]");
      const mediaSelect = mediaSelectorRoot.querySelector(".media-selector-input");
      const mediaPreview = mediaSelectorRoot.querySelector("[data-media-preview]");
      const mediaName = mediaSelectorRoot.querySelector("[data-media-name]");
      const mediaDetails = mediaSelectorRoot.querySelector("[data-media-details]");
      const mediaPath = mediaSelectorRoot.querySelector("[data-media-path]");
      const mediaOpenFile = mediaSelectorRoot.querySelector("[data-media-open-file]");
      const mediaOpenFolder = mediaSelectorRoot.querySelector("[data-media-open-folder]");

      const renderMediaItem = (index) => {{
        const item = mediaItems[index];
        if (!item) {{
          mediaPreview.innerHTML = "<p>No media selected.</p>";
          mediaName.textContent = "";
          mediaDetails.textContent = "";
          mediaPath.textContent = "";
          mediaOpenFile.setAttribute("href", "#");
          mediaOpenFolder.setAttribute("href", "#");
          return;
        }}
        mediaPreview.innerHTML = item.viewer_html;
        mediaName.textContent = item.name;
        mediaDetails.textContent = item.details;
        mediaPath.textContent = item.path;
        mediaOpenFile.setAttribute("href", item.file_href || "#");
        mediaOpenFolder.setAttribute("href", item.folder_href || "#");
      }};

      mediaSelect.addEventListener("change", () => {{
        renderMediaItem(Number(mediaSelect.value));
      }});

      renderMediaItem(0);
    }}

    const printButton = document.querySelector("[data-print-report]");
    if (printButton) {{
      printButton.addEventListener("click", () => {{
        window.print();
      }});
    }}

    const themeRoot = document.documentElement;
    const themeButton = document.querySelector("[data-toggle-theme]");
    const storedTheme = localStorage.getItem("spectacular-report-theme");
    const applyTheme = (theme) => {{
      const nextTheme = theme === "light" ? "light" : "dark";
      themeRoot.setAttribute("data-theme", nextTheme);
      if (themeButton) {{
        themeButton.textContent = nextTheme === "dark" ? "Light Mode" : "Dark Mode";
      }}
    }};
    applyTheme(storedTheme || "dark");
    if (themeButton) {{
      themeButton.addEventListener("click", () => {{
        const currentTheme = themeRoot.getAttribute("data-theme") || "dark";
        const nextTheme = currentTheme === "dark" ? "light" : "dark";
        localStorage.setItem("spectacular-report-theme", nextTheme);
        applyTheme(nextTheme);
      }});
    }}
  </script>
</body>
</html>
"""
    with open(windows_safe_path(path), "w", encoding="utf-8") as handle:
        handle.write(html_output)
