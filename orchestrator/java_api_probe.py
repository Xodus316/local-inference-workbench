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
POLICY_V2 = 'pinned-java-api-probe-v2'
MAX_JARS = 2048
MAX_ENTRIES = 2_000_000
MAX_CLASSES = 12
MAX_SIGNATURE_BYTES = 24_000


def words(name):
    return {word.casefold() for word in re.findall(r'[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)', name)}


def focused_inspection(request,index,javap,started,jars):
    """Inspect actual owners/members first, including the pinned JDK's public API."""
    jmods=Path(javap).resolve().parents[1]/'jmods'
    modules=sorted(jmods.glob('*.jmod'))
    if not modules:raise ValueError('Approved JDK inspection requires its complete jmod inventory')
    platform={}
    for module in modules:
        with zipfile.ZipFile(module) as archive:
            for path in archive.namelist():
                if path.startswith('classes/') and path.endswith('.class') and not path.endswith('module-info.class'):
                    platform[path[8:-6].replace('/','.')]=str(module)
        if len(platform)>100_000:raise ValueError('Pinned JDK inventory exceeds its bound')
    available=set(index)|set(platform)
    cache={}
    def read(name):
        if name in cache:return cache[name]
        if name not in available:return ''
        if len(cache)>=64 or time.monotonic()-started>55:
            raise TimeoutError('Focused Java signature inspection exceeded its bound')
        args=[javap,'-public']
        if name in index:args+=['-classpath',sorted(index[name])[0]]
        result=subprocess.run([*args,name],capture_output=True,timeout=5)
        text=result.stdout.decode('utf8','replace') if result.returncode==0 else ''
        if not re.search(r'(?m)^public\s+[^\r\n]*\b(?:class|interface|enum)\s',text):text=''
        cache[name]=text
        return text
    symbols=set(request['symbols'])
    members=set(request.get('members',[]))|symbols
    focus=[name for name in request.get('focus_types',[]) if name in available]
    exact_symbols=sorted(name for name in available if name.rsplit('.',1)[-1] in symbols
        and any(name.startswith('.'.join(item.split('.')[:2])+'.') for item in request['imports']))[:12]
    tokens=set().union(*(words(symbol) for symbol in symbols)) if symbols else set()
    families={'.'.join(name.split('.')[:2])+'.' for name in request['imports']}
    alternatives=sorted(((-len(words(name.rsplit('.',1)[-1])&tokens),name) for name in index
        if any(name.startswith(prefix) for prefix in families)
        and len(words(name.rsplit('.',1)[-1])&tokens)>=2))[:6]
    primary=list(dict.fromkeys([*focus,*exact_symbols]))[:10]
    # A required interface in an actual signature can have usable platform enum
    # implementations. Inspect its own package; never guess a constant or owner.
    dependencies=[]
    for name in primary:
        text=read(name)
        selected=[line for line in text.splitlines() if any(re.search(r'\b'+re.escape(member)+r'\s*\(',line) for member in members)]
        for reference in re.findall(r'\bjava\.[\w.$]+','\n'.join(selected)):
            if reference in platform and not reference.startswith('java.lang.') and reference not in primary:
                dependencies.append(reference)
    peers=[]
    for name in list(dict.fromkeys(dependencies))[:6]:
        text=read(name)
        if not re.search(r'(?m)^public (?:abstract )?interface ',text):continue
        package=name.rsplit('.',1)[0]
        candidates=sorted(other for other in platform if other.rsplit('.',1)[0]==package and '$' not in other)[:24]
        for other in candidates:
            header=next((line for line in read(other).splitlines() if line.startswith('public ')),'')
            if 'extends java.lang.Enum<' in header and re.search(r'\b'+re.escape(name)+r'\b',header):peers.append(other)
    chosen=list(dict.fromkeys([*primary,*peers,*dependencies,*[name for _,name in alternatives],
                              *[name for name in request['imports'] if name in available]]))[:MAX_CLASSES]
    documents,remaining=[],MAX_SIGNATURE_BYTES
    for name in chosen:
        text=read(name)
        if not text or remaining<512:continue
        lines=text.splitlines()
        headers=[line for line in lines if line.startswith('public ')]
        fields=[line for line in lines if re.match(r'\s+public (?:static )?(?:final )?[^()]+;',line)]
        matching=[line for line in lines if any(re.search(r'\b'+re.escape(member)+r'\s*\(',line) for member in members)]
        projected=list(dict.fromkeys([*headers,*matching,*fields]))
        # Include complete small APIs. For large APIs preserve the relevant
        # methods even when they occur beyond javap's first 4 KiB.
        if len(text.encode())<=4096:content=text
        else:content='\n'.join(projected)+'\n'
        limit=min(4096,remaining);data=content.encode();shown=data[:limit].decode('utf8','ignore')
        remaining-=len(shown.encode())
        declared=[m[1] for line in fields if (m:=re.search(r'\b(\w+)\s*;',line))]
        enum=any('extends java.lang.Enum<' in line for line in headers)
        origins=sorted(index.get(name,[]))
        documents.append({'name':name,'content':shown,'truncated':shown!=text,
            'jar':origins[0] if origins else platform[name],'cached_versions':origins or [platform[name]],
            'jdk':name in platform,'declared_fields':declared[:256],
            'enum_fields_complete':enum and len(declared)<=256,
            'member_projection':{'policy':'diagnostic-members-v1','requested':sorted(members),
                'matching_signatures_complete':all(line in shown for line in matching),
                'inherited_members_inspected':False}})
    return {'policy':POLICY_V2,'complete_index':True,'complete_jdk_index':True,
        'jars_scanned':len(jars),'jdk_modules_scanned':len(modules),
        'classpath_roots':request['classpath_roots'],
        'missing_imports':[name for name in request['imports'] if name not in available],
        'documents':documents,'source_anchors':request.get('source_anchors',[]),
        'guidance':'Exact diagnostic owner/method signatures precede other imports. Platform signatures and enum '
            'implementations come from this pinned JDK. Match argument and return types; constants on different '
            'enum owners are not interchangeable. A projected API omits unrelated members and never proves '
            'inherited members absent. Jar-cache versions do not alone prove which version Gradle resolved.'}


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
    if request.get('policy')==POLICY_V2:
        if request.get('include_jdk') is not True:raise ValueError('JDK inspection must be explicitly approved')
        javap=shutil.which('javap')
        if not javap:raise ValueError('The pinned image has no javap executable')
        return focused_inspection(request,index,javap,started,jars)
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
