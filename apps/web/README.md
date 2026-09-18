# Operator workspace

Run the local API on `127.0.0.1:8000`, then:

```sh
bun install
bun run dev
```

Vite serves the web app on `127.0.0.1:5173` and proxies `/api` to the local API. Fonts are bundled locally.

```sh
bun test
bun run typecheck
bun run build
```

The browser component suite uses Vitest/jsdom. `bun test` runs a small Bun test adapter that invokes the same Vitest suite; `bun run test` runs Vitest directly. Network boundaries use fixtures and perform no real API calls.

Task 4 implements the workspace. Run and review destinations are explicit route boundaries for Task 5. Upload success carries a duplicate notice in navigation state, which those screens should retain or replace with the run occurrence projection. Workspace scope uses `scope=current|selected|all` and repeated `run_id` query parameters.
