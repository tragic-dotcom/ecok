const state = {
  user: null, profile: null, modules: [], packages: [], currentPlan: null, devices: [], history: [], transactions: [], adminUsers: [], bmaxStatus: null, bmaxAdmin: null, bmaxTimer: null, bmaxBattery: null,
  selectedModule: null, nearestModule: null, activeSession: null, lastEstimate: null,
  map: null, mapEngine: null, mapLoaded: false, mapVisible: false, mapMarkers: [], mapTileLayer: null,
  mapFallbackTimer: null, mapErrorCount: 0, map3d: false, nativeBuilding3DLayers: [], adminPickMode: false, adminRelocateModuleId: null,
  userLocation: null, userMarker: null, mapCenter: [-86.8515, 21.1619], sessionRaf: null, sessionTick: null,
  currentProfileName: '', connectionAnimationActive: false, connectionAnimationToken: 0, packageAnimationTimer: null,
};

const DEVICE_PRESETS = [
  {name:'iPhone 16 Pro', batteryMah:3582, maxWatts:27, connector:'USB-C'},
  {name:'iPhone 15', batteryMah:3349, maxWatts:20, connector:'USB-C'},
  {name:'Samsung Galaxy S25 Ultra', batteryMah:5000, maxWatts:45, connector:'USB-C'},
  {name:'Samsung Galaxy S24', batteryMah:4000, maxWatts:25, connector:'USB-C'},
  {name:'Google Pixel 9', batteryMah:4700, maxWatts:27, connector:'USB-C'},
  {name:'Xiaomi 15', batteryMah:5400, maxWatts:90, connector:'USB-C'},
  {name:'Motorola Edge', batteryMah:5000, maxWatts:68, connector:'USB-C'},
];
const $ = id => document.getElementById(id);
const money = value => '$' + Number(value || 0).toLocaleString('es-MX',{minimumFractionDigits:2,maximumFractionDigits:2});
const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));

function showToast(message, type='info') {
  const el=$('toast'); el.textContent=message; el.className=`toast ${type}`;
  clearTimeout(el._timer); el._timer=setTimeout(()=>el.classList.add('hidden'),3500);
}
async function syncDeviceBatteryLevel(){
  if(!navigator.getBattery||state.bmaxBattery)return;
  try{const battery=await navigator.getBattery();state.bmaxBattery=battery;const apply=()=>{if(!battery.charging||battery.level>=0){const pct=Math.max(1,Math.min(99,Math.round(battery.level*100)));$('batteryLevel').value=pct;$('batteryValue').textContent=`${pct}%`;$('batteryValue').title='Lectura del nivel de batería del navegador';updateEstimate();}};apply();battery.addEventListener('levelchange',apply);}catch(_){/* Manual slider remains available when this browser blocks battery access. */}
}

function setConnection(isOnline=true){$('connectionPill').textContent=isOnline?'ONLINE':'LOCAL';$('connectionPill').className=`status-pill ${isOnline?'online-pill':'local-pill'}`;}
async function api(url, options={}) {
  const headers={...(options.headers||{})};
  if(options.body && !headers['Content-Type']) headers['Content-Type']='application/json';
  let response;
  try { response=await fetch(url,{credentials:'same-origin',...options,headers}); }
  catch(error){setConnection(false);throw new Error('No se pudo conectar con Flask. Comprueba que py app.py siga ejecutándose.');}
  const data=await response.json().catch(()=>({}));
  if(!response.ok){const error=new Error(data.error||`Error ${response.status}`);error.status=response.status;error.data=data;setConnection(response.status>=500?false:true);throw error;}
  setConnection(true);return data;
}

function setView(view){
  if(view==='charge' && state.user?.role!=='admin' && !state.currentPlan){showToast('Primero compra un plan en Servicios para poder utilizar EcoBlack.','info');view='services';}
  document.querySelectorAll('.view').forEach(el=>el.classList.toggle('active-view',el.id===`view-${view}`));
  document.querySelectorAll('.nav').forEach(el=>el.classList.toggle('active',el.dataset.view===view));
  const titles={home:'Encuentra energía cerca de ti',services:'Paquetes de servicio',charge:'Configurar una carga',profile:'Mi perfil',admin:'Administración EcoBlack'};
  $('pageTitle').textContent=state.activeSession?`⚡ ${state.activeSession.module_name}`:(titles[view]||'EcoBlack');
  if(view==='home'&&state.mapVisible) requestAnimationFrame(resizeMap);
  if(view==='admin'&&state.user?.role==='admin'){loadAdminUsers().catch(e=>showToast(e.message,'error'));loadAdminRevenue().catch(e=>showToast(e.message,'error'));loadAdminBmax().catch(e=>showToast(e.message,'error'));}
  if(view==='services') loadPackages().catch(()=>{});
}
function showAuthPanel(panel){document.querySelectorAll('.auth-tab').forEach(el=>el.classList.toggle('active',el.dataset.authView===panel));$('loginPanel').classList.toggle('hidden',panel!=='login');$('registerPanel').classList.toggle('hidden',panel!=='register');$('authError').classList.add('hidden');}
function showAuthError(message){$('authError').textContent=message;$('authError').classList.remove('hidden');}
async function checkAuth(){const data=await api('/api/auth/me');if(data.authenticated){state.user=data.user;await enterApp();}else showLogin();}
function showLogin(){document.body.classList.remove(...[...document.body.classList].filter(name=>name.startsWith('admin-theme-')));$('authScreen').classList.remove('hidden');$('app').classList.add('hidden');}
async function enterApp(){
  $('authScreen').classList.add('hidden');$('app').classList.remove('hidden');
  state.user=state.user||{};$('userBadge').textContent=state.user.role==='admin'?`⚡ ${state.user.username} · ADMIN`:`@${state.user.username}`;
  $('adminNav').classList.toggle('hidden',state.user.role!=='admin');
  state.currentPlan=null;await loadProfile();await loadData();await loadModules();await loadPackages();await refreshActiveSession();applyPlanTheme();applyAdminServiceTheme();renderActivePlan();
  if(state.user.role==='admin'){await loadAdminUsers();await loadAdminRevenue();await loadAdminBmax();}
  syncDeviceBatteryLevel().catch(()=>{});
}

// IndexedDB keeps device presets and a convenient local session history for this browser.
function dbOpen(){return new Promise((resolve,reject)=>{const req=indexedDB.open('EcoBlackDB',5);req.onupgradeneeded=()=>{const db=req.result;if(!db.objectStoreNames.contains('devices'))db.createObjectStore('devices',{keyPath:'id'});if(!db.objectStoreNames.contains('history'))db.createObjectStore('history',{keyPath:'id',autoIncrement:true});};req.onsuccess=()=>resolve(req.result);req.onerror=()=>reject(req.error);});}
async function dbAll(store){const db=await dbOpen();return new Promise((resolve,reject)=>{const req=db.transaction(store,'readonly').objectStore(store).getAll();req.onsuccess=()=>resolve(req.result||[]);req.onerror=()=>reject(req.error);});}
async function dbPut(store,value){const db=await dbOpen();return new Promise((resolve,reject)=>{const req=db.transaction(store,'readwrite').objectStore(store).put(value);req.onsuccess=()=>resolve(req.result);req.onerror=()=>reject(req.error);});}
async function dbDelete(store,key){const db=await dbOpen();return new Promise((resolve,reject)=>{const req=db.transaction(store,'readwrite').objectStore(store).delete(key);req.onsuccess=()=>resolve();req.onerror=()=>reject(req.error);});}
function normalizeDevice(device){
  const name=device.name||device.model||'Mi celular';
  let preset=DEVICE_PRESETS.find(p=>p.name.toLowerCase()===name.toLowerCase());
  if(!preset){const low=name.toLowerCase();preset=DEVICE_PRESETS.find(p=>(low.includes('iphone')&&p.name.toLowerCase().includes('iphone'))||(low.includes('samsung')&&p.name.toLowerCase().includes('samsung'))||(low.includes('pixel')&&p.name.toLowerCase().includes('pixel'))||(low.includes('xiaomi')&&p.name.toLowerCase().includes('xiaomi'))||(low.includes('motorola')&&p.name.toLowerCase().includes('motorola')));}
  return {...(preset||{batteryMah:4200,maxWatts:25,connector:'USB-C'}),...device,name,batteryMah:Number(device.batteryMah||device.battery_mah||(preset&&preset.batteryMah)||4200),maxWatts:Number(device.maxWatts||device.max_watts||(preset&&preset.maxWatts)||25),connector:device.connector||(preset&&preset.connector)||'USB-C'};
}
async function loadData(){
  state.devices=(await dbAll('devices')).filter(d=>d.owner===state.user.username).map(normalizeDevice);
  if(!state.devices.length){state.devices=DEVICE_PRESETS.slice(0,5).map((d,i)=>({...d,id:`${state.user.username}-preset-${i}`,owner:state.user.username}));for(const d of state.devices)await dbPut('devices',d);}
  state.history=(await dbAll('history')).filter(h=>h.owner===state.user.username);
  renderDevices();renderHistory();
}
function renderDevices(){
  $('deviceSelect').innerHTML=state.devices.map(d=>`<option value="${esc(d.id)}">${esc(d.name)} · ${d.batteryMah} mAh · ${d.maxWatts} W</option>`).join('');
  $('deviceList').innerHTML=state.devices.map(d=>`<div class="list-item device-profile-row"><div><b>${esc(d.name)}</b><div class="meta">${d.batteryMah} mAh · hasta ${d.maxWatts} W · ${esc(d.connector)}</div></div><button class="glass-button" data-remove-device="${esc(d.id)}">Eliminar</button></div>`).join('');
  updateEstimate();
}
function paymentMethodLabel(method){return method==='wallet'?'Saldo EcoBlack':method==='cash'?'Efectivo pendiente':method==='card_demo'?'Tarjeta (demo)':method==='bmax_pro'?'Acceso Bmax Pro':method;}
function renderHistory(){
  $('historyList').innerHTML=state.history.length?[...state.history].sort((a,b)=>new Date(b.date)-new Date(a.date)).slice(0,30).map(h=>{
    const payment=h.payment_method?` · ${paymentMethodLabel(h.payment_method)}`:'';
    const due=Number(h.amount_due||0)>0?` · pendiente ${money(h.amount_due)}`:'';
    return `<div class="list-item"><div><b>${esc(h.device)}</b><div class="meta">${esc(h.package)} · ${new Date(h.date).toLocaleString('es-MX')}</div></div><div><b>${h.minutes} min</b><div class="meta">${esc(h.connector||'')} ${h.moduleName?'· '+esc(h.moduleName):''}${payment}${due}</div></div></div>`;
  }).join(''):'<div class="muted">Todavía no tienes sesiones guardadas.</div>';
}

async function loadProfile(){
  const data=await api('/api/profile');state.profile=data.profile;state.user={...state.user,...data.profile};
  const visibleName=state.profile.display_name||state.user.username;
  $('profileName').textContent=visibleName;
  $('displayNameInput').value=visibleName;
  $('profileUsage').textContent=`Uso: ${state.user.usage||'General'}`;
  $('userBadge').textContent=state.user.role==='admin'?`⚡ ${visibleName} · ADMIN`:visibleName;
  $('profileBalance').textContent=money(state.profile.balance);$('profileDiscount').textContent=state.profile.discount_percent>0?`Tu descuento personal: ${state.profile.discount_percent}%`:'Sin descuento personal';
  $('balanceBadge').textContent=money(state.profile.balance);$('balanceBadge').title='Saldo disponible';
  if(state.packages.length){syncCurrentPlanFromAccount();}
  renderProfilePhoto(state.profile.avatar_data);renderTransactions(state.profile.transactions||[]);renderPackages();renderActivePlan();applyPlanTheme();updateEstimate();
}
function renderProfilePhoto(data){const img=$('profilePhoto'),fallback=$('profilePhotoFallback');if(data){img.src=data;img.classList.remove('hidden');fallback.classList.add('hidden');}else{img.removeAttribute('src');img.classList.add('hidden');fallback.classList.remove('hidden');fallback.textContent=(state.profile?.display_name||state.user?.username||'EB').slice(0,2).toUpperCase();}}
function renderTransactions(rows){$('transactionList').innerHTML=rows.length?rows.map(t=>`<div class="list-item transaction-row"><div><b>${esc(t.description||t.kind)}</b><div class="meta">${new Date(t.created_at).toLocaleString('es-MX')}</div></div><div class="transaction-amount ${Number(t.amount)>=0?'positive':'negative'}">${Number(t.amount)>=0?'+':''}${money(t.amount)}<small>Saldo ${money(t.balance_after)}</small></div></div>`).join(''):'<div class="muted">Aquí aparecerán tus recargas de saldo y los cargos por paquetes.</div>';}

async function loadPackages(){
  const data=await api('/api/packages');state.packages=data.packages||[];state.bmaxStatus=data.bmax||state.bmaxStatus;state.currentPlan=data.current_plan||null;
  if(!state.currentPlan&&state.bmaxStatus?.has_purchased)state.currentPlan={id:'bmax_pro',name:'Bmax Pro',minutes:0,max_watts:100,status:'active',permanent:true};
  state.user.balance=data.balance;state.user.discount_percent=data.discount_percent;state.user.active_plan=state.currentPlan?.id||null;
  if(state.profile){state.profile.balance=data.balance;state.profile.discount_percent=data.discount_percent;state.profile.active_plan=state.currentPlan?.id||null;}
  $('balanceBadge').textContent=money(data.balance);syncCurrentPlanFromAccount();renderPackages();renderActivePlan();applyPlanTheme();updatePriceSummary();startBmaxCountdown();
}
function syncCurrentPlanFromAccount(){
  const id=state.user?.active_plan||(state.bmaxStatus?.has_purchased?'bmax_pro':null);
  if(!id){state.currentPlan=null;return;}
  if(id==='bmax_pro')state.currentPlan={id:'bmax_pro',name:'Bmax Pro',minutes:0,max_watts:100,status:'active',permanent:true};
  else{const p=state.packages.find(item=>item.id===id);state.currentPlan=p?{...p,id,status:state.user?.active_plan_status||'ready',minutes:Number(state.user?.active_plan_minutes||p.minutes),permanent:false}:state.currentPlan;}
}
function currentPackage(){
  const activeId=state.currentPlan?.id;
  const id=state.user?.role==='admin'?($('packageSelect')?.value||activeId||'basic'):(activeId||'basic');
  return state.packages.find(p=>p.id===id)||state.packages.find(p=>p.id==='basic')||{id:'basic',name:'Básico',minutes:15,max_watts:10,base_price:15,final_price:15,discount_percent:0};
}
function renderActivePlan(){
  const plan=state.currentPlan;const admin=state.user?.role==='admin';
  const name=admin?'Administrador':plan?.name||'Sin plan';
  const description=admin?'Acceso administrativo para gestionar la red.':plan?.id==='bmax_pro'?'Acceso permanente · módulos Bmax VIP · objetivo estimado 100% · tope 100 W.':plan?`Una sesión incluida · ${plan.minutes} min · hasta ${plan.max_watts} W. Se consumirá al iniciar la carga.`:'Necesitas comprar un plan para conectar un módulo. Puedes consultar el mapa antes de comprar.';
  const tag=admin?'ADMIN':plan?.id==='bmax_pro'?'VIP BMAX ACTIVO':plan?'PLAN LISTO · 1 SESIÓN':'SIN PLAN · ACCESO BLOQUEADO';
  const target=$('homePlanName');if(target)target.textContent=name;
  const desc=$('homePlanDescription');if(desc)desc.textContent=description;
  const badge=$('planBadge');if(badge){badge.textContent=tag;badge.className=`plan-badge ${plan?.id==='bmax_pro'?'bmax-plan-badge':''} ${!plan&&!admin?'no-plan-badge':''}`;}
  const action=$('homePlanAction');if(action){action.textContent=plan||admin?'Ver módulos':'Comprar un plan';action.dataset.view=plan||admin?'home':'services';}
  const tagEl=$('homePlanTag');if(tagEl)tagEl.textContent=tag;
}
function applyPlanTheme(){
  if(!document.body)return;
  [...document.body.classList].filter(name=>name.startsWith('plan-theme-')).forEach(name=>document.body.classList.remove(name));
  const key=state.currentPlan?.id||'none';document.body.classList.add(`plan-theme-${key}`);
}
const ADMIN_SERVICE_THEMES = ['wine','basic','fast','power','custom','bmax_pro'];
function applyAdminServiceTheme(requestedTheme=null){
  if(!document.body)return;
  [...document.body.classList].filter(name=>name.startsWith('admin-theme-')).forEach(name=>document.body.classList.remove(name));
  if(state.user?.role!=='admin')return;
  let saved=requestedTheme;
  if(!saved){saved=state.user?.admin_service_theme||null;}
  if(!saved){try{saved=localStorage.getItem('ecoblack-admin-service-theme');}catch(_){saved=null;}}
  const theme=ADMIN_SERVICE_THEMES.includes(saved)?saved:'wine';
  document.body.classList.add(`admin-theme-${theme}`);
  const selector=$('adminServiceSelect');if(selector)selector.value=theme;
  try{localStorage.setItem('ecoblack-admin-service-theme',theme);}catch(_){/* Browser storage may be disabled; the wine default still applies. */}
}
async function changeAdminServiceTheme(theme){
  if(!ADMIN_SERVICE_THEMES.includes(theme)){showToast('Selecciona un servicio válido.','error');applyAdminServiceTheme('wine');return;}
  applyAdminServiceTheme(theme);
  const names={wine:'Administración · vino',basic:'Básico · verde',fast:'Rápido · azul',power:'Power · naranja',custom:'Personalizado · turquesa',bmax_pro:'Bmax Pro · violeta'};
  try{
    await api('/api/admin/service-theme',{method:'PUT',body:JSON.stringify({theme})});
    if(state.user)state.user.admin_service_theme=theme;
    if(state.profile)state.profile.admin_service_theme=theme;
    showToast(`Vista cambiada a ${names[theme]}. Se guardó en el perfil administrador.`, 'success');
  }catch(error){showToast(`Se aplicó el tema en este navegador, pero no se pudo guardar en el perfil: ${error.message}`,'error');}
}
function durationForPackage(pack=currentPackage()){if(pack.id==='bmax_pro')return 0;return pack.id==='custom'?Math.max(10,Math.min(120,parseInt($('customMinutes')?.value||pack.minutes,10)||pack.minutes)):pack.minutes;}
function pricingForPackage(pack=currentPackage()){
  const duration=durationForPackage(pack);const discount=Number(state.profile?.discount_percent??state.user?.discount_percent??pack.discount_percent??0);
  const base=pack.id==='bmax_pro'?0:(pack.id==='custom'?duration:Number(pack.base_price??pack.price??15));return {duration,base,discount:pack.id==='bmax_pro'?0:discount,final:Math.round(base*(1-(pack.id==='bmax_pro'?0:discount)/100)*100)/100};
}
function renderPackages(){
  if(!$('serviceGrid'))return;
  if(!state.packages.length&&!state.bmaxStatus){$('serviceGrid').innerHTML='<div class="muted">Cargando planes…</div>';return;}
  const regular=state.packages.filter(p=>p.id!=='bmax_pro');
  const b=state.bmaxStatus||{available_slots:0,sold_out:true,price_mxn:99,total_slots:10,sold_count:0,reserved_count:0};
  const activeId=state.currentPlan?.id||(b.has_purchased?'bmax_pro':null);
  const isAdmin=state.user?.role==='admin';
  const options=isAdmin?regular.map(p=>`<option value="${p.id}">${esc(p.name)} · ${p.minutes} min</option>`):activeId&&activeId!=='bmax_pro'?[`<option value="${activeId}">${esc(state.currentPlan?.name||activeId)} · plan comprado</option>`]:activeId==='bmax_pro'?['<option value="bmax_pro">Bmax Pro · VIP</option>']:['<option value="" selected disabled>Compra un plan en Servicios</option>'];
  if(isAdmin&&b.has_purchased)options.push('<option value="bmax_pro">Bmax Pro · VIP</option>');
  $('packageSelect').innerHTML=options.join('');
  if(activeId&&!isAdmin)$('packageSelect').value=activeId;
  if(!activeId&&!isAdmin)$('packageSelect').value='';
  $('packageSelect').disabled=!activeId&&!isAdmin;
  const cards=regular.map(p=>{
    const price=pricingForPackage(p);let stateBadge='',button='';
    if(activeId===p.id){stateBadge='<div class="service-badge plan-ready-badge">TU PLAN ACTIVO</div>';button=`<button class="primary glass-button full" data-select-package="${p.id}">Usar plan · elegir módulo</button>`;}
    else if(activeId){stateBadge='<div class="service-badge plan-locked-badge">BLOQUEADO POR PLAN ACTIVO</div>';button='<button class="outline-full glass-button full" disabled>Ya tienes otro plan activo</button>';}
    else if(isAdmin){button=`<button class="primary glass-button full" data-select-package="${p.id}">Seleccionar para prueba</button>`;}
    else{button=`<button class="primary glass-button full" data-buy-plan="${p.id}">Comprar ${esc(p.name)} · ${money(price.final)}</button>`;}
    return `<article class="glass service-card ${p.id==='fast'?'recommended':''}" data-package-card="${p.id}">${stateBadge}${p.id==='fast'&&!stateBadge?'<div class="service-badge">POPULAR</div>':''}<div class="service-symbol-wrap"><div class="service-mini-orbit"></div><div class="service-symbol">${p.id==='power'?'✦':p.id==='fast'?'ϟ':'◈'}</div></div><h3>${esc(p.name)}</h3><div class="service-price">${money(price.final)} <small>/ sesión</small></div><p class="muted">${esc(p.description)}</p><ul><li>✓ ${p.id==='custom'?'10–120 min, ajustables':`Hasta ${p.minutes} min de sesión`}</li><li>✓ Límite del paquete: ${Number(p.max_watts||10)} W</li><li>✓ Se compra antes de conectar</li></ul>${button}</article>`;
  }).join('');
  let stateBadge='',action='';
  if(b.has_purchased||activeId==='bmax_pro'){stateBadge='<div class="service-badge bmax-active-badge">ACCESO VIP ACTIVO</div>';action='<button class="primary glass-button full" data-select-package="bmax_pro">Entrar a módulos Bmax ⚡</button><p class="bmax-card-foot">Acceso permanente ligado a tu cuenta. Bmax Pro solo conecta en módulos Bmax.</p>';}
  else if(activeId){stateBadge='<div class="service-badge plan-locked-badge">COMPRA BLOQUEADA</div>';action='<button class="outline-full glass-button full" disabled>Primero consume tu plan actual</button><p class="bmax-card-foot">Cuando termine tu sesión podrás comprar Bmax Pro.</p>';}
  else if(b.reservation){stateBadge='<div class="service-badge bmax-hold-badge">CUPO APARTADO</div>';action=`<div class="bmax-reservation-count">Tu apartado vence en <b id="bmaxCountdown">05:00</b></div><button class="primary glass-button full" data-bmax-confirm>Confirmar compra · ${money(b.price_mxn)}</button><button class="outline-full glass-button full" data-bmax-cancel>Cancelar apartado</button>`;}
  else if(Number(b.available_slots)>0){stateBadge='<div class="service-badge">EDICIÓN LIMITADA</div>';action=`<button class="primary glass-button full" data-bmax-reserve>Apartar cupo 5 minutos</button><p class="bmax-card-foot">Aparta primero; confirma la compra con tu saldo EcoBlack para obtener acceso permanente.</p>`;}
  else{stateBadge='<div class="service-badge bmax-sold-badge">AGOTADO</div>';action='<button class="primary glass-button full" disabled>Agotado</button><p class="bmax-card-foot">No quedan cupos disponibles. Consulta más adelante.</p>';}
  const stats=`<div class="bmax-slot-line"><b>${Number(b.available_slots||0)}</b><span>cupos disponibles</span><small>${Number(b.sold_count||0)} vendidos · ${Number(b.reserved_count||0)} apartados</small></div>`;
  const bmaxCard=`<article class="glass service-card bmax-service-card ${activeId==='bmax_pro'?'bmax-owned-card':''}" data-package-card="bmax_pro">${stateBadge}<div class="bmax-service-halo"></div><div class="service-symbol-wrap bmax-symbol-wrap"><div class="service-mini-orbit"></div><div class="service-symbol">ϟ</div></div><div class="bmax-kicker">LIMITED BLACK SECTOR · VIP</div><h3>Bmax Pro</h3><div class="service-price">${b.has_purchased?'ACTIVO':money(b.price_mxn)} <small>${b.has_purchased?'/ acceso permanente':'/ acceso único'}</small></div><p class="muted">Acceso exclusivo a módulos negros y morados. Sesiones sin límite fijo de tiempo, con potencia ajustada al dispositivo.</p><ul><li>✓ Tope del paquete: hasta 100 W</li><li>✓ Solo módulos Bmax negros / morados</li><li>✓ El tiempo se estima según batería y dispositivo</li><li>✓ Acceso permanente en tu cuenta</li></ul>${stats}${action}</article>`;
  $('serviceGrid').innerHTML=cards+bmaxCard;
  renderActivePlan();
}
async function buyPlan(packageId){
  const button=document.querySelector(`[data-buy-plan="${CSS.escape(packageId)}"]`);if(button)button.disabled=true;
  try{
    const payload={package_id:packageId};if(packageId==='custom')payload.minutes=Math.max(10,Math.min(120,Number($('customMinutes')?.value||25)));
    const result=await api('/api/plans/purchase',{method:'POST',body:JSON.stringify(payload)});
    state.currentPlan=result.plan;state.user={...state.user,...(result.user||{}),active_plan:packageId};
    await loadProfile();await loadPackages();
    showToast(`${result.plan.name} comprado. Ya puedes conectar un módulo compatible.`,'success');
    setView('home');
  }catch(error){showToast(error.message,'error');await loadProfile().catch(()=>{});await loadPackages().catch(()=>{});}
  finally{const again=document.querySelector(`[data-buy-plan="${CSS.escape(packageId)}"]`);if(again)again.disabled=false;}
}

function startBmaxCountdown(){
  if(state.bmaxTimer)clearInterval(state.bmaxTimer);state.bmaxTimer=null;
  const tick=()=>{const expiry=Number(state.bmaxStatus?.reservation?.expires_at||0);if(!expiry)return;const remain=Math.max(0,Math.ceil(expiry-Date.now()/1000));const el=$('bmaxCountdown');if(el)el.textContent=`${String(Math.floor(remain/60)).padStart(2,'0')}:${String(remain%60).padStart(2,'0')}`;if(remain<=0){clearInterval(state.bmaxTimer);state.bmaxTimer=null;loadPackages().catch(()=>{});}};
  if(state.bmaxStatus?.reservation){tick();state.bmaxTimer=setInterval(tick,1000);}
}
async function reserveBmax(){try{await api('/api/bmax/reserve',{method:'POST',body:JSON.stringify({})});await loadPackages();showToast('Cupo apartado durante 5 minutos. Confirma antes de que venza.','success');}catch(error){showToast(error.message,'error');await loadPackages().catch(()=>{});}}
async function cancelBmaxReservation(){try{await api('/api/bmax/cancel',{method:'POST',body:JSON.stringify({})});await loadPackages();showToast('El apartado se canceló y el cupo volvió a estar disponible.','info');}catch(error){showToast(error.message,'error');}}
async function playBmaxActivation(){
  const overlay=$('bmaxPurchaseFx');if(!overlay)return;const bar=$('bmaxActivationProgress');$('bmaxActivationTitle').textContent='Activando Bmax Pro';$('bmaxActivationText').textContent='Compra confirmada · vinculando el acceso a tu cuenta';bar.style.width='0%';overlay.classList.remove('hidden');
  const start=performance.now(),duration=window.matchMedia?.('(prefers-reduced-motion: reduce)').matches?500:1900;
  await new Promise(resolve=>{const frame=now=>{const pct=Math.min(100,(now-start)/duration*100);bar.style.width=`${pct}%`;if(pct<100)requestAnimationFrame(frame);else resolve();};requestAnimationFrame(frame);});
  $('bmaxActivationTitle').textContent='Bmax Pro activado';$('bmaxActivationText').textContent='Tu acceso exclusivo ya está listo.';await new Promise(r=>setTimeout(r,450));overlay.classList.add('hidden');
}
async function confirmBmaxPurchase(){
  const button=document.querySelector('[data-bmax-confirm]');if(button)button.disabled=true;
  try{const result=await api('/api/bmax/purchase',{method:'POST',body:JSON.stringify({})});state.bmaxStatus=result.bmax;if(state.user)state.user.balance=result.balance;if(state.profile)state.profile.balance=result.balance;await playBmaxActivation();await loadProfile();await loadPackages();showToast('Bmax Pro quedó activado en tu cuenta.','success');}
  catch(error){showToast(error.message,'error');await loadPackages().catch(()=>{});}
  finally{const again=document.querySelector('[data-bmax-confirm]');if(again)again.disabled=false;}
}

function playPackageSelectionAnimation(packageId=currentPackage().id){
  const pack=state.packages.find(p=>p.id===packageId)||currentPackage();
  const price=pricingForPackage(pack);
  document.querySelectorAll('[data-package-card]').forEach(card=>card.classList.toggle('package-selected',card.dataset.packageCard===pack.id));
  const fx=$('packageSelectionFx');if(!fx)return;
  $('packageFxName').textContent=pack.name;
  $('packageFxDetail').textContent=`hasta ${price.duration} min · hasta ${Number(pack.max_watts||10)} W · ${money(price.final)}`;
  fx.classList.add('hidden');fx.classList.remove('package-fx-play','package-fx-out');
  void fx.offsetWidth;fx.classList.remove('hidden');fx.classList.add('package-fx-play');
  if(state.packageAnimationTimer)clearTimeout(state.packageAnimationTimer);
  state.packageAnimationTimer=setTimeout(()=>{fx.classList.add('package-fx-out');setTimeout(()=>{fx.classList.add('hidden');fx.classList.remove('package-fx-play','package-fx-out');},260);},1150);
  window.setTimeout(()=>{document.querySelectorAll('[data-package-card]').forEach(card=>card.classList.remove('package-selected'));const selected=document.querySelector(`[data-package-card="${CSS.escape(pack.id)}"]`);selected?.classList.add('package-selected');},0);
}

function waitForAnimation(ms){return new Promise(resolve=>setTimeout(resolve,ms));}
function prepareConnectionFlight(core, sourceRing){
  const dock=$('activeSessionDock'),targetRing=$('activeBatteryRing');
  // Temporarily expose the dock invisibly so its real responsive position can be measured.
  dock.classList.remove('hidden');dock.style.visibility='hidden';
  const target=targetRing.getBoundingClientRect();
  dock.classList.add('hidden');dock.style.removeProperty('visibility');
  const source=sourceRing.getBoundingClientRect(),container=core.getBoundingClientRect();
  const scale=Math.max(.08,Math.min(.35,target.width/Math.max(1,source.width)));
  const cx=container.left+container.width/2,cy=container.top+container.height/2;
  const sx=source.left+source.width/2,sy=source.top+source.height/2;
  const tx=target.left+target.width/2,ty=target.top+target.height/2;
  const dx=tx-cx-scale*(sx-cx),dy=ty-cy-scale*(sy-cy);
  core.style.setProperty('--flight-x',`${dx}px`);core.style.setProperty('--flight-y',`${dy}px`);core.style.setProperty('--flight-scale',String(scale));
}
async function playConnectionAnimation(module, session, device, price){
  state.connectionAnimationActive=true;
  const overlay=$('connectionAnimation'),core=$('connectionAnimationCore'),ring=$('connectionBatteryRing');
  const token=++state.connectionAnimationToken;
  const startBattery=Math.max(0,Math.min(100,Number(session?.battery_start??state.lastEstimate?.battery??$('batteryLevel').value??35)));
  $('connectionModuleName').textContent=module?.name||session?.module_name||'Módulo EcoBlack';
  $('connectionBatteryPercent').textContent=`${startBattery}%`;
  const connectionLabel=document.querySelector('.connection-label');
  const connectionFootnote=document.querySelector('.connection-footnote');
  const isBmaxPackage=price?.pack?.id==='bmax_pro';
  const isBmaxModule=module?.module_type==='bmax'||session?.module_type==='bmax';
  overlay.classList.toggle('bmax-connection',isBmaxPackage&&!isBmaxModule);
  overlay.classList.toggle('bmax-module-connection',isBmaxModule);
  $('connectionPhaseText').textContent=isBmaxModule?'BMAX MODULE · NÚCLEO VIOLETA ACTIVADO':isBmaxPackage?'BMAX PRO · CARGA ADAPTATIVA HASTA EL 100%':`Preparando ${device?.name||'tu celular'} · ${price?.pack?.name||'paquete EcoBlack'}`;
  if(connectionLabel)connectionLabel.innerHTML=isBmaxModule?'<i></i> BMAX VIP · SPECTRAL CORE':'<i></i> ECOBLACK · CONEXIÓN SEGURA';
  if(connectionFootnote)connectionFootnote.textContent=isBmaxModule?'SINCRONIZANDO NÚCLEO VIOLETA':'PREPARANDO TU ENERGÍA';
  $('connectionProgressBar').style.width='0%';ring.style.setProperty('--connection-sweep','0%');
  const particles=$('connectionParticles');particles.innerHTML='';
  for(let i=0;i<42;i++){
    const spark=document.createElement('span');spark.className='connection-spark';
    spark.style.setProperty('--a',`${(360/42)*i+Math.random()*5}deg`);
    spark.style.setProperty('--r',`${125+Math.random()*115}px`);
    spark.style.setProperty('--d',`${.65+Math.random()*.9}s`);
    spark.style.setProperty('--delay',`${Math.random()*.35}s`);
    spark.textContent=i%6===0?'ϟ':'·';particles.appendChild(spark);
  }
  core.style.removeProperty('--flight-x');core.style.removeProperty('--flight-y');core.style.removeProperty('--flight-scale');
  overlay.classList.remove('hidden','is-collapsing','is-running');
  overlay.setAttribute('aria-hidden','false');
  void core.offsetWidth;overlay.classList.add('is-running');
  const reduceMotion=window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  const duration=reduceMotion?800:2150;
  const start=performance.now();
  const progressFrame=now=>{
    if(token!==state.connectionAnimationToken)return;
    const pct=Math.min(100,((now-start)/duration)*100);
    $('connectionProgressBar').style.width=`${pct}%`;ring.style.setProperty('--connection-sweep',`${pct}%`);
    if(pct<100)requestAnimationFrame(progressFrame);
  };
  requestAnimationFrame(progressFrame);
  const phaseTimers=[];
  phaseTimers.push(setTimeout(()=>{if(token===state.connectionAnimationToken)$('connectionPhaseText').textContent='Módulo reservado · iniciando transferencia';},620));
  phaseTimers.push(setTimeout(()=>{if(token===state.connectionAnimationToken)$('connectionPhaseText').textContent='Sincronizando energía con tu dispositivo';},1250));
  await waitForAnimation(duration);
  if(token!==state.connectionAnimationToken)return;
  prepareConnectionFlight(core,ring);
  overlay.classList.remove('is-running');overlay.classList.add('is-collapsing');
  await waitForAnimation(reduceMotion?150:780);
  if(token!==state.connectionAnimationToken)return;
  overlay.classList.add('hidden');overlay.classList.remove('is-collapsing');overlay.setAttribute('aria-hidden','true');
  state.connectionAnimationActive=false;
}

function updatePriceSummary(){
  if(!$('packageSelect'))return;const pack=currentPackage(),price=pricingForPackage(pack);
  $('priceBase').textContent=money(price.base);$('priceDiscount').textContent=`${price.discount}%`;$('priceFinal').textContent=money(price.final);
  $('customMinutesWrap').classList.toggle('hidden',pack.id!=='custom');
  if($('priceBase'))$('priceBase').textContent=pack.id==='bmax_pro'?'Incluido':money(price.base);
  if($('priceDiscount'))$('priceDiscount').textContent=pack.id==='bmax_pro'?'—':`${price.discount}%`;
  if($('priceFinal'))$('priceFinal').textContent=pack.id==='bmax_pro'?'$0.00':money(price.final);
  const available=Number(state.profile?.balance??state.user?.balance??0);$('priceFinal').classList.toggle('price-warning',available<price.final);
  updateModalPrice();
}
function selectedDevice(){return normalizeDevice(state.devices.find(d=>String(d.id)===$('deviceSelect').value)||state.devices[0]||DEVICE_PRESETS[0]);}
function nearestAvailableModule(){if(state.nearestModule?.status==='available')return state.nearestModule;const origin=originPoint();return state.modules.filter(m=>m.status==='available').map(m=>({...m,_d:distanceKm(origin.lat,origin.lng,m.lat,m.lng)})).sort((a,b)=>a._d-b._d)[0]||state.modules[0]||null;}
function parseWatts(text){const n=parseFloat(String(text||'').replace(',','.'));return Number.isFinite(n)?n:30;}
function estimateCharging(device=selectedDevice(),battery=Number($('batteryLevel').value),module=(state.selectedModule||nearestAvailableModule())){
  const pack=currentPackage();
  const moduleWatts=module?parseWatts(module.power):45;
  const isBmax=pack.id==='bmax_pro';
  const packageMaxWatts=Math.max(3,Number(pack.max_watts)||(isBmax?100:10));
  const phoneMaxWatts=Math.max(3,Number(device.maxWatts)||25);
  const connector=$('connectorSelect').value;
  const connectorCaps={'USB-C':240,'Lightning':20,'Micro-USB':12,'Inalámbrico':15};
  const efficiencies={'USB-C':.78,'Lightning':.75,'Micro-USB':.70,'Inalámbrico':.68};
  const effectiveWatts=Math.max(3,Math.min(packageMaxWatts,phoneMaxWatts,moduleWatts,connectorCaps[connector]||240));
  const efficiency=efficiencies[connector]||.78;
  const capacityWh=(Number(device.batteryMah)||4200)*3.85/1000;
  const targetBattery=isBmax?100:90;
  const remaining=Math.max(0,targetBattery-battery);
  const energyWh=capacityWh*remaining/100;
  let targetMinutes=1,sessionMinutes=1,packageLimit=durationForPackage(pack),estimatedEndBattery=battery;
  if(energyWh>0&&isBmax){
    let level=battery,total=0;while(level<100){const next=Math.min(100,level+1),mid=(level+next)/2;const taper=mid<50?.78:mid<75?.68:mid<90?.48:mid<97?.30:.18;const stepWh=capacityWh*(next-level)/100;total+=stepWh/Math.max(.1,effectiveWatts*efficiency*taper)*60;level=next;}
    targetMinutes=Math.max(1,Math.ceil(total+3));sessionMinutes=targetMinutes;packageLimit=targetMinutes;estimatedEndBattery=100;
  }else if(energyWh>0){
    const taper=battery<50?.78:(battery<75?.68:.56);targetMinutes=Math.max(1,Math.ceil(energyWh/Math.max(.1,effectiveWatts*efficiency*taper)*60+2));packageLimit=durationForPackage(pack);sessionMinutes=Math.max(1,Math.min(targetMinutes,packageLimit));const delivered=Math.max(0,sessionMinutes-2)/60*effectiveWatts*efficiency*taper;estimatedEndBattery=Math.min(90,Math.max(battery,Math.floor(battery+delivered/Math.max(.1,capacityWh)*100)));
  }
  return {minutes:sessionMinutes,session_minutes:sessionMinutes,minutes_to_target:targetMinutes,estimated_minutes:targetMinutes,package_limit_minutes:packageLimit,package_max_watts:packageMaxWatts,watts:effectiveWatts,effective_watts:effectiveWatts,estimated_end_battery:estimatedEndBattery,battery_end_estimate:estimatedEndBattery,target_reached_in_session:isBmax||targetMinutes<=packageLimit,remaining,battery,target_battery:targetBattery,device,connector,module};
}

function updateEstimate(){
  if(!$('batteryLevel')||!state.devices.length)return;
  const result=estimateCharging();const pack=currentPackage();
  $('batteryValue').textContent=`${result.battery}%`;$('startPct').textContent=`${result.battery}%`;
  $('chargeTime').textContent=pack.id==='bmax_pro'?`~${result.minutes_to_target} min`:`${result.session_minutes} min`;
  const targetText=pack.id==='bmax_pro'?`Bmax Pro no fija minutos: estima el tiempo hasta el 100% según la batería y el dispositivo.`:(result.target_reached_in_session?`Se estima llegar al 90% en ${result.minutes_to_target} min.`:`El límite de este paquete es ${result.package_limit_minutes} min; para llegar al 90% se estiman ${result.minutes_to_target} min.`);
  $('chargeText').textContent=`${result.device.name} · ${pack.name}: hasta ${result.package_max_watts} W. Potencia estimada aplicada: ${result.watts} W (limitada por módulo, conector y celular). ${targetText} Batería estimada al terminar: ${result.estimated_end_battery}%. Cálculo orientativo, no controla físicamente el cargador.`;
  $('chargeMeter').style.width=`${pack.id==='bmax_pro'?100:Math.min(100,result.session_minutes/Math.max(1,result.package_limit_minutes)*100)}%`;$('estimateBatteryPercent').textContent=`${result.battery}%`;
  $('estimateRing').style.setProperty('--battery-level',`${result.battery}%`);
  if($('chargeWatts'))$('chargeWatts').textContent=`${result.watts} W / ${result.package_max_watts} W`;
  if($('chargeTargetLabel'))$('chargeTargetLabel').textContent=pack.id==='bmax_pro'?'Tiempo estimado hasta 100%':'Tiempo para 90%';
  if($('chargeTargetTime'))$('chargeTargetTime').textContent=result.minutes_to_target===1&&result.battery>=result.target_battery?'Ya alcanzada':`${result.minutes_to_target} min aprox.`;
  if($('chargeEndBattery'))$('chargeEndBattery').textContent=`${result.estimated_end_battery}%`;
  state.lastEstimate={...result};updatePriceSummary();
}

function moduleHasValidLocation(module){
  const lat=Number(module?.lat),lng=Number(module?.lng);
  return Number.isFinite(lat)&&Number.isFinite(lng)&&lat>=-90&&lat<=90&&lng>=-180&&lng<=180&&!(Math.abs(lat)<1e-9&&Math.abs(lng)<1e-9);
}
async function loadModules(){
  const data=await api('/api/modules');
  state.modules=(Array.isArray(data)?data:(data.modules||[])).map(module=>({...module,lat:Number.parseFloat(module.lat),lng:Number.parseFloat(module.lng)}));
  const located=state.modules.filter(moduleHasValidLocation).length;
  $('mapStatus').textContent=`${located} de ${state.modules.length} módulos ubicados`;
  updateNearest();renderAdminModules();if(state.mapVisible)renderMapMarkers();
}
function distanceKm(a,b,c,d){const rad=Math.PI/180,dx=(c-a)*rad,dy=(d-b)*rad,q=Math.sin(dx/2)**2+Math.cos(a*rad)*Math.cos(c*rad)*Math.sin(dy/2)**2;return 6371*2*Math.asin(Math.sqrt(q));}
function originPoint(){return state.userLocation||{lat:21.1619,lng:-86.8515};}
function updateNearest(){
  const locatedModules=state.modules.filter(moduleHasValidLocation);
  if(!locatedModules.length){$('nearestName').textContent=state.modules.length?'Ubicación pendiente':'Sin módulos';$('nearestMeta').textContent=state.modules.length?'El administrador debe ubicar los módulos desde el panel admin.':'El administrador aún no ha agregado módulos.';$('nearestDistance').textContent='—';$('nearestPower').textContent='—';state.nearestModule=null;return;}
  const origin=originPoint();const order=locatedModules.map(m=>({...m,_d:distanceKm(origin.lat,origin.lng,m.lat,m.lng)})).sort((a,b)=>((a.status==='available'?0:1)-(b.status==='available'?0:1))||a._d-b._d);
  state.nearestModule=order[0];if(state.selectedModule){const refreshed=state.modules.find(m=>String(m.id)===String(state.selectedModule.id));if(refreshed)state.selectedModule=refreshed;} $('nearestName').textContent=order[0].name;$('nearestDistance').textContent=order[0]._d.toFixed(1);$('nearestPower').textContent=order[0].power;$('nearestMeta').textContent=`${order[0].status==='available'?'Disponible':'Ocupado'} · ${order[0].connectors.join(' · ')}`;
}
function moduleMarkerElement(module){
  // MapLibre applies the geographic translate transform to the supplied root
  // element. Keep the visual button as a child so its CSS cannot override that
  // transform and send every marker to the top-left corner.
  const anchor=document.createElement('div');
  anchor.className='ecoblack-map-marker-anchor';
  anchor.setAttribute('data-module-anchor',String(module.id));
  const button=document.createElement('button');button.type='button';
  const isBmax=module.module_type==='bmax';
  button.className=`ecoblack-map-marker ${module.status==='busy'?'busy':'available'} ${isBmax?'bmax-module-marker':''}`;
  button.title=`${module.name} · ${isBmax?'Bmax · ':''}${module.status==='busy'?'Ocupado':'Disponible'}`;
  button.innerHTML=isBmax?`<span class="marker-pulse"></span><span class="bmax-marker-orbit"></span><span class="marker-bolt">ϟ</span><span class="marker-label">${esc(module.name)}</span>`:`<span class="marker-pulse"></span><span class="marker-bolt">⚡</span><span class="marker-label">${esc(module.name)}</span>`;
  button.addEventListener('click',event=>{event.stopPropagation();openModule(module)});
  anchor.appendChild(button);
  return anchor;
}
function clusterModulesForZoom(){
  const zoom=state.map.getZoom();
  const level=zoom<=5.5?'state':zoom<=10.5?'city':'module';
  if(level==='module')return {level,groups:[]};
  const buckets=new Map();
  for(const module of state.modules.filter(moduleHasValidLocation)){
    const stateName=String(module.state||'').trim();const city=String(module.city||'').trim();
    let key,label;
    if(level==='state'){
      if(stateName){key=`state:${stateName.toLocaleLowerCase()}`;label=stateName;}
      else{const gx=Math.floor(Number(module.lng)/8),gy=Math.floor(Number(module.lat)/8);key=`state-grid:${gx}:${gy}`;label='Zona regional';}
    }else{
      if(city){key=`city:${(city+'|'+stateName).toLocaleLowerCase()}`;label=city+(stateName?`, ${stateName}`:'');}
      else{const gx=Math.floor(Number(module.lng)/.38),gy=Math.floor(Number(module.lat)/.38);key=`city-grid:${gx}:${gy}`;label='Zona local';}
    }
    if(!buckets.has(key))buckets.set(key,{key,label,level,members:[],latSum:0,lngSum:0});
    const group=buckets.get(key);group.members.push(module);group.latSum+=Number(module.lat);group.lngSum+=Number(module.lng);
  }
  return {level,groups:[...buckets.values()].map(g=>({...g,lat:g.latSum/g.members.length,lng:g.lngSum/g.members.length}))};
}
function clusterMarkerElement(group){
  const el=document.createElement('button');el.type='button';
  const total=group.members.length,busy=group.members.filter(m=>m.status==='busy').length;
  el.className=`ecoblack-map-cluster ${group.level==='state'?'state-cluster':'city-cluster'}`;
  el.innerHTML=`<span class="cluster-count">${total}</span><span class="cluster-label">${esc(group.label)}</span><small>${busy?`${busy} ocupados · `:''}${total} módulos</small>`;
  el.title=`${group.label}: ${total} módulos. Acerca el mapa para ver ubicaciones individuales.`;
  el.addEventListener('click',event=>{event.stopPropagation();const nextZoom=group.level==='state'?7.8:12.2;if(state.mapEngine==='maplibre')state.map.flyTo({center:[group.lng,group.lat],zoom:nextZoom,duration:850});else state.map.flyTo([group.lat,group.lng],nextZoom,{duration:.85});});
  return el;
}
function ensureMap(){
  if(state.map)return;
  sync3DControl();
  $('map').innerHTML='';state.mapLoaded=false;state.mapErrorCount=0;
  if(window.maplibregl){
    try{
      state.mapEngine='maplibre';
      state.map=new maplibregl.Map({container:'map',style:'https://tiles.openfreemap.org/styles/liberty',center:state.mapCenter,zoom:5.2,pitch:state.map3d?56:0,bearing:state.map3d?-18:0,antialias:true,maxPitch:75,attributionControl:true,renderWorldCopies:false});
      state.map.addControl(new maplibregl.NavigationControl({visualizePitch:true}),'top-right');
      state.map.on('load',()=>{state.mapLoaded=true;add3DBuildings();sync3DControl();resizeMap();fitMapToNetwork();renderMapMarkers();clearTimeout(state.mapFallbackTimer);$('mapFallback').classList.add('hidden');});
      state.map.on('moveend',()=>{const c=state.map.getCenter();state.mapCenter=[c.lng,c.lat]});state.map.on('zoomend',renderMapMarkers);
      state.map.on('click',event=>handleMapPick(event.lngLat.lat,event.lngLat.lng));
      state.map.on('error',event=>{console.warn('MapLibre tile/style error',event?.error||event);state.mapErrorCount++;if(state.mapErrorCount>=6&&!state.mapLoaded)switchToLeafletMap();else if(state.mapErrorCount>=12&&state.mapLoaded)switchToLeafletMap();});
      state.mapFallbackTimer=setTimeout(()=>{if(!state.mapLoaded&&state.mapEngine==='maplibre')switchToLeafletMap();},12000);
      return;
    }catch(error){console.warn('MapLibre could not initialize',error);state.map=null;state.mapEngine=null;}
  }
  createLeafletMap();
}
function add3DBuildings(){
  if(!state.map||state.mapEngine!=='maplibre')return;
  try{
    const style=state.map.getStyle();
    const styleLayers=style.layers||[];
    // Some vector styles ship their own 3D buildings. Keep all of those hidden
    // while 2D mode is selected, and reveal them only after explicit opt-in.
    state.nativeBuilding3DLayers=styleLayers
      .filter(layer=>layer.type==='fill-extrusion' && ((layer['source-layer']||'').toLowerCase().includes('building') || (layer.id||'').toLowerCase().includes('building')))
      .map(layer=>layer.id);
    for(const layerId of state.nativeBuilding3DLayers){
      try{state.map.setLayoutProperty(layerId,'visibility',state.map3d?'visible':'none');}catch(_){}
    }
    // Reuse the exact vector source used by the current city style when possible.
    let buildingSourceId=styleLayers.find(layer=>layer['source-layer']==='building')?.source;
    if(!buildingSourceId){
      buildingSourceId=Object.keys(style.sources||{}).find(id=>{
        const src=style.sources[id];
        return src?.type==='vector'&&String(src.url||'').includes('openfreemap');
      });
    }
    if(!buildingSourceId){
      buildingSourceId='ecoblack-openfreemap';
      if(!state.map.getSource(buildingSourceId))state.map.addSource(buildingSourceId,{type:'vector',url:'https://tiles.openfreemap.org/planet'});
    }
    const labelLayer=styleLayers.find(layer=>layer.type==='symbol'&&layer.layout&&layer.layout['text-field']);
    const beforeId=labelLayer?.id;

    // Always draw available building footprints. Some homes have no height tags,
    // but their outlines can still be displayed as 2D polygons.
    if(!state.map.getLayer('ecoblack-building-footprints')){
      state.map.addLayer({
        id:'ecoblack-building-footprints',source:buildingSourceId,'source-layer':'building',type:'fill',minzoom:11.5,
        paint:{
          'fill-color':['coalesce',['get','colour'],'#cbd2bc'],
          'fill-opacity':['interpolate',['linear'],['zoom'],11.5,.12,12.5,.4,13.5,.58,15.5,.35],
          'fill-outline-color':'#727b66'
        }
      },beforeId);
    }
    if(!state.map.getLayer('ecoblack-buildings-3d') && state.nativeBuilding3DLayers.length===0){
      const heightExpr=['coalesce',['to-number',['get','render_height']],['to-number',['get','height']],['*',['to-number',['get','building:levels'],2],3.1],5.5];
      const baseExpr=['coalesce',['to-number',['get','render_min_height']],['to-number',['get','min_height']],0];
      state.map.addLayer({
        id:'ecoblack-buildings-3d',source:buildingSourceId,'source-layer':'building',type:'fill-extrusion',minzoom:12.8,
        layout:{visibility:state.map3d?'visible':'none'},
        paint:{
          'fill-extrusion-color':['case',['has','colour'],['get','colour'],['interpolate',['linear'],heightExpr,0,'#858d73',12,'#aab397',40,'#c2cbb0',120,'#e0e4cf']],
          'fill-extrusion-height':['interpolate',['linear'],['zoom'],12.8,0,13.8,heightExpr,15.5,heightExpr],
          'fill-extrusion-base':baseExpr,
          'fill-extrusion-opacity':.96,
          'fill-extrusion-vertical-gradient':true
        }
      },beforeId);
    }
    if(state.map.getLayer('ecoblack-buildings-3d'))state.map.setLayoutProperty('ecoblack-buildings-3d','visibility',state.map3d?'visible':'none');
    if(typeof state.map.setLight==='function')state.map.setLight({anchor:'viewport',color:'#fff4d0',intensity:.45,position:[1.15,210,38]});
  }catch(error){console.warn('No se pudieron activar todas las huellas/edificios 3D',error);}
}

function switchToLeafletMap(){
  if(state.mapEngine==='leaflet'||!window.L)return;
  clearTimeout(state.mapFallbackTimer);try{state.map?.remove();}catch(_){}state.map=null;state.mapEngine='leaflet';state.mapLoaded=true;state.map3d=false;$('map').innerHTML='';
  try{createLeafletMap();showToast('Se está usando el mapa compatible.','info');}catch(error){console.error(error);showMapFallback();}
}
function createLeafletMap(){
  if(!window.L){showMapFallback();return;}
  state.mapEngine='leaflet';state.mapLoaded=true;state.map=L.map('map',{zoomControl:true,preferCanvas:true}).setView([state.mapCenter[1],state.mapCenter[0]],5.2);
  state.mapTileLayer=L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'© OpenStreetMap contributors'}).addTo(state.map);sync3DControl();
  let errors=0;state.mapTileLayer.on('tileerror',()=>{errors++;if(errors>=4){showMapFallback();}});state.map.on('moveend',()=>{const c=state.map.getCenter();state.mapCenter=[c.lng,c.lat]});state.map.on('zoomend',renderMapMarkers);state.map.on('click',event=>handleMapPick(event.latlng.lat,event.latlng.lng));fitMapToNetwork();renderMapMarkers();setTimeout(resizeMap,100);
}
function fitMapToNetwork(){
  if(!state.map||!state.mapLoaded)return;
  const items=state.modules.filter(moduleHasValidLocation);
  if(!items.length)return;
  if(items.length===1){
    const only=items[0];
    if(state.mapEngine==='maplibre')state.map.jumpTo({center:[only.lng,only.lat],zoom:13.5});
    else state.map.setView([only.lat,only.lng],13.5,{animate:false});
    return;
  }
  const lats=items.map(item=>Number(item.lat)),lngs=items.map(item=>Number(item.lng));
  const south=Math.min(...lats),north=Math.max(...lats),west=Math.min(...lngs),east=Math.max(...lngs);
  const span=Math.max(north-south,east-west);
  const maxZoom=span<0.2?14:span<1.5?11.5:span<5?8.5:5.2;
  const bounds=[[west,south],[east,north]];
  if(Math.abs(north-south)<1e-6&&Math.abs(east-west)<1e-6){
    const point=items[0];
    if(state.mapEngine==='maplibre')state.map.jumpTo({center:[point.lng,point.lat],zoom:maxZoom});
    else state.map.setView([point.lat,point.lng],maxZoom,{animate:false});
    return;
  }
  try{
    if(state.mapEngine==='maplibre')state.map.fitBounds(bounds,{padding:{top:65,bottom:65,left:65,right:65},maxZoom,duration:0});
    else state.map.fitBounds([[south,west],[north,east]],{padding:[48,48],maxZoom,animate:false});
  }catch(error){console.warn('No se pudo ajustar el mapa a la red',error);}
}

async function handleMapPick(lat,lng){
  if(!state.adminPickMode||state.user?.role!=='admin')return;
  const fixedLat=Number(lat.toFixed(6)),fixedLng=Number(lng.toFixed(6));
  if(state.adminRelocateModuleId){
    const module=state.modules.find(item=>String(item.id)===String(state.adminRelocateModuleId));
    if(!module){state.adminRelocateModuleId=null;state.adminPickMode=false;showToast('No se encontró el módulo que deseas ubicar.','error');setView('admin');return;}
    try{
      await api(`/api/modules/${encodeURIComponent(module.id)}`,{method:'PUT',body:JSON.stringify({...module,lat:fixedLat,lng:fixedLng,connectors:module.connectors})});
      state.adminRelocateModuleId=null;state.adminPickMode=false;await loadModules();setView('admin');
      showToast(`Ubicación de ${module.name} corregida.`, 'success');
    }catch(error){showToast(error.message,'error');}
    return;
  }
  $('moduleLat').value=fixedLat.toFixed(6);$('moduleLng').value=fixedLng.toFixed(6);
  $('pickLocationStatus').textContent=`Punto seleccionado: ${fixedLat.toFixed(5)}, ${fixedLng.toFixed(5)}`;
  state.adminPickMode=false;setView('admin');showToast('Ubicación elegida. Ahora guarda el módulo.','success');
}
function showMapFallback(){
  $('mapFallback').classList.remove('hidden');$('mapFallback').innerHTML=`<div><b>Mapa alternativo EcoBlack</b><span>El proveedor de mapas no responde. Los módulos siguen funcionando; revisa Internet y vuelve a intentar.</span><button id="retryMapBtn" class="secondary glass-button">Reintentar mapa</button><div class="fallback-module-list">${state.modules.map(m=>`<button data-focus-module="${esc(m.id)}">${esc(m.name)} · ${m.status==='busy'?'Ocupado':'Disponible'}</button>`).join('')}</div></div>`;
  $('retryMapBtn').addEventListener('click',retryMap);
}
function hideMapFallback(){$('mapFallback').classList.add('hidden');}
function clearMapMarkers(){for(const marker of state.mapMarkers){try{if(state.mapEngine==='maplibre')marker.remove();else marker.remove();}catch(_){}}state.mapMarkers=[];}
function renderMapMarkers(){
  if(!state.mapVisible||!state.map||!state.mapLoaded)return;clearMapMarkers();if(state.userMarker){try{state.userMarker.remove();}catch(_){}state.userMarker=null;}hideMapFallback();
  const locatedModules=state.modules.filter(moduleHasValidLocation);
  const clustered=clusterModulesForZoom();
  if(clustered.level!=='module'){
    for(const group of clustered.groups){
      const el=clusterMarkerElement(group);
      if(state.mapEngine==='maplibre'){
        const marker=new maplibregl.Marker({element:el,anchor:'center'}).setLngLat([group.lng,group.lat]).addTo(state.map);state.mapMarkers.push(marker);
      }else if(state.mapEngine==='leaflet'){
        const icon=L.divIcon({className:'ecoblack-leaflet-cluster',html:el.outerHTML,iconSize:[94,66],iconAnchor:[47,33]});const marker=L.marker([group.lat,group.lng],{icon,title:el.title}).addTo(state.map);marker.on('click',()=>{const z=group.level==='state'?7.8:12.2;state.map.flyTo([group.lat,group.lng],z,{duration:.85});});state.mapMarkers.push(marker);
      }
    }
  }else{
    for(const module of locatedModules){
      if(state.mapEngine==='maplibre'){
        const marker=new maplibregl.Marker({element:moduleMarkerElement(module),anchor:'center'}).setLngLat([module.lng,module.lat]).addTo(state.map);state.mapMarkers.push(marker);
      }else if(state.mapEngine==='leaflet'){
        const el=moduleMarkerElement(module);const icon=L.divIcon({className:'ecoblack-leaflet-marker',html:el.outerHTML,iconSize:[34,34],iconAnchor:[17,17]});const marker=L.marker([module.lat,module.lng],{icon,title:module.name}).addTo(state.map);marker.on('click',()=>openModule(module));state.mapMarkers.push(marker);
      }
    }
  }
  if(state.userLocation){
    if(state.mapEngine==='maplibre'){const el=document.createElement('div');el.className='user-map-marker';state.userMarker=new maplibregl.Marker({element:el}).setLngLat([state.userLocation.lng,state.userLocation.lat]).addTo(state.map);}
    else if(state.mapEngine==='leaflet')state.userMarker=L.circleMarker([state.userLocation.lat,state.userLocation.lng],{radius:8,color:'#dfff00',fillColor:'#dfff00',fillOpacity:.95}).addTo(state.map);
  }
  const count=locatedModules.length;const detail=clustered.level==='state'?'por estado':clustered.level==='city'?'por ciudad':'ubicaciones precisas';$('mapStatus').textContent=`${count} módulos · ${detail}`;
  resizeMap();
}

function resizeMap(){setTimeout(()=>{if(state.map&&typeof state.map.resize==='function'&&state.mapEngine==='maplibre')state.map.resize();else if(state.map&&state.mapEngine==='leaflet')state.map.invalidateSize({pan:false});},100);}
function focusMapAt(lng,lat,zoom=16){
  const focus=()=>{
    if(!state.map)return;
    try{
      if(state.mapEngine==='maplibre'){
        state.map.resize();
        state.map.flyTo({center:[Number(lng),Number(lat)],zoom,pitch:state.map3d?56:0,duration:850});
      }else if(state.mapEngine==='leaflet'){
        state.map.invalidateSize({pan:false});
        state.map.setView([Number(lat),Number(lng)],zoom,{animate:true,duration:.85});
      }
      setTimeout(()=>renderMapMarkers(),180);
    }catch(error){console.warn('No se pudo enfocar el módulo en el mapa',error);}
  };
  if(!state.map){ensureMap();}
  if(state.mapEngine==='maplibre'&&!state.mapLoaded){state.map.once('load',focus);return;}
  requestAnimationFrame(()=>setTimeout(focus,80));
}
function toggleMap(visible){state.mapVisible=visible;$('map').classList.toggle('hidden',!visible);$('mapPlaceholder').classList.toggle('hidden',visible);document.querySelector('.map-controls')?.classList.toggle('hidden',!visible);$('toggleMapBtn').textContent=visible?'Ocultar mapa':'Mostrar mapa';if(visible){ensureMap();setTimeout(()=>{resizeMap();renderMapMarkers();},130);}}
function retryMap(){clearTimeout(state.mapFallbackTimer);try{state.map?.remove();}catch(_){}state.map=null;state.mapEngine=null;state.mapLoaded=false;state.mapErrorCount=0;$('mapFallback').classList.add('hidden');$('map').innerHTML='';ensureMap();}
function sync3DControl(){
  const button=$('toggle3dBtn');if(!button)return;
  const supported=state.mapEngine==='maplibre'&&!!state.map;
  button.disabled=!supported;
  button.setAttribute('aria-pressed',String(!!state.map3d));
  button.textContent=!supported?'3D no disponible':state.map3d?'3D activado':'Activar edificios 3D';
  button.classList.toggle('active',!!state.map3d&&supported);
  const description=document.querySelector('.map-controls span');if(description)description.textContent=state.map3d?'Edificios extruidos activados':'Vista 2D · edificios 3D apagados';
}
function set3D(enabled){
  if(state.mapEngine!=='maplibre'||!state.map){
    state.map3d=false;sync3DControl();showToast('Los edificios 3D requieren el mapa vectorial MapLibre.','info');return;
  }
  state.map3d=!!enabled;
  try{
    if(state.map.getLayer('ecoblack-buildings-3d'))state.map.setLayoutProperty('ecoblack-buildings-3d','visibility',state.map3d?'visible':'none');
    for(const layerId of state.nativeBuilding3DLayers){if(state.map.getLayer(layerId))state.map.setLayoutProperty(layerId,'visibility',state.map3d?'visible':'none');}
    state.map.easeTo({pitch:state.map3d?56:0,bearing:state.map3d?-18:0,duration:650});
  }catch(error){console.warn('No se pudo cambiar la visibilidad 3D',error);}
  sync3DControl();
  showToast(state.map3d?'Edificios 3D activados.':'Vista 2D activada.', 'success');
}

function locate(){if(!navigator.geolocation){showToast('Este navegador no permite geolocalización.','error');return;}navigator.geolocation.getCurrentPosition(pos=>{state.userLocation={lat:pos.coords.latitude,lng:pos.coords.longitude};updateNearest();if(state.mapVisible&&state.map){if(state.mapEngine==='maplibre')state.map.flyTo({center:[state.userLocation.lng,state.userLocation.lat],zoom:15,pitch:state.map3d?56:0,duration:1200});else state.map.setView([state.userLocation.lat,state.userLocation.lng],15);renderMapMarkers();}},()=>showToast('No se pudo obtener la ubicación; se usará Cancún como referencia.','error'),{enableHighAccuracy:true,timeout:10000,maximumAge:30000});}
function moneyForSelectedPackage(){return pricingForPackage(currentPackage());}
function openModule(module){state.selectedModule=module;const distance=distanceKm(originPoint().lat,originPoint().lng,module.lat,module.lng);$('modalName').textContent=module.name;$('modalMeta').textContent=`${module.id} · ${module.module_type==='bmax'?'MÓDULO BMAX · ':''}${distance.toFixed(1)} km aprox.`;$('modalConnectors').innerHTML=[...(module.module_type==='bmax'?['BMAX · NÚCLEO VIOLETA']:[]),...(module.connectors||[])].map((c,i)=>`<span class="chip ${i===0&&module.module_type==='bmax'?'bmax-module-chip':''}">${esc(c)}</span>`).join('');$('modalPower').textContent=module.power;$('modalStatus').textContent=module.status==='busy'?'Ocupado':'Disponible';$('modalStatus').className=module.status==='busy'?'busy':'available';$('modalStatusTop').textContent=module.status==='busy'?'OCUPADO':'DISPONIBLE';$('modalStatusTop').className=`modal-status-top ${module.status==='busy'?'busy':'ok'} ${module.module_type==='bmax'?'bmax-module-status':''}`;
  const own=state.activeSession&&String(state.activeSession.module_id)===String(module.id);$('modalUse').classList.toggle('hidden',module.status!=='available'||!!state.activeSession);$('modalDisconnect').classList.toggle('hidden',!own);updateModalPrice();if(module.remaining_seconds){$('modalCountdown').classList.remove('hidden');updateModalCountdown(module.remaining_seconds);}else $('modalCountdown').classList.add('hidden');$('moduleModal').classList.remove('hidden');}
function closeModal(){$('moduleModal').classList.add('hidden');clearInterval(state._modalTimer);}
function updateModalCountdown(seconds){clearInterval(state._modalTimer);let remain=seconds;const tick=()=>{const mm=Math.floor(Math.max(0,remain)/60),ss=Math.max(0,remain)%60;$('modalCountdown').textContent=`Sesión activa · ${String(mm).padStart(2,'0')}:${String(ss).padStart(2,'0')} restantes`;remain--;if(remain<0)clearInterval(state._modalTimer);};tick();state._modalTimer=setInterval(tick,1000);}
function updateModalPrice(){
  if(!$('modulePrice'))return;
  const price=moneyForSelectedPackage();
  const balance=Number(state.profile?.balance??state.user?.balance??0);
  const plan=estimateCharging(selectedDevice(),Number($('batteryLevel')?.value||35),state.selectedModule||nearestAvailableModule());
  const hasPlan=state.user?.role==='admin'||!!state.currentPlan;const eligible=state.user?.role==='admin'||(state.currentPlan?.id==='bmax_pro'?state.selectedModule?.module_type==='bmax':state.currentPlan?.id!=='bmax_pro'&&state.selectedModule?.module_type!=='bmax');
  $('modulePrice').textContent=hasPlan?'Incluido':money(price.final);
  $('modulePriceHint').textContent=!hasPlan?'Compra un plan en Servicios para poder conectar.':!eligible?'Este módulo requiere un plan distinto. Bmax Pro solo funciona en módulos Bmax.':`Sesión estimada ${plan.session_minutes} min · potencia efectiva ${plan.watts} W de un máximo de ${plan.package_max_watts} W. Incluida en ${state.currentPlan?.name||'tu acceso'}.`;
  $('modalUse').disabled=!!state.activeSession||state.selectedModule?.status==='busy'||!hasPlan||!eligible;
  $('modalUse').textContent=!hasPlan?'Compra un plan en Servicios':!eligible?'No compatible con tu plan':'Conectar con plan activo ⚡';
}

function estimate(){updateEstimate();return state.lastEstimate||estimateCharging();}
function paymentMethod(){return document.querySelector('input[name="checkoutPayment"]:checked')?.value||'wallet';}
function checkoutPrice(){
  const pack=currentPackage();const raw=pricingForPackage(pack);
  // El importe ya se pagó al activar el plan; iniciar la sesión no vuelve a cobrar.
  return {pack,duration:raw.duration,base:0,discount:0,total:0,useDiscount:false};
}
function updateCheckoutSummary(){
  if(!$('checkoutModal'))return;
  const price=checkoutPrice(),isBmax=price.pack.id==='bmax_pro';
  const paymentOptions=document.querySelector('.payment-options');if(paymentOptions)paymentOptions.classList.add('hidden');
  document.querySelector('.checkout-discount-option')?.classList.add('hidden');
  const plan=estimateCharging(selectedDevice(),Number($('batteryLevel').value),state.selectedModule||nearestAvailableModule());
  $('checkoutDuration').textContent=isBmax?`~${plan.session_minutes} min · hasta 100%`:`${plan.session_minutes} min`;
  $('checkoutWatts').textContent=`${plan.watts} W efectivos (tope ${plan.package_max_watts} W)`;
  if($('checkoutTargetLabel'))$('checkoutTargetLabel').textContent=isBmax?'Objetivo estimado hasta 100%':'Objetivo estimado hasta 90%';
  $('checkoutTargetTime').textContent=plan.battery>=plan.target_battery?'Objetivo ya alcanzado':`${plan.minutes_to_target} min aprox.`;
  $('checkoutFinalBattery').textContent=`${plan.estimated_end_battery}%`;
  $('checkoutPlanHint').textContent=isBmax?'Bmax Pro solo conecta en módulos Bmax. El tiempo se estima con la batería, capacidad y potencia admitida; el hardware del cargador determina la carga real.':'La compra del plan ya está registrada. La sesión se incluye sin un segundo cobro.';
  $('checkoutBase').textContent='Cubierto por tu plan';$('checkoutDiscount').textContent='—';$('checkoutTotal').textContent=money(0);
  $('checkoutDiscountLabel').textContent='Descuento aplicado al comprar el plan';$('checkoutDiscountHint').textContent='No se vuelve a cobrar al conectar.';
  $('checkoutWalletBalance').textContent='Sin cargo adicional';
  $('checkoutPaymentNote').textContent=isBmax?'Acceso Bmax Pro activo. Esta sesión está incluida y solo funcionará en un módulo Bmax.':'Tu plan ya fue comprado. Al iniciar la sesión no se descontará saldo adicional.';
  $('checkoutPaymentNote').classList.remove('price-warning');
  $('checkoutConfirm').disabled=false;$('checkoutConfirm').textContent=isBmax?'Activar conexión Bmax VIP ⚡':'Iniciar sesión incluida ⚡';
  return price;
}

function discountAvailableSafe(){return Number(state.profile?.discount_percent??state.user?.discount_percent??0);}
function openCheckout(moduleId){
  const module=state.modules.find(m=>String(m.id)===String(moduleId))||state.selectedModule;
  if(!module)return;
  state.selectedModule=module;
  if(state.user?.role!=='admin'&&!state.currentPlan){showToast('No tienes un plan activo. Compra uno en Servicios antes de conectar.','error');setView('services');return;}
  if(state.user?.role!=='admin'&&((state.currentPlan.id==='bmax_pro'&&module.module_type!=='bmax')||(state.currentPlan.id!=='bmax_pro'&&module.module_type==='bmax'))){showToast(state.currentPlan.id==='bmax_pro'?'Bmax Pro solo se puede usar en módulos Bmax.':'Este módulo es exclusivo de Bmax Pro.','error');return;}
  if(state.activeSession){showToast('Ya tienes una sesión activa. Desconéctala primero.','error');return;}
  if(module.status==='busy'){showToast('Este módulo ya está ocupado. Elige otro.','error');return;}
  estimate();
  $('checkoutModuleName').textContent=`${module.name} · ${module.power} · ${module.connectors.join(' · ')}`;
  $('checkoutUseDiscount').checked=false;
  $('checkoutUseDiscount').disabled=true;
  document.querySelector('input[name="checkoutPayment"][value="wallet"]').checked=true;
  updateCheckoutSummary();
  closeModal();$('checkoutModal').classList.remove('hidden');
}
function closeCheckout(){ $('checkoutModal').classList.add('hidden'); }
async function connectToModule(moduleId){openCheckout(moduleId);}
async function confirmConnection(){
  const module=state.modules.find(m=>String(m.id)===String(state.selectedModule?.id))||state.selectedModule;
  if(!module)return;
  if(state.user?.role!=='admin'&&!state.currentPlan){showToast('Compra un plan antes de conectar.','error');closeCheckout();setView('services');return;}
  const price=updateCheckoutSummary(),method=price.pack.id==='bmax_pro'?'bmax_pro':(state.user?.role==='admin'?'admin':'plan');
  const calc=estimate(),device=selectedDevice();
  $('checkoutConfirm').disabled=true;
  try{
    const data=await api(`/api/modules/${encodeURIComponent(module.id)}/connect`,{method:'POST',body:JSON.stringify({
      package_id:price.pack.id,minutes:price.duration,device:device.name,connector:$('connectorSelect').value,
      estimated_minutes:calc.minutes_to_target,session_minutes:calc.session_minutes,battery_end_estimate:calc.estimated_end_battery,battery_start:calc.battery,device_capacity_mah:device.batteryMah,
      device_max_watts:device.maxWatts,use_discount:false,payment_method:method
    })});
    state.activeSession=data.session;state.user.balance=data.balance;if(state.profile)state.profile.balance=data.balance;
    state.connectionAnimationActive=true;
    $('activeSessionDock').classList.add('hidden');
    updateBalanceDisplay();closeCheckout();closeModal();
    setView('home');toggleMap(true);
    if(state.mapEngine==='maplibre')state.map.flyTo({center:[module.lng,module.lat],zoom:16,pitch:state.map3d?56:0,duration:1000});
    else if(state.mapEngine==='leaflet')state.map.setView([module.lat,module.lng],16);

    // Start the big connection animation immediately after the server confirms the reservation.
    const animationPromise=playConnectionAnimation(module,data.session,device,price);
    await Promise.allSettled([loadModules(),loadProfile(),loadPackages()]);
    await animationPromise;
    // Only after the large animation collapses do we reveal the compact always-on charging dock.
    await refreshActiveSession();
    if(method==='bmax_pro')showToast(`${module.name} ocupado. Sesión Bmax incluida; objetivo estimado hasta el 100%.`,'success');
    else showToast(`${module.name} ocupado. Sesión incluida en tu plan. No hubo un segundo cobro.`,'success');
    await dbPut('history',{owner:state.user.username,device:device.name,connector:$('connectorSelect').value,package:price.pack.name,minutes:Number(data.session?.minutes||calc.session_minutes),estimated_minutes:calc.minutes_to_target,battery_end_estimate:calc.estimated_end_battery,moduleName:module.name,amount_paid:data.charged,amount_due:data.amount_due,payment_method:method,payment_status:data.payment_status,discount_percent:data.discount_percent,date:new Date().toISOString()});
    state.history=(await dbAll('history')).filter(h=>h.owner===state.user.username);renderHistory();
  }catch(error){
    state.connectionAnimationActive=false;
    $('connectionAnimation').classList.add('hidden');$('connectionAnimation').classList.remove('is-running','is-collapsing');
    showToast(error.message,'error');if(error.status===402){await loadProfile();updateCheckoutSummary();}
  }finally{$('checkoutConfirm').disabled=false;updateCheckoutSummary();}
}
async function disconnectActive(moduleId){try{await api(`/api/modules/${encodeURIComponent(moduleId)}/disconnect`,{method:'POST'});state.activeSession=null;stopChargingAnimation();hideSessionDock();await loadModules();await loadProfile();closeModal();showToast('Módulo liberado y saldo actualizado.','success');}catch(error){showToast(error.message,'error');}}
async function refreshActiveSession(){try{const result=await api('/api/sessions/active');const previous=state.activeSession;if(result.active){state.activeSession=result.session;if(!state.connectionAnimationActive)renderSessionDock();}else{state.activeSession=null;hideSessionDock();if(previous){stopChargingAnimation();await loadModules();await loadProfile();showToast('La sesión terminó. El módulo está disponible.','success');}}}catch(error){if(error.status!==401)console.warn(error);}}
function updateBalanceDisplay(){const balance=Number(state.profile?.balance??state.user?.balance??0);$('balanceBadge').textContent=money(balance);$('profileBalance').textContent=money(balance);updatePriceSummary();}
function renderSessionDock(){
  const s=state.activeSession;if(!s)return;const dock=$('activeSessionDock');const wasHidden=dock.classList.contains('hidden');dock.classList.remove('hidden');if(wasHidden){dock.classList.remove('session-dock-enter');void dock.offsetWidth;dock.classList.add('session-dock-enter');setTimeout(()=>dock.classList.remove('session-dock-enter'),780);}$('activeSessionName').textContent=s.module_name||'Módulo EcoBlack';const isBmaxSession=(s.package_id==='bmax_pro'||s.package_name==='Bmax Pro');const timeLabel=$('activeSessionTime')?.querySelector('small');if(timeLabel)timeLabel.textContent=isBmaxSession?'ESTIMADO HASTA 100%':'TIEMPO RESTANTE';$('activeSessionMeta').textContent=`${s.device||'Celular'} · ${s.package_name||'Paquete'} · ${Number(s.effective_watts||s.package_max_watts||0)} W · batería estimada ${Number(s.battery_end_estimate||s.battery_start||35)}% al finalizar · ${paymentMethodLabel(s.payment_method||'wallet')}${s.payment_status==='pending_cash'?' · pago pendiente':''}`;
  if(state.sessionTick)clearInterval(state.sessionTick);state.sessionTick=setInterval(()=>{if(!state.activeSession)return;const until=Number(s.occupied_until||s.until||0);const now=Date.now()/1000;const remain=Math.max(0,Math.ceil(until-now));$('activeSessionCountdown').textContent=`${String(Math.floor(remain/60)).padStart(2,'0')}:${String(remain%60).padStart(2,'0')}`;if(remain<=0)refreshActiveSession();},1000);
  stopChargingAnimation();let lastPercent=-1;const animate=()=>{if(!state.activeSession||document.visibilityState==='hidden'){state.sessionRaf=null;return;}const sess=state.activeSession;const targetBattery=isBmaxSession?100:90;const start=Number(sess.started_at||Date.now()/1000),batteryStart=Number(sess.battery_start??35),batteryEnd=Math.max(batteryStart,Math.min(targetBattery,Number(sess.battery_end_estimate??targetBattery))),chargeSeconds=Math.max(60,Number(sess.minutes||15)*60);const progress=Math.min(1,Math.max(0,(Date.now()/1000-start)/chargeSeconds));const percent=Math.round(batteryStart+(batteryEnd-batteryStart)*progress);if(percent!==lastPercent){lastPercent=percent;$('activeBatteryRing').style.setProperty('--battery-level',`${percent}%`);$('activeBatteryPercent').textContent=`${percent}%`;}if(progress>=1&&batteryEnd>=targetBattery)$('activeBatteryRing').classList.add('battery-full');else $('activeBatteryRing').classList.remove('battery-full');state.sessionRaf=requestAnimationFrame(animate);};state.sessionRaf=requestAnimationFrame(animate);
}
function hideSessionDock(){$('activeSessionDock').classList.add('hidden');if(state.sessionTick)clearInterval(state.sessionTick);state.sessionTick=null;}
function stopChargingAnimation(){if(state.sessionRaf)cancelAnimationFrame(state.sessionRaf);state.sessionRaf=null;}

function renderAdminModules(){
  if(state.user?.role!=='admin')return;
  $('adminModuleList').innerHTML=state.modules.map(m=>{
    const isBmax=m.module_type==='bmax',located=moduleHasValidLocation(m);
    const coordinates=located?`${m.lat.toFixed(5)}, ${m.lng.toFixed(5)}`:'<span class="module-location-missing">Ubicación pendiente</span>';
    return `<div class="list-item admin-item ${isBmax?'admin-bmax-module':''} ${located?'':'admin-module-unlocated'}"><div><b>${esc(m.name)} ${isBmax?'<span class="admin-bmax-type">BMAX</span>':''}</b><div class="meta">${esc(m.city||'Ciudad sin asignar')}${m.state?' · '+esc(m.state):''} · ${coordinates} · ${esc(m.power)} · <span class="inline-status ${m.status}">${m.status==='available'?'Disponible':'Ocupado'}</span>${isBmax?' · Núcleo negro / violeta':''}</div></div><div class="admin-actions">${located?`<button class="glass-button" data-focus-module="${esc(m.id)}">Ver</button>`:''}<button class="glass-button" data-relocate-module="${esc(m.id)}" ${m.remaining_seconds?'disabled':''}>${located?'Reubicar':'Ubicar en mapa'}</button><button class="glass-button" data-edit-module-geo="${esc(m.id)}">Ciudad/estado</button><button class="glass-button" data-toggle-module="${esc(m.id)}" ${m.remaining_seconds?'disabled':''}>${m.status==='available'?'Ocupar':'Liberar'}</button><button class="glass-button danger-text" data-delete-module="${esc(m.id)}" ${m.remaining_seconds?'disabled':''}>Quitar</button></div></div>`;
  }).join('')||'<div class="muted">No hay módulos.</div>';
}
async function addModule(){
  const name=$('moduleName').value.trim(),latText=$('moduleLat').value.trim(),lngText=$('moduleLng').value.trim();
  const lat=Number.parseFloat(latText),lng=Number.parseFloat(lngText),city=$('moduleCity').value.trim(),stateName=$('moduleState').value.trim();
  const power=$('modulePower').value,status=$('moduleStatus').value,module_type=$('moduleType').value;
  const connectors=[...document.querySelectorAll('#connectorChecks input:checked')].map(input=>input.value);
  if(!name||!city||!stateName){showToast('Completa nombre, ciudad y estado.','error');return;}
  // If the admin presses Add before choosing coordinates, guide them straight
  // into map-pick mode instead of silently doing nothing.
  if(!latText||!lngText){
    state.adminRelocateModuleId=null;state.adminPickMode=true;setView('home');toggleMap(true);
    showToast('Toca el punto exacto en el mapa. Después volverás al formulario para guardar el módulo.','info');return;
  }
  if(!Number.isFinite(lat)||!Number.isFinite(lng)||lat < -90||lat > 90||lng < -180||lng > 180||(Math.abs(lat)<1e-9&&Math.abs(lng)<1e-9)){
    showToast('La ubicación no es válida. Elige un punto real en el mapa o corrige latitud y longitud.','error');return;
  }
  const button=$('addModuleBtn');button.disabled=true;button.textContent='Guardando módulo…';
  try{
    const result=await api('/api/modules',{method:'POST',body:JSON.stringify({name,lat,lng,city,state:stateName,power,status,module_type,connectors})});
    showToast(module_type==='bmax'?'Módulo Bmax añadido con diseño negro y violeta.':'Módulo añadido al mapa.','success');
    $('moduleName').value='';$('moduleCity').value='';$('moduleState').value='';$('moduleLat').value='';$('moduleLng').value='';
    $('moduleType').value='standard';$('modulePower').value='30 W';$('pickLocationStatus').textContent='Sin punto seleccionado';
    setView('home');toggleMap(true);
    await loadModules();
    focusMapAt(Number(result.module.lng),Number(result.module.lat),16);
  }catch(error){showToast(error.message,'error');}
  finally{button.disabled=false;button.textContent='Añadir al mapa ⚡';}
}
async function editModuleGeo(id){
  const module=state.modules.find(item=>String(item.id)===String(id));if(!module)return;
  const city=window.prompt('Ciudad del módulo:',module.city||'');if(city===null)return;
  const stateName=window.prompt('Estado del módulo:',module.state||'');if(stateName===null)return;
  if(!city.trim()||!stateName.trim()){showToast('La ciudad y el estado son necesarios para agrupar el mapa.','error');return;}
  try{await api(`/api/modules/${encodeURIComponent(id)}`,{method:'PUT',body:JSON.stringify({city:city.trim(),state:stateName.trim()})});showToast('Ciudad y estado actualizados.','success');await loadModules();}
  catch(error){showToast(error.message,'error');}
}
async function deleteModule(id){try{await api(`/api/modules/${encodeURIComponent(id)}`,{method:'DELETE'});showToast('Módulo eliminado.','success');await loadModules();}catch(error){showToast(error.message,'error');}}
async function toggleAdminModule(id){const module=state.modules.find(m=>String(m.id)===String(id));if(!module)return;try{await api(`/api/modules/${encodeURIComponent(id)}`,{method:'PUT',body:JSON.stringify({status:module.status==='available'?'busy':'available'})});showToast(`Módulo ${module.status==='available'?'marcado ocupado':'liberado'}.`,'success');await loadModules();}catch(error){showToast(error.message,'error');}}
async function loadAdminUsers(){if(state.user?.role!=='admin')return;const data=await api('/api/admin/users');state.adminUsers=data.users;renderAdminUsers();}
function renderAdminUsers(){
  $('adminUsersList').innerHTML=state.adminUsers.map(user=>`<article class="admin-user-card ${user.blocked?'is-blocked':''}"><div class="admin-user-heading"><div class="admin-user-avatar">${esc((user.display_name||user.username).slice(0,1).toUpperCase())}</div><div class="admin-user-name"><b>${esc(user.display_name||user.username)}</b><span>@${esc(user.username)} · ${esc(user.role)}</span></div><span class="user-status ${user.blocked?'blocked':'unblocked'}">${user.blocked?'BLOQUEADO':'ACTIVO'}</span></div><div class="admin-user-metrics"><div><span>SALDO</span><b>${money(user.balance)}</b></div><div><span>DESCUENTO</span><b>${Number(user.discount_percent||0)}%</b></div><div><span>USO</span><b>${esc(user.usage||'—')}</b></div></div><div class="admin-user-controls"><label>Agregar saldo ($ MXN)<input type="number" min="1" step="0.01" placeholder="100" data-topup-input="${esc(user.username)}" /></label><button class="glass-button topup-button" data-topup="${esc(user.username)}">+ Saldo</button><label>Descuento (%)<input type="number" min="0" max="90" step="1" value="${Number(user.discount_percent||0)}" data-discount-input="${esc(user.username)}" /></label><button class="glass-button" data-save-discount="${esc(user.username)}">Guardar</button></div>${user.role!=='admin'?`<button class="glass-button block-user-btn ${user.blocked?'unblock':'block'}" data-block-user="${esc(user.username)}" data-blocked="${user.blocked?'1':'0'}">${user.blocked?'Desbloquear cuenta':'Bloquear cuenta'}</button>`:'<div class="admin-note">Cuenta administradora protegida</div>'}</article>`).join('')||'<div class="muted">No hay usuarios registrados.</div>';
}
async function loadAdminBmax(){if(state.user?.role!=='admin')return;const data=await api('/api/admin/bmax');state.bmaxAdmin=data;renderAdminBmax();}
function renderAdminBmax(){
  const data=state.bmaxAdmin;if(!data)return;const c=data.config||{};
  $('adminBmaxTotal').textContent=Number(c.total_slots||0);$('adminBmaxSold').textContent=Number(c.sold_count||0);$('adminBmaxReserved').textContent=Number(c.reserved_count||0);$('adminBmaxAvailable').textContent=Number(c.available_slots||0);
  $('adminBmaxSlots').value=Number(c.total_slots||0);$('adminBmaxPrice').value=Number(c.price_mxn||99);
  const rows=data.purchases||[];$('adminBmaxPurchases').innerHTML=rows.length?rows.map(row=>`<div class="bmax-buyer-row"><div><b>${esc(row.display_name||row.username)}</b><small>@${esc(row.username)} · ${new Date(Number(row.purchased_at)*1000).toLocaleString('es-MX')}</small></div><strong>${money(row.price_mxn)}</strong></div>`).join(''):'<div class="muted">Todavía no hay compras de Bmax Pro.</div>';
}
async function saveAdminBmaxConfig(){const total_slots=Number($('adminBmaxSlots').value),price_mxn=Number($('adminBmaxPrice').value);if(!Number.isInteger(total_slots)||total_slots<0||!(price_mxn>0)){showToast('Revisa los cupos y el precio.','error');return;}try{await api('/api/admin/bmax/config',{method:'PUT',body:JSON.stringify({total_slots,price_mxn})});showToast('Cupos y precio de Bmax Pro actualizados.','success');await loadAdminBmax();await loadPackages();}catch(error){showToast(error.message,'error');await loadAdminBmax().catch(()=>{});}}

async function loadAdminRevenue(){
  if(state.user?.role!=='admin')return;
  const data=await api('/api/admin/revenue');
  renderAdminRevenue(data);
}
function renderAdminRevenue(data){
  const summary=data.summary||{},account=data.account||{};
  $('adminRevenueAccount').textContent=money(account.balance);
  $('adminRevenueToday').textContent=money(summary.collected_today);
  $('adminRevenueTotal').textContent=money(summary.collected_total);
  $('adminRevenuePending').textContent=money(summary.cash_pending);
  $('adminRevenueTodayCount').textContent=Number(summary.payments_today||0).toLocaleString('es-MX');
  $('adminRevenueTotalCount').textContent=Number(summary.payments_total||0).toLocaleString('es-MX');
  const rows=data.transactions||[];
  $('adminRevenueTransactions').innerHTML=rows.length?rows.map(row=>{
    const method=row.payment_method==='wallet'?'Saldo EcoBlack':row.payment_method==='cash'?'Efectivo':'Tarjeta demo';
    const status=row.collected?'COBRADO':row.payment_status==='pending_cash'?'PENDIENTE DE EFECTIVO':row.payment_status==='demo_not_processed'?'NO PROCESADO':'PENDIENTE';
    const date=new Date(Number(row.collected_at||row.created_at)*1000).toLocaleString('es-MX');
    const pendingCash=!row.collected&&row.payment_method==='cash'&&row.payment_status==='pending_cash';
    return `<article class="revenue-row"><div class="revenue-row-main"><b>${esc(row.module_name||'Módulo')}</b><span>@${esc(row.payer_username)} · ${esc(row.package_name)} · ${esc(method)}</span><small>${esc(date)} · ${esc(status)}</small></div><div class="revenue-row-amount"><strong>${money(row.amount)}</strong>${row.discount_amount>0?`<small>Desc. ${money(row.discount_amount)}</small>`:''}${pendingCash?`<button class="glass-button collect-cash-btn" data-collect-cash="${row.id}">Confirmar efectivo</button>`:''}</div></article>`;
  }).join(''):'<div class="muted revenue-empty">Todavía no hay cobros registrados. Los cobros por saldo aparecerán aquí automáticamente.</div>';
}
async function confirmCashCollection(id){
  if(!confirm('Confirma que recibiste este pago en efectivo y deseas sumarlo a la cuenta de ingresos de tragic.'))return;
  try{const result=await api(`/api/admin/revenue/${id}/collect`,{method:'POST',body:JSON.stringify({})});showToast(`Se agregaron ${money(result.collected)} a la cuenta admin.`, 'success');await loadAdminRevenue();}catch(error){showToast(error.message,'error');}
}

async function adminTopup(username){const input=document.querySelector(`[data-topup-input="${CSS.escape(username)}"]`);const amount=Number(input?.value||0);if(!(amount>0)){showToast('Escribe una cantidad mayor que cero.','error');return;}try{await api(`/api/admin/users/${encodeURIComponent(username)}/balance`,{method:'POST',body:JSON.stringify({amount})});showToast(`Se agregaron ${money(amount)} a @${username}.`,'success');await loadAdminUsers();if(username===state.user.username)await loadProfile();}catch(error){showToast(error.message,'error');}}
async function adminDiscount(username){const input=document.querySelector(`[data-discount-input="${CSS.escape(username)}"]`);const discount=Number(input?.value);if(discount<0||discount>90){showToast('El descuento debe ser de 0% a 90%.','error');return;}try{await api(`/api/admin/users/${encodeURIComponent(username)}/discount`,{method:'PUT',body:JSON.stringify({discount_percent:discount})});showToast(`Descuento actualizado para @${username}.`,'success');await loadAdminUsers();if(username===state.user.username){await loadProfile();await loadPackages();}}catch(error){showToast(error.message,'error');}}
async function adminBlock(username,blocked){try{await api(`/api/admin/users/${encodeURIComponent(username)}/blocked`,{method:'PUT',body:JSON.stringify({blocked:!blocked})});showToast(`Cuenta @${username} ${blocked?'desbloqueada':'bloqueada'}.`, 'success');await loadAdminUsers();}catch(error){showToast(error.message,'error');}}

async function saveProfileName(){
  const input=$('displayNameInput');
  const name=(input?.value||'').trim().replace(/\s+/g,' ');
  if(!name){showToast('Escribe un nombre visible antes de guardar.','error');input?.focus();return;}
  const button=$('editNameBtn');
  if(button){button.disabled=true;button.textContent='Guardando…';}
  try{
    const result=await api('/api/profile',{method:'PUT',body:JSON.stringify({display_name:name})});
    state.profile={...state.profile,...result.profile};
    state.user={...state.user,...result.profile};
    // Re-read the profile returned by Flask to keep the UI and database in sync.
    await loadProfile();
    if(state.user.role==='admin')await loadAdminUsers();
    showToast(`Nombre visible guardado: ${state.profile.display_name}`,'success');
  }catch(error){showToast(error.message,'error');}
  finally{if(button){button.disabled=false;button.textContent='Guardar nombre visible';}}
}

async function saveProfilePhoto(file){if(!file)return;if(!file.type.startsWith('image/')){showToast('Selecciona un archivo de imagen.','error');return;}try{const dataUrl=await imageToDataUrl(file);const result=await api('/api/profile',{method:'PUT',body:JSON.stringify({display_name:state.profile?.display_name||state.user.username,avatar_data:dataUrl})});state.profile={...state.profile,...result.profile};renderProfilePhoto(dataUrl);showToast('Foto de perfil guardada.','success');}catch(error){showToast(error.message,'error');}}
function imageToDataUrl(file){return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onerror=()=>reject(new Error('No se pudo leer la imagen.'));reader.onload=()=>{const img=new Image();img.onerror=()=>reject(new Error('La imagen no se pudo abrir.'));img.onload=()=>{const scale=Math.min(1,512/Math.max(img.width,img.height));const canvas=document.createElement('canvas');canvas.width=Math.max(1,Math.round(img.width*scale));canvas.height=Math.max(1,Math.round(img.height*scale));canvas.getContext('2d').drawImage(img,0,0,canvas.width,canvas.height);resolve(canvas.toDataURL('image/jpeg',.78));};img.src=reader.result;};reader.readAsDataURL(file);});}
async function addDevice(){const name=$('newDevice').value.trim(),batteryMah=Number($('newDeviceBattery').value),maxWatts=Number($('newDeviceWatts').value),connector=$('newDeviceConnector').value;if(!name){showToast('Escribe el nombre/modelo del celular.','error');return;}if(batteryMah<1000||batteryMah>30000||maxWatts<3||maxWatts>300){showToast('Revisa mAh y potencia W.','error');return;}const device={id:`${state.user.username}-device-${Date.now()}`,owner:state.user.username,name,batteryMah,maxWatts,connector};await dbPut('devices',device);state.devices=(await dbAll('devices')).filter(d=>d.owner===state.user.username).map(normalizeDevice);renderDevices();$('newDevice').value='';showToast('Celular añadido y listo para el cálculo.','success');}

async function logout(){if(state.activeSession){const leave=confirm(`Tienes una sesión activa en ${state.activeSession.module_name}. ¿Desconectarla y cerrar sesión?`);if(!leave)return;await api(`/api/modules/${encodeURIComponent(state.activeSession.module_id)}/disconnect`,{method:'POST'});}await api('/api/auth/logout',{method:'POST'});state.user=null;state.profile=null;state.activeSession=null;stopChargingAnimation();hideSessionDock();if(state.map?.remove)state.map.remove();state.map=null;state.mapEngine=null;state.mapVisible=false;showLogin();showAuthPanel('login');}

// Ambient yellow node network. Links naturally break when moving points drift apart.
function bootNetworkBackground(){
  const canvas=$('networkCanvas');if(!canvas)return;const ctx=canvas.getContext('2d',{alpha:true});let width=0,height=0,dpr=1,points=[],raf=0;
  const reduced=window.matchMedia?.('(prefers-reduced-motion: reduce)').matches||false;
  const resize=()=>{
    dpr=Math.min(window.devicePixelRatio||1,1.25);width=window.innerWidth;height=window.innerHeight;
    canvas.width=Math.round(width*dpr);canvas.height=Math.round(height*dpr);canvas.style.width=`${width}px`;canvas.style.height=`${height}px`;ctx.setTransform(dpr,0,0,dpr,0,0);
    const mobile=width<700,count=reduced?16:(mobile?38:78);
    points=Array.from({length:count},()=>({x:Math.random()*width,y:Math.random()*height,vx:(Math.random()-.5)*(mobile ? .3 : .48),vy:(Math.random()-.5)*(mobile ? .3 : .48),r:.9+Math.random()*2.0,phase:Math.random()*6.28}));
  };
  const frame=()=>{
    ctx.clearRect(0,0,width,height);
    if(document.visibilityState==='hidden'){raf=0;return;}
    const linkMax=width<700?108:168;
    for(const p of points){
      p.x+=p.vx;p.y+=p.vy;p.phase+=.018;
      if(p.x < -10)p.x=width+10;else if(p.x>width+10)p.x=-10;
      if(p.y < -10)p.y=height+10;else if(p.y>height+10)p.y=-10;
    }
    // Dynamic edges: no line is drawn when two points drift outside linkMax.
    for(let i=0;i<points.length;i++){
      const a=points[i];
      for(let j=i+1;j<points.length;j++){
        const b=points[j],dx=a.x-b.x,dy=a.y-b.y,d2=dx*dx+dy*dy,limit2=linkMax*linkMax;
        if(d2<limit2){const d=Math.sqrt(d2),alpha=(1-d/linkMax)*.48;ctx.strokeStyle=`rgba(223,255,0,${alpha})`;ctx.lineWidth=.7;ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);ctx.stroke();}
      }
    }
    for(const p of points){
      const pulse=.55+.35*Math.sin(p.phase);ctx.beginPath();ctx.arc(p.x,p.y,p.r,0,Math.PI*2);ctx.fillStyle=`rgba(232,255,74,${.55+pulse*.42})`;ctx.fill();
    }
    raf=requestAnimationFrame(frame);
  };
  resize();window.addEventListener('resize',resize,{passive:true});frame();document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible'&&!raf){raf=requestAnimationFrame(frame);}});
}

// Energy intro: particles spiral into the logo, bolts rush inward and briefly orbit the core.
function bootStorm(){const storm=$('storm');setInterval(()=>{if($('app').classList.contains('hidden')||document.visibilityState==='hidden')return;const count=Math.random()<.14?2:1;for(let n=0;n<count;n++){const bolt=document.createElement('div');bolt.className='falling-bolt';bolt.textContent=Math.random()<.2?'ϟ':'⚡';bolt.style.left=`${Math.random()*100}%`;bolt.style.animationDuration=`${2.3+Math.random()*4}s`;bolt.style.setProperty('--drift',`${(Math.random()-.5)*110}px`);storm.appendChild(bolt);setTimeout(()=>bolt.remove(),6800);}},1250);}
function bootSplash(){
  const canvas=$('particleCanvas'),ctx=canvas.getContext('2d');let W,H,dpr=1;const resize=()=>{dpr=Math.min(devicePixelRatio||1,2);W=innerWidth;H=innerHeight;canvas.width=W*dpr;canvas.height=H*dpr;canvas.style.width=`${W}px`;canvas.style.height=`${H}px`;ctx.setTransform(dpr,0,0,dpr,0,0)};resize();addEventListener('resize',resize);
  const particles=Array.from({length:440},()=>({angle:Math.random()*Math.PI*2,radius:Math.max(W,H)*(.15+Math.random()*.85),speed:.4+Math.random()*1.15,size:.4+Math.random()*2.2,tail:8+Math.random()*30}));
  const field=$('inwardBolts');field.innerHTML='';for(let i=0;i<84;i++){const bolt=document.createElement('span');bolt.className='inward-bolt';bolt.textContent=i%8===0?'ϟ':'⚡';const angle=Math.random()*Math.PI*2,rad=Math.max(W,H)*(.48+Math.random()*.6),ox=Math.cos(angle)*rad,oy=Math.sin(angle)*rad;bolt.style.setProperty('--sx',`${Math.cos(angle)*rad}px`);bolt.style.setProperty('--sy',`${Math.sin(angle)*rad}px`);bolt.style.setProperty('--ox',`${Math.cos(angle+.8)*(50+Math.random()*100)}px`);bolt.style.setProperty('--oy',`${Math.sin(angle+.8)*(50+Math.random()*100)}px`);bolt.style.setProperty('--rot',`${Math.random()*100-50}deg`);bolt.style.setProperty('--spin',`${Math.random()*220-110}deg`);bolt.style.setProperty('--duration',`${1.2+Math.random()*1.8}s`);bolt.style.setProperty('--delay',`${Math.random()*2.2}s`);field.appendChild(bolt);}
  const draw=()=>{if($('splash').classList.contains('hidden'))return;ctx.clearRect(0,0,W,H);const cx=W/2,cy=H/2;for(const p of particles){p.radius-=p.speed;if(p.radius<18){p.angle=Math.random()*Math.PI*2;p.radius=Math.max(W,H)*(.55+Math.random()*.65);}const a=p.angle+performance.now()*.000015,x=cx+Math.cos(a)*p.radius,y=cy+Math.sin(a)*p.radius;ctx.beginPath();ctx.moveTo(cx+Math.cos(a)*(p.radius+p.tail),cy+Math.sin(a)*(p.radius+p.tail));ctx.lineTo(x,y);ctx.strokeStyle=`rgba(223,255,0,${Math.max(.06,.5-p.radius/Math.max(W,H))*.26})`;ctx.lineWidth=Math.max(.5,p.size*.5);ctx.stroke();ctx.beginPath();ctx.arc(x,y,p.size,0,Math.PI*2);ctx.fillStyle=`rgba(223,255,0,${Math.max(.07,.8-p.radius/Math.max(W,H))})`;ctx.fill();}requestAnimationFrame(draw)};draw();
  const status=['RAYOS DIRIGIDOS AL NÚCLEO','CONCENTRANDO ENERGÍA','CALIBRANDO CELULARES','CARGANDO MÓDULOS','ECOBLACK LISTO'];const start=performance.now(),duration=2800;const tick=now=>{const pct=Math.min(100,(now-start)/duration*100);$('splashProgress').style.width=`${pct}%`;$('splashStatus').textContent=status[Math.min(status.length-1,Math.floor(pct/100*status.length))];if(pct<100)requestAnimationFrame(tick)};requestAnimationFrame(tick);setTimeout(()=>{$('splash').classList.add('hidden');checkAuth().catch(error=>{showLogin();showAuthError(error.message)})},3000);
}

// Delegated actions.
document.addEventListener('click',async event=>{
  const nav=event.target.closest('[data-view]');if(nav){setView(nav.dataset.view);return;}
  const authTab=event.target.closest('[data-auth-view]');if(authTab){showAuthPanel(authTab.dataset.authView);return;}
  const buyButton=event.target.closest('[data-buy-plan]');if(buyButton){await buyPlan(buyButton.dataset.buyPlan);return;}
  const packageButton=event.target.closest('[data-select-package]');if(packageButton){const id=packageButton.dataset.selectPackage;if(state.user?.role!=='admin'&&(!state.currentPlan||state.currentPlan.id!==id)){showToast('Compra este plan antes de usarlo.','info');return;}$('packageSelect').value=id;setView('charge');updatePriceSummary();updateEstimate();playPackageSelectionAnimation(id);return;}
  if(event.target.matches('[data-close]')||event.target.closest('[data-close]')){closeModal();return;}
  if(event.target.matches('[data-checkout-close]')||event.target.closest('[data-checkout-close]')){closeCheckout();return;}
  const removeDevice=event.target.closest('[data-remove-device]');if(removeDevice){await dbDelete('devices',removeDevice.dataset.removeDevice);state.devices=(await dbAll('devices')).filter(d=>d.owner===state.user.username).map(normalizeDevice);if(!state.devices.length){const fallback={...DEVICE_PRESETS[0],id:`${state.user.username}-fallback`,owner:state.user.username};await dbPut('devices',fallback);state.devices=[fallback];}renderDevices();return;}
  const focus=event.target.closest('[data-focus-module]');if(focus){const module=state.modules.find(m=>String(m.id)===String(focus.dataset.focusModule));if(module){setView('home');toggleMap(true);if(moduleHasValidLocation(module))focusMapAt(module.lng,module.lat,16);openModule(module);}return;}
  const editGeo=event.target.closest('[data-edit-module-geo]');if(editGeo){await editModuleGeo(editGeo.dataset.editModuleGeo);return;}
  const deleteBtn=event.target.closest('[data-delete-module]');if(deleteBtn&&!deleteBtn.disabled){await deleteModule(deleteBtn.dataset.deleteModule);return;}
  const toggleModule=event.target.closest('[data-toggle-module]');if(toggleModule&&!toggleModule.disabled){await toggleAdminModule(toggleModule.dataset.toggleModule);return;}
  const bmaxReserve=event.target.closest('[data-bmax-reserve]');if(bmaxReserve){await reserveBmax();return;}
  const bmaxConfirm=event.target.closest('[data-bmax-confirm]');if(bmaxConfirm){await confirmBmaxPurchase();return;}
  const bmaxCancel=event.target.closest('[data-bmax-cancel]');if(bmaxCancel){await cancelBmaxReservation();return;}
  const topup=event.target.closest('[data-topup]');if(topup){await adminTopup(topup.dataset.topup);return;}
  const discount=event.target.closest('[data-save-discount]');if(discount){await adminDiscount(discount.dataset.saveDiscount);return;}
  const blocked=event.target.closest('[data-block-user]');if(blocked){await adminBlock(blocked.dataset.blockUser,blocked.dataset.blocked==='1');return;}
  const collectCash=event.target.closest('[data-collect-cash]');if(collectCash){await confirmCashCollection(Number(collectCash.dataset.collectCash));return;}
  const relocate=event.target.closest('[data-relocate-module]');if(relocate&&!relocate.disabled){state.adminRelocateModuleId=relocate.dataset.relocateModule;state.adminPickMode=true;setView('home');toggleMap(true);showToast('Toca el lugar exacto en el mapa para ubicar este módulo.','info');return;}
  const pick=event.target.closest('#pickModuleLocationBtn');if(pick){state.adminRelocateModuleId=null;state.adminPickMode=true;setView('home');toggleMap(true);showToast('Toca el mapa para fijar la ubicación exacta del nuevo módulo.','info');return;}
});

$('checkoutClose').addEventListener('click',closeCheckout);$('checkoutCancel').addEventListener('click',closeCheckout);$('checkoutConfirm').addEventListener('click',()=>confirmConnection().catch(error=>showToast(error.message,'error')));$('checkoutUseDiscount').addEventListener('change',updateCheckoutSummary);document.querySelectorAll('input[name="checkoutPayment"]').forEach(input=>input.addEventListener('change',updateCheckoutSummary));
$('loginBtn').addEventListener('click',async()=>{try{state.user=(await api('/api/auth/login',{method:'POST',body:JSON.stringify({username:$('loginUsername').value,password:$('loginPassword').value})})).user;await enterApp();}catch(error){showAuthError(error.message);}});
$('registerBtn').addEventListener('click',async()=>{try{state.user=(await api('/api/auth/register',{method:'POST',body:JSON.stringify({username:$('registerUsername').value,password:$('registerPassword').value,usage:$('usageSelect').value})})).user;await enterApp();}catch(error){showAuthError(error.message);}});
$('logoutBtn').addEventListener('click',()=>logout().catch(error=>showToast(error.message,'error')));$('refreshRevenueBtn').addEventListener('click',()=>loadAdminRevenue().catch(error=>showToast(error.message,'error')));$('refreshBmaxBtn').addEventListener('click',()=>loadAdminBmax().catch(error=>showToast(error.message,'error')));$('saveBmaxConfigBtn').addEventListener('click',()=>saveAdminBmaxConfig().catch(error=>showToast(error.message,'error')));
$('batteryLevel').addEventListener('input',updateEstimate);$('connectorSelect').addEventListener('change',updateEstimate);$('deviceSelect').addEventListener('change',updateEstimate);$('packageSelect').addEventListener('change',()=>{updatePriceSummary();updateEstimate();playPackageSelectionAnimation(currentPackage().id);});$('customMinutes').addEventListener('input',()=>{updatePriceSummary();updateEstimate();});
$('estimateBtn').addEventListener('click',()=>{const calc=estimate();showToast(`Tiempo orientativo: ${calc.minutes} min · ${calc.watts} W calculados.`, 'success');});$('saveSessionBtn').addEventListener('click',async()=>{if(state.activeSession){showToast('La sesión conectada ya se registra automáticamente.','info');return;}const calc=estimate();await dbPut('history',{owner:state.user.username,device:calc.device.name,connector:calc.connector,package:currentPackage().name,minutes:calc.minutes,moduleName:'Estimación sin conexión',amount_paid:0,date:new Date().toISOString()});state.history=(await dbAll('history')).filter(h=>h.owner===state.user.username);renderHistory();showToast('Estimación guardada en tu historial.','success');});
$('adminServiceSelect')?.addEventListener('change',event=>changeAdminServiceTheme(event.target.value).catch(error=>showToast(error.message,'error')));
$('locateBtn').addEventListener('click',locate);$('mapMyLocationBtn').addEventListener('click',locate);$('nearestBtn').addEventListener('click',()=>state.nearestModule&&openModule(state.nearestModule));$('toggleMapBtn').addEventListener('click',()=>toggleMap(!state.mapVisible));$('showMapBtn').addEventListener('click',()=>toggleMap(true));$('retryMapBtn')?.addEventListener('click',()=>retryMap());$('resetMapBtn').addEventListener('click',()=>{if(!state.map)return;if(state.mapEngine==='maplibre')state.map.flyTo({center:state.mapCenter,zoom:15.1,pitch:state.map3d?56:0,bearing:state.map3d?-18:0,duration:850});else state.map.setView([state.mapCenter[1],state.mapCenter[0]],13);});$('toggle3dBtn').addEventListener('click',()=>set3D(!state.map3d));
$('addDeviceBtn').addEventListener('click',()=>addDevice().catch(error=>showToast(error.message,'error')));$('editNameBtn').addEventListener('click',()=>saveProfileName().catch(error=>showToast(error.message,'error')));$('displayNameInput').addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();saveProfileName().catch(error=>showToast(error.message,'error'));}});$('profilePhotoInput').addEventListener('change',event=>saveProfilePhoto(event.target.files?.[0]).finally(()=>event.target.value=''));
$('useNearestBtn').addEventListener('click',()=>{const module=state.nearestModule&&state.nearestModule.status==='available'?state.nearestModule:nearestAvailableModule();if(module&&module.status==='available')connectToModule(module.id);else showToast('No hay módulos disponibles.','error');});$('modalUse').addEventListener('click',()=>state.selectedModule&&connectToModule(state.selectedModule.id));$('modalDisconnect').addEventListener('click',()=>state.selectedModule&&disconnectActive(state.selectedModule.id));$('activeSessionDisconnect').addEventListener('click',()=>state.activeSession&&disconnectActive(state.activeSession.module_id));$('addModuleBtn').addEventListener('click',()=>addModule().catch(error=>showToast(error.message,'error')));$('moduleType')?.addEventListener('change',event=>{if(event.target.value==='bmax')$('modulePower').value='100 W';else if($('modulePower').value==='100 W')$('modulePower').value='30 W';});
window.addEventListener('resize',()=>{if(state.mapVisible)resizeMap();});window.addEventListener('orientationchange',()=>setTimeout(resizeMap,250));
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible'&&state.user&&state.activeSession&&!state.connectionAnimationActive)renderSessionDock();});
setInterval(async()=>{if(state.user&&document.visibilityState==='visible'){try{await loadModules();await refreshActiveSession();if(state.user.role==='admin'&&!$('view-admin').classList.contains('hidden')&&$('view-admin').classList.contains('active-view'))await loadAdminRevenue();}catch(error){console.warn(error);}}},15000);

(async()=>{bootNetworkBackground();bootSplash();bootStorm();if('serviceWorker' in navigator)navigator.serviceWorker.register('/sw.js').catch(()=>{});})();
