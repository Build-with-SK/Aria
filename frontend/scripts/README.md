# Offline frontend checks

From `frontend/`, with the existing npm dependencies installed:

```powershell
node scripts/verify-launch-routes.mjs
node scripts/verify-technical-dashboard.mjs
node scripts/verify-shell.mjs
```

The route checks render the eight workspace pages with synthetic read-hook
responses: populated, null, empty arrays, withheld summaries/consensus, errors
and partial objects. Loaded Research details are rendered separately. The
technical checks exercise populated and withheld multi-timeframe evidence.
No server, credentials, live accounts or vault access is needed.

These are React server renders, so effects, hydration, charts loaded by script,
authentication and action handlers are outside their scope. The route verifier
filters only React Router's expected SSR useLayoutEffect warning and fails on
other React warnings. The older technical verifier prints that SSR warning.

The shell verifier executes the worker in a Node VM with in-memory caches. It
checks base paths, cache scope and network-only API handling without installing
a worker. Pass a staging build directory to also verify the built public URLs:

```powershell
npm run build -- --outDir C:/path/to/fresh-staging-directory
node scripts/verify-shell.mjs C:/path/to/fresh-staging-directory
```

Use a fresh staging directory. `frontend/dist` is the production build location;
only the runtime owner should replace the build being served.
