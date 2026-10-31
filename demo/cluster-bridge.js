// Only the paired cluster window may read or edit these demo-only settings.
(() => {
  'use strict';
  const ORIGIN='https://www.joongyu.co.kr',CHANNEL='cluster-pilot-v1';
  const KEYS=['ClusterLaneTest','ClusterLaneTestValue','ClusterLaneTestFrom','ClusterLaneTestTo'];
  const {BASE}=window.__CARROT_DEMO__,meta=window.__CARROT_DEMO_META__||{};
  if(window.parent===window)return;
  let subscribed=false,writing=false,timer=null;
  const catalog=fetch(BASE+'_demo/cluster-fields.json?v='+encodeURIComponent(meta.demoCommit||meta.commit||'local')).then(async r=>{if(!r.ok)throw Error('계기판 신호 정의를 읽지 못했습니다.');return r.json();});
  const send=(type,data={})=>window.parent.postMessage({channel:CHANNEL,type,...data},ORIGIN);
  const error=e=>send('error',{error:String(e.message||e).slice(0,200)});
  async function snapshot(refreshUI=false){
    const definitions=await catalog;
    const r=await fetch('/api/params_bulk?names='+KEYS.join(','));
    const body=await r.json();if(!r.ok||!body.ok)throw Error(body.error||'데모 설정을 읽지 못했습니다.');
    const values=Object.fromEntries(KEYS.map(k=>[k,body.values?.[k]]));
    if(refreshUI)window.dispatchEvent(new CustomEvent('carrot:paramsrestored',{detail:{values}}));
    send('snapshot',{values,mapping:definitions.fields[String(values.ClusterLaneTest)]||null,
      settings:definitions.settings,source:{commit:meta.commit,branch:meta.branch}});
  }
  async function receive(event){
    if(event.origin!==ORIGIN||event.source!==window.parent||event.data?.channel!==CHANNEL)return;
    const msg=event.data;
    if(msg.type==='subscribe'){subscribed=true;await snapshot();return;}
    if(msg.type==='unsubscribe'){subscribed=false;return;}
    if(msg.type!=='set'||!subscribed||writing)return;
    const definitions=await catalog,values=msg.values;
    if(!values||Object.keys(values).length!==2||!['ClusterLaneTest','ClusterLaneTestValue'].every(k=>Object.hasOwn(values,k)))throw Error('계기판 테스트 칸과 값만 변경할 수 있습니다.');
    for(const [key,value] of Object.entries(values)){
      const bounds=definitions.settings[key];
      if(!bounds||!Number.isSafeInteger(value)||value<bounds.min||value>bounds.max)throw Error('지원 범위를 벗어난 계기판 테스트 값입니다.');
    }
    writing=true;
    try{
      for(const [name,value] of Object.entries(values)){
        const r=await fetch('/api/param_set',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,value,source:'cluster-preview'})});
        const body=await r.json();if(!r.ok||body.ok===false)throw Error(body.error||'데모 설정 저장 실패');
      }
    }finally{writing=false;await snapshot(true);}
  }
  window.addEventListener('message',event=>receive(event).catch(error));
  window.addEventListener('carrot-demo-settings-changed',()=>{if(!subscribed||writing)return;clearTimeout(timer);timer=setTimeout(()=>snapshot().catch(error),40);});
  catalog.then(()=>send('ready')).catch(error);
})();
