"""Conservative source anchors for pinned Java signatures and scoped edit feedback."""
import re

_NON_CODE=re.compile(r'/\*.*?\*/|//[^\r\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',re.S)
_IMPORT=re.compile(r'(?m)^\s*import\s+([\w.$]+)\s*;')


def code(text):
    return _NON_CODE.sub(lambda m:re.sub(r'[^\r\n]',' ',m[0]),text)


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
    findings=[]
    for file in after:
        if not file.path.endswith('.java') or file.path not in old:continue
        current=code(file.content);prior=code(old[file.path])
        imports={name.rsplit('.',1)[-1]:name for name in _IMPORT.findall(current)}
        old_imports={name.rsplit('.',1)[-1]:name for name in _IMPORT.findall(prior)}
        package=re.search(r'\bpackage\s+([\w.]+)\s*;',current)
        local=set(re.findall(r'\b(?:class|interface|enum|record)\s+(\w+)',current))
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
