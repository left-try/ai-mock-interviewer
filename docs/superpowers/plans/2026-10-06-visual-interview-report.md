# Visual Interview Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make interview Markdown and PDF reports visually clear, evidence-led, searchable, and safe for incomplete interviews.

**Architecture:** Derive verdict, topic coverage, score bars, and quote cards from the validated report and saved turns. Render the same semantics in Markdown and ReportLab flowables; paginate grouped evidence blocks safely.

**Tech Stack:** Python 3.11+, ReportLab, pypdf, pytest, Poppler `pdftoppm` for visual review.

**Spec:** `docs/superpowers/specs/2026-10-06-voice-test-and-report-design.md`

## Global Constraints

- Use only the existing qualitative recommendations: `strong_signal`, `mixed_signal`, `insufficient_data`.
- Show topic coverage as actual covered/required topics and criterion scores as separate 1–5 bars.
- Never invent scores for missing evidence or label rubric scores as hiring probability.
- Preserve exact evidence quote validation and searchable Cyrillic PDF text.
- Visual review must render every page; text extraction alone does not establish layout correctness.

## Review Focus

- `insufficient_data` with no scores must not display a numeric rating or percentage.
- Long candidate quotes and observations must wrap inside their cards without overlap or clipping.
- Evidence groups must not separate the source label, quote, and observation across pages confusingly.
- Empty strengths/evidence/uncertainties remain legible and do not collapse section spacing.
- A multi-page report must not strand its disclaimer or orphan a section heading.

---

### Task 1: Define report infographic data and Markdown contract

**Files:**
- Modify: `src/mock_interviewer/report_export.py`
- Test: `tests/test_report_exports.py`

**Interfaces:**
- `_coverage_data(turns) -> tuple[int, int, list[str]]` returns covered count, required count, and missing topic labels from existing interview progress rules.
- `_score_bar(score: int | None) -> str` returns a five-cell bar plus score when known, or the explicit insufficient-data label when unknown.
- `render_report_markdown(report, turns) -> str` adds a verdict/coverage summary and criterion bars while preserving all current sections and evidence sources.

- [ ] **Step 1: Add these failing tests** in `tests/test_report_exports.py`:
  - `test_markdown_shows_covered_and_required_topics`: assert the coverage summary matches topic IDs present in turns and the required-topic total.
  - `test_markdown_score_bar_contains_only_known_five_point_score`: assert a score of 4 renders four filled cells and `4/5`.
  - `test_insufficient_data_report_does_not_render_score_or_probability`: with all scores `None`, assert explicit insufficient-data labels and no aggregate percentage/hiring probability.
  - `test_verdict_label_matches_validated_recommendation`: parameterize all three recommendations and assert the correct localized verdict.
- [ ] **Step 2: Run report tests and verify the new assertions fail** on the current plain table.
- [ ] **Step 3: Implement data-derived labels and Markdown bars** without changing report scoring or recommendation validation.
- [ ] **Step 4: Run `pytest tests/test_report_exports.py -q`** and verify all Markdown contracts pass.
- [ ] **Step 5: Commit** as `[feat] add coverage and score visuals to reports`.

### Task 2: Rebuild PDF layout with pagination-safe components

**Files:**
- Modify: `src/mock_interviewer/report_export.py`
- Test: `tests/test_report_exports.py`

**Interfaces:**
- Add a report verdict panel and reusable score-bar flowable/table helper.
- Add an evidence-card builder that groups source label, quote, and observation in one padded flowable with page-splitting behavior defined for long cards.
- Add page callbacks for consistent page numbering and restrained header/footer styling.

- [ ] **Step 1: Add these failing PDF content tests** in `tests/test_report_exports.py`:
  - `test_pdf_contains_verdict_coverage_and_scored_criteria`: extract searchable text and assert verdict, coverage counts, and criterion labels/scores.
  - `test_pdf_long_evidence_quote_remains_searchable`: use a multi-line quote and observation, export, and assert both full strings are extractable.
  - `test_pdf_insufficient_data_has_no_fabricated_aggregate`: assert no probability/aggregate score label is present when all criteria are unscored.
  - `test_pdf_empty_sections_keep_headings_and_placeholders`: with empty strengths/evidence/uncertainties, assert all three headings and their explicit empty-state text remain in the PDF.
- [ ] **Step 2: Run PDF tests and verify expected content/layout helper failures.**
- [ ] **Step 3: Implement the PDF component builders** with explicit padding, spacing, and `KeepTogether` only where the complete block can fit a page.
- [ ] **Step 4: Run focused tests** and verify extracted PDF text includes every section and Cyrillic quote.
- [ ] **Step 5: Commit** as `[fix] prevent PDF report overlap and orphaned pages`.

### Task 3: Inspect representative rendered PDFs

**Files:**
- Test fixtures: `tests/test_report_exports.py`
- Generated intermediates: `tmp/pdfs/` (remove after inspection)

- [ ] **Step 1: Generate PDFs** for insufficient evidence, a fully scored interview, long quotes, empty lists, and a fixture long enough to span multiple pages; assert the long fixture has more than one page.
- [ ] **Step 2: Render every page with `pdftoppm -png -r 130`.**
- [ ] **Step 3: Inspect every page** for overlaps, clipped text, Cyrillic glyph defects, awkward page breaks, and disclaimer placement; repair and re-render if any defect appears.
- [ ] **Step 4: Reopen PDFs with `pypdf`** and verify searchable report text, expected page count greater than one for the long fixture, and complete section text.
- [ ] **Step 5: Remove rendered intermediates and commit any visual fixes** as `[fix] polish interview report pagination`.

