// Fixed Answer repeated against identical saved evidence. No new retrieval or grading.
import {readFileSync,writeFileSync,mkdirSync,appendFileSync} from 'node:fs';
import {join,resolve} from 'node:path';import {createHash} from 'node:crypto';import {parseEnv} from 'node:util';
import {completion} from '../../eval/dist/models.js';import {ANSWER_PROMPT} from '../../eval/dist/runner.js';
const parent=resolve(process.argv[2]);const inputPath=join(parent,'answers.jsonl');
const rows=readFileSync(inputPath,'utf8').trim().split('\n').map(JSON.parse);if(rows.length!==1)throw Error('Expected one diagnostic answer');
const metadata=JSON.parse(readFileSync(join(parent,'report.json')));const sha=x=>createHash('sha256').update(x).digest('hex');
if(metadata.answer_prompt_sha256!==sha(ANSWER_PROMPT)||metadata.answers_sha256!==sha(readFileSync(inputPath)))throw Error('Frozen answer identity mismatch');
const output=join(parent,'answer-repeats-v1');mkdirSync(output);
const e=parseEnv(readFileSync('.env','utf8')),saved=rows[0],results=[{repeat:0,origin:'original',answer:saved.answer}];
writeFileSync(join(output,'answers.jsonl'),JSON.stringify(results[0])+'\n');
for(let repeat=1;repeat<=2;repeat++){
 let row;try{const answer=await completion(e.MEMORY_LLM_BASE_URL,e.MEMORY_LLM_API_KEY,metadata.answer_model,[{role:'system',content:ANSWER_PROMPT},{role:'user',content:JSON.stringify({question:saved.query,memories:saved.memories})}]);row={repeat,origin:'repeat',answer};}catch(error){row={repeat,origin:'repeat',error:String(error)};}
 results.push(row);appendFileSync(join(output,'answers.jsonl'),JSON.stringify(row)+'\n');console.log(JSON.stringify(row));
}
const report={protocol:'fixed-answer-evidence-repetition-v1',source_answers_sha256:sha(readFileSync(inputPath)),answer_prompt_sha256:sha(ANSWER_PROMPT),model:metadata.answer_model,max_completion_tokens:1800,planned:3,results,scope:'Includes the original answer and two additional calls on identical saved evidence. No favorable selection, no reretrieval, no benchmark score or full-pipeline repeat claim.'};
writeFileSync(join(output,'report.json'),JSON.stringify(report,null,2)+'\n');
