import re

_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])|(//[^\n]*)|([^\s{}"]+)')


def loads(text: str) -> dict:
    stack = [{}]
    key = None
    for m in _TOKEN.finditer(text):
        quoted, brace, comment, bare = m.groups()
        if comment is not None:
            continue
        if brace == "{":
            child = {}
            stack[-1][key] = child
            stack.append(child)
            key = None
        elif brace == "}":
            stack.pop()
        else:
            tok = quoted if quoted is not None else bare
            tok = tok.replace('\\"', '"').replace("\\\\", "\\")
            if key is None:
                key = tok
            else:
                stack[-1][key] = tok
                key = None
    return stack[0]


def _esc(s) -> str:
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def dumps(obj: dict, indent: int = 0) -> str:
    out = []
    pad = "\t" * indent
    for k, v in obj.items():
        if isinstance(v, dict):
            out.append(f'{pad}"{_esc(k)}"\n{pad}{{\n{dumps(v, indent + 1)}{pad}}}\n')
        else:
            out.append(f'{pad}"{_esc(k)}"\t\t"{_esc(v)}"\n')
    return "".join(out)
