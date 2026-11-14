# MRTA Warehouse Control Center frontend

See the complete [dashboard user guide](../README.md) for features, startup,
single runs, CSV inputs, experiments, comparisons, scientific record inspection,
backend explanation, storage and troubleshooting.

```bash
cd /home/ibrahim/rnd_mrta_ilp_ws/src/dashboard_GUI/frontend
npm ci
npm run dev
```

Open http://127.0.0.1:5173/ with the backend on 127.0.0.1:8000.
Validation: `npm test` and `npm run build`. Run build before `npm run preview`;
preview uses the same local port after the dev server is stopped.
