"use client";
import { useCallback, useEffect, useState } from "react";
import { api, apiDownload, setToken } from "./api";

type Tab = "home" | "sources" | "metrics" | "ask" | "reports" | "alerts" | "activity" | "settings";
const card = { background: "#fff", borderRadius: 8, padding: 16, boxShadow: "0 1px 3px #0001", marginBottom: 16 } as const;

function Auth({ onAuthed }: { onAuthed: () => void }) {
  const [mode, setMode] = useState<"login" | "register">("register");
  const [f, setF] = useState({ organisation: "", email: "", password: "", code: "" });
  const [needCode, setNeedCode] = useState(false);
  const [err, setErr] = useState("");
  const submit = async () => {
    try {
      const r = mode === "register" ? await api("/tenants", { method: "POST", body: JSON.stringify({ organisation: f.organisation, email: f.email, password: f.password }) })
                                    : await api("/auth/login", { method: "POST", body: JSON.stringify({ email: f.email, password: f.password, code: f.code || undefined }) });
      setToken(r.token); onAuthed();
    } catch (e: any) {
      if (e.message === "mfa_required") { setNeedCode(true); setErr("Enter the 6-digit code from your authenticator app (or a recovery code)."); }
      else setErr(e.message);
    }
  };
  return (
    <div style={{ ...card, maxWidth: 380, margin: "80px auto" }}>
      <h2>DataPilot AI</h2>
      {mode === "register" && <input placeholder="Organisation" value={f.organisation} onChange={e => setF({ ...f, organisation: e.target.value })} />}
      <input placeholder="Email" value={f.email} onChange={e => setF({ ...f, email: e.target.value })} />
      <input placeholder="Password (10+ chars)" type="password" value={f.password} onChange={e => setF({ ...f, password: e.target.value })} />
      {mode === "login" && needCode && <input placeholder="Authenticator or recovery code" autoComplete="one-time-code" value={f.code} onChange={e => setF({ ...f, code: e.target.value })} />}
      <p><button onClick={submit}>{mode === "register" ? "Create workspace" : "Sign in"}</button>{" "}
        <a href="#" onClick={e => { e.preventDefault(); setMode(mode === "login" ? "register" : "login"); }}>{mode === "login" ? "Register" : "Sign in"}</a></p>
      {err && <p role="alert" style={{ color: "#b00020" }}>{err}</p>}
    </div>
  );
}

function Home() {
  const [tiles, setTiles] = useState<any[]>([]);
  const [hot, setHot] = useState<any[]>([]);
  const [ins, setIns] = useState<any[]>([]);
  const [qw, setQw] = useState<any[]>([]);
  const [issues, setIssues] = useState<any[]>([]);
  useEffect(() => {
    api("/health").then(r => setIssues(r.issues)).catch(() => {});
    api("/usage").then(r => setQw(r.quota?.warnings || [])).catch(() => {});
    api("/insights").then(r => setIns(r.insights)).catch(() => {});
    api("/dashboard").then(r => setTiles(r.tiles)).catch(() => {});
    api("/alerts").then(r => setHot(r.filter((a: any) => a.last_state === "triggered"))).catch(() => {});
  }, []);
  const banner = hot.length > 0 && <div data-testid="alert-banner" role="alert" style={{ ...card, borderColor: "#b00020", background: "#fdecee" }}>
    <b style={{ color: "#b00020" }}>{hot.length === 1 ? "1 alert is triggered" : `${hot.length} alerts are triggered`}</b>
    {hot.map(a => <div key={a.id}>{a.name}: {a.metric} is {a.last_value} ({a.comparison} {a.threshold})</div>)}
    <small>Details and checks are under Alerts.</small></div>;
  const attention = issues.length > 0 && <div data-testid="health-banner" role="status" style={{ ...card, borderColor: issues.some(i => i.severity === "high") ? "#b00020" : "#8a5a00", background: "#fff6e5" }}>
    <b>{issues.length === 1 ? "1 item needs attention" : `${issues.length} items need attention`}</b>
    {issues.slice(0, 5).map(i => <div key={i.key}>{i.message}</div>)}
    <small>Source health is under Data Sources.</small></div>;
  const quota = qw.length > 0 && <div data-testid="quota-banner" role="status" style={{ ...card, borderColor: "#8a5a00", background: "#fff6e5" }}>
    <b style={{ color: "#8a5a00" }}>{qw.some(w => w.reached) ? "A plan limit has been reached" : "You are close to a plan limit"}</b>
    {qw.map(w => <div key={w.item}>{w.message}</div>)}
    <small>Limits are part of your plan. Ask your administrator to review the plan if you need more.</small></div>;
  const insights = ins.length > 0 && <div style={card} data-testid="insights">
    <h3 style={{ marginTop: 0 }}>Insights</h3>
    {ins.map(i => <div key={i.metric} style={{ marginBottom: 8 }}>
      <div>{i.headline}</div>
      {i.anomaly && <div style={{ color: "#8a5a00" }}>Unusual: {i.anomaly.month} is {Math.abs(i.anomaly.z_score)} standard deviations {i.anomaly.z_score > 0 ? "above" : "below"} its recent average.</div>}
    </div>)}
    <small>Computed from your approved metrics. It shows where numbers moved, not why; ask "Why did gross profit change?" under Ask Your Data for the breakdown by product, region and customer.</small></div>;
  if (!tiles.length) return <>{banner}{attention}{quota}<div style={card}>No approved metrics with data yet. Upload a file under Data Sources and the model is set up for you when the columns are clear.</div></>;
  return <>{banner}{attention}{quota}{insights}{tiles.map(t => (
    <div key={t.metric} style={card}>
      <h3 style={{ margin: 0 }}>{t.label}: {Number(t.total).toLocaleString()}</h3>
      {t.caveats.map((c: string) => <small key={c} style={{ color: "#8a5a00" }}>{c}</small>)}
      <Spark rows={t.trend} />
    </div>))}</>;
}

function Spark({ rows }: { rows: [string, number][] }) {
  if (rows.length < 2) return null;
  const vals = rows.map(r => Number(r[1])), max = Math.max(...vals), min = Math.min(...vals, 0);
  const pts = vals.map((v, i) => `${(i / (vals.length - 1)) * 300},${60 - ((v - min) / (max - min || 1)) * 55}`).join(" ");
  return <svg width={300} height={64} role="img" aria-label="trend by month"><polyline fill="none" stroke="#2563eb" strokeWidth={2} points={pts} /></svg>;
}

const FACT_FIELDS: [string, string][] = [
  ["order_id", "Order ID"], ["order_date", "Order date"], ["net_sales", "Net sales (revenue)"],
  ["cost_of_goods_sold", "Cost of goods sold"], ["product", "Product"], ["region", "Region"], ["customer", "Customer"],
];

const STATE_LABEL: Record<string, [string, string]> = {
  ok: ["Up to date", "#1a7f37"], failed: ["Failed", "#b00020"], late: ["Late", "#8a5a00"],
  processing: ["Processing", "#2563eb"], empty: ["No files", "#6b7280"],
};

/** Data-source health: latest upload state, and how often a new file is expected (so a missing file is noticed). */
function SourceHealth() {
  const [rows, setRows] = useState<any[]>([]);
  const [days, setDays] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState("");
  const load = useCallback(() => api("/health").then(r => {
    setRows(r.sources); setDays(Object.fromEntries(r.sources.map((x: any) => [x.id, x.expected_every_days ? String(x.expected_every_days) : ""])));
  }).catch(() => {}), []);
  useEffect(() => { load(); }, [load]);
  const save = async (id: string) => {
    try { await api(`/data-sources/${id}`, { method: "PATCH", body: JSON.stringify({ expected_every_days: days[id] ? Number(days[id]) : 0 }) }); setMsg("Saved."); await load(); }
    catch (e: any) { setMsg(e.message); }
  };
  if (!rows.length) return null;
  return (<div style={card} data-testid="source-health">
    <h3 style={{ marginTop: 0 }}>Source health</h3>
    <table><thead><tr><th>Source</th><th>State</th><th>Last upload</th><th>Expect a new file every</th></tr></thead>
      <tbody>{rows.map(r => { const [label, colour] = STATE_LABEL[r.state] || [r.state, "#444"]; return (
        <tr key={r.id} data-source={r.name}>
          <td>{r.name}</td>
          <td><b style={{ color: colour }}>{label}</b><br /><small>{r.message}</small></td>
          <td>{r.last_upload_at ? new Date(r.last_upload_at).toLocaleString() : "-"}</td>
          <td><input type="number" min={0} max={365} placeholder="days" aria-label={`Days between files for ${r.name}`} style={{ width: 70, display: "inline" }}
            value={days[r.id] ?? ""} onChange={e => setDays(d => ({ ...d, [r.id]: e.target.value }))} /> days <button onClick={() => save(r.id)}>Save</button></td>
        </tr>); })}</tbody></table>
    {msg && <small role="status">{msg}</small>}
    <small style={{ display: "block", marginTop: 6 }}>If nothing arrives within that time the source is shown as Late. Leave blank to switch the check off.</small>
  </div>);
}

/** Management summary: generated weekly by the worker and on request. */
function Summaries() {
  const [list, setList] = useState<any[]>([]);
  const [open, setOpen] = useState<any>(null);
  const [msg, setMsg] = useState("");
  const load = useCallback(async () => {
    const l = await api("/summaries"); setList(l);
    if (l.length) setOpen(await api(`/summaries/${l[0].id}`));
  }, []);
  useEffect(() => { load().catch(() => {}); }, [load]);
  const gen = async () => { try { setMsg("Generating…"); const r = await api("/summaries/generate", { method: "POST" }); setOpen(r); setMsg(""); setList(await api("/summaries")); } catch (e: any) { setMsg(e.message); } };
  const c = open?.content;
  return (<div style={card} data-testid="summary">
    <h3 style={{ marginTop: 0 }}>Management summary</h3>
    <p style={{ marginTop: 0 }}>A short summary is created every week. You can also create one now for the last 7 days. <button onClick={gen}>Create summary now</button> {msg && <span role="status">{msg}</span>}</p>
    {!c ? <p>No summary yet.</p> : <>
      <p style={{ fontWeight: 600 }} data-testid="summary-headline">{c.headline}</p>
      <small>{c.period.start} to {c.period.end} ({open.kind === "weekly" ? "weekly" : "on request"})</small>
      {c.metrics.length > 0 && <ul>{c.metrics.map((x: any) => <li key={x.label}>{x.headline}</li>)}</ul>}
      <p style={{ marginBottom: 4 }}><b>This period</b></p>
      <ul style={{ marginTop: 0 }}>
        <li>{c.operations.files_uploaded} file(s) uploaded, {c.operations.rows_loaded} rows loaded{c.operations.uploads_failed ? `, ${c.operations.uploads_failed} failed` : ""}</li>
        <li>{c.operations.questions_answered} question(s) answered, {c.operations.questions_refused} refused</li>
        <li>{c.operations.alerts_triggered} alert(s) triggered, {c.operations.alerts_recovered} recovered</li>
      </ul>
      {c.attention.length > 0 && <><p style={{ marginBottom: 4 }}><b>Needs attention</b></p><ul style={{ marginTop: 0 }}>{c.attention.map((a: any) => <li key={a.key}>{a.message}</li>)}</ul></>}
      {c.caveats.map((x: string) => <div key={x}><small style={{ color: "#8a5a00" }}>{x}</small></div>)}
      <small style={{ display: "block", marginTop: 6 }}>{c.note}</small></>}
  </div>);
}

function Sources() {
  const [msg, setMsg] = useState("");
  const [note, setNote] = useState("");   // result of the last approve, shown next to the button
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [results, setResults] = useState<{ name: string; status: string }[]>([]);
  const [datasets, setDatasets] = useState<any[]>([]);
  const [profile, setProfile] = useState<any>(null);
  const [quality, setQuality] = useState<any>(null);
  const [versions, setVersions] = useState<any[]>([]);
  const [rels, setRels] = useState<any[]>([]);
  const refresh = useCallback(() => api("/datasets").then(setDatasets), []);
  useEffect(() => { refresh().catch(() => {}); }, [refresh]);

  // One file: upload, then wait for the worker. Returns a short result for the list below the picker.
  const uploadOne = async (file: File): Promise<string> => {
    try {
      const src = await api("/data-sources", { method: "POST", body: JSON.stringify({ name: file.name }) });
      const fd = new FormData(); fd.append("file", file);
      const up = await api(`/data-sources/${src.id}/uploads`, { method: "POST", body: fd });
      for (let i = 0; i < 60; i++) {
        const j = await api(`/jobs/${up.job_id}`);
        if (j.status === "succeeded") {
          const d = (j.datasets || [])[0];
          if (d?.auto === "built") return "ingested and set up automatically";
          if (d?.auto === "needs_review") return "ingested - needs your check (open it below)";
          return "ingested";
        }
        if (j.status === "failed") return `failed: ${j.error_message}`;
        await new Promise(r => setTimeout(r, 1000));
      }
      return "still processing - check back shortly";
    } catch (e: any) { return `failed: ${e.message}`; }
  };
  // Several files: one after another (keeps within the upload rate limit); a failure does not stop the rest.
  const uploadMany = async (files: File[]) => {
    setMsg(""); setResults(files.map(f => ({ name: f.name, status: "waiting" })));
    for (let i = 0; i < files.length; i++) {
      setResults(r => r.map((x, k) => k === i ? { ...x, status: "uploading…" } : x));
      const status = await uploadOne(files[i]);
      setResults(r => r.map((x, k) => k === i ? { ...x, status } : x));
      refresh().catch(() => {});
    }
    setMsg(`${files.length} file${files.length === 1 ? "" : "s"} processed.`);
  };
  const open = async (id: string) => {
    const pr = await api(`/datasets/${id}/profile`);
    // An approved dataset shows the columns that were actually saved; otherwise the automatic suggestions.
    setMapping(pr.model_status === "approved" && pr.column_mapping && Object.keys(pr.column_mapping).length
      ? pr.column_mapping
      : Object.fromEntries(Object.entries<any>(pr.profile.suggested_mapping || {})
        .filter(([, v]) => !v.needs_clarification).map(([k, v]) => [k, v.column])));
    setNote("");
    setProfile(pr);
    setQuality(await api(`/datasets/${id}/quality`).catch(() => null));
    setVersions((await api(`/datasets/${id}/schema-versions`).catch(() => ({ versions: [] }))).versions);
    setRels((await api("/relationships").catch(() => ({ relationships: [] }))).relationships);
  };
  const approve = async () => {
    const chosen = Object.fromEntries(Object.entries(mapping).filter(([, v]) => v));
    try { const r = await api(`/datasets/${profile.id}/approve`, { method: "POST", body: JSON.stringify({ mapping: chosen }) });
      const text = `Saved. Model built: ${r.rows_loaded} rows loaded, ${r.rows_rejected} quarantined.`;
      setMsg(text); refresh(); await open(profile.id); setNote(text); }
    catch (e: any) { setMsg(e.message); setNote(`Not saved: ${e.message}`); }
  };
  const approveDim = async () => {
    const sd = profile.profile.suggested_dimension;
    try { const r = await api(`/datasets/${profile.id}/approve`, { method: "POST", body: JSON.stringify({ mapping: sd.mapping, role: sd.role }) });
      const text = `Saved. ${sd.label} list built: ${r.rows_loaded} loaded, ${r.rows_rejected} quarantined.`;
      setMsg(text); refresh(); await open(profile.id); setNote(text); }
    catch (e: any) { setMsg(e.message); setNote(`Not saved: ${e.message}`); }
  };
  return (<>
    <SourceHealth key={results.map(r => r.status).join("|")} />
    <div style={card}>
      <input type="file" multiple accept=".csv,.xlsx,.txt,.json,.jsonl,.xml" aria-label="Choose files to upload"
        onChange={e => { const fs = Array.from(e.target.files || []); e.target.value = ""; if (fs.length) uploadMany(fs); }} /> <span>{msg}</span>
      {results.length > 0 && <ul data-testid="upload-results" style={{ margin: "8px 0 0", paddingLeft: 18 }}>
        {results.map((r, i) => <li key={i}>{r.name} - {r.status}</li>)}</ul>}
    </div>
    <div style={card}>{datasets.map(d => <div key={d.id}><a href="#" onClick={e => { e.preventDefault(); open(d.id); }}>{d.name}</a> — {d.rows} rows — {d.model_status}{d.role && d.role !== "fact_sales" ? ` (${d.role === "dim_product" ? "products" : "customers"} list)` : ""}</div>)}</div>
    {profile && <div style={card}>
      <h3>{profile.name}</h3>
      <table><thead><tr><th>Column</th><th>Type</th><th>Nulls</th><th>Distinct</th></tr></thead>
        <tbody>{profile.profile.columns.map((c: any) => <tr key={c.name}><td>{c.name}</td><td>{c.type}</td><td>{c.null_count}</td><td>{c.distinct}</td></tr>)}</tbody></table>
      {profile.profile.auto?.status === "built" && <p data-testid="auto-note" style={{ background: "#eef7ee", padding: 8, borderRadius: 6 }}>
        Detected and set up automatically ({profile.profile.auto.role === "fact_sales" ? "sales data" : profile.profile.auto.role === "dim_product" ? "products list" : "customers list"}).
        {profile.profile.auto.metrics_approved?.length > 0 && <> Metrics approved: {profile.profile.auto.metrics_approved.join(", ")}.</>}
        {" "}Check the columns below and change them if something is wrong.</p>}
      {profile.profile.auto?.status === "needs_review" && <p data-testid="auto-review" style={{ background: "#fff6e5", padding: 8, borderRadius: 6 }}>
        Needs your check: {profile.profile.auto.reason} Choose the right columns below, then approve.</p>}
      <h4>Proposed mapping</h4>
      {!profile.profile.suggested_dimension && <div data-testid="mapping">
        <p>Match each field to a column in this file. Gross profit needs a cost column; leave a field on "not in this file" if there is none.</p>
        {FACT_FIELDS.map(([k, label]) => { const sug = profile.profile.suggested_mapping?.[k]; return (
          <div key={k} style={{ margin: "4px 0" }}>
            <label>{label}: <select aria-label={label} value={mapping[k] || ""} onChange={e => setMapping(m => ({ ...m, [k]: e.target.value }))}>
              <option value="">not in this file</option>
              {profile.profile.columns.map((c: any) => <option key={c.name} value={c.name}>{c.name}</option>)}
            </select></label>
            {sug && !sug.needs_clarification && mapping[k] === sug.column && <span> suggested ({Math.round(sug.confidence * 100)}%)</span>}
            {sug?.needs_clarification && <b style={{ color: "#b00020" }}> {sug.needs_clarification}</b>}
          </div>); })}
      </div>}
      {profile.profile.suggested_dimension && Object.entries<any>(profile.profile.suggested_mapping).map(([k, v]) => <div key={k}>{k} ← {v.column} ({Math.round(v.confidence * 100)}%)</div>)}
      {profile.profile.suggested_dimension && <>
        <h4>This looks like a {profile.profile.suggested_dimension.label.toLowerCase()} list</h4>
        <div>{Object.entries<string>(profile.profile.suggested_dimension.mapping).map(([k, v]) => <div key={k}>{k} ← {v}</div>)}</div>
        <p><button onClick={approveDim}>Approve as {profile.profile.suggested_dimension.label.toLowerCase()} list</button></p></>}
      {!profile.profile.suggested_dimension && <p><button onClick={approve}>{profile.model_status === "approved" ? "Rebuild model with these columns" : "Approve and build model"}</button>
        {profile.model_status === "approved" && <b data-testid="model-state" style={{ color: "#1a7f37", marginLeft: 8 }}>Approved</b>}</p>}
      {note && <p role="status" data-testid="approve-note" style={{ fontWeight: 600, color: note.startsWith("Not saved") ? "#b00020" : "#1a7f37" }}>{note}</p>}
    </div>}
    {profile && quality && <div style={card} data-testid="quality">
      <h3>Data quality: {quality.overall}</h3>
      <table><thead><tr><th>Stage</th><th>Check</th><th>Result</th><th>Detail</th></tr></thead>
        <tbody>{quality.results.map((r: any) => <tr key={r.stage + r.rule}><td>{r.stage}</td><td>{r.rule}</td>
          <td style={{ color: r.status === "fail" ? "#b00020" : r.status === "warn" ? "#8a5a00" : "#1a7f37" }}>{r.status}</td><td>{r.message}</td></tr>)}</tbody></table>
    </div>}
    {profile && rels.length > 0 && <div style={card} data-testid="joins">
      <h3>Joins to sales</h3>
      <table><thead><tr><th>List</th><th>Sales key</th><th>Matched</th><th>Sales rows shown as Unassigned</th></tr></thead>
        <tbody>{rels.map((r: any) => <tr key={r.role}><td>{r.dataset}</td>
          <td>{r.sales_modelled ? `${r.fact_column} → ${r.dimension_key}` : "waiting for approved sales data"}</td>
          <td>{r.sales_modelled ? `${r.matched_keys} of ${r.sales_keys} (${Math.round(r.key_coverage * 100)}%)` : "–"}</td>
          <td>{r.sales_modelled ? r.rows_unassigned : "–"}</td></tr>)}</tbody></table>
    </div>}
    {profile && versions.length > 0 && <div style={card}>
      <h3>Schema versions</h3>
      {versions.map((v: any) => <div key={v.version}>v{v.version}: {v.columns.length} columns
        {v.changes && (v.changes.added?.length || v.changes.removed?.length || v.changes.type_changed?.length) ?
          <> — added {v.changes.added.join(", ") || "none"}; removed {v.changes.removed.join(", ") || "none"}; type changed {v.changes.type_changed.length}</> : null}</div>)}
    </div>}
  </>);
}

function LineagePanel({ name }: { name: string }) {
  const [t, setT] = useState<any>(null);
  const [err, setErr] = useState("");
  useEffect(() => { setT(null); setErr(""); api(`/lineage/metrics/${name}`).then(setT).catch(e => setErr(e.message)); }, [name]);
  if (err) return <p role="alert">{err}</p>;
  if (!t) return <p>Loading…</p>;
  const when = (iso: string) => new Date(iso).toLocaleString();
  return (<div data-testid="lineage" style={{ background: "#f6f8fa", padding: 12, borderRadius: 6 }}>
    <p style={{ margin: "0 0 6px" }}><b>1. Source files</b> - which uploaded column feeds each field</p>
    <ul style={{ margin: "0 0 10px", paddingLeft: 18 }}>
      {t.columns.map((c: any) => <li key={c.field}>
        <code>{c.field}</code> {c.mapped
          ? <>&larr; column <code>{c.source_column}</code> of <b>{c.file?.filename}</b> (dataset {c.dataset.name}, uploaded {when(c.file.uploaded_at)}, checksum {c.file.sha256.slice(0, 12)}&hellip;)</>
          : <b style={{ color: "#b00020" }}>not mapped to any uploaded column</b>}</li>)}
    </ul>
    <p style={{ margin: "0 0 6px" }}><b>2. Models</b> - what the files were turned into</p>
    <ul style={{ margin: "0 0 10px", paddingLeft: 18 }}>
      {t.models.map((x: any) => <li key={x.role}>{x.label}: {x.built ? `built${x.rows != null ? `, ${x.rows} rows` : ""} from ${x.datasets.map((d: any) => d.file?.filename || d.name).join(", ")}` : "not built yet"}</li>)}
    </ul>
    <p style={{ margin: "0 0 6px" }}><b>3. Definition</b></p>
    <p style={{ margin: "0 0 10px" }}><code>{t.metric.aggregation}({t.metric.expression})</code>, version {t.metric.version}, {t.metric.status === "approved" ? "approved" : "needs approval"}.</p>
    <p style={{ margin: "0 0 6px" }}><b>4. Used by</b></p>
    <ul style={{ margin: 0, paddingLeft: 18 }}>
      <li>Reports: {t.used_by.reports.length ? t.used_by.reports.map((r: any) => r.title).join(", ") : "none"}</li>
      <li>Alerts: {t.used_by.alerts.length ? t.used_by.alerts.map((a: any) => a.name).join(", ") : "none"}</li>
      <li>Answers in the last 30 days: {t.used_by.answers_last_30_days}</li>
    </ul>
    {t.note && <p style={{ margin: "8px 0 0", color: "#b00020" }}>{t.note}</p>}
  </div>);
}

function Metrics() {
  const [rows, setRows] = useState<any[]>([]);
  const [msg, setMsg] = useState("");
  const [showing, setShowing] = useState("");   // metric whose lineage is open
  const refresh = useCallback(() => api("/metrics").then(setRows), []);
  useEffect(() => { refresh().catch(e => setMsg(e.message)); }, [refresh]);
  const approve = async (name: string) => {
    try { await api(`/metrics/${name}/approve`, { method: "POST" }); setMsg(`Approved ${name}.`); await refresh(); }
    catch (e: any) { setMsg(e.message); }
  };
  return (<>
    <div style={card}>
      <p style={{ marginTop: 0 }}>Only approved metrics can be used in answers and dashboards. Approval needs an administrator.</p>
      {msg && <p role="status">{msg}</p>}
      <table><thead><tr><th>Metric</th><th>Definition</th><th>Formula</th><th>Status</th><th></th></tr></thead>
        <tbody>{rows.map(m => <tr key={m.name} data-metric={m.name}>
          <td>{m.label} <small>v{m.version}</small></td><td>{m.description}</td><td><code>{m.expression}</code></td>
          <td>{m.status === "approved" ? "Approved" : "Needs approval"}</td>
          <td>{m.status !== "approved" && <button onClick={() => approve(m.name)}>Approve {m.label}</button>}{" "}
            <button style={{ background: "#6b7280" }} onClick={() => setShowing(showing === m.name ? "" : m.name)}>{showing === m.name ? "Hide lineage" : "Show lineage"}</button></td>
        </tr>).flatMap((tr, i) => showing === rows[i].name
          ? [tr, <tr key={`${rows[i].name}-lineage`}><td colSpan={5}><LineagePanel name={rows[i].name} /></td></tr>] : [tr])}</tbody></table>
    </div></>);
}

function Explanation({ a }: { a: any }) {
  const money = (v: number) => Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return <div data-testid="explanation">
    <p style={{ fontWeight: 600, marginTop: 0 }} data-testid="explanation-headline">{a.headline}</p>
    {a.anomaly?.flagged && <p data-testid="anomaly" style={{ background: "#fff6e5", padding: 8, borderRadius: 6 }}>
      Unusual: {a.anomaly.month} was {money(a.anomaly.value)} against an average of {money(a.anomaly.baseline_mean)} over the previous {a.anomaly.baseline_months} months.</p>}
    {Object.entries<any[]>(a.drivers || {}).map(([dim, rows]) => <div key={dim}>
      <h4 style={{ margin: "12px 0 4px" }}>Where it changed, by {dim}</h4>
      <table data-testid={`drivers-${dim}`}><thead><tr><th>{dim}</th><th>Before</th><th>Now</th><th>Change</th><th>Share of total change</th></tr></thead>
        <tbody>{rows.map(r => <tr key={r.member}><td>{r.member}{r.status !== "changed" ? ` (${r.status})` : ""}</td><td>{money(r.previous)}</td><td>{money(r.current)}</td>
          <td style={{ color: r.change < 0 ? "#b00020" : r.change > 0 ? "#1a7f37" : undefined }}>{r.change > 0 ? "+" : ""}{money(r.change)}</td>
          <td>{r.share_of_change === null ? "-" : `${r.share_of_change}%`}</td></tr>)}</tbody></table></div>)}
    <details><summary>How this was calculated</summary>
      <p><b>{a.metric.label}</b>: {a.metric.definition} (<code>{a.metric.expression}</code>, v{a.metric.version})</p>
      <p>Compared {a.period.current.join(", ")} with {a.period.previous.join(", ")}. Data up to {a.freshness.latest_order_date}.</p>
      {a.sql.map((q: string, i: number) => <pre key={i}>{q}</pre>)}</details>
    {a.caveats.map((c: string) => <p key={c}><small>{c}</small></p>)}
  </div>;
}

function AnswerView({ a }: { a: any }) {
  if (a.outcome === "explanation") return <Explanation a={a} />;
  if (a.outcome !== "answered") return <p>{a.message}</p>;
  return <>
    <table><thead><tr>{a.columns.map((c: string) => <th key={c}>{c}</th>)}</tr></thead>
      <tbody>{a.rows.map((r: any[], i: number) => <tr key={i}>{r.map((v, j) => <td key={j}>{String(v)}</td>)}</tr>)}</tbody></table>
    {a.truncated && <p><small>Showing the first {a.rows.length} of {a.rows_total} rows. Download the CSV for all of them.</small></p>}
    <details><summary>How this was calculated</summary>
      <p><b>{a.metric.label}</b>: {a.metric.definition} (<code>{a.metric.expression}</code>, v{a.metric.version})</p>
      <p>Data up to {a.freshness.latest_order_date}</p><pre>{a.sql}</pre></details>
    {(a.caveats || []).map((c: string) => <p key={c}><small>{c}</small></p>)}
  </>;
}

type Msg = { id?: string; q: string; a: any; feedback?: string | null };

function Ask() {
  const [q, setQ] = useState("");
  const [conv, setConv] = useState("");
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [hist, setHist] = useState<any[]>([]);
  const [busy, setBusy] = useState(false);
  const [role, setRole] = useState("");
  const [note, setNote] = useState("");
  const [why, setWhy] = useState<number | null>(null);   // which answer is being marked "not helpful"
  const [reason, setReason] = useState("");
  const loadHist = useCallback(() => api("/conversations").then(setHist).catch(() => {}), []);
  useEffect(() => { loadHist(); api("/auth/me").then(r => setRole(r.role)).catch(() => {}); }, [loadHist]);

  const send = async () => {
    const text = q.trim();
    if (!text || busy) return;
    setBusy(true); setQ(""); setNote("");
    try {
      let id = conv;
      if (!id) { id = (await api("/conversations", { method: "POST" })).id; setConv(id); }
      const a = await api(`/conversations/${id}/messages`, { method: "POST", body: JSON.stringify({ question: text }) })
        .catch(e => ({ outcome: "error", message: e.message }));
      setMsgs(m => [...m, { id: a.message_id, q: text, a }]);
      loadHist();
    } catch (e: any) { setNote(e.message); }
    finally { setBusy(false); }
  };
  const open = async (id: string) => {
    if (!id) return;
    try {
      const c = await api(`/conversations/${id}`);
      setConv(id); setNote(""); setWhy(null);
      setMsgs(c.messages.map((x: any) => ({ id: x.id, q: x.question, feedback: x.feedback,
        a: { ...x.answer, outcome: x.outcome, interpreted: x.interpreted } })));
    } catch (e: any) { setNote(e.message); }
  };
  const fresh = () => { setConv(""); setMsgs([]); setNote(""); setWhy(null); setQ(""); };
  const rate = async (i: number, rating: "up" | "down", comment?: string) => {
    const m = msgs[i];
    if (!m.id) return;
    try {
      await api(`/messages/${m.id}/feedback`, { method: "POST", body: JSON.stringify({ rating, comment: comment || undefined }) });
      setMsgs(list => list.map((x, k) => k === i ? { ...x, feedback: rating } : x));
      setWhy(null); setReason(""); setNote(rating === "up" ? "Thanks for the feedback." : "Thanks, noted.");
    } catch (e: any) { setNote(e.message); }
  };
  const download = async (m: Msg) => {
    try { await apiDownload(`/messages/${m.id}/export.csv`, `answer-${(m.id || "").slice(0, 8)}.csv`); setNote("CSV downloaded."); }
    catch (e: any) { setNote(e.message); }
  };
  const save = async (m: Msg) => {
    const question = m.a.interpreted || m.q;    // a follow-up is saved as the full question it meant
    try { await api("/reports", { method: "POST", body: JSON.stringify({ title: question.slice(0, 120), question }) }); setNote("Saved under Reports."); }
    catch (e: any) { setNote(e.message); }
  };
  const canExport = role === "admin" || role === "analyst";
  return (<>
    <div style={card}>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 8 }}>
        <button onClick={fresh} disabled={!msgs.length && !conv}>New conversation</button>
        {hist.length > 0 && <select aria-label="Earlier conversations" value="" onChange={e => open(e.target.value)} style={{ maxWidth: 320 }}>
          <option value="">Earlier conversations…</option>
          {hist.map(h => <option key={h.id} value={h.id}>{h.title} ({h.messages})</option>)}
        </select>}
      </div>
      <input style={{ width: "70%" }} disabled={busy} placeholder="e.g. Why has gross profit changed over the last 3 months? Or: net revenue by category"
        value={q} onChange={e => setQ(e.target.value)} onKeyDown={e => e.key === "Enter" && send()} /> <button onClick={send} disabled={busy}>Ask</button>
      <p style={{ margin: "8px 0 0", fontSize: 13 }}>You can follow up, for example "and by region?", "what about the last 3 months?" or "same for gross profit".</p>
      {note && <p role="status" data-testid="chat-note" style={{ margin: "8px 0 0" }}>{note}</p>}
    </div>
    <div data-testid="chat">
      {msgs.map((m, i) => <div key={m.id || i} style={card} data-testid="chat-message">
        <p style={{ marginTop: 0 }}><b>You:</b> {m.q}</p>
        {m.a.interpreted && <p><small data-testid="understood">Understood as: {m.a.interpreted}</small></p>}
        <AnswerView a={m.a} />
        {m.id && (m.a.outcome === "answered" || m.a.outcome === "explanation") && <p style={{ marginBottom: 0 }}>
          <button onClick={() => save(m)}>Save as report</button>{" "}
          {m.a.outcome === "answered" && canExport && <><button onClick={() => download(m)}>Download CSV</button>{" "}</>}
          <button onClick={() => rate(i, "up")} aria-pressed={m.feedback === "up"} style={m.feedback === "up" ? { outline: "2px solid #1a7f37" } : undefined}>Helpful</button>{" "}
          <button onClick={() => setWhy(i)} aria-pressed={m.feedback === "down"} style={m.feedback === "down" ? { outline: "2px solid #b00020" } : undefined}>Not helpful</button>
        </p>}
        {why === i && <p><input style={{ width: "60%" }} placeholder="What was wrong? (optional)" maxLength={500} value={reason} onChange={e => setReason(e.target.value)} />{" "}
          <button onClick={() => rate(i, "down", reason)}>Send</button> <button onClick={() => { setWhy(null); setReason(""); }}>Cancel</button></p>}
      </div>)}
    </div>
  </>);
}

function ResultTable({ a }: { a: any }) {
  if (a.outcome === "explanation") return <Explanation a={a} />;
  if (a.outcome !== "answered") return <p>{a.message}</p>;
  return <>
    <table><thead><tr>{a.columns.map((c: string) => <th key={c}>{c}</th>)}</tr></thead>
      <tbody>{a.rows.map((r: any[], i: number) => <tr key={i}>{r.map((v, j) => <td key={j}>{String(v)}</td>)}</tr>)}</tbody></table>
    <small>{a.metric.label} v{a.metric.version} — data up to {a.freshness.latest_order_date}</small>
    {a.caveats.map((c: string) => <div key={c}><small>{c}</small></div>)}
  </>;
}

function Reports() {
  const [rows, setRows] = useState<any[]>([]);
  const [result, setResult] = useState<any>(null);
  const [msg, setMsg] = useState("");
  const refresh = useCallback(() => api("/reports").then(setRows), []);
  useEffect(() => { refresh().catch(e => setMsg(e.message)); }, [refresh]);
  const run = async (id: string) => setResult(await api(`/reports/${id}/run`).catch(e => ({ outcome: "error", message: e.message })));
  const del = async (id: string) => { try { await api(`/reports/${id}`, { method: "DELETE" }); setResult(null); await refresh(); } catch (e: any) { setMsg(e.message); } };
  return (<>
    <Summaries />
    <div style={card}>
      <p style={{ marginTop: 0 }}>Reports are saved questions. Each run uses the current approved metric definitions and data.</p>
      {msg && <p role="alert">{msg}</p>}
      {!rows.length && <p>No reports yet. Ask a question under Ask Your Data and choose Save as report.</p>}
      <table><tbody>{rows.map(r => <tr key={r.id}><td><b>{r.title}</b><br /><small>{r.question}</small></td>
        <td><button onClick={() => run(r.id)}>Run</button> <button onClick={() => del(r.id)}>Delete</button></td></tr>)}</tbody></table>
    </div>
    {result && <div style={card} data-testid="report-result"><h3 style={{ marginTop: 0 }}>{result.title}</h3><ResultTable a={result} /></div>}
  </>);
}

function Alerts() {
  const [rows, setRows] = useState<any[]>([]);
  const [metrics, setMetrics] = useState<any[]>([]);
  const [f, setF] = useState({ name: "", metric: "", comparison: "below", threshold: "" });
  const [msg, setMsg] = useState("");
  const refresh = useCallback(() => api("/alerts").then(setRows), []);
  useEffect(() => {
    let live = true;  // ignore responses that arrive after the screen was left or the effect re-ran
    refresh().catch(e => setMsg(e.message));
    api("/metrics").then(ms => {
      if (!live) return;
      const ok = ms.filter((x: any) => x.status === "approved");
      setMetrics(ok);
      setF(v => ({ ...v, metric: v.metric || ok[0]?.name || "" }));  // never overwrite a choice the user already made
    }).catch(() => {});
    return () => { live = false; };
  }, [refresh]);
  const create = async () => {
    try { await api("/alerts", { method: "POST", body: JSON.stringify({ ...f, threshold: Number(f.threshold) }) }); setMsg("Alert created."); setF({ ...f, name: "", threshold: "" }); await refresh(); }
    catch (e: any) { setMsg(e.message); }
  };
  const check = async () => { try { await api("/alerts/evaluate", { method: "POST" }); await refresh(); setMsg("Checked."); } catch (e: any) { setMsg(e.message); } };
  const del = async (id: string) => { try { await api(`/alerts/${id}`, { method: "DELETE" }); await refresh(); } catch (e: any) { setMsg(e.message); } };
  return (<>
    <div style={card}>
      <h3 style={{ marginTop: 0 }}>New alert</h3>
      <input placeholder="Name, e.g. Profit below target" value={f.name} onChange={e => setF({ ...f, name: e.target.value })} />
      <select value={f.metric} disabled={!metrics.length} onChange={e => setF(v => ({ ...v, metric: e.target.value }))} style={{ marginRight: 8 }}>{metrics.map(m => <option key={m.name} value={m.name}>{m.label}</option>)}</select>
      <select value={f.comparison} onChange={e => setF(v => ({ ...v, comparison: e.target.value }))} style={{ marginRight: 8 }}><option value="below">is below</option><option value="above">is above</option></select>
      <input placeholder="Threshold" value={f.threshold} onChange={e => setF({ ...f, threshold: e.target.value })} />
      <button onClick={create} disabled={!f.metric || !f.name || f.threshold === "" || Number.isNaN(Number(f.threshold))}>Create alert</button>
      {msg && <p role="status">{msg}</p>}
    </div>
    <div style={card}>
      <p style={{ marginTop: 0 }}><button onClick={check}>Check now</button> <small>Alerts are also checked automatically about once an hour by the background worker, and you see a banner on Home when one is triggered.</small></p>
      <table><thead><tr><th>Alert</th><th>Rule</th><th>Last value</th><th>State</th><th>Last checked</th><th></th></tr></thead>
        <tbody>{rows.map(a => <tr key={a.id} data-alert={a.name}><td>{a.name}</td><td>{a.metric} {a.comparison} {a.threshold}</td>
          <td>{a.last_value ?? "–"}</td>
          <td style={{ color: a.last_state === "triggered" ? "#b00020" : a.last_state === "error" ? "#8a5a00" : "#1a7f37" }}>{a.last_state ?? "not checked"}</td>
          <td>{a.last_checked_at ? new Date(a.last_checked_at).toLocaleString() : "–"}</td>
          <td><button onClick={() => del(a.id)}>Delete</button></td></tr>)}</tbody></table>
    </div></>);
}

function Activity() {
  const [events, setEvents] = useState<any[]>([]);
  const [jobs, setJobs] = useState<any[]>([]);
  const [usage, setUsage] = useState<any>(null);
  const [fb, setFb] = useState<any>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    api("/feedback/summary").then(setFb).catch(() => {});
    api("/jobs").then(setJobs).catch(() => {});
    api("/usage").then(setUsage).catch(() => {});
    api("/activity").then(setEvents).catch(e => setErr(e.message));
  }, []);
  return (<>
    {usage && <div style={card} data-testid="usage"><h3 style={{ marginTop: 0 }}>Usage</h3>
      {usage.files} files, {usage.datasets} datasets, {usage.rows_loaded} rows loaded, {usage.reports} reports, {usage.alerts} alerts. Questions in the last 30 days: {usage.questions_last_30_days.answered} answered, {usage.questions_last_30_days.refusal} refused.
      {usage.quota && <div data-testid="plan-limits" style={{ marginTop: 8 }}>
        <p style={{ margin: "0 0 4px" }}>Plan: <b>{usage.quota.plan}</b> (placeholder limits, 0 = unlimited)</p>
        <table><thead><tr><th>What</th><th>Used</th><th>Limit</th></tr></thead><tbody>
          {([["uploads", "Files uploaded (30 days)"], ["rows", "Rows loaded (30 days)"], ["questions", "Questions (24 hours)"], ["users", "People"], ["documents", "Knowledge documents"]] as [string, string][]).map(([k, label]) =>
            <tr key={k}><td>{label}</td><td>{Number(usage.quota.used[k]).toLocaleString()}</td><td>{usage.quota.limits[k] ? Number(usage.quota.limits[k]).toLocaleString() : "unlimited"}</td></tr>)}
        </tbody></table></div>}</div>}
    {fb && <div style={card} data-testid="feedback"><h3 style={{ marginTop: 0 }}>Answer feedback</h3>
      <p>{fb.helpful} marked helpful, {fb.not_helpful} marked not helpful.</p>
      {fb.recent_not_helpful.length > 0 && <table><thead><tr><th>Question</th><th>What was wrong</th><th>When</th></tr></thead>
        <tbody>{fb.recent_not_helpful.map((x: any, i: number) => <tr key={i}><td>{x.question}</td><td>{x.comment || "-"}</td><td>{new Date(x.created_at).toLocaleString()}</td></tr>)}</tbody></table>}</div>}
    <div style={card}><h3 style={{ marginTop: 0 }}>Upload jobs</h3>
      {!jobs.length ? <p>No uploads yet.</p> : <table><thead><tr><th>File</th><th>Status</th><th>Rows</th><th>When</th></tr></thead>
        <tbody>{jobs.map(j => <tr key={j.id}><td>{j.filename}</td><td>{j.status}{j.error_message ? `: ${j.error_message}` : ""}</td><td>{j.rows_loaded}</td><td>{new Date(j.created_at).toLocaleString()}</td></tr>)}</tbody></table>}</div>
    <div style={card}><h3 style={{ marginTop: 0 }}>Activity log</h3>
      {err ? <p>{err}</p> : <table><thead><tr><th>When</th><th>Action</th><th>Detail</th></tr></thead>
        <tbody>{events.map(e => <tr key={e.id}><td>{new Date(e.created_at).toLocaleString()}</td><td>{e.action}</td><td><small>{JSON.stringify(e.detail)}</small></td></tr>)}</tbody></table>}</div>
  </>);
}

function AccountSecurity({ onLoaded }: { onLoaded?: (me: any) => void }) {
  const [me, setMe] = useState<any>(null);
  const [setup, setSetup] = useState<any>(null);
  const [codes, setCodes] = useState<string[]>([]);
  const [code, setCode] = useState("");
  const [pw, setPw] = useState({ current_password: "", new_password: "" });
  const [msg, setMsg] = useState("");
  const load = useCallback(() => api("/auth/me").then(r => { setMe(r); onLoaded?.(r); }).catch(e => setMsg(e.message)), [onLoaded]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {   // the organisation policy may have just been changed in the settings form above
    const again = () => { load(); };
    window.addEventListener("settings-saved", again);
    return () => window.removeEventListener("settings-saved", again);
  }, [load]);
  const begin = async () => { try { setSetup(await api("/auth/mfa/setup", { method: "POST" })); setMsg(""); } catch (e: any) { setMsg(e.message); } };
  const enable = async () => {
    try { const r = await api("/auth/mfa/enable", { method: "POST", body: JSON.stringify({ code }) }); setCodes(r.recovery_codes); setSetup(null); setCode(""); await load(); setMsg("Two-step sign-in is on."); }
    catch (e: any) { setMsg(e.message); }
  };
  const disable = async () => {
    try { await api("/auth/mfa/disable", { method: "POST", body: JSON.stringify({ password: pw.current_password, code }) }); setCode(""); setCodes([]); await load(); setMsg("Two-step sign-in is off."); }
    catch (e: any) { setMsg(e.message); }
  };
  const changePw = async () => {
    try { await api("/auth/change-password", { method: "POST", body: JSON.stringify(pw) }); setPw({ current_password: "", new_password: "" }); setMsg("Password changed."); }
    catch (e: any) { setMsg(e.message); }
  };
  if (!me) return <div style={card}>{msg || "Loading…"}</div>;
  return (<div style={card} data-testid="security">
    <h3 style={{ marginTop: 0 }}>Your sign-in</h3>
    <p>{me.email} — two-step sign-in is <b>{me.mfa_enabled ? "on" : "off"}</b>{me.mfa_enabled ? ` (${me.recovery_codes_left} recovery codes left)` : ""}.</p>
    {me.mfa_required && <p data-testid="mfa-required-note"><b>Your organisation requires two-step sign-in.</b>{me.mfa_enabled ? " It cannot be turned off." : " Set it up below to continue."}</p>}
    {!me.mfa_enabled && !setup && <button onClick={begin}>Set up two-step sign-in</button>}
    {setup && <div>
      <p>Add this key to an authenticator app (Microsoft Authenticator, Google Authenticator, 1Password…), then enter the 6-digit code it shows.</p>
      <p>Key: <code data-testid="mfa-secret">{setup.secret}</code></p>
      <p><small>Or open this link on your phone: <code>{setup.otpauth_uri}</code></small></p>
      <input placeholder="6-digit code" value={code} onChange={e => setCode(e.target.value)} />
      <button onClick={enable} disabled={code.trim().length < 6}>Turn on</button></div>}
    {codes.length > 0 && <div data-testid="recovery-codes"><p><b>Recovery codes</b> — save them now. Each works once and they are not shown again.</p>
      <pre>{codes.join("\n")}</pre></div>}
    {me.mfa_enabled && !me.mfa_required && <div><p>To turn it off, enter your password and a current code.</p>
      <input type="password" placeholder="Current password" value={pw.current_password} onChange={e => setPw({ ...pw, current_password: e.target.value })} />
      <input placeholder="Authenticator or recovery code" value={code} onChange={e => setCode(e.target.value)} />
      <button onClick={disable}>Turn off</button></div>}
    <h4>Change password</h4>
    <input type="password" placeholder="Current password" value={pw.current_password} onChange={e => setPw({ ...pw, current_password: e.target.value })} />
    <input type="password" placeholder="New password (10+ characters)" value={pw.new_password} onChange={e => setPw({ ...pw, new_password: e.target.value })} />
    <button onClick={changePw} disabled={!pw.current_password || pw.new_password.length < 10}>Change password</button>
    {msg && <p role="status">{msg}</p>}
  </div>);
}

function Users() {
  const [rows, setRows] = useState<any[] | null>(null);
  const [f, setF] = useState({ email: "", password: "", role: "viewer" });
  const [msg, setMsg] = useState("");
  const [erasing, setErasing] = useState("");
  const load = useCallback(() => api("/users").then(setRows).catch(e => setMsg(e.message)), []);
  useEffect(() => { load(); }, [load]);
  const add = async () => {
    try { await api("/users", { method: "POST", body: JSON.stringify(f) }); setMsg(`Added ${f.email}. Share the password securely; they can change it after signing in.`); setF({ email: "", password: "", role: "viewer" }); await load(); }
    catch (e: any) { setMsg(e.message); }
  };
  const patch = async (id: string, body: any) => { try { await api(`/users/${id}`, { method: "PATCH", body: JSON.stringify(body) }); await load(); } catch (e: any) { setMsg(e.message); } };
  const erase = async (id: string, email: string) => {
    try { await api(`/users/${id}`, { method: "DELETE" }); setErasing(""); setMsg(`Erased ${email}.`); await load(); }
    catch (e: any) { setErasing(""); setMsg(e.message); }
  };
  if (!rows) return <div style={card}>{msg || "Users are visible to administrators only."}</div>;
  return (<div style={card} data-testid="users">
    <h3 style={{ marginTop: 0 }}>People</h3>
    <table><thead><tr><th>Email</th><th>Role</th><th>Two-step</th><th>Status</th><th></th></tr></thead>
      <tbody>{rows.map(u => <tr key={u.id} data-user={u.email}><td>{u.email}</td>
        <td><select value={u.role} onChange={e => patch(u.id, { role: e.target.value })}><option>admin</option><option>analyst</option><option>viewer</option></select></td>
        <td>{u.mfa_enabled ? "on" : "off"}</td><td>{u.active ? "active" : "deactivated"}</td>
        <td><button onClick={() => patch(u.id, { active: !u.active })}>{u.active ? "Deactivate" : "Reactivate"}</button>{" "}
          {u.mfa_enabled && <><button onClick={() => patch(u.id, { reset_mfa: true })} style={{ background: "#6b7280" }} title="Use when someone loses their phone: they set it up again at next sign-in">Reset two-step</button>{" "}</>}
          {erasing === u.id
            ? <><button onClick={() => erase(u.id, u.email)} style={{ background: "#b00020" }}>Confirm erase</button> <button onClick={() => setErasing("")}>Cancel</button></>
            : <button onClick={() => setErasing(u.id)} style={{ background: "#6b7280" }}>Erase</button>}</td></tr>)}</tbody></table>
    <small>Erasing removes the person's e-mail, password and sign-in links permanently; the activity log keeps only an anonymous id.</small>
    <h4>Add a person</h4>
    <input placeholder="Email" value={f.email} onChange={e => setF({ ...f, email: e.target.value })} />
    <input type="password" placeholder="Initial password (10+ characters)" value={f.password} onChange={e => setF({ ...f, password: e.target.value })} />
    <select value={f.role} onChange={e => setF({ ...f, role: e.target.value })}><option>viewer</option><option>analyst</option><option>admin</option></select>{" "}
    <button onClick={add} disabled={!f.email || f.password.length < 10}>Add person</button>
    {msg && <p role="status">{msg}</p>}
  </div>);
}

function YourData() {
  const [raw, setRaw] = useState(false);
  const [msg, setMsg] = useState("");
  const [d, setD] = useState({ confirm_name: "", password: "", code: "" });
  const exportAll = async () => {
    setMsg("Preparing export…");
    try { await apiDownload(`/export/tenant?include_raw=${raw}`, "datapilot-export.zip"); setMsg("Export downloaded."); }
    catch (e: any) { setMsg(e.message); }
  };
  const del = async () => {
    try {
      await api("/tenant/delete", { method: "POST", body: JSON.stringify({ confirm_name: d.confirm_name, password: d.password, ...(d.code ? { code: d.code } : {}) }) });
      setToken(""); window.location.reload();
    } catch (e: any) { setMsg(e.message); }
  };
  return (<>
    <div style={card} data-testid="export">
      <h3 style={{ marginTop: 0 }}>Export your data</h3>
      <p>Download everything we hold for your organisation: records as JSON, modelled tables as CSV, and (optionally) your original uploads. Passwords and sign-in secrets are never included.</p>
      <label style={{ display: "inline-block", marginRight: 12 }}><input type="checkbox" checked={raw} onChange={e => setRaw(e.target.checked)} style={{ display: "inline", width: "auto", marginRight: 6 }} />Include original uploaded files</label>
      <button onClick={exportAll}>Download export</button> {msg && <span role="status">{msg}</span>}
    </div>
    <div style={{ ...card, border: "1px solid #e0b4b4" }} data-testid="danger">
      <h3 style={{ marginTop: 0, color: "#b00020" }}>Delete this organisation</h3>
      <p>Permanently erases all data, files, reports and people in this organisation. This cannot be undone. Export your data first if you need a copy.</p>
      <input placeholder="Type the organisation name exactly" value={d.confirm_name} onChange={e => setD({ ...d, confirm_name: e.target.value })} />
      <input type="password" placeholder="Your password" value={d.password} onChange={e => setD({ ...d, password: e.target.value })} />
      <input placeholder="Two-step code or recovery code (if you use two-step sign-in)" value={d.code} onChange={e => setD({ ...d, code: e.target.value })} />
      <button onClick={del} disabled={!d.confirm_name || !d.password} style={{ background: "#b00020" }}>Delete organisation and all data</button>
    </div>
  </>);
}

function Settings() {
  const [f, setF] = useState({ name: "", currency: "", timezone: "", auto_model: true, require_mfa: false });
  const [role, setRole] = useState("");
  const [msg, setMsg] = useState("");
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    let live = true;
    api("/settings").then(r => { if (!live) return; setF({ name: r.name, currency: r.currency, timezone: r.timezone, auto_model: r.auto_model, require_mfa: r.require_mfa }); setRole(r.your_role); setLoaded(true); })
      .catch(e => live && setMsg(e.message));
    return () => { live = false; };
  }, []);
  const save = async () => { try { await api("/settings", { method: "PATCH", body: JSON.stringify(f) }); setMsg("Saved."); window.dispatchEvent(new Event("settings-saved")); } catch (e: any) { setMsg(e.message); } };
  return (<><div style={card}>
    <p style={{ marginTop: 0 }}>Your role: <b>{role}</b>. Only administrators can change settings.</p>
    <label>Organisation<input disabled={!loaded} value={f.name} onChange={e => setF(v => ({ ...v, name: e.target.value }))} /></label>
    <label>Currency (3 letters, e.g. GBP)<input disabled={!loaded} value={f.currency} onChange={e => setF(v => ({ ...v, currency: e.target.value.toUpperCase() }))} /></label>
    <label>Time zone (e.g. Europe/London)<input disabled={!loaded} value={f.timezone} onChange={e => setF(v => ({ ...v, timezone: e.target.value }))} /></label>
    <label style={{ display: "block", margin: "8px 0" }}><input type="checkbox" disabled={!loaded} checked={f.auto_model}
      onChange={e => setF(v => ({ ...v, auto_model: e.target.checked }))} style={{ display: "inline", width: "auto", marginRight: 6 }} />
      Build models and approve standard metrics automatically when uploaded columns are recognised</label>
    <p style={{ margin: "0 0 8px", fontSize: 13 }}>Anything unclear is still left for a person to confirm, and every automatic step is recorded in the Activity log. Turn this off if every model and metric must be approved by hand.</p>
    <label style={{ display: "block", margin: "8px 0" }}><input type="checkbox" disabled={!loaded || role !== "admin"} checked={f.require_mfa}
      onChange={e => setF(v => ({ ...v, require_mfa: e.target.checked }))} style={{ display: "inline", width: "auto", marginRight: 6 }} />
      Require two-step sign-in for everyone in this organisation</label>
    <p style={{ margin: "0 0 8px", fontSize: 13 }}>People who have not set it up can only reach the set-up screen until they do. You must have it on for your own account first. People who sign in through a company identity provider use that provider's own checks.</p>
    <button onClick={save} disabled={!loaded || role !== "admin"}>Save settings</button> {msg && <span role="status">{msg}</span>}
  </div>{role === "admin" && <YourData />}</>);
}

export default function Page() {
  const [authed, setAuthed] = useState(false);
  const [tab, setTab] = useState<Tab>("home");
  const [enrol, setEnrol] = useState(false);            // organisation requires two-step and this person has not set it up
  const [enrolMe, setEnrolMe] = useState<any>(null);
  useEffect(() => {
    if (!authed) return;
    api("/auth/me").then(r => setEnrol(!!r.mfa_enrollment_needed)).catch(() => {});
    const on = () => setEnrol(true);
    window.addEventListener("mfa-needed", on);
    return () => window.removeEventListener("mfa-needed", on);
  }, [authed]);
  if (!authed) return <Auth onAuthed={() => setAuthed(true)} />;
  if (enrol) return (
    <main style={{ maxWidth: 900, margin: "0 auto", padding: 24 }} data-testid="mfa-enrol">
      <div style={card}>
        <h2 style={{ marginTop: 0 }}>Set up two-step sign-in to continue</h2>
        <p>Your organisation requires two-step sign-in. Add DataPilot to an authenticator app, enter the code, and save your recovery codes.</p>
      </div>
      <AccountSecurity onLoaded={setEnrolMe} />
      {enrolMe?.mfa_enabled && <button onClick={() => setEnrol(false)}>I have saved my recovery codes - continue</button>}
    </main>);
  return (
    <main style={{ maxWidth: 900, margin: "0 auto", padding: 24 }}>
      <nav style={{ marginBottom: 16 }}>
        {(["home", "sources", "metrics", "ask", "reports", "alerts", "activity", "settings"] as Tab[]).map(t => <button key={t} onClick={() => setTab(t)} disabled={tab === t} style={{ marginRight: 8 }}>{{ home: "Home", sources: "Data Sources", metrics: "Business Metrics", ask: "Ask Your Data", reports: "Reports", alerts: "Alerts", activity: "Activity", settings: "Settings" }[t]}</button>)}
      </nav>
      {tab === "home" ? <Home /> : tab === "sources" ? <Sources /> : tab === "metrics" ? <Metrics /> : tab === "reports" ? <Reports /> : tab === "alerts" ? <Alerts /> : tab === "activity" ? <Activity /> : tab === "settings" ? <><Settings /><AccountSecurity /><Users /></> : <Ask />}
    </main>
  );
}
