import { useEffect, useState } from 'react';
import { useSnapshot } from './hooks/useSnapshot';
import { useSnapshotV2 } from './hooks/useSnapshotV2';
import { useFactoryControl } from './hooks/useFactoryControl';
import { useAudioAlerts } from './hooks/useAudioAlerts';
import { ErrorBoundary } from './components/ErrorBoundary';
import { TopCommandBar } from './components/TopCommandBar';
import { APIFeedsStrip } from './components/APIFeedsStrip';
import { NavSidebar } from './components/NavSidebar';
import { FactoryHealthPage } from './pages/FactoryHealthPage';
import { PipelinePage } from './pages/PipelinePage';
import { PaperModelsPage } from './pages/PaperModelsPage';
import { FamilyExplorerPage } from './pages/FamilyExplorerPage';
import { GoldfishDNAPage } from './pages/GoldfishDNAPage';
import { ComputeCostPage } from './pages/ComputeCostPage';
import { VenueReadinessPage } from './pages/VenueReadinessPage';
import { AlertsPage } from './pages/AlertsPage';
import type { Zone } from './types/nav';
import './App.css';

function App() {
  const [zone, setZone] = useState<Zone>('factory-health');
  const { data, prev, loading, error } = useSnapshot();
  const { data: dataV2 } = useSnapshotV2();
  const { toggle, pending } = useFactoryControl(data?.factory_paused);
  const { audioEnabled, toggleAudio, playAgentRun, playPaperTrade } = useAudioAlerts();

  useEffect(() => {
    if (!data || !prev || !audioEnabled) return;

    const currentRuns = data.factory?.agent_runs ?? [];
    const prevRuns = prev.factory?.agent_runs ?? [];
    if (currentRuns.length > 0) {
      const latest = currentRuns[0]?.run_id;
      const prevLatest = prevRuns[0]?.run_id;
      if (latest && latest !== prevLatest) playAgentRun();
    }

    const currentPortfolios = data.execution?.portfolios ?? [];
    const prevPortfolios = prev.execution?.portfolios ?? [];
    if (currentPortfolios.length && prevPortfolios.length) {
      const prevCounts = new Map<string, number>();
      for (const p of prevPortfolios) prevCounts.set(p.portfolio_id, p.trade_count);
      const hasNewTrade = currentPortfolios.some((p) => {
        const prevCount = prevCounts.get(p.portfolio_id);
        return prevCount != null && p.trade_count > prevCount;
      });
      if (hasNewTrade) playPaperTrade();
    }
  }, [data, prev, audioEnabled, playAgentRun, playPaperTrade]);

  if (loading && !data) {
    return (
      <div className="app-loader">
        <div className="app-loader__ring" />
        <span className="app-loader__text">NEBULA INITIALIZING</span>
      </div>
    );
  }

  if (error && !data) {
    return (
      <div className="app-error">
        <span className="app-error__icon">⚠</span>
        <span>Connection lost: {error}</span>
      </div>
    );
  }

  const alerts = data?.company?.alerts ?? [];
  const maintenance = data?.factory?.operator_signals?.maintenance_queue ?? [];
  const escalations = data?.factory?.operator_signals?.escalation_candidates ?? [];
  const badgeCount = alerts.length + maintenance.length + escalations.length;
  const criticalCount = alerts.filter((a) => a.severity === 'critical').length;

  const pageProps = { snapshot: data, snapshotV2: dataV2 };

  return (
    <div className="app">
      <TopCommandBar
        factoryPaused={data?.factory_paused ?? false}
        factoryMode={data?.factory?.mode ?? 'unknown'}
        apiHealthStatus={data?.api_health?.status ?? 'unknown'}
        snapshotTime={data?.generated_at ?? null}
        onToggleFactory={toggle}
        togglePending={pending}
        audioEnabled={audioEnabled}
        onToggleAudio={toggleAudio}
        schemaVersion={dataV2?.schema_version}
        runtimeBackend={dataV2?.runtime?.backend}
      />
      <APIFeedsStrip feeds={data?.api_feeds} />
      <div className="app__body">
        <NavSidebar
          activeZone={zone}
          onNavigate={setZone}
          alertCount={badgeCount}
          criticalCount={criticalCount}
        />
        <main className="app__content">
          <ErrorBoundary name="Page">
            {zone === 'factory-health' && <FactoryHealthPage {...pageProps} />}
            {zone === 'pipeline' && <PipelinePage {...pageProps} />}
            {zone === 'paper-models' && <PaperModelsPage {...pageProps} />}
            {zone === 'family-explorer' && (
              <FamilyExplorerPage {...pageProps} />
            )}
            {zone === 'goldfish-dna' && <GoldfishDNAPage {...pageProps} />}
            {zone === 'compute-cost' && <ComputeCostPage {...pageProps} />}
            {zone === 'venue-readiness' && (
              <VenueReadinessPage {...pageProps} />
            )}
            {zone === 'alerts' && <AlertsPage {...pageProps} />}
          </ErrorBoundary>
        </main>
      </div>
    </div>
  );
}

export default App;
