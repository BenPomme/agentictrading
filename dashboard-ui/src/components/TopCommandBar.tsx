import React, { useCallback, useEffect, useState } from 'react';
import { relativeTime } from '../utils/format';
import './TopCommandBar.css';

interface TopCommandBarProps {
  factoryPaused: boolean;
  factoryMode: string;
  apiHealthStatus: string;
  snapshotTime: string | null;
  onToggleFactory: () => Promise<unknown>;
  togglePending: boolean;
  audioEnabled: boolean;
  onToggleAudio: () => void;
  schemaVersion?: string;
  runtimeBackend?: string;
}

function useUtcClock(): string {
  const [now, setNow] = useState(() => formatUtc(new Date()));
  useEffect(() => {
    const id = setInterval(() => setNow(formatUtc(new Date())), 1000);
    return () => clearInterval(id);
  }, []);
  return now;
}

function formatUtc(d: Date): string {
  return d.toISOString().slice(11, 19) + ' UTC';
}

const PowerIcon: React.FC = () => (
  <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
    <path d="M12 3v9" strokeLinecap="round" />
    <path d="M18.36 6.64A9 9 0 1 1 5.64 6.64" strokeLinecap="round" />
  </svg>
);

export const TopCommandBar: React.FC<TopCommandBarProps> = ({
  factoryPaused,
  factoryMode,
  apiHealthStatus,
  snapshotTime,
  onToggleFactory,
  togglePending,
  audioEnabled,
  onToggleAudio,
  schemaVersion,
  runtimeBackend,
}) => {
  const utc = useUtcClock();
  const [showConfirm, setShowConfirm] = useState(false);
  const isOn = !factoryPaused;

  const handleToggleClick = useCallback(() => {
    setShowConfirm(true);
  }, []);

  const handleConfirm = useCallback(async () => {
    setShowConfirm(false);
    await onToggleFactory();
  }, [onToggleFactory]);

  const handleCancel = useCallback(() => {
    setShowConfirm(false);
  }, []);

  const modeBadgeClass = `tcb__mode-badge tcb__mode-badge--${factoryMode}`;

  const healthDotClass =
    apiHealthStatus === 'healthy'
      ? 'tcb__health-dot tcb__health-dot--healthy'
      : apiHealthStatus === 'warning'
        ? 'tcb__health-dot tcb__health-dot--warning'
        : 'tcb__health-dot tcb__health-dot--critical';

  return (
    <>
      <header className="tcb">
        <div className="tcb__brand">
          <span className="tcb__logo">NEBULA</span>
          <span className="tcb__subtitle">CONTROL ROOM</span>
        </div>

        <div className="tcb__center">
          <div className="tcb__toggle-wrap">
            <button
              className={`tcb__power-btn ${isOn ? 'tcb__power-btn--on' : 'tcb__power-btn--off'}`}
              onClick={handleToggleClick}
              disabled={togglePending}
              title={isOn ? 'Pause factory' : 'Resume factory'}
            >
              <PowerIcon />
            </button>
            <span
              className={`tcb__toggle-label ${isOn ? 'tcb__toggle-label--on' : 'tcb__toggle-label--off'}`}
            >
              {isOn ? 'ONLINE' : 'PAUSED'}
            </span>
          </div>

          <span className={modeBadgeClass}>
            {factoryMode.replace('_', ' ')}
          </span>

          {(schemaVersion || runtimeBackend) && (
            <span
              className="tcb__schema-badge"
              title={`Schema: ${schemaVersion ?? 'v1'} · Runtime: ${runtimeBackend ?? 'unknown'}`}
            >
              {schemaVersion ?? 'v1'}{runtimeBackend ? ` · ${runtimeBackend}` : ''}
            </span>
          )}
        </div>

        <div className="tcb__right">
          <button
            type="button"
            className={`tcb__audio-toggle ${audioEnabled ? 'tcb__audio-toggle--on' : 'tcb__audio-toggle--off'}`}
            onClick={onToggleAudio}
            title={audioEnabled ? 'Disable audio alerts' : 'Enable audio alerts'}
          >
            {audioEnabled ? '🔊 Audio alerts' : '🔈 Audio alerts'}
          </button>
          <span className="tcb__clock">{utc}</span>
          <span className="tcb__age">{relativeTime(snapshotTime)}</span>
          <span
            className={healthDotClass}
            title={`API: ${apiHealthStatus}`}
          />
        </div>
      </header>

      {showConfirm && (
        <div className="tcb__confirm-overlay" onClick={handleCancel}>
          <div className="tcb__confirm-dialog" onClick={(e) => e.stopPropagation()}>
            <div className="tcb__confirm-title">
              {isOn ? 'PAUSE FACTORY' : 'RESUME FACTORY'}
            </div>
            <p className="tcb__confirm-msg">
              {isOn
                ? 'This will pause all research cycles, paper runtime, and agent runs. Existing portfolios will continue but no new work will be scheduled.'
                : 'This will resume the factory. Research cycles and agent runs will restart.'}
            </p>
            <div className="tcb__confirm-actions">
              <button className="tcb__confirm-btn" onClick={handleCancel}>
                CANCEL
              </button>
              <button
                className={`tcb__confirm-btn ${isOn ? 'tcb__confirm-btn--danger' : 'tcb__confirm-btn--go'}`}
                onClick={handleConfirm}
              >
                {isOn ? 'PAUSE' : 'RESUME'}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
};
