"""Render validated interview reports for on-screen reading and local export."""

from __future__ import annotations

import html
import os
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .interview_plan import REQUIRED_TOPICS, interview_progress
from .scenarios import level_label, normalize_level

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
TOPIC_LABELS = {
    "motivation": "Мотивация",
    "education": "Образование",
    "project": "Проекты",
    "personal_contribution": "Личный вклад",
    "teamwork": "Командная работа",
    "challenge": "Сложная ситуация",
    "reflection": "Выводы",
    "expectations": "Ожидания",
}


class ReportExportError(RuntimeError):
    """The report is valid, but one or more local export files could not be written."""


def default_reports_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "ai-mock-interviewer" / "reports"
    return Path.home() / ".local" / "share" / "ai-mock-interviewer" / "reports"


def _coverage_data(turns) -> tuple[int, int, list[str]]:
    progress = interview_progress(turns)
    missing = progress["missing_topics"]
    return len(REQUIRED_TOPICS) - len(missing), len(REQUIRED_TOPICS), missing


def _score_bar(score: int | None) -> str:
    if score is None:
        return "Недостаточно данных"
    if isinstance(score, bool) or not isinstance(score, int) or not 1 <= score <= 5:
        raise ValueError("score must be an integer from 1 to 5 or None")
    return f"{'■' * score}{'□' * (5 - score)} {score}/5"


def render_report_markdown(report, turns, *, level: str = "internship") -> str:
    level_name = level_label(level)
    source_labels = _source_labels(turns)
    covered, required, missing = _coverage_data(turns)
    coverage_bar = f"{'●' * covered}{'○' * (required - covered)}"
    lines = [
        f"# Отчёт Backend Screening — {level_name}",
        "",
        "## Рекомендация и вердикт",
        "",
        RECOMMENDATION_LABELS.get(report.recommendation, report.recommendation),
        "",
        "## Покрытие тем",
        "",
        f"{coverage_bar} {covered}/{required}",
        "",
    ]
    if missing:
        lines.append("Не проверены: " + ", ".join(TOPIC_LABELS.get(topic, topic) for topic in missing) + ".")
        lines.append("")
    lines.extend([
        "## Оценки",
        "",
        "| Критерий | Шкала 1–5 |",
        "| --- | :--- |",
    ])
    if report.scores:
        for criterion, score in report.scores.items():
            label = CRITERION_LABELS.get(criterion, criterion)
            score_text = _score_bar(score)
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


def export_report_files(
    report,
    turns,
    session_id: str,
    *,
    output_dir: Path | str | None = None,
    markdown: str | None = None,
    level: str = "internship",
) -> dict[str, str]:
    level = normalize_level(level)
    markdown = markdown if markdown is not None else render_report_markdown(report, turns, level=level)
    directory = Path(output_dir) if output_dir is not None else default_reports_dir()
    safe_id = re.sub(r"[^a-zA-Z0-9]", "", session_id)[:32] or "session"
    stem = f"backend-screening-{level}-report-{safe_id}"
    markdown_path = directory / f"{stem}.md"
    pdf_path = directory / f"{stem}.pdf"

    try:
        directory.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(markdown, encoding="utf-8", newline="\n")
        _write_pdf(report, turns, markdown_path, pdf_path, level=level)
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


def _write_pdf(report, turns, markdown_path: Path, pdf_path: Path, *, level: str = "internship") -> None:
    title = f"Отчёт Backend Screening — {level_label(level)}"
    font_name = "InterviewerReportFont"
    if font_name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(font_name, str(_font_path())))
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="ReportTitleRu", parent=styles["Title"], fontName=font_name, alignment=TA_CENTER, fontSize=18, leading=23, textColor=colors.HexColor("#17324D"), spaceAfter=8 * mm))
    styles.add(ParagraphStyle(name="ReportHeadingRu", parent=styles["Heading2"], fontName=font_name, fontSize=13, leading=17, textColor=colors.HexColor("#17324D"), spaceBefore=5 * mm, spaceAfter=2 * mm))
    styles.add(ParagraphStyle(name="ReportBodyRu", parent=styles["BodyText"], fontName=font_name, fontSize=9.5, leading=14, spaceAfter=2 * mm))
    styles.add(ParagraphStyle(name="ReportQuoteRu", parent=styles["BodyText"], fontName=font_name, fontSize=9, leading=13, textColor=colors.HexColor("#334155")))
    covered, required, missing = _coverage_data(turns)
    verdict_color = {
        "strong_signal": "#DCEFE5",
        "mixed_signal": "#FFF1D6",
        "insufficient_data": "#E8EEF4",
    }.get(report.recommendation, "#E8EEF4")
    verdict_panel = Table(
        [[Paragraph(html.escape(RECOMMENDATION_LABELS.get(report.recommendation, report.recommendation)), styles["ReportBodyRu"]),
          Paragraph(f"Покрытие тем: {covered}/{required}", styles["ReportBodyRu"])]],
        colWidths=[95 * mm, 65 * mm],
        hAlign="LEFT",
    )
    verdict_panel.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(verdict_color)),
        ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#CBD5E1")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story = [
        Paragraph(html.escape(title), styles["ReportTitleRu"]),
        Paragraph("Вердикт и покрытие", styles["ReportHeadingRu"]),
        verdict_panel,
        Paragraph(f"Покрытые темы: {covered} из {required} · {'●' * covered}{'○' * (required - covered)}", styles["ReportBodyRu"]),
        Paragraph("Не проверены: " + html.escape(", ".join(TOPIC_LABELS.get(topic, topic) for topic in missing) if missing else "все обязательные темы"), styles["ReportBodyRu"]),
        Paragraph("Оценки", styles["ReportHeadingRu"]),
    ]
    score_rows = [["Критерий", "Шкала 1–5"]]
    for criterion, score in report.scores.items():
        score_rows.append([CRITERION_LABELS.get(criterion, criterion), _score_bar(score)])
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
    labels = _source_labels(turns)
    if report.evidence:
        for index, raw_item in enumerate(report.evidence):
            item = _as_mapping(raw_item)
            title = f"{CRITERION_LABELS.get(item.get('criterion', ''), item.get('criterion', ''))} · {labels.get(item.get('source_turn_id'), 'Источник не указан')}"
            quote_text = Paragraph(
                html.escape(str(item.get("quote", ""))).replace("\n", "<br/>"),
                styles["ReportQuoteRu"],
            )
            quote = Table(
                [[quote_text]],
                colWidths=[A4[0] - 44 * mm],
                splitByRow=1,
                splitInRow=1,
                hAlign="LEFT",
            )
            quote.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F5F8FB")),
                ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor("#CBD5E1")),
                ("LEFTPADDING", (0, 0), (-1, -1), 4 * mm),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4 * mm),
                ("TOPPADDING", (0, 0), (-1, -1), 4 * mm),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4 * mm),
            ]))
            if index == 0:
                story.append(KeepTogether([
                    Paragraph("Подтверждения", styles["ReportHeadingRu"]),
                    Paragraph(html.escape(title), styles["ReportBodyRu"]),
                ]))
            else:
                story.append(KeepTogether([Paragraph(html.escape(title), styles["ReportBodyRu"])]))
            story.append(quote)
            story.append(Spacer(1, 3 * mm))
            story.append(Paragraph(html.escape(str(item.get("observation", ""))), styles["ReportBodyRu"]))
    else:
        story.append(Paragraph("Подтверждения", styles["ReportHeadingRu"]))
        story.append(Paragraph("Подтверждения не приведены.", styles["ReportBodyRu"]))
    _pdf_list_section(story, "Неопределённости", report.uncertainties, styles, font_name)
    story.extend([Spacer(1, 5 * mm), KeepTogether([
        Paragraph("Об ограничениях оценки", styles["ReportHeadingRu"]),
        Paragraph(html.escape(report.disclaimer), styles["ReportBodyRu"]),
    ])])
    document = SimpleDocTemplate(
        str(pdf_path), pagesize=A4,
        rightMargin=22 * mm, leftMargin=22 * mm,
        topMargin=20 * mm, bottomMargin=20 * mm,
        title=title,
        author="AI Mock Interviewer",
    )
    def draw_page_number(canvas, doc):
        canvas.saveState()
        canvas.setFont(font_name, 8)
        canvas.setFillColor(colors.HexColor("#64748B"))
        canvas.drawRightString(A4[0] - 22 * mm, 11 * mm, f"Страница {doc.page}")
        canvas.restoreState()

    document.build(story, onFirstPage=draw_page_number, onLaterPages=draw_page_number)


def _pdf_list_section(story, title: str, values: list[str], styles, font_name: str) -> None:
    story.append(Paragraph(html.escape(title), styles["ReportHeadingRu"]))
    if values:
        for value in values:
            story.append(Paragraph(f"•&nbsp;&nbsp;{html.escape(value)}", styles["ReportBodyRu"]))
    else:
        story.append(Paragraph("Не указано.", styles["ReportBodyRu"]))
