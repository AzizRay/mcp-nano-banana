# prompt_builder.py (no external deps)
import re

def _get(ctx, path):
    cur = ctx
    for k in path.split('.'):
        cur = cur.get(k) if isinstance(cur, dict) else None
        if cur is None: break
    return cur

def _eval_cond(expr, ctx):
    expr = expr.trim() if hasattr(expr, "trim") else str(expr).strip()
    m = re.match(r'^\(eq\s+([A-Za-z0-9_.-]+)\s+"([^"]+)"\)$', expr)
    if m:
        return str(_get(ctx, m.group(1))) == m.group(2)
    v = _get(ctx, expr)
    return bool(v)

_block_re = re.compile(r'{{#if\s+([^}]+)}}([\s\S]*?){{/if}}')
_var_re   = re.compile(r'{{\s*([A-Za-z0-9_.-]+)\s*}}')

def _render_blocks(text, ctx):
    def repl(m):
        cond, inner = m.group(1), m.group(2)
        return inner if _eval_cond(cond, ctx) else ''
    return _block_re.sub(repl, text)

def _render_vars(text, ctx):
    def repl(m):
        v = _get(ctx, m.group(1))
        return '' if v is None else str(v)
    return _var_re.sub(repl, text)

def _render_line(line, ctx):
    return re.sub(r'\s+', ' ', _render_vars(_render_blocks(line, ctx), ctx)).strip()

def compose_prompt(spec: dict, params: dict | None = None):
    params = params or {}
    merged = dict(spec.get('defaults', {}))
    merged.update(params)

    pos = ' '.join([_render_line(l, merged) for l in spec.get('builders', {}).get('positive', []) if l.strip()])
    neg = ' '.join([_render_line(l, merged) for l in spec.get('builders', {}).get('negative', []) if l.strip()])

    include_neg = spec.get('compose', {}).get('includeNegative', True)
    neg_prefix  = spec.get('compose', {}).get('negativePrefix', 'Negative prompt: ')
    prompt = f"{pos} {neg_prefix}{neg}".strip() if include_neg else pos

    meta_keys = spec.get('output', {}).get('meta', [])
    meta = {k: merged.get(k) for k in meta_keys}
    return {"prompt": prompt, "negative": neg, "meta": meta}
