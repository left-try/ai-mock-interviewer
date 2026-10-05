"""Render validated interview reports for on-screen reading and local export."""

from __future__ import annotations

import html
import os
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


CRITERION_LABELS = {
    "self_presentation": "Самопрезентация",
    "motivation": "Мотивация",
    "personal_contribution": "Личный вклад",
    "communication": "Коммуникация",
    "reflection": "Рефлексия",
    "consistency": "Последовательность и согласованность",
    "teamwork": "Командная работа",
    "challenge": "Работа со сложными ситуациями",
}
RECOMMENDATION_LABELS = {
    "strong_signal": "Сильный сигнал",
    "mixed_signal": "Смешанный сигнал",
    "insufficient_data": "Недостаточно данных для оценки",
}


class ReportExportError(RuntimeError):
    """The report is valid, but one or more local export files could not be written."""


def default_reports_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "ai-mock-interviewer" / "reports"
    return Path.home() / ".local" / "share" / "ai-mock-interviewer" / "reports"


def render_report_markdown(report, turns) -> str:
    source_labels = _source_labels(turns)
    lines = [
        "# Отчёт HR-интервью — Backend Internship",
        "",
        "## Рекомендация",
        "",
        RECOMMENDATION_LABELS.get(report.recommendation, report.recommendation),
        "",
        "## Оценки",
        "",
        "| Критерий | Оценка |",
        "| --- | ---: |",
    ]
    if report.scores:
        for criterion, score in report.scores.items():
            label = CRITERION_LABELS.get(criterion, criterion)
            score_text = "Недостаточно данных" if score is None else f"{score}/5"
            lines.append(f"| {_md(label)} | {_md(score_text)} |")
    else:
        lines.append("| Оценки не выставлены | Недостаточно данных |")

    _append_list_section(lines, "Сильные стороны", report.strengths)
    _append_list_section(lines, "Зоны роста", report.growth_areas)
    lines.extend(["", "## Подтверждения", ""])
    if report.evidence:
        for item in report.evidence:
            item = _as_mapping(item)
            criterion = CRITERION_LABELS.get(item.get("criterion", ""), item.get("criterion", ""))
            source = source_labels.get(item.get("source_turn_id"), "Источник не указан")
            lines.extend(
                [
                    f"### {_md(criterion)} · {_md(source)}",
                    "",
                    f"> {_md(item.get('quote', ''))}",
                    "",
                    _md(item.get("observation", "")),
                    "",
                ]
            )
    else:
        lines.extend(["Подтверждения не приведены.", ""])

    _append_list_section(lines, "Неопределённости", report.uncertainties)
    lines.extend(["", "---", "", _md(report.disclaimer), ""])
    return "\n".join(lines)


def export_report_files(report, turns, session_id: str, *, output_dir: Path | str | None = None) -> dict[str, str]:
    markdown = render_report_markdown(report, turns)
    directory = Path(output_dir) if output_dir is not None else default_reports_dir()
    safe_id = re.sub(r"[^a-zA-Z0-9]", "", session_id)[:32] or "session"
    stem = f"backend-internship-report-{safe_id}"
    markdown_path = directory / f"{stem}.md"
    pdf_path = directory / f"{stem}.pdf"

    try:
        directory.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(markdown, encoding="utf-8", newline="\n")
        _write_pdf(report, turns, markdown_path, pdf_path)
    except (OSError, ReportExportError) as exc:
        raise ReportExportError(f"Could not export the interview report: {exc}") from exc

    return {
        "markdown": markdown,
        "markdown_path": str(markdown_path.resolve()),
        "pdf_path": str(pdf_path.resolve()),
    }


def _append_list_section(lines: list[str], title: str, values: list[str]) -> None:
    lines.extend(["", f"## {title}", ""])
    if values:
        lines.extend(f"- {_md(value)}" for value in values)
    else:
        lines.append("Не указано.")


def _source_labels(turns) -> dict[str, str]:
    labels = {"resume": "Резюме"}
    candidate_number = 0
    for turn in turns:
        if turn.role == "candidate":
            candidate_number += 1
            labels[turn.id] = f"Ответ кандидата №{candidate_number}"
    return labels


def _as_mapping(value):
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return {}


def _md(value) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ")
    text = html.escape(text, quote=False)
    return re.sub(r"([\\`*_{}\[\]()#+|>~])", r"\\\1", text)


def _font_path() -> Path:
    windows = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    candidates = [
        windows / "arial.ttf",
        windows / "segoeui.ttf",
        windows / "calibri.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    ]
    for path in candidates:
        if path.is_file():
            return path
    raise ReportExportError("No installed TrueType font with Cyrillic coverage was found for PDF export.")


def _write_pdf(report, turns, markdown_path: Path, pdf_path: Path) -> None:
    font_name = "InterviewerReportFont"
    if font_name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(font_name, str(_font_path())))
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="ReportTitleRu", parent=styles["Title"], fontName=font_name, alignment=TA_CENTER, fontSize=18, leading=23, textColor=colors.HexColor("#17324D"), spaceAfter=8 * mm))
    styles.add(ParagraphStyle(name="ReportHeadingRu", parent=styles["Heading2"], fontName=font_name, fontSize=13, leading=17, textColor=colors.HexColor("#17324D"), spaceBefore=5 * mm, spaceAfter=2 * mm))
    styles.add(ParagraphStyle(name="ReportBodyRu", parent=styles["BodyText"], fontName=font_name, fontSize=9.5, leading=14, spaceAfter=2 * mm))
    styles.add(ParagraphStyle(name="ReportQuoteRu", parent=styles["BodyText"], fontName=font_name, fontSize=9, leading=13, leftIndent=5 * mm, borderColor=colors.HexColor("#CBD5E1"), borderWidth=0.7, borderPadding=4 * mm, backColor=colors.HexColor("#F5F8FB"), spaceAfter=2 * mm))
    story = [
        Paragraph("Отчёт HR-интервью — Backend Internship", styles["ReportTitleRu"]),
        Paragraph("Рекомендация", styles["ReportHeadingRu"]),
        Paragraph(html.escape(RECOMMENDATION_LABELS.get(report.recommendation, report.recommendation)), styles["ReportBodyRu"]),
        Paragraph("Оценки", styles["ReportHeadingRu"]),
    ]
    score_rows = [["Критерий", "Оценка"]]
    for criterion, score in report.scores.items():
        score_rows.append([CRITERION_LABELS.get(criterion, criterion), "Недостаточно данных" if score is None else f"{score}/5"])
    if len(score_rows) == 1:
        score_rows.append(["Оценки не выставлены", "Недостаточно данных"])
    table = Table(score_rows, colWidths=[95 * mm, 65 * mm], repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font_name),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EEF4")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#17324D")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.extend([table, Spacer(1, 3 * mm)])
    _pdf_list_section(story, "Сильные стороны", report.strengths, styles, font_name)
    _pdf_list_section(story, "Зоны роста", report.growth_areas, styles, font_name)
    story.append(Paragraph("Подтверждения", styles["ReportHeadingRu"]))
    labels = _source_labels(turns)
    if report.evidence:
        for raw_item in report.evidence:
            item = _as_mapping(raw_item)
            title = f"{CRITERION_LABELS.get(item.get('criterion', ''), item.get('criterion', ''))} · {labels.get(item.get('source_turn_id'), 'Источник не указан')}"
            story.append(Paragraph(html.escape(title), styles["ReportBodyRu"]))
            story.append(Paragraph(html.escape(str(item.get("quote", ""))).replace("\n", "<br/>"), styles["ReportQuoteRu"]))
            story.append(Paragraph(html.escape(str(item.get("observation", ""))), styles["ReportBodyRu"]))
    else:
        story.append(Paragraph("Подтверждения не приведены.", styles["ReportBodyRu"]))
    _pdf_list_section(story, "Неопределённости", report.uncertainties, styles, font_name)
    story.extend([Spacer(1, 5 * mm), Paragraph(html.escape(report.disclaimer), styles["ReportBodyRu"])])
    document = SimpleDocTemplate(
        str(pdf_path), pagesize=A4,
        rightMargin=22 * mm, leftMargin=22 * mm,
        topMargin=20 * mm, bottomMargin=20 * mm,
        title="Отчёт HR-интервью — Backend Internship",
        author="AI Mock Interviewer",
    )
    document.build(story)


def _pdf_list_section(story, title: str, values: list[str], styles, font_name: str) -> None:
    story.append(Paragraph(html.escape(title), styles["ReportHeadingRu"]))
    if values:
        for value in values:
            story.append(Paragraph(f"•&nbsp;&nbsp;{html.escape(value)}", styles["ReportBodyRu"]))
    else:
        story.append(Paragraph("Не указано.", styles["ReportBodyRu"]))
