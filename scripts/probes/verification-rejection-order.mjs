// Regression at the real Models.verify seam; model outputs are fixed, no API.
import {resolve,join} from 'node:path';import {pathToFileURL} from 'node:url';import assert from 'node:assert/strict';
const runtime=resolve(process.argv[2]??'service');
const {Models}=await import(pathToFileURL(join(runtime,'dist/models.js')));
const {configFromEnv}=await import(pathToFileURL(join(runtime,'dist/config.js')));
const {extractionSchema}=await import(pathToFileURL(join(runtime,'dist/types.js')));
const browser='My browser is Firefox.',plan='I will visit Paris next month.';
const req={request_id:'ordering',user_id:'u',session_id:'s',messages:[{role:'user',content:browser+' '+plan,timestamp:'2026-01-01T00:00:00Z'}]};
const p=extractionSchema.parse({facts:[{content:browser,subject:'user',predicate:'browser',value:'Firefox',sources:[{index:0,quote:browser}]}]});
const model=new Models(configFromEnv({}));let calls=0;
model.json=async()=>{calls++;const f={index:0,supported:true,modality_supported:true,source_index:0,quote:browser};return {fact_checks:calls===1?[f,f]:[f],operation_checks:[],replacement_checks:[],message_checks:calls===1?[{index:0,disposition:'missing',quote:plan,reason:'Personal travel plan missing'}]:[{index:0,disposition:'represented',fact_indices:[0]}]};};
const issues=await model.verify(p,req,[],[],AbortSignal.timeout(1000));console.log(JSON.stringify({runtime,calls,issues}));
assert.equal(calls,1,'A malformed fact array must not trigger resampling of a valid later coverage rejection');assert.ok(issues.some(x=>x.startsWith('message 0:')));
