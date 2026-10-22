"use client";
import { useCallback, useEffect, useState } from "react";
import { api, setToken } from "./api";

type Tab = "home" | "sources" | "metrics" | "ask";
const card = { background: "#fff", borderRadius: 8, padding: 16, boxShadow: "0 1px 3px #0001", marginBottom: 16 } as const;

function Auth({ onAuthed }: { onAuthed: () => void }) {
  const [mode, setMode] = useState<"login" | "register">("register");
  const [f, setF] = useState({ organisation: "", email: "", password: "" });
  const [err, setErr] = useState("");
  const submit = async () => {
    try {
      const r = mode === "register" ? await api("/tenants", { method: "POST", body: JSON.stringify(f) })
                                    : await api("/auth/login", { method: "POST", body: JSON.stringify(f) });
      setToken(r.token); onAuthed();
    } catch (e: any) { setErr(e.message); }
  };
  return (
    <div style={{ ...card, maxWidth: 380, margin: "80px auto" }}>
      <h2>DataPilot AI</h2>
      {mode === "register" && <input placeholder="Organisation" value={f.organisation} onChange={e => setF({ ...f, organisation: e.target.value })} />}
      <input placeholder="Email" value={f.email} onChange={e => setF({ ...f, email: e.target.value })} />
      <input placeholder="Password (10+ chars)" type="password" value={f.password} onChange={e => setF({ ...f, password: e.target.value })} />
      <p><button onClick={submit}>{mode === "register" ? "Create workspace" : "Sign in"}</button>{" "}
        <a href="#" onClick={e => { e.preventDefault(); setMode(mode === "login" ? "register" : "login"); }}>{mode === "login" ? "Register" : "Sign in"}</a></p>
      {err && <p role="alert" style={{ color: "#b00020" }}>{err}</p>}
    </div>
  );
}

function Home() {
  const [tiles, setTiles] = useState<any[]>([]);
  useEffect(() => { api("/dashboard").then(r => setTiles(r.tiles)).catch(() => {}); }, []);
  if (!tiles.length) return <div style={card}>No approved metrics with data yet. Upload a file under Data Sources and approve the model.</div>;
  return <>{tiles.map(t => (
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

function Sources() {
  const [msg, setMsg] = useState("");
  const [datasets, setDatasets] = useState<any[]>([]);
  const [profile, setProfile] = useState<any>(null);
  const [quality, setQuality] = useState<any>(null);
  const [versions, setVersions] = useState<any[]>([]);
  const [rels, setRels] = useState<any[]>([]);
  const refresh = useCallback(() => api("/datasets").then(setDatasets), []);
  useEffect(() => { refresh().catch(() => {}); }, [refresh]);

  const upload = async (file: File) => {
    setMsg("Uploading…");
    try {
      const src = await api("/data-sources", { method: "POST", body: JSON.stringify({ name: file.name }) });
      const fd = new FormData(); fd.append("file", file);
      const up = await api(`/data-sources/${src.id}/uploads`, { method: "POST", body: fd });
      for (let i = 0; i < 30; i++) {
        const j = await api(`/jobs/${up.job_id}`);
        if (j.status === "succeeded") { setMsg("Ingested."); return refresh(); }
        if (j.status === "failed") return setMsg(`Ingestion failed: ${j.error_message}`);
        await new Promise(r => setTimeout(r, 1000));
      }
      setMsg("Still processing — check back shortly.");
    } catch (e: any) { setMsg(e.message); }
  };
  const open = async (id: string) => {
    setProfile(await api(`/datasets/${id}/profile`));
    setQuality(await api(`/datasets/${id}/quality`).catch(() => null));
    setVersions((await api(`/datasets/${id}/schema-versions`).catch(() => ({ versions: [] }))).versions);
    setRels((await api("/relationships").catch(() => ({ relationships: [] }))).relationships);
  };
  const approve = async () => {
    const mapping = Object.fromEntries(Object.entries<any>(profile.profile.suggested_mapping)
      .filter(([, v]) => !v.needs_clarification).map(([k, v]) => [k, v.column]));
    try { const r = await api(`/datasets/${profile.id}/approve`, { method: "POST", body: JSON.stringify({ mapping }) });
      setMsg(`Model built: ${r.rows_loaded} rows loaded, ${r.rows_rejected} quarantined.`); refresh(); open(profile.id); }
    catch (e: any) { setMsg(e.message); }
  };
  const approveDim = async () => {
    const sd = profile.profile.suggested_dimension;
    try { const r = await api(`/datasets/${profile.id}/approve`, { method: "POST", body: JSON.stringify({ mapping: sd.mapping, role: sd.role }) });
      setMsg(`${sd.label} list built: ${r.rows_loaded} loaded, ${r.rows_rejected} quarantined.`); refresh(); open(profile.id); }
    catch (e: any) { setMsg(e.message); }
  };
  return (<>
    <div style={card}><input type="file" accept=".csv,.xlsx,.txt,.json,.jsonl" onChange={e => e.target.files?.[0] && upload(e.target.files[0])} /> <span>{msg}</span></div>
    <div style={card}>{datasets.map(d => <div key={d.id}><a href="#" onClick={e => { e.preventDefault(); open(d.id); }}>{d.name}</a> — {d.rows} rows — {d.model_status}{d.role && d.role !== "fact_sales" ? ` (${d.role === "dim_product" ? "products" : "customers"} list)` : ""}</div>)}</div>
    {profile && <div style={card}>
      <h3>{profile.name}</h3>
      <table><thead><tr><th>Column</th><th>Type</th><th>Nulls</th><th>Distinct</th></tr></thead>
        <tbody>{profile.profile.columns.map((c: any) => <tr key={c.name}><td>{c.name}</td><td>{c.type}</td><td>{c.null_count}</td><td>{c.distinct}</td></tr>)}</tbody></table>
      <h4>Proposed mapping</h4>
      {Object.entries<any>(profile.profile.suggested_mapping).map(([k, v]) => <div key={k}>{k} ← {v.column} ({Math.round(v.confidence * 100)}%) {v.needs_clarification && <b style={{ color: "#b00020" }}>{v.needs_clarification}</b>}</div>)}
      {profile.profile.suggested_dimension && <>
        <h4>This looks like a {profile.profile.suggested_dimension.label.toLowerCase()} list</h4>
        <div>{Object.entries<string>(profile.profile.suggested_dimension.mapping).map(([k, v]) => <div key={k}>{k} ← {v}</div>)}</div>
        <p><button onClick={approveDim}>Approve as {profile.profile.suggested_dimension.label.toLowerCase()} list</button></p></>}
      {!profile.profile.suggested_dimension && <p><button onClick={approve}>Approve and build model</button></p>}
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

function Metrics() {
  const [rows, setRows] = useState<any[]>([]);
  const [msg, setMsg] = useState("");
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
          <td>{m.status !== "approved" && <button onClick={() => approve(m.name)}>Approve {m.label}</button>}</td>
        </tr>)}</tbody></table>
    </div></>);
}

function Ask() {
  const [q, setQ] = useState("");
  const [a, setA] = useState<any>(null);
  const go = async () => setA(await api("/analytics/query", { method: "POST", body: JSON.stringify({ question: q }) }).catch(e => ({ outcome: "error", message: e.message })));
  return (<>
    <div style={card}><input style={{ width: "70%" }} placeholder="e.g. Why has gross profit changed over the last 3 months? Or: net revenue by category" value={q} onChange={e => setQ(e.target.value)} onKeyDown={e => e.key === "Enter" && go()} /> <button onClick={go}>Ask</button></div>
    {a && <div style={card}>
      {a.outcome !== "answered" ? <p>{a.message}</p> : <>
        <table><thead><tr>{a.columns.map((c: string) => <th key={c}>{c}</th>)}</tr></thead>
          <tbody>{a.rows.map((r: any[], i: number) => <tr key={i}>{r.map((v, j) => <td key={j}>{String(v)}</td>)}</tr>)}</tbody></table>
        <details><summary>How this was calculated</summary>
          <p><b>{a.metric.label}</b>: {a.metric.definition} (<code>{a.metric.expression}</code>, v{a.metric.version})</p>
          <p>Data up to {a.freshness.latest_order_date}</p><pre>{a.sql}</pre></details>
        {a.caveats.map((c: string) => <p key={c}><small>{c}</small></p>)}
      </>}
    </div>}
  </>);
}

export default function Page() {
  const [authed, setAuthed] = useState(false);
  const [tab, setTab] = useState<Tab>("home");
  if (!authed) return <Auth onAuthed={() => setAuthed(true)} />;
  return (
    <main style={{ maxWidth: 900, margin: "0 auto", padding: 24 }}>
      <nav style={{ marginBottom: 16 }}>
        {(["home", "sources", "metrics", "ask"] as Tab[]).map(t => <button key={t} onClick={() => setTab(t)} disabled={tab === t} style={{ marginRight: 8 }}>{{ home: "Home", sources: "Data Sources", metrics: "Business Metrics", ask: "Ask Your Data" }[t]}</button>)}
      </nav>
      {tab === "home" ? <Home /> : tab === "sources" ? <Sources /> : tab === "metrics" ? <Metrics /> : <Ask />}
    </main>
  );
}
