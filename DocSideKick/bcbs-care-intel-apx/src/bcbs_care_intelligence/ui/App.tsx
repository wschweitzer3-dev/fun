import { Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { SidebarRouteShell } from "@/routes/_sidebar/route";
import { ChatPage } from "@/routes/_sidebar/chat";
import { InsightsPage } from "@/routes/_sidebar/insights";
import { Skeleton } from "@/components/ui/skeleton";

function InsightsLoading() {
  return (
    <div className="insights-page">
      <div className="insights-header">
        <Skeleton className="h-8 w-52" />
        <Skeleton className="h-4 w-3/4" />
      </div>
      <div className="insights-cards">
        <Skeleton className="h-28 w-full" />
        <Skeleton className="h-28 w-full" />
        <Skeleton className="h-28 w-full" />
      </div>
      <Skeleton className="h-72 w-full" />
    </div>
  );
}

export default function App() {
  return (
    <Routes>
      <Route element={<SidebarRouteShell />}>
        <Route path="/" element={<Navigate to="/chat" replace />} />
        <Route path="/chat" element={<ChatPage />} />
        <Route
          path="/insights"
          element={
            <Suspense fallback={<InsightsLoading />}>
              <InsightsPage mode="member-insights" />
            </Suspense>
          }
        />
        <Route
          path="/population-trends"
          element={
            <Suspense fallback={<InsightsLoading />}>
              <InsightsPage mode="population-trends" />
            </Suspense>
          }
        />
      </Route>
    </Routes>
  );
}

