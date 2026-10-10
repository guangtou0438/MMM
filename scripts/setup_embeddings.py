"""Download the local model to the user's cache, never to this repository."""
import argparse
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from calendar_agent.providers.embedding import LocalEmbedding

parser = argparse.ArgumentParser()
parser.add_argument('--download', action='store_true')
args = parser.parse_args()
if args.download:
    os.environ['EMBEDDING_ALLOW_DOWNLOAD'] = '1'
provider = LocalEmbedding()
vector = provider.encode('Synthetic local embedding verification')
print({'model': provider.name, 'dimension': provider.dimension, 'bytes': len(vector), 'status': 'ready'})
