"""Isolated CPU-only bytecode inspection; never imports or executes application code."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import zipfile

POLICY = 'pinned-java-api-probe-v1'
MAX_JARS = 2048
MAX_ENTRIES = 2_000_000
MAX_CLASSES = 12
MAX_SIGNATURE_BYTES = 24_000


def words(name):
    return {word.casefold() for word in re.findall(r'[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)', name)}


def inspect(request):
    started = time.monotonic()
    roots = [Path(root) for root in request['classpath_roots']]
    jars = sorted({p for root in roots for p in root.rglob('*.jar')})
    if len(jars) > MAX_JARS or not jars:
        raise ValueError('The approved Java cache is absent or exceeds the bounded index')
    index, entries = {}, 0
    for jar in jars:
        if time.monotonic() - started > 40:
            raise TimeoutError('Java cache indexing exceeded its bound')
        with zipfile.ZipFile(jar) as archive:
            names = archive.namelist()
            entries += len(names)
            if entries > MAX_ENTRIES:
                raise ValueError('Java cache class inventory exceeds its bound')
            for name in names:
                if (name.endswith('.class') and not name.startswith('META-INF/')
                        and not name.endswith(('module-info.class', 'package-info.class'))):
                    index.setdefault(name[:-6].replace('/', '.'), []).append(str(jar))
    symbols = request['symbols']
    tokens = set().union(*(words(name) for name in symbols)) if symbols else set()
    candidates = []
    families = {'.'.join(name.split('.')[:2]) for name in request['imports']}
    for name in index:
        if not any(name.startswith(family + '.') for family in families):
            continue
        simple = name.rsplit('.', 1)[-1]
        overlap = words(simple) & tokens
        if simple in symbols or overlap and (len(overlap) >= 2 or simple.casefold() in tokens):
            score = 100 if simple in symbols else 30 if simple.casefold() in tokens else len(overlap)
            candidates.append((-score, len(simple), name))
    ranked = [name for _, _, name in sorted(candidates)]
    exact = [name for name in request['imports'] if name in index]
    chosen = list(dict.fromkeys([*ranked, *exact]))[:MAX_CLASSES]
    javap = shutil.which('javap')
    if not javap:
        raise ValueError('The pinned image has no javap executable')
    documents, remaining = [], MAX_SIGNATURE_BYTES
    for name in chosen:
        if time.monotonic() - started > 55 or remaining < 512:
            break
        origins = sorted(index[name])
        # Inspect a named jar, rather than silently selecting a class version
        # from a combined cache classpath. Multiple cached versions are explicit.
        result = subprocess.run([javap, '-public', '-classpath', origins[0], name],
                                capture_output=True, timeout=5)
        if result.returncode:
            continue
        data = result.stdout
        if not re.search(rb'(?m)^public\s+[^\r\n]*\b(?:class|interface|enum)\s', data):
            continue
        limit = min(4096, remaining)
        content = data[:limit].decode('utf8', 'replace')
        remaining -= len(content.encode('utf8'))
        documents.append({'name': name, 'content': content, 'truncated': len(data) > limit,
                          'jar': origins[0], 'cached_versions': origins})
    return {'policy': POLICY, 'complete_index': True, 'jars_scanned': len(jars),
            'classpath_roots': request['classpath_roots'],
            'missing_imports': [name for name in request['imports'] if name not in index],
            'documents': documents,
            'guidance': 'These are public bytecode signatures in the pinned image cache. '
                        'Match jar versions to the approved build dependencies; multiple cached versions '
                        'are not proof of which version Gradle resolved. Missing imports are absent '
                        'from the declared cache roots, not from every possible Java installation.'}


if __name__ == '__main__':
    request = json.loads(Path(sys.argv[1]).read_text(encoding='utf8'))
    print('WORKBENCH_JAVA_API ' + json.dumps(inspect(request)), flush=True)
