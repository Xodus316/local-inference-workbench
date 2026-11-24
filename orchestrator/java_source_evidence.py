"""Conservative source anchors for pinned Java signatures and scoped edit feedback."""
import re

_NON_CODE=re.compile(r'/\*.*?\*/|//[^\r\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',re.S)
_IMPORT=re.compile(r'(?m)^\s*import\s+([\w.$]+)\s*;')


def code(text,*,preserve_literals=False):
    def erase(match):
        result=re.sub(r'[^\r\n]',' ',match[0])
        if preserve_literals and match[0][0] in {'"',"'"}:result='L'+result[1:]
        return result
    return _NON_CODE.sub(erase,text)


def constructor_calls(text):
    """Count plain new-call arguments, declining ambiguous expressions/subclasses."""
    source=code(text,preserve_literals=True)
    pattern=r'\bnew\s+([\w.$]+)(?:\s*<[^;{}()]*>)?\s*\('
    for match in re.finditer(pattern,source):
        start=match.end();parens,brackets,braces=1,0,0;commas=0
        for end in range(start,len(source)):
            char=source[end]
            if char=='(':parens+=1
            elif char==')':
                parens-=1
                if parens==0:break
            elif char=='[':brackets+=1
            elif char==']':brackets-=1
            elif char=='{':braces+=1
            elif char=='}':braces-=1
            elif char==',' and parens==1 and brackets==0 and braces==0:commas+=1
        else:continue
        arguments=source[start:end]
        if '<' in arguments or '>' in arguments or source[end+1:].lstrip().startswith('{'):continue
        yield match[1],0 if not arguments.strip() else commas+1,source.count('\n',0,match.start())+1


def focus(files,selection):
    by_path={file.path:file.content for file in files}
    owners,members,anchors=[],set(),[]
    for match in selection['matches']:
        path,line=match['path'],match['line']
        if not path.endswith('.java'):continue
        text=code(by_path[path]);lines=text.splitlines()
        if not 1<=line<=len(lines):continue
        imports={name.rsplit('.',1)[-1]:name for name in _IMPORT.findall(text)}
        source=lines[line-1]
        # A diagnostic can point at the opening line of a multiline call.
        snippet=' '.join(lines[max(0,line-2):min(len(lines),line+2)])
        for receiver,member in re.findall(r'\b(\w+)\.(\w+)\s*(?=[(,);.])',snippet):
            members.add(member)
            if receiver in imports:
                owners.append(imports[receiver])
            else:
                types=re.findall(r'\b([A-Z]\w*)(?:\s*<[^;{}()]*>)?\s+'+re.escape(receiver)+r'\b',text)
                owners.extend(imports[name] for name in types if name in imports)
        # Explicit type names at the error site, including generic type arguments.
        owners.extend(name for simple,name in imports.items() if re.search(r'\b'+re.escape(simple)+r'\b',snippet))
        anchors.append({'path':path,'line':line,'source':by_path[path].splitlines()[line-1].strip()[:500]})
    return {'focus_types':list(dict.fromkeys(owners))[:24],'members':sorted(members)[:48],
            'source_anchors':anchors[:32]}


def patch_findings(before,after,evidence):
    """Request the existing edit correction; these observations never grant a pass."""
    old={file.path:file.content for file in before}
    enums={doc['name']:set(doc['declared_fields']) for doc in evidence.get('documents',[])
           if doc.get('enum_fields_complete') is True and isinstance(doc.get('declared_fields'),list)}
    constructors={doc['name']:doc['public_constructors'] for doc in evidence.get('documents',[])
        if doc.get('public_constructors_complete') is True and isinstance(doc.get('public_constructors'),list)
        and '$' not in doc['name']}
    findings=[]
    for file in after:
        if not file.path.endswith('.java') or file.path not in old:continue
        current=code(file.content);prior=code(old[file.path])
        imports={name.rsplit('.',1)[-1]:name for name in _IMPORT.findall(current)}
        old_imports={name.rsplit('.',1)[-1]:name for name in _IMPORT.findall(prior)}
        package=re.search(r'\bpackage\s+([\w.]+)\s*;',current)
        local=set(re.findall(r'\b(?:class|interface|enum|record)\s+(\w+)',current))
        for reported,count,number in constructor_calls(file.content):
            owner=imports.get(reported,reported)
            if (owner not in constructors or reported in local
                    or package and owner.rsplit('.',1)[0]==package[1]):continue
            candidates=constructors[owner]
            if not any(count==item['parameter_count'] or item.get('varargs') is True
                       and count>=item['parameter_count']-1 for item in candidates):
                findings.append({'path':file.path,'line':number,'kind':'unsupported-public-constructor-arity',
                    'owner':owner,'argument_count':count,'declared_public_constructors':candidates,
                    'guidance':'This plain new-call has no matching public constructor in the pinned API. '
                        'Use a supported public factory/instance or a valid extension while preserving behavior; '
                        'do not guess another overload. Anonymous subclasses and ambiguous calls are not inferred.'})
        for number,line in enumerate(current.splitlines(),1):
            if re.match(r'\s*(?:import|package)\b',line):continue
            for simple,name in imports.items():
                if name not in enums:continue
                for member in re.findall(r'(?<![\w.])'+re.escape(simple)+r'\.([A-Z][A-Z0-9_]+)\b',line):
                    if member not in enums[name]:
                        findings.append({'path':file.path,'line':number,'kind':'constant-absent-from-pinned-enum',
                            'owner':name,'constant':member,'declared_constants':sorted(enums[name])})
            if re.search(r'(?m)^\s*import\s+[\w.]+\.\*\s*;',current):continue
            for simple,name in old_imports.items():
                if (simple in imports or simple in local or name.startswith('java.lang.')
                        or package and name.rsplit('.',1)[0]==package[1]):continue
                # Skip a locally declared value/type shadowing the old import.
                if re.search(r'\b(?:var|boolean|byte|short|int|long|float|double|char|[A-Z]\w*)\s+'+re.escape(simple)+r'\b',current):continue
                if line in prior.splitlines() and re.search(r'(?<![\w.])'+re.escape(simple)+r'\b',line):
                    findings.append({'path':file.path,'line':number,'kind':'removed-import-still-used',
                        'import':name,'source':file.content.splitlines()[number-1].strip()[:500]})
        if len(findings)>=64:break
    return findings[:64]
