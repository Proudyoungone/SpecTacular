import json
import os
import re
import sqlite3
from pathlib import Path

from scripts.models import (
    AccountRecord,
    AndroidMetaAppProfileRecord,
    AndroidMetaDeviceRecord,
    AndroidMetaSyncRecord,
    PromptRecord,
    StellaCaseSettingsRecord,
    StellaDeviceSyncRecord,
    StellaDerivedSkuRecord,
)
from scripts.utils import normalize_meta_timestamp, normalize_text


ANDROID_STELLA_DB_NAMES = {"stelladatabase", "stelladatabase.db"}


def source_is_android_stella_db(path: Path | str) -> bool:
    return Path(str(path)).name.lower() in ANDROID_STELLA_DB_NAMES


def source_is_android_interaction_log_db(path: Path | str) -> bool:
    return Path(str(path)).name.lower() == "interaction_log.db"


def source_is_android_graphql_cache(path: Path | str) -> bool:
    normalized = str(path).replace("/", "\\").lower()
    base_name = Path(str(path)).name.lower()
    return (
        "\\graphql_response_cache\\" in normalized
        and (
            "\\graphql_response_cache\\companion-ar\\" in normalized
            or base_name.startswith("p3%3a")
            or base_name.endswith((".json", ".txt", ".cache", ".blob"))
            or "." not in base_name
        )
    )


def _clean_text(value):
    if value in [None, ""]:
        return ""
    text = normalize_text(value).strip()
    text = re.sub(r"[\x00-\x1f]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _readable_blob_strings(blob: bytes, min_len: int = 4) -> list[str]:
    return [match.decode("utf-8", "ignore") for match in re.findall(rb"[ -~]{%d,}" % min_len, blob)]


def _first_clean(values: list[str], max_len: int = 500) -> str:
    for value in values:
        cleaned = _clean_text(value)
        if cleaned and cleaned.lower() != "null" and len(cleaned) <= max_len:
            return cleaned
    return ""


def _next_value(strings: list[str], key: str) -> list[str]:
    values: list[str] = []
    for index, token in enumerate(strings[:-1]):
        if token == key:
            nxt = _clean_text(strings[index + 1])
            if nxt:
                values.append(nxt)
    return values


def _unique_preserve(values: list[str]) -> list[str]:
    output: list[str] = []
    for value in values:
        cleaned = _clean_text(value)
        if cleaned and cleaned not in output:
            output.append(cleaned)
    return output


def _family_id_from_name(base_name: str) -> str:
    parts = base_name.split("%3a")
    if len(parts) >= 3:
        return parts[1]
    return ""


def _extract_role_aware_turns(strings: list[str]) -> list[tuple[str, str]]:
    role_tokens: list[str] = []
    text_tokens: list[str] = []

    for index, token in enumerate(strings[:-1]):
        value = _clean_text(strings[index + 1])
        if not value:
            continue
        if token == "roles" and value in {"USER", "ASSISTANT"}:
            role_tokens.append(value)
        elif token in {"snippets", "unformatted_snippets", "markdowns", "markdownsx"}:
            text_tokens.append(value)

    turns: list[tuple[str, str]] = []
    text_index = 0
    for role in role_tokens:
        while text_index < len(text_tokens):
            text_value = text_tokens[text_index]
            text_index += 1
            if len(text_value) < 2:
                continue
            if turns and turns[-1] == (role, text_value):
                continue
            turns.append((role, text_value))
            break

    if not turns and text_tokens:
        seen: set[str] = set()
        for text_value in text_tokens:
            if text_value not in seen:
                seen.add(text_value)
                turns.append(("", text_value))

    return turns


def _normalize_chat_key_part(value) -> str:
    text = _clean_text(value).lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _normalize_timestamp_ms(value) -> str:
    if value in [None, ""]:
        return ""
    try:
        return normalize_meta_timestamp(int(value) / 1000 if int(value) > 9999999999 else int(value))
    except Exception:
        return ""


def extract_android_meta_app_profiles(
    path: Path,
    profile_records: list[AndroidMetaAppProfileRecord],
    account_records: list[AccountRecord],
):
    try:
        safe_path = str(path.resolve()).replace("\\", "/")
        db = sqlite3.connect(f"file:{safe_path}?mode=ro", uri=True)
    except Exception:
        return

    try:
        cursor = db.cursor()
        cursor.execute("""
            SELECT
                user_id,
                user_name,
                short_name,
                fetch_timestamp_ms,
                social_profile_user_name,
                social_profile_display_name,
                social_profile_id,
                imported_data_source,
                constellation_group_id,
                abra_id,
                abra_messaging_user_id,
                eligible_for_c50,
                is_eligible_for_c50_feed,
                is_eligible_for_c50_prompt_box,
                is_eligible_for_c50_prompt_history_tab,
                nme_gai_subscription_status,
                nme_gai_subscription_product_name,
                concord_nux_seen_version,
                is_eligible_for_c50_wearables_tab
            FROM user_profile
        """)
        for row in cursor.fetchall():
            eligibility = json.dumps(
                {
                    "eligible_for_c50": row[11],
                    "is_eligible_for_c50_feed": row[12],
                    "is_eligible_for_c50_prompt_box": row[13],
                    "is_eligible_for_c50_prompt_history_tab": row[14],
                    "nme_gai_subscription_status": row[15],
                    "nme_gai_subscription_product_name": row[16],
                    "concord_nux_seen_version": row[17],
                    "is_eligible_for_c50_wearables_tab": row[18],
                },
                ensure_ascii=True,
            )
            profile_records.append(
                AndroidMetaAppProfileRecord(
                    fetched=normalize_meta_timestamp(int(row[3]) / 1000) if row[3] else "",
                    user_id=_clean_text(row[0]),
                    user_name=_clean_text(row[1]),
                    short_name=_clean_text(row[2]),
                    social_username=_clean_text(row[4]),
                    social_display_name=_clean_text(row[5]),
                    social_profile_id=_clean_text(row[6]),
                    imported_data_source=_clean_text(row[7]),
                    constellation_group_id=_clean_text(row[8]),
                    abra_id=_clean_text(row[9]),
                    abra_messaging_user_id=_clean_text(row[10]),
                    eligibility_subscription=eligibility,
                    source_path=str(path),
                )
            )
            account_field_map = (
                ("User ID", row[0]),
                ("User Name", row[1]),
                ("Short Name", row[2]),
                ("Social Username", row[4]),
                ("Social Display Name", row[5]),
                ("Social Profile ID", row[6]),
                ("Imported Data Source", row[7]),
                ("Constellation Group ID", row[8]),
                ("Abra ID", row[9]),
                ("Abra Messaging User ID", row[10]),
            )
            for field_name, raw_value in account_field_map:
                clean_value = _clean_text(raw_value)
                if clean_value:
                    account_records.append(
                        AccountRecord(
                            field=field_name,
                            value=clean_value,
                            source_path=str(path),
                            context="android_user_profile",
                        )
                    )
    except Exception:
        return
    finally:
        db.close()


def extract_android_meta_devices_and_sync(
    path: Path,
    device_records: list[AndroidMetaDeviceRecord],
    sync_records: list[AndroidMetaSyncRecord],
):
    try:
        safe_path = str(path.resolve()).replace("\\", "/")
        db = sqlite3.connect(f"file:{safe_path}?mode=ro", uri=True)
    except Exception:
        return

    try:
        cursor = db.cursor()
        seen_devices: set[tuple[str, ...]] = set()
        try:
            cursor.execute("""
                SELECT
                    device_codename,
                    source,
                    pairing_id,
                    device_id,
                    device_serial,
                    type,
                    capture_id,
                    attributes_json
                FROM capture
                ORDER BY capture_timestamp_ms
            """)
            for row in cursor.fetchall():
                key = tuple(_clean_text(value) for value in row[:6])
                if key in seen_devices:
                    continue
                seen_devices.add(key)
                device_records.append(
                    AndroidMetaDeviceRecord(
                        device_codename=_clean_text(row[0]),
                        source=_clean_text(row[1]),
                        pairing_id=_clean_text(row[2]),
                        device_id=_clean_text(row[3]),
                        serial=_clean_text(row[4]),
                        capture_type=_clean_text(row[5]),
                        example_capture_id=_clean_text(row[6]),
                        attributes=_clean_text(row[7]),
                        source_path=str(path),
                    )
                )
        except Exception:
            pass

        try:
            cursor.execute("""
                SELECT
                    c.capture_timestamp_ms,
                    m.fetch_completed_timestamp_ms,
                    m.import_completed_timestamp_ms,
                    m.auto_saved_timestamp_ms,
                    m.session_id,
                    c.capture_id,
                    m.type,
                    m.import_trigger,
                    m.processing_state,
                    m.thumbnail_state,
                    m.full_media_state,
                    m.shared_media_global_id,
                    m.wifi_scan_data
                FROM capture c
                LEFT JOIN media_item m ON m.capture_id = c.capture_id
                ORDER BY c.capture_timestamp_ms
            """)
            for row in cursor.fetchall():
                sync_records.append(
                    AndroidMetaSyncRecord(
                        capture_time=_normalize_timestamp_ms(row[0]),
                        fetch_completed=_normalize_timestamp_ms(row[1]),
                        import_completed=_normalize_timestamp_ms(row[2]),
                        auto_saved=_normalize_timestamp_ms(row[3]),
                        session_id=_clean_text(row[4]),
                        capture_id=_clean_text(row[5]),
                        media_type=_clean_text(row[6]),
                        import_trigger=_clean_text(row[7]),
                        processing_state=_clean_text(row[8]),
                        thumbnail_state=_clean_text(row[9]),
                        full_media_state=_clean_text(row[10]),
                        shared_media_global_id=_clean_text(row[11]),
                        wifi_scan_data=_clean_text(row[12]),
                        source_path=str(path),
                    )
                )
        except Exception:
            pass
    finally:
        db.close()


def _flatten_android_attribute_map(value, output: dict[str, str]):
    if isinstance(value, dict):
        for key, nested_value in value.items():
            normalized_key = _clean_text(key).lower()
            if isinstance(nested_value, (dict, list)):
                _flatten_android_attribute_map(nested_value, output)
            else:
                cleaned = _clean_text(nested_value)
                if normalized_key and cleaned and normalized_key not in output:
                    output[normalized_key] = cleaned
    elif isinstance(value, list):
        for item in value:
            _flatten_android_attribute_map(item, output)


def _parse_android_attributes(attributes_text: str) -> dict[str, str]:
    cleaned = _clean_text(attributes_text)
    if not cleaned:
        return {}
    candidates = [cleaned]
    if cleaned.startswith('"') and cleaned.endswith('"'):
        candidates.append(cleaned[1:-1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        flattened: dict[str, str] = {}
        _flatten_android_attribute_map(parsed, flattened)
        if flattened:
            return flattened
    return {}


def synthesize_android_derived_sku_records(
    device_records: list[AndroidMetaDeviceRecord],
) -> list[StellaDerivedSkuRecord]:
    records: list[StellaDerivedSkuRecord] = []
    seen: set[tuple[str, ...]] = set()
    model_hint_pattern = re.compile(
        r"(ray[\s-]?ban|meta|wayfarer|headliner|skyler|stories|oakley|sphaera|display|hud|rw40\d+)",
        re.IGNORECASE,
    )

    def pick_value(attribute_map: dict[str, str], *keys: str) -> str:
        for key in keys:
            value = attribute_map.get(key.lower(), "")
            if value:
                return value
        return ""

    for device in device_records:
        attribute_map = _parse_android_attributes(device.attributes)
        source_blob = " | ".join(
            part for part in (
                device.device_codename,
                device.source,
                device.capture_type,
                device.attributes,
            )
            if part
        )

        model = pick_value(
            attribute_map,
            "model",
            "model_name",
            "device_model",
            "glasses_model",
            "frametypedisplayname",
            "product_name",
            "display_name",
        )
        model_short_name = pick_value(
            attribute_map,
            "frame_type_short_display_name",
            "frametypeshortdisplayname",
            "model_short_name",
            "short_name",
            "short_display_name",
        )
        frame_style = pick_value(
            attribute_map,
            "frame_style",
            "framestyle",
            "frame_type",
            "frame",
        )
        frame_color_display_name = pick_value(
            attribute_map,
            "frame_color_display_name",
            "framecolordisplayname",
            "frame_color_name",
        )
        frame_color = pick_value(
            attribute_map,
            "frame_color",
            "framecolor",
        )
        lens_color_display_name = pick_value(
            attribute_map,
            "lens_color_display_name",
            "lenscolordisplayname",
            "lens_color_name",
        )
        lens_color = pick_value(
            attribute_map,
            "lens_color",
            "lenscolor",
        )
        serial = pick_value(
            attribute_map,
            "serial",
            "device_serial",
            "serial_number",
        ) or _clean_text(device.serial)

        if not any((model, model_short_name, frame_style)) and model_hint_pattern.search(source_blob):
            inferred = _clean_text(device.source) or _clean_text(device.device_codename)
            if inferred:
                model = inferred

        if not any((model, model_short_name, frame_style, frame_color_display_name, frame_color, lens_color_display_name, lens_color, serial)):
            continue

        key = (
            model,
            model_short_name,
            frame_style,
            frame_color_display_name,
            frame_color,
            lens_color_display_name,
            lens_color,
            serial,
            str(device.source_path),
        )
        if key in seen:
            continue
        seen.add(key)
        records.append(
            StellaDerivedSkuRecord(
                glasses_serial_number=serial,
                model=model,
                model_short_name=model_short_name,
                frame_style=frame_style,
                frame_color_display_name=frame_color_display_name,
                frame_color=frame_color,
                lens_color_display_name=lens_color_display_name,
                lens_color=lens_color,
                source_path=str(device.source_path),
            )
        )
    return records


def synthesize_android_case_settings_records(
    profile_records: list[AndroidMetaAppProfileRecord],
) -> list[StellaCaseSettingsRecord]:
    records: list[StellaCaseSettingsRecord] = []
    seen: set[tuple[str, ...]] = set()

    def _parse_eligibility_blob(blob_text: str) -> dict[str, str]:
        cleaned = _clean_text(blob_text)
        if not cleaned:
            return {}
        try:
            parsed = json.loads(cleaned)
        except Exception:
            return {}
        if not isinstance(parsed, dict):
            return {}
        return {str(key): _clean_text(value) for key, value in parsed.items()}

    for item in profile_records:
        eligibility = _parse_eligibility_blob(item.eligibility_subscription)
        glasses_device_id = (
            _clean_text(item.constellation_group_id)
            or _clean_text(item.social_profile_id)
            or _clean_text(item.user_id)
        )
        case_serial_number = _clean_text(item.abra_id) or _clean_text(item.abra_messaging_user_id)
        case_software_version = _clean_text(item.imported_data_source)
        key = (
            glasses_device_id,
            case_serial_number,
            case_software_version,
            _clean_text(item.fetched),
            str(item.source_path),
        )
        if key in seen:
            continue
        seen.add(key)
        records.append(
            StellaCaseSettingsRecord(
                glasses_device_id=glasses_device_id,
                case_serial_number=case_serial_number,
                case_software_version=case_software_version,
                last_settings_snapshot_time=_clean_text(item.fetched),
                has_completed_voice_oobe=eligibility.get("concord_nux_seen_version", ""),
                meta_ai_opt_in_completed=eligibility.get("is_eligible_for_c50_prompt_box", ""),
                meta_ai_geo_opt_in_completed=eligibility.get("is_eligible_for_c50_feed", ""),
                live_ai_eap_opt_in_status=eligibility.get("nme_gai_subscription_status", ""),
                default_provider_backward_compatibility_script_run=eligibility.get("eligible_for_c50", ""),
                default_provider_backward_compatibility_script_run_v2=eligibility.get("is_eligible_for_c50_wearables_tab", ""),
                show_language_reverted_notification=eligibility.get("nme_gai_subscription_product_name", ""),
                show_language_reverted_push_notification=eligibility.get("is_eligible_for_c50_prompt_history_tab", ""),
                source_path=str(item.source_path),
            )
        )
    return records


def synthesize_android_device_sync_records(
    sync_records: list[AndroidMetaSyncRecord],
    device_records: list[AndroidMetaDeviceRecord] | None = None,
) -> list[StellaDeviceSyncRecord]:
    records: list[StellaDeviceSyncRecord] = []
    seen: set[tuple[str, ...]] = set()
    capture_to_device: dict[str, AndroidMetaDeviceRecord] = {}
    for device in device_records or []:
        capture_id = _clean_text(device.example_capture_id)
        if capture_id and capture_id not in capture_to_device:
            capture_to_device[capture_id] = device

    for item in sync_records:
        related_device = capture_to_device.get(_clean_text(item.capture_id))
        glasses_device_id = (
            _clean_text(related_device.device_id) if related_device else ""
        ) or (
            _clean_text(related_device.pairing_id) if related_device else ""
        ) or _clean_text(item.capture_id) or _clean_text(item.session_id)
        app_version = _clean_text(item.processing_state)
        firmware_version = _clean_text(item.media_type)
        last_sync_time = (
            _clean_text(item.import_completed)
            or _clean_text(item.fetch_completed)
            or _clean_text(item.auto_saved)
            or _clean_text(item.capture_time)
        )
        key = (
            glasses_device_id,
            firmware_version,
            app_version,
            last_sync_time,
            str(item.source_path),
        )
        if key in seen:
            continue
        seen.add(key)
        records.append(
            StellaDeviceSyncRecord(
                glasses_device_id=glasses_device_id,
                glasses_firmware_version=firmware_version,
                app_version_at_last_sync=app_version,
                last_sync_time=last_sync_time,
                source_path=str(item.source_path),
            )
        )
    return records


def extract_android_prompts_from_interaction_log(path: Path, prompt_records: list[PromptRecord]):
    try:
        safe_path = str(path.resolve()).replace("\\", "/")
        db = sqlite3.connect(f"file:{safe_path}?mode=ro", uri=True)
    except Exception:
        return

    try:
        cursor = db.cursor()
        cursor.execute("""
            SELECT timestamp, interaction, event, event_data
            FROM entries
            ORDER BY timestamp
        """)
        seen: set[tuple[str, str, str]] = set()
        for timestamp, interaction, event, event_data in cursor.fetchall():
            event_name = _clean_text(event).lower()
            interaction_text = _clean_text(interaction)
            event_text = _clean_text(event_data)
            if not any(token in event_name for token in ("transcription", "prompt", "query", "utterance", "speech")):
                continue
            prompt_text = event_text or interaction_text
            if not prompt_text:
                continue
            prompt_ts = normalize_meta_timestamp(int(timestamp) / 1000) if timestamp else ""
            key = (prompt_ts, prompt_text.lower(), interaction_text.lower())
            if key in seen:
                continue
            seen.add(key)
            prompt_records.append(
                PromptRecord(
                    prompt_timestamp=prompt_ts,
                    response_timestamp="",
                    prompt_text=prompt_text,
                    response_text="",
                    prompt_line_number="",
                    response_line_number="",
                    source_path=str(path),
                )
            )
    except Exception:
        return
    finally:
        db.close()


def extract_android_prompts_from_graphql_cache(path: Path, prompt_records: list[PromptRecord]):
    base_name = path.name

    try:
        blob = path.read_bytes()
    except OSError:
        return

    strings = _readable_blob_strings(blob)
    if not strings:
        return

    turns = _extract_role_aware_turns(strings)
    if not turns:
        snippet_values = _unique_preserve(_next_value(strings, "snippets"))
        display_title = _clean_text(_first_clean(_next_value(strings, "display_titles")))
        short_candidates = [value for value in snippet_values if len(value) <= 120]
        prompt_text = short_candidates[0] if short_candidates else display_title
        if prompt_text:
            turns = [("USER", prompt_text)]

    seen_local: set[str] = set()
    for role, text_value in turns:
        if role and role != "USER":
            continue
        clean_value = _clean_text(text_value)
        if not clean_value:
            continue
        dedupe = _normalize_chat_key_part(clean_value)
        if dedupe in seen_local:
            continue
        seen_local.add(dedupe)
        prompt_records.append(
            PromptRecord(
                prompt_timestamp="",
                response_timestamp="",
                prompt_text=clean_value,
                response_text="",
                prompt_line_number="",
                response_line_number="",
                source_path=str(path),
            )
        )


def extract_android_prompts_from_sqlite_fallback(path: Path, prompt_records: list[PromptRecord]):
    try:
        safe_path = str(path.resolve()).replace("\\", "/")
        db = sqlite3.connect(f"file:{safe_path}?mode=ro", uri=True)
    except Exception:
        return

    prompt_column_hints = (
        "prompt",
        "query",
        "transcript",
        "transcription",
        "utterance",
        "snippet",
        "markdown",
        "display_title",
        "title",
        "message",
        "text",
    )
    timestamp_column_hints = ("timestamp", "time", "date", "created", "updated")
    seen: set[tuple[str, str, str]] = set()

    try:
        cursor = db.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        table_names = [row[0] for row in cursor.fetchall()]
        for table_name in table_names:
            lowered_table = _clean_text(table_name).lower()
            if not any(token in lowered_table for token in ("prompt", "query", "interaction", "history", "entry", "chat", "message", "meta")):
                continue
            try:
                columns = [row[1] for row in db.execute(f'PRAGMA table_info("{table_name}")').fetchall()]
            except Exception:
                continue
            prompt_columns = [column for column in columns if any(token in column.lower() for token in prompt_column_hints)]
            if not prompt_columns:
                continue
            timestamp_columns = [column for column in columns if any(token in column.lower() for token in timestamp_column_hints)]
            selected_columns = timestamp_columns[:1] + prompt_columns[:3]
            if not selected_columns:
                continue
            quoted_columns = ", ".join(f'"{column}"' for column in selected_columns)
            try:
                rows = db.execute(f'SELECT {quoted_columns} FROM "{table_name}" LIMIT 250').fetchall()
            except Exception:
                continue
            for row in rows:
                timestamp_value = ""
                prompt_text = ""
                for index, value in enumerate(row):
                    column_name = selected_columns[index]
                    clean_value = _clean_text(value)
                    if not clean_value:
                        continue
                    if not timestamp_value and column_name in timestamp_columns:
                        timestamp_value = _normalize_timestamp_ms(clean_value) or clean_value
                        continue
                    if column_name in prompt_columns and len(clean_value) >= 2:
                        prompt_text = clean_value
                        break
                if not prompt_text:
                    continue
                key = (_normalize_chat_key_part(timestamp_value), _normalize_chat_key_part(prompt_text), str(path))
                if key in seen:
                    continue
                seen.add(key)
                prompt_records.append(
                    PromptRecord(
                        prompt_timestamp=timestamp_value,
                        response_timestamp="",
                        prompt_text=prompt_text,
                        response_text="",
                        prompt_line_number="",
                        response_line_number="",
                        source_path=str(path),
                    )
                )
    except Exception:
        return
    finally:
        db.close()


def dedupe_prompt_records(prompt_records: list[PromptRecord]) -> list[PromptRecord]:
    deduped: list[PromptRecord] = []
    seen: set[tuple[str, str, str]] = set()
    for item in prompt_records:
        key = (
            _normalize_chat_key_part(item.prompt_timestamp),
            _normalize_chat_key_part(item.prompt_text),
            _normalize_chat_key_part(item.source_path),
        )
        if not key[1] or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped
