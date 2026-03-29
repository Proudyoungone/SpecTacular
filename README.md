# SpecTacular

SpecTacular is a small standalone parser for Meta smart glasses artifacts.

What it does:
- scans a folder recursively
- accepts archive input such as `.zip`, `.tar`, `.tgz`, `.tar.gz`, and `.gz`
- extracts identifiers from `plist`, `json`, `txt`, `log`, and `sqlite` files
- looks for prompt/response markers in log files
- finds likely related photos and videos
- writes an HTML report plus Excel (`.xlsx`) and JSON exports

What it does not do:
- it does not import or depend on `iLEAPP`
- it does not modify evidence files

## Usage

GUI:

```powershell
python SpecTacular.py
```

The GUI accepts either:
- an extracted or unzipped evidence folder
- a supported archive such as `.zip`, `.tar`, `.tgz`, `.tar.gz`, or `.gz`

You can also force GUI mode with:

```powershell
python SpecTacular.py --gui
```

CLI:

```powershell
python SpecTacular.py "C:\evidence\dump" "C:\evidence\SpecTacular_output"
```

You can also use an archive file as input:

```powershell
python SpecTacular.py "C:\evidence\dump.zip" "C:\evidence\SpecTacular_output"
```

Optional:

```powershell
python SpecTacular.py "C:\evidence\dump" "C:\evidence\SpecTacular_output" --exiftool "C:\tools\exiftool.exe"
```

If `exiftool.exe` exists in the same folder as `SpecTacular.py`, the parser will try to use it automatically.

## Output files

- `report.html`
- `summary.json`
- `identifiers.xlsx`
- `identifiers.json`
- `metaai_prompts.xlsx`
- `metaai_prompts.json`
- `stella_case_settings.xlsx`
- `stella_case_settings.json`
- `stella_device_sync_log.xlsx`
- `stella_device_sync_log.json`
- `stella_derived_sku_info.xlsx`
- `stella_derived_sku_info.json`
- `stella_app_profiles.xlsx`
- `stella_app_profiles.json`
- `stella_device_records.xlsx`
- `stella_device_records.json`
- `stella_sync_activity.xlsx`
- `stella_sync_activity.json`
- `android_meta_app_profiles.xlsx`
- `android_meta_app_profiles.json`
- `android_meta_devices.xlsx`
- `android_meta_devices.json`
- `android_meta_sync.xlsx`
- `android_meta_sync.json`
- `media_hits.xlsx`
- `media_hits.json`
- `media_hits_exif_full.xlsx`
- `stella_linked_accounts.xlsx`
- `stella_linked_accounts.json`

The Excel exports are formatted workbooks instead of plain CSV files.
`media_hits_exif_full.xlsx` is a special EXIF workbook with source files across the top row and metadata fields listed down column `A`.
The `stella_app_profiles`, `stella_device_records`, and `stella_sync_activity` files are the Android-facing exports in the same naming style as the Apple/Stella outputs. The older `android_meta_*` filenames are still written as compatibility copies.

## Current artifact focus

- `com.meta.mwa.glasses.userSettingsStore*.plist`
- `com.meta.mwa.dmcsynclog*.plist`
- `com.meta.mwa.derivedskuinfo*.plist`
- `com.stellaapp.fxlinkedaccountsstore.plist`
- `MetaAI-log-*.txt`

## Media matching

Media hits are flagged when the parser finds evidence such as:
- a direct EXIF make/model match to Meta Ray-Ban smart glasses
- embedded EXIF text containing Meta or Ray-Ban smart glasses identifiers
- embedded Photos metadata linking the asset to glasses-related media

The user-facing exports now explain why a media item was flagged instead of exposing only the internal numeric ranking.

This first version is meant to give you a clean foundation you can extend under your own project name.
