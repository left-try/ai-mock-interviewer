import { useCallback, useEffect, useRef, useState } from "react";
import { Excalidraw } from "@excalidraw/excalidraw";
import {
  diffScenes,
  emptyProjection,
  projectScene,
  type BoardEvent,
  type SceneProjection,
} from "./whiteboard-adapter";

const apiRoot = "/api/sessions";
const debounceMs = 450;

type PersistedEvent = {
  event_id: string;
  sequence: number;
  kind: string;
};

type ResettableSceneApi = {
  resetScene: () => void;
  onPointerDown: (callback: () => void) => () => void;
  onPointerUp: (callback: () => void) => () => void;
};

const sessionFromLocation = (): string => {
  const query = new URLSearchParams(window.location.search);
  return query.get("session_id") || `demo-${crypto.randomUUID()}`;
};

export function WhiteboardApp() {
  const [sessionId, setSessionId] = useState(sessionFromLocation);
  const [api, setApi] = useState<ResettableSceneApi | null>(null);
  const [status, setStatus] = useState("Connecting to local interview service…");
  const [lastSaved, setLastSaved] = useState(0);
  const [sessionReady, setSessionReady] = useState(false);
  const [pendingCount, setPendingCount] = useState(0);
  const [unsentScene, setUnsentScene] = useState(false);
  const previousRef = useRef<SceneProjection>(emptyProjection());
  const latestRef = useRef<SceneProjection>(emptyProjection());
  const timerRef = useRef<number | undefined>(undefined);
  const queueRef = useRef<Array<{ event: BoardEvent; eventId: string }>>([]);
  const sequenceRef = useRef(1);
  const sendingRef = useRef(false);
  const pointerDownRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    setSessionReady(false);
    setStatus("Connecting to local interview service…");
    fetch(`${apiRoot}/${encodeURIComponent(sessionId)}/events?after_cursor=0`, { cache: "no-store" })
      .then(async (response) => {
        const body = await response.json();
        if (!response.ok) throw new Error(body.error || "Could not open the board session");
        if (body.next_cursor > 0) {
          throw new Error("This session already has saved events. Start a fresh session to avoid duplicating the board.");
        }
        return body;
      })
      .then((body) => {
        if (cancelled) return;
        sequenceRef.current = body.next_cursor + 1;
        previousRef.current = emptyProjection();
        latestRef.current = emptyProjection();
        setSessionReady(true);
        setStatus("Connected. Board changes are saved locally as you draw.");
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setStatus(error instanceof Error ? error.message : "Could not connect to the local interview service.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  const flushQueue = useCallback(async () => {
    if (sendingRef.current || !sessionReady) return;
    sendingRef.current = true;
    while (queueRef.current.length) {
      const queued = queueRef.current[0];
      const clientSequence = sequenceRef.current;
      try {
        const response = await fetch(`${apiRoot}/${encodeURIComponent(sessionId)}/events`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            event_id: queued.eventId,
            client_sequence: clientSequence,
            kind: queued.event.kind,
            payload: queued.event.payload,
          }),
        });
        const result: PersistedEvent & { error?: string } = await response.json();
        if (!response.ok) throw new Error(result.error || "The board change was not saved");
        queueRef.current.shift();
        setPendingCount(queueRef.current.length);
        sequenceRef.current += 1;
        setLastSaved(result.sequence);
        setStatus(`Saved ${result.kind} as ${result.event_id} (event ${result.sequence}).`);
      } catch (error) {
        setStatus(error instanceof Error ? `Save failed: ${error.message}. Use Retry to resend.` : "Save failed. Use Retry to resend.");
        break;
      }
    }
    sendingRef.current = false;
  }, [sessionId, sessionReady]);

  const commitLatestScene = useCallback(() => {
    if (timerRef.current !== undefined) window.clearTimeout(timerRef.current);
    timerRef.current = undefined;
    const events = diffScenes(previousRef.current, latestRef.current);
    previousRef.current = latestRef.current;
    setUnsentScene(false);
    if (events.length === 0) return;
    queueRef.current.push(...events.map((event) => ({ event, eventId: crypto.randomUUID() })));
    setPendingCount(queueRef.current.length);
    void flushQueue();
  }, [flushQueue]);

  useEffect(() => {
    if (!api) return;
    const unsubscribeDown = api.onPointerDown(() => {
      pointerDownRef.current = true;
      if (timerRef.current !== undefined) window.clearTimeout(timerRef.current);
      timerRef.current = undefined;
    });
    const unsubscribeUp = api.onPointerUp(() => {
      pointerDownRef.current = false;
      commitLatestScene();
    });
    return () => {
      unsubscribeDown();
      unsubscribeUp();
    };
  }, [api, commitLatestScene]);

  const scheduleSceneFlush = useCallback(
    (elements: readonly unknown[]) => {
      latestRef.current = projectScene(elements as Parameters<typeof projectScene>[0]);
      setUnsentScene(true);
      if (timerRef.current !== undefined) window.clearTimeout(timerRef.current);
      timerRef.current = pointerDownRef.current
        ? undefined
        : window.setTimeout(commitLatestScene, debounceMs);
    },
    [commitLatestScene],
  );

  const beginFreshSession = () => {
    const newId = `demo-${crypto.randomUUID()}`;
    if (timerRef.current !== undefined) window.clearTimeout(timerRef.current);
    timerRef.current = undefined;
    queueRef.current = [];
    setPendingCount(0);
    setUnsentScene(false);
    sequenceRef.current = 1;
    previousRef.current = emptyProjection();
    latestRef.current = emptyProjection();
    api?.resetScene();
    setSessionId(newId);
    window.history.replaceState(null, "", `?session_id=${encodeURIComponent(newId)}`);
  };

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand">
          <span className="eyebrow">SYSTEM DESIGN PRACTICE</span>
          <h1>Whiteboard</h1>
        </div>
        <div className="session-control">
          <label htmlFor="session">Session</label>
          <code id="session">{sessionId}</code>
          <button type="button" onClick={beginFreshSession} disabled={pendingCount > 0 || (unsentScene && sessionReady)}>New session</button>
          <button type="button" onClick={() => void flushQueue()} disabled={pendingCount === 0}>Retry save</button>
        </div>
      </header>
      <aside className="notice">
        <strong>Local demo only.</strong> Draw synthetic system designs. This page records explicit Excalidraw changes; it does not inspect other tabs or capture your screen. Do not enter personal or confidential information.
      </aside>
      <section className="board-frame" aria-label="System design whiteboard">
        <Excalidraw
          excalidrawAPI={(value) => setApi(value)}
          onChange={(elements) => scheduleSceneFlush(elements)}
          UIOptions={{ canvasActions: { loadScene: false, export: false, saveAsImage: false } }}
        />
      </section>
      <footer className="statusbar" aria-live="polite">
        <span className={sessionReady ? "status-dot ready" : "status-dot"} />
        <span>{status}</span>
        <span className="event-count">Saved events: {lastSaved}</span>
      </footer>
    </main>
  );
}
