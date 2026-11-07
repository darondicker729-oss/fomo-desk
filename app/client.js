'use strict';
// Decrypts the same AES-GCM snapshot as the desktop dashboard. The key comes only
// from the private link's #t= fragment, which browsers never send to a server.
window.DeskClient=(()=>{
 const label=new TextEncoder().encode('fomo-desk-data-v1'),info=new TextEncoder().encode('snapshot-encryption');
 const standalone=navigator.standalone===true||matchMedia('(display-mode: standalone)').matches;
 const frag=new URLSearchParams(location.hash.slice(1));
 const store=standalone?localStorage:sessionStorage,K='fomo-app-key';
 let token=frag.get('t')||store.getItem(K)||sessionStorage.getItem('desk-decryption-token')||'';
 // Keep the fragment in Safari so "Add to Home Screen" captures it; the installed app
 // keeps the key in its own sandboxed storage and drops it from the visible URL.
 if(token)store.setItem(K,token);
 if(standalone&&frag.has('t'))history.replaceState(null,'',location.pathname+location.search);
 let keyP=null,snap=null,etag='';
 const locked=()=>{const e=new Error('locked');e.locked=true;return e};
 function key(){if(!token)throw locked();return keyP||(keyP=crypto.subtle.importKey('raw',new TextEncoder().encode(token),'HKDF',false,['deriveKey']).then(k=>crypto.subtle.deriveKey({name:'HKDF',hash:'SHA-256',salt:label,info},k,{name:'AES-GCM',length:256},false,['decrypt'])))}
 async function load(){
  if(!token)throw locked();
  const r=await fetch(new URL('../d/state.bin?v='+Math.floor(Date.now()/20000),location.href),{cache:'no-store',credentials:'omit',referrerPolicy:'no-referrer'});
  if(!r.ok)throw Error('Snapshot unavailable ('+r.status+')');
  const b=new Uint8Array(await r.arrayBuffer());
  if(b.length<32||new TextDecoder().decode(b.slice(0,4))!=='FDM1')throw Error('Unsupported snapshot');
  let pt;try{pt=await crypto.subtle.decrypt({name:'AES-GCM',iv:b.slice(4,16),additionalData:label},await key(),b.slice(16))}catch(e){throw locked()}
  const s=JSON.parse(new TextDecoder().decode(pt));
  if(s.version!==1||!s.routes)throw Error('Invalid snapshot');
  snap=s;return s;
 }
 function forget(){localStorage.removeItem(K);sessionStorage.removeItem(K);sessionStorage.removeItem('desk-decryption-token');token='';keyP=null;snap=null}
 return{load,forget,standalone,get snap(){return snap}};
})();
