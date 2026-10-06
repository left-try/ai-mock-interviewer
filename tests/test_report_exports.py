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
    from mock_interviewer import report_export

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


def test_markdown_shows_data_derived_verdict_coverage_and_known_score_bar():
    markdown = render_report_markdown(_report(), _turns())

    assert "Смешанный сигнал" in markdown
    assert "Покрытие тем" in markdown
    assert "1/8" in markdown
    assert "Мотивация" in markdown
    assert "4/5" in markdown
    assert "2/5" in markdown


def test_insufficient_data_does_not_invent_aggregate_score_or_hiring_probability():
    insufficient = InterviewReport(
        recommendation="insufficient_data",
        scores={"motivation": None, "teamwork": None},
        strengths=[],
        growth_areas=[],
        evidence=[],
        uncertainties=["Недостаточно ответов."],
        disclaimer="Учебная обратная связь для практики.",
    )

    markdown = render_report_markdown(insufficient, _turns())

    assert "Недостаточно данных для оценки" in markdown
    assert "Недостаточно данных" in markdown
    assert "вероятность найма" not in markdown.lower()
    assert "шанс найма" not in markdown.lower()
    assert "Средний балл" not in markdown


def test_pdf_contains_same_verdict_coverage_and_score_semantics(tmp_path: Path):
    files = export_report_files(_report(), _turns(), "visual-contract", output_dir=tmp_path)
    pdf_text = "\n".join(
        page.extract_text() or "" for page in PdfReader(files["pdf_path"]).pages
    )

    assert "Смешанный сигнал" in pdf_text
    assert "Покрытие тем" in pdf_text
    assert "1/8" in pdf_text
    assert "Мотивация" in pdf_text
    assert "4/5" in pdf_text
    assert "2/5" in pdf_text


def test_pdf_long_evidence_and_disclaimer_remain_searchable_across_pages(tmp_path: Path):
    long_quote = "Это подробный кандидатский ответ. " * 90
    long_observation = "Наблюдение интервьюера с контекстом. " * 50
    report = InterviewReport(
        recommendation="mixed_signal",
        scores={"motivation": 4},
        strengths=["Инициативность."],
        growth_areas=["Практика командной разработки."],
        evidence=[{
            "criterion": "motivation", "source_turn_id": "turn-2",
            "quote": long_quote, "observation": long_observation,
        }],
        uncertainties=["Не проверен опыт проектирования API."],
        disclaimer="Учебная обратная связь для практики; это не решение о найме.",
    )
    turns = [
        Turn("turn-1", "interviewer", "Почему backend?", topic_id="motivation"),
        Turn("turn-2", "candidate", long_quote),
    ]

    files = export_report_files(report, turns, "long-report", output_dir=tmp_path)
    pages = PdfReader(files["pdf_path"]).pages
    pdf_text = "\n".join(page.extract_text() or "" for page in pages)
    normalized_pdf_text = " ".join(pdf_text.split())

    assert len(pages) > 1
    assert " ".join(long_quote[:100].split()) in normalized_pdf_text
    assert " ".join(long_observation[:100].split()) in normalized_pdf_text
    assert "это не решение о найме" in normalized_pdf_text
