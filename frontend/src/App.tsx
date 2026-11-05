import { lazy, Suspense } from "react";
import { Footer, Header, VIEWS, type ViewKey } from "./components/Header";
import { Skeleton } from "./components/ui";
import { useUrlParam } from "./lib/url";
import { SessionProvider } from "./session";
import AgentView from "./views/agent/AgentView";
import AuditView from "./views/audit/AuditView";
import DataSourcesView from "./views/data/DataView";

// Charts are heavy: the Estate and Evals views load on demand.
const EstateView = lazy(() => import("./views/estate/EstateView"));
const EvalsView = lazy(() => import("./views/evals/EvalsView"));

function ViewSkeleton() {
  return (
    <div className="space-y-6" role="status" aria-busy="true" aria-label="Loading">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        {["a", "b", "c", "d", "e"].map((k) => (
          <Skeleton key={k} className="h-24 rounded-xl" />
        ))}
      </div>
      <Skeleton className="h-64 rounded-xl" />
    </div>
  );
}

function CurrentView() {
  const [view] = useUrlParam("view");
  const key: ViewKey = VIEWS.some((v) => v.key === view) ? (view as ViewKey) : "agent";
  return (
    <Suspense fallback={<ViewSkeleton />}>
      {key === "agent" && <AgentView />}
      {key === "estate" && <EstateView />}
      {key === "data" && <DataSourcesView />}
      {key === "evals" && <EvalsView />}
      {key === "audit" && <AuditView />}
    </Suspense>
  );
}

export function App() {
  return (
    <div className="flex min-h-screen flex-col">
      <a
        href="#main"
        className="sr-only z-50 rounded-md bg-emerald-500 px-3 py-2 text-sm font-medium text-zinc-950 focus:not-sr-only focus:fixed focus:top-3 focus:left-3"
      >
        Skip to content
      </a>
      <SessionProvider
        fallback={
          <>
            <div className="h-[105px] border-b border-zinc-800/80" />
            <main className="mx-auto w-full max-w-[1440px] flex-1 px-4 py-6 sm:px-6 lg:px-8">
              <ViewSkeleton />
            </main>
          </>
        }
      >
        <Header />
        <main id="main" className="mx-auto w-full max-w-[1440px] flex-1 px-4 py-6 sm:px-6 lg:px-8">
          <CurrentView />
        </main>
      </SessionProvider>
      <Footer />
    </div>
  );
}
