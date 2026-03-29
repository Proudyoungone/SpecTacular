import html
import json
import os
import plistlib
from datetime import date, datetime
from hashlib import sha1
from pathlib import Path
from urllib.parse import quote

from scripts.html_parts import (
    body_end,
    body_main_data_title,
    body_main_trailer,
    body_spinner,
    body_start,
    default_responsive_table_script,
    nav_bar_script_footer,
    page_footer,
    page_header,
)


MAX_PREVIEW_BYTES = 262144
TEXT_PREVIEW_BYTES = 131072
_generated_previews = {}
VIEWER_FOLDER_NAME = '_HTML_Viewers'
VIEWER_BRAND = 'SpecTacular'
REPORT_HOME_HREF = '../report.html'
VIEWER_THEME = """
<style>
    body { background: #f4f1ea; }
    .viewer-shell { padding-bottom: 2rem; }
    .viewer-toolbar {
        background: linear-gradient(135deg, #23262d 0%, #384150 100%);
        color: #f5f7fa;
        border-radius: 14px;
        padding: 1rem 1.25rem;
        margin-bottom: 1rem;
        box-shadow: 0 10px 30px rgba(26, 31, 41, 0.18);
    }
    .viewer-toolbar a { color: #9fd3ff; }
    .viewer-badges { margin-top: 0.5rem; }
    .viewer-badge {
        display: inline-block;
        margin-right: 0.5rem;
        padding: 0.2rem 0.55rem;
        border-radius: 999px;
        font-size: 0.78rem;
        font-weight: 600;
        letter-spacing: 0.04em;
        text-transform: uppercase;
        background: rgba(255, 255, 255, 0.12);
    }
    .viewer-meta {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
        gap: 0.75rem;
        margin-bottom: 1rem;
    }
    .viewer-meta-card {
        background: #fffdf8;
        border: 1px solid #e5dccd;
        border-radius: 12px;
        padding: 0.85rem 1rem;
    }
    .viewer-meta-card .label {
        display: block;
        color: #7c6f60;
        font-size: 0.78rem;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        margin-bottom: 0.25rem;
    }
    .viewer-meta-card .value {
        color: #1f2933;
        word-break: break-word;
        font-family: Consolas, "Courier New", monospace;
        font-size: 0.92rem;
    }
    .viewer-panel {
        background: #ffffff;
        border: 1px solid #ddd3c3;
        border-radius: 14px;
        overflow: hidden;
        box-shadow: 0 8px 24px rgba(41, 34, 24, 0.08);
        margin-bottom: 1rem;
    }
    .viewer-panel-header {
        background: linear-gradient(180deg, #f5efe3 0%, #ede4d3 100%);
        border-bottom: 1px solid #ddd3c3;
        padding: 0.75rem 1rem;
    }
    .viewer-panel-header h5 {
        margin: 0;
        font-weight: 700;
        color: #2d2418;
    }
    .viewer-editor-wrap {
        background: #1f2430;
        overflow: auto;
        max-height: 72vh;
    }
    .viewer-editor {
        width: 100%;
        border-collapse: collapse;
        font-family: Consolas, "Courier New", monospace;
        font-size: 0.9rem;
        color: #e6edf3;
    }
    .viewer-editor td {
        vertical-align: top;
        padding: 0;
        white-space: pre-wrap;
        word-break: break-word;
    }
    .viewer-editor .line-no {
        width: 1%;
        min-width: 3.5rem;
        padding: 0.15rem 0.75rem;
        text-align: right;
        color: #7f8da3;
        background: #171b24;
        border-right: 1px solid #313847;
        user-select: none;
    }
    .viewer-editor .line-text {
        padding: 0.15rem 0.9rem;
    }
    .plist-tree { padding: 1rem; background: #fbfaf7; }
    .plist-tree details { margin-left: 1rem; padding-left: 0.35rem; border-left: 2px solid #e4d9c8; }
    .plist-tree summary { cursor: pointer; color: #2f3a46; font-family: Consolas, "Courier New", monospace; }
    .plist-key { color: #7c4d2d; font-weight: 700; }
    .plist-type { color: #7b8794; margin-left: 0.5rem; font-size: 0.82rem; text-transform: uppercase; }
    .plist-value { color: #1f2933; font-family: Consolas, "Courier New", monospace; }
</style>
"""


def _html_root(report_folder):
    normalized = os.path.normpath(report_folder)
    if os.path.basename(normalized).lower() == '_html':
        return normalized
    parent = os.path.dirname(normalized)
    if os.path.basename(parent).lower() == '_html':
        return parent
    return os.path.join(normalized, '_HTML')


def _report_root(report_folder):
    html_root = _html_root(report_folder)
    return os.path.dirname(html_root)


def _viewer_root(report_folder):
    return os.path.join(_report_root(report_folder), VIEWER_FOLDER_NAME)


def _viewer_relpath(viewer_name):
    return f'{VIEWER_FOLDER_NAME}/{viewer_name}'


def _viewer_template(template, asset_prefix='../_HTML/', home_href=REPORT_HOME_HREF):
    return (
        template
        .replace('_elements/', f'{asset_prefix}_elements/')
        .replace('href="index.html"', f'href="{home_href}"')
        .replace('<img src="_elements/iLEAPP_banner.png" alt="iLEAPP banner">', '')
    )


def _write_root_redirect(report_folder, viewer_name):
    return


def _normalize_path(path):
    if not path:
        return ''
    normalized = str(path).strip()
    if normalized.startswith('\\\\?\\'):
        normalized = normalized[4:]
    return normalized


def _path_to_file_uri(path):
    try:
        return Path(path).resolve(strict=False).as_uri()
    except (OSError, ValueError):
        normalized = path.replace('\\', '/')
        if normalized.startswith('//'):
            return f'file:{quote(normalized)}'
        return f'file:///{quote(normalized)}'


def _path_to_directory_uri(path):
    directory = path if os.path.isdir(path) else os.path.dirname(path)
    return _path_to_file_uri(directory)


def _make_preview_filename(source_path, kind):
    digest = sha1(f'{kind}:{_normalize_path(source_path)}'.encode('utf8')).hexdigest()[:12]
    return f'FileView_{kind}_{digest}.html'


def _write_page_start(handle, title, description):
    handle.write(_viewer_template(page_header).format(title))
    handle.write(_viewer_template(body_start).format(VIEWER_BRAND))
    handle.write('<main role="main" class="col-12 px-4">')
    handle.write(_viewer_template(body_main_data_title).format(title, description))
    handle.write(body_spinner)


def _write_page_end(handle):
    handle.write(_viewer_template(body_main_trailer + body_end + default_responsive_table_script + nav_bar_script_footer + page_footer))


def _serialize_value(value):
    if isinstance(value, dict):
        return {str(key): _serialize_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_serialize_value(item) for item in value]
    if isinstance(value, plistlib.UID):
        return {'type': 'UID', 'value': value.data}
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        preview = value[:64].hex()
        suffix = '' if len(value) <= 64 else '...'
        return f'<{len(value)} bytes: {preview}{suffix}>'
    if isinstance(value, Path):
        return str(value)
    return value


def _render_pre_block(handle, heading, content):
    handle.write(f'<div class="card bg-white mb-3"><div class="card-body"><h5 class="card-title">{html.escape(heading)}</h5>')
    handle.write(
        '<pre class="mb-0 p-3 border rounded bg-light" '
        'style="white-space: pre-wrap; word-break: break-word; max-height: 70vh; overflow: auto;">'
    )
    handle.write(html.escape(content))
    handle.write('</pre></div></div>')


def _render_editor_block(handle, heading, content):
    handle.write('<div class="viewer-panel">')
    handle.write(f'<div class="viewer-panel-header"><h5>{html.escape(heading)}</h5></div>')
    handle.write('<div class="viewer-editor-wrap"><table class="viewer-editor"><tbody>')
    lines = content.splitlines() or ['']
    for index, line in enumerate(lines, start=1):
        handle.write('<tr>')
        handle.write(f'<td class="line-no">{index}</td>')
        handle.write(f'<td class="line-text">{html.escape(line)}</td>')
        handle.write('</tr>')
    handle.write('</tbody></table></div></div>')


def _render_plist_tree_value(handle, value, key_name=''):
    type_name = type(value).__name__
    if isinstance(value, dict):
        label = html.escape(key_name) if key_name else 'root'
        handle.write(f'<details open><summary><span class="plist-key">{label}</span><span class="plist-type">dict</span></summary>')
        if not value:
            handle.write('<div class="plist-value">empty</div>')
        for child_key, child_value in value.items():
            _render_plist_tree_value(handle, child_value, str(child_key))
        handle.write('</details>')
        return
    if isinstance(value, list):
        label = html.escape(key_name) if key_name else 'root'
        handle.write(f'<details open><summary><span class="plist-key">{label}</span><span class="plist-type">array[{len(value)}]</span></summary>')
        if not value:
            handle.write('<div class="plist-value">empty</div>')
        for index, child_value in enumerate(value):
            _render_plist_tree_value(handle, child_value, f'[{index}]')
        handle.write('</details>')
        return

    label = html.escape(key_name) if key_name else 'value'
    value_text = '' if value in [None, 'N/A'] else str(value)
    handle.write(
        f'<div><span class="plist-key">{label}</span>'
        f'<span class="plist-type">{html.escape(type_name)}</span> '
        f'<span class="plist-value">{html.escape(value_text)}</span></div>'
    )


def _render_plist_tree(handle, value):
    handle.write('<div class="viewer-panel">')
    handle.write('<div class="viewer-panel-header"><h5>Plist Structure</h5></div>')
    handle.write('<div class="plist-tree">')
    _render_plist_tree_value(handle, value)
    handle.write('</div></div>')


def _render_metadata(handle, source_path):
    stat = os.stat(source_path)
    handle.write('<div class="viewer-meta">')
    handle.write('<div class="viewer-meta-card"><span class="label">Path</span>')
    handle.write(f'<span class="value">{html.escape(source_path)}</span></div>')
    handle.write('<div class="viewer-meta-card"><span class="label">Size</span>')
    handle.write(f'<span class="value">{stat.st_size:,} bytes</span></div>')
    handle.write('<div class="viewer-meta-card"><span class="label">Modified UTC</span>')
    handle.write(f'<span class="value">{datetime.utcfromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")}</span></div>')
    handle.write('<div class="viewer-meta-card"><span class="label">File Location</span>')
    handle.write(f'<span class="value"><a href="{html.escape(_path_to_directory_uri(source_path), quote=True)}">Open file location</a></span></div>')
    handle.write('</div>')


def _read_json_preview(source_path):
    with open(source_path, 'r', encoding='utf8') as handle:
        data = json.load(handle)
    return json.dumps(data, indent=2, ensure_ascii=True)


def _read_plist_preview(source_path):
    with open(source_path, 'rb') as handle:
        data = plistlib.load(handle)
    return data


def _read_other_preview(source_path):
    with open(source_path, 'rb') as handle:
        sample = handle.read(TEXT_PREVIEW_BYTES)

    if b'\x00' in sample:
        hex_preview = sample[:256].hex()
        return (
            'Binary file preview\n'
            f'First {min(len(sample), 256)} bytes (hex):\n{hex_preview}'
        )

    text = sample.decode('utf8', errors='replace')
    if os.path.getsize(source_path) > TEXT_PREVIEW_BYTES:
        text += '\n\n[Preview truncated]'
    return text


def generate_file_preview(report_folder, source_path, kind):
    normalized_path = _normalize_path(source_path)
    if not normalized_path or not os.path.exists(normalized_path):
        return None

    viewer_root = _viewer_root(report_folder)
    os.makedirs(viewer_root, exist_ok=True)
    cache_key = (os.path.normcase(normalized_path), kind, os.path.normcase(viewer_root))
    viewer_relpath = _generated_previews.get(cache_key)
    if viewer_relpath and os.path.exists(os.path.join(_report_root(report_folder), viewer_relpath)):
        return viewer_relpath

    viewer_name = _make_preview_filename(normalized_path, kind)
    viewer_relpath = _viewer_relpath(viewer_name)
    viewer_path = os.path.join(viewer_root, viewer_name)

    try:
        if kind == 'json':
            preview_text = _read_json_preview(normalized_path)
            description = 'Formatted JSON preview'
        elif kind == 'plist':
            preview_text = _read_plist_preview(normalized_path)
            description = 'Structured property list inspector'
        else:
            preview_text = _read_other_preview(normalized_path)
            description = 'Editor-style extracted file preview'
    except (OSError, ValueError, plistlib.InvalidFileException, json.JSONDecodeError) as ex:
        preview_text = f'Preview unavailable.\n\n{type(ex).__name__}: {ex}'
        description = 'Preview could not be generated'

    if kind == 'plist':
        preview_json = json.dumps(_serialize_value(preview_text), indent=2, ensure_ascii=True)
    else:
        preview_json = preview_text

    if len(preview_json.encode('utf8', errors='replace')) > MAX_PREVIEW_BYTES:
        preview_json = preview_json[:MAX_PREVIEW_BYTES] + '\n\n[Preview truncated]'

    with open(viewer_path, 'w', encoding='utf8') as handle:
        title = f'{kind.upper()} Explorer - {os.path.basename(normalized_path)}'
        _write_page_start(handle, title, description)
        handle.write(VIEWER_THEME)
        handle.write('<div class="viewer-shell">')
        handle.write('<div class="viewer-toolbar">')
        handle.write(f'<div><strong>{html.escape(os.path.basename(normalized_path))}</strong></div>')
        handle.write('<div class="viewer-badges">')
        handle.write(f'<span class="viewer-badge">{html.escape(kind)}</span>')
        handle.write('</div>')
        handle.write(f'<div class="mt-2"><a href="{REPORT_HOME_HREF}">Back to SpecTacular report</a></div>')
        handle.write('</div>')
        _render_metadata(handle, normalized_path)
        if kind == 'plist' and not isinstance(preview_text, str):
            _render_plist_tree(handle, preview_text)
            _render_editor_block(handle, 'Raw Structured View', preview_json)
        elif kind == 'json':
            _render_editor_block(handle, 'JSON Viewer', preview_json)
        else:
            _render_editor_block(handle, 'File Viewer', preview_json)
        handle.write('</div>')
        _write_page_end(handle)

    _write_root_redirect(report_folder, viewer_relpath)
    _generated_previews[cache_key] = viewer_relpath
    return viewer_relpath
