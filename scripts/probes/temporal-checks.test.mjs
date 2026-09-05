import {test} from 'node:test';
import assert from 'node:assert/strict';
import {checkTemporalAnswer as check} from './temporal-checks.mjs';
test('operation check rejects saved false positive and unsupported request attribution',()=>{
 assert.equal(check('forget-restore','Your access code now is NEW-927. I don’t know what you asked me to do with the old one; the memories only show that it was forgotten/removed from memory.'),false);
 assert.equal(check('forget-restore',"NEW-927. You did not ask me to forget the old code."),false);
 assert.equal(check('forget-restore','NEW-927; you asked me to forget the old code.'),true);
 assert.equal(check('forget-restore','NEW-927; you asked me to forget ZX-482.'),false);
});
test('calendar checks separate correct dates, source dates, unsupported precision and explicit abstention',()=>{
 assert.equal(check('leap-day','1 March 2024'),false);assert.equal(check('leap-day','29 February 2024'),true);
 assert.equal(check('month-precision','November 2024'),true);assert.equal(check('month-precision','November 1, 2024'),false);
 assert.equal(check('synthetic-anchor','I do not know the exact calendar date; the memory only says yesterday.'),true);
 assert.equal(check('synthetic-anchor','1999-12-31, though I do not know.'),false);
});
