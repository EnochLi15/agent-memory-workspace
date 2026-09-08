// Exercise both repositories over real HTTP without touching a running tenant.
import {mkdtempSync,rmSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {buildServer} from '../../service/dist/server.js';
import {configFromEnv} from '../../service/dist/config.js';
import {contract} from '../../eval/dist/contract.js';
const dir=mkdtempSync(join(tmpdir(),'memory-contract-'));
let app;
try{
 app=await buildServer(configFromEnv({MEMORY_MODE:'offline',MEMORY_DATA_DIR:dir}));
 const base=await app.listen({host:'127.0.0.1',port:0});
 await contract(base);
}finally{if(app)await app.close();rmSync(dir,{recursive:true,force:true});}
