import { useCallback, useState } from 'react';

export function useFactoryControl(factoryPaused: boolean | undefined) {
  const [pending, setPending] = useState(false);

  const toggle = useCallback(async () => {
    setPending(true);
    try {
      const action = factoryPaused ? 'resume' : 'pause';
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
  }, [factoryPaused]);

  return { toggle, pending };
}
