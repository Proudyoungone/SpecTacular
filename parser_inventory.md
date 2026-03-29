# SpecTacular Parser Inventory

This file inventories the iLEAPP/ALEAPP parser logic that SpecTacular currently relies on or mirrors, and where the copied or localized versions live inside SpecTacular.

## Copied From iLEAPP

### Apple Device / Identifier Parsers

- `CommCenter Device Specific`
  - Original family: iLEAPP device / CommCenter parsing
  - Copied version: `SpecTacular/scripts/ileapp_device_parsers.py::parse_commcenter_device_specific`
- `Preferences Plist`
  - Original family: iLEAPP `preferencesPlist.py`
  - Copied version: `SpecTacular/scripts/ileapp_device_parsers.py::parse_preferences_plist`
- `System Version Plist`
  - Original family: iLEAPP `systemVersionPlist.py`
  - Copied version: `SpecTacular/scripts/ileapp_device_parsers.py::parse_system_version_plist`
- `Device Values Plist`
  - Original family: iLEAPP `Ph100UFEDdevcievaluesplist.py`
  - Copied version: `SpecTacular/scripts/ileapp_device_parsers.py::parse_device_values_plist`
- `Advertising ID`
  - Original family: iLEAPP `advertisingID.py`
  - Copied version: `SpecTacular/scripts/ileapp_device_parsers.py::parse_advertising_id`
- `AirDrop ID`
  - Original family: iLEAPP `airdropId.py`
  - Copied version: `SpecTacular/scripts/ileapp_device_parsers.py::parse_airdrop_id`
- `Cellular Wireless`
  - Original family: iLEAPP `celWireless.py`
  - Copied version: `SpecTacular/scripts/ileapp_device_parsers.py::parse_cellular_wireless`
- `IMEI / IMSI`
  - Original family: iLEAPP `imeiImsi.py`
  - Copied version: `SpecTacular/scripts/ileapp_device_parsers.py::parse_imei_imsi`
- `Subscriber Info`
  - Original family: iLEAPP `subscriberInfo.py`
  - Copied version: `SpecTacular/scripts/ileapp_device_parsers.py::parse_subscriber_info`
- `Device Activator`
  - Original family: iLEAPP `deviceActivator.py`
  - Copied version: `SpecTacular/scripts/ileapp_device_parsers.py::parse_device_activator`
- `Device Name`
  - Original family: iLEAPP `deviceName.py`
  - Copied version: `SpecTacular/scripts/ileapp_device_parsers.py::parse_device_name`
- `Timezone Info`
  - Original family: iLEAPP `timezoneInfo.py`
  - Copied version: `SpecTacular/scripts/ileapp_device_parsers.py::parse_timezone_info`
- `iTunes Backup Info`
  - Original family: iLEAPP `iTunesBackupInfo.py`
  - Copied version: `SpecTacular/scripts/ileapp_device_parsers.py::parse_itunes_backup_info`

### Apple Bluetooth / Wi-Fi / Device-Correlation Parsers

- `Bluetooth Paired`
  - Original family: iLEAPP mobile Bluetooth parsing
  - Copied version: `SpecTacular/scripts/ileapp_device_parsers.py::parse_mobilebluetooth_devices`
- `Bluetooth Paired LE`
  - Original family: iLEAPP LE paired database parsing
  - Copied version: `SpecTacular/scripts/ileapp_device_parsers.py::parse_mobilebluetooth_paired_le`
- `Bluetooth Other LE`
  - Original family: iLEAPP LE other database parsing
  - Copied version: `SpecTacular/scripts/ileapp_device_parsers.py::parse_mobilebluetooth_other_le`
- `WiFi Known Networks`
  - Original family: iLEAPP Wi-Fi plist parsing
  - Copied version: `SpecTacular/scripts/ileapp_device_parsers.py::parse_wifi_known_networks`
- `WiFi Network Store Model`
  - Original family: iLEAPP Wi-Fi network store model parsing
  - Copied version: `SpecTacular/scripts/ileapp_device_parsers.py::parse_wifinetworkstoremodel`
- `LocationD Serial Number`
  - Original family: iLEAPP consolidated / locationd serial parsing
  - Copied version: `SpecTacular/scripts/ileapp_device_parsers.py::parse_consolidated_serials`

### Apple Meta Glasses Parsers

- `Stella Case Settings / Device Sync / Derived SKU`
  - Original family: iLEAPP Meta glasses parsers
  - Copied version: `SpecTacular/scripts/artifacts/stella.py`

## Copied From ALEAPP

### Android Device / Identifier Parsers

- `Settings Secure`
  - Original family: ALEAPP `settingsSecure.py`
  - Copied version: `SpecTacular/scripts/aleapp_device_parsers.py::parse_settings_secure`
- `Build Info`
  - Original family: ALEAPP `build.py`
  - Copied version: `SpecTacular/scripts/aleapp_device_parsers.py::parse_build_prop`
- `SIM Info`
  - Original family: ALEAPP `siminfo.py`
  - Copied version: `SpecTacular/scripts/aleapp_device_parsers.py::parse_siminfo`
- `ADB Hosts`
  - Original family: ALEAPP `adb_hosts.py`
  - Copied version: `SpecTacular/scripts/aleapp_device_parsers.py::parse_adb_hosts`
- `UsageStats Version`
  - Original family: ALEAPP `usagestatsVersion.py`
  - Copied version: `SpecTacular/scripts/aleapp_device_parsers.py::parse_usagestats_version`
- `ALEX Device Info`
  - Original family: ALEAPP `alexDeviceInfo.py`
  - Copied version: `SpecTacular/scripts/aleapp_device_parsers.py::parse_alex_device_info`

### Android Bluetooth / Wi-Fi / Vehicle Parsers

- `Bluetooth Connections`
  - Original family: ALEAPP `bluetoothConnections.py`
  - Copied version: `SpecTacular/scripts/aleapp_device_parsers.py::parse_bluetooth_connections`
- `WifiConfigStore`
  - Original family: ALEAPP `wifiConfigstore2.py`
  - Copied version: `SpecTacular/scripts/aleapp_device_parsers.py::parse_wifi_configstore2`
- `Wi-Fi Profiles`
  - Original family: ALEAPP `wifiProfiles.py`
  - Copied version: `SpecTacular/scripts/aleapp_device_parsers.py::parse_wifi_profiles`
- `Android Auto Connected Cars`
  - Original family: ALEAPP `androidauto.py`
  - Copied version: `SpecTacular/scripts/aleapp_device_parsers.py::parse_android_auto`
- `ABX Binary XML Support`
  - Original family: ALEAPP `ilapfuncs.py::abxread` and `checkabx`
  - Copied version: `SpecTacular/scripts/aleapp_abx.py`

## SpecTacular-Native Parser Modules

- `Android Meta app profile / device / sync extraction`
  - Local version: `SpecTacular/scripts/artifacts/meta_glasses_android.py::extract_android_meta_app_profiles`
  - Local version: `SpecTacular/scripts/artifacts/meta_glasses_android.py::extract_android_meta_devices_and_sync`
- `Android Meta AI prompt extraction`
  - Local version: `SpecTacular/scripts/artifacts/meta_glasses_android.py::extract_android_prompts_from_interaction_log`
  - Local version: `SpecTacular/scripts/artifacts/meta_glasses_android.py::extract_android_prompts_from_graphql_cache`
  - Local version: `SpecTacular/scripts/artifacts/meta_glasses_android.py::extract_android_prompts_from_sqlite_fallback`
  - Local version: `SpecTacular/scripts/artifacts/meta_glasses_android.py::dedupe_prompt_records`
- `Android Meta source detectors`
  - Local version: `SpecTacular/scripts/artifacts/meta_glasses_android.py::source_is_android_stella_db`
  - Local version: `SpecTacular/scripts/artifacts/meta_glasses_android.py::source_is_android_interaction_log_db`
  - Local version: `SpecTacular/scripts/artifacts/meta_glasses_android.py::source_is_android_graphql_cache`
- `Apple Stella source detectors`
  - Local version: `SpecTacular/scripts/artifacts/stella.py::source_is_stella_account_artifact`
  - Local version: `SpecTacular/scripts/artifacts/stella.py::source_is_stella_case_settings_artifact`
  - Local version: `SpecTacular/scripts/artifacts/stella.py::source_is_stella_sync_log_artifact`
  - Local version: `SpecTacular/scripts/artifacts/stella.py::source_is_stella_derived_sku_artifact`

## SpecTacular Orchestration

- `SpecTacular/scripts/scan_engine.py`
  - Central scan and synthesis layer that calls the copied/local parser functions above
- `SpecTacular/scripts/pipeline.py`
  - Candidate-file discovery, media export, and report-support pipeline logic
- `SpecTacular/scripts/report.py`
  - HTML report generation and examiner-facing presentation logic

## Non-Parser Remnants Noted In SpecTacular

- `SpecTacular/scripts/scan_engine.py`
  - Examiner-facing detail strings still say things like `Recovered from copied iLEAPP...` and `Recovered from copied ALEAPP...`
- `SpecTacular/version_info.py`
  - Still uses `ileapp_version` and `ileapp_contributors`
- `SpecTacular/html_parts.py`
  - Still contains visible `iLEAPP` branding/text, an iLEAPP GitHub link, iLEAPP contributor text, and an ALEAPP sample path

## Scope Note

This inventory focuses on parser modules and parser-family logic that SpecTacular currently uses or mirrors for artifact extraction. It does not attempt to list every utility helper, model dataclass, or UI/report helper in the project.
