export type Scope = 'app' | 'history'
export type Envelope<T> = { api_schema_version: number; data: T; availability: string; warnings: string[]; next_cursor: string | null }
export type Capability = { implemented: boolean; readable: boolean; reason?: string | null; scope?: string }
export type Capabilities = Record<string, Capability | string>
export type Strategy = { id: string; implementation: string; review_status: string }
export type Scenario = { id: string; width: number; height: number; capacity: number; settings: Record<string, unknown>; hashes: Record<string, string> }
export type Cell = [number, number]
export type Geometry = { id: string; width: number; height: number; resolution_m: number; origin: Cell;
  obstacles: Cell[]; traversable_cells: Cell[]; non_transit_cells: Cell[];
  zones: { id: string; category: string; cells: Cell[] }[]; directed_edges: [Cell, Cell][] }
export type Robot = { robot_id: string; cell: Cell; status: string; active_order_id: string | null;
  assigned_order_id: string | null; charge_state: string | null; soc_percent: number | null; soc_exact: string | null;
  distance_m: number; waiting_time_s: number; travel_time_s: number; service_time_s: number; charging_time_s: number }
export type Snapshot = { schema_version: number; type: 'fleet_snapshot'; job_id: string; source: string;
  stream_generation: string; sequence?: number; sample_sequence: number; observed_at_utc: string;
  sim_time_exact: string; sim_time_s: number; payload: { sample_mode: string; dropped_samples_total: number;
    robots: Robot[]; orders: { total: number; pending: number; assigned: number; in_progress: number; completed: number;
      failed: number; deferred_now: number; cumulative_deferrals: number }; resources: Record<string, unknown>[] } }
export type Telemetry = { available: boolean; availability: string; reason?: string; recording_complete?: boolean;
  live?: boolean; stale?: boolean; age_wall_s?: number; dropped_samples_total?: number;
  footer?: { dropped_samples_total: number; writer_dropped_samples: number; truncated: boolean; error?: string | null } }
export type Plan = { job_id: string; kind: 'run' | 'experiment'; argv: string[]; original_cli_argv?: string[];
  output_dir: string; request: Record<string, unknown>; warnings: string[]; execution_mode?: string;
  effective: { robots?: number; orders?: number; seed?: number; window?: string | number; quantum_s?: number; speed_mps?: number; [key: string]: unknown } }
export type Preview = Plan & { preview_id: string; expires_in_s: number }
export type Job = { id: string; kind: 'run' | 'experiment'; status: string; sequence: number; created_at: string;
  started_at?: string | null; ended_at?: string | null; exit_code?: number | null; elapsed_wall_s?: number;
  artifact_status?: string; error?: string | null; output_dir: string; owned_process_alive: boolean;
  plan: Plan; telemetry: Telemetry; snapshot?: Snapshot }
export type Run = { id: string; directory: string; integrity: string; scientific_status: string; process_status: string;
  provenance: string; metrics_schema: string; warnings: string[]; dataset_schema_version?: number }
export type Artifact = { id: string; availability: string; size: number | null; sha256?: string | null; warning?: string }
export type Form = { strategy: string; robots: string; orders: string; seed: string; mode: 'observed' | 'cli';
  window: string; quantum: string; speed: string; sample: string; export: boolean; input_id?: string }
export type TracePage = { records: unknown[]; next_cursor: number; has_more: boolean; trailing_partial: boolean; size: number }
export type LogPage = { text: string; next_cursor: number; size: number; complete: boolean }
