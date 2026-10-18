"""Read-only reminder for this public layout, not a semantic correctness gate."""
import argparse
from pathlib import Path, PurePosixPath
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BODY_FILES = {'wander_manager/capability_catalog.py', 'wander_manager/body_knowledge.py'}


def changed_paths(worktree=False):
    commands = [['diff', '--cached', '--name-only', '-z', '--']]
    if worktree:
        commands = [['diff', 'HEAD', '--name-only', '-z', '--'],
                    ['ls-files', '--others', '--exclude-standard', '-z']]
    paths = set()
    for args in commands:
        data = subprocess.run(['git', '-C', str(ROOT), *args], check=True,
                              capture_output=True, timeout=10).stdout
        paths.update(item.decode('utf-8') for item in data.split(b'\0') if item)
    return paths


def runtime_source(path):
    item = PurePosixPath(path)
    if set(item.parts) & {'tests', 'scripts', 'docs', 'data', 'node_modules', 'agent_tools_lib'}:
        return False
    if item.name.startswith('test_') or item.name == 'conftest.py':
        return False
    return item.suffix in {'.py', '.java', '.kt'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worktree', action='store_true')
    args = parser.parse_args()
    paths = changed_paths(args.worktree)
    count = sum(runtime_source(path) for path in paths - BODY_FILES)
    if count:
        print(f'Body review reminder: {count} runtime files changed. '
              'Review capability_catalog.py and body_knowledge.py against the public host. '
              'This check neither reads source bodies nor proves semantic synchronization.')
