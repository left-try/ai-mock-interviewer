"""Contracts for visible Markdown and downloadable PDF interview reports."""

from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader

from mock_interviewer.domain.models import InterviewReport, Turn
from mock_interviewer.report_export import export_report_files, render_report_markdown


def _report() -> InterviewReport:
    return InterviewReport(
        recommendation="mixed_signal",
        scores={"motivation": 4, "teamwork": 2},
        strengths=["Открыто рассказал о сложном рабочем случае."],
        growth_areas=["Подготовить конкретный пример командной разработки."],
        evidence=[
            {
                "criterion": "motivation",
                "source_turn_id": "turn-2",
                "quote": "Хочу развивать backend-навыки.",
                "observation": "Назвал конкретную область профессионального роста.",
            }
        ],
        uncertainties=["Не удалось подробно проверить проектирование API."],
        disclaimer="Учебная обратная связь для практики; это не решение о найме.",
    )


def _turns() -> list[Turn]:
    return [
        Turn("turn-1", "interviewer", "Почему backend?", topic_id="motivation"),
        Turn("turn-2", "candidate", "Хочу развивать backend-навыки."),
    ]


def test_markdown_contains_full_report_and_readable_evidence_sources():
    markdown = render_report_markdown(_report(), _turns())

    assert "# Отчёт HR-интервью — Backend Internship" in markdown
    assert "## Рекомендация" in markdown
    assert "Смешанный сигнал" in markdown
    assert "## Сильные стороны" in markdown
    assert "## Зоны роста" in markdown
    assert "## Подтверждения" in markdown
    assert "Ответ кандидата №1" in markdown
    assert "Хочу развивать backend-навыки." in markdown
    assert "## Неопределённости" in markdown
    assert "это не решение о найме" in markdown


def test_export_writes_cyrillic_markdown_and_searchable_pdf(tmp_path: Path):
    files = export_report_files(_report(), _turns(), "session-test-123", output_dir=tmp_path)

    markdown_path = Path(files["markdown_path"])
    pdf_path = Path(files["pdf_path"])
    assert markdown_path.exists()
    assert pdf_path.exists()
    assert markdown_path.suffix == ".md"
    assert pdf_path.suffix == ".pdf"
    assert markdown_path.read_text(encoding="utf-8") == files["markdown"]
    assert pdf_path.read_bytes().startswith(b"%PDF-")

    extracted_pdf_text = "\n".join(page.extract_text() or "" for page in PdfReader(pdf_path).pages)
    assert "Отчёт HR-интервью" in extracted_pdf_text
    assert "Хочу развивать backend-навыки." in extracted_pdf_text
    assert "это не решение о найме" in extracted_pdf_text


def test_reexporting_same_session_replaces_the_same_files(tmp_path: Path, monkeypatch):
    import mock_interviewer.report_export as report_export

    class AdvancingClock:
        from datetime import datetime as _datetime

        @classmethod
        def now(cls):
            cls._current = getattr(cls, "_current", cls._datetime(2026, 10, 5, 10, 0, 0))
            current = cls._current
            from datetime import timedelta
            cls._current += timedelta(seconds=1)
            return current

    monkeypatch.setattr(report_export, "datetime", AdvancingClock, raising=False)

    first = export_report_files(_report(), _turns(), "same-session", output_dir=tmp_path)
    second = export_report_files(_report(), _turns(), "same-session", output_dir=tmp_path)

    assert second["markdown_path"] == first["markdown_path"]
    assert second["pdf_path"] == first["pdf_path"]
    assert len(list(tmp_path.iterdir())) == 2
