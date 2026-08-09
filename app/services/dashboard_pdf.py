from __future__ import annotations

import html
import json
import os
import re
import threading
from dataclasses import dataclass
from functools import lru_cache, partial
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from anyio import to_thread
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dashboard import (
    Dashboard,
    DashboardSnapshot,
    DashboardType,
)
from app.services.tenant import TenantScope

DASHBOARD_PDF_MAX_METRIC_ROWS = 500

_DASHBOARD_TYPE_LABELS = {
    DashboardType.EXECUTIVE_SUMMARY: "Executive Summary",
    DashboardType.FINANCIAL_PERFORMANCE: "Financial Performance",
    DashboardType.OPERATIONAL_KPI: "Operational KPI",
}

_WINDOWS_RUNTIME_LOCK = threading.Lock()
_WINDOWS_RUNTIME_READY = False
_WINDOWS_DLL_DIRECTORY_HANDLES: list[object] = []


class DashboardPDFDashboardNotFoundError(Exception):
    pass


class DashboardPDFSnapshotNotFoundError(Exception):
    pass


class DashboardPDFRendererError(Exception):
    pass


class DashboardPDFRendererUnavailableError(DashboardPDFRendererError):
    pass


class DashboardPDFRenderer(Protocol):
    def render(
        self,
        *,
        snapshot: DashboardSnapshot,
        business_name: str,
        business_industry: object | None,
    ) -> bytes: ...


@dataclass(frozen=True, slots=True)
class DashboardPDFExport:
    content: bytes
    filename: str
    snapshot_version: int
    snapshot_content_hash: str


def _escape(value: object) -> str:
    if value is None:
        return "—"
    return html.escape(str(value), quote=True)


def _json_display(value: object) -> str:
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ": "),
        )
    except (TypeError, ValueError) as exc:
        raise DashboardPDFRendererError("Dashboard configuration could not be rendered.") from exc
    return _escape(rendered)


def _as_dict(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DashboardPDFRendererError("Dashboard snapshot payload is invalid.")
    return value


def _as_list(value: object) -> list[Any]:
    if not isinstance(value, list):
        raise DashboardPDFRendererError("Dashboard snapshot payload is invalid.")
    return value


def _industry_text(value: object | None) -> str:
    if value is None:
        return "Not specified"

    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, str):
        return enum_value

    return str(value)


def dashboard_pdf_filename(
    *,
    title: str,
    dashboard_id: UUID,
    version: int,
) -> str:
    normalized = title.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized)
    slug = slug.strip("-")[:80]

    if not slug:
        slug = f"dashboard-{dashboard_id}"

    return f"{slug}-v{version}.pdf"


def _configuration_rows(
    configuration: dict[str, Any],
) -> str:
    if not configuration:
        return '<tr><td colspan="2" class="muted">No dashboard configuration was stored.</td></tr>'

    rows = []

    for key in sorted(configuration):
        rows.append(f"<tr><th>{_escape(key)}</th><td>{_json_display(configuration[key])}</td></tr>")

    return "".join(rows)


def _source_rows(sources: list[Any]) -> str:
    if not sources:
        return '<tr><td colspan="4" class="muted">No report sources were recorded.</td></tr>'

    rows = []

    for position, raw_source in enumerate(sources, start=1):
        source = _as_dict(raw_source)

        rows.append(
            "<tr>"
            f"<td>{position}</td>"
            f"<td>{_escape(source.get('filename'))}</td>"
            f"<td>{_escape(source.get('record_count'))}</td>"
            f"<td>{_escape(source.get('sheet_count'))}</td>"
            "</tr>"
        )

    return "".join(rows)


def _metric_rows(
    sources: list[Any],
) -> tuple[str, int]:
    rows: list[str] = []
    omitted = 0

    for raw_source in sources:
        source = _as_dict(raw_source)
        filename = source.get("filename", "Report")
        aggregate = _as_dict(source.get("aggregate"))

        for raw_sheet in _as_list(aggregate.get("sheets")):
            sheet = _as_dict(raw_sheet)
            sheet_name = sheet.get("sheet_name", "Sheet")

            for raw_column in _as_list(sheet.get("columns")):
                column = _as_dict(raw_column)
                numeric = column.get("numeric")

                if numeric is None:
                    continue

                numeric_data = _as_dict(numeric)

                if len(rows) >= DASHBOARD_PDF_MAX_METRIC_ROWS:
                    omitted += 1
                    continue

                rows.append(
                    "<tr>"
                    f"<td>{_escape(filename)}</td>"
                    f"<td>{_escape(sheet_name)}</td>"
                    f"<td>{_escape(column.get('name'))}</td>"
                    f"<td>{_escape(numeric_data.get('count'))}</td>"
                    f"<td>{_escape(numeric_data.get('sum_decimal'))}</td>"
                    f"<td>{_escape(numeric_data.get('average_decimal'))}</td>"
                    f"<td>{_escape(numeric_data.get('min_decimal'))}</td>"
                    f"<td>{_escape(numeric_data.get('max_decimal'))}</td>"
                    "</tr>"
                )

    if not rows:
        return (
            '<tr><td colspan="8" class="muted">'
            "No numeric aggregate columns were available "
            "in this snapshot."
            "</td></tr>",
            omitted,
        )

    return "".join(rows), omitted


def build_dashboard_pdf_html(
    *,
    snapshot: DashboardSnapshot,
    business_name: str,
    business_industry: object | None,
) -> str:
    payload = _as_dict(snapshot.payload)

    if payload.get("format") != "spike.dashboard-snapshot":
        raise DashboardPDFRendererError("Unsupported dashboard snapshot format.")

    dashboard = _as_dict(payload.get("dashboard"))
    configuration = _as_dict(dashboard.get("configuration"))
    sources = _as_list(payload.get("sources"))

    try:
        dashboard_type = DashboardType(dashboard["type"])
    except (KeyError, ValueError) as exc:
        raise DashboardPDFRendererError("Dashboard snapshot type is invalid.") from exc

    title = dashboard.get("title")
    if not isinstance(title, str) or not title.strip():
        raise DashboardPDFRendererError("Dashboard snapshot title is invalid.")

    metric_rows, omitted_metric_rows = _metric_rows(sources)

    omitted_note = ""
    if omitted_metric_rows:
        omitted_note = (
            '<div class="notice">'
            f"{omitted_metric_rows} additional numeric columns "
            "were omitted from this PDF to keep the export bounded."
            "</div>"
        )

    type_label = _DASHBOARD_TYPE_LABELS[dashboard_type]

    created_at = snapshot.created_at.isoformat()

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<style>
@page {{
    size: A4;
    margin: 16mm 14mm 18mm;
}}

* {{
    box-sizing: border-box;
}}

body {{
    margin: 0;
    font-family: Arial, "Segoe UI", sans-serif;
    color: #151513;
    font-size: 10px;
    line-height: 1.45;
}}

h1 {{
    margin: 0;
    font-size: 25px;
    line-height: 1.15;
}}

h2 {{
    margin: 20px 0 8px;
    font-size: 15px;
    color: #0f172a;
}}

.brand {{
    color: #2563eb;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
}}

.subtitle {{
    margin-top: 5px;
    color: #575855;
    font-size: 12px;
}}

.meta {{
    margin-top: 14px;
    padding: 10px 12px;
    background: #f8fafc;
    border: 1px solid #e5e7eb;
    border-radius: 6px;
}}

.meta-grid {{
    width: 100%;
    border-collapse: collapse;
}}

.meta-grid td {{
    padding: 3px 8px 3px 0;
    vertical-align: top;
}}

.meta-label {{
    color: #575855;
    font-weight: 700;
    white-space: nowrap;
}}

.cards {{
    width: 100%;
    border-collapse: separate;
    border-spacing: 6px;
    margin-left: -6px;
    margin-right: -6px;
}}

.card {{
    width: 33.333%;
    padding: 11px;
    border: 1px solid #dbe4f4;
    background: #fafafa;
    vertical-align: top;
}}

.card-label {{
    color: #575855;
    font-size: 9px;
    text-transform: uppercase;
}}

.card-value {{
    margin-top: 3px;
    font-size: 19px;
    font-weight: 700;
    color: #0f172a;
}}

table.data {{
    width: 100%;
    border-collapse: collapse;
    table-layout: auto;
}}

table.data th,
table.data td {{
    border-bottom: 1px solid #e5e7eb;
    padding: 5px 6px;
    text-align: left;
    vertical-align: top;
    overflow-wrap: anywhere;
}}

table.data thead th {{
    background: #e9effd;
    color: #102a63;
    font-weight: 700;
}}

table.configuration th {{
    width: 25%;
    color: #575855;
}}

.hash {{
    font-family: monospace;
    font-size: 8px;
    overflow-wrap: anywhere;
}}

.muted {{
    color: #858585;
}}

.notice {{
    margin-top: 8px;
    padding: 7px 9px;
    background: #fff7ed;
    border: 1px solid #fed7aa;
}}

.footer {{
    margin-top: 22px;
    padding-top: 9px;
    border-top: 1px solid #e5e7eb;
    color: #858585;
    font-size: 8px;
}}

.no-break {{
    break-inside: avoid;
}}
</style>
</head>

<body>

<div class="brand">Spike Technology</div>
<h1>{_escape(title)}</h1>
<div class="subtitle">{_escape(type_label)}</div>

<div class="meta">
<table class="meta-grid">
<tr>
<td class="meta-label">Business</td>
<td>{_escape(business_name)}</td>
<td class="meta-label">Industry</td>
<td>{_escape(_industry_text(business_industry))}</td>
</tr>

<tr>
<td class="meta-label">Snapshot version</td>
<td>{snapshot.version}</td>
<td class="meta-label">Snapshot created</td>
<td>{_escape(created_at)}</td>
</tr>

<tr>
<td class="meta-label">Dashboard ID</td>
<td>{_escape(snapshot.dashboard_id)}</td>
<td class="meta-label">Schema version</td>
<td>{snapshot.schema_version}</td>
</tr>

<tr>
<td class="meta-label">Content hash</td>
<td colspan="3" class="hash">{_escape(snapshot.content_hash)}</td>
</tr>
</table>
</div>

<h2>Snapshot overview</h2>

<table class="cards">
<tr>
<td class="card">
<div class="card-label">Report sources</div>
<div class="card-value">{_escape(payload.get("source_count"))}</div>
</td>

<td class="card">
<div class="card-label">Records</div>
<div class="card-value">{_escape(payload.get("record_count"))}</div>
</td>

<td class="card">
<div class="card-label">Sheets</div>
<div class="card-value">{_escape(payload.get("sheet_count"))}</div>
</td>
</tr>
</table>

<div class="no-break">
<h2>Dashboard configuration</h2>

<table class="data configuration">
<tbody>
{_configuration_rows(configuration)}
</tbody>
</table>
</div>

<h2>Report sources</h2>

<table class="data">
<thead>
<tr>
<th>#</th>
<th>File</th>
<th>Records</th>
<th>Sheets</th>
</tr>
</thead>
<tbody>
{_source_rows(sources)}
</tbody>
</table>

<h2>Numeric aggregates</h2>

<table class="data">
<thead>
<tr>
<th>Source</th>
<th>Sheet</th>
<th>Column</th>
<th>Count</th>
<th>Sum</th>
<th>Average</th>
<th>Min</th>
<th>Max</th>
</tr>
</thead>
<tbody>
{metric_rows}
</tbody>
</table>

{omitted_note}

<div class="footer">
This report is rendered from immutable dashboard snapshot
version {snapshot.version}. It does not re-read or re-parse the
original uploaded report during export.
</div>

</body>
</html>
"""


def _prepare_windows_weasyprint_runtime() -> None:
    global _WINDOWS_RUNTIME_READY

    if os.name != "nt":
        return

    with _WINDOWS_RUNTIME_LOCK:
        if _WINDOWS_RUNTIME_READY:
            return

        configured = os.environ.get(
            "WEASYPRINT_DLL_DIRECTORIES",
            "",
        )

        directories = [Path(item.strip()) for item in configured.split(os.pathsep) if item.strip()]

        directories = [directory for directory in directories if directory.is_dir()]

        if not directories:
            raise DashboardPDFRendererUnavailableError(
                "WEASYPRINT_DLL_DIRECTORIES is not configured."
            )

        required = (
            "libglib-2.0-0.dll",
            "libgobject-2.0-0.dll",
            "libharfbuzz-0.dll",
            "libfontconfig-1.dll",
            "libpango-1.0-0.dll",
            "libpangoft2-1.0-0.dll",
        )

        for filename in required:
            if not any((directory / filename).exists() for directory in directories):
                raise DashboardPDFRendererUnavailableError(
                    f"Required WeasyPrint DLL is missing: {filename}"
                )

        first_directory = directories[0]

        derived_font_directory = first_directory.parent / "etc" / "fonts"
        derived_fonts_conf = derived_font_directory / "fonts.conf"

        if derived_fonts_conf.exists():
            os.environ.setdefault(
                "FONTCONFIG_PATH",
                str(derived_font_directory),
            )
            os.environ.setdefault(
                "FONTCONFIG_FILE",
                "fonts.conf",
            )

        # CFFI ultimately opens Pango/GLib libraries by basename.
        # Windows may therefore choose another application bundle
        # containing DLLs with identical names, such as Tesseract.
        #
        # Make the explicitly configured WeasyPrint runtime the first
        # native-library root and remove other PATH entries that expose
        # conflicting Pango/GLib/Fontconfig DLLs.
        configured_keys = {
            os.path.normcase(os.path.abspath(str(directory))) for directory in directories
        }

        existing_path = os.environ.get(
            "PATH",
            "",
        ).split(os.pathsep)

        clean_path: list[str] = []

        collision_names = (
            "libgobject-2.0-0.dll",
            "libfontconfig-1.dll",
            "libpango-1.0-0.dll",
        )

        for item in existing_path:
            if not item.strip():
                continue

            key = os.path.normcase(os.path.abspath(item))

            if key in configured_keys:
                continue

            candidate = Path(item)

            if any((candidate / filename).exists() for filename in collision_names):
                continue

            clean_path.append(item)

        os.environ["PATH"] = os.pathsep.join(
            [
                *(str(directory) for directory in directories),
                *clean_path,
            ]
        )

        # Python 3.8+ uses explicit DLL-directory handles when loading
        # extension/native dependencies. Keep the handles alive for the
        # lifetime of the API process.
        for directory in directories:
            try:
                handle = os.add_dll_directory(str(directory))
            except OSError as exc:
                raise DashboardPDFRendererUnavailableError(
                    "WeasyPrint DLL directory could not be loaded."
                ) from exc

            _WINDOWS_DLL_DIRECTORY_HANDLES.append(handle)

        _WINDOWS_RUNTIME_READY = True


class WeasyPrintDashboardPDFRenderer:
    def render(
        self,
        *,
        snapshot: DashboardSnapshot,
        business_name: str,
        business_industry: object | None,
    ) -> bytes:
        _prepare_windows_weasyprint_runtime()

        try:
            from weasyprint import HTML
        except Exception as exc:
            raise DashboardPDFRendererUnavailableError(
                "WeasyPrint runtime is unavailable."
            ) from exc

        document = build_dashboard_pdf_html(
            snapshot=snapshot,
            business_name=business_name,
            business_industry=business_industry,
        )

        try:
            content = HTML(
                string=document,
                base_url=None,
            ).write_pdf()
        except Exception as exc:
            raise DashboardPDFRendererError("Dashboard PDF rendering failed.") from exc

        if not isinstance(content, bytes) or not content.startswith(b"%PDF-"):
            raise DashboardPDFRendererError("Dashboard PDF renderer returned invalid output.")

        return content


@lru_cache
def get_dashboard_pdf_renderer() -> DashboardPDFRenderer:
    return WeasyPrintDashboardPDFRenderer()


class DashboardPDFService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        renderer: DashboardPDFRenderer,
    ) -> None:
        self.session = session
        self.renderer = renderer

    async def export_latest(
        self,
        scope: TenantScope,
        dashboard_id: UUID,
        *,
        business_name: str,
        business_industry: object | None,
    ) -> DashboardPDFExport:
        dashboard = await scope.get(
            self.session,
            Dashboard,
            dashboard_id,
        )

        if dashboard is None:
            raise DashboardPDFDashboardNotFoundError

        result = await self.session.execute(
            scope.select(
                DashboardSnapshot,
                DashboardSnapshot.dashboard_id == dashboard_id,
            )
            .order_by(DashboardSnapshot.version.desc())
            .limit(1)
        )

        snapshot = result.scalar_one_or_none()

        if snapshot is None:
            await self.session.commit()
            raise DashboardPDFSnapshotNotFoundError

        # PDF generation is CPU-heavy and must not hold a database
        # transaction or block the asyncio event loop.
        await self.session.commit()

        render = partial(
            self.renderer.render,
            snapshot=snapshot,
            business_name=business_name,
            business_industry=business_industry,
        )

        content = await to_thread.run_sync(render)

        if not content.startswith(b"%PDF-"):
            raise DashboardPDFRendererError("Dashboard PDF renderer returned invalid output.")

        snapshot_dashboard = _as_dict(snapshot.payload.get("dashboard"))
        title = snapshot_dashboard.get("title")

        if not isinstance(title, str):
            raise DashboardPDFRendererError("Dashboard snapshot title is invalid.")

        return DashboardPDFExport(
            content=content,
            filename=dashboard_pdf_filename(
                title=title,
                dashboard_id=dashboard_id,
                version=snapshot.version,
            ),
            snapshot_version=snapshot.version,
            snapshot_content_hash=snapshot.content_hash,
        )


__all__ = [
    "DASHBOARD_PDF_MAX_METRIC_ROWS",
    "DashboardPDFDashboardNotFoundError",
    "DashboardPDFExport",
    "DashboardPDFRenderer",
    "DashboardPDFRendererError",
    "DashboardPDFRendererUnavailableError",
    "DashboardPDFService",
    "DashboardPDFSnapshotNotFoundError",
    "WeasyPrintDashboardPDFRenderer",
    "build_dashboard_pdf_html",
    "dashboard_pdf_filename",
    "get_dashboard_pdf_renderer",
]
