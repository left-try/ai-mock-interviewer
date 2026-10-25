export type BoardEvent = {
  kind:
    | "node_added"
    | "node_updated"
    | "node_moved"
    | "node_resized"
    | "node_deleted"
    | "edge_added"
    | "edge_updated"
    | "edge_deleted";
  payload: Record<string, string | number>;
};

type SceneElement = {
  id: string;
  type: string;
  isDeleted?: boolean;
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  angle?: number;
  text?: string;
  containerId?: string | null;
  startBinding?: { elementId?: string | null } | null;
  endBinding?: { elementId?: string | null } | null;
};

type NodeProjection = {
  node_id: string;
  label: string;
  shape_type: "rectangle" | "ellipse" | "diamond";
  x: number;
  y: number;
  width: number;
  height: number;
  angle: number;
};

type EdgeProjection = {
  edge_id: string;
  source_id: string;
  target_id: string;
  label?: string;
};

export type SceneProjection = {
  nodes: Map<string, NodeProjection>;
  edges: Map<string, EdgeProjection>;
};

const nodeTypes = new Set<NodeProjection["shape_type"]>(["rectangle", "ellipse", "diamond"]);
const cleanText = (value: unknown): string =>
  typeof value === "string" ? value.replace(/\s+/g, " ").trim().slice(0, 200) : "";
const finite = (value: number | undefined): number =>
  typeof value === "number" && Number.isFinite(value) ? value : 0;

export function projectScene(elements: readonly SceneElement[]): SceneProjection {
  const live = elements.filter((element) => !element.isDeleted);
  const labels = new Map<string, string>();
  for (const element of live) {
    if (element.type === "text" && element.containerId) {
      const label = cleanText(element.text);
      if (label) labels.set(element.containerId, label);
    }
  }

  const nodes = new Map<string, NodeProjection>();
  for (const element of live) {
    if (!nodeTypes.has(element.type as NodeProjection["shape_type"])) continue;
    const shapeType = element.type as NodeProjection["shape_type"];
    nodes.set(element.id, {
      node_id: element.id,
      label: labels.get(element.id) || shapeType,
      shape_type: shapeType,
      x: finite(element.x),
      y: finite(element.y),
      width: Math.max(0, finite(element.width)),
      height: Math.max(0, finite(element.height)),
      angle: finite(element.angle),
    });
  }

  const edges = new Map<string, EdgeProjection>();
  for (const element of live) {
    if (element.type !== "arrow") continue;
    const sourceId = element.startBinding?.elementId;
    const targetId = element.endBinding?.elementId;
    if (!sourceId || !targetId || !nodes.has(sourceId) || !nodes.has(targetId)) continue;
    edges.set(element.id, {
      edge_id: element.id,
      source_id: sourceId,
      target_id: targetId,
      label: labels.get(element.id),
    });
  }
  return { nodes, edges };
}

export function diffScenes(previous: SceneProjection, current: SceneProjection): BoardEvent[] {
  const events: BoardEvent[] = [];
  const edgeDeletions: BoardEvent[] = [];
  const nodeDeletions: BoardEvent[] = [];

  for (const [id, next] of current.nodes) {
    const prior = previous.nodes.get(id);
    if (!prior) {
      events.push({
        kind: "node_added",
        payload: {
          node_id: id,
          label: next.label,
          shape_type: next.shape_type,
          x: next.x,
          y: next.y,
          width: next.width,
          height: next.height,
          angle: next.angle,
        },
      });
      continue;
    }
    if (prior.label !== next.label) events.push({ kind: "node_updated", payload: { node_id: id, label: next.label } });
    if (prior.x !== next.x || prior.y !== next.y) {
      events.push({ kind: "node_moved", payload: { node_id: id, x: next.x, y: next.y } });
    }
    if (prior.width !== next.width || prior.height !== next.height || prior.angle !== next.angle) {
      events.push({
        kind: "node_resized",
        payload: { node_id: id, width: next.width, height: next.height, angle: next.angle },
      });
    }
  }

  for (const [id, next] of current.edges) {
    const prior = previous.edges.get(id);
    if (!prior) {
      events.push({ kind: "edge_added", payload: { ...next } });
      continue;
    }
    if (prior.source_id !== next.source_id || prior.target_id !== next.target_id || prior.label !== next.label) {
      events.push({ kind: "edge_updated", payload: { ...next } });
    }
  }
  for (const id of previous.edges.keys()) {
    if (!current.edges.has(id)) edgeDeletions.push({ kind: "edge_deleted", payload: { edge_id: id } });
  }
  for (const id of previous.nodes.keys()) {
    if (!current.nodes.has(id)) nodeDeletions.push({ kind: "node_deleted", payload: { node_id: id } });
  }
  return [...events, ...edgeDeletions, ...nodeDeletions];
}

export const emptyProjection = (): SceneProjection => ({ nodes: new Map(), edges: new Map() });
