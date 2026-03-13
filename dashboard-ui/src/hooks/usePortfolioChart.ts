import { useState, useCallback } from 'react';
import type { ChartPayload } from '../types/snapshot';

export function usePortfolioChart() {
  const [charts, setCharts] = useState<Record<string, ChartPayload>>({});
  const [loading, setLoading] = useState<Record<string, boolean>>({});

  const fetchChart = useCallback(async (portfolioId: string) => {
    if (charts[portfolioId]) return charts[portfolioId];
    setLoading((p) => ({ ...p, [portfolioId]: true }));
    try {
      const res = await fetch(`/api/portfolio/${portfolioId}/chart`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const payload: ChartPayload = await res.json();
      setCharts((p) => ({ ...p, [portfolioId]: payload }));
      return payload;
    } finally {
      setLoading((p) => ({ ...p, [portfolioId]: false }));
    }
  }, [charts]);

  return { charts, loading, fetchChart };
}
