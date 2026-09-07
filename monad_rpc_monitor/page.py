"""The status page. Plain HTML + a little vanilla JS; renders /api/v1/status.json client-side
so the same file works both from the live server and as a static export."""

STATUS_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Monad RPC Monitor</title>
<style>
:root{--bg:#0f1116;--panel:#171a21;--line:#262a33;--fg:#e6e6e6;--muted:#8b93a1;
 --ok:#2ecc71;--slow:#f1c40f;--lag:#e67e22;--rl:#3498db;--bad:#e74c3c;--dim:#4b5160}
@media (prefers-color-scheme: light){:root{--bg:#f6f7f9;--panel:#fff;--line:#e2e5ea;--fg:#1c1f26;--muted:#5b6370;--dim:#c9ced8}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
a{color:inherit}main{max-width:1180px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:20px;margin:0 0 4px}h2{font-size:16px;margin:32px 0 10px}.sub{color:var(--muted);margin-bottom:20px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin:12px 0 4px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px 14px}
.card b{display:block;font-size:22px;margin-top:2px}.card small{color:var(--muted)}
.wrap{overflow-x:auto;background:var(--panel);border:1px solid var(--line);border-radius:8px}
table{border-collapse:collapse;width:100%;min-width:900px}th,td{padding:8px 10px;text-align:left;border-top:1px solid var(--line);white-space:nowrap}
th{color:var(--muted);font-weight:500;font-size:12px;text-transform:uppercase;letter-spacing:.03em;border-top:0}
tr.ep{cursor:pointer}tr.ep:hover td{background:rgba(127,127,127,.06)}
.pill{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;font-weight:600;color:#000}
.s-healthy{background:var(--ok)}.s-slow{background:var(--slow)}.s-lagging{background:var(--lag)}
.s-rate_limited{background:var(--rl)}.s-degraded,.s-wrong_chain,.s-down{background:var(--bad);color:#fff}
.tl{display:inline-flex;gap:1px;height:16px;vertical-align:middle}.tl i{display:block;width:5px;height:100%;background:var(--dim);border-radius:1px}
.tl i.healthy{background:var(--ok)}.tl i.slow{background:var(--slow)}.tl i.lagging{background:var(--lag)}.tl i.rate_limited{background:var(--rl)}
.tl i.degraded,.tl i.wrong_chain,.tl i.down{background:var(--bad)}
.num{font-variant-numeric:tabular-nums}.muted{color:var(--muted)}.err{color:var(--bad);max-width:340px;white-space:normal;font-size:12px}
.detail td{white-space:normal;background:rgba(127,127,127,.04)}
.methods{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:4px 16px;font-size:12px}
.methods span::before{content:"✓ ";color:var(--ok)}.methods span.no::before{content:"✗ ";color:var(--bad)}.methods span.unk::before{content:"? ";color:var(--muted)}
.foot{margin-top:28px;color:var(--muted);font-size:12px}.foot a{color:var(--muted)}
.legend span{margin-right:12px}
</style>
</head>
<body><main>
<h1>Monad RPC Monitor</h1>
<div class="sub" id="sub">Loading…</div>
<div id="root"></div>
<h2>Recent incidents</h2>
<div class="wrap"><table id="incidents"><thead><tr><th>Network</th><th>Endpoint</th><th>Status</th><th>Started</th><th>Duration</th><th>Detail</th></tr></thead><tbody></tbody></table></div>
<div class="foot">
<div class="legend"><span class="pill s-healthy">healthy</span><span class="pill s-slow">slow</span><span class="pill s-lagging">lagging</span><span class="pill s-rate_limited">rate limited</span><span class="pill s-degraded">degraded / down</span></div>
<p id="thr"></p>
<p>Data: <a href="api/v1/status.json">api/v1/status.json</a> · Prometheus: <a href="metrics">/metrics</a> · Source: <a href="https://github.com/Gabbe-x/monad-rpc-monitor">github.com/Gabbe-x/monad-rpc-monitor</a></p>
</div>
</main>
<script>
const DATA_URL = 'api/v1/status.json';
const fmt = (v, d=0) => v == null ? '–' : Number(v).toFixed(d);
const ago = s => { if (s == null) return '–'; s = Math.max(0, s|0); if (s < 60) return s + 's'; if (s < 3600) return (s/60|0) + 'm'; if (s < 86400) return (s/3600|0) + 'h ' + ((s%3600)/60|0) + 'm'; return (s/86400|0) + 'd ' + ((s%86400)/3600|0) + 'h'; };
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const pill = st => `<span class="pill s-${st}">${st.replace('_',' ')}</span>`;
const tl = t => `<span class="tl" title="${t.length} buckets">` + t.map(b => `<i class="${b.status||''}" title="${b.status||'no data'}${b.latency_ms!=null?' · '+fmt(b.latency_ms)+' ms':''}"></i>`).join('') + '</span>';
function methods(m, probes){ if(!m) return '<span class="muted">method probe not run yet</span>';
  return '<div class="methods">' + probes.map(p => { const r = m[p.key]; const cls = !r || r.supported === null ? 'unk' : (r.supported ? '' : 'no');
    return `<span class="${cls}" title="${esc(r && r.error || '')}">${esc(p.key)} <small class="muted">${r && r.latency_ms!=null ? fmt(r.latency_ms)+' ms' : ''}</small></span>`; }).join('') + '</div>'; }
function render(d){
  const now = d.generated_at; document.getElementById('sub').textContent = `Updated ${new Date(now*1000).toLocaleString()} · probe every ${d.interval_s}s · ${d.window_h}h window · v${d.version}`;
  document.getElementById('thr').textContent = `Thresholds: lagging > ${d.thresholds.lag_blocks} blocks behind best head · slow > ${d.thresholds.slow_ms} ms · consistency checked at best head − ${d.thresholds.consistency_depth}.`;
  let html = '';
  for (const [key, net] of Object.entries(d.networks)) {
    const eps = d.endpoints.filter(e => e.network === key).sort((a,b) => (b.uptime_pct??-1)-(a.uptime_pct??-1) || (a.latency_ms??1e9)-(b.latency_ms??1e9));
    html += `<h2>${esc(net.display)} <small class="muted">chain ${net.chain_id}</small></h2>
    <div class="cards">
      <div class="card"><small>Best head</small><b class="num">${net.best_block ?? '–'}</b></div>
      <div class="card"><small>Healthy endpoints</small><b class="num">${net.endpoints_healthy} / ${net.endpoints_total}</b></div>
      <div class="card"><small>Consistency @ ${net.consistency_block ?? '–'}</small><b>${net.canonical_hash ? (net.mismatching.length ? '⚠ '+net.mismatching.length+' mismatch' : '✓ all agree') : '–'}</b>${net.canonical_hash ? `<small class="num">${net.canonical_hash.slice(0,18)}…</small>` : ''}</div>
    </div>
    <div class="wrap"><table><thead><tr><th>Endpoint</th><th>Status</th><th>Latency</th><th>Head</th><th>Lag</th><th>Uptime ${d.window_h}h</th><th>p50 / p95</th><th>Timeline</th><th>Client</th></tr></thead><tbody>`;
    for (const e of eps) {
      html += `<tr class="ep" data-ep="${esc(e.endpoint)}"><td><b>${esc(e.endpoint)}</b><br><small class="muted">${esc(e.provider)} · ${esc(e.url)}</small></td>
        <td>${pill(e.status)}${e.consistency==='mismatch'?' ⚠':''}</td><td class="num">${fmt(e.latency_ms)} ms</td><td class="num">${e.block ?? '–'}</td>
        <td class="num">${e.lag ?? '–'}</td><td class="num">${fmt(e.uptime_pct,2)}%</td><td class="num">${fmt(e.p50_ms)} / ${fmt(e.p95_ms)}</td><td>${tl(e.timeline)}</td><td class="muted">${esc(e.client || '')}</td></tr>
        <tr class="detail" hidden><td colspan="9">${e.error ? `<div class="err">${esc(e.error)}</div>` : ''}
          <div class="muted" style="margin:4px 0 8px">Consistency: ${e.consistency ?? 'not checked'}${e.consistency_hash ? ' · block '+e.consistency_block+' · '+e.consistency_hash : ''} · Samples in window: ${e.samples}${e.methods_ts ? ' · Method probe '+ago(now-e.methods_ts)+' ago' : ''}</div>
          ${methods(e.methods, d.method_probes)}</td></tr>`;
    }
    html += '</tbody></table></div>';
  }
  document.getElementById('root').innerHTML = html;
  document.querySelectorAll('tr.ep').forEach(r => r.addEventListener('click', () => { const n = r.nextElementSibling; n.hidden = !n.hidden; }));
  const tb = document.querySelector('#incidents tbody');
  tb.innerHTML = d.incidents.length ? d.incidents.map(i => `<tr><td>${esc(i.network)}</td><td>${esc(i.endpoint)}</td><td>${pill(i.status)}</td><td>${new Date(i.started*1000).toLocaleString()}</td><td class="num">${i.ended ? ago(i.ended-i.started) : 'ongoing ('+ago(now-i.started)+')'}</td><td class="err">${esc(i.detail||'')}</td></tr>`).join('')
    : '<tr><td colspan="6" class="muted">No incidents in the retained window.</td></tr>';
}
async function load(){ try { const r = await fetch(DATA_URL, {cache:'no-store'}); render(await r.json()); } catch (e) { document.getElementById('sub').textContent = 'Failed to load status: ' + e; } }
load(); setInterval(load, 30000);
</script>
</body></html>
"""
