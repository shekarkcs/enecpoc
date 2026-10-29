'use client';
import { useState } from 'react';
import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from '@tanstack/react-query';
import { FluentProvider, webLightTheme, Button } from '@fluentui/react-components';

type Match = {id:string; txn_ids:string[]; sap_id:string; method:string; status:string; reason:string; confidence:number};
type Exception = {txn_id:string; category:string; reason:string; status:string};
type JV = {id:string; status:string; prepared_by:string; sap_doc_no:string|null; error:string|null; payload:{reason:string; lines:{gl_account:string; cost_center:string|null; amount:string}[]; validation:unknown}};
type Batch = {id:string; filename:string; status:string; error:string|null; payload:{transactions:{id:string; merchant:string; amount:string; currency:string; reference:string}[]; matches?:Match[]; exceptions?:Exception[]}; jvs:JV[]};
async function api<T>(path:string, init?:RequestInit): Promise<T> {
  const r = await fetch('/api/proxy/' + path, init);
  const text = await r.text();
  if (!r.ok) throw new Error(text);
  return JSON.parse(text) as T;
}
const client = new QueryClient();
export default function Page() { return <QueryClientProvider client={client}><FluentProvider theme={webLightTheme}><Worklist/></FluentProvider></QueryClientProvider>; }
function Worklist() {
  const cache = useQueryClient();
  const [section, setSection] = useState('Batches');
  const [batchId, setBatchId] = useState('');
  const [tab, setTab] = useState('Exceptions');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const batches = useQuery({queryKey:['batches'], queryFn:()=>api<Batch[]>('batches'), refetchInterval:3000});
  const batch = useQuery({queryKey:['batch', batchId], queryFn:()=>api<Batch>('batches/'+batchId+'/results'), enabled:!!batchId, refetchInterval:3000});
  const audit = useQuery({queryKey:['audit'], queryFn:()=>api<Record<string,string>[]>('audit'), enabled:section==='Audit'});
  const providers = useQuery({queryKey:['providers'], queryFn:()=>api<Record<string,string>[]>('admin/llm-providers'), enabled:section==='Models'});
  const rules = useQuery({queryKey:['rules'], queryFn:()=>api<Record<string,string>>('admin/rules'), enabled:section==='Rules'});
  async function act(path:string, body?:unknown) {
    setBusy(true); setMessage('');
    try { await api(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body??{})}); await cache.invalidateQueries(); setMessage('Saved'); }
    catch(e) {setMessage(String(e));} finally {setBusy(false);}
  }
  async function upload(e:React.FormEvent<HTMLFormElement>) {
    e.preventDefault(); setBusy(true);
    const data = new FormData(e.currentTarget);
    try {const result=await api<{batch_id:string}>('statements',{method:'POST',body:data});setBatchId(result.batch_id);await cache.invalidateQueries();setMessage('Uploaded; worker is processing the statement');}
    catch(e) {setMessage(String(e));} finally {setBusy(false);}
  }
  const b = batch.data;
  return <main><header><div><small>FINANCE OPERATIONS</small><h1>Credit card reconciliation</h1></div><a href="/api/auth/login">Sign in with Microsoft</a></header>
    <nav aria-label="Main navigation">{['Batches','Audit','Models','Rules'].map(s=><Button key={s} appearance={section===s?'primary':'subtle'} onClick={()=>setSection(s)}>{s}</Button>)}</nav>
    <p role="status" className="message">{message || String(batches.error || '')}</p>
    {section==='Batches' && <><section><h2>Upload statement</h2><form onSubmit={upload}><label>CSV statement<input required name="file" type="file" accept=".csv"/></label><label>Statement total (AED)<input required name="total_aed" type="number" step="0.01" defaultValue="350.00"/></label><Button type="submit" appearance="primary" disabled={busy}>Upload</Button></form></section>
      <div className="columns"><aside><h2>Batches</h2>{batches.data?.map(x=><button className={'batch '+(batchId===x.id?'selected':'')} key={x.id} onClick={()=>setBatchId(x.id)}><strong>{x.filename}</strong><span className="badge">{x.status}</span><small>{x.id}</small></button>)}</aside>
      <section className="review"><h2>{b?.filename || 'Select a batch'}</h2>{b && <><p>Status: <strong>{b.status}</strong> {b.error}</p><div className="tabs">{['Auto-matched','AI suggestions','Exceptions','Journal entries'].map(t=><Button key={t} onClick={()=>setTab(t)} appearance={tab===t?'primary':'subtle'}>{t}</Button>)}</div>
      {(tab==='Auto-matched'||tab==='AI suggestions') && <table><thead><tr><th>Transaction</th><th>SAP item</th><th>Confidence / reason</th><th>Status</th><th>Decision</th></tr></thead><tbody>{b.payload.matches?.filter(m=>m.method===(tab==='Auto-matched'?'rule':'ai')).map(m=><tr key={m.id}><td>{m.txn_ids.join(', ')}</td><td>{m.sap_id}</td><td>{Math.round(m.confidence*100)}% · {m.reason}</td><td>{m.status}</td><td>{m.status==='suggested' && <><Button disabled={busy} onClick={()=>act(`batches/${b.id}/matches/${m.id}/decision`,{decision:'approve'})}>Accept</Button><Button disabled={busy} onClick={()=>{const comment=prompt('Reason for rejecting');if(comment)void act(`batches/${b.id}/matches/${m.id}/decision`,{decision:'reject',comment});}}>Reject</Button></>}</td></tr>)}</tbody></table>}
      {tab==='Exceptions' && <>{b.payload.exceptions?.length===0 && <p>No exceptions.</p>}{b.payload.exceptions?.map(e=>{const t=b.payload.transactions.find(t=>t.id===e.txn_id);return <article key={e.txn_id}><h3>{t?.merchant} <span>{t?.amount} {t?.currency}</span></h3><p>{e.category} · {e.status}</p><p>{e.reason}</p><Button disabled={busy||e.status==='resolved'} onClick={()=>{const comment=prompt('Resolution comment');if(comment)void act(`batches/${b.id}/exceptions/${e.txn_id}/resolve`,{comment});}}>Resolve</Button><Button disabled={busy||e.status==='resolved'} onClick={()=>{const value=prompt('Balanced JV lines as JSON',JSON.stringify([{gl_account:'400100',cost_center:'CC100',amount:'200.00',currency:'AED'},{gl_account:'210000',amount:'-200.00',currency:'AED'}]));if(value){try{void act(`batches/${b.id}/jvs`,{txn_ids:[e.txn_id],reason:'Finance adjustment',lines:JSON.parse(value)});}catch{setMessage('Invalid JSON');}}}}>Prepare JV</Button></article>;})}</>}
      {tab==='Journal entries' && <>{b.jvs.map(j=><article key={j.id}><h3>JV {j.id} · {j.status}</h3><p>Prepared by {j.prepared_by} · {j.payload.reason}</p><table><thead><tr><th>GL account</th><th>Cost centre</th><th>Signed amount (AED)</th></tr></thead><tbody>{j.payload.lines.map((l,i)=><tr key={i}><td>{l.gl_account}</td><td>{l.cost_center}</td><td>{l.amount}</td></tr>)}</tbody></table><p>SAP validation: {JSON.stringify(j.payload.validation)}</p><p>{j.error || j.sap_doc_no}</p>{j.status==='validated' && <><Button disabled={busy} onClick={()=>act(`jv/${j.id}/decision`,{decision:'approve'})}>Approve</Button><Button disabled={busy} onClick={()=>{const comment=prompt('Reason for rejection');if(comment)void act(`jv/${j.id}/decision`,{decision:'reject',comment});}}>Reject</Button></>}{j.status==='approved'&&<Button disabled={busy} onClick={()=>{if(confirm('Post this approved JV to SAP?'))void act(`jv/${j.id}/post`);}}>Post approved JV</Button>}{j.status==='posting'&&<Button disabled={busy} onClick={()=>act(`jv/${j.id}/recover`)}>Check SAP posting</Button>}</article>)}</>}
      {b.status==='review'&&<Button disabled={busy} onClick={()=>act(`batches/${b.id}/close`)}>Close batch</Button>}</>}</section></div></>}
    {section==='Audit'&&<section><h2>Audit history</h2><p>{String(audit.error||'')}</p><table><thead><tr><th>Time</th><th>Actor</th><th>Action</th><th>Entity</th></tr></thead><tbody>{audit.data?.map(a=><tr key={a.id}><td>{a.at}</td><td>{a.actor}</td><td>{a.action}</td><td>{a.entity_id}</td></tr>)}</tbody></table></section>}
    {section==='Models'&&<section><h2>Governed models</h2><p>Providers require an operator-run labelled evaluation and approval by a second admin. Model endpoints must be allowlisted by the operator.</p><p>{String(providers.error||'')}</p>{providers.data?.map(p=><article key={p.id}><h3>{p.id} · {p.status}</h3><pre>{p.payload}</pre><p>Accuracy: {p.accuracy??'Not evaluated'}</p><Button disabled={busy} onClick={()=>act(`admin/llm-providers/${p.id}/test`)}>Test connection</Button><Button disabled={busy} onClick={()=>act(`admin/llm-providers/${p.id}/approve`)}>Second-admin approval</Button></article>)}<Button onClick={()=>{const s=prompt('Provider configuration JSON');if(s){try{void act('admin/llm-providers',JSON.parse(s));}catch{setMessage('Invalid JSON');}}}}>Add provider</Button><p>Use the API for governed task assignment requests.</p></section>}
    {section==='Rules'&&<section><h2>Matching and approval rules</h2><p>{String(rules.error||'')}</p><pre>{JSON.stringify(rules.data,null,2)}</pre><Button disabled={busy} onClick={async()=>{const value=prompt('Rules JSON',JSON.stringify(rules.data));if(!value)return;try{await api('admin/rules',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(JSON.parse(value))});await cache.invalidateQueries();setMessage('Rules updated');}catch(e){setMessage(String(e));}}}>Edit rules</Button></section>}
  </main>;
}
