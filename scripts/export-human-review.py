"""Render the frozen calibration packet without prior verdicts or generated labels."""
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
source = root / 'eval/artifacts/round2-judge-calibration-v1/blind.jsonl'
rows = [json.loads(line) for line in source.read_text().splitlines()]
assert len({row['qid'] for row in rows}) == len(rows)
out = root / 'artifacts/round2-human-review'
out.mkdir(exist_ok=True)
text = ['# 记忆问答独立人工盲审\n',
        '这份材料包含预先选定的199条保存答案，隐藏了原模型结论和分歧标签。请按问题、参考答案和rubric独立判断，允许语义等价表达。缺少必需信息与说出了禁止内容分别判断。无法确定时标为 uncertain。\n',
        '评审人应未参与对应模型判分。不要把模型意见填作人工结论。请记录支持判断的答案原文、缺失项或有害额外断言，不虚构引用。\n',
        '填写 reviews.jsonl：reviewer 为评审者标识，reviewer_kind 为 human，correct 为 true/false（不确定用 null），reason 说明理由，status 为 reviewed/uncertain。qid 保持原样。criteria 可逐项填写 {criterion, verdict, answer_quote, reason}。当前尚无人工结论。\n']
for index, row in enumerate(rows, 1):
    text.append(f"## {index}. {row['qid']}\n\n**问题**\n\n{row['question']}\n\n**选项**\n\n{json.dumps(row.get('options'), ensure_ascii=False)}\n\n**参考答案**\n\n{row['reference']}\n\n**判据**\n\n```json\n{json.dumps(row['rubric'], ensure_ascii=False, indent=2)}\n```\n\n**待评答案**\n\n" + '\n'.join('> ' + line for line in row['answer'].splitlines()) + '\n')
(out / 'review.md').write_text('\n'.join(text))
response = out / 'reviews.jsonl'
if not response.exists():
    response.write_text(''.join(json.dumps({'qid': row['qid'], 'reviewer': None, 'reviewer_kind': None, 'correct': None, 'criteria': [], 'reason': None, 'status': 'pending_independent_review'}, ensure_ascii=False) + '\n' for row in rows))
manifest = {'protocol': 'independent-human-review-packet-v1', 'source': str(source.relative_to(root)), 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'cases': len(rows), 'review_document': str((out/'review.md').relative_to(root)), 'response_file': str(response.relative_to(root)), 'prior_verdicts_included': False, 'existing_responses_preserved': True, 'note': 'Rendering never supplies human labels. Submitted labels require separate identity and content validation.'}
(out / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(manifest, ensure_ascii=False))
