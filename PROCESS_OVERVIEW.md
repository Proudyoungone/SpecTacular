# SpecTacular Process Overview

## Purpose

SpecTacular is a standalone parser focused on Meta smart glasses artifacts. It is designed to:

- scan a file system extraction or supported archive
- identify artifacts that are likely related to Meta / Ray-Ban smart glasses activity
- normalize those findings into reviewable JSON, Excel, and HTML outputs
- preserve enough source context to support validation and follow-up analysis

This document explains the end-to-end process from scan start through output generation, and why each output exists.

## High-Level Flow

At a high level, the process is:

1. Accept input path and output folder
2. Resolve ExifTool, if available
3. If the input is an archive, extract only relevant items to a temporary working folder
4. Enumerate candidate files
5. Scan those files for identifiers, account data, prompts, device artifacts, and media hits
6. Build a summary of findings
7. Write JSON outputs for structured machine-readable review
8. Write formatted Excel workbooks for analyst review
9. Copy relevant prompt/media files into `File_Hits`
10. Build the HTML report and field reference PDF

## Input Handling

SpecTacular accepts either:

- a folder containing an extracted file system or evidence set
- a supported archive such as `.zip`, `.tar`, `.tgz`, `.tar.gz`, or `.gz`

When the input is an archive, the parser does not blindly extract everything. It uses path and extension filters to pull out only likely-relevant items. This keeps the temporary extraction smaller and makes large archive processing more practical.

The archive filters prioritize:

- media file types
- Apple artifact paths such as `PhotoData`, `com.apple.*`, and key SQLite/plist locations
- Android / Meta application paths such as Stella databases and GraphQL cache folders

## Candidate File Discovery

Once the scan root is established, SpecTacular walks the directory tree and collects files that pass the relevance filter.

The candidate list is later constrained by `input_mode`:

- `apple`: favor Apple-style artifacts and skip Android-specific ones
- `android`: favor Android-specific artifacts and skip Apple-specific ones where appropriate

This keeps the scan targeted when the examiner already knows the source platform.

## Scanning Logic

### 1. Structured Artifact Scanning

Structured artifacts are parsed from:

- plist
- json
- txt / log / strings
- sqlite / db / sqlite3

The parser looks for values that appear glasses-related based on:

- relevant keywords in keys or values
- known Meta / Stella / Ray-Ban path hints
- normalized identifiers that look meaningful for device/account attribution

Structured scanning is used to populate:

- identifiers
- linked accounts
- Apple Stella records
- Android Meta app records
- prompt / response records

### 2. Apple-Focused Artifact Extraction

For Apple data, the scan looks at:

- Stella case settings plists
- Stella sync log plists
- Stella derived SKU plists
- linked-account plists
- `Photos.sqlite`-based embedded metadata
- selected Apple system artifacts such as Wi-Fi, Bluetooth, and `consolidated.db`

Why this matters:

- it recovers glasses-specific settings and hardware details
- it helps tie app usage back to a particular device pair
- it surfaces environmental context such as known Wi-Fi or Bluetooth peers

### 3. Android-Focused Artifact Extraction

For Android data, the scan looks at:

- Meta / Stella databases
- interaction logs
- GraphQL response cache artifacts
- selected Android configuration artifacts such as Bluetooth, Wi-Fi, and build properties

Why this matters:

- it surfaces account/profile details from the Meta app
- it identifies paired devices and sync state
- it extracts prompts and app-level interaction history

### 4. Media Hit Detection

Media detection happens in two paths:

#### Direct media scanning

If a file has a media extension, SpecTacular may call ExifTool and inspect metadata such as:

- make
- model
- software
- date/time
- GPS values

The parser then uses internal matching logic to determine whether the media appears related to Meta glasses activity.

#### Embedded media metadata scanning

For `Photos.sqlite`-style artifacts, SpecTacular also inspects embedded metadata blobs associated with assets. This can produce media hits even when the physical media file is not directly parsed as a normal camera file.

This is important because:

- some extractions preserve metadata context even if a matching media file is not easily surfaced
- embedded asset records can still indicate Meta glasses provenance

## Matching and Reasoning

### Media reasoning

Media hits are not meant to be raw “camera roll” exports. They are flagged because the parser found evidence suggesting they are related to Meta / Ray-Ban smart glasses.

Examples of evidence include:

- direct EXIF make/model match to Meta Ray-Ban smart glasses
- embedded EXIF text containing Meta / Ray-Ban smart glasses identifiers
- embedded Photos metadata linking the asset to glasses-related media

Internally, SpecTacular still uses a numeric ranking to sort hits, but the user-facing exports now explain the reason in plain language rather than exposing only the internal score.

### Identifier reasoning

Identifiers are recorded when a key/value pair looks relevant to glasses or account attribution. The goal is not to dump every value in the extraction, but to preserve values that are likely useful for:

- attribution
- timeline placement
- device correlation
- account linkage

### Account reasoning

Account records are extracted only when fields appear account-related and are not obviously credential/token noise. This helps reduce clutter while preserving values that may connect the device/app environment to a person or service identity.

## Deduplication

SpecTacular deduplicates certain findings so exports stay readable.

Examples:

- media records are deduped by a combination of media path and key EXIF identifiers
- account records are deduped by key/value pairs
- detected-device summary entries are deduped within their categories

This is done to reduce repetitive output while still preserving meaningful evidence.

## Why Topics May Show Multiple Results

SpecTacular intentionally preserves distinct recovered records instead of collapsing everything into a single guessed final value. Multiple results are often expected and may reflect different devices, different snapshots in time, or overlapping evidence from more than one artifact source.

### Case Settings

Multiple case-settings records may appear when:

- the phone was paired with more than one pair of glasses
- the app stored repeated settings snapshots over time instead of updating one row in place
- the same settings state was recovered from more than one artifact instance
- a migration, reset, or re-pairing event left older and newer settings states behind

### Device Sync Log

Multiple sync records may appear when:

- the app recorded more than one sync event across time
- separate glasses contributed separate sync histories
- sync-related timestamps such as firmware, app version, and last-sync time changed between snapshots
- Apple and Android sources preserved different portions of the same overall sync history

### Derived SKU Info

Multiple derived-SKU records may appear when:

- more than one pair of glasses was associated with the phone
- the same glasses were recorded in repeated model snapshots
- model, frame, or lens attributes differed slightly between artifact sources
- the parser preserved overlapping but non-identical model evidence instead of forcing a single result

### Linked Accounts

Multiple linked-account records may appear when:

- several account-related fields were recovered for the same user
- more than one account or social identity was associated with the app
- the same account appeared in multiple artifacts with different field names or formats
- profile refreshes over time preserved older and newer values

### Meta AI Prompts

Multiple prompt records may appear when:

- the user submitted multiple prompts over time
- prompt and response history was preserved across more than one file or database
- cached or repeated interaction records survived in separate artifact locations
- the parser preserved separate prompt events rather than collapsing similar text into one row

### Related Media Hits

Multiple media hits may appear when:

- multiple images or videos were taken with the glasses
- the same capture was recovered both as a physical media file and as embedded metadata context
- several files shared the same glasses EXIF make/model evidence
- derivative copies or exports of a captured item remained in the extraction

### Phone Identifiers

Multiple phone-identifier records may appear when:

- different identifiers were recovered, such as device name, serial, IMEI, ICCID, IMSI, model, or build values
- Apple or Android system artifacts preserved overlapping identity data in more than one place
- backup artifacts and live filesystem artifacts both contributed handset details
- the parser preserved separate identifiers rather than reducing them to one summary value

### Wi-Fi

Multiple Wi-Fi records may appear when:

- the device knew or observed multiple networks
- saved-network artifacts and scan-history artifacts both contributed results
- the same network appeared in more than one system or app artifact
- the phone was used across multiple locations or time periods

### Bluetooth

Multiple Bluetooth records may appear when:

- the device had more than one paired or observed Bluetooth peer
- adapter-level data and paired-device data were both recovered
- different artifacts preserved different timestamps or device names for the same peer
- connected-car or accessory records added additional Bluetooth context

### Media / Companion

Multiple media or companion-device records may appear when:

- more than one glasses-related media item was detected
- companion devices were identified from different artifacts or metadata sources
- EXIF-derived evidence and app-derived evidence both contributed entries
- the same accessory or companion context appeared across repeated records with different supporting details

## ExifTool Usage

If ExifTool is available, SpecTacular uses it to enrich media analysis and EXIF exports.

The current EXIF calls are intentionally broad and request a rich metadata set, including grouped and expanded metadata where possible. This supports:

- stronger media provenance analysis
- richer `media_hits_exif_full.xlsx` output
- better make/model matching for Meta glasses indicators

If ExifTool is not available or cannot reopen a specific file, SpecTacular still falls back to metadata already captured in the media record when possible.

## Output Types

SpecTacular writes three main output classes:

### 1. JSON outputs

Purpose:

- machine-readable
- script-friendly
- preserves structured values without spreadsheet formatting concerns

Examples:

- `summary.json`
- `identifiers.json`
- `metaai_prompts.json`
- `media_hits.json`
- device/account JSON exports

Why they exist:

- best for automation, downstream parsing, and exact value preservation

### 2. Excel outputs

Purpose:

- examiner-friendly tabular review
- filtering, sorting, and quick triage
- formatted for readability

Most tabular exports are standard row/column workbooks with:

- bold headers
- left-aligned wrapped cells
- borders
- frozen header row
- autofilter enabled

`media_hits_exif_full.xlsx` is special:

- row 1 contains source file names
- column A contains metadata field names
- the workbook is arranged vertically to make side-by-side EXIF comparison easier

Why they exist:

- easier for most analysts to review than raw JSON
- preserves the reasoning and context in a usable format

### 3. HTML report

Purpose:

- provide a consolidated human-readable review surface
- present summaries, selectors, previews, and grouped artifact sections

Why it exists:

- gives the examiner one place to start
- supports quick triage before drilling into Excel/JSON outputs

## File_Hits Export

SpecTacular also copies certain source files into `File_Hits`:

- prompt-related source files
- media files that were flagged as hits when those files exist on disk

Why this exists:

- it gives the examiner fast access to the source artifacts that produced the findings
- it reduces the need to manually relocate files from deep extraction paths

## Summary Output

The summary is meant to answer:

- what was found
- how many records were found in each category
- where the output lives
- what likely device relationships were inferred

It acts as the high-level entry point for the rest of the outputs.

## Important Interpretation Notes

- A hit means the parser found evidence of relevance, not that every item is conclusively glasses-origin media.
- Media hits should still be independently validated by the examiner.
- Embedded metadata-derived media hits can be especially valuable, but they may represent logical asset references rather than always pointing to a currently accessible physical file.
- Timestamps should be interpreted in artifact context and not assumed to be authoritative without cross-checking.

## Why the Tool Uses Multiple Output Formats

The outputs are intentionally split because each serves a different purpose:

- JSON supports exact structured review and automation
- Excel supports analyst triage and reporting workflows
- HTML supports narrative review, previewing, and quick browsing
- File_Hits supports direct evidence follow-up

Together, they provide:

- transparency about why a record was flagged
- traceability back to a source artifact
- practical review surfaces for both manual and scripted analysis

## Bottom Line

SpecTacular is designed to move from broad evidence scanning to focused, explainable outputs.

Its process is:

- narrow the evidence set
- detect likely Meta glasses artifacts
- normalize them into structured records
- explain why the parser thinks those records matter
- present the results in formats that support both fast review and deeper verification
