import datetime as dt
import hashlib
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any
import re


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", errors="replace")
        except Exception:
            return value.hex()
    if isinstance(value, (list, tuple, set)):
        return ", ".join(normalize_text(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=True, default=str)
    return str(value)


def basename(path: Path | str) -> str:
    return os.path.basename(str(path))


def device_id_from_path(path: Path | str) -> str:
    name = basename(path)
    marker = "-Namespace("
    if marker in name:
        return name.split(marker, 1)[0]
    return name


def suffix_key(full_key: str) -> str:
    return str(full_key).rsplit(")-", 1)[-1]


def normalize_meta_timestamp(value: Any) -> str:
    if isinstance(value, (int, float)) and value:
        try:
            return dt.datetime.utcfromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M:%S UTC")
        except Exception:
            return str(value)
    return normalize_text(value).strip()


def normalize_phone_identifier_value(value: Any) -> str:
    normalized = normalize_text(value).strip()
    if not normalized:
        return ""
    return normalized[:200]


def normalize_gps_coordinate(value: Any, axis: str) -> str:
    text = normalize_text(value).strip()
    if not text:
        return ""

    axis = str(axis or "").strip().lower()
    negative_markers = {"s"} if axis == "lat" else {"w"} if axis == "lon" else set()
    positive_markers = {"n"} if axis == "lat" else {"e"} if axis == "lon" else set()

    try:
        numeric_value = float(text)
        if axis == "lat" and -90.0 <= numeric_value <= 90.0:
            return f"{numeric_value:.8f}".rstrip("0").rstrip(".")
        if axis == "lon" and -180.0 <= numeric_value <= 180.0:
            return f"{numeric_value:.8f}".rstrip("0").rstrip(".")
    except Exception:
        pass

    lowered = text.lower()
    numbers = [float(match) for match in re.findall(r"[-+]?\d+(?:\.\d+)?", text)]
    if not numbers:
        return ""

    sign = -1.0 if any(marker in lowered for marker in negative_markers) else 1.0
    if any(marker in lowered for marker in positive_markers):
        sign = 1.0
    if text.lstrip().startswith("-"):
        sign = -1.0

    if len(numbers) >= 3:
        degrees, minutes, seconds = numbers[:3]
        absolute_value = abs(degrees) + (minutes / 60.0) + (seconds / 3600.0)
    elif len(numbers) == 2:
        degrees, minutes = numbers[:2]
        absolute_value = abs(degrees) + (minutes / 60.0)
    else:
        absolute_value = abs(numbers[0])

    numeric_value = absolute_value * sign
    if axis == "lat" and not (-90.0 <= numeric_value <= 90.0):
        return ""
    if axis == "lon" and not (-180.0 <= numeric_value <= 180.0):
        return ""
    return f"{numeric_value:.8f}".rstrip("0").rstrip(".")


def clean_log_string(value: str) -> str:
    return (
        value
        .replace('\\"', '"')
        .replace("\\'", "'")
        .replace("\\n", "\n")
        .replace("\\t", "\t")
    )


def device_entry(name: str, identifier: str = "", source: str = "", previously_connected: str = "Unknown", details: str = "") -> dict[str, str]:
    return {
        "name": str(name or "Unknown"),
        "identifier": str(identifier or ""),
        "source": str(source or ""),
        "previously_connected": str(previously_connected or "Unknown"),
        "details": str(details or ""),
    }


def normalize_source_path(path: Path | str) -> str:
    if not path:
        return ""
    normalized = str(path).strip()
    if os.name == "nt":
        normalized = normalized.replace("/", "\\")
    if normalized.startswith("\\\\?\\"):
        normalized = normalized[4:]
    return normalized


def extract_media_identifiers(raw_value: Any) -> tuple[str, str]:
    if not raw_value:
        return "", ""
    text = re.sub(r"\s+", " ", str(raw_value))
    patterns = {
        "make": (
            r"(?i)(?:^|[{\[,; ]|[\"'])make(?:[\"']?\s*[:=]\s*|[\"']\s*=>\s*[\"'])([^,;|}\]]+)",
            r"(?i)(?:^|[{\[,; ]|[\"'])tiff:make(?:[\"']?\s*[:=]\s*|[\"']\s*=>\s*[\"'])([^,;|}\]]+)",
        ),
        "model": (
            r"(?i)(?:^|[{\[,; ]|[\"'])model(?:[\"']?\s*[:=]\s*|[\"']\s*=>\s*[\"'])([^,;|}\]]+)",
            r"(?i)(?:^|[{\[,; ]|[\"'])tiff:model(?:[\"']?\s*[:=]\s*|[\"']\s*=>\s*[\"'])([^,;|}\]]+)",
        ),
    }

    def clean_match(value: str) -> str:
        value = str(value or "").strip().strip("\"'")
        value = re.split(r"(?<!\\)[|,;}\]]", value, 1)[0].strip()
        return value[:120]

    found = {"make": "", "model": ""}
    for field, field_patterns in patterns.items():
        for pattern in field_patterns:
            match = re.search(pattern, text)
            if match:
                cleaned = clean_match(match.group(1))
                if cleaned:
                    found[field] = cleaned
                    break
    return found["make"], found["model"]


def is_metaglasses_make_model(make: str, model: str) -> bool:
    normalized_make = str(make or "").strip().lower()
    normalized_model = str(model or "").strip().lower()

    exact_match = normalized_make == "meta ai" and normalized_model == "ray-ban meta smart glasses"
    if exact_match:
        return True

    make_is_meta = normalized_make in {"meta ai", "meta", "facebook", "fb"}
    model_aliases = (
        "ray-ban meta smart glasses",
        "ray-ban meta",
        "ray ban meta",
        "rayban meta",
        "rb meta",
        "rbsmartglasses",
        "rb smart glasses",
    )

    if make_is_meta and any(alias in normalized_model for alias in model_aliases):
        return True

    if ("meta" in normalized_make or "facebook" in normalized_make) and "smart glasses" in normalized_model:
        return True

    if normalized_model.startswith("rb") and "meta" in normalized_model:
        return True

    return False


def explain_media_hit(
    reasons_text: str,
    make: str = "",
    model: str = "",
    software: str = "",
    exif_datetime: str = "",
) -> str:
    tokens = [token.strip() for token in str(reasons_text or "").split(",") if token.strip()]
    explanations: list[str] = []
    embedded_source = ""
    embedded_blob = ""
    detected_datetime = str(exif_datetime or "").strip()

    for token in tokens:
        if token == "exact_exif_make_model_match":
            explanations.append("Direct EXIF make/model match to Meta Ray-Ban smart glasses.")
        elif token == "embedded_exif_make_model_match":
            explanations.append("Embedded EXIF text contains Meta/Ray-Ban smart glasses make/model identifiers.")
        elif token == "embedded_media_metadata_match":
            explanations.append("Embedded Photos metadata links this asset to Meta/Ray-Ban smart glasses media.")
        elif token.startswith("embedded_metadata_source:"):
            embedded_source = token.split(":", 1)[1].strip()
        elif token.startswith("embedded_metadata_blob:"):
            embedded_blob = token.split(":", 1)[1].strip()
        elif token.startswith("exif_datetime:") or token.startswith("embedded_exif_datetime:"):
            detected_datetime = token.split(":", 1)[1].strip()

    metadata_bits: list[str] = []
    if str(make or "").strip():
        metadata_bits.append(f"make '{str(make).strip()}'")
    if str(model or "").strip():
        metadata_bits.append(f"model '{str(model).strip()}'")
    if str(software or "").strip():
        metadata_bits.append(f"software '{str(software).strip()}'")
    if metadata_bits:
        explanations.append("Observed metadata: " + ", ".join(metadata_bits) + ".")

    if detected_datetime:
        explanations.append(f"Recovered EXIF date/time: {detected_datetime}.")

    if embedded_source or embedded_blob:
        source_bits: list[str] = []
        if embedded_source:
            source_bits.append(f"source artifact '{embedded_source}'")
        if embedded_blob:
            source_bits.append(f"metadata field '{embedded_blob}'")
        explanations.append("Embedded metadata context: " + ", ".join(source_bits) + ".")

    if explanations:
        return " ".join(explanations)
    if tokens:
        return "; ".join(tokens)
    return "Flagged as potentially related Meta/Ray-Ban smart glasses media."


def windows_safe_path(path: Path | str) -> str:
    path_str = str(path)
    if os.name != "nt":
        return path_str
    if path_str.startswith("\\\\?\\"):
        return path_str
    if path_str.startswith("\\\\"):
        return "\\\\?\\UNC\\" + path_str[2:]
    absolute = os.path.abspath(path_str)
    return "\\\\?\\" + absolute


def external_tool_path(path: Path | str) -> str:
    path_str = str(path)
    if os.name != "nt":
        return path_str
    if path_str.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path_str[8:]
    if path_str.startswith("\\\\?\\"):
        return path_str[4:]
    return path_str


def sanitize_windows_component(component: str, max_length: int = 120) -> str:
    invalid_chars = '<>:"/\\|?*'
    sanitized = "".join("_" if ch in invalid_chars or ord(ch) < 32 else ch for ch in component)
    sanitized = sanitized.rstrip(" .")
    if not sanitized:
        sanitized = "_"
    reserved_names = {
        "CON", "PRN", "AUX", "NUL",
        "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
        "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
    }
    if sanitized.upper() in reserved_names:
        sanitized = f"_{sanitized}"
    if len(sanitized) > max_length:
        suffix = hashlib.sha1(sanitized.encode("utf-8", errors="ignore")).hexdigest()[:8]
        sanitized = f"{sanitized[:max_length-9]}_{suffix}"
    return sanitized


def build_output_folder_name(app_name: str, case_number: str, timestamp: str) -> str:
    case_component = sanitize_windows_component(case_number.strip() or "NoCase")
    return f"{app_name}_Output_{case_component}_{timestamp}"


def build_safe_archive_path(base_dir: Path, member_name: str) -> Path | None:
    normalized = member_name.replace("\\", "/").lstrip("/")
    parts = [part for part in normalized.split("/") if part not in ("", ".")]
    safe_parts: list[str] = []
    for part in parts:
        if part == "..":
            continue
        safe_parts.append(sanitize_windows_component(part))
    if not safe_parts:
        return None
    return base_dir.joinpath(*safe_parts)


def ensure_parent_dir(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)


def cleanup_temp_dir(temp_dir: tempfile.TemporaryDirectory[str] | None):
    if temp_dir:
        temp_name = getattr(temp_dir, "name", "")
        try:
            temp_dir.cleanup()
            return
        except Exception:
            pass

        finalizer = getattr(temp_dir, "_finalizer", None)
        if finalizer:
            try:
                finalizer.detach()
            except Exception:
                pass

        for _ in range(3):
            try:
                if temp_name and os.path.exists(temp_name):
                    shutil.rmtree(temp_name, ignore_errors=True)
                break
            except Exception:
                time.sleep(0.2)


def decode_zip_extended_timestamp(extra_data: bytes) -> tuple[int | None, int | None]:
    atime = None
    mtime = None
    index = 0
    while index + 4 <= len(extra_data):
        header_id = int.from_bytes(extra_data[index:index + 2], "little")
        data_size = int.from_bytes(extra_data[index + 2:index + 4], "little")
        data_start = index + 4
        data_end = data_start + data_size
        if data_end > len(extra_data):
            break
        if header_id == 0x5455 and data_size >= 1:
            flags = extra_data[data_start]
            cursor = data_start + 1
            if flags & 0x1 and cursor + 4 <= data_end:
                mtime = int.from_bytes(extra_data[cursor:cursor + 4], "little")
                cursor += 4
            if flags & 0x2 and cursor + 4 <= data_end:
                atime = int.from_bytes(extra_data[cursor:cursor + 4], "little")
        index = data_end
    return atime, mtime
