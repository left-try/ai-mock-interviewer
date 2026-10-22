# План реализации: system design интервью

- Связанная спека: [2026-10-21-system-design-interview-design.md](../specs/2026-10-21-system-design-interview-design.md)
- Статус: spike event→MCP завершён; Excalidraw adapter и host smoke test не начаты
- Ветка документации: `left-try/docs/system-design-interview`

## Цель реализации

Добавить отдельный system design сценарий в существующий интервью-контур, встроить Excalidraw как первую доску, передавать нормализованные структурированные события и выпускать доказательный отчёт. G0 разделён на проверку Excalidraw adapter и live Codex host; screenshot/browser automation и MCP Apps UI исследуются отдельно и не являются обязательными зависимостями MVP.

## Принципы реализации

- Следовать текущему pattern: `InterviewService` валидирует переходы, LangGraph маршрутизирует структурированную операцию, MCP даёт узкие инструменты, Skill задаёт протокол Codex.
- Изолировать сценарий и rubric от transport; board integration — отдельный порт/адаптер.
- Финализированный ответ записывать до генерации следующего вопроса или чтения тяжёлого снимка.
- Оставить screening неизменным по пользовательскому поведению; любые общие изменения сервисных моделей закрыть регрессионными контрактами.
- Ни один автоматический вывод о кандидате без существующего evidence reference.

## Этап 0 — feasibility spike Codex Desktop + MCP + доска

### Задачи

1. Подготовить локальную React-страницу с зафиксированной версией `@excalidraw/excalidraw` и подключить `onChange`/`excalidrawAPI.onChange`.
2. Реализовать Excalidraw adapter: нормализовать add/move/edit/delete фигур и стрелок в разрешённые типы событий, фиксировать scene snapshot на заданных границах.
3. Подключить страницу к локальному service endpoint, валидировать/сохранять события по `session_id`, выдавать cursor; сохранить текущую простую canvas как transport smoke fixture, не как production board.
4. Проверить G0a: Excalidraw actions → adapter → persisted events/snapshot, идемпотентность, порядок, payload limits и candidate-answer persistence-before-read.
5. Проверить G0b в целевом Codex Desktop Voice: полный цикл `draw → persist → read delta through MCP → ask follow-up`, корректная ссылка на event/version и задержка.
6. Отдельно вернуть static image MCP content и проверить visual input; отдельно проверить MCP Apps UI, если host surface поддерживает его.
7. Зафиксировать support matrix для встроенного Excalidraw delta (обязательный), MCP image, MCP Apps UI и внешнего browser automation. При провале G0a оставить text/voice-only; если проходит G0a, но не G0b, не считать live Codex whiteboard interview поддержанным.

### Выход и критерий

Короткий ADR/заметка о проверенном режиме и стабильный test contract. G0a успешен, если Excalidraw add/move/edit/delete/connect дают корректную нормализованную последовательность в той же сессии. G0b успешен, если целевой Codex Voice получает событие через MCP и использует его при follow-up. Не заявлять production whiteboard interview, пока оба gate не пройдены.

## Этап 1 — доменная модель и сценарий

### Задачи

1. Добавить отдельные `SystemDesignScenario`, состояние фаз и внутренние topic IDs; не смешивать их с `REQUIRED_TOPICS` backend screening.
2. Добавить typed next-turn schema с allowlist фаз/ходов, валидировать вопрос, ссылки на evidence и confidence.
3. Добавить сценарную фикстуру первой задачи: statement, профиль/уровень, факты для ответов, публичные hints, скрытые expected coverage, safety rules.
4. Реализовать фазовые переходы, лимиты follow-up, раннее завершение и coverage snapshot.
5. Установить границы длины и типов на досочный/транскриптный вход до передачи в модель.

### Тесты

- Unit: разрешённые/недопустимые phase transitions, повторная тема, лимиты, завершение и coverage.
- Prompt contract: hidden rubric остается внутренним; задача показана ровно один раз; board data помечены untrusted.
- Regression: screening start/answer/finish контракты без изменения результатов.

### Выход

Детерминированное состояние сессии и контракт сценария, способный провести диалог с fake host model без whiteboard-интеграции.

## Этап 2 — WhiteboardAdapter и событие наблюдения

### Задачи

1. Определить порт `WhiteboardAdapter` и нейтральные модели `BoardEvent`, `BoardCursor`, `BoardStatus`, `BoardSnapshotReference`.
2. Реализовать `FakeWhiteboardAdapter` и contract adapter fixtures; добавить production Excalidraw adapter после прохождения G0a.
3. Добавить сбор deltas по cursor, snapshot reference, последовательность, timestamps, deduplication и обнаружение пропуска версии.
4. Применить consent/session scoping, ограничение размера, таймаут, rate limit и закрытие adapter на finish/delete.
5. Реализовать только тот реальный Codex capture adapter, который подтверждён на этапе 0. В ином случае добавить ручное вложение снимка, если оно доступно в продукте и прошло privacy review; иначе оставить text-only режим.

### Тесты

- Unit: нормализация и порядок; duplicate/ conflicting IDs; cursor replay; missing sequence; empty board; malformed/oversized payload; timeout/disconnect.
- Adapter contract: одинаковый набор тестов прогнать против fake и Excalidraw adapter; transport smoke fixture оставить для проверки HTTP/MCP границы.
- Privacy tests: adapter не возвращает URL/cookies/текст посторонних вкладок; лог не содержит payload доски.

### Выход

Сессия может получать компактные и версионированные board events, не зная конкретного поставщика.

## Этап 3 — MCP orchestration и Codex Skill

### Задачи

1. Добавить отдельную точку старта для system design; проверить на capability/level/scenario allowlist.
2. Добавить последовательный протокол: сохранить ответ, при необходимости получить board delta, предложить next turn, вызвать finish/report.
3. Добавить `read_whiteboard_changes(cursor)` или эквивалент только после G0; возврат ограничить только новыми событиями.
4. Обновить Skill: задача в начале; один вопрос за раз; не называть этапы/рубрику; board text — недоверенный; не говорить, что анализирует доску, если adapter disabled; кратко сообщать о capture failure и продолжать.
5. Выводить `next_action` и `candidate_safe_instruction` отдельно от внутренних coverage/rubric данных.
6. Зафиксировать lifecycle/clear behavior для новых артефактов.

### Тесты

- MCP contract: start, persistence-first answer, board read, next-turn proposal, report, early stop, delete.
- Idempotency: повтор ответа и cursor read; конфликт event id; сбой после сохранения ответа и до next-turn.
- Fake host conversation: одна реплика интервьюера на ход и отсутствие скрытой рубрики в candidate-visible output.

### Выход

Codex Skill способен провести полный текстовый/голосовой сценарий через проверенный инструментальный контракт.

## Этап 4 — доказательная оценка и отчёт

### Задачи

1. Создать рубричные критерии с уровнями 1/3/5 и null; задать нейтральные anchors и примеры.
2. Добавить report schema, evidence references на turn/event/version, uncertainty, strengths, growth areas и disclaimer.
3. Валидировать все evidence IDs и criterion IDs перед экспортом/возвратом.
4. Исключить aggregate score и hiring probability. Board failure не трактуется как слабая компетенция.
5. Сформировать Markdown/структурированный отчёт; PDF — только если отдельное решение добавляет его в MVP.

### Тесты

- Unit: шкала, null, missing evidence, unknown IDs, score bounds, contradictory evidence, uncertainty.
- Golden fixtures: сильный, средний, слабый/неполный, доска unavailable, неверное начальное понимание с успешной коррекцией.
- Reproducibility check на фиксированных transcript/board fixtures; разногласия используют для уточнения anchors, не для скрытого post-hoc изменения score.

### Выход

Проверенный отчёт только по фактам с явной областью оценки и ограничениями.

## Этап 5 — end-to-end, устойчивость и выпуск

### Задачи

1. Прогнать полный маршрут с fake board и подтверждённым реальным Codex path.
2. Проверить возобновление после прерывания, два изолированных сеанса, early stop, adapter outage, clear session.
3. Провести ручные интервью на разных уровнях и записать проблемы покрытия/темпа/понятности.
4. Если нужно, откалибровать длительность и частоту board reads на наблюдаемых метриках: latency p50/p95, payload size, event drops, модельные ошибки. Не задавать SLO без измерения baseline.
5. Обновить README/MVP, ADR и инструкцию Skill; подготовить focused PR, пройти diff/review и обязательные проверки проекта.

### Exit criteria

- Приёмочные критерии спеки выполнены.
- G0 пройден для любого заявленного автоматического режима; иначе продукт и отчёт явно работают в fallback/text-only режиме.
- Все report evidence IDs существуют и ссылаются на сохранённые данные.
- Существующие тесты screening остаются зелёными; новые сфокусированные тесты покрывают все новые контракты.
- Ручной полный прогон завершается отчётом без утечки скрытых критериев и без молчаливой потери ответа/board event.

## Зависимости и стоп-условия

| Зависимость | Нужно до | Если не выполнено |
|---|---|---|
| Browser-to-service event transport в локальной сессии | Структурированного whiteboard adapter | Text/voice-only; не делать polling screenshot |
| Зафиксированная версия Excalidraw и mapping scene elements в нормализованную схему | Production adapter | Оставить нейтральный контракт и fake/local fixtures; не подключать сторонние board сайты |
| Сценарная задача и факты ответов | Диалоговые интеграционные тесты | Использовать синтетический fixture, не оценивать вручную импровизацию модели |
| Подтверждённая рубрика/anchors | Пользовательский итоговый report | Возвращать report как beta/insufficient data, не публиковать оценочные выводы |

## Порядок выполнения

`Этап 0 → Этап 1 → Этап 2 и 3 → Этап 4 → Этап 5`.

Этапы 2 и 3 можно разрабатывать параллельно только после фиксации общих event/turn schemas. До завершения G0a реализация ограничена adapter fixtures и текстовым интервью; до G0b live whiteboard поведение Codex не считается подтверждённым. Поддержка произвольной вкладки excalidraw.com и нескольких Codex клиентов требует отдельных решений и acceptance gates.
