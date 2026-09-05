// Versioned, deliberately narrow acceptance checks for these synthetic fixtures.
// These checks are not a general semantic judge or a benchmark accuracy metric.
const unknown=/\b(?:cannot|can.t|don.t know|do not know|unknown|unsure|not (?:known|provided|specified|enough)|no (?:real|actual|calendar|date)|doesn.t (?:give|provide))\b/i;
export function checkTemporalAnswer(id,answer){
 const a=answer.replace(/[’‘]/g,"'");
 if(id==='leap-day')return /2024-02-29|February 29(?:,)? 2024|29 February 2024/i.test(a)&&!unknown.test(a);
 if(id==='month-precision')return /November 2024/i.test(a)&&!/November \d{1,2}(?:st|nd|rd|th)?(?:,|\s+2024)|2024-11-\d\d/i.test(a);
 if(id==='trajectory')return /Oslo[\s\S]*Bergen[\s\S]*Troms[oø]/i.test(a)&&!unknown.test(a);
 if(id==='forget-restore')return /NEW-927/.test(a)&&/forget|forgot|remov|delet/i.test(a)&&!/ZX-482/.test(a)&&!unknown.test(a)&&!/\b(?:did not|didn't|never) (?:ask|request)|(?:not|never) (?:forget|remove|delete)/i.test(a);
 if(id==='synthetic-anchor')return !/1999|2000/.test(a)&&unknown.test(a);
 throw Error('Unknown synthetic acceptance case: '+id);
}
