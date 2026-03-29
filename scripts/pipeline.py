import csv
import gzip
import html
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import zipfile
from xml.sax.saxutils import escape as xml_escape
from pathlib import Path
from typing import Any

from scripts.models import MediaRecord, PromptRecord
from scripts.utils import (
    basename,
    build_safe_archive_path,
    decode_zip_extended_timestamp,
    ensure_parent_dir,
    explain_media_hit,
    external_tool_path,
    extract_media_identifiers,
    is_metaglasses_make_model,
    normalize_gps_coordinate,
    normalize_text,
    sanitize_windows_component,
    windows_safe_path,
)


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

EXIFTOOL_RICH_ARGS = [
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
]

VIDEO_EXTENSIONS = {
    ".3gp",
    ".mov",
    ".mp4",
}

STRUCTURED_ARCHIVE_EXTENSIONS = {
    ".csv",
    ".db",
    ".json",
    ".log",
    ".plist",
    ".sqlite",
    ".sqlite3",
    ".strings",
    ".tsv",
    ".txt",
}

ARCHIVE_PATH_KEYWORDS = (
    "meta",
    "meta ai",
    "rayban",
    "ray-ban",
    "smart glasses",
    "stella",
    "wearable",
    "com.facebook.stellaapp",
    "com.meta.mwa",
    "glasses",
    "metaai-log-",
)

ANDROID_STELLA_DB_NAMES = {"stelladatabase", "stelladatabase.db"}
ANDROID_DEVICE_ARTIFACT_BASENAMES = {
    "settings_secure.xml",
    "bt_config.conf",
    "wificonfigstore.xml",
    "build.prop",
    "carservicedata.db",
    "telephony.db",
    "adb_keys",
    "version",
    "device_info_alex.json",
}
ANDROID_ARCHIVE_PATH_KEYWORDS = (
    "\\system\\users\\",
    "\\misc\\wifi\\",
    "\\apexdata\\com.android.wifi\\",
    "\\misc\\bluedroid\\",
    "\\misc\\adb\\",
    "\\com.google.android.projection.gearhead\\databases\\",
    "\\com.android.providers.telephony\\databases\\",
    "\\system\\usagestats\\",
    "\\system_ce\\",
    "\\vendor\\build.prop",
)
APPLE_ARCHIVE_PATH_KEYWORDS = (
    "\\private\\var\\",
    "\\mobile\\containers\\",
    "photodata\\",
    "com.apple.",
)
APPLE_ARCHIVE_BASENAMES = {
    "photos.sqlite",
    "wifinetworkstoremodel.sqlite",
    "consolidated.db",
    "systemversion.plist",
    "device_values.plist",
    "com.apple.lsdidentifiers.plist",
    "com.apple.sharingd.plist",
    "com.apple.commcenter.plist",
    "cellularusage.db",
    "ucrt_oob_request.txt",
    "data_ark.plist",
    "com.apple.commcenter.device_specific_nobackup.plist",
    "com.apple.mobilebluetooth.devices.plist",
    "com.apple.mobilebluetooth.ledevices.paired.db",
    "com.apple.wifi.plist",
    "com.apple.wifi.known-networks.plist",
}


def archive_member_is_relevant(member_name: str) -> bool:
    normalized = str(member_name or "").replace("/", "\\").lower()
    base_name = Path(str(member_name or "")).name.lower()
    suffix = Path(base_name).suffix.lower()

    if not normalized or normalized.startswith("__macosx\\"):
        return False
    if suffix in MEDIA_EXTENSIONS:
        return True
    if base_name in ANDROID_STELLA_DB_NAMES or base_name == "interaction_log.db":
        return True
    if base_name in ANDROID_DEVICE_ARTIFACT_BASENAMES:
        return True
    if suffix in STRUCTURED_ARCHIVE_EXTENSIONS and any(keyword in normalized for keyword in ANDROID_ARCHIVE_PATH_KEYWORDS):
        return True
    if "\\graphql_response_cache\\companion-ar\\" in normalized and base_name.startswith("p3%3a"):
        return True
    if base_name in APPLE_ARCHIVE_BASENAMES:
        return True
    if normalized.endswith("\\preferences\\systemconfiguration\\preferences.plist"):
        return True
    if normalized.endswith("\\mobile\\library\\preferences\\com.apple.appstore.plist"):
        return True
    if base_name == "info.plist" and ("\\manifest.plist" in normalized or "\\status.plist" in normalized or "\\info.plist" == normalized[-11:]):
        return True
    if suffix in STRUCTURED_ARCHIVE_EXTENSIONS and any(keyword in normalized for keyword in APPLE_ARCHIVE_PATH_KEYWORDS):
        return True
    if suffix in STRUCTURED_ARCHIVE_EXTENSIONS and any(keyword in normalized for keyword in ARCHIVE_PATH_KEYWORDS):
        return True
    return False


def _write_media_kml_fallback(path: Path, media_records: list[MediaRecord]) -> bool:
    entries: list[MediaRecord] = []
    for record in media_records:
        latitude = normalize_gps_coordinate(record.latitude, "lat")
        longitude = normalize_gps_coordinate(record.longitude, "lon")
        if latitude and longitude:
            record.latitude = latitude
            record.longitude = longitude
            entries.append(record)
    if not entries:
        return False

    path.parent.mkdir(parents=True, exist_ok=True)
    placemarks: list[str] = []
    for record in entries:
        name = Path(record.report_copy_path or record.media_path).name
        description_parts = []
        if record.reasons:
            description_parts.append(
                explain_media_hit(
                    record.reasons,
                    make=record.exif_make,
                    model=record.exif_model,
                    software=record.exif_software,
                    exif_datetime=record.exif_datetime,
                )
            )
        if record.exif_datetime:
            description_parts.append(f"Date/Time: {record.exif_datetime}")
        description_parts.append(f"Source: {record.media_path}")
        description = html.escape("\n".join(description_parts))
        placemarks.append(
            "    <Placemark>\n"
            f"      <name>{html.escape(name)}</name>\n"
            f"      <description>{description}</description>\n"
            "      <Point>\n"
            f"        <coordinates>{html.escape(str(record.longitude))},{html.escape(str(record.latitude))},0</coordinates>\n"
            "      </Point>\n"
            "    </Placemark>"
        )

    document = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<kml xmlns="http://www.opengis.net/kml/2.2">\n'
        "  <Document>\n"
        f"{chr(10).join(placemarks)}\n"
        "  </Document>\n"
        "</kml>\n"
    )
    with open(windows_safe_path(path), "w", encoding="utf-8", newline="") as handle:
        handle.write(document)
    return True


def _build_preview_output_path(base_dir: Path, source_path: Path) -> Path:
    stem = sanitize_windows_component(source_path.stem or "preview")
    candidate = base_dir / f"{stem}.jpg"
    if not candidate.exists():
        return candidate

    counter = 2
    while True:
        candidate = base_dir / f"{stem}_{counter}.jpg"
        if not candidate.exists():
            return candidate
        counter += 1


def _generate_image_preview(source_path: Path, preview_root: Path) -> str:
    suffix = source_path.suffix.lower()
    if suffix not in {".heic", ".heif"}:
        return ""

    try:
        from PIL import Image
        from pillow_heif import register_heif_opener

        register_heif_opener()
        preview_path = _build_preview_output_path(preview_root, source_path)
        with Image.open(windows_safe_path(source_path)) as image:
            converted = image.convert("RGB")
            converted.save(windows_safe_path(preview_path), format="JPEG", quality=92)
        return str(preview_path)
    except Exception:
        return ""


def emit_log(log_callback, message: str):
    if log_callback:
        log_callback(message)


def extract_zip_safely(archive_path: Path, extracted_root: Path, progress_callback=None):
    with zipfile.ZipFile(windows_safe_path(archive_path), "r") as archive:
        extracted_count = 0
        inspected_count = 0
        for member in archive.infolist():
            inspected_count += 1
            if member.filename.startswith("__MACOSX"):
                continue
            if member.is_dir():
                continue
            if not archive_member_is_relevant(member.filename):
                if progress_callback and (inspected_count == 1 or inspected_count % 1000 == 0):
                    progress_callback(extracted_count, 0, f"Extracting relevant archive items ({inspected_count:,} inspected)")
                continue
            target_path = build_safe_archive_path(extracted_root, member.filename)
            if target_path is None:
                continue
            try:
                ensure_parent_dir(target_path)
                with archive.open(member, "r") as src, open(windows_safe_path(target_path), "wb") as dst:
                    shutil.copyfileobj(src, dst)
                _creation_time, modification_time = decode_zip_extended_timestamp(member.extra)
                zip_mtime = modification_time
                if zip_mtime is None:
                    try:
                        zip_mtime = time.mktime(member.date_time + (0, 0, -1))
                    except Exception:
                        zip_mtime = None
                if zip_mtime:
                    os.utime(windows_safe_path(target_path), (zip_mtime, zip_mtime))
                extracted_count += 1
                if progress_callback and (extracted_count == 1 or extracted_count % 100 == 0 or inspected_count % 1000 == 0):
                    progress_callback(extracted_count, 0, f"Extracting relevant archive items ({inspected_count:,} inspected)")
            except Exception:
                continue
        if progress_callback:
            progress_callback(extracted_count, 0, f"Extracting relevant archive items ({inspected_count:,} inspected)")


def extract_tar_safely(archive_path: Path, extracted_root: Path, progress_callback=None):
    with tarfile.open(windows_safe_path(archive_path), "r:*") as archive:
        extracted_count = 0
        inspected_count = 0
        for member in archive.getmembers():
            inspected_count += 1
            if member.isdir():
                continue
            if not archive_member_is_relevant(member.name):
                if progress_callback and (inspected_count == 1 or inspected_count % 1000 == 0):
                    progress_callback(extracted_count, 0, f"Extracting relevant archive items ({inspected_count:,} inspected)")
                continue
            target_path = build_safe_archive_path(extracted_root, member.name)
            if target_path is None:
                continue
            try:
                if not member.isfile():
                    continue
                ensure_parent_dir(target_path)
                extracted_file = archive.extractfile(member)
                if extracted_file is None:
                    continue
                with extracted_file as src, open(windows_safe_path(target_path), "wb") as dst:
                    shutil.copyfileobj(src, dst)
                if member.mtime:
                    os.utime(windows_safe_path(target_path), (member.mtime, member.mtime))
                extracted_count += 1
                if progress_callback and (extracted_count == 1 or extracted_count % 100 == 0 or inspected_count % 1000 == 0):
                    progress_callback(extracted_count, 0, f"Extracting relevant archive items ({inspected_count:,} inspected)")
            except Exception:
                continue
        if progress_callback:
            progress_callback(extracted_count, 0, f"Extracting relevant archive items ({inspected_count:,} inspected)")


def locate_exiftool(cli_arg: str | None, repo_root: Path) -> str | None:
    if cli_arg and Path(cli_arg).exists():
        return cli_arg

    same_folder_candidate = Path(__file__).resolve().parents[1] / "exiftool.exe"
    if same_folder_candidate.exists():
        return str(same_folder_candidate)

    local_candidate = repo_root / "exiftool.exe"
    if local_candidate.exists():
        return str(local_candidate)

    return None


def is_supported_archive(path: Path) -> bool:
    lowered_name = path.name.lower()
    return (
        lowered_name.endswith(".zip")
        or lowered_name.endswith(".tar")
        or lowered_name.endswith(".tgz")
        or lowered_name.endswith(".tar.gz")
        or lowered_name.endswith(".gz")
    )


def extract_archive_to_temp(archive_path: Path, progress_callback=None) -> tuple[Path, tempfile.TemporaryDirectory[str]]:
    temp_base = Path.home()
    temp_dir = tempfile.TemporaryDirectory(prefix="mgp_", dir=str(temp_base))
    extracted_root = Path(temp_dir.name)
    lowered_name = archive_path.name.lower()

    try:
        if lowered_name.endswith(".zip"):
            extract_zip_safely(archive_path, extracted_root, progress_callback=progress_callback)
        elif lowered_name.endswith(".tar") or lowered_name.endswith(".tgz") or lowered_name.endswith(".tar.gz"):
            extract_tar_safely(archive_path, extracted_root, progress_callback=progress_callback)
        elif lowered_name.endswith(".gz"):
            output_path = extracted_root / sanitize_windows_component(archive_path.stem)
            with gzip.open(windows_safe_path(archive_path), "rb") as src, open(windows_safe_path(output_path), "wb") as dst:
                shutil.copyfileobj(src, dst)
            if progress_callback:
                progress_callback(1, 0, "Extracting archive")
        else:
            raise ValueError(f"Unsupported archive type: {archive_path}")
    except Exception:
        temp_dir.cleanup()
        raise

    return extracted_root, temp_dir


def read_exif_metadata(media_path: Path, exiftool_path: str | None) -> dict[str, str]:
    if not exiftool_path:
        return {}

    try:
        completed = subprocess.run(
            [external_tool_path(exiftool_path), *EXIFTOOL_RICH_ARGS, external_tool_path(media_path)],
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
                "Latitude": normalize_gps_coordinate(first.get("GPSLatitude", ""), "lat"),
                "Longitude": normalize_gps_coordinate(first.get("GPSLongitude", ""), "lon"),
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
    *,
    safe_suffix_func,
    progress_callback=None,
    progress_start: int = 0,
    progress_total: int = 0,
) -> list[MediaRecord]:
    media_records: list[MediaRecord] = []
    for index, path in enumerate(files, start=1):
        try:
            suffix = safe_suffix_func(path)
            if suffix not in CAPTURE_MEDIA_EXTENSIONS:
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
                    exif_datetime=exif_data.get("DateTime", ""),
                    latitude=exif_data.get("Latitude", ""),
                    longitude=exif_data.get("Longitude", ""),
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


def write_csv(path: Path, rows: list[dict[str, Any]]):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        with open(windows_safe_path(path), "w", newline="", encoding="utf-8") as handle:
            handle.write("")
        return

    fieldnames: list[str] = []
    seen_fields: set[str] = set()
    for row in rows:
        for key in row.keys():
            if key in seen_fields:
                continue
            seen_fields.add(key)
            fieldnames.append(key)

    with open(windows_safe_path(path), "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_table_xlsx(path: Path, rows: list[dict[str, Any]], sheet_name: str = "Export"):
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    seen_fields: set[str] = set()
    for row in rows:
        for key in row.keys():
            if key in seen_fields:
                continue
            seen_fields.add(key)
            fieldnames.append(key)

    sheet_rows: list[str] = []
    header_cells = []
    if fieldnames:
        for col_index, field in enumerate(fieldnames, start=1):
            header_cells.append(_xlsx_inline_cell(f"{_xlsx_column_name(col_index)}1", field, 1))
        sheet_rows.append(f'<row r="1">{"".join(header_cells)}</row>')

        for row_index, row in enumerate(rows, start=2):
            cells = []
            for col_index, field in enumerate(fieldnames, start=1):
                cells.append(_xlsx_inline_cell(f"{_xlsx_column_name(col_index)}{row_index}", row.get(field, ""), 2))
            sheet_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')

    cols_xml_parts = []
    if fieldnames:
        for col_index, field in enumerate(fieldnames, start=1):
            max_len = len(str(field))
            for row in rows[:250]:
                max_len = max(max_len, len(str(row.get(field, ""))))
            width = max(14, min(48, max_len + 3))
            cols_xml_parts.append(f'<col min="{col_index}" max="{col_index}" width="{width}" customWidth="1"/>')

    last_col = _xlsx_column_name(len(fieldnames)) if fieldnames else "A"
    last_row = len(rows) + 1 if fieldnames else 1
    dimension_ref = f"A1:{last_col}{last_row}"
    safe_sheet_name = _xlsx_safe_text(sheet_name or "Export").strip()
    safe_sheet_name = re.sub(r'[:\\/*?\[\]]', "_", safe_sheet_name)
    safe_sheet_name = safe_sheet_name.strip("'")
    safe_sheet_name = (safe_sheet_name[:31] or "Export")
    safe_sheet_name = xml_escape(safe_sheet_name)
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="{dimension_ref}"/>'
        '<sheetViews><sheetView workbookViewId="0">'
        '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
        '<selection pane="bottomLeft" activeCell="A2" sqref="A2"/>'
        '</sheetView></sheetViews>'
        '<sheetFormatPr defaultRowHeight="15"/>'
        + (f'<cols>{"".join(cols_xml_parts)}</cols>' if cols_xml_parts else '')
        +
        '<sheetData>'
        + "".join(sheet_rows) +
        '</sheetData>'
        + (f'<autoFilter ref="A1:{last_col}{last_row}"/>' if fieldnames else '')
        +
        '</worksheet>'
    )

    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="2">'
        '<font><sz val="11"/><name val="Calibri"/><family val="2"/></font>'
        '<font><b/><sz val="11"/><name val="Calibri"/><family val="2"/></font>'
        '</fonts>'
        '<fills count="3">'
        '<fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FFD9EAF7"/><bgColor indexed="64"/></patternFill></fill>'
        '</fills>'
        '<borders count="2">'
        '<border><left/><right/><top/><bottom/><diagonal/></border>'
        '<border><left style="thin"><color auto="1"/></left><right style="thin"><color auto="1"/></right><top style="thin"><color auto="1"/></top><bottom style="thin"><color auto="1"/></bottom><diagonal/></border>'
        '</borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="3">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment horizontal="left" vertical="top" wrapText="1"/></xf>'
        '<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="left" vertical="top" wrapText="1"/></xf>'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1"><alignment horizontal="left" vertical="top" wrapText="1"/></xf>'
        '</cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        '</styleSheet>'
    )

    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{safe_sheet_name}" sheetId="1" r:id="rId1"/></sheets>'
        '</workbook>'
    )

    workbook_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        '</Relationships>'
    )

    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>'
    )

    content_types_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '</Types>'
    )

    with zipfile.ZipFile(windows_safe_path(path), "w", compression=zipfile.ZIP_DEFLATED) as workbook_zip:
        workbook_zip.writestr("[Content_Types].xml", content_types_xml)
        workbook_zip.writestr("_rels/.rels", rels_xml)
        workbook_zip.writestr("xl/workbook.xml", workbook_xml)
        workbook_zip.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml)
        workbook_zip.writestr("xl/worksheets/sheet1.xml", sheet_xml)
        workbook_zip.writestr("xl/styles.xml", styles_xml)


def write_vertical_csv(path: Path, rows: list[dict[str, Any]], top_left_label: str = "Field"):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        with open(windows_safe_path(path), "w", newline="", encoding="utf-8") as handle:
            handle.write("")
        return

    column_headers: list[str] = []
    for row in rows:
        column_headers.append(str(row.get("SourceFile", "")))

    fieldnames: list[str] = []
    seen_fields: set[str] = set()
    for row in rows:
        for key in row.keys():
            if key == "SourceFile" or key in seen_fields:
                continue
            seen_fields.add(key)
            fieldnames.append(key)

    with open(windows_safe_path(path), "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([top_left_label, *column_headers])
        for field in fieldnames:
            writer.writerow([field, *[str(row.get(field, "")) for row in rows]])


def _xlsx_column_name(index: int) -> str:
    name = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _xlsx_safe_text(value: Any) -> str:
    text = normalize_text(value)
    if not text:
        return ""
    return "".join(
        ch for ch in text
        if ch in ("\t", "\n", "\r") or ord(ch) >= 32
    )


def _xlsx_inline_cell(cell_ref: str, value: Any, style_id: int) -> str:
    safe_text = xml_escape(_xlsx_safe_text(value))
    return (
        f'<c r="{cell_ref}" t="inlineStr" s="{style_id}">'
        f"<is><t>{safe_text}</t></is>"
        f"</c>"
    )


def write_vertical_xlsx(path: Path, rows: list[dict[str, Any]], top_left_label: str = "Field"):
    path.parent.mkdir(parents=True, exist_ok=True)
    column_headers: list[str] = []
    seen_header_counts: dict[str, int] = {}
    for row in rows:
        base_header = str(row.get("SourceFileName") or row.get("SourceFile") or "").strip()
        if not base_header:
            base_header = "Source"
        count = seen_header_counts.get(base_header, 0) + 1
        seen_header_counts[base_header] = count
        column_headers.append(base_header if count == 1 else f"{base_header} ({count})")

    fieldnames: list[str] = []
    seen_fields: set[str] = set()
    for row in rows:
        for key in row.keys():
            if key == "SourceFile" or key in seen_fields:
                continue
            seen_fields.add(key)
            fieldnames.append(key)

    sheet_rows: list[str] = []
    if column_headers or fieldnames:
        header_cells = [_xlsx_inline_cell("A1", top_left_label, 1)]
        for col_index, header_value in enumerate(column_headers, start=2):
            header_cells.append(_xlsx_inline_cell(f"{_xlsx_column_name(col_index)}1", header_value, 1))
        sheet_rows.append(f'<row r="1">{"".join(header_cells)}</row>')

        for row_index, field in enumerate(fieldnames, start=2):
            cells = [_xlsx_inline_cell(f"A{row_index}", field, 1)]
            for col_index, row in enumerate(rows, start=2):
                cells.append(_xlsx_inline_cell(f"{_xlsx_column_name(col_index)}{row_index}", row.get(field, ""), 2))
            sheet_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')

    last_col = _xlsx_column_name(len(column_headers) + 1) if (column_headers or fieldnames) else "A"
    last_row = len(fieldnames) + 1 if (column_headers or fieldnames) else 1
    dimension_ref = f"A1:{last_col}{last_row}"
    cols_xml_parts = []
    if column_headers or fieldnames:
        cols_xml_parts.append('<col min="1" max="1" width="28" customWidth="1"/>')
        for col_index, header_value in enumerate(column_headers, start=2):
            width = max(18, min(48, len(str(header_value)) + 4))
            cols_xml_parts.append(f'<col min="{col_index}" max="{col_index}" width="{width}" customWidth="1"/>')
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="{dimension_ref}"/>'
        '<sheetViews><sheetView workbookViewId="0">'
        '<pane xSplit="1" ySplit="1" topLeftCell="B2" activePane="bottomRight" state="frozen"/>'
        '<selection pane="bottomRight" activeCell="B2" sqref="B2"/>'
        '</sheetView></sheetViews>'
        '<sheetFormatPr defaultRowHeight="15"/>'
        + (f'<cols>{"".join(cols_xml_parts)}</cols>' if cols_xml_parts else '')
        +
        '<sheetData>'
        + "".join(sheet_rows) +
        '</sheetData>'
        '</worksheet>'
    )

    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="2">'
        '<font><sz val="11"/><name val="Calibri"/><family val="2"/></font>'
        '<font><b/><sz val="11"/><name val="Calibri"/><family val="2"/></font>'
        '</fonts>'
        '<fills count="3">'
        '<fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FFD9EAF7"/><bgColor indexed="64"/></patternFill></fill>'
        '</fills>'
        '<borders count="2">'
        '<border><left/><right/><top/><bottom/><diagonal/></border>'
        '<border><left style="thin"><color auto="1"/></left><right style="thin"><color auto="1"/></right><top style="thin"><color auto="1"/></top><bottom style="thin"><color auto="1"/></bottom><diagonal/></border>'
        '</borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="3">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment horizontal="left" vertical="top" wrapText="1"/></xf>'
        '<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="left" vertical="top" wrapText="1"/></xf>'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1"><alignment horizontal="left" vertical="top" wrapText="1"/></xf>'
        '</cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        '</styleSheet>'
    )

    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="EXIF Full" sheetId="1" r:id="rId1"/></sheets>'
        '</workbook>'
    )

    workbook_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        '</Relationships>'
    )

    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>'
    )

    content_types_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '</Types>'
    )

    with zipfile.ZipFile(windows_safe_path(path), "w", compression=zipfile.ZIP_DEFLATED) as workbook_zip:
        workbook_zip.writestr("[Content_Types].xml", content_types_xml)
        workbook_zip.writestr("_rels/.rels", rels_xml)
        workbook_zip.writestr("xl/workbook.xml", workbook_xml)
        workbook_zip.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml)
        workbook_zip.writestr("xl/worksheets/sheet1.xml", sheet_xml)
        workbook_zip.writestr("xl/styles.xml", styles_xml)


def media_record_export_row(record: MediaRecord) -> dict[str, str]:
    return {
        "media_path": record.media_path,
        "extension": record.extension,
        "why_flagged": explain_media_hit(
            record.reasons,
            make=record.exif_make,
            model=record.exif_model,
            software=record.exif_software,
            exif_datetime=record.exif_datetime,
        ),
        "exif_make": record.exif_make,
        "exif_model": record.exif_model,
        "exif_software": record.exif_software,
        "exif_datetime": record.exif_datetime,
        "latitude": record.latitude,
        "longitude": record.longitude,
        "report_copy_path": record.report_copy_path,
        "report_preview_path": record.report_preview_path,
    }


def _flatten_exif_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return json.dumps(list(value), ensure_ascii=True, default=str)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=True, default=str)
    return normalize_text(value)


def _flatten_exif_record(payload: dict[str, Any], prefix: str = ""):
    flattened: dict[str, str] = {}
    for key, value in payload.items():
        key_text = str(key)
        next_prefix = f"{prefix}.{key_text}" if prefix else key_text
        if isinstance(value, dict):
            flattened.update(_flatten_exif_record(value, next_prefix))
        else:
            flattened[next_prefix] = _flatten_exif_value(value)
    return flattened


def _run_exiftool_json(exiftool_path: str, source_paths: list[str]) -> list[dict[str, Any]]:
    if not source_paths:
        return []

    completed = subprocess.run(
        [external_tool_path(exiftool_path), *EXIFTOOL_RICH_ARGS, *[external_tool_path(item) for item in source_paths]],
        capture_output=True,
        text=True,
        check=False,
    )
    stdout_text = (completed.stdout or "").strip()
    if not stdout_text:
        return []

    try:
        payload = json.loads(stdout_text)
    except Exception:
        return []

    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def export_media_exif_csv(path: Path, media_records: list[MediaRecord], exiftool_path: str | None) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not media_records:
        write_csv(path, [])
        return 0

    fallback_rows: list[dict[str, str]] = []
    fallback_by_path: dict[str, dict[str, str]] = {}
    for record in media_records:
        candidate = record.report_copy_path or record.media_path
        normalized_candidate = os.path.normcase(os.path.abspath(candidate)) if candidate else ""
        fallback_row = {
            "SourceFile": candidate or record.media_path,
            "SourceFileName": basename(candidate or record.media_path) if (candidate or record.media_path) else "",
            "MediaPath": record.media_path,
            "ReportCopyPath": record.report_copy_path,
            "ReportPreviewPath": record.report_preview_path,
            "Extension": record.extension,
            "WhyFlagged": explain_media_hit(
                record.reasons,
                make=record.exif_make,
                model=record.exif_model,
                software=record.exif_software,
                exif_datetime=record.exif_datetime,
            ),
            "Make": record.exif_make,
            "Model": record.exif_model,
            "Software": record.exif_software,
            "DateTime": record.exif_datetime,
            "GPSLatitude": record.latitude,
            "GPSLongitude": record.longitude,
            "ExifExportSource": "media_record_fallback",
        }
        fallback_rows.append(fallback_row)
        if normalized_candidate:
            fallback_by_path[normalized_candidate] = fallback_row

    if not exiftool_path:
        write_vertical_xlsx(path, fallback_rows)
        return len(fallback_rows)

    source_paths: list[str] = []
    seen_paths: set[str] = set()
    for record in media_records:
        candidate = record.report_copy_path or record.media_path
        if not candidate or not os.path.exists(candidate):
            continue
        normalized = os.path.normcase(os.path.abspath(candidate))
        if normalized in seen_paths:
            continue
        seen_paths.add(normalized)
        source_paths.append(candidate)

    if not source_paths:
        write_vertical_xlsx(path, fallback_rows)
        return len(fallback_rows)

    rows: list[dict[str, str]] = []
    exported_paths: set[str] = set()
    chunk_size = 100
    for start in range(0, len(source_paths), chunk_size):
        chunk = source_paths[start:start + chunk_size]
        payload = _run_exiftool_json(exiftool_path, chunk)
        if not payload and len(chunk) > 1:
            for source_path in chunk:
                payload.extend(_run_exiftool_json(exiftool_path, [source_path]))
        for item in payload:
            flattened = _flatten_exif_record(item)
            source_file = flattened.get("SourceFile", "")
            flattened["SourceFileName"] = basename(source_file) if source_file else ""
            normalized_source = os.path.normcase(os.path.abspath(source_file)) if source_file else ""
            fallback_row = fallback_by_path.get(normalized_source)
            if fallback_row:
                merged = dict(fallback_row)
                merged.update(flattened)
                merged["ExifExportSource"] = "exiftool"
                rows.append(merged)
                exported_paths.add(normalized_source)
            else:
                flattened["ExifExportSource"] = "exiftool"
                rows.append(flattened)

    for normalized_candidate, fallback_row in fallback_by_path.items():
        if normalized_candidate not in exported_paths:
            rows.append(dict(fallback_row))

    write_vertical_xlsx(path, rows)
    return len(rows)


def write_json(path: Path, payload: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(windows_safe_path(path), "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=True)


def build_unique_output_path(base_dir: Path, source_path: Path) -> Path:
    safe_name = sanitize_windows_component(source_path.name or "file")
    candidate = base_dir / safe_name
    if not candidate.exists():
        return candidate

    stem = sanitize_windows_component(source_path.stem or "file")
    suffix = source_path.suffix
    counter = 2
    while True:
        candidate = base_dir / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def export_hit_files(
    output_root: Path,
    prompt_records: list[PromptRecord],
    media_records: list[MediaRecord],
    log_callback=None,
) -> dict[str, int]:
    file_hits_root = output_root / "File_Hits"
    prompt_hits_root = file_hits_root / "Prompt_Hits"
    media_hits_root = file_hits_root / "Media_Hits"
    media_preview_root = media_hits_root / "_previews"
    copied_prompt_files = 0
    copied_media_files = 0

    prompt_hits_root.mkdir(parents=True, exist_ok=True)
    media_hits_root.mkdir(parents=True, exist_ok=True)
    media_preview_root.mkdir(parents=True, exist_ok=True)

    seen_prompt_paths: set[str] = set()
    for record in prompt_records:
        source = record.source_path.strip()
        if not source or source in seen_prompt_paths:
            continue
        seen_prompt_paths.add(source)
        source_path = Path(source)
        if not source_path.exists():
            continue
        try:
            target_path = build_unique_output_path(prompt_hits_root, source_path)
            shutil.copy2(windows_safe_path(source_path), windows_safe_path(target_path))
            copied_prompt_files += 1
        except Exception:
            continue

    seen_media_paths: dict[str, tuple[str, str]] = {}
    for record in media_records:
        source = record.media_path.strip()
        if not source:
            continue
        existing_copy = seen_media_paths.get(source)
        if existing_copy:
            record.report_copy_path, record.report_preview_path = existing_copy
            continue
        source_path = Path(source)
        if not source_path.exists():
            continue
        try:
            target_path = build_unique_output_path(media_hits_root, source_path)
            shutil.copy2(windows_safe_path(source_path), windows_safe_path(target_path))
            record.report_copy_path = str(target_path)
            record.report_preview_path = _generate_image_preview(target_path, media_preview_root)
            seen_media_paths[source] = (record.report_copy_path, record.report_preview_path)
            copied_media_files += 1
        except Exception:
            continue

    emit_log(
        log_callback,
        f"Exported File_Hits: {copied_prompt_files} prompt files, {copied_media_files} media files",
    )
    return {
        "prompt_files_copied": copied_prompt_files,
        "media_files_copied": copied_media_files,
    }


def write_media_kml(path: Path, media_records: list[MediaRecord], exiftool_path: str | None):
    entries: list[MediaRecord] = []
    for record in media_records:
        latitude = normalize_gps_coordinate(record.latitude, "lat")
        longitude = normalize_gps_coordinate(record.longitude, "lon")
        if latitude and longitude:
            record.latitude = latitude
            record.longitude = longitude
            entries.append(record)
    if not entries:
        return False

    output_root = path.parent.parent
    if not exiftool_path:
        return _write_media_kml_fallback(path, media_records)

    still_fmt_path = Path(__file__).resolve().parents[1] / "kml.fmt"
    track_fmt_path = Path(__file__).resolve().parents[1] / "kml_track.fmt"
    if not still_fmt_path.exists() or not track_fmt_path.exists():
        return _write_media_kml_fallback(path, media_records)

    source_paths: list[str] = []
    for record in entries:
        candidate = record.report_copy_path or record.media_path
        if not candidate:
            continue
        if os.path.exists(candidate):
            source_paths.append(candidate)
    if not source_paths:
        return _write_media_kml_fallback(path, media_records)

    still_paths = [item for item in source_paths if Path(item).suffix.lower() not in VIDEO_EXTENSIONS]
    video_paths = [item for item in source_paths if Path(item).suffix.lower() in VIDEO_EXTENSIONS]

    def run_exiftool_kml(format_path: Path, target_paths: list[str], *, include_embedded: bool) -> str:
        if not target_paths:
            return ""
        command = [
            external_tool_path(exiftool_path),
            "-m",
        ]
        if include_embedded:
            command.append("-ee")
        command.extend(
            [
                "-if",
                "$gpslatitude and $gpslongitude",
                "-n",
                "-p",
                external_tool_path(format_path),
            ]
        )
        command.extend(external_tool_path(item) for item in target_paths)
        completed = subprocess.run(
            command,
            cwd=external_tool_path(output_root),
            capture_output=True,
            text=True,
            check=True,
        )
        return completed.stdout

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        placemark_chunks: list[str] = []
        for raw_output in (
            run_exiftool_kml(still_fmt_path, still_paths, include_embedded=False),
            run_exiftool_kml(track_fmt_path, video_paths, include_embedded=True),
        ):
            placemark_chunks.extend(re.findall(r"<Placemark>.*?</Placemark>", raw_output, flags=re.DOTALL))

        if placemark_chunks:
            document = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<kml xmlns="http://earth.google.com/kml/2.0">\n'
                "  <Document>\n"
                f"{chr(10).join(f'    {chunk}' for chunk in placemark_chunks)}\n"
                "  </Document>\n"
                "</kml>\n"
            )
            with open(windows_safe_path(path), "w", encoding="utf-8", newline="") as handle:
                handle.write(document)
            return True
    except Exception:
        pass
    return _write_media_kml_fallback(path, media_records)
