import html
import json
import os
import re
import sqlite3
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


MAX_TABLE_ROWS = 200
_generated_viewers = {}
VIEWER_FOLDER_NAME = '_HTML_Viewers'
VIEWER_BRAND = 'SpecTacular'
REPORT_HOME_HREF = '../report.html'
VIEWER_MAIN_HEADER = """
            <main role="main" class="col-12 px-4">
"""
VIEWER_THEME = """
<style>
    body { background: #eef1f5; }
    .db-shell { padding-bottom: 2rem; }
    .db-toolbar {
        background: linear-gradient(135deg, #1d2733 0%, #31455c 100%);
        color: #f7fafc;
        border-radius: 16px;
        padding: 1rem 1.25rem;
        margin-bottom: 1rem;
        box-shadow: 0 12px 30px rgba(24, 36, 52, 0.18);
    }
    .db-toolbar a { color: #8ed0ff; }
    .db-stat-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
        gap: 0.75rem;
        margin-bottom: 1rem;
    }
    .db-stat-card {
        background: #ffffff;
        border: 1px solid #d7dde6;
        border-radius: 12px;
        padding: 0.85rem 1rem;
        box-shadow: 0 6px 18px rgba(40, 52, 69, 0.06);
    }
    .db-stat-card .label {
        display: block;
        color: #6a7787;
        font-size: 0.78rem;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        margin-bottom: 0.2rem;
    }
    .db-stat-card .value {
        color: #1e2a36;
        font-weight: 700;
        word-break: break-word;
    }
    .db-grid {
        display: grid;
        grid-template-columns: minmax(260px, 320px) minmax(0, 1fr);
        gap: 1rem;
        align-items: start;
    }
    .db-panel {
        background: #ffffff;
        border: 1px solid #d7dde6;
        border-radius: 14px;
        overflow: hidden;
        box-shadow: 0 6px 18px rgba(40, 52, 69, 0.06);
        margin-bottom: 1rem;
    }
    .db-panel-header {
        background: linear-gradient(180deg, #f7f9fb 0%, #edf2f7 100%);
        border-bottom: 1px solid #d7dde6;
        padding: 0.85rem 1rem;
    }
    .db-panel-header h5 {
        margin: 0;
        color: #1f2c3a;
        font-weight: 700;
    }
    .db-panel-body { padding: 1rem; }
    .db-object-list {
        list-style: none;
        margin: 0;
        padding: 0;
        max-height: 70vh;
        overflow: auto;
    }
    .db-object-list li { border-bottom: 1px solid #edf2f7; }
    .db-object-link {
        display: block;
        padding: 0.8rem 1rem;
        color: #22384d;
        text-decoration: none;
    }
    .db-object-link:hover { background: #f7fafc; text-decoration: none; }
    .db-object-type {
        display: inline-block;
        min-width: 3.5rem;
        margin-right: 0.6rem;
        color: #6a7787;
        font-size: 0.76rem;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
    .db-table-wrap { overflow: auto; }
    .db-table-wrap table { margin-bottom: 0; }
    .db-table-wrap thead th {
        position: sticky;
        top: 0;
        background: #f2f6fb;
        z-index: 2;
    }
    .db-data-table td, .db-data-table th {
        white-space: nowrap;
        max-width: 420px;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    .db-sql-block {
        background: #1f2430;
        color: #d8dee9;
        border-radius: 10px;
        padding: 0.85rem 1rem;
        font-family: Consolas, "Courier New", monospace;
        white-space: pre-wrap;
        word-break: break-word;
    }
    @media (max-width: 991px) {
        .db-grid { grid-template-columns: 1fr; }
    }
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


def _quote_identifier(value):
    return '"' + str(value).replace('"', '""') + '"'


def _format_sqlite_error(error):
    message = str(error).strip()
    return message or error.__class__.__name__


def _make_safe_filename(name, max_length=40):
    safe = re.sub(r'[^A-Za-z0-9._-]+', '_', str(name)).strip('._')
    return (safe or 'table')[:max_length]


def _json_safe_value(value):
    if isinstance(value, bytes):
        return {'type': 'bytes', 'hex': value.hex()}
    if isinstance(value, bytearray):
        return {'type': 'bytes', 'hex': bytes(value).hex()}
    if isinstance(value, memoryview):
        return {'type': 'bytes', 'hex': value.tobytes().hex()}
    if isinstance(value, dict):
        return {str(key): _json_safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe_value(item) for item in value]
    return value


def _db_hash(source_path):
    return sha1(_normalize_path(source_path).encode('utf8')).hexdigest()[:12]


def _table_filename(source_path, table_name):
    safe_name = _make_safe_filename(table_name)
    table_hash = sha1(str(table_name).encode('utf8')).hexdigest()[:8]
    return f'DBView_{_db_hash(source_path)}_{safe_name}_{table_hash}.html'


def _viewer_filename(source_path):
    return f'DBView_{_db_hash(source_path)}.html'


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


def _open_db(path):
    normalized = _normalize_path(path)
    if not normalized or not os.path.exists(normalized):
        return None

    try:
        return sqlite3.connect(f'file:{normalized}?mode=ro', uri=True)
    except sqlite3.Error:
        return None


def _is_sqlite_path(path):
    db = _open_db(path)
    if not db:
        return False

    try:
        cursor = db.cursor()
        cursor.execute("SELECT name FROM sqlite_master LIMIT 1")
        return True
    except sqlite3.Error:
        return False
    finally:
        db.close()


def _fetch_schema(db):
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT type, name, IFNULL(tbl_name, ''), IFNULL(sql, '')
        FROM sqlite_master
        WHERE type IN ('table', 'view', 'trigger')
          AND name NOT LIKE 'sqlite_%'
        ORDER BY
            CASE type
                WHEN 'table' THEN 1
                WHEN 'view' THEN 2
                WHEN 'trigger' THEN 3
                ELSE 4
            END,
            name
        """
    )
    return cursor.fetchall()


def _count_rows(db, table_name):
    try:
        cursor = db.cursor()
        cursor.execute(f'SELECT COUNT(*) FROM {_quote_identifier(table_name)}')
        row = cursor.fetchone()
        return row[0] if row else 0
    except sqlite3.Error:
        return 'N/A'


def _fetch_columns(db, table_name):
    cursor = db.cursor()
    try:
        cursor.execute(f'PRAGMA table_info({_quote_identifier(table_name)})')
        return cursor.fetchall(), None
    except sqlite3.Error as error:
        return [], _format_sqlite_error(error)


def _fetch_indexes(db, table_name):
    cursor = db.cursor()
    indexes = []
    try:
        cursor.execute(f'PRAGMA index_list({_quote_identifier(table_name)})')
        for seq, name, is_unique, origin, partial in cursor.fetchall():
            cursor.execute(f'PRAGMA index_info({_quote_identifier(name)})')
            columns = [row[2] for row in cursor.fetchall()]
            indexes.append({
                'name': name,
                'unique': bool(is_unique),
                'origin': origin,
                'partial': bool(partial),
                'columns': columns,
            })
    except sqlite3.Error as error:
        return [], _format_sqlite_error(error)
    return indexes, None


def _fetch_triggers(schema_rows, table_name):
    triggers = []
    for obj_type, name, trigger_table, definition in schema_rows:
        if obj_type == 'trigger' and trigger_table == table_name:
            triggers.append({
                'name': name,
                'table': trigger_table,
                'definition': definition,
            })
    return triggers


def _fetch_preview_rows(db, table_name, limit=MAX_TABLE_ROWS):
    cursor = db.cursor()
    try:
        cursor.execute(f'SELECT rowid AS __ileapp_rowid__, * FROM {_quote_identifier(table_name)} LIMIT {limit}')
        return [desc[0] for desc in cursor.description], cursor.fetchall(), True, None
    except sqlite3.Error:
        try:
            cursor.execute(f'SELECT * FROM {_quote_identifier(table_name)} LIMIT {limit}')
            return [desc[0] for desc in cursor.description], cursor.fetchall(), False, None
        except sqlite3.Error as error:
            return [], [], False, _format_sqlite_error(error)


def _write_page_start(handle, title, description):
    handle.write(_viewer_template(page_header).format(title))
    handle.write(_viewer_template(body_start).format(VIEWER_BRAND))
    handle.write(VIEWER_MAIN_HEADER)
    handle.write(_viewer_template(body_main_data_title).format(title, description))
    handle.write(body_spinner)
    handle.write(VIEWER_THEME)


def _write_page_end(handle):
    handle.write(_viewer_template(body_main_trailer + body_end + default_responsive_table_script + nav_bar_script_footer + page_footer))


def _write_table(handle, headers, rows, table_id, row_metadata=None):
    metadata = row_metadata or []
    table_class = 'table table-striped table-bordered table-xsm'
    if table_id.startswith('tbl_'):
        table_class += ' db-data-table'
    handle.write(f'<div class="db-table-wrap"><table id="{html.escape(table_id)}" class="{table_class}" cellspacing="0"><thead><tr>')
    for header in headers:
        handle.write(f'<th class="th-sm">{html.escape(str(header))}</th>')
    handle.write('</tr></thead><tbody>')
    for index, row in enumerate(rows):
        row_id = f'row-{index + 1}'
        row_attrs = [f'id="{html.escape(row_id, quote=True)}"']
        if index < len(metadata):
            if metadata[index].get('rowid') not in [None, 'N/A']:
                row_attrs.append(f'data-rowid="{html.escape(str(metadata[index]["rowid"]), quote=True)}"')
            if metadata[index].get('pk_values'):
                safe_pk_values = _json_safe_value(metadata[index]['pk_values'])
                row_attrs.append(f'data-pk="{html.escape(json.dumps(safe_pk_values), quote=True)}"')
        handle.write(f'<tr {" ".join(row_attrs)}>')
        for cell in row:
            handle.write(f'<td>{html.escape("" if cell in [None, "N/A"] else str(cell))}</td>')
        handle.write('</tr>')
    handle.write('</tbody></table></div>')


def _write_columns_table(handle, columns_info):
    headers = ('Name', 'Type', 'Role', 'Required', 'Default')
    rows = []
    for cid, name, col_type, not_null, default_value, pk in columns_info:
        role = 'Primary key' if pk else 'Standard'
        rows.append((name, col_type or '', role, 'Yes' if not_null else 'No', default_value or ''))
    _write_table(handle, headers, rows, 'dbViewerColumns')


def _write_definition_block(handle, title, definition):
    if not definition:
        return
    escaped_title = html.escape(str(title))
    escaped_definition = html.escape(str(definition))
    handle.write(f'<div class="db-panel"><div class="db-panel-header"><h5>{escaped_title}</h5></div>')
    handle.write(f'<div class="db-panel-body"><div class="db-sql-block">{escaped_definition}</div></div></div>')


def _write_overview_stats(handle, source_path, schema_rows):
    tables = [row for row in schema_rows if row[0] == 'table']
    views = [row for row in schema_rows if row[0] == 'view']
    triggers = [row for row in schema_rows if row[0] == 'trigger']
    handle.write('<div class="db-stat-grid">')
    for label, value in [
        ('Database', os.path.basename(source_path)),
        ('Tables', len(tables)),
        ('Views', len(views)),
        ('Triggers', len(triggers)),
    ]:
        handle.write('<div class="db-stat-card">')
        handle.write(f'<span class="label">{html.escape(str(label))}</span>')
        handle.write(f'<span class="value">{html.escape(str(value))}</span>')
        handle.write('</div>')
    handle.write('</div>')


def _write_object_browser(handle, source_path, schema_rows):
    handle.write('<div class="db-panel"><div class="db-panel-header"><h5>Object Browser</h5></div><ul class="db-object-list">')
    for obj_type, name, _, _ in schema_rows:
        if obj_type == 'trigger':
            continue
        handle.write(
            f'<li><a class="db-object-link" href="{html.escape(_table_filename(source_path, name), quote=True)}">'
            f'<span class="db-object-type">{html.escape(str(obj_type))}</span>{html.escape(str(name))}</a></li>'
        )
    handle.write('</ul></div>')


def _write_columns_summary(handle, columns_info):
    total_columns = len(columns_info)
    pk_columns = [column[1] for column in columns_info if column[5]]
    required_columns = [column[1] for column in columns_info if column[3]]
    typed_columns = [column[1] for column in columns_info if column[2]]

    handle.write('<div class="card bg-white mb-3" style="max-width: 920px;"><div class="card-body">')
    handle.write('<h5 class="card-title">Column Summary</h5>')
    handle.write('<p class="mb-2">')
    handle.write(f'<strong>Total columns:</strong> {total_columns}<br />')
    handle.write(f'<strong>Primary key columns:</strong> {html.escape(", ".join(pk_columns) if pk_columns else "None")}<br />')
    handle.write(f'<strong>Required columns:</strong> {html.escape(", ".join(required_columns) if required_columns else "None")}<br />')
    handle.write(f'<strong>Typed columns:</strong> {len(typed_columns)}')
    handle.write('</p></div></div>')


def _write_indexes_table(handle, indexes):
    if not indexes:
        handle.write('<p class="lead">No indexes reported for this object.</p>')
        return
    headers = ('Name', 'Unique', 'Origin', 'Partial', 'Columns')
    rows = []
    for index in indexes:
        column_names = [str(column) for column in index['columns'] if column not in [None, '']]
        rows.append((
            index['name'],
            index['unique'],
            index['origin'],
            index['partial'],
            ', '.join(column_names) if column_names else '[expression or unnamed column]',
        ))
    _write_table(handle, headers, rows, 'dbViewerIndexes')


def _write_triggers_table(handle, triggers):
    if not triggers:
        handle.write('<p class="lead">No triggers reported for this object.</p>')
        return
    headers = ('Name', 'Target Table')
    rows = []
    for trigger in triggers:
        rows.append((trigger['name'], trigger['table']))
    _write_table(handle, headers, rows, 'dbViewerTriggers')


def _write_filter_controls(handle, table_id, searchable_columns):
    options = ''.join(
        f'<option value="{html.escape(str(index), quote=True)}">{html.escape(column)}</option>'
        for index, column in searchable_columns
    )
    handle.write(
        f"""
        <div class="card bg-white mb-3" style="max-width: 920px;">
            <div class="card-body">
                <h5 class="card-title">Filter and Jump</h5>
                <div class="form-row">
                    <div class="col-md-4 mb-2">
                        <input type="text" id="{table_id}_search" class="form-control" placeholder="Global search" />
                    </div>
                    <div class="col-md-3 mb-2">
                        <select id="{table_id}_column" class="custom-select">
                            <option value="">Column filter</option>
                            {options}
                        </select>
                    </div>
                    <div class="col-md-3 mb-2">
                        <input type="text" id="{table_id}_column_value" class="form-control" placeholder="Column value" />
                    </div>
                    <div class="col-md-2 mb-2">
                        <button type="button" id="{table_id}_apply" class="btn btn-primary btn-sm btn-block">Apply</button>
                    </div>
                </div>
                <p class="mb-0">
                    Use <code>?row=7</code> or <code>?rowid=123</code> in the page URL to jump to a preview row.
                </p>
            </div>
        </div>
        """
    )


def _write_table_navigation_script(handle, table_id):
    handle.write(
        f"""
        <script>
            $(document).ready(function() {{
                var table = $('#{table_id}').DataTable();
                var globalSearch = document.getElementById('{table_id}_search');
                var columnSelect = document.getElementById('{table_id}_column');
                var columnValue = document.getElementById('{table_id}_column_value');
                var applyButton = document.getElementById('{table_id}_apply');

                function applyFilters() {{
                    table.search(globalSearch.value || '');
                    table.columns().search('');
                    if (columnSelect.value !== '' && columnValue.value) {{
                        table.column(parseInt(columnSelect.value, 10)).search(columnValue.value);
                    }}
                    table.draw();
                }}

                if (globalSearch) {{
                    globalSearch.addEventListener('keyup', function(event) {{
                        if (event.key === 'Enter') {{
                            applyFilters();
                        }}
                    }});
                }}

                if (columnValue) {{
                    columnValue.addEventListener('keyup', function(event) {{
                        if (event.key === 'Enter') {{
                            applyFilters();
                        }}
                    }});
                }}

                if (applyButton) {{
                    applyButton.addEventListener('click', applyFilters);
                }}

                var params = new URLSearchParams(window.location.search);
                if (params.has('filter') && globalSearch) {{
                    globalSearch.value = params.get('filter');
                }}
                if (params.has('column') && columnSelect) {{
                    columnSelect.value = params.get('column');
                }}
                if (params.has('value') && columnValue) {{
                    columnValue.value = params.get('value');
                }}
                if (params.has('filter') || params.has('column') || params.has('value')) {{
                    applyFilters();
                }}

                function highlightRow(row) {{
                    if (!row) {{
                        return;
                    }}
                    row.classList.add('table-warning');
                    row.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
                }}

                var targetRow = params.get('row');
                if (targetRow) {{
                    highlightRow(document.getElementById('row-' + targetRow));
                }}

                var targetRowId = params.get('rowid');
                if (targetRowId) {{
                    var matchingRow = null;
                    document.querySelectorAll('tr[data-rowid]').forEach(function(row) {{
                        if (row.getAttribute('data-rowid') === targetRowId) {{
                            matchingRow = row;
                        }}
                    }});
                    highlightRow(matchingRow);
                }}
            }});
        </script>
        """
    )


def _write_viewer_page(report_folder, source_path, schema_rows):
    viewer_name = _viewer_filename(source_path)
    viewer_path = os.path.join(_viewer_root(report_folder), viewer_name)
    display_path = html.escape(_normalize_path(source_path))

    with open(viewer_path, 'w', encoding='utf8') as handle:
        _write_page_start(handle, f'SQLite Viewer - {os.path.basename(source_path)}', 'Internal SQLite schema and preview browser')
        handle.write('<div class="db-shell">')
        handle.write('<div class="db-toolbar">')
        handle.write(f'<div><strong>Database location:</strong> <a href="{html.escape(_path_to_directory_uri(source_path), quote=True)}">{display_path}</a></div>')
        handle.write(f'<div class="mt-2">Preview pages include up to {MAX_TABLE_ROWS} rows per table or view.</div>')
        handle.write('</div>')
        _write_overview_stats(handle, source_path, schema_rows)

        summary_rows = []
        for obj_type, name, _, sql in schema_rows:
            if obj_type == 'trigger':
                continue
            table_file = _table_filename(source_path, name)
            summary_rows.append((
                obj_type,
                name,
                f'<a href="{html.escape(table_file, quote=True)}">Open preview</a>',
                sql,
            ))

        handle.write('<div class="db-grid"><div>')
        _write_object_browser(handle, source_path, schema_rows)
        handle.write('</div><div>')
        handle.write('<div class="db-panel"><div class="db-panel-header"><h5>Schema Overview</h5></div><div class="db-panel-body">')
        handle.write('<div class="db-table-wrap"><table id="dbViewerSchema" class="table table-striped table-bordered table-xsm" cellspacing="0"><thead><tr><th>Type</th><th>Name</th><th>Preview</th><th>Definition</th></tr></thead><tbody>')
        for obj_type, name, preview_link, sql in summary_rows:
            handle.write('<tr>')
            handle.write(f'<td>{html.escape(str(obj_type))}</td>')
            handle.write(f'<td>{html.escape(str(name))}</td>')
            handle.write(f'<td>{preview_link}</td>')
            handle.write(f'<td><code>{html.escape(str(sql or ""))}</code></td>')
            handle.write('</tr>')
        handle.write('</tbody></table></div></div></div>')

        trigger_rows = [row for row in schema_rows if row[0] == 'trigger']
        handle.write('<div class="db-panel"><div class="db-panel-header"><h5>Triggers</h5></div><div class="db-panel-body">')
        _write_triggers_table(handle, [
            {'name': name, 'table': trigger_table, 'definition': definition}
            for _, name, trigger_table, definition in trigger_rows
        ])
        handle.write('</div></div></div></div>')
        _write_page_end(handle)

    _write_root_redirect(report_folder, _viewer_relpath(viewer_name))
    return viewer_name


def _write_table_page(report_folder, source_path, obj_type, table_name, definition, row_count, columns_info, indexes, triggers, preview_headers, preview_rows, has_rowid, object_errors=None):
    table_file = _table_filename(source_path, table_name)
    table_path = os.path.join(_viewer_root(report_folder), table_file)
    pk_columns = [column[1] for column in columns_info if column[5]]
    row_metadata = []
    rendered_rows = []
    object_errors = object_errors or []

    for index, row in enumerate(preview_rows):
        row_values = list(row)
        rowid_value = row_values[0] if has_rowid else None
        data_cells = row_values[1:] if has_rowid else row_values
        row_number = index + 1
        if has_rowid:
            rendered_rows.append(tuple([row_number, rowid_value] + data_cells))
        else:
            rendered_rows.append(tuple([row_number] + data_cells))

        pk_values = {}
        for pk_name in pk_columns:
            if pk_name in preview_headers:
                pk_values[pk_name] = row[preview_headers.index(pk_name)]

        row_metadata.append({
            'rowid': rowid_value,
            'pk_values': pk_values,
        })

    display_headers = ['Preview Row', 'ROWID'] + preview_headers[1:] if has_rowid else ['Preview Row'] + preview_headers

    with open(table_path, 'w', encoding='utf8') as handle:
        _write_page_start(handle, f'SQLite Viewer - {table_name}', 'Internal SQLite table preview')
        handle.write('<div class="db-shell">')
        handle.write('<div class="db-toolbar">')
        handle.write(f'<div><a href="{html.escape(_viewer_filename(source_path), quote=True)}">Back to database overview</a></div>')
        handle.write(f'<div class="mt-2"><strong>{html.escape(str(table_name))}</strong> <span class="db-object-type">{html.escape(str(obj_type))}</span></div>')
        handle.write('</div>')
        handle.write('<div class="db-stat-grid">')
        for label, value in [('Object type', obj_type), ('Object name', table_name), ('Estimated rows', row_count)]:
            handle.write('<div class="db-stat-card">')
            handle.write(f'<span class="label">{html.escape(str(label))}</span>')
            handle.write(f'<span class="value">{html.escape(str(value))}</span>')
            handle.write('</div>')
        handle.write('</div>')
        if has_rowid:
            handle.write('<div class="db-panel"><div class="db-panel-body"><p class="mb-0">Row jump by <code>?rowid=...</code> is available for previewed rows.</p></div></div>')
        if object_errors:
            handle.write('<div class="db-panel"><div class="db-panel-header"><h5>Viewer Limitations</h5></div><div class="db-panel-body">')
            handle.write('<p class="mb-2">This SQLite object could not be fully inspected by the embedded viewer.</p><ul class="mb-0">')
            for error_message in object_errors:
                handle.write(f'<li><code>{html.escape(str(error_message))}</code></li>')
            handle.write('</ul></div></div>')
        _write_definition_block(handle, f'{table_name} definition', definition)

        handle.write('<div class="db-grid"><div>')
        _write_columns_summary(handle, columns_info)
        handle.write('<div class="db-panel"><div class="db-panel-header"><h5>Columns</h5></div><div class="db-panel-body">')
        _write_columns_table(handle, columns_info)
        handle.write('</div></div>')

        handle.write('<div class="db-panel"><div class="db-panel-header"><h5>Indexes</h5></div><div class="db-panel-body">')
        _write_indexes_table(handle, indexes)
        handle.write('</div></div>')
        if indexes:
            for index in indexes:
                index_columns = [str(column) for column in index['columns'] if column not in (None, '')]
                index_columns_text = ', '.join(index_columns) if index_columns else '[expression or unnamed column]'
                index_definition = f"Columns: {index_columns_text}\nUnique: {index['unique']}\nOrigin: {index['origin']}\nPartial: {index['partial']}"
                _write_definition_block(handle, f"Index {index['name']}", index_definition)

        handle.write('<div class="db-panel"><div class="db-panel-header"><h5>Triggers</h5></div><div class="db-panel-body">')
        _write_triggers_table(handle, triggers)
        handle.write('</div></div></div><div>')
        if triggers:
            for trigger in triggers:
                _write_definition_block(handle, f"Trigger {trigger['name']}", trigger['definition'])

        if preview_rows:
            table_id = f'tbl_{_make_safe_filename(table_name, 20)}'
            searchable_columns = [(index, header) for index, header in enumerate(display_headers)]
            _write_filter_controls(handle, table_id, searchable_columns)
            handle.write('<div class="db-panel"><div class="db-panel-header"><h5>Data Browser</h5></div><div class="db-panel-body">')
            _write_table(handle, display_headers, rendered_rows, table_id, row_metadata=row_metadata)
            handle.write('</div></div>')
            _write_table_navigation_script(handle, table_id)
        else:
            handle.write('<div class="db-panel"><div class="db-panel-body"><p class="mb-0">No preview rows available.</p></div></div>')
        handle.write('</div></div>')

        _write_page_end(handle)

    _write_root_redirect(report_folder, _viewer_relpath(table_file))


def generate_sqlite_viewer(report_folder, source_path):
    normalized_path = _normalize_path(source_path)
    if not normalized_path or not _is_sqlite_path(normalized_path):
        return None

    viewer_root = _viewer_root(report_folder)
    os.makedirs(viewer_root, exist_ok=True)
    normalized_cache_path = normalized_path.casefold() if os.name == 'nt' else normalized_path
    cache_key = (normalized_cache_path, os.path.normcase(viewer_root))
    viewer_relpath = _generated_viewers.get(cache_key)
    if viewer_relpath and os.path.exists(os.path.join(_report_root(report_folder), viewer_relpath)):
        return viewer_relpath

    db = _open_db(normalized_path)
    if not db:
        return None

    try:
        schema_rows = _fetch_schema(db)
        viewer_name = _write_viewer_page(report_folder, normalized_path, schema_rows)

        for obj_type, name, _, definition in schema_rows:
            if obj_type == 'trigger':
                continue
            columns_info, column_error = _fetch_columns(db, name)
            indexes, index_error = _fetch_indexes(db, name)
            triggers = _fetch_triggers(schema_rows, name)
            preview_headers, preview_rows, has_rowid, preview_error = _fetch_preview_rows(db, name)
            row_count = _count_rows(db, name) if obj_type == 'table' else len(preview_rows)
            object_errors = [error for error in (column_error, index_error, preview_error) if error]
            _write_table_page(
                report_folder,
                normalized_path,
                obj_type,
                name,
                definition,
                row_count,
                columns_info,
                indexes,
                triggers,
                preview_headers,
                preview_rows,
                has_rowid,
                object_errors,
            )
    finally:
        db.close()

    viewer_relpath = _viewer_relpath(viewer_name)
    _generated_viewers[cache_key] = viewer_relpath
    return viewer_relpath
