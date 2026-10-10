"""One command for memory ablation, compression checks, tools and procedural replay."""
import argparse
import json
from pathlib import Path
from .ablations import evaluate
from .workflows import evaluate_workflows
from .report import render


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--semantic',action='store_true')
    parser.add_argument('--output',type=Path,default=Path('artifacts/evaluation-suite.json'))
    args=parser.parse_args()
    result={'memory':evaluate(args.live,args.semantic),'workflows':evaluate_workflows(args.live)}
    report=render(result)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    args.output.with_suffix('.md').write_text(report,encoding='utf-8')
    print(json.dumps({'output':str(args.output),'mode':'live' if args.live else 'fixture'}))


if __name__=='__main__': main()
