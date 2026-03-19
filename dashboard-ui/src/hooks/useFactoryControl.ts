import { useCallback, useState } from 'react';

export function useFactoryControl(factoryRunning: boolean | undefined) {
  const [pending, setPending] = useState(false);

  const toggle = useCallback(async () => {
    setPending(true);
    try {
      const action = factoryRunning ? 'stop' : 'start';
      const res = await fetch('/api/factory/control', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return (await res.json()) as { factory_running: boolean };
    } finally {
      setPending(false);
    }
  }, [factoryRunning]);

  return { toggle, pending };
}
