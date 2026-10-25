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
        if (j.status === "succeeded") return "ingested";
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
    <div style={card}>
      <input type="file" multiple accept=".csv,.xlsx,.txt,.json,.jsonl" aria-label="Choose files to upload"
        onChange={e => { const fs = Array.from(e.target.files || []); e.target.value = ""; if (fs.length) uploadMany(fs); }} /> <span>{msg}</span>
      {results.length > 0 && <ul data-testid="upload-results" style={{ margin: "8px 0 0", paddingLeft: 18 }}>
        {results.map((r, i) => <li key={i}>{r.name} - {r.status}</li>)}</ul>}
    </div>
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
  const [saved, setSaved] = useState("");
  const save = async () => {
    try { await api("/reports", { method: "POST", body: JSON.stringify({ title: q.slice(0, 120), question: q }) }); setSaved("Saved under Reports."); }
    catch (e: any) { setSaved(e.message); }
  };
  const go = async () => { setSaved(""); await ask(); };
  const ask = async () => setA(await api("/analytics/query", { method: "POST", body: JSON.stringify({ question: q }) }).catch(e => ({ outcome: "error", message: e.message })));
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
        <p><button onClick={save}>Save as report</button> <small role="status">{saved}</small></p>
      </>}
    </div>}
  </>);
}

function ResultTable({ a }: { a: any }) {
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
      <p style={{ marginTop: 0 }}><button onClick={check}>Check now</button> <small>Alerts are checked when you press this button; scheduled checks need the background worker.</small></p>
      <table><thead><tr><th>Alert</th><th>Rule</th><th>Last value</th><th>State</th><th></th></tr></thead>
        <tbody>{rows.map(a => <tr key={a.id} data-alert={a.name}><td>{a.name}</td><td>{a.metric} {a.comparison} {a.threshold}</td>
          <td>{a.last_value ?? "–"}</td>
          <td style={{ color: a.last_state === "triggered" ? "#b00020" : a.last_state === "error" ? "#8a5a00" : "#1a7f37" }}>{a.last_state ?? "not checked"}</td>
          <td><button onClick={() => del(a.id)}>Delete</button></td></tr>)}</tbody></table>
    </div></>);
}

function Activity() {
  const [events, setEvents] = useState<any[]>([]);
  const [jobs, setJobs] = useState<any[]>([]);
  const [usage, setUsage] = useState<any>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    api("/jobs").then(setJobs).catch(() => {});
    api("/usage").then(setUsage).catch(() => {});
    api("/activity").then(setEvents).catch(e => setErr(e.message));
  }, []);
  return (<>
    {usage && <div style={card} data-testid="usage"><h3 style={{ marginTop: 0 }}>Usage</h3>
      {usage.files} files, {usage.datasets} datasets, {usage.rows_loaded} rows loaded, {usage.reports} reports, {usage.alerts} alerts. Questions in the last 30 days: {usage.questions_last_30_days.answered} answered, {usage.questions_last_30_days.refusal} refused.</div>}
    <div style={card}><h3 style={{ marginTop: 0 }}>Upload jobs</h3>
      {!jobs.length ? <p>No uploads yet.</p> : <table><thead><tr><th>File</th><th>Status</th><th>Rows</th><th>When</th></tr></thead>
        <tbody>{jobs.map(j => <tr key={j.id}><td>{j.filename}</td><td>{j.status}{j.error_message ? `: ${j.error_message}` : ""}</td><td>{j.rows_loaded}</td><td>{new Date(j.created_at).toLocaleString()}</td></tr>)}</tbody></table>}</div>
    <div style={card}><h3 style={{ marginTop: 0 }}>Activity log</h3>
      {err ? <p>{err}</p> : <table><thead><tr><th>When</th><th>Action</th><th>Detail</th></tr></thead>
        <tbody>{events.map(e => <tr key={e.id}><td>{new Date(e.created_at).toLocaleString()}</td><td>{e.action}</td><td><small>{JSON.stringify(e.detail)}</small></td></tr>)}</tbody></table>}</div>
  </>);
}

function AccountSecurity() {
  const [me, setMe] = useState<any>(null);
  const [setup, setSetup] = useState<any>(null);
  const [codes, setCodes] = useState<string[]>([]);
  const [code, setCode] = useState("");
  const [pw, setPw] = useState({ current_password: "", new_password: "" });
  const [msg, setMsg] = useState("");
  const load = useCallback(() => api("/auth/me").then(setMe).catch(e => setMsg(e.message)), []);
  useEffect(() => { load(); }, [load]);
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
    {!me.mfa_enabled && !setup && <button onClick={begin}>Set up two-step sign-in</button>}
    {setup && <div>
      <p>Add this key to an authenticator app (Microsoft Authenticator, Google Authenticator, 1Password…), then enter the 6-digit code it shows.</p>
      <p>Key: <code data-testid="mfa-secret">{setup.secret}</code></p>
      <p><small>Or open this link on your phone: <code>{setup.otpauth_uri}</code></small></p>
      <input placeholder="6-digit code" value={code} onChange={e => setCode(e.target.value)} />
      <button onClick={enable} disabled={code.trim().length < 6}>Turn on</button></div>}
    {codes.length > 0 && <div data-testid="recovery-codes"><p><b>Recovery codes</b> — save them now. Each works once and they are not shown again.</p>
      <pre>{codes.join("\n")}</pre></div>}
    {me.mfa_enabled && <div><p>To turn it off, enter your password and a current code.</p>
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
  const [f, setF] = useState({ name: "", currency: "", timezone: "" });
  const [role, setRole] = useState("");
  const [msg, setMsg] = useState("");
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    let live = true;
    api("/settings").then(r => { if (!live) return; setF({ name: r.name, currency: r.currency, timezone: r.timezone }); setRole(r.your_role); setLoaded(true); })
      .catch(e => live && setMsg(e.message));
    return () => { live = false; };
  }, []);
  const save = async () => { try { await api("/settings", { method: "PATCH", body: JSON.stringify(f) }); setMsg("Saved."); } catch (e: any) { setMsg(e.message); } };
  return (<><div style={card}>
    <p style={{ marginTop: 0 }}>Your role: <b>{role}</b>. Only administrators can change settings.</p>
    <label>Organisation<input disabled={!loaded} value={f.name} onChange={e => setF(v => ({ ...v, name: e.target.value }))} /></label>
    <label>Currency (3 letters, e.g. GBP)<input disabled={!loaded} value={f.currency} onChange={e => setF(v => ({ ...v, currency: e.target.value.toUpperCase() }))} /></label>
    <label>Time zone (e.g. Europe/London)<input disabled={!loaded} value={f.timezone} onChange={e => setF(v => ({ ...v, timezone: e.target.value }))} /></label>
    <button onClick={save} disabled={!loaded || role !== "admin"}>Save settings</button> {msg && <span role="status">{msg}</span>}
  </div>{role === "admin" && <YourData />}</>);
}

export default function Page() {
  const [authed, setAuthed] = useState(false);
  const [tab, setTab] = useState<Tab>("home");
  if (!authed) return <Auth onAuthed={() => setAuthed(true)} />;
  return (
    <main style={{ maxWidth: 900, margin: "0 auto", padding: 24 }}>
      <nav style={{ marginBottom: 16 }}>
        {(["home", "sources", "metrics", "ask", "reports", "alerts", "activity", "settings"] as Tab[]).map(t => <button key={t} onClick={() => setTab(t)} disabled={tab === t} style={{ marginRight: 8 }}>{{ home: "Home", sources: "Data Sources", metrics: "Business Metrics", ask: "Ask Your Data", reports: "Reports", alerts: "Alerts", activity: "Activity", settings: "Settings" }[t]}</button>)}
      </nav>
      {tab === "home" ? <Home /> : tab === "sources" ? <Sources /> : tab === "metrics" ? <Metrics /> : tab === "reports" ? <Reports /> : tab === "alerts" ? <Alerts /> : tab === "activity" ? <Activity /> : tab === "settings" ? <><Settings /><AccountSecurity /><Users /></> : <Ask />}
    </main>
  );
}
