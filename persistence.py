"""Persist the outbox and heartbeat, rebasing safely over concurrent edits."""
import json
import os
import subprocess
import time
from pathlib import Path

FILES = ('state.json', 'health.json')

def atomic_write(path, value):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    tmp.replace(path)

def merge_state(a, b):
    delivered = set(a.get('delivered', [])) | set(b.get('delivered', []))
    pending = dict(a.get('pending', {}), **b.get('pending', {}))
    stock = dict(a.get('stock', {}))
    for url, value in b.get('stock', {}).items():
        if value.get('observed_at', '') >= stock.get(url, {}).get('observed_at', ''):
            stock[url] = value
    return {'stock': stock, 'categories': sorted(set(a.get('categories', [])) | set(b.get('categories', []))),
            'products': dict(a.get('products', {}), **b.get('products', {})),
            'delivered': sorted(delivered),
            'pending': {k: v for k, v in pending.items() if k not in delivered}}

def git(*args, check=True):
    return subprocess.run(['git', *args], check=check, capture_output=True, text=True,
                          timeout=45, env=dict(os.environ, GIT_EDITOR='true'))

def save_state():
    paths = [p for p in FILES if Path(p).exists()]
    git('add', '--', *paths)
    diff = git('diff', '--cached', '--quiet', check=False)
    if diff.returncode == 1:
        git('commit', '-m', 'Update monitor state and heartbeat')
    elif diff.returncode:
        raise RuntimeError('Cannot inspect persisted state')
    # Push even when the tree is clean: an earlier push may have failed.
    for attempt in range(3):
        if git('push', 'origin', 'HEAD:main', check=False).returncode == 0:
            return
        git('fetch', 'origin', 'main')
        rebased = git('rebase', 'origin/main', check=False)
        while rebased.returncode:
            conflicts = git('diff', '--name-only', '--diff-filter=U').stdout.splitlines()
            if not conflicts or any(p not in FILES for p in conflicts):
                git('rebase', '--abort', check=False)
                raise RuntimeError('Cannot safely rebase state; restart from latest code')
            for path in conflicts:
                versions = []
                for stage in (2, 3):
                    blob = git('show', f':{stage}:{path}', check=False)
                    versions.append(json.loads(blob.stdout) if blob.returncode == 0 else {})
                if path == 'state.json':
                    merged = merge_state(*versions)
                else:
                    merged = max(versions, key=lambda x: x.get('last_success_utc', ''))
                atomic_write(path, merged)
                git('add', '--', path)
            rebased = git('rebase', '--continue', check=False)
        if attempt < 2:
            time.sleep(2)
    raise RuntimeError('State push failed after three attempts')

