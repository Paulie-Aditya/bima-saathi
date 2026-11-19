import { WS_BASE } from "./api";

export type ServerEvent = { type: string; session_id?: string; ts?: number; [k: string]: any };

/** One socket per member, reconnecting. Returns a close function. */
export function subscribe(memberId: string, onEvent: (e: ServerEvent) => void, onState?: (up: boolean) => void) {
  let ws: WebSocket | null = null;
  let closed = false;
  let ping: ReturnType<typeof setInterval> | undefined;
  let retry: ReturnType<typeof setTimeout> | undefined;

  const open = () => {
    ws = new WebSocket(`${WS_BASE}/ws/${memberId}`);
    ws.onopen = () => {
      onState?.(true);
      ping = setInterval(() => ws?.readyState === 1 && ws.send("ping"), 20000);
    };
    ws.onmessage = (m) => {
      try {
        onEvent(JSON.parse(m.data));
      } catch {
        /* ignore */
      }
    };
    ws.onclose = () => {
      onState?.(false);
      clearInterval(ping);
      if (!closed) retry = setTimeout(open, 1500);
    };
  };
  open();
  return () => {
    closed = true;
    clearInterval(ping);
    clearTimeout(retry);
    ws?.close();
  };
}
