'use strict';
window.EncryptedDesk=(()=>{
 const label=new TextEncoder().encode('fomo-desk-data-v1'),info=new TextEncoder().encode('snapshot-encryption');
 const fragment=new URLSearchParams(location.hash.slice(1));
 let token=fragment.get('t')||sessionStorage.getItem('desk-decryption-token')||'';
 if(fragment.has('t')){sessionStorage.setItem('desk-decryption-token',token);history.replaceState(null,'',location.pathname+location.search)}
 // Never retain keys in persistent storage, nor send them as headers, paths, bodies or queries.
 localStorage.removeItem('desk-token');localStorage.removeItem('desk-data-path');
 let keyPromise=null,snapshot=null,lastFetch=0,pending=null;
 function locked(){const e=new Error('Desk locked');e.locked=true;return e}
 function key(){
  if(!token)throw locked();
  if(!keyPromise)keyPromise=crypto.subtle.importKey('raw',new TextEncoder().encode(token),'HKDF',false,['deriveKey']).then(k=>crypto.subtle.deriveKey({name:'HKDF',hash:'SHA-256',salt:label,info},k,{name:'AES-GCM',length:256},false,['decrypt']));
  return keyPromise;
 }
 async function load(){
  if(!token)throw locked();
  if(snapshot&&Date.now()-lastFetch<10000)return snapshot;
  if(pending)return pending;
  pending=(async()=>{
   const response=await fetch(new URL('./d/state.bin?v='+Math.floor(Date.now()/15000),location.href),{cache:'no-store',credentials:'omit',referrerPolicy:'no-referrer'});
   if(!response.ok)throw Error('Encrypted snapshot unavailable');
   const bytes=new Uint8Array(await response.arrayBuffer());
   if(bytes.length<32||new TextDecoder().decode(bytes.slice(0,4))!=='FDM1')throw Error('Unsupported encrypted snapshot');
   let plaintext;
   try{plaintext=await crypto.subtle.decrypt({name:'AES-GCM',iv:bytes.slice(4,16),additionalData:label},await key(),bytes.slice(16))}catch(e){throw locked()}
   const next=JSON.parse(new TextDecoder().decode(plaintext));
   if(next.version!==1||typeof next.generated_at!=='number'||!next.routes)throw Error('Invalid snapshot');
   snapshot=next;lastFetch=Date.now();return snapshot;
  })();
  try{return await pending}finally{pending=null}
 }
 async function api(path,body){
  if(body!==undefined)throw Error('Read-only encrypted desk. Send commands to your Telegram bot.');
  const snap=await load(),route=path.split('?')[0];
  if(route==='search'){
   const q=(new URLSearchParams(path.split('?')[1]).get('q')||'').toLowerCase();
   return{rows:(snap.routes['coins-index']?.rows||[]).filter(c=>[c.mint,c.name,c.symbol].some(x=>(x||'').toLowerCase().includes(q)))};
  }
  if(!(route in snap.routes)){
   if(route.startsWith('audit/'))return{mint:route.slice(6),name:'No audit yet',layers:[],passed:null,reason:null,coin_url:''};
   throw Error('Not available in the published snapshot');
  }
  return snap.routes[route];
 }
 function showAge(){
  const node=document.getElementById('data-age');if(!node||!snapshot)return;
  const seconds=Math.max(0,Math.floor(Date.now()/1000-snapshot.generated_at)),age=seconds<60?seconds+'s':Math.floor(seconds/60)+'m';
  node.hidden=false;node.textContent='Data age '+age+' · updated '+new Date(snapshot.generated_at*1000).toLocaleTimeString('en-US',{timeZone:'America/New_York',hour:'numeric',minute:'2-digit'})+' ET';
 }
 setInterval(showAge,1000);
 return{api,showAge};
})();
