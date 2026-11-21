import { useEffect, useRef, useState } from "react";
import "./source-schema-panel.css";

type Operation = { op: string; format?: string; values?: Record<string, string | boolean> };
type Definition = { version: number; source: string; payer: string | null; network: string | null; source_version: string | null;
  contract: string; grain: string; headers: string[]; mapping: Record<string, string>; transforms: Record<string, Operation[]>;
  required: string[]; required_when: { field: string; equals: string; require: string[] }[];
  evidence_refs: string[]; source_notes: Record<string, string>; [key: string]: unknown };
type Context = { payer: string | null; network: string | null; source_version: string | null; completeness: string };
type Profile = { inspection: { fields: string[]; row_count: number }; recognition: { status: string }; draft: Definition;
  catalog_revision: number; fields: string[]; variants?: { id: string; definition: Definition }[];
  pending: { id: string; origin: string; catalog_revision: number }[] };
type Preview = { preview_token: string; report: { coverage: string; row_count: number; rows_truncated: boolean; samples_clipped: boolean;
  rows: { locator: string; normalized: Record<string, unknown> }[]; findings: { counts: Record<string, number>; total: number };
  eligibility: { state: string } } };
const dateFields = new Set(["office_end", "credentialing_end", "effective_start", "effective_end", "source_updated_at"]);
const fieldLabels: Record<string, string> = { practitioner_npi: "Practitioner NPI", billing_npi: "Billing NPI", group_npi: "Group NPI",
  location_id: "Office identifier", directory_visible: "Directory visibility", office_end: "Office departure date",
  credentialing_end: "Credentialing end date" };
const fieldLabel = (field: string) => fieldLabels[field] || field.replaceAll("_", " ");

export function SourceSchemaPanel({ receiptId, attemptId }: { receiptId: string; attemptId: string }) {
  const [open, setOpen] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const [profile, setProfile] = useState<Profile | null>(null), [definition, setDefinition] = useState<Definition | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null), [saved, setSaved] = useState<string | null>(null);
  const [actor, setActor] = useState(""), [reason, setReason] = useState(""), [notice, setNotice] = useState("");
  const [context, setContext] = useState<Context>({ payer: null, network: null, source_version: null, completeness: "unknown" });
  const [needsInspection, setNeedsInspection] = useState(false), [extraFields, setExtraFields] = useState<string[]>([]);
  const [supersedes, setSupersedes] = useState(""), [conditionField, setConditionField] = useState(""), [conditionValue, setConditionValue] = useState(""), [conditionTarget, setConditionTarget] = useState("");
  const controller = useRef(new AbortController());
  useEffect(() => { const active = new AbortController(); controller.current = active; return () => active.abort(); }, []);
  const base = `/api/intakes/${encodeURIComponent(receiptId)}/source-schema`;
  const invalidate = () => { setPreview(null); setSaved(null); setNotice(""); };
  const request = async <T,>(path: string, body: unknown): Promise<T> => {
    const response = await fetch(base + path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal: controller.current.signal });
    if (!response.ok) {
      if (response.status === 404) throw new Error("Source format review is unavailable for this delivery or installation.");
      const result = await response.json();
      throw new Error(typeof result.detail === "string" ? result.detail : "Unable to complete source format review.");
    }
    const result = await response.json();
    if (controller.current.signal.aborted) throw new DOMException("Request cancelled", "AbortError");
    return result as T;
  };
  const run = async (action: () => Promise<void>) => {
    setBusy(true); setError("");
    try { await action(); } catch (caught) { if (!controller.current.signal.aborted) setError((caught as Error).message); }
    finally { if (!controller.current.signal.aborted) setBusy(false); }
  };
  const inspect = async () => {
    const result = await request<Profile>("/inspect", { expected_attempt: attemptId, context });
    setProfile(result); setDefinition(result.draft); setNeedsInspection(false); setExtraFields([]); setSupersedes(""); invalidate();
  };
  const change = (update: Partial<Definition>) => { if (definition) setDefinition({ ...definition, ...update }); invalidate(); };
  const setBinding = (field: string, column: string) => {
    if (!definition) return;
    const mapping = { ...definition.mapping }, transforms = { ...definition.transforms };
    if (column) mapping[field] = column; else { delete mapping[field]; delete transforms[field]; }
    change({ mapping, transforms });
  };
  const setFlag = (value: string, visible: boolean) => {
    if (!definition) return;
    const old = definition.transforms.directory_visible?.find(op => op.op === "value_map")?.values || {};
    const values = Object.fromEntries(Object.entries(old).filter(([, target]) => target !== visible));
    if (value) values[value] = visible;
    change({ transforms: { ...definition.transforms, directory_visible: [{ op: "value_map", values }] } });
  };
  const loadPending = async (id: string) => {
    const proposal = await request<{ variant: Definition; context: Context; catalog_revision: number }>(`/proposals/${id}/inspect`, { expected_attempt: attemptId });
    setDefinition(proposal.variant); setContext(proposal.context); setNeedsInspection(false); setPreview(null);
    setSaved(proposal.catalog_revision === profile?.catalog_revision ? id : null);
    setNotice("Loaded a proposed interpretation. Preview its values before recording a review.");
  };
  const fields = [...new Set(["practitioner_npi", "location_id", "directory_visible", ...Object.keys(definition?.mapping || {}), ...(definition?.required || []), ...extraFields])];
  const flagValues = definition?.transforms.directory_visible?.find(op => op.op === "value_map")?.values || {};
  const visibleValue = Object.entries(flagValues).find(([, value]) => value === true)?.[0] || "";
  const excludedValue = Object.entries(flagValues).find(([, value]) => value === false)?.[0] || "";
  const note = definition?.source_notes["analyst-source-note"] || Object.values(definition?.source_notes || {}).join("\n");
  return <section className="source-schema" aria-label="Source format catalog">
    <button type="button" aria-expanded={open} onClick={() => { setOpen(!open); if (!open && !profile) void run(inspect); }}>Source format catalog</button>
    {open && <div>
      <p>Interpret this sender’s columns, review a changed format and reuse an interpretation you have checked. Format review keeps dataset approval separate.</p>
      {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
      <fieldset disabled={busy}>
        <legend>Declared source context</legend>
        {(["payer", "network", "source_version"] as const).map(key => <label key={key}>{key === "source_version" ? "Source format version (if supplied)" : key === "payer" ? "Payer reference (if known)" : "Network reference (if known)"}
          <input value={context[key] || ""} onChange={event => { setContext({ ...context, [key]: event.target.value || null }); setNeedsInspection(true); invalidate(); }} /></label>)}
        <label>Declared delivery type<select value={context.completeness} onChange={event => { setContext({ ...context, completeness: event.target.value }); setNeedsInspection(true); invalidate(); }}>
          <option value="unknown">Unknown</option><option value="partial">Partial</option><option value="full">Full roster</option><option value="delta">Changes only</option>
        </select></label>
        <button type="button" onClick={() => void run(inspect)}>Inspect current source context</button>
      </fieldset>
      {profile && definition && <fieldset disabled={busy || needsInspection}>
        <legend>Review field meanings</legend>
        <p>{profile.inspection.row_count} records · Recognition: <strong>{profile.recognition.status.replaceAll("_", " ")}</strong> · Catalog revision {profile.catalog_revision}</p>
        <label>Row scope<select value={definition.grain} onChange={event => change({ grain: event.target.value })}>
          <option value="source_declared">Unresolved</option><option value="practitioner_office">Practitioner at one office</option><option value="provider">Provider level</option><option value="organization">Organization level</option>
        </select></label>
        <div className="source-schema-fields">{fields.map(field => <div key={field}>
          <label>{fieldLabel(field)}<select aria-label={`Source column for ${fieldLabel(field)}`} value={definition.mapping[field] || ""} onChange={event => setBinding(field, event.target.value)}>
            <option value="">Unresolved / not supplied</option>{profile.inspection.fields.map(column => <option key={column} value={column} disabled={Object.entries(definition.mapping).some(([target, source]) => target !== field && source === column)}>{column}</option>)}
          </select></label>
          {dateFields.has(field) && definition.mapping[field] && <label>Date format<select aria-label={`Date format for ${fieldLabel(field)}`} value={definition.transforms[field]?.find(op => op.op === "date")?.format || ""} onChange={event => change({ transforms: { ...definition.transforms, [field]: event.target.value ? [{ op: "date", format: event.target.value }] : [] } })}>
            <option value="">ISO date already supplied / unresolved</option><option value="%Y-%m-%d">YYYY-MM-DD</option><option value="%m/%d/%Y">MM/DD/YYYY</option><option value="%d/%m/%Y">DD/MM/YYYY</option>
          </select></label>}
        </div>)}</div>
        <label>Add another field<select value="" onChange={event => { if (event.target.value) setExtraFields([...extraFields, event.target.value]); }}>
          <option value="">Choose a field…</option>{profile.fields.filter(field => !fields.includes(field)).map(field => <option key={field} value={field}>{fieldLabel(field)}</option>)}
        </select></label>
        {definition.mapping.directory_visible && <div className="source-schema-fields">
          <label>Exact source value meaning “display”<input value={visibleValue} placeholder="For example, N" onChange={event => setFlag(event.target.value, true)} /></label>
          <label>Exact source value meaning “exclude”<input value={excludedValue} placeholder="For example, Y" onChange={event => setFlag(event.target.value, false)} /></label>
        </div>}
        <label>Source note supporting these meanings<textarea value={note} maxLength={1500} placeholder="Record the source instructions or clarification used to interpret these fields." onChange={event => change({ evidence_refs: event.target.value ? ["analyst-source-note"] : [], source_notes: event.target.value ? { "analyst-source-note": event.target.value } : {} })} /></label>
        <details><summary>Source-local requirements and conditions</summary>
          <p>Use requirements documented for this source. Base parser and downstream requirements remain enforced.</p>
          {fields.map(field => <label className="source-schema-check" key={field}><input type="checkbox" disabled={field === "practitioner_npi"} checked={field === "practitioner_npi" || definition.required.includes(field)} onChange={event => change({ required: event.target.checked ? [...new Set([...definition.required, field])] : definition.required.filter(item => item !== field) })} />Source requires {fieldLabel(field)}</label>)}
          {definition.required_when.map((rule, index) => <p key={index}>Require {rule.require.map(fieldLabel).join(", ")} when {fieldLabel(rule.field)} equals “{rule.equals}”. <button type="button" onClick={() => change({ required_when: definition.required_when.filter((_, i) => i !== index) })}>Remove condition {index + 1}</button></p>)}
          <label>Condition classifier<select value={conditionField} onChange={event => setConditionField(event.target.value)}><option value="">Choose a mapped text field…</option>{Object.keys(definition.mapping).filter(field => field !== "directory_visible").map(field => <option key={field} value={field}>{fieldLabel(field)}</option>)}</select></label>
          <label>Exact interpreted value<input value={conditionValue} maxLength={256} onChange={event => setConditionValue(event.target.value)} /></label>
          <label>Then require<select value={conditionTarget} onChange={event => setConditionTarget(event.target.value)}><option value="">Choose a field…</option>{profile.fields.map(field => <option key={field} value={field}>{fieldLabel(field)}</option>)}</select></label>
          <button type="button" disabled={!conditionField || !conditionValue || !conditionTarget || definition.required_when.length >= 16} onClick={() => { change({ required_when: [...definition.required_when, { field: conditionField, equals: conditionValue, require: [conditionTarget] }] }); setConditionValue(""); }}>Add source condition</button>
        </details>
        <button type="button" onClick={() => void run(async () => { const result = await request<Preview>("/preview", { expected_attempt: attemptId, context, definition, expected_catalog_revision: profile.catalog_revision }); setPreview(result); setNotice(""); })}>Preview source interpretation</button>
        {profile.pending.map(proposal => <button key={proposal.id} type="button" onClick={() => void run(() => loadPending(proposal.id))}>Inspect {proposal.origin === "mcp" ? "agent" : "pending"} proposal {proposal.id.slice(0, 8)}</button>)}
      </fieldset>}
      {preview && profile && definition && <div>
        <p role="status">Coverage: {preview.report.coverage.replaceAll("_", " ")} · Dataset use: {preview.report.eligibility.state.replaceAll("_", " ")}. No dataset acceptance has occurred.</p>
        <ul>{Object.entries(preview.report.findings.counts).map(([code, count]) => <li key={code}>{code.replaceAll("_", " ")}: {count}</li>)}</ul>
        <div className="source-schema-scroll"><table><caption>Interpreted preview — {preview.report.rows.length} of {preview.report.row_count} records{preview.report.samples_clipped ? "; long samples clipped" : ""}</caption>
          <thead><tr><th>Source record</th>{Object.keys(definition.mapping).map(field => <th key={field}>{fieldLabel(field)}</th>)}</tr></thead>
          <tbody>{preview.report.rows.map(row => <tr key={row.locator}><th>{row.locator}</th>{Object.keys(definition.mapping).map(field => <td key={field}>{row.normalized[field] === null || row.normalized[field] === undefined ? "Unresolved" : String(row.normalized[field])}</td>)}</tr>)}</tbody>
        </table></div>
        <fieldset disabled={busy || needsInspection}>
          <legend>Record a reviewed interpretation for future deliveries</legend>
          <label>Reviewer<input value={actor} onChange={event => setActor(event.target.value)} /></label>
          <label>Review reason<textarea value={reason} maxLength={2000} onChange={event => setReason(event.target.value)} /></label>
          <label>Replace an earlier interpretation (optional)<select value={supersedes} onChange={event => setSupersedes(event.target.value)}><option value="">Keep earlier interpretations available</option>{(profile.variants || []).map(variant => <option key={variant.id} value={variant.id}>{variant.id.slice(0, 8)} — source version {variant.definition.source_version || "not declared"} — {variant.definition.grain.replaceAll("_", " ")}</option>)}</select></label>
          <p>Replace an earlier interpretation only when it is incorrect or retired. Its history remains available.</p>
          {!saved ? <button type="button" onClick={() => void run(async () => { const result = await request<{ id: string }>("/proposals", { expected_attempt: attemptId, context, definition, expected_catalog_revision: profile.catalog_revision, preview_token: preview.preview_token, request_id: crypto.randomUUID() }); setSaved(result.id); setNotice("Proposal saved. Recording the source review is a separate step."); })}>Save format proposal</button>
            : <button type="button" disabled={!actor.trim() || !reason.trim()} onClick={() => void run(async () => { await request(`/proposals/${saved}/review`, { expected_attempt: attemptId, actor, reason, expected_catalog_revision: profile.catalog_revision, preview_token: preview.preview_token, supersedes: supersedes || null }); await inspect(); setNotice("Reviewed source interpretation recorded. Future compatible deliveries can reuse it; dataset approval remains separate."); })}>Record reviewed interpretation</button>}
        </fieldset>
      </div>}
    </div>}
  </section>;
}
