"""Re-score stored tool audits and render a compact, reproducible benchmark report."""
import argparse
import json
from pathlib import Path
from .metrics import mean, tool_accuracy


def render(data):
    lines = ['# 合成综合评测结果', '',
             '所有输入均为虚构数据。fixture 只验证链路，live 才代表真实接口调用。', '',
             '| 模型 | 组别 | 成功率 | Recall@8 | 忘记误召回 | 平均步骤 | 实际 Token | 总延迟/秒 |',
             '| --- | --- | --- | --- | --- | --- | --- | --- |']
    def number(value): return '—' if value is None else f'{value:.3f}'
    for group in data['memory']['groups']:
        metrics = group['metrics']
        lines.append('| '+' | '.join([group['model'], group['group']]+[number(metrics[k]) for k in
                      ('success','recall_at_8','forgotten_recall','steps','provider_tokens','total_seconds')])+' |')
    lines += ['', '每组八题。C 组的巩固调用与问答成本分别保存在 JSON；首 Token、引用覆盖、引用正确率和关键事实保持同样保留逐题证据。', '',
              '| 模型 | 阶段 | 成功率 | 工具选择 | 误操作 | 平均步骤 | 实际 Token |',
              '| --- | --- | --- | --- | --- | --- | --- |']
    for model in data['workflows']['models']:
        for row in model['records']:
            expected = ['create_task'] if row['phase']=='baseline' else ['get_weather','create_adjustment_suggestion']
            row['tool_accuracy'] = tool_accuracy(expected,row['tools'])
        metrics = {phase:{key:mean([r.get(key) for r in model['records'] if r['phase']==phase])
                         for key in ('success','tool_accuracy','misoperations','steps','provider_tokens','seconds')}
                   for phase in ('baseline','before','after')}
        model['metrics']=metrics
        for phase, values in metrics.items():
            lines.append('| '+' | '.join([model['model'],phase]+[number(values[k]) for k in
                          ('success','tool_accuracy','misoperations','steps','provider_tokens')])+' |')
        lines += ['', f"{model['model']} 候选状态：{model['candidate_status']}。"+(model.get('reason') or ''), '']
    lines += ['工具选择分数衡量必需工具覆盖，并惩罚失败或无关调用；读取日历作为允许的前置步骤。', '',
              '天气回放每阶段三种合成预报，启用前后复用相同目标。该规模只能支持工程演示，不能证明普遍质量提升。', '']
    data['scoring_version']='synthetic-v2-audit-penalized'
    return '\n'.join(lines)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('input',type=Path)
    parser.add_argument('--output',type=Path,default=Path('artifacts/evaluation-report.md'))
    args=parser.parse_args()
    data=json.loads(args.input.read_text(encoding='utf-8'))
    report=render(data)
    args.input.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    args.output.write_text(report,encoding='utf-8')
    print(args.output)


if __name__=='__main__': main()
