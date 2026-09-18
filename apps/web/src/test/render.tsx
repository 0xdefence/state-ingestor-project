import { render } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { WorkspacePage } from "../features/workspace/WorkspacePage";
function Destination() {
  const location = useLocation();
  return (
    <main>
      <h1>Run destination</h1>
      <p>{location.pathname}</p>
      <p>{location.state?.duplicateNotice}</p>
    </main>
  );
}
export function renderWorkspace(url = "/") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          <Route path="/" element={<WorkspacePage />} />
          <Route path="/runs/:id" element={<Destination />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
