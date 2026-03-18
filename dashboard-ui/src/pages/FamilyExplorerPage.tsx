import { ErrorBoundary } from '../components/ErrorBoundary';
import FamiliesPanel from '../components/FamiliesPanel';
import LeaguePanel from '../components/LeaguePanel';
import LineageBoard from '../components/LineageBoard';
import LineageAtlas from '../components/LineageAtlas';
import type { DashboardSnapshot, SnapshotV2 } from '../types/snapshot';
import './pages.css';

interface Props {
  snapshot: DashboardSnapshot | null;
  snapshotV2: SnapshotV2 | null;
}

export function FamilyExplorerPage({ snapshot, snapshotV2: _ }: Props) {
  return (
    <div className="page">
      <div className="page__header">
        <h2 className="page__title">Family &amp; Lineage Explorer</h2>
        <p className="page__subtitle">
          Family lifecycle, model league, lineage tree, and evolution ledger
        </p>
      </div>

      <div className="page__grid page__grid--2col">
        <ErrorBoundary name="FamiliesPanel">
          <FamiliesPanel families={snapshot?.factory?.families} />
        </ErrorBoundary>
        <ErrorBoundary name="LeaguePanel">
          <LeaguePanel league={snapshot?.factory?.model_league} />
        </ErrorBoundary>
      </div>

      <ErrorBoundary name="LineageBoard">
        <LineageBoard lineages={snapshot?.factory?.lineages} />
      </ErrorBoundary>

      <ErrorBoundary name="LineageAtlas">
        <LineageAtlas atlas={snapshot?.factory?.lineage_atlas} />
      </ErrorBoundary>
    </div>
  );
}
