// LLM Benchmark Dashboard - app.js
let presets={},selectedPreset=null,running=false,sortCol='created_at',sortDir='desc',allResults=[],chartInstances={};
const $=s=>document.getElementById(s);
const esc=s=>{const d=document.createElement('div');d.textContent=s;return d.innerHTML};
const fmt=d=>d?new Date(d).toLocaleString():'—';
const fmtMs=v=>v!=null?v.toFixed(0)+' ms':'—';
const fmtSec=v=>v!=null?(v/1000).toFixed(2)+' s':'—';
const fmtNum=v=>v!=null&&v!==''?Number(v).toLocaleString():'—';
const fmtDec=(v,d=1)=>v!=null?v.toFixed(d):'—';
const api=(path,opts={})=>fetch(path,{headers:{'Content-Type':'application/json'},...opts}).then(async r=>{if(!r.ok)throw new Error(`${r.status}: ${await r.text()}`);return r.json()});

(async()=>{await loadPresets();await loadEndpoints()})();

function switchTab(name){
  document.querySelectorAll('.tab-panel').forEach(p=>p.classList.add('hidden'));
  document.querySelectorAll('[id^="tab-"]').forEach(t=>{t.classList.remove('tab-active');t.classList.add('tab-inactive')});
  const p=$('panel-'+name),t=$('tab-'+name);
  if(p)p.classList.remove('hidden');if(t){t.classList.remove('tab-inactive');t.classList.add('tab-active')}
  if(name==='history')loadHistory();if(name==='charts')loadCharts();
}

async function loadPresets(){
  presets=await api('/api/presets');
  const list=$('presetList'),sel=$('selPreset');
  list.innerHTML='';sel.innerHTML='<option value="">— select preset —</option>';
  for(const[k,p]of Object.entries(presets)){
    const btn=document.createElement('button');
    btn.className='w-full text-left px-3 py-2 rounded-lg text-sm hover:bg-surface-300 transition-colors preset-btn';
    btn.dataset.key=k;btn.innerHTML=`<div class="font-medium">${p.name}</div><div class="text-xs text-gray-500 truncate">${p.description}</div>`;
    btn.onclick=()=>selectPreset(k);list.appendChild(btn);
    const opt=document.createElement('option');opt.value=k;opt.textContent=p.name;sel.appendChild(opt);
  }
  sel.onchange=()=>selectPreset(sel.value);
}
function selectPreset(key){
  selectedPreset=key;
  document.querySelectorAll('.preset-btn').forEach(b=>{b.classList.toggle('bg-surface-300',b.dataset.key===key);b.classList.toggle('ring-1 ring-cyan-500',b.dataset.key===key)});
  $('selPreset').value=key;checkReady();
}

async function loadEndpoints(){
  const eps=await api('/api/endpoints');
  const list=$('endpointList'),sel=$('selEndpoint');
  list.innerHTML='';sel.innerHTML='<option value="">— select endpoint —</option>';
  for(const ep of eps){
    const div=document.createElement('div');
    div.className='group flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm hover:bg-surface-300 cursor-pointer';
    div.innerHTML=`<span class="flex-1 truncate" onclick="selEndpoint('${ep.id}')">${esc(ep.name)}</span>
      <button onclick="editEndpoint('${ep.id}')" class="hidden group-hover:inline text-gray-500 hover:text-gray-300 text-xs">✏️</button>
      <button onclick="removeEndpoint('${ep.id}')" class="hidden group-hover:inline text-red-500 hover:text-red-400 text-xs">🗑️</button>`;
    list.appendChild(div);
    const opt=document.createElement('option');opt.value=ep.id;opt.textContent=ep.name;sel.appendChild(opt);
  }
}
function selEndpoint(id){$('selEndpoint').value=id;onEndpointChange()}
function onEndpointChange(){$('selModel').innerHTML='<option value="">— select model —</option>';loadModels();checkReady()}

function showEndpointModal(id){
  $('endpointModal').classList.remove('hidden');
  $('modalTitle').textContent=id?'Edit Endpoint':'Add Endpoint';$('epId').value=id||'';
  if(!id){$('epName').value='';$('epUrl').value='';$('epKey').value='';$('epHeaders').value='{}'}
}
async function saveEndpoint(){
  const id=$('epId').value,body={name:$('epName').value,base_url:$('epUrl').value,api_key:$('epKey').value,extra_headers:$('epHeaders').value};
  if(id){body.id=id;await api('/api/endpoints/'+id,{method:'PUT',body:JSON.stringify(body)})}
  else await api('/api/endpoints',{method:'POST',body:JSON.stringify(body)});
  $('endpointModal').classList.add('hidden');await loadEndpoints();
}
async function editEndpoint(id){
  const ep=(await api('/api/endpoints')).find(e=>e.id===id);if(!ep)return;
  $('epId').value=ep.id;$('epName').value=ep.name;$('epUrl').value=ep.base_url;$('epKey').value=ep.api_key;$('epHeaders').value=ep.extra_headers;
  showEndpointModal(id);
}
async function removeEndpoint(id){if(!confirm('Delete this endpoint?'))return;await api('/api/endpoints/'+id,{method:'DELETE'});await loadEndpoints()}

async function loadModels(){
  const epId=$('selEndpoint').value,sel=$('selModel');
  sel.innerHTML='<option value="">— loading… —</option>';
  try{
    const models=await api('/api/models?endpoint_id='+epId);
    sel.innerHTML='<option value="">— select model —</option>';
    models.forEach(m=>{const o=document.createElement('option');o.value=m.id;o.textContent=m.id;sel.appendChild(o)});
  }catch(e){sel.innerHTML=`<option value="">⚠ ${esc(e.message)}</option>`}
  sel.onchange=checkReady;checkReady();
}
function checkReady(){
  const r=$('selEndpoint').value&&$('selModel').value&&$('selPreset').value;
  $('btnRun').disabled=!r||running;
}

async function runBenchmark(){
  if(running)return;
  const epId=$('selEndpoint').value,model=$('selModel').value,preset=$('selPreset').value;
  if(!epId||!model||!preset)return;
  running=true;$('btnRun').disabled=true;$('btnRun').textContent='Running…';
  switchTab('result');
  $('panel-result').innerHTML='<div class="text-center py-16"><div class="inline-block w-8 h-8 border-4 border-cyan-500 border-t-transparent rounded-full animate-spin mb-3"></div><p class="text-sm text-gray-400">Running benchmark on <strong>'+esc(model)+'</strong>…</p></div>';
  try{
    const result=await api('/api/run',{method:'POST',body:JSON.stringify({endpoint_id:epId,model,preset,max_tokens:parseInt($('inpMaxTokens').value)||2048,temperature:parseFloat($('inpTemp').value)||0.7})});
    renderResult(result);
  }catch(e){$('panel-result').innerHTML='<div class="text-center py-16 text-red-400"><p>Error: '+esc(e.message)+'</p></div>'}
  finally{running=false;checkReady();$('btnRun').textContent='Run Benchmark'}
}

function renderResult(r){
  const area=$('panel-result'),date=fmt(r.created_at);
  const stats=[{l:'TTFT',v:fmtMs(r.time_to_first_token_ms),i:'⚡'},{l:'Total Time',v:fmtSec(r.total_time_ms),i:'⏱️'},{l:'Tok/s',v:fmtDec(r.tokens_per_second),i:'📊'},{l:'Tokens',v:fmtNum(r.completion_tokens),i:'🔢'},{l:'Output',v:(r.output_length||0).toLocaleString()+' ch',i:'📏'}];
  area.innerHTML=`<div class="max-w-5xl mx-auto space-y-4 fade-in">
    <div><h2 class="text-lg font-semibold">${esc(r.endpoint_name)} / ${esc(r.model)}</h2><p class="text-xs text-gray-500">${esc(r.preset_name)} · ${date}</p></div>
    <div class="grid grid-cols-2 md:grid-cols-5 gap-3">${stats.map(s=>`<div class="bg-surface-200 border border-surface-300 rounded-xl p-3 text-center"><div class="text-lg mb-1">${s.i}</div><div class="text-lg font-bold text-cyan-400">${s.v}</div><div class="text-xs text-gray-500">${s.l}</div></div>`).join('')}</div>
    <div class="bg-surface-200 border border-surface-300 rounded-xl p-4"><h3 class="text-xs font-semibold text-gray-400 uppercase mb-2">Prompt</h3><p class="text-sm text-gray-300 whitespace-pre-wrap">${esc(r.prompt)}</p></div>
    <div class="bg-surface-200 border border-surface-300 rounded-xl p-4"><h3 class="text-xs font-semibold text-gray-400 uppercase mb-2">Response</h3><div class="text-sm text-gray-200 whitespace-pre-wrap leading-relaxed">${esc(r.response)}</div></div>
  </div>`;
}

// History
async function loadHistory(){
  const params=new URLSearchParams();
  const fm=$('filterModel')?.value,fp=$('filterPreset')?.value,fd=$('filterFrom')?.value,td=$('filterTo')?.value;
  if(fm)params.set('model',fm);if(fp)params.set('preset',fp);if(fd)params.set('from_date',fd);if(td)params.set('to_date',td);
  allResults=await api('/api/results?'+params.toString());
  populateFilters();renderHistoryTable();
}
function populateFilters(){
  let fm=$('filterModel');if(!fm)return;
  const current=fm.value;
  const models=[...new Set(allResults.map(r=>r.model))].sort();
  fm.innerHTML='<option value="">All Models</option>'+models.map(m=>`<option ${m===current?'selected':''}>${esc(m)}</option>`).join('');
  let fp=$('filterPreset');if(!fp)return;
  const cp=fp.value;
  const prs=[...new Set(allResults.map(r=>r.preset_name))].sort();
  fp.innerHTML='<option value="">All Presets</option>'+prs.map(p=>`<option ${p===cp?'selected':''}>${esc(p)}</option>`).join('');
}
function renderHistoryTable(){
  const panel=$('panel-history');
  const sorted=[...allResults].sort((a,b)=>{
    let va=a[sortCol],vb=b[sortCol];
    if(sortCol==='created_at')return sortDir==='asc'?va.localeCompare(vb):vb.localeCompare(va);
    va=Number(va)||0;vb=Number(vb)||0;return sortDir==='asc'?va-vb:vb-va;
  });
  const colLabels={created_at:'Date',endpoint_name:'Endpoint',model:'Model',preset_name:'Preset',total_time_ms:'Latency',time_to_first_token_ms:'TTFT',tokens_per_second:'Tok/s',completion_tokens:'Tokens',output_length:'Length'};
  const leftCols=['created_at','endpoint_name','model','preset_name'];
  panel.innerHTML=`<div class="max-w-7xl mx-auto space-y-4 fade-in">
    <div class="flex items-center justify-between">
      <h2 class="text-lg font-semibold">Benchmark History (${allResults.length} runs)</h2>
      <div class="flex gap-2">
        <button onclick="loadHistory()" class="px-3 py-1.5 text-xs bg-surface-200 hover:bg-surface-300 border border-surface-400 rounded-lg">↻ Refresh</button>
        <button onclick="clearAllResults()" class="px-3 py-1.5 text-xs bg-red-900/30 hover:bg-red-900/50 border border-red-800/50 text-red-400 rounded-lg">Clear All</button>
      </div>
    </div>
    <div class="flex flex-wrap gap-3">
      <select id="filterModel" onchange="loadHistory()" class="bg-surface-200 border border-surface-400 rounded-lg px-3 py-1.5 text-xs"><option value="">All Models</option></select>
      <select id="filterPreset" onchange="loadHistory()" class="bg-surface-200 border border-surface-400 rounded-lg px-3 py-1.5 text-xs"><option value="">All Presets</option></select>
      <input type="date" id="filterFrom" onchange="loadHistory()" class="bg-surface-200 border border-surface-400 rounded-lg px-3 py-1.5 text-xs">
      <input type="date" id="filterTo" onchange="loadHistory()" class="bg-surface-200 border border-surface-400 rounded-lg px-3 py-1.5 text-xs">
    </div>
    <div class="bg-surface-200 border border-surface-300 rounded-xl overflow-hidden">
      <div class="overflow-x-auto"><table class="sortable w-full text-sm">
        <thead><tr class="border-b border-surface-300 bg-surface/80">
          ${Object.keys(colLabels).map(c=>`<th class="text-${leftCols.includes(c)?'left':'right'} px-4 py-2 text-xs font-semibold text-gray-400 uppercase" onclick="handleSort('${c}')">${colLabels[c]}${sortCol===c?(sortDir==='asc'?' ▲':' ▼'):''}</th>`).join('')}
          <th class="px-4 py-2"></th>
        </tr></thead>
        <tbody class="divide-y divide-surface-300/50">
          ${sorted.length?sorted.map(r=>`<tr class="hover:bg-surface-300/50 transition-colors">
            <td class="px-4 py-2 text-xs text-gray-300">${fmt(r.created_at)}</td>
            <td class="px-4 py-2 text-xs">${esc(r.endpoint_name)}</td>
            <td class="px-4 py-2 text-xs font-medium">${esc(r.model)}</td>
            <td class="px-4 py-2 text-xs text-gray-400">${esc(r.preset_name)}</td>
            <td class="px-4 py-2 text-xs text-right">${fmtSec(r.total_time_ms)}</td>
            <td class="px-4 py-2 text-xs text-right">${fmtMs(r.time_to_first_token_ms)}</td>
            <td class="px-4 py-2 text-xs text-right text-cyan-400 font-medium">${fmtDec(r.tokens_per_second)}</td>
            <td class="px-4 py-2 text-xs text-right">${fmtNum(r.completion_tokens)}</td>
            <td class="px-4 py-2 text-xs text-right">${fmtNum(r.output_length)}</td>
            <td class="px-4 py-2 text-right whitespace-nowrap">
              <button onclick='showDetail("${r.id}")' class="text-cyan-400 hover:text-cyan-300 text-xs mr-2">View</button>
              <button onclick="deleteResult('${r.id}')" class="text-red-500 hover:text-red-400 text-xs">✕</button>
            </td>
          </tr>`).join(''):'<tr><td colspan="10" class="text-center py-12 text-gray-600 text-sm">No results yet. Run a benchmark!</td></tr>'}
        </tbody></table></div></div>
  </div>`;
}
function handleSort(col){if(sortCol===col)sortDir=sortDir==='asc'?'desc':'asc';else{sortCol=col;sortDir='desc'}renderHistoryTable()}
function showDetail(id){
  const r=allResults.find(x=>x.id===id);if(!r)return;
  $('detailTitle').textContent=r.endpoint_name+' / '+r.model;
  $('detailMeta').textContent=r.preset_name+' · '+fmt(r.created_at);
  const stats=[{l:'TTFT',v:fmtMs(r.time_to_first_token_ms)},{l:'Total',v:fmtSec(r.total_time_ms)},{l:'Tok/s',v:fmtDec(r.tokens_per_second)},{l:'Tokens',v:fmtNum(r.completion_tokens)},{l:'Output',v:(r.output_length||0).toLocaleString()+' ch'}];
  $('detailStats').innerHTML=stats.map(s=>`<div class="bg-surface rounded-lg p-2 text-center"><div class="text-base font-bold text-cyan-400">${s.v}</div><div class="text-xs text-gray-500">${s.l}</div></div>`).join('');
  $('detailPrompt').textContent=r.prompt;$('detailResponse').textContent=r.response;
  $('detailModal').classList.remove('hidden');
}
async function deleteResult(id){await api('/api/results/'+id,{method:'DELETE'});loadHistory()}
async function clearAllResults(){if(!confirm('Clear all benchmark results?'))return;await api('/api/results',{method:'DELETE'});loadHistory()}

// Charts
async function loadCharts(){
  const summary=await api('/api/summary');
  const latest=await api('/api/latest');
  renderCharts(summary,latest);
}
function renderCharts(summary,latest){
  const panel=$('panel-charts');
  const cards=[
    {l:'Total Runs',v:summary.total_runs||0,color:'text-cyan-400'},
    {l:'Avg Latency',v:summary.avg_latency_ms?summary.avg_latency_ms.toFixed(0)+' ms':'—',color:'text-blue-400'},
    {l:'Avg TTFT',v:summary.avg_ttfb_ms?summary.avg_ttfb_ms.toFixed(0)+' ms':'—',color:'text-green-400'},
    {l:'Avg Throughput',v:summary.avg_tps?summary.avg_tps.toFixed(1)+' tok/s':'—',color:'text-amber-400'},
  ];
  panel.innerHTML=`<div class="max-w-7xl mx-auto space-y-4 fade-in">
    <div class="grid grid-cols-2 md:grid-cols-4 gap-3">${cards.map(c=>`<div class="bg-surface-200 border border-surface-300 rounded-xl p-4 text-center"><div class="text-2xl font-bold ${c.color}">${c.v}</div><div class="text-xs text-gray-500 mt-1">${c.l}</div></div>`).join('')}</div>
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <div class="bg-surface-200 border border-surface-300 rounded-xl p-4"><h3 class="text-xs font-semibold text-gray-400 uppercase mb-3">Latency by Run (ms)</h3><canvas id="chartLatency" height="220"></canvas></div>
      <div class="bg-surface-200 border border-surface-300 rounded-xl p-4"><h3 class="text-xs font-semibold text-gray-400 uppercase mb-3">Tokens/sec by Model</h3><canvas id="chartThroughput" height="220"></canvas></div>
      <div class="bg-surface-200 border border-surface-300 rounded-xl p-4"><h3 class="text-xs font-semibold text-gray-400 uppercase mb-3">TTFT by Run (ms)</h3><canvas id="chartTTFT" height="220"></canvas></div>
      <div class="bg-surface-200 border border-surface-300 rounded-xl p-4"><h3 class="text-xs font-semibold text-gray-400 uppercase mb-3">Tokens by Preset</h3><canvas id="chartTokens" height="220"></canvas></div>
    </div>
    <div class="bg-surface-200 border border-surface-300 rounded-xl p-4"><h3 class="text-xs font-semibold text-gray-400 uppercase mb-3">Model Comparison</h3>
      <div class="overflow-x-auto"><table class="w-full text-sm"><thead><tr class="border-b border-surface-300">
        <th class="text-left px-3 py-2 text-xs font-semibold text-gray-400 uppercase">Model</th>
        <th class="text-right px-3 py-2 text-xs font-semibold text-gray-400 uppercase">Runs</th>
        <th class="text-right px-3 py-2 text-xs font-semibold text-gray-400 uppercase">Avg Latency</th>
        <th class="text-right px-3 py-2 text-xs font-semibold text-gray-400 uppercase">Avg TTFT</th>
        <th class="text-right px-3 py-2 text-xs font-semibold text-gray-400 uppercase">Avg Tok/s</th>
      </tr></thead><tbody id="modelStatsBody" class="divide-y divide-surface-300/50"></tbody></table></div></div>
  </div>`;

  Object.values(chartInstances).forEach(c=>c.destroy());chartInstances={};
  if(!latest?.length)return;
  const reversed=[...latest].reverse();
  const colors=['#06b6d4','#f59e0b','#ef4444','#22c55e','#8b5cf6','#ec4899','#3b82f6','#14b8a6'];
  const labels=reversed.map(r=>r.model.split('/').pop().substring(0,12));
  const baseOpts={responsive:true,maintainAspectRatio:false,plugins:{legend:{labels:{color:'#9ca3af',font:{size:11}}}},scales:{x:{ticks:{color:'#6b7280',font:{size:10}},grid:{color:'#1e293b'}},y:{ticks:{color:'#6b7280',font:{size:10}},grid:{color:'#1e293b'}}}};

  chartInstances.latency=new Chart(document.getElementById('chartLatency').getContext('2d'),{type:'bar',data:{labels,datasets:[{label:'Latency (ms)',data:reversed.map(r=>r.total_time_ms),backgroundColor:'rgba(6,182,212,0.6)',borderColor:'#06b6d4',borderWidth:1}]},options:baseOpts});

  // Group throughput by model
  const modelTps={};reversed.forEach(r=>{if(!modelTps[r.model])modelTps[r.model]=[];modelTps[r.model].push(r.tokens_per_second)});
  const tpsModels=Object.keys(modelTps),tpsAvgs=tpsModels.map(m=>modelTps[m].reduce((a,b)=>a+b,0)/modelTps[m].length);
  chartInstances.throughput=new Chart(document.getElementById('chartThroughput').getContext('2d'),{type:'bar',data:{labels:tpsModels.map(m=>m.split('/').pop().substring(0,16)),datasets:[{label:'Avg Tok/s',data:tpsAvgs,backgroundColor:tpsModels.map((_,i)=>colors[i%colors.length]),borderWidth:0}]},options:baseOpts});

  chartInstances.ttfb=new Chart(document.getElementById('chartTTFT').getContext('2d'),{type:'line',data:{labels,datasets:[{label:'TTFT (ms)',data:reversed.map(r=>r.time_to_first_token_ms||r.total_time_ms),borderColor:'#22c55e',backgroundColor:'rgba(34,197,94,0.1)',fill:true,tension:0.3,pointRadius:3,pointBackgroundColor:'#22c55e'}]},options:baseOpts});

  // Group tokens by preset
  const presetTokens={};reversed.forEach(r=>{if(!presetTokens[r.preset_name])presetTokens[r.preset_name]=[];presetTokens[r.preset_name].push(r.completion_tokens)});
  const tokPresets=Object.keys(presetTokens),tokAvgs=tokPresets.map(p=>presetTokens[p].reduce((a,b)=>a+b,0)/presetTokens[p].length);
  chartInstances.tokens=new Chart(document.getElementById('chartTokens').getContext('2d'),{type:'doughnut',data:{labels:tokPresets,datasets:[{data:tokAvgs,backgroundColor:tokPresets.map((_,i)=>colors[i%colors.length]),borderWidth:0}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{labels:{color:'#9ca3af',font:{size:11}},position:'bottom'}}}});

  // Model stats table
  const tbody=$('modelStatsBody');
  if(summary.model_stats?.length){
    tbody.innerHTML=summary.model_stats.map(s=>`<tr><td class="px-3 py-2 text-xs font-medium">${esc(s.model.split('/').pop())}</td><td class="px-3 py-2 text-xs text-right">${s.count}</td><td class="px-3 py-2 text-xs text-right">${s.avg_latency_ms?.toFixed(0)||0} ms</td><td class="px-3 py-2 text-xs text-right">${s.avg_ttfb_ms?.toFixed(0)||0} ms</td><td class="px-3 py-2 text-xs text-right text-cyan-400 font-medium">${s.avg_tps?.toFixed(1)||0}</td></tr>`).join('');
  }
}