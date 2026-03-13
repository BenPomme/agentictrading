import { useState } from 'react';
import { useSnapshot } from './hooks/useSnapshot';
import { useFactoryControl } from './hooks/useFactoryControl';
import { ErrorBoundary } from './components/ErrorBoundary';
import { TopCommandBar } from './components/TopCommandBar';
import { APIFeedsStrip } from './components/APIFeedsStrip';
import { KPIDeck } from './components/KPIDeck';
import { AgentActivityPanel } from './components/AgentActivityPanel';
import { PortfolioGrid } from './components/PortfolioGrid';
import AlertsPanel from './components/AlertsPanel';
import EscalationsPanel from './components/EscalationsPanel';
import MaintenancePanel from './components/MaintenancePanel';
import FamiliesPanel from './components/FamiliesPanel';
import LeaguePanel from './components/LeaguePanel';
import LineageBoard from './components/LineageBoard';
import JournalPanel from './components/JournalPanel';
import IdeasPanel from './components/IdeasPanel';
import QueuePanel from './components/QueuePanel';
import DesksPanel from './components/DesksPanel';
import LineageAtlas from './components/LineageAtlas';
import './App.css';

type Tab = 'overview' | 'atlas';

function App() {
  const { data, loading, error } = useSnapshot();
  const { toggle, pending } = useFactoryControl(data?.factory_paused);
  const [activeTab, setActiveTab] = useState<Tab>('overview');

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

  return (
    <div className="app">
      <TopCommandBar
        factoryPaused={data?.factory_paused ?? false}
        factoryMode={data?.factory?.mode ?? 'unknown'}
        apiHealthStatus={data?.api_health?.status ?? 'unknown'}
        snapshotTime={data?.generated_at ?? null}
        onToggleFactory={toggle}
        togglePending={pending}
      />

      <APIFeedsStrip feeds={data?.api_feeds} />

      <nav className="app__tabs">
        <button
          className={`app__tab ${activeTab === 'overview' ? 'app__tab--active' : ''}`}
          onClick={() => setActiveTab('overview')}
        >
          Overview
        </button>
        <button
          className={`app__tab ${activeTab === 'atlas' ? 'app__tab--active' : ''}`}
          onClick={() => setActiveTab('atlas')}
        >
          Lineage Atlas
        </button>
      </nav>

      {activeTab === 'overview' ? (
        <main className="app__main">
          <ErrorBoundary name="KPIDeck">
            <KPIDeck factory={data?.factory} execution={data?.execution} ideas={data?.ideas} />
          </ErrorBoundary>

          <div className="app__grid app__grid--primary">
            <div className="app__col app__col--wide">
              <ErrorBoundary name="AgentActivity">
                <AgentActivityPanel agentRuns={data?.factory?.agent_runs} />
              </ErrorBoundary>
              <ErrorBoundary name="PortfolioGrid">
                <PortfolioGrid portfolios={data?.execution?.portfolios} placeholders={data?.execution?.placeholders} />
              </ErrorBoundary>
            </div>
            <div className="app__col app__col--narrow">
              <ErrorBoundary name="Alerts">
                <AlertsPanel alerts={data?.company?.alerts} />
              </ErrorBoundary>
              <ErrorBoundary name="Escalations">
                <EscalationsPanel signals={data?.factory?.operator_signals} />
              </ErrorBoundary>
              <ErrorBoundary name="Maintenance">
                <MaintenancePanel items={data?.factory?.operator_signals?.maintenance_queue} />
              </ErrorBoundary>
            </div>
          </div>

          <div className="app__grid app__grid--secondary">
            <ErrorBoundary name="Families">
              <FamiliesPanel families={data?.factory?.families} />
            </ErrorBoundary>
            <ErrorBoundary name="League">
              <LeaguePanel league={data?.factory?.model_league} />
            </ErrorBoundary>
          </div>

          <div className="app__grid app__grid--tertiary">
            <div className="app__col app__col--wide">
              <ErrorBoundary name="LineageBoard">
                <LineageBoard lineages={data?.factory?.lineages} />
              </ErrorBoundary>
            </div>
            <div className="app__col app__col--narrow">
              <ErrorBoundary name="Ideas">
                <IdeasPanel ideas={data?.ideas} />
              </ErrorBoundary>
              <ErrorBoundary name="Queue">
                <QueuePanel queue={data?.factory?.queue} />
              </ErrorBoundary>
              <ErrorBoundary name="Desks">
                <DesksPanel desks={data?.company?.desks} />
              </ErrorBoundary>
              <ErrorBoundary name="Journal">
                <JournalPanel actions={data?.company?.recent_actions} />
              </ErrorBoundary>
            </div>
          </div>
        </main>
      ) : (
        <main className="app__main">
          <ErrorBoundary name="LineageAtlas">
            <LineageAtlas atlas={data?.factory?.lineage_atlas} />
          </ErrorBoundary>
        </main>
      )}
    </div>
  );
}

export default App;
