import re, html as h, os

HTML_PATH = r'C:\Users\Asus\Desktop\记忆\.scratch\reflexion-2303.11366.html'
OUT_PATH = r'C:\Users\Asus\Desktop\记忆\docs\research\papers\02-reflexion-fulltext.md'

with open(HTML_PATH, encoding='utf-8') as f:
    raw = f.read()

m = re.search(r'<article[^>]*>(.*?)</article>', raw, re.S)
body = m.group(1) if m else raw

# strip scripts/styles/comments
body = re.sub(r'<!--.*?-->', '', body, flags=re.S)
body = re.sub(r'<script.*?</script>', '', body, flags=re.S)
body = re.sub(r'<style.*?</style>', '', body, flags=re.S)

# math -> TeX annotation
def math_repl(mt):
    ann = re.search(r'<annotation[^>]*>(.*?)</annotation>', mt.group(1), re.S)
    if ann:
        tex = h.unescape(ann.group(1)).strip().replace('\n', ' ')
        return ' $' + tex + '$ '
    return ' '

body = re.sub(r'<math[^>]*>(.*?)</math>', math_repl, body, flags=re.S)

# remove base64 images
body = re.sub(r'<img[^>]*src="data:image[^"]*"[^>]*>', '', body, flags=re.S)
body = re.sub(r'<img[^>]*alt="([^"]*)"[^>]*>', r'[图: \1]', body)

# headings -> markdown
for i in range(6, 0, -1):
    body = re.sub(r'<h'+str(i)+r'[^>]*>(.*?)</h'+str(i)+r'>',
                  lambda mm, n=i: '\n\n' + '#'*n + ' ' + mm.group(1).strip() + '\n\n',
                  body, flags=re.S)

# block-level tags -> newline
for tag in ['figcaption','figure','section','div','p','table','thead','tbody','tr','ul','ol','li','br','hr','blockquote','pre','caption']:
    body = re.sub(r'<'+tag+r'[^>]*>', '\n', body, flags=re.I)
    body = re.sub(r'</'+tag+r'>', '\n', body, flags=re.I)
# table cells
body = re.sub(r'<t[dh][^>]*>', ' | ', body, flags=re.I)
body = re.sub(r'</t[dh]>', '', body, flags=re.I)
# remaining tags
body = re.sub(r'<[^>]+>', '', body)
body = h.unescape(body)

# normalize whitespace
body = re.sub(r'[ \t]+\n', '\n', body)
body = re.sub(r'\n[ \t]+', '\n', body)
body = re.sub(r'\n{3,}', '\n\n', body)
body = body.strip()

os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
with open(OUT_PATH, 'w', encoding='utf-8') as f:
    f.write('# Reflexion: Language Agents with Verbal Reinforcement Learning (全文抓取)\n\n')
    f.write('> 来源: https://arxiv.org/html/2303.11366 (arXiv HTML) | 抓取日期: 2026-08-11\n\n')
    f.write(body + '\n')

print('OK chars=', len(body))
