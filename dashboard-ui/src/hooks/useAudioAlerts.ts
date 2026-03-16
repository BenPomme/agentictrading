import { useCallback, useEffect, useRef, useState } from 'react';

const STORAGE_KEY = 'nebula_audio_alerts_enabled';

function createAudioContext(): AudioContext | null {
  if (typeof window === 'undefined' || typeof window.AudioContext === 'undefined') {
    return null;
  }
  try {
    return new window.AudioContext();
  } catch {
    return null;
  }
}

function playBeep(ctx: AudioContext, frequency: number, durationMs: number) {
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();

  osc.type = 'sine';
  osc.frequency.value = frequency;

  gain.gain.setValueAtTime(0.001, ctx.currentTime);
  gain.gain.exponentialRampToValueAtTime(0.2, ctx.currentTime + 0.01);
  gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + durationMs / 1000);

  osc.connect(gain);
  gain.connect(ctx.destination);

  osc.start();
  osc.stop(ctx.currentTime + durationMs / 1000 + 0.05);
}

export function useAudioAlerts() {
  const [audioEnabled, setAudioEnabled] = useState<boolean>(() => {
    if (typeof window === 'undefined') return false;
    try {
      return window.localStorage.getItem(STORAGE_KEY) === 'true';
    } catch {
      return false;
    }
  });

  const hasInteractedRef = useRef(false);
  const ctxRef = useRef<AudioContext | null>(null);

  useEffect(() => {
    if (typeof window === 'undefined') return;

    const markInteracted = () => {
      if (!hasInteractedRef.current) {
        hasInteractedRef.current = true;
        if (!ctxRef.current) {
          ctxRef.current = createAudioContext();
        }
      }
    };

    window.addEventListener('pointerdown', markInteracted, { once: true });
    window.addEventListener('keydown', markInteracted, { once: true });

    return () => {
      window.removeEventListener('pointerdown', markInteracted);
      window.removeEventListener('keydown', markInteracted);
    };
  }, []);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    try {
      window.localStorage.setItem(STORAGE_KEY, audioEnabled ? 'true' : 'false');
    } catch {
      // ignore
    }
  }, [audioEnabled]);

  const ensureContext = useCallback((): AudioContext | null => {
    if (!hasInteractedRef.current) return null;
    if (!ctxRef.current) {
      ctxRef.current = createAudioContext();
    }
    return ctxRef.current;
  }, []);

  const playAgentRun = useCallback(() => {
    if (!audioEnabled) return;
    const ctx = ensureContext();
    if (!ctx) return;
    playBeep(ctx, 880, 140);
  }, [audioEnabled, ensureContext]);

  const playPaperTrade = useCallback(() => {
    if (!audioEnabled) return;
    const ctx = ensureContext();
    if (!ctx) return;
    playBeep(ctx, 440, 120);
  }, [audioEnabled, ensureContext]);

  const toggleAudio = useCallback(() => {
    setAudioEnabled(prev => !prev);
  }, []);

  return {
    audioEnabled,
    toggleAudio,
    playAgentRun,
    playPaperTrade,
  };
}

