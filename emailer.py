from __future__ import annotations

from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
import html
import os
import smtplib
import textwrap


@dataclass
class EmailSettings:
    enabled: bool = False
    smtp_server: str = "smtp.gmail.com"
    smtp_port: int = 587
    sender: str = ""
    recipient: str = ""
    username_env: str = "FOCUS_TRACKER_EMAIL_USERNAME"
    password_env: str = "FOCUS_TRACKER_EMAIL_PASSWORD"
    use_tls: bool = True
    attach_report_file: bool = True


class ReportEmailer:
    def __init__(self, settings: EmailSettings) -> None:
        self.settings = settings

    def is_configured(self) -> bool:
        return self.settings.enabled and bool(self.settings.sender and self.settings.recipient)

    def send_report(self, report_path: Path) -> None:
        if not self.is_configured():
            return

        username = os.getenv(self.settings.username_env, "").strip()
        password = os.getenv(self.settings.password_env, "").strip()
        if not username or not password:
            raise RuntimeError(
                "Email delivery is enabled but SMTP credentials are missing from the configured environment variables."
            )

        report_text = report_path.read_text(encoding="utf-8")
        subject = f"Focus report - {report_path.stem.replace('focus-report-', '')}"
        pdf_path = render_report_pdf(report_path, report_text)

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.settings.sender
        message["To"] = self.settings.recipient
        message.set_content(build_email_body(report_text, pdf_path.name))
        message.add_alternative(build_email_html(report_text, pdf_path.name), subtype="html")

        if self.settings.attach_report_file:
            message.add_attachment(
                pdf_path.read_bytes(),
                maintype="application",
                subtype="pdf",
                filename=pdf_path.name,
            )

        with smtplib.SMTP(self.settings.smtp_server, self.settings.smtp_port, timeout=30) as smtp:
            if self.settings.use_tls:
                smtp.starttls()
            smtp.login(username, password)
            smtp.send_message(message)


def build_email_body(report_text: str, pdf_filename: str) -> str:
    preview_lines = [line.strip() for line in report_text.splitlines() if line.strip()][:8]
    preview = "\n".join(preview_lines)
    return "\n".join(
        [
            "Your focus report is attached as a PDF for easier reading.",
            f"Attachment: {pdf_filename}",
            "",
            "Preview:",
            preview,
        ]
    ).strip()


def build_email_html(report_text: str, pdf_filename: str) -> str:
    elements = _parse_report_elements(report_text)
    blocks = [
        "<!doctype html>",
        "<html>",
        "<body style=\"margin:0;padding:24px;background:#f3f4f6;font-family:Arial,Helvetica,sans-serif;color:#111827;\">",
        "<div style=\"max-width:860px;margin:0 auto;background:#ffffff;border:1px solid #e5e7eb;border-radius:12px;overflow:hidden;\">",
        "<div style=\"padding:20px 24px;border-bottom:1px solid #e5e7eb;background:#f9fafb;\">",
        "<div style=\"font-size:14px;color:#4b5563;margin-bottom:6px;\">Focus report attached as PDF</div>",
        f"<div style=\"font-size:13px;color:#6b7280;\">Attachment: {html.escape(pdf_filename)}</div>",
        "</div>",
        "<div style=\"padding:24px;\">",
    ]

    for element in elements:
        element_type = str(element["type"])
        if element_type == "title":
            blocks.append(
                f"<h1 style=\"margin:0 0 18px;font-size:28px;line-height:1.2;color:#111827;\">{html.escape(str(element['text']))}</h1>"
            )
        elif element_type == "heading":
            blocks.append(
                f"<h2 style=\"margin:26px 0 10px;padding-bottom:8px;border-bottom:1px solid #e5e7eb;font-size:18px;line-height:1.3;color:#111827;\">{html.escape(str(element['text']))}</h2>"
            )
        elif element_type == "paragraph":
            blocks.append(
                f"<p style=\"margin:10px 0;font-size:14px;line-height:1.65;color:#1f2937;\">{_htmlize_inline_text(str(element['text']))}</p>"
            )
        elif element_type == "bullet":
            blocks.append(
                f"<p style=\"margin:8px 0 8px 0;padding-left:14px;font-size:14px;line-height:1.6;color:#1f2937;\">&bull; {_htmlize_inline_text(str(element['text']))}</p>"
            )
        elif element_type == "table":
            rows = element["rows"]
            if isinstance(rows, list) and rows:
                blocks.append(_build_html_table(rows))

    blocks.extend(["</div>", "</div>", "</body>", "</html>"])
    return "".join(blocks)


def render_report_pdf(report_path: Path, report_text: str) -> Path:
    pdf_path = report_path.with_suffix(".pdf")
    pdf_bytes = _build_styled_pdf(_parse_report_elements(report_text))
    pdf_path.write_bytes(pdf_bytes)
    return pdf_path


def _wrap_text(value: str, width: int) -> list[str]:
    if not value:
        return [""]
    return textwrap.wrap(
        value,
        width=width,
        replace_whitespace=False,
        drop_whitespace=False,
        break_long_words=False,
        break_on_hyphens=False,
    ) or [value]


def _htmlize_inline_text(value: str) -> str:
    escaped = html.escape(value)
    return escaped.replace("\n", "<br>")


def _escape_pdf_text(value: str) -> str:
    ascii_value = (
        value.replace("\u2014", "-")
        .replace("\u2013", "-")
        .replace("\u2022", "*")
        .encode("ascii", "replace")
        .decode("ascii")
    )
    return ascii_value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _parse_report_elements(report_text: str) -> list[dict[str, object]]:
    lines = report_text.splitlines()
    elements: list[dict[str, object]] = []
    index = 0

    while index < len(lines):
        raw_line = lines[index].rstrip()
        stripped = raw_line.strip()

        if not stripped:
            index += 1
            continue

        if stripped.startswith("# "):
            elements.append({"type": "title", "text": stripped[2:].strip()})
            index += 1
            continue

        if stripped.startswith("## "):
            elements.append({"type": "heading", "text": stripped[3:].strip()})
            index += 1
            continue

        if stripped.startswith("|"):
            table_lines: list[str] = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                table_lines.append(lines[index].strip())
                index += 1
            table_rows = _parse_markdown_table(table_lines)
            if table_rows:
                elements.append({"type": "table", "rows": table_rows})
            continue

        if stripped.startswith("- "):
            bullet_lines = [stripped[2:].strip()]
            index += 1
            while index < len(lines):
                candidate = lines[index].rstrip()
                if not candidate.strip():
                    break
                if candidate.strip().startswith(("# ", "## ", "|", "- ")):
                    break
                bullet_lines.append(candidate.strip())
                index += 1
            elements.append({"type": "bullet", "text": " ".join(bullet_lines)})
            continue

        paragraph_lines = [stripped]
        index += 1
        while index < len(lines):
            candidate = lines[index].rstrip()
            if not candidate.strip():
                break
            if candidate.strip().startswith(("# ", "## ", "|", "- ")):
                break
            paragraph_lines.append(candidate.strip())
            index += 1
        elements.append({"type": "paragraph", "text": " ".join(paragraph_lines)})

    return elements or [{"type": "paragraph", "text": "Focus report"}]


def _parse_markdown_table(table_lines: list[str]) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in table_lines:
        parts = [segment.strip() for segment in line.strip("|").split("|")]
        if parts and all(set(part) <= {"-"} for part in parts):
            continue
        rows.append(parts)
    return rows


def _build_html_table(rows: list[list[str]]) -> str:
    normalized_column_count = max(len(row) for row in rows)
    normalized_rows = [row + [""] * (normalized_column_count - len(row)) for row in rows]
    header = normalized_rows[0]
    body = normalized_rows[1:]

    parts = [
        "<table style=\"width:100%;border-collapse:collapse;margin:12px 0 18px;font-size:13px;color:#1f2937;\">",
        "<thead><tr>",
    ]
    for cell in header:
        parts.append(
            f"<th style=\"text-align:left;padding:10px 12px;background:#f3f4f6;border:1px solid #d1d5db;font-weight:600;\">{_htmlize_inline_text(cell)}</th>"
        )
    parts.append("</tr></thead><tbody>")
    for row in body:
        parts.append("<tr>")
        for cell in row:
            parts.append(
                f"<td style=\"padding:10px 12px;border:1px solid #e5e7eb;vertical-align:top;\">{_htmlize_inline_text(cell)}</td>"
            )
        parts.append("</tr>")
    parts.append("</tbody></table>")
    return "".join(parts)


def _text_wrap_for_width(text: str, width_points: float, font_size: int) -> list[str]:
    approx_chars = max(12, int(width_points / max(font_size * 0.56, 1)))
    return _wrap_text(text, approx_chars)


def _build_styled_pdf(elements: list[dict[str, object]]) -> bytes:
    page_height = 792
    page_width = 612
    left_margin = 48
    right_margin = 48
    top_margin = 746
    bottom_margin = 48
    content_width = page_width - left_margin - right_margin

    pages: list[list[str]] = [[]]
    current_y = top_margin

    def add_page() -> None:
        nonlocal current_y
        pages.append([])
        current_y = top_margin

    def write_text(text: str, *, x: float, y: float, font: str, size: int) -> None:
        pages[-1].append(
            "\n".join(
                [
                    "BT",
                    f"/{font} {size} Tf",
                    f"1 0 0 1 {x:.2f} {y:.2f} Tm",
                    f"({_escape_pdf_text(text)}) Tj",
                    "ET",
                ]
            )
        )

    def draw_line(x1: float, y1: float, x2: float, y2: float, *, width: float = 1.0) -> None:
        pages[-1].append(f"{width:.2f} w {x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S")

    def ensure_space(height_needed: float) -> None:
        nonlocal current_y
        if current_y - height_needed < bottom_margin:
            add_page()

    for element in elements:
        element_type = str(element["type"])

        if element_type == "title":
            ensure_space(42)
            write_text(str(element["text"]), x=left_margin, y=current_y, font="F2", size=20)
            current_y -= 12
            draw_line(left_margin, current_y, page_width - right_margin, current_y, width=1.2)
            current_y -= 22
            continue

        if element_type == "heading":
            ensure_space(30)
            write_text(str(element["text"]), x=left_margin, y=current_y, font="F2", size=13)
            current_y -= 8
            draw_line(left_margin, current_y, page_width - right_margin, current_y, width=0.6)
            current_y -= 18
            continue

        if element_type == "paragraph":
            wrapped = _text_wrap_for_width(str(element["text"]), content_width, 10)
            ensure_space((len(wrapped) * 14) + 8)
            for line in wrapped:
                write_text(line, x=left_margin, y=current_y, font="F1", size=10)
                current_y -= 14
            current_y -= 6
            continue

        if element_type == "bullet":
            wrapped = _text_wrap_for_width(str(element["text"]), content_width - 18, 10)
            ensure_space((len(wrapped) * 14) + 6)
            for line_index, line in enumerate(wrapped):
                prefix = "* " if line_index == 0 else "  "
                write_text(prefix + line, x=left_margin, y=current_y, font="F1", size=10)
                current_y -= 14
            current_y -= 4
            continue

        if element_type == "table":
            table_rows = element["rows"]
            if not isinstance(table_rows, list) or not table_rows:
                continue
            current_y = _render_table(
                pages=pages,
                rows=table_rows,
                start_y=current_y,
                top_margin=top_margin,
                bottom_margin=bottom_margin,
                left_margin=left_margin,
                content_width=content_width,
            )
            current_y -= 10
            continue

    page_pairs: list[tuple[int, int]] = []
    next_object_id = 5
    for _ in pages:
        content_id = next_object_id
        page_id = next_object_id + 1
        page_pairs.append((content_id, page_id))
        next_object_id += 2

    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: f"<< /Type /Pages /Count {len(pages)} /Kids [{' '.join(f'{page_id} 0 R' for _, page_id in page_pairs)}] >>".encode("ascii"),
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        4: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
    }

    for page_index, (page_commands, (content_id, page_id)) in enumerate(zip(pages, page_pairs, strict=True), start=1):
        footer = [
            "BT",
            "/F1 9 Tf",
            f"1 0 0 1 {left_margin:.2f} 24.00 Tm",
            f"(Page {page_index} of {len(pages)}) Tj",
            "ET",
        ]
        content_stream = "\n".join(page_commands + footer).encode("ascii")
        objects[content_id] = (
            f"<< /Length {len(content_stream)} >>\nstream\n".encode("ascii")
            + content_stream
            + b"\nendstream"
        )
        objects[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 {page_height}] "
            f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {content_id} 0 R >>"
        ).encode("ascii")

    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for object_id in range(1, len(objects) + 1):
        offsets.append(len(output))
        output.extend(f"{object_id} 0 obj\n".encode("ascii"))
        output.extend(objects[object_id])
        output.extend(b"\nendobj\n")

    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))

    output.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(output)


def _render_table(
    *,
    pages: list[list[str]],
    rows: list[list[str]],
    start_y: float,
    top_margin: int,
    bottom_margin: int,
    left_margin: int,
    content_width: int,
) -> float:
    current_y = start_y
    header = rows[0]
    body = rows[1:]
    column_count = max(len(row) for row in rows)
    normalized_rows = [row + [""] * (column_count - len(row)) for row in rows]
    max_lengths = [max(len(row[index]) for row in normalized_rows) for index in range(column_count)]
    total_length = max(sum(max_lengths), 1)
    widths = [max(72.0, (value / total_length) * content_width) for value in max_lengths]
    width_scale = content_width / sum(widths)
    widths = [round(width * width_scale, 2) for width in widths]

    def add_page() -> None:
        nonlocal current_y
        pages.append([])
        current_y = top_margin

    def cell_lines(text: str, width: float) -> list[str]:
        return _text_wrap_for_width(text, width - 10, 9)

    def row_height(row: list[str]) -> float:
        return max(len(cell_lines(cell, width)) for cell, width in zip(row, widths, strict=True)) * 12 + 10

    def draw_row(row: list[str], *, is_header: bool) -> None:
        nonlocal current_y
        height = row_height(row)
        if current_y - height < bottom_margin:
            add_page()
            if not is_header:
                draw_row(header, is_header=True)
        x = float(left_margin)
        if is_header:
            pages[-1].append(f"0.92 g {left_margin:.2f} {current_y - height:.2f} {content_width:.2f} {height:.2f} re f 0 g")
        for cell, width in zip(row, widths, strict=True):
            pages[-1].append(f"0.80 w {x:.2f} {current_y - height:.2f} {width:.2f} {height:.2f} re S")
            text_y = current_y - 14
            font = "F2" if is_header else "F1"
            for line in cell_lines(cell, width):
                pages[-1].append(
                    "\n".join(
                        [
                            "BT",
                            f"/{font} 9 Tf",
                            f"1 0 0 1 {x + 5:.2f} {text_y:.2f} Tm",
                            f"({_escape_pdf_text(line)}) Tj",
                            "ET",
                        ]
                    )
                )
                text_y -= 12
            x += width
        current_y -= height

    draw_row(header, is_header=True)
    for row in body:
        draw_row(row, is_header=False)

    return current_y
