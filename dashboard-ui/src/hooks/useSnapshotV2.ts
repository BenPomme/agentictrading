import { useState, useEffect, useRef, useCallback } from 'react';
import type { SnapshotV2 } from '../types/snapshot';

const REFRESH_MS = 5000;

export function useSnapshotV2() {
  const [data, setData] = useState<SnapshotV2 | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const prevRef = useRef<SnapshotV2 | null>(null);

  const fetch_ = useCallback(async () => {
    try {
      const res = await fetch('/api/snapshot/v2', { cache: 'no-store' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const snap: SnapshotV2 = await res.json();
      prevRef.current = data;
      setData(snap);
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  }, [data]);

  useEffect(() => {
    fetch_();
    const id = setInterval(fetch_, REFRESH_MS);
    return () => clearInterval(id);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return { data, prev: prevRef.current, loading, error };
}
