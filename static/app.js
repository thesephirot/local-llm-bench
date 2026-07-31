// LLM Benchmark Dashboard
let presets={},running=false,sortCol='created_at',sortDir='desc',allResults=[],chartInstances={},allSwapConfigs=[],compareSelected=[],chainSelected=new Set();
const $=s=>document.getElementById(s);
const esc=s=>{const d=document.createElement('div');d.textContent=s;return d.innerHTML};
const fmt=d=>d?new Date(d).toLocaleString():'—';
const fmtMs=v=>v!=null?v.toFixed(0)+' ms':'—';
const fmtSec=v=>v!=null?(v/1000).toFixed(2)+' s':'—';
const fmtNum=v=>v!=null&&v!==''?Number(v).toLocaleString():'—';
const fmtDec=(v,d=1)=>v!=null?v.toFixed(d):'—';
const api=(p,o={})=>fetch(p,{headers:{'Content-Type':'application/json'},...o}).then(async r=>{if(!r.ok)throw new Error(r.status+': '+await r.text());return r.json()});

(async()=>{await loadPresets();await loadEndpoints();await loadSwapConfigs();checkReady();switchTab('dashboard');})();

function switchTab(n){
  document.querySelectorAll('.tab-panel').forEach(p=>p.classList.add('hidden'));
  document.querySelectorAll('[id^="tab-"]').forEach(t=>{t.classList.remove('tab-active');t.classList.add('tab-inactive')});
  const p=$('panel-'+n),t=$('tab-'+n);
  if(p)p.classList.remove('hidden');if(t){t.classList.remove('tab-inactive');t.classList.add('tab-active')}
  document.querySelectorAll('.sidebar-nav').forEach(b=>{const m=b.dataset.tab===n;b.classList.toggle('text-gray-200',m);b.classList.toggle('bg-surface-300',m);b.classList.toggle('text-gray-400',!m)});
  if(n==='dashboard')loadDashboard();if(n==='history')loadHistory();if(n==='compare')loadCompare();if(n==='charts')loadCharts();
}
function closeModal(id){$(id).classList.add('hidden')}

// Presets CRUD
async function loadPresets(){
  presets=await api('/api/presets');
  const list=$('presetList'),sel=$('selPreset');list.innerHTML='';sel.innerHTML='<option value="">— preset —</option>';
  for(const[k,p]of Object.entries(presets)){
    const btn=document.createElement('button');btn.className='w-full text-left px-3 py-1.5 rounded-lg text-sm hover:bg-surface-300 transition-colors preset-btn group relative';
    btn.dataset.key=k;btn.innerHTML=`<div class="font-medium text-xs truncate">${p.name}</div><div class="text-[10px] text-gray-500 truncate">${p.description}</div><span class="hidden group-hover:flex absolute right-1 top-1 gap-0.5"><button onclick="event.stopPropagation();editPreset('${p.id}','${k}')" class="text-gray-500 hover:text-gray-300 text-[10px] px-1">✏️</button><button onclick="event.stopPropagation();removePreset('${p.id}')" class="text-red-500 hover:text-red-400 text-[10px] px-1">✕</button></span>`;
    btn.onclick=()=>selectPreset(k);list.appendChild(btn);
    const opt=document.createElement('option');opt.value=k;opt.textContent=p.name;sel.appendChild(opt);
  }
  sel.onchange=()=>selectPreset(sel.value);populateSwapConfigPresets();
}
function selectPreset(key){
  Object.keys(presets).forEach(k=>{const b=document.querySelector(`.preset-btn[data-key="${k}"]`);if(b){b.classList.toggle('bg-surface-300',k===key);b.classList.toggle('ring-1',k===key);b.classList.toggle('ring-cyan-500',k===key)}});
  $('selPreset').value=key;checkReady();
}
function showPresetModal(){$('presetModal').classList.remove('hidden');$('presetModalTitle').textContent='Add Preset';$('presetId').value='';$('presetKey').value='';$('presetName').value='';$('presetPrompt').value='';$('presetDesc').value='';$('presetKey').disabled=false}
function editPreset(id,key){const p=presets[key];if(!p)return;$('presetModal').classList.remove('hidden');$('presetModalTitle').textContent='Edit Preset';$('presetId').value=id;$('presetKey').value=key;$('presetName').value=p.name;$('presetPrompt').value=p.prompt;$('presetDesc').value=p.description;$('presetKey').disabled=true}
async function savePreset(){const id=$('presetId').value,body={key:$('presetKey').value,name:$('presetName').value,prompt:$('presetPrompt').value,description:$('presetDesc').value};if(id){body.id=id;await api('/api/presets/'+id,{method:'PUT',body:JSON.stringify(body)})}else await api('/api/presets',{method:'POST',body:JSON.stringify(body)});closeModal('presetModal');await loadPresets();checkReady()}
async function removePreset(id){if(!confirm('Delete preset?'))return;await api('/api/presets/'+id,{method:'DELETE'});await loadPresets()}

// Endpoints
async function loadEndpoints(){
  const eps=await api('/api/endpoints'),list=$('endpointList'),sel=$('selEndpoint');list.innerHTML='';sel.innerHTML='<option value="">— endpoint —</option>';
  for(const ep of eps){
    const div=document.createElement('div');div.className='group flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm hover:bg-surface-300 cursor-pointer';
    div.innerHTML=`<span class="flex-1 truncate" onclick="selEndpoint('${ep.id}')">${esc(ep.name)}</span><button onclick="editEndpoint('${ep.id}')" class="hidden group-hover:inline text-gray-500 hover:text-gray-300 text-xs">✏️</button><button onclick="removeEndpoint('${ep.id}')" class="hidden group-hover:inline text-red-500 hover:text-red-400 text-xs">🗑️</button>`;
    list.appendChild(div);const opt=document.createElement('option');opt.value=ep.id;opt.textContent=ep.name;sel.appendChild(opt);
  }
  populateSwapConfigEndpoints();checkReady();
}
function selEndpoint(id){$('selEndpoint').value=id;onEndpointChange()}
function onEndpointChange(){$('selModel').innerHTML='<option value="">— model —</option>';loadModels();checkReady()}
function showEndpointModal(){$('endpointModal').classList.remove('hidden');$('modalTitle').textContent='Add Endpoint';$('epId').value='';$('epName').value='';$('epUrl').value='';$('epKey').value='';$('epHeaders').value='{}'}
async function saveEndpoint(){const id=$('epId').value,body={name:$('epName').value,base_url:$('epUrl').value,api_key:$('epKey').value,extra_headers:$('epHeaders').value};if(id){body.id=id;await api('/api/endpoints/'+id,{method:'PUT',body:JSON.stringify(body)})}else await api('/api/endpoints',{method:'POST',body:JSON.stringify(body)});closeModal('endpointModal');await loadEndpoints();checkReady()}
async function editEndpoint(id){const ep=(await api('/api/endpoints')).find(e=>e.id===id);if(!ep)return;$('epId').value=ep.id;$('epName').value=ep.name;$('epUrl').value=ep.base_url;$('epKey').value=ep.api_key;$('epHeaders').value=ep.extra_headers;$('endpointModal').classList.remove('hidden');$('modalTitle').textContent='Edit Endpoint'}
async function removeEndpoint(id){if(!confirm('Delete endpoint?'))return;await api('/api/endpoints/'+id,{method:'DELETE'});await loadEndpoints()}
async function loadModels(){const epId=$('selEndpoint').value,sel=$('selModel');sel.innerHTML='<option value="">— loading… —</option>';try{const models=await api('/api/models?endpoint_id='+epId);sel.innerHTML='<option value="">— model —</option>';models.forEach(m=>{const o=document.createElement('option');o.value=m.id;o.textContent=m.id;sel.appendChild(o)})}catch(e){sel.innerHTML=`<option value="">⚠ ${esc(e.message)}</option>`}sel.onchange=checkReady;checkReady()}
function checkReady(){const ep=$('selEndpoint')?.value||'',md=$('selModel')?.value||'',ps=$('selPreset')?.value||'',btn=$('btnRun');if(btn)btn.disabled=!(ep&&md&&ps)||running}

// Swap Configs
async function loadSwapConfigs(){
  allSwapConfigs=await api('/api/swap-configs');const list=$('swapConfigList');list.innerHTML='';
  if(!allSwapConfigs.length){list.innerHTML='<p class="text-[10px] text-gray-600 px-3">No configs</p>';renderChainConfigList();return}
  for(const cfg of allSwapConfigs){
    const div=document.createElement('div');div.className='group flex items-center gap-2 px-2 py-1 rounded text-xs hover:bg-surface-300 cursor-pointer';
    div.innerHTML=`<span class="flex-1 truncate" onclick="loadSwapConfig('${cfg.id}')" title="${esc(cfg.model)}">${esc(cfg.name)}</span><span class="text-[9px] text-gray-600 truncate max-w-[80px]">${esc(cfg.model.split('/').pop())}</span><button onclick="editSwapConfig('${cfg.id}')" class="hidden group-hover:inline text-gray-500 hover:text-gray-300 text-[10px]">✏️</button><button onclick="removeSwapConfig('${cfg.id}')" class="hidden group-hover:inline text-red-500 hover:text-red-400 text-[10px]">✕</button>`;
    list.appendChild(div);
  }
  renderChainConfigList();
}
function showSwapConfigModal(){$('swapConfigModal').classList.remove('hidden');$('swapConfigModalTitle').textContent='Save Swap Config';$('swapCfgId').value='';$('swapCfgName').value='';$('swapCfgModel').value='';$('swapCfgMaxTokens').value='2048';$('swapCfgTemp').value='0.7';$('swapCfgNotes').value='';populateSwapConfigEndpoints();populateSwapConfigPresets()}
function editSwapConfig(id){const cfg=allSwapConfigs.find(c=>c.id===id);if(!cfg)return;$('swapConfigModal').classList.remove('hidden');$('swapConfigModalTitle').textContent='Edit Swap Config';$('swapCfgId').value=id;$('swapCfgName').value=cfg.name;$('swapCfgModel').value=cfg.model;$('swapCfgMaxTokens').value=cfg.max_tokens;$('swapCfgTemp').value=cfg.temperature;$('swapCfgNotes').value=cfg.notes||'';populateSwapConfigEndpoints();populateSwapConfigPresets();$('swapCfgEndpoint').value=cfg.endpoint_id;$('swapCfgPreset').value=cfg.preset_key}
async function saveSwapConfig(){const id=$('swapCfgId').value,body={name:$('swapCfgName').value,endpoint_id:$('swapCfgEndpoint').value,model:$('swapCfgModel').value,preset_key:$('swapCfgPreset').value,max_tokens:parseInt($('swapCfgMaxTokens').value)||2048,temperature:parseFloat($('swapCfgTemp').value)||0.7,notes:$('swapCfgNotes').value};if(id){body.id=id;await api('/api/swap-configs/'+id,{method:'PUT',body:JSON.stringify(body)})}else await api('/api/swap-configs',{method:'POST',body:JSON.stringify(body)});closeModal('swapConfigModal');await loadSwapConfigs()}
async function removeSwapConfig(id){if(!confirm('Delete config?'))return;await api('/api/swap-configs/'+id,{method:'DELETE'});await loadSwapConfigs()}
async function loadSwapConfig(id){const cfg=allSwapConfigs.find(c=>c.id===id);if(!cfg)return;$('selEndpoint').value=cfg.endpoint_id;$('selModel').innerHTML='<option value="">— loading… —</option>';await loadModels();$('selModel').value=cfg.model;$('selPreset').value=cfg.preset_key;$('inpMaxTokens').value=cfg.max_tokens;$('inpTemp').value=cfg.temperature;checkReady()}
function populateSwapConfigEndpoints(){const sel=$('swapCfgEndpoint');if(!sel)return;sel.innerHTML='<option value="">— endpoint —</option>';document.querySelectorAll('#selEndpoint option').forEach(o=>{if(o.value){const opt=document.createElement('option');opt.value=o.value;opt.textContent=o.textContent;sel.appendChild(opt)}})}
function populateSwapConfigPresets(){const sel=$('swapCfgPreset');if(!sel)return;sel.innerHTML='<option value="">— preset —</option>';for(const[k,p]of Object.entries(presets)){const opt=document.createElement('option');opt.value=k;opt.textContent=p.name;sel.appendChild(opt)}}

// ── Chain Benchmark UI ──────────────────────────────────────

function renderChainConfigList(){
  const el=$('chainConfigList');if(!el)return;
  if(!allSwapConfigs.length){el.innerHTML='<p class="text-gray-600">No swap configs — create one first</p>';return}
  el.innerHTML=allSwapConfigs.map(cfg=>{
    const checked=chainSelected.has(cfg.id)?'checked':'';
    return`<label class="flex items-center gap-1.5 cursor-pointer hover:text-gray-200"><input type="checkbox" data-cfg-id="${esc(cfg.id)}" ${checked} onchange="toggleChainConfig(this)" class="rounded bg-surface border-surface-400 text-purple-500 focus:ring-purple-500 w-3 h-3"><span class="flex-1 truncate">${esc(cfg.name)}</span><span class="text-gray-600 shrink-0">${esc(cfg.model.split('/').pop())}</span></label>`;
  }).join('');
}

function toggleChainConfig(cb){
  const id=cb.dataset.cfgId;if(cb.checked)chainSelected.add(id);else chainSelected.delete(id);
  updateRunChainButton();
}

function updateRunChainButton(){
  const btn=$('btnRunChain');if(!btn)return;btn.disabled=chainSelected.size===0||running;
}

async function runChainBenchmarkStream(){
  if(running||chainSelected.size===0)return;
  running=true;updateRunChainButton();switchTab('dashboard');
  const panel=$('panel-dashboard');
  panel.innerHTML=`<div class="max-w-4xl mx-auto space-y-4 fade-in"><h2 class="text-lg font-semibold">Chain Run</h2><p class="text-sm text-gray-400">Running ${chainSelected.size} LLM${chainSelected.size>1?'s':''} in sequence…</p><div id="chainProgress"></div></div>`;
  const progress=$('chainProgress');

  try{
    const resp=await fetch('/api/run-chain?stream=true',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({config_ids:[...chainSelected]})});
    if(!resp.ok)throw new Error(`HTTP ${resp.status}: ${await resp.text()}`);

    const reader=resp.body.getReader();
    const decoder=new TextDecoder();
    let buffer='';
    let chainId='';
    let stepCount=0;
    let completedCount=0;
    let failedCount=0;

    while(true){
      const{done,value}=await reader.read();
      if(done)break;
      buffer+=decoder.decode(value,{stream:true});
      // Process complete SSE events (split on double newline)
      const parts=buffer.split('\n\n');
      buffer=parts.pop()||''; // keep incomplete event in buffer

      for(const part of parts){
        const lines=part.trim().split('\n');
        let eventType='';
        let dataStr='';
        for(const line of lines){
          if(line.startsWith('event: '))eventType=line.slice(7).trim();
          else if(line.startsWith('data: '))dataStr=line.slice(6).trim();
        }
        if(!eventType||!dataStr)continue;

        try{
          const data=JSON.parse(dataStr);
          if(eventType==='start'){
            chainId=data.chain_id||'';
          }else if(eventType==='step'){
            renderStreamStep(progress,data);
            stepCount++;
          }else if(eventType==='complete'){
            completedCount=data.completed_steps||0;
            failedCount=data.failed_steps||0;
            renderStreamComplete(progress,completedCount,failedCount);
          }else if(eventType==='error'){
            progress.innerHTML+=`<div class="text-center py-4 text-red-400"><p>Error: ${esc(data.message)}</p></div>`;
          }
        }catch(parseErr){
          // Skip malformed events
        }
      }
    }
  }catch(e){
    progress.innerHTML+=`<div class="text-center py-12 text-red-400"><p class="text-lg">Error</p><p class="text-sm mt-1">${esc(e.message)}</p></div>`;
  }finally{
    running=false;updateRunChainButton();
  }
}

function renderStreamStep(progress,data){
  const icon=data.success?'✅':'❌';
  const status=data.success?'completed':(data.error||'failed');
  const metrics=data.benchmark_result?` · ${fmtDec(data.benchmark_result.tokens_per_second)} tok/s · ${fmtMs(data.benchmark_result.total_time_ms)}`:'';
  const card=document.createElement('div');
  card.className='flex items-center gap-3 bg-surface rounded-lg px-4 py-2 text-sm fade-in';
  card.innerHTML=`<span>${icon}</span><div class="flex-1"><div class="font-medium">${esc(data.config_name)}</div><div class="text-xs text-gray-500">${esc(data.model)} · ${esc(status)}${metrics}</div></div><span class="text-xs text-gray-600 shrink-0">step ${data.step_index+1}</span>`;
  progress.appendChild(card);
}

function renderStreamComplete(progress,completedCount,failedCount){
  const summary=document.createElement('div');
  summary.className='flex gap-4 mt-4 text-xs text-gray-500';
  summary.innerHTML=`<span>Total: ${completedCount+failedCount}</span><span>Completed: ${completedCount}</span><span>Failed: ${failedCount}</span>`;
  progress.appendChild(summary);
}

// Run
async function runBenchmark(){
  if(running)return;const epId=$('selEndpoint').value,model=$('selModel').value,preset=$('selPreset').value;if(!epId||!model||!preset)return;
  running=true;$('btnRun').disabled=true;$('btnRun').textContent='Running…';switchTab('dashboard');
  $('panel-dashboard').innerHTML='<div class="text-center py-24"><div class="inline-block w-10 h-10 border-4 border-cyan-500 border-t-transparent rounded-full animate-spin mb-4"></div><p class="text-sm text-gray-400">Running benchmark on <strong>'+esc(model)+'</strong>…</p></div>';
  try{const res=await api('/api/run',{method:'POST',body:JSON.stringify({endpoint_id:epId,model,preset,max_tokens:parseInt($('inpMaxTokens').value)||2048,temperature:parseFloat($('inpTemp').value)||0.7})});
    if(res&&res.success===false){$('panel-dashboard').innerHTML=`<div class="text-center py-24 text-red-400"><p class="text-lg">Benchmark failed</p><p class="text-sm mt-1">${esc(res.error||'Unknown error')}</p></div>`}
    else{await loadDashboard()}}
  catch(e){$('panel-dashboard').innerHTML=`<div class="text-center py-24 text-red-400"><p class="text-lg">Error</p><p class="text-sm mt-1">${esc(e.message)}</p></div>`}
  finally{running=false;checkReady();$('btnRun').textContent='Run'}
}

// Dashboard
async function loadDashboard(){
  const[summary,bestWorst]=await Promise.all([api('/api/summary'),api('/api/best-worst')]);
  const panel=$('panel-dashboard'),s=summary;
  panel.innerHTML=`<div class="max-w-7xl mx-auto space-y-5 fade-in">
    <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
      <div class="bg-surface-200 border border-surface-300 rounded-xl p-4 text-center"><div class="text-2xl font-bold text-cyan-400">${s.total_runs||0}</div><div class="text-xs text-gray-500 mt-1">Total Runs</div></div>
      <div class="bg-surface-200 border border-surface-300 rounded-xl p-4 text-center"><div class="text-2xl font-bold text-blue-400">${s.avg_latency_ms?s.avg_latency_ms.toFixed(0)+' ms':'—'}</div><div class="text-xs text-gray-500 mt-1">Avg Latency</div></div>
      <div class="bg-surface-200 border border-surface-300 rounded-xl p-4 text-center"><div class="text-2xl font-bold text-green-400">${s.avg_ttfb_ms?s.avg_ttfb_ms.toFixed(0)+' ms':'—'}</div><div class="text-xs text-gray-500 mt-1">Avg TTFT</div></div>
      <div class="bg-surface-200 border border-surface-300 rounded-xl p-4 text-center"><div class="text-2xl font-bold text-amber-400">${s.avg_tps?s.avg_tps.toFixed(1)+' tok/s':'—'}</div><div class="text-xs text-gray-500 mt-1">Avg Throughput</div></div>
    </div>`+buildBestWorst(bestWorst)+buildModelTable(s);
}
function buildBestWorst(bw){
  if(!bw||!Object.keys(bw).length)return '';
  const items=[{l:'Throughput',b:bw.best_tps,w:bw.worst_tps,m:'tokens_per_second'},{l:'Latency',b:bw.best_latency,w:bw.worst_latency,m:'total_time_ms'},{l:'TTFT',b:bw.best_ttfb,w:bw.worst_ttfb,m:'time_to_first_token_ms'}].filter(x=>x.b||x.w);
  return `<div class="grid grid-cols-1 md:grid-cols-3 gap-3">${items.map(i=>`<div class="bg-surface-200 border border-surface-300 rounded-xl p-3"><h4 class="text-xs font-semibold text-gray-400 uppercase mb-2">${i.l}</h4><div class="grid grid-cols-2 gap-2">${i.b?`<div class="rounded-lg p-2 badge-best"><div class="text-xs font-medium">${fmtDec(i.b[i.m],1)}</div><div class="text-[10px] text-gray-500 truncate">${esc(i.b.model.split('/').pop())}</div></div>`:''}${i.w?`<div class="rounded-lg p-2 badge-worst"><div class="text-xs font-medium">${fmtDec(i.w[i.m],1)}</div><div class="text-[10px] text-gray-500 truncate">${esc(i.w.model.split('/').pop())}</div></div>`:''}</div></div>`).join('')}</div>`;
}
function buildModelTable(s){
  if(!s.model_stats?.length)return '<div class="bg-surface-200 border border-surface-300 rounded-xl p-12 text-center"><p class="text-sm text-gray-500">Run a benchmark to see stats</p></div>';
  return `<div class="bg-surface-200 border border-surface-300 rounded-xl overflow-hidden"><div class="px-4 py-3 border-b border-surface-300"><h3 class="text-xs font-semibold text-gray-400 uppercase">Model Averages</h3></div><div class="overflow-x-auto"><table class="w-full text-sm"><thead><tr class="border-b border-surface-300 bg-surface/80"><th class="text-left px-4 py-2 text-xs font-semibold text-gray-400 uppercase">Model</th><th class="text-right px-4 py-2 text-xs font-semibold text-gray-400 uppercase">Runs</th><th class="text-right px-4 py-2 text-xs font-semibold text-gray-400 uppercase">Avg Latency</th><th class="text-right px-4 py-2 text-xs font-semibold text-gray-400 uppercase">Avg TTFT</th><th class="text-right px-4 py-2 text-xs font-semibold text-gray-400 uppercase">Avg Tok/s</th><th class="text-right px-4 py-2 text-xs font-semibold text-gray-400 uppercase">Min Lat</th><th class="text-right px-4 py-2 text-xs font-semibold text-gray-400 uppercase">Max Lat</th></tr></thead><tbody class="divide-y divide-surface-300/50">${s.model_stats.map(m=>`<tr class="hover:bg-surface-300/50"><td class="px-4 py-2 text-xs font-medium">${esc(m.model.split('/').pop())}</td><td class="px-4 py-2 text-xs text-right">${m.count}</td><td class="px-4 py-2 text-xs text-right">${(m.avg_latency_ms||0).toFixed(0)}ms</td><td class="px-4 py-2 text-xs text-right">${(m.avg_ttfb_ms||0).toFixed(0)}ms</td><td class="px-4 py-2 text-xs text-right text-cyan-400 font-medium">${(m.avg_tps||0).toFixed(1)}</td><td class="px-4 py-2 text-xs text-right text-green-400">${(m.min_latency||0).toFixed(0)}ms</td><td class="px-4 py-2 text-xs text-right text-red-400">${(m.max_latency||0).toFixed(0)}ms</td></tr>`).join('')}</tbody></table></div></div>`;
}

// History
async function loadHistory(){
  const p=new URLSearchParams();const fm=$('filterModel')?.value,fp=$('filterPreset')?.value,fe=$('filterEndpoint')?.value,fd=$('filterFrom')?.value,td=$('filterTo')?.value;
  if(fm)p.set('model',fm);if(fp)p.set('preset',fp);if(fe)p.set('endpoint',fe);if(fd)p.set('from_date',fd);if(td)p.set('to_date',td);
  allResults=await api('/api/history?'+p.toString());populateHistoryFilters();renderHistoryTable();
}
function populateHistoryFilters(){
  const fm=$('filterModel');if(!fm)return;const c=fm.value;fm.innerHTML='<option value="">All Models</option>'+[...new Set(allResults.map(r=>r.model))].sort().map(m=>`<option ${m===c?'selected':''}>${esc(m)}</option>`).join('');
  const fp=$('filterPreset');if(!fp)return;const c2=fp.value;fp.innerHTML='<option value="">All Presets</option>'+[...new Set(allResults.map(r=>r.preset_name))].sort().map(p=>`<option ${p===c2?'selected':''}>${esc(p)}</option>`).join('');
  const fe=$('filterEndpoint');if(!fe)return;const c3=fe.value;fe.innerHTML='<option value="">All Endpoints</option>'+[...new Set(allResults.map(r=>r.endpoint_id))].sort().map(e=>{const ep=allResults.find(r=>r.endpoint_id===e);return`<option value="${e}" ${e===c3?'selected':''}>${esc(ep?.endpoint_name||e)}</option>`}).join('');
}
function renderHistoryTable(){
  const panel=$('panel-history'),sorted=[...allResults].sort((a,b)=>{let va=a[sortCol],vb=b[sortCol];if(sortCol==='created_at')return sortDir==='asc'?va.localeCompare(vb):vb.localeCompare(va);va=Number(va)||0;vb=Number(vb)||0;return sortDir==='asc'?va-vb:vb-va});
  const cl={created_at:'Date',endpoint_name:'Endpoint',model:'Model',preset_name:'Preset',total_time_ms:'Latency',time_to_first_token_ms:'TTFT',tokens_per_second:'Tok/s',completion_tokens:'Tokens',output_length:'Len'},lc=['created_at','endpoint_name','model','preset_name'];
  panel.innerHTML=`<div class="max-w-7xl mx-auto space-y-4 fade-in"><div class="flex items-center justify-between"><h2 class="text-lg font-semibold">History (${allResults.length} runs)</h2><div class="flex gap-2"><button onclick="loadHistory()" class="px-3 py-1.5 text-xs bg-surface-200 hover:bg-surface-300 border border-surface-400 rounded-lg">↻ Refresh</button><button onclick="clearAllResults()" class="px-3 py-1.5 text-xs bg-red-900/30 hover:bg-red-900/50 border border-red-800/50 text-red-400 rounded-lg">Clear All</button></div></div><div class="flex flex-wrap gap-3"><select id="filterModel" onchange="loadHistory()" class="bg-surface-200 border border-surface-400 rounded-lg px-3 py-1.5 text-xs"><option value="">All Models</option></select><select id="filterPreset" onchange="loadHistory()" class="bg-surface-200 border border-surface-400 rounded-lg px-3 py-1.5 text-xs"><option value="">All Presets</option></select><select id="filterEndpoint" onchange="loadHistory()" class="bg-surface-200 border border-surface-400 rounded-lg px-3 py-1.5 text-xs"><option value="">All Endpoints</option></select><input type="date" id="filterFrom" onchange="loadHistory()" class="bg-surface-200 border border-surface-400 rounded-lg px-3 py-1.5 text-xs" title="From"><input type="date" id="filterTo" onchange="loadHistory()" class="bg-surface-200 border border-surface-400 rounded-lg px-3 py-1.5 text-xs" title="To"></div><div class="bg-surface-200 border border-surface-300 rounded-xl overflow-hidden"><div class="overflow-x-auto"><table class="sortable w-full text-sm"><thead><tr class="border-b border-surface-300 bg-surface/80">${Object.keys(cl).map(c=>`<th class="text-${lc.includes(c)?'left':'right'} px-4 py-2 text-xs font-semibold text-gray-400 uppercase" onclick="handleSort('${c}')">${cl[c]}${sortCol===c?(sortDir==='asc'?' ▲':' ▼'):''}</th>`).join('')}<th class="px-4 py-2"></th></tr></thead><tbody class="divide-y divide-surface-300/50">${sorted.length?sorted.map(r=>`<tr class="hover:bg-surface-300/50"><td class="px-4 py-2 text-xs text-gray-300">${r.success?'':`<span class="text-red-400" title="${esc(r.error||'failed').replace(/"/g,'&quot;')}">⚠ </span>`}${fmt(r.created_at)}</td><td class="px-4 py-2 text-xs">${esc(r.endpoint_name)}</td><td class="px-4 py-2 text-xs font-medium">${esc(r.model)}</td><td class="px-4 py-2 text-xs text-gray-400">${esc(r.preset_name)}</td><td class="px-4 py-2 text-xs text-right">${fmtSec(r.total_time_ms)}</td><td class="px-4 py-2 text-xs text-right">${fmtMs(r.time_to_first_token_ms)}</td><td class="px-4 py-2 text-xs text-right text-cyan-400 font-medium">${fmtDec(r.tokens_per_second)}</td><td class="px-4 py-2 text-xs text-right">${fmtNum(r.completion_tokens)}</td><td class="px-4 py-2 text-xs text-right">${fmtNum(r.output_length)}</td><td class="px-4 py-2 text-right whitespace-nowrap"><button onclick="showDetail('${r.id}')" class="text-cyan-400 hover:text-cyan-300 text-xs mr-2">View</button><button onclick="deleteResult('${r.id}')" class="text-red-500 hover:text-red-400 text-xs">✕</button></td></tr>`).join(''):'<tr><td colspan="10" class="text-center py-12 text-gray-600 text-sm">No results</td></tr>'}</tbody></table></div></div></div>`;
}
function handleSort(c){if(sortCol===c)sortDir=sortDir==='asc'?'desc':'asc';else{sortCol=c;sortDir='desc'}renderHistoryTable()}
async function showDetail(id){const r=await api('/api/results/'+id);$('detailTitle').textContent=r.endpoint_name+' / '+r.model;$('detailMeta').textContent=r.preset_name+' · '+fmt(r.created_at);$('detailStats').innerHTML=[{l:'TTFT',v:fmtMs(r.time_to_first_token_ms)},{l:'Total',v:fmtSec(r.total_time_ms)},{l:'Tok/s',v:fmtDec(r.tokens_per_second)},{l:'Tokens',v:fmtNum(r.completion_tokens)},{l:'Output',v:(r.output_length||0).toLocaleString()+' ch'}].map(s=>`<div class="bg-surface rounded-lg p-2 text-center"><div class="text-base font-bold text-cyan-400">${s.v}</div><div class="text-xs text-gray-500">${s.l}</div></div>`).join('');$('detailPrompt').textContent=r.prompt;$('detailResponse').textContent=r.response;$('detailModal').classList.remove('hidden')}
async function deleteResult(id){await api('/api/results/'+id,{method:'DELETE'});loadHistory()}
async function clearAllResults(){if(!confirm('Clear all?'))return;await api('/api/results',{method:'DELETE'});loadHistory()}

// Compare
async function loadCompare(){
  compareSelected=[];const h=await api('/api/history?limit=500');const panel=$('panel-compare');
  panel.innerHTML=`<div class="max-w-7xl mx-auto space-y-4 fade-in"><div class="flex items-center justify-between"><h2 class="text-lg font-semibold">Compare Runs</h2><button onclick="doCompare()" id="btnCompare" disabled class="bg-cyan-600 hover:bg-cyan-500 disabled:opacity-40 text-white font-semibold rounded-lg px-5 py-2 text-sm transition-colors">Compare (${compareSelected.length})</button></div><p class="text-xs text-gray-500">Select 2+ runs below</p><div class="bg-surface-200 border border-surface-300 rounded-xl overflow-hidden"><div class="overflow-x-auto"><table class="w-full text-sm"><thead><tr class="border-b border-surface-300 bg-surface/80"><th class="px-4 py-2 text-left text-xs font-semibold text-gray-400 uppercase w-10">Sel</th><th class="px-4 py-2 text-left text-xs font-semibold text-gray-400 uppercase">Date</th><th class="px-4 py-2 text-left text-xs font-semibold text-gray-400 uppercase">Endpoint</th><th class="px-4 py-2 text-left text-xs font-semibold text-gray-400 uppercase">Model</th><th class="px-4 py-2 text-left text-xs font-semibold text-gray-400 uppercase">Preset</th><th class="px-4 py-2 text-right text-xs font-semibold text-gray-400 uppercase">Latency</th><th class="px-4 py-2 text-right text-xs font-semibold text-gray-400 uppercase">Tok/s</th></tr></thead><tbody class="divide-y divide-surface-300/50">${h.length?h.map(r=>`<tr class="hover:bg-surface-300/50"><td class="px-4 py-2"><input type="checkbox" data-id="${r.id}" onchange="toggleCompare(this)" class="rounded bg-surface border-surface-400 text-cyan-500 focus:ring-cyan-500"></td><td class="px-4 py-2 text-xs text-gray-300">${fmt(r.created_at)}</td><td class="px-4 py-2 text-xs">${esc(r.endpoint_name)}</td><td class="px-4 py-2 text-xs font-medium">${esc(r.model)}</td><td class="px-4 py-2 text-xs text-gray-400">${esc(r.preset_name)}</td><td class="px-4 py-2 text-xs text-right">${fmtSec(r.total_time_ms)}</td><td class="px-4 py-2 text-xs text-right text-cyan-400 font-medium">${fmtDec(r.tokens_per_second)}</td></tr>`).join(''):'<tr><td colspan="7" class="text-center py-12 text-gray-600">No runs to compare</td></tr>'}</tbody></table></div></div><div id="compareResults"></div></div>`;
}
function toggleCompare(cb){const id=cb.dataset.id;if(cb.checked)compareSelected.push(id);else compareSelected=compareSelected.filter(x=>x!==id);const btn=$('btnCompare');if(btn){btn.disabled=compareSelected.length<2;btn.textContent='Compare ('+compareSelected.length+')'}}
async function doCompare(){
  if(compareSelected.length<2)return;
  const results=await api('/api/compare',{method:'POST',body:JSON.stringify({result_ids:compareSelected})});
  const el=$('compareResults');
  const metrics=[{k:'total_time_ms',l:'Latency',f:v=>fmtSec(v)},{k:'time_to_first_token_ms',l:'TTFT',f:v=>fmtMs(v)},{k:'tokens_per_second',l:'Tok/s',f:v=>fmtDec(v)},{k:'completion_tokens',l:'Tokens',f:v=>fmtNum(v)},{k:'output_length',l:'Length',f:v=>fmtNum(v)}];
  const colors=['text-cyan-400','text-amber-400','text-green-400','text-pink-400','text-blue-400','text-purple-400'];
  el.innerHTML=`<div class="mt-6 space-y-4 fade-in"><h3 class="text-sm font-semibold text-gray-300">Comparison</h3><div class="grid grid-cols-1 md:grid-cols-${Math.min(results.length,4)} gap-4">${results.map((r,i)=>{
    let bc=0;metrics.forEach(m=>{const vals=results.map(x=>Number(x[m.k])||0);if(Number(r[m.k])===Math.max(...vals)||Number(r[m.k])===Math.min(...vals))bc++});
    return`<div class="comparison-card bg-surface-200 border border-surface-300 rounded-xl p-4 ${bc>0?'ring-1 ring-cyan-500/30':''}"><div class="flex items-center justify-between mb-3"><h4 class="font-semibold ${colors[i%colors.length]} text-sm">${esc(r.model.split('/').pop())}</h4>${bc>0?'<span class="text-[10px] bg-cyan-500/20 text-cyan-400 px-1.5 py-0.5 rounded">best×'+bc+'</span>':''}</div><div class="text-[10px] text-gray-500 mb-3">${esc(r.endpoint_name)} · ${esc(r.preset_name)}<br>${fmt(r.created_at)}</div>${metrics.map(m=>`<div class="flex justify-between py-1 border-b border-surface-300/50 text-xs"><span class="text-gray-400">${m.l}</span><span class="font-medium ${colors[i%colors.length]}">${m.f(r[m.k])}</span></div>`).join('')}</div>`;
  }).join('')}</div></div>`;
}

// Charts / Trends
async function loadCharts(){
  const summary=await api('/api/summary'),latest=await api('/api/latest');
  const trends=await api('/api/trends?group_by=day');
  renderCharts(summary,latest,trends);
}
async function renderCharts(summary,latest,trends){
  const panel=$('panel-charts');const old=Object.values(chartInstances);chartInstances={};old.forEach(c=>{try{c.destroy()}catch(e){}});
  const cards=[{l:'Total Runs',v:summary.total_runs||0,c:'text-cyan-400'},{l:'Avg Latency',v:summary.avg_latency_ms?summary.avg_latency_ms.toFixed(0)+' ms':'—',c:'text-blue-400'},{l:'Avg TTFT',v:summary.avg_ttfb_ms?summary.avg_ttfb_ms.toFixed(0)+' ms':'—',c:'text-green-400'},{l:'Avg Throughput',v:summary.avg_tps?summary.avg_tps.toFixed(1)+' tok/s':'—',c:'text-amber-400'}];
  panel.innerHTML=`<div class="max-w-7xl mx-auto space-y-4 fade-in">
    <div class="grid grid-cols-2 md:grid-cols-4 gap-3">${cards.map(c=>`<div class="bg-surface-200 border border-surface-300 rounded-xl p-4 text-center"><div class="text-2xl font-bold ${c.c}">${c.v}</div><div class="text-xs text-gray-500 mt-1">${c.l}</div></div>`).join('')}</div>
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <div class="bg-surface-200 border border-surface-300 rounded-xl p-4"><h3 class="text-xs font-semibold text-gray-400 uppercase mb-3">Latency Trend (ms)</h3><canvas id="chartLatency"></canvas></div>
      <div class="bg-surface-200 border border-surface-300 rounded-xl p-4"><h3 class="text-xs font-semibold text-gray-400 uppercase mb-3">Throughput Trend (tok/s)</h3><canvas id="chartThroughput"></canvas></div>
      <div class="bg-surface-200 border border-surface-300 rounded-xl p-4"><h3 class="text-xs font-semibold text-gray-400 uppercase mb-3">TTFT Trend (ms)</h3><canvas id="chartTTFT"></canvas></div>
      <div class="bg-surface-200 border border-surface-300 rounded-xl p-4"><h3 class="text-xs font-semibold text-gray-400 uppercase mb-3">Runs Over Time</h3><canvas id="chartRuns"></canvas></div>
    </div>
    ${summary.model_stats?.length?`<div class="bg-surface-200 border border-surface-300 rounded-xl p-4"><h3 class="text-xs font-semibold text-gray-400 uppercase mb-3">Model Comparison</h3><div class="overflow-x-auto"><table class="w-full text-sm"><thead><tr class="border-b border-surface-300"><th class="text-left px-3 py-2 text-xs font-semibold text-gray-400 uppercase">Model</th><th class="text-right px-3 py-2 text-xs font-semibold text-gray-400 uppercase">Runs</th><th class="text-right px-3 py-2 text-xs font-semibold text-gray-400 uppercase">Avg Latency</th><th class="text-right px-3 py-2 text-xs font-semibold text-gray-400 uppercase">Avg TTFT</th><th class="text-right px-3 py-2 text-xs font-semibold text-gray-400 uppercase">Avg Tok/s</th></tr></thead><tbody class="divide-y divide-surface-300/50">${summary.model_stats.map(s=>`<tr><td class="px-3 py-2 text-xs font-medium">${esc(s.model.split('/').pop())}</td><td class="px-3 py-2 text-xs text-right">${s.count}</td><td class="px-3 py-2 text-xs text-right">${(s.avg_latency_ms||0).toFixed(0)} ms</td><td class="px-3 py-2 text-xs text-right">${(s.avg_ttfb_ms||0).toFixed(0)} ms</td><td class="px-3 py-2 text-xs text-right text-cyan-400 font-medium">${(s.avg_tps||0).toFixed(1)}</td></tr>`).join('')}</tbody></table></div></div>`:''}
  </div>`;
  await new Promise(r=>requestAnimationFrame(r))
  if(!latest?.length)return;
  const MAX=50,reversed=[...latest].reverse().slice(0,MAX);
  const labels=reversed.map(r=>r.model.split('/').pop().substring(0,12));
  const bo={responsive:true,maintainAspectRatio:false,animation:false,plugins:{legend:{labels:{color:'#9ca3af',font:{size:11}}}},scales:{x:{ticks:{color:'#6b7280',font:{size:10}},grid:{color:'#1e293b'}},y:{ticks:{color:'#6b7280',font:{size:10}},grid:{color:'#1e293b'}}}};
  const gc=id=>{const el=document.getElementById(id);const ex=Chart.getChart(el);if(ex)ex.destroy();return el.getContext('2d')};
  chartInstances.latency=new Chart(gc('chartLatency'),{type:'bar',data:{labels,datasets:[{label:'Latency (ms)',data:reversed.map(r=>r.total_time_ms),backgroundColor:'rgba(6,182,212,0.6)',borderColor:'#06b6d4',borderWidth:1}]},options:bo});
  chartInstances.ttfb=new Chart(gc('chartTTFT'),{type:'line',data:{labels,datasets:[{label:'TTFT (ms)',data:reversed.map(r=>r.time_to_first_token_ms||r.total_time_ms),borderColor:'#22c55e',backgroundColor:'rgba(34,197,94,0.1)',fill:true,tension:0.3,pointRadius:3,pointBackgroundColor:'#22c55e'}]},options:bo});
  chartInstances.throughput=new Chart(gc('chartThroughput'),{type:'line',data:{labels,datasets:[{label:'Tok/s',data:reversed.map(r=>r.tokens_per_second),borderColor:'#f59e0b',backgroundColor:'rgba(245,158,11,0.1)',fill:true,tension:0.3,pointRadius:3,pointBackgroundColor:'#f59e0b'}]},options:bo});
  if(trends?.length){
    const tLabels=trends.map(t=>t.period),tCounts=trends.map(t=>t.count);
    chartInstances.runs=new Chart(gc('chartRuns'),{type:'bar',data:{labels:tLabels,datasets:[{label:'Runs',data:tCounts,backgroundColor:'rgba(139,92,246,0.6)',borderColor:'#8b5cf6',borderWidth:1}]},options:bo});
  }
}
