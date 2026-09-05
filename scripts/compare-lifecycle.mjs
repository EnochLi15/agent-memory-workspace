import {writeFileSync} from 'node:fs';
const result={date:new Date().toISOString(),scope:'Synthetic behavioral comparison, not benchmark accuracy',systems:{}};
for(const [name,base] of [['U0','http://127.0.0.1:8091'],['candidate','http://127.0.0.1:8090']]){
 const user=`lifecycle-${name}-${Date.now()}`;const post=async(path,body)=>{const r=await fetch(base+path,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(120000)});const d=await r.json();if(!r.ok)throw Error(JSON.stringify(d));return d;};
 const add=async(id,content,timestamp)=>post('/add',{request_id:id,user_id:user,session_id:id,messages:[{role:'user',content,timestamp}]});
 try{
  await add('a','My current city is Seattle. My access code is LL-378. My manager is Clara.','2026-01-01T00:00:00Z');
  await add('b','I now live in Portland. That is my current city.','2026-02-01T00:00:00Z');
  const current=await post('/search',{query:'What is my current city?',user_id:user,top_k:100});
  await add('c','Please forget my access code and keep my manager and city.','2026-03-01T00:00:00Z');
  const forgotten=await post('/search',{query:'What was my access code LL-378?',user_id:user,top_k:100});
  const neighbor=await post('/search',{query:'Who is my manager?',user_id:user,top_k:100});
  result.systems[name]={old_city_in_current_evidence:current.data.some(x=>/Seattle/i.test(x.content)),forgotten_value_in_evidence:forgotten.data.some(x=>x.content.includes('LL-378')),neighbor_retained:neighbor.data.some(x=>x.content.includes('Clara')),evidence:{current,forgotten,neighbor}};
 }catch(e){result.systems[name]={error:e.message};}
 console.log(name,JSON.stringify(result.systems[name]));
}
writeFileSync('reports/lifecycle-comparison.json',JSON.stringify(result,null,2)+'\n');
