import { useState, useEffect, useRef, useCallback } from 'react';
import type { DashboardSnapshot } from '../types/snapshot';

const REFRESH_MS = 5000;

export function useSnapshot() {
  const [data, setData] = useState<DashboardSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const prevRef = useRef<DashboardSnapshot | null>(null);

  const fetch_ = useCallback(async () => {
    try {
      const res = await fetch('/api/snapshot', { cache: 'no-store' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const snap: DashboardSnapshot = await res.json();
      prevRef.current = data;
      setData(snap);
      setError(null);
      setLastUpdated(new Date());
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

  return { data, prev: prevRef.current, loading, error, lastUpdated };
}
