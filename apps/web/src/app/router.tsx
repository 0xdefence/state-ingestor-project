import { createBrowserRouter } from "react-router-dom";
import { WorkspacePage } from "../features/workspace/WorkspacePage";
import { RunPage } from "../features/runs/RunPage";
import { ReviewPage } from "../features/reviews/ReviewPage";
export const router = createBrowserRouter([
  { path: "/", element: <WorkspacePage /> },
  { path: "/runs/:runId", element: <RunPage /> },
  { path: "/reviews", element: <ReviewPage /> },
]);
