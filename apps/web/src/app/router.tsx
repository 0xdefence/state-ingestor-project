import { createBrowserRouter, Link, useLocation } from "react-router-dom";
import { WorkspacePage } from "../features/workspace/WorkspacePage";
// Task 5 replaces this boundary with the run and review screens.
function DestinationBoundary() {
  const location = useLocation();
  return (
    <main className="workspace">
      <h1>{location.pathname.startsWith("/runs/") ? "Run" : "Review queue"}</h1>
      {location.state?.duplicateNotice && (
        <p role="status">{location.state.duplicateNotice}</p>
      )}
      <p>This view will be available with the review workflow.</p>
      <Link to="/">Back to workspace</Link>
    </main>
  );
}
export const router = createBrowserRouter([
  { path: "/", element: <WorkspacePage /> },
  { path: "/runs/:runId", element: <DestinationBoundary /> },
  { path: "/reviews", element: <DestinationBoundary /> },
]);
