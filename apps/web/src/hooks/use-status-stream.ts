"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { eventsApi, getAccessToken, type SnapshotData } from "@/api/client";

/**
 * 状态流通道状态（DECISIONS.md D-006：SSE 主通道，失败后 30 秒轮询降级）。
 */
export type StreamChannel = "connecting" | "sse" | "polling";

/**
 * 订阅状态流：SSE 主通道，失败自动降级为 30 秒轮询，界面明示当前通道。
 *
 * - EventSource 自动携带 Last-Event-ID 续传（浏览器内建）；
 * - SSE 连接失败或连续异常 → 切换到 `GET /events/snapshot` 每 30 秒轮询；
 * - 返回的 `channel` 供 UI 明示"实时 / 轮询降级"（主基线 §10.1：降级可感知）。
 */
export function useStatusStream(enabled: boolean): {
  snapshot: SnapshotData | null;
  channel: StreamChannel;
  error: string | null;
  refresh: () => void;
} {
  const [snapshot, setSnapshot] = useState<SnapshotData | null>(null);
  const [channel, setChannel] = useState<StreamChannel>("connecting");
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const sourceRef = useRef<EventSource | null>(null);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const refresh = useCallback(() => setTick((value) => value + 1), []);

  useEffect(() => {
    if (!enabled) {
      return;
    }
    let cancelled = false;
    const POLLING_INTERVAL_MS = 30_000;

    const stopPolling = () => {
      if (pollTimerRef.current !== null) {
        clearInterval(pollTimerRef.current);
        pollTimerRef.current = null;
      }
    };

    const startPolling = () => {
      if (cancelled || pollTimerRef.current !== null) {
        return;
      }
      setChannel("polling");
      const poll = () => {
        eventsApi
          .snapshot()
          .then((data) => {
            if (!cancelled) {
              setSnapshot(data);
              setError(null);
            }
          })
          .catch((cause: unknown) => {
            if (!cancelled) {
              setError(cause instanceof Error ? cause.message : "轮询失败");
            }
          });
      };
      void poll();
      pollTimerRef.current = setInterval(poll, POLLING_INTERVAL_MS);
    };

    const openStream = () => {
      const token = getAccessToken();
      if (!token) {
        return;
      }
      const source = new EventSource(eventsApi.streamUrl());
      sourceRef.current = source;

      source.onopen = () => {
        if (!cancelled) {
          setChannel("sse");
          setError(null);
          stopPolling();
        }
      };
      source.onmessage = (event) => {
        try {
          setSnapshot(JSON.parse(event.data) as SnapshotData);
        } catch {
          // 快照帧解析失败不中断连接，等待下一帧
        }
      };
      source.addEventListener("snapshot", (event) => {
        try {
          setSnapshot(JSON.parse((event as MessageEvent).data) as SnapshotData);
        } catch {
          // 同上
        }
      });
      source.onerror = () => {
        // EventSource 会自动重连；为避免无限重试风暴，失败即降级轮询
        source.close();
        if (!cancelled) {
          startPolling();
        }
      };
    };

    openStream();

    return () => {
      cancelled = true;
      stopPolling();
      sourceRef.current?.close();
      sourceRef.current = null;
    };
  }, [enabled, tick]);

  return { snapshot, channel, error, refresh };
}
