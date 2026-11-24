"""Archive the Olifis forum topic "Soluzioni SNS" (t=228) and every topic it links to, as a static site.

Usage (from the repo root):
    python -I build_archive.py            # fetch what is missing, then build the site
    python -I build_archive.py --build    # rebuild the site from the pages already fetched
    add --retry-images to try again the images that could not be saved (raw/img_failed.json)

Pages fetched from the forums are kept in raw/ (not committed), so a rebuild never asks the forum again.
The site goes to site/: open site/index.html in a browser. It works offline.
"""
import argparse, codecs, concurrent.futures, hashlib, html, json, re, sys, time, urllib.parse, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW = ROOT / 'raw'
SITE = ROOT / 'site'
UA = {'User-Agent': 'Mozilla/5.0 (archivio Olifis)'}
INDEX_TOPIC = 228
FORUMS = {
    'olifis': 'https://www.olifis.it/forum.olifis.it/viewtopic.php',
    'oliforum': 'http://www.oliforum.it/viewtopic.php',
}
SITE_NAME = 'Archivio del forum Olifis'
# figures from the forum's front page, November 2026
FORUM_STATS = {'topics': '2.622', 'posts': '19.681', 'members': '1.529', 'last': '2026'}
# sections of chosen discussions; their topics are listed in selezioni.json and were downloaded to raw/sezioni/<folder>
SECTIONS = [
    {'key': 'teorici', 'folder': 'problemi-teorici', 'page': 'problemi-teorici.html', 'total': 1463, 'title': 'Altri problemi teorici',
     'lede': ''},
    {'key': 'olimpiadi', 'folder': 'olimpiadi-di-fisica', 'page': 'olimpiadi-caffe.html', 'total': 251, 'title': 'Olimpiadi di Fisica',
     'lede': 'Le gare italiane e internazionali raccontate da chi le ha preparate e fatte: prove, preparazione, risultati.'},
    {'key': 'caffe', 'folder': 'caffe', 'page': 'olimpiadi-caffe.html', 'total': 265, 'title': 'Caffè',
     'lede': 'La sezione per parlare di tutto il resto: università, curiosità, attualità.'},
]
# pages of chosen discussions: one or more sections each
PAGES = [
    {'page': 'problemi-teorici.html', 'title': 'Altri problemi teorici', 'keys': ['teorici'],
     'lede': 'Problemi di fisica proposti, risolti e discussi nella sezione più grande del forum, oltre a quelli della staffetta e delle prove di ammissione: una selezione.',
     'card': 'Una selezione degli altri problemi proposti, risolti e discussi sul forum.'},
    {'page': 'olimpiadi-caffe.html', 'title': 'Olimpiadi di Fisica e Caffè', 'keys': ['olimpiadi', 'caffe'],
     'lede': 'Una selezione di discussioni dalle sezioni dedicate alle gare e a tutto il resto.',
     'card': 'Le gare raccontate da chi le ha fatte, e le chiacchiere migliori del forum.'},
]
MATHJAX_FILE ='tex-svg-full.js'  # the full build: every TeX extension is inside, nothing is loaded later
MATHJAX_URL = 'https://cdn.jsdelivr.net/npm/mathjax@3.2.2/es5/' + MATHJAX_FILE

# Old posts mix Latin-1 bytes into UTF-8 pages: decode those bytes as cp1252 rather than losing them.
codecs.register_error('cp1252', lambda e: (e.object[e.start:e.start + 1].decode('cp1252', 'replace'), e.start + 1))


def get(url, timeout=30):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read(), r.headers.get('Content-Type', '')


def decode(b):
    return b.decode('utf8', 'cp1252')


# ---------- recognising links to topics ----------

TOPIC_RE = re.compile(r'(?:https?://)?(?:www\.)?(?P<host>olifis\.it/forum\.olifis\.it|forum\.olifis\.it(?:/olifis/phpBB3)?|oliforum\.it)/viewtopic\.php\?(?P<q>[^"\'#\s<]*)(?:#p(?P<anchor>\d+))?')


def topic_key(url):
    """('olifis', 't', 148) / ('olifis', 'p', 1356) for a link to a topic or a post, else None."""
    url = html.unescape(url)
    m = TOPIC_RE.search(url)
    if not m:
        return None
    forum = 'oliforum' if 'oliforum' in m['host'] else 'olifis'
    q = urllib.parse.parse_qs(m['q'])
    if 't' in q and q['t'][0].isdigit():
        return (forum, 't', int(q['t'][0]))
    if 'p' in q and q['p'][0].isdigit():
        return (forum, 'p', int(q['p'][0]))
    return None


def local_relative(forum, href):
    """A relative link inside a fetched page (./viewtopic.php?...) made absolute."""
    base = FORUMS[forum].rsplit('/', 1)[0] + '/'
    url = urllib.parse.urljoin(base, html.unescape(href))
    # drop the forum's session id, which changes on every visit
    return re.sub(r'([?&])sid=[0-9a-f]+&?', r'\1', url).rstrip('?&')


# ---------- fetching ----------

def raw_path(forum, t, start):
    return RAW / forum / f't{t}_s{start}.html'


DELAY = 2  # olifis.it robots.txt asks for Crawl-delay: 2; used for OliForum too


def fetch_topic(forum, t, delay=DELAY):
    """Fetch every page of a topic into raw/. Returns the list of page files.
    OliForum's robots.txt disallows &start=, so only its first page is fetched."""
    first = raw_path(forum, t, 0)
    if not first.exists():
        first.parent.mkdir(parents=True, exist_ok=True)
        b, _ = get(f'{FORUMS[forum]}?t={t}')
        first.write_bytes(b)
        time.sleep(delay)
    h = decode(first.read_bytes())
    starts = {0}
    for href in re.findall(r'href="([^"]*viewtopic\.php\?[^"]*start=\d+[^"]*)"', h):
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(html.unescape(href)).query)
        if q.get('t') == [str(t)] and q.get('start', [''])[0].isdigit():
            starts.add(int(q['start'][0]))
    starts = sorted(starts)
    # phpBB shows only some page numbers: fill the gaps from the step and the last page
    if len(starts) > 1:
        step = starts[1]
        starts = list(range(0, starts[-1] + 1, step))
    if forum == 'oliforum':
        starts = [0]
    files = []
    for s in starts:
        p = raw_path(forum, t, s)
        if not p.exists():
            b, _ = get(f'{FORUMS[forum]}?t={t}&start={s}')
            p.write_bytes(b)
            time.sleep(delay)
        files.append(p)
    return files


# ---------- parsing ----------

def balanced_div(h, i):
    """Inner HTML of the <div> whose opening tag starts at h[i]."""
    j = h.index('>', i) + 1
    depth, k = 1, j
    for m in re.finditer(r'<div\b|</div>', h[j:]):
        depth += 1 if m.group() == '<div' else -1
        if depth == 0:
            return h[j:j + m.start()]
    return h[j:]


def parse_page(h):
    title = re.search(r'<h2 class="topic-title"><a[^>]*>(.*?)</a>', h, re.S)
    posts = []
    starts = list(re.finditer(r'<div id="p(\d+)" class="post\b', h))
    for n, m in enumerate(starts):
        chunk = h[m.start():starts[n + 1].start() if n + 1 < len(starts) else len(h)]
        author = re.search(r'class="username(?:-coloured)?"[^>]*>(.*?)</a>', chunk, re.S)
        if not author:
            author = re.search(r'<span class="username(?:-coloured)?"[^>]*>(.*?)</span>', chunk, re.S)
        date = re.search(r'&raquo;\s*</span>\s*(?:<time[^>]*>)?([^<]+)', chunk)
        subj = re.search(r'<h3[^>]*><a href="#p\d+">(.*?)</a></h3>', chunk, re.S)
        c = chunk.find('<div class="content">')
        if c < 0:
            continue
        content = balanced_div(chunk, c)
        # attachments (pictures and files uploaded to the forum) sit in their own box after the text
        a = chunk.find('<dl class="attachbox">')
        if a >= 0:
            depth, end = 0, len(chunk)
            for mm in re.finditer(r'<dl\b|</dl>', chunk[a:]):
                depth += 1 if mm.group() == '<dl' else -1
                if depth == 0:
                    end = a + mm.end()
                    break
            content += '\n' + chunk[a:end]
        posts.append({
            'id': int(m[1]),
            'author': html.unescape(re.sub('<[^>]+>', '', author[1])).strip() if author else '?',
            'date': html.unescape(date[1]).strip() if date else '',
            'subject': subj[1].strip() if subj else '',
            'content': content,
        })
    return (title[1].strip() if title else None), posts


def load_topic(forum, t):
    files = sorted(RAW.glob(f'{forum}/t{t}_s*.html'), key=lambda p: int(p.stem.split('_s')[1]))
    title, posts, seen = None, [], set()
    for f in files:
        tt, ps = parse_page(decode(f.read_bytes()))
        title = title or tt
        for p in ps:
            if p['id'] not in seen:
                seen.add(p['id'])
                posts.append(p)
    return title, posts


# ---------- rewriting post content ----------

LATEX_IMG = re.compile(r'<img[^>]*?src="(?P<src>[^"]*(?:codecogs\.com/[a-z]+\.latex\?|mimetex\.cgi\?|mathtex\.cgi\?)[^"]*)"[^>]*>', re.I)
SMILEY = re.compile(r'<img class="smilies"[^>]*alt="([^"]*)"[^>]*>')
IMG = re.compile(r'<img(?P<a>[^>]*?)src="(?P<src>[^"]+)"(?P<b>[^>]*)>', re.I)
HREF = re.compile(r'href="(?P<href>[^"]+)"')


def latex_from_src(src):
    q = html.unescape(src).split('?', 1)[1]
    try:
        q = urllib.parse.unquote(q)
    except Exception:
        pass
    q = re.sub(r'^\\(?:dpi|bg|fg)\{[^}]*\}\s*|^\\(?:inline|large|small|normal)\s*', '', q)
    return q


class Rewriter:
    def __init__(self, topic_pages, post_pages, images):
        self.topic_pages = topic_pages  # (forum, t) -> filename
        self.post_pages = post_pages    # (forum, post id) -> filename
        self.images = images            # absolute url -> local path or None
        self.missing_images = set()

    def link(self, forum, href):
        absolute = local_relative(forum, href)
        k = topic_key(absolute)
        if k:
            f = self.topic_pages.get((k[0], k[2])) if k[1] == 't' else self.post_pages.get((k[0], k[2]))
            if f:
                anchor = f'#p{k[2]}' if k[1] == 'p' else ''
                return f + anchor, True
        if absolute in self.images and self.images[absolute]:
            return '../' + self.images[absolute], True
        return absolute, False

    def content(self, forum, h):
        if forum == 'oliforum':
            # older posts wrap formulas as "$ $x$ $", left over from a conversion: make them "$x$"
            h = re.sub(r'\$ \$(.+?)\$ \$', r'$\1$', h, flags=re.S)
        h = LATEX_IMG.sub(lambda m: '<span class="tex">\\(' + html.escape(latex_from_src(m['src']), quote=False) + '\\)</span>', h)
        h = SMILEY.sub(lambda m: html.unescape(m[1]), h)
        h = re.sub(r'\s on\w+="[^"]*"', '', h)  # the forum's scripts are not part of the archive
        # links to members' profiles (in quotes) would lead to the forum: keep just the name
        h = re.sub(r'<a [^>]*href="[^"]*memberlist\.php[^"]*"[^>]*>(.*?)</a>', r'\1', h, flags=re.S)

        def img(m):
            src = m['src']
            absolute = local_relative(forum, src)
            local = self.images.get(absolute)
            if local:
                return f'<img{m["a"]}src="../{local}"{m["b"]}>'
            self.missing_images.add(absolute)
            return f'<a class="lost" href="{html.escape(absolute)}">[immagine non più disponibile]</a>'
        h = IMG.sub(img, h)

        def href(m):
            new, local = self.link(forum, m['href'])
            return f'href="{html.escape(new)}"' + ('' if local else ' class="ext"')
        return HREF.sub(href, h)


def image_urls(forum, h):
    h = LATEX_IMG.sub('', SMILEY.sub('', h))
    urls = set()
    for m in IMG.finditer(h):
        s = m['src']
        urls.add(local_relative(forum, s))
    for m in HREF.finditer(h):  # attachments linked rather than shown
        s = m['href']
        if 'download/file.php' in s:
            urls.add(local_relative(forum, s))
    return urls


import threading
OLIFIS_LOCK = threading.Lock()
FAILED_FILE = RAW / 'img_failed.json'
FAILED = set()
WAYBACK_LOCK = threading.Lock()


def wayback(url):
    """The oldest copy of an image on archive.org, or (None, None)."""
    if 'download/file.php' in url or url.startswith('//'):
        url = ('https:' + url) if url.startswith('//') else url
    with WAYBACK_LOCK:  # archive.org limits how fast it may be asked
        try:
            b, _ = get('https://archive.org/wayback/available?timestamp=2005&url=' + urllib.parse.quote(url, safe=''), timeout=30)
            snap = json.loads(b).get('archived_snapshots', {}).get('closest')
            time.sleep(1)
            if not snap or not snap.get('available'):
                return None, None
            # "id_" asks for the original bytes, without the Wayback toolbar
            raw = re.sub(r'/web/(\d+)/', r'/web/\1id_/', snap['url'])
            b, ct = get(raw, timeout=40)
            time.sleep(1)
        except Exception:
            return None, None
    if not ct.lower().startswith(('image/', 'application/pdf')):
        return None, None
    return b, ct


def fetch_image(url):
    name = hashlib.sha1(url.encode()).hexdigest()[:16]
    done = list((SITE / 'img').glob(name + '.*'))
    if done:
        return url, 'img/' + done[0].name
    if 'oliforum.it' in url and 'download/file.php' in url:  # disallowed by OliForum's robots.txt
        return url, None
    if 'imgur.com' in url:
        # fetched from outside the UK by the GitHub workflow, branch "imgur", copied to raw/imgur/
        cached = RAW / 'imgur' / url.rsplit('/', 1)[-1]
        if cached.exists() and cached.stat().st_size > 0:
            ext = cached.suffix.lower().replace('.jpeg', '.jpg')
            (SITE / 'img').mkdir(parents=True, exist_ok=True)
            (SITE / 'img' / (name + ext)).write_bytes(cached.read_bytes())
            return url, 'img/' + name + ext
    if url in FAILED:  # already tried and lost; --retry-images tries again
        return url, None
    b = ct = None
    # imgur refuses UK visitors and sends a "not viewable in your region" picture instead: go straight to the Wayback Machine
    if 'imgur.com' not in url:
        try:
            if 'olifis.it' in url:  # one request at a time to the forum, other hosts in parallel
                with OLIFIS_LOCK:
                    b, ct = get(url, timeout=20)
                    time.sleep(DELAY)
            else:
                b, ct = get(url, timeout=20)
        except Exception:
            b = None
    if b is None or not ct.lower().startswith(('image/', 'application/pdf')):
        b, ct = wayback(url)
        if b is None:
            return url, None
    ct = ct.split(';')[0].strip().lower()
    ext = {'image/png': '.png', 'image/jpeg': '.jpg', 'image/gif': '.gif', 'image/webp': '.webp', 'image/svg+xml': '.svg',
           'application/pdf': '.pdf'}.get(ct)
    if not ext and 'download/file.php' in url and len(b) > 0 and not b.lstrip().startswith(b'<'):
        ext = '.bin'
    if not ext or len(b) > 15_000_000:
        return url, None
    (SITE / 'img').mkdir(parents=True, exist_ok=True)
    (SITE / 'img' / (name + ext)).write_bytes(b)
    return url, 'img/' + name + ext


# ---------- page templates ----------

CSS = """
:root {
  --bg:#f7f6f2; --paper:#ffffff; --fg:#1c1b19; --muted:#6d6a63; --faint:#9b978e; --line:#e4e1d8;
  --link:#1d5ea8; --accent:#9a3412; --chip:#eef2f7; --chip-fg:#1d4f8c; --quote:#f3f1ec;
  --serif: Charter, "Bitstream Charter", "Iowan Old Style", Cambria, Georgia, serif;
  --sans: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}
@media (prefers-color-scheme: dark) { :root {
  --bg:#141416; --paper:#1c1c1f; --fg:#e7e5e0; --muted:#a19e96; --faint:#77746d; --line:#2e2e33;
  --link:#86b6ef; --accent:#f0a376; --chip:#22303f; --chip-fg:#a9cdf5; --quote:#232327; } }
* { box-sizing:border-box; }
html { -webkit-text-size-adjust:100%; }
body { margin:0; background:var(--bg); color:var(--fg); font:17px/1.6 var(--serif); }
main { max-width:780px; margin:0 auto; padding:20px 16px 80px; }
a { color:var(--link); text-decoration-thickness:1px; text-underline-offset:2px; }
a.ext::after { content:"\\2197"; font:12px var(--sans); margin-left:2px; color:var(--faint); }
a.lost { color:var(--faint); font:italic 14px var(--sans); text-decoration:none; border:1px dashed var(--line); padding:2px 8px; border-radius:4px; }

.top { display:flex; justify-content:space-between; align-items:center; gap:12px; font:14px var(--sans); color:var(--muted);
       padding-bottom:14px; margin-bottom:28px; border-bottom:1px solid var(--line); }
.top a { color:var(--accent); text-decoration:none; font-weight:600; }
.top .crumb { color:var(--faint); }

h1 { font-size:30px; line-height:1.2; margin:0 0 6px; letter-spacing:-.01em; }
h2 { font:600 13px var(--sans); text-transform:uppercase; letter-spacing:.08em; color:var(--muted); margin:44px 0 14px; }
.meta { font:14px var(--sans); color:var(--muted); margin:0 0 28px; }
.lede { font-size:18px; color:var(--muted); margin:0 0 8px; }
.notice { font:14px/1.5 var(--sans); color:var(--muted); background:var(--paper); border:1px solid var(--line); border-radius:8px; padding:10px 14px; margin:0 0 24px; }

/* posts */
.post { padding:22px 0; border-top:1px solid var(--line); overflow-wrap:anywhere; }
.post:first-of-type { border-top:none; padding-top:4px; }
.post .who { font:15px var(--sans); color:var(--muted); margin-bottom:14px; padding-bottom:8px; border-bottom:1px solid var(--line);
             display:flex; flex-wrap:wrap; gap:4px 12px; align-items:baseline; }
.post .who b { color:var(--accent); font-weight:700; font-size:18px; }
.post.first { background:var(--paper); border:1px solid var(--line); border-radius:10px; padding:20px 22px; margin-bottom:12px; }
.post.first + .post { border-top:none; }
.post img { max-width:100%; height:auto; border-radius:4px; }
.post blockquote { margin:10px 0; padding:10px 14px; border-left:3px solid var(--line); background:var(--quote); border-radius:0 6px 6px 0; font-size:.95em; }
.post blockquote cite { display:block; font:600 12px var(--sans); color:var(--muted); margin-bottom:4px; font-style:normal; }
.post .codebox p { display:none; }
.post pre, .post code { font:14px/1.45 Consolas, "SF Mono", monospace; }
.post pre { overflow-x:auto; background:var(--quote); padding:10px; border-radius:6px; }
.post ul, .post ol { padding-left:1.4em; }
.post dl.attachbox { margin:14px 0 0; padding:10px 14px; border:1px solid var(--line); border-radius:8px; font:14px var(--sans); }
.post dl.attachbox > dt { font-weight:600; color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.06em; margin-bottom:6px; }
.post dl.attachbox dd { margin:0; } .post dl.file { margin:6px 0; } .post dl.file dd { color:var(--faint); font-size:12px; }
.post dl.file dt img[src*="upload_icons"] { vertical-align:middle; margin-right:4px; }
mjx-container { max-width:100%; overflow-x:auto; overflow-y:hidden; }
mjx-container[display="true"] { margin:.6em 0 !important; }
/* a formula MathJax cannot read is shown as its source, like plain text rather than highlighted */
mjx-container [data-mml-node="merror"] > rect { fill:none !important; }
mjx-container [data-mml-node="merror"] { fill:currentColor !important; stroke:none !important; }

.pager { display:flex; justify-content:space-between; gap:12px; margin-top:40px; padding-top:18px; border-top:1px solid var(--line); font:14px var(--sans); }
.pager a { text-decoration:none; max-width:48%; }
.pager .next { text-align:right; margin-left:auto; }
.pager small { display:block; color:var(--faint); font-size:12px; }

/* index */
.stats { display:flex; flex-wrap:wrap; gap:8px 22px; font:14px var(--sans); color:var(--muted); margin:16px 0 0; }
.stats b { color:var(--fg); font-size:20px; font-weight:600; display:block; }
.years { width:100%; border-collapse:collapse; font-family:var(--sans); }
.years td { padding:7px 0; border-top:1px solid var(--line); vertical-align:top; }
.years tr:first-child td { border-top:none; }
.years td.y { width:110px; font-size:14px; font-weight:600; font-variant-numeric:tabular-nums; color:var(--muted); padding-top:10px; }
.chips { display:flex; flex-wrap:wrap; gap:6px; }
.chips a { display:inline-block; min-width:34px; text-align:center; padding:4px 9px; border-radius:6px; background:var(--chip); color:var(--chip-fg);
           text-decoration:none; font-size:14px; font-weight:600; font-variant-numeric:tabular-nums; }
.chips a:hover { outline:2px solid var(--chip-fg); }
.chips a.ext { background:transparent; border:1px solid var(--line); color:var(--muted); font-weight:500; }
.chips a.ext::after { content:none; }
.cards { display:flex; flex-direction:column; gap:12px; margin:28px 0 8px; }
.card { display:grid; grid-template-columns:1fr auto; column-gap:20px; row-gap:4px; align-items:baseline; padding:16px 20px;
        background:var(--paper); border:1px solid var(--line); border-radius:10px; text-decoration:none; color:var(--fg); font:14px/1.5 var(--sans); }
.card:hover { border-color:var(--chip-fg); }
.card b { grid-column:1; font:600 20px/1.25 var(--serif); color:var(--link); }
.card span { grid-column:1; color:var(--muted); }
.card .count { grid-column:2; grid-row:1; font-size:12px; color:var(--faint); white-space:nowrap; }
@media (max-width:600px) { .card { grid-template-columns:1fr; } .card .count { grid-column:1; grid-row:auto; } }
.card.big { grid-column:1 / -1; padding:22px 24px; border-color:var(--chip-fg); background:var(--chip); }
.card.big b { font-size:24px; color:var(--chip-fg); }
.card .kicker { font-size:11px; font-weight:600; letter-spacing:.08em; text-transform:uppercase; color:var(--chip-fg); }
h2.part { font:600 26px/1.2 var(--serif); text-transform:none; letter-spacing:0; color:var(--fg); margin-top:48px; }
h3.tema { font:600 13px var(--sans); text-transform:uppercase; letter-spacing:.08em; color:var(--muted); margin:28px 0 10px; }
ol.relay { list-style:none; padding:0; margin:0; columns:2; column-gap:28px; font-size:15px; }
ol.relay li { break-inside:avoid; padding:3px 0; display:flex; gap:8px; align-items:baseline; }
ol.relay .num { flex:0 0 2.6em; text-align:right; font:600 13px var(--sans); color:var(--faint); font-variant-numeric:tabular-nums; }
ol.relay a { text-decoration:none; }
ol.relay a:hover { text-decoration:underline; }
@media (max-width:600px) { ol.relay { columns:1; } }
ul.picks { list-style:none; padding:0; margin:0; }
ul.picks li { padding:14px 0; border-top:1px solid var(--line); }
ul.picks li:first-child { border-top:none; }
ul.picks a { font-size:18px; font-weight:600; text-decoration:none; }
ul.picks .when { display:block; font:12px var(--sans); color:var(--faint); margin-top:2px; }
ul.picks p { margin:6px 0 0; font-size:15px; color:var(--muted); }
details { margin-top:8px; }
details summary { cursor:pointer; font:600 15px var(--sans); color:var(--fg); padding:8px 0; }
details p { font:14px var(--sans); color:var(--muted); }
.notes { font-size:15px; color:var(--muted); }
ul.others { columns:2; column-gap:28px; padding-left:1.1em; font-size:15px; } ul.others li { break-inside:avoid; margin-bottom:4px; }
@media (max-width:600px) { ul.others { columns:1; } h1 { font-size:25px; } .years td.y { width:84px; font-size:13px; } }
"""


def page(title, body, depth, dollar, crumb=''):
    up = '../' * depth
    inline = "[['\\\\(','\\\\)'],['[tex]','[/tex]']" + (",['$','$']" if dollar else '') + ']'
    display = "[['\\\\[','\\\\]']" + (",['$$','$$']" if dollar else '') + ']'
    top = f'<a href="{up}index.html">{SITE_NAME}</a>' + (f'<span class="crumb">{crumb}</span>' if crumb else '')
    return f"""<!doctype html>
<html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title><link rel="stylesheet" href="{up}assets/style.css">
<script>window.MathJax = {{
  startup: {{ ready() {{
    MathJax.startup.defaultReady();
    // forum authors' slips that the old renderers tolerated: unbalanced braces, \\left without \\right
    MathJax.startup.document.inputJax[0].preFilters.add(({{math}}) => {{
      let t = math.math;
      const bare = t.replace(/\\\\[{{}}]/g, '');
      const open = (bare.match(/{{/g) || []).length, close = (bare.match(/}}/g) || []).length;
      if (open > close) t += '}}'.repeat(open - close);
      if (close > open) t = '{{'.repeat(close - open) + t;
      if ((t.match(/\\\\left(?![a-z])/g) || []).length !== (t.match(/\\\\right(?![a-z])/g) || []).length)
        t = t.replace(/\\\\(left|right)(?![a-z])/g, '');
      math.math = t;
    }});
  }} }},
  tex: {{ packages: {{ '[+]': ['noerrors'] }}, inlineMath: {inline}, displayMath: {display}, processEscapes: {'true' if dollar else 'false'} }},
  svg: {{ fontCache: 'global' }} }};</script>
<script src="{up}assets/{MATHJAX_FILE}" async></script>
</head><body><main>
<nav class="top">{top}</nav>
{body}
</main></body></html>
"""


YEAR_LINE = re.compile(r'^\s*(\d{4}\s*-\s*\d{4}|\(\?\))?\s*(.*)$', re.S)


def parse_index(first_html):
    """The first post of t=228 as {'fisica': [(year, [(label, href)])], 'matematica': [...], 'notes': html}."""
    fis = first_html.find('Fisica:')
    mat = first_html.find('Matematica:')
    sections = {'fisica': first_html[fis + 7:mat], 'matematica': first_html[mat + 11:]}
    out = {'notes': ''}
    for name, chunk in sections.items():
        rows, notes = [], []
        for line in re.split(r'<br\s*/?>', chunk):
            links = re.findall(r'<a [^>]*href="([^"]+)"[^>]*>(.*?)</a>', line, re.S)
            text = re.sub(r'<[^>]+>', '', line).strip()
            year = re.match(r'\s*(\d{4}\s*-\s*\d{4})', text)
            rest = html.unescape(re.sub(r'<[^>]+>', '', re.sub(r'<a .*?</a>', '', line, flags=re.S)))
            if links and re.fullmatch(r'\s*(\d{4}\s*-\s*\d{4})?[\s()?e]*', rest):
                rows.append((year[1].replace(' ', '') if year else 'anno ?', [(re.sub('<[^>]+>', '', l).strip(), h) for h, l in links]))
            elif text:
                notes.append(line.strip())
        out[name] = rows
        if notes:
            out['notes'] += '<br>'.join(notes) + '<br>'
    return out


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--build', action='store_true', help='only rebuild the site from raw/')
    ap.add_argument('--retry-images', action='store_true', help='try again the images that could not be saved before')
    args = ap.parse_args()
    if FAILED_FILE.exists() and not args.retry_images:
        FAILED.update(json.loads(FAILED_FILE.read_text(encoding='utf-8-sig')))

    # 1. the index topic and the topics it links to
    if not args.build:
        fetch_topic('olifis', INDEX_TOPIC)
    _, index_posts = load_topic('olifis', INDEX_TOPIC)
    wanted, linked_posts = [], []
    for n, p in enumerate(index_posts):
        c = p['content']
        math = c.find('Matematica:') if n == 0 else -1
        for m in HREF.finditer(c):
            if math >= 0 and m.start() > math and 'oliforum' in m['href']:
                continue  # the maths list: OliForum is not closing, keep those as links to it
            href = m['href']
            k = topic_key(local_relative('olifis', href) if href.startswith('.') else href)
            if not k:
                continue
            if k[1] == 't':
                if (k[0], k[2]) not in wanted and (k[0], k[2]) != ('olifis', INDEX_TOPIC):
                    wanted.append((k[0], k[2]))
            else:
                linked_posts.append((k[0], k[2]))
    # links to a single post: fetch the post's page, which tells us its topic
    for forum, pid in linked_posts:
        p = RAW / forum / f'post{pid}.html'
        if not p.exists() and not args.build:
            p.parent.mkdir(parents=True, exist_ok=True)
            try:
                b, _ = get(f'{FORUMS[forum]}?p={pid}')
                p.write_bytes(b)
                time.sleep(DELAY)
            except Exception as e:
                print('  post', forum, pid, e)
        if p.exists():
            h = decode(p.read_bytes())
            m = re.search(r'viewtopic\.php\?(?:[^"]*?&amp;)?t=(\d+)', h)
            if m and (forum, int(m[1])) not in wanted:
                wanted.append((forum, int(m[1])))

    print(f'{len(wanted)} linked topics ({sum(1 for f, _ in wanted if f == "olifis")} on Olifis, {sum(1 for f, _ in wanted if f == "oliforum")} on OliForum)')
    # topics added by hand: extra_topics.json
    extra = json.loads((ROOT / 'extra_topics.json').read_text(encoding='utf8'))
    extra_keys = ([('olifis', e['t']) for e in extra['fisica'] + extra['chimica_biologia']]
                  + [('olifis', t) for t in extra['orali'] + extra['preparazione']])
    for k in extra_keys:
        if k not in wanted:
            wanted.append(k)
    print(f'{len(extra_keys)} topics added from extra_topics.json')
    # chosen discussions from whole sections: selezioni.json; their pages were already downloaded to raw/sezioni/
    sel_file = ROOT / 'selezioni.json'
    selections = json.loads(sel_file.read_text(encoding='utf8')) if sel_file.exists() else {}
    for sec in SECTIONS:
        for e in selections.get(sec['key'], []):
            k = ('olifis', e['t'])
            if not raw_path(*k, 0).exists():
                for f in (RAW / 'sezioni' / sec['folder']).glob(f't{e["t"]}_s*.html'):
                    (RAW / 'olifis').mkdir(parents=True, exist_ok=True)
                    (RAW / 'olifis' / f.name).write_bytes(f.read_bytes())
            if k not in wanted:
                wanted.append(k)
    # the numbered relay of problems: staffetta.json
    staff = json.loads((ROOT / 'staffetta.json').read_text(encoding='utf8'))
    staff_keys = [('olifis', t) for t in staff['prime_staffette']] + [('olifis', e['t']) for e in staff['problemi']]
    for k in staff_keys:
        if k not in wanted:
            wanted.append(k)
    # any topic already downloaded with its whole section is copied rather than fetched again
    for forum, t in wanted:
        if forum == 'olifis' and not raw_path(forum, t, 0).exists():
            for f in (RAW / 'sezioni').glob(f'*/t{t}_s*.html'):
                (RAW / 'olifis').mkdir(parents=True, exist_ok=True)
                (RAW / 'olifis' / f.name).write_bytes(f.read_bytes())
    excluded_posts = {pid for sec in selections.values() if isinstance(sec, list) for e in sec for pid in e.get('escludi', [])}
    errors = {}
    if not args.build:
        for n, (forum, t) in enumerate(wanted, 1):
            try:
                fetch_topic(forum, t)
            except Exception as e:
                errors[f'{forum}/{t}'] = str(e)
            if n % 25 == 0:
                print(f'  fetched {n}/{len(wanted)}')

    # 2. parse everything
    topics = {}
    for forum, t in [('olifis', INDEX_TOPIC)] + wanted:
        title, posts = load_topic(forum, t)
        posts = [p for p in posts if p['id'] not in excluded_posts]  # spam, listed in selezioni.json
        if posts:
            title = re.sub(r'^\s*Re:\s*', '', title or '') or f'Discussione {t}'  # a few topics were titled like a reply
            topics[(forum, t)] = {'title': title, 'posts': posts}
        else:
            errors.setdefault(f'{forum}/{t}', 'nessun messaggio: discussione cancellata o non leggibile')
    topic_pages = {k: f'{k[0]}-{k[1]}.html' for k in topics}
    post_pages = {(k[0], p['id']): topic_pages[k] for k, v in topics.items() for p in v['posts']}

    # 3. images and attachments
    SITE.mkdir(exist_ok=True)
    urls = set()
    for (forum, _), v in topics.items():
        for p in v['posts']:
            urls |= image_urls(forum, p['content'])
    images = {}
    with concurrent.futures.ThreadPoolExecutor(8) as ex:
        for url, local in ex.map(fetch_image, sorted(urls)):
            images[url] = local
    FAILED_FILE.write_text(json.dumps(sorted(u for u, l in images.items() if not l), indent=1), encoding='utf8')
    print(f'{len(urls)} images and attachments, {sum(1 for v in images.values() if v)} saved')

    # 4. write the site
    (SITE / 'assets').mkdir(exist_ok=True)
    (SITE / 'assets' / 'style.css').write_text(CSS, encoding='utf8')
    mj = SITE / 'assets' / MATHJAX_FILE
    if not mj.exists():
        mj.write_bytes(get(MATHJAX_URL)[0])
    (SITE / 't').mkdir(exist_ok=True)
    for old in (SITE / 'assets').glob('tex-svg*.js'):
        if old.name != MATHJAX_FILE:
            old.unlink()
    rw = Rewriter(topic_pages, post_pages, images)

    def key_of(href):
        k = topic_key(local_relative('olifis', href))
        return (k[0], k[2]) if k and k[1] == 't' else None

    idx = parse_index(index_posts[0]['content'])

    def merge(rows, entries):
        """Add the hand-picked topics to the year rows, keeping years and problem numbers in order."""
        by_year = {y: list(links) for y, links in rows}
        for e in entries:
            by_year.setdefault(e['year'], []).append((e['n'], f'{FORUMS["olifis"]}?t={e["t"]}'))
        num = lambda link: (0, int(link[0])) if link[0].isdigit() else (1, 0)
        # most recent year first, rows without a year at the end
        return [(y, sorted(by_year[y], key=num)) for y in sorted(by_year, key=lambda y: (0, -int(y[:4])) if y[0].isdigit() else (1, 0))]

    idx['fisica'] = merge(idx['fisica'], extra['fisica'])
    idx['chimica_biologia'] = merge([], extra['chimica_biologia'])
    idx['matematica'] = merge(idx['matematica'], [])
    # reading order for the previous / next links: the physics list, year by year
    order, crumbs = [], {}
    for year, links in idx['fisica']:
        for label, href in links:
            k = key_of(href)
            if k in topics and k not in crumbs:
                order.append(k)
                crumbs[k] = f'Fisica {year}' + (f', problema {label}' if label.isdigit() else '')
    for year, links in idx['chimica_biologia']:
        for label, href in links:
            k = key_of(href)
            crumbs.setdefault(k, f'Chimica e biologia {year}' + (f', problema {label}' if label.isdigit() else ''))
    for t in extra['orali']:
        crumbs.setdefault(('olifis', t), 'Orali')
    for t in extra['preparazione']:
        crumbs.setdefault(('olifis', t), "Prepararsi all'ammissione")
    crumbs = {k: f'<a href="../sns.html">{c}</a>' for k, c in crumbs.items()}
    # previous / next: the physics list for the admission problems, the page's order for each section
    sequence = {k: order for k in order}
    # the relay comes first for its own problems, admission problems included: it is a strict sequence
    staff_seq = [k for k in staff_keys if k in topics]
    staff_label = {('olifis', e['t']): e['n'] for e in staff['problemi']}
    for k in staff_seq:
        n = staff_label.get(k, '')
        crumbs[k] = '<a href="../staffetta.html">La staffetta' + (f', problema {n}' if n.isdigit() else '') + '</a>'
        sequence[k] = staff_seq
    for sec in SECTIONS:
        seq = [('olifis', e['t']) for e in selections.get(sec['key'], []) if ('olifis', e['t']) in topics]
        for k in seq:
            crumbs.setdefault(k, f'<a href="../{sec["page"]}">{sec["title"]}</a>')
            sequence.setdefault(k, seq)
    plain = lambda s: html.escape(html.unescape(re.sub('<[^>]+>', '', s)))

    for (forum, t), v in topics.items():
        k = (forum, t)
        src = f'{FORUMS[forum]}?t={t}'
        n = len(v['posts'])
        body = [f'<h1>{v["title"]}</h1>',
                f'<p class="meta">{n} {"messaggio" if n == 1 else "messaggi"} · originale su <a class="ext" href="{src}">{"forum Olifis" if forum == "olifis" else "OliForum"}</a></p>']
        if forum == 'oliforum' and re.search(rf'viewtopic\.php\?[^"]*\bt={t}&amp;[^"]*start=[1-9]', decode(raw_path(forum, t, 0).read_bytes())):
            body.append('<p class="notice">Qui c\'è solo la prima pagina della discussione: il seguito è su OliForum, al link qui sopra.</p>')
        for i, p in enumerate(v['posts']):
            body.append(f'<article class="post{" first" if i == 0 else ""}" id="p{p["id"]}"><div class="who"><b>{html.escape(p["author"])}</b><span>{html.escape(p["date"])}</span></div>'
                        f'{rw.content(forum, p["content"])}</article>')
        if k in sequence:
            seq = sequence[k]
            i = seq.index(k)
            prev_ = f'<a class="prev" href="{topic_pages[seq[i - 1]]}"><small>← precedente</small>{plain(topics[seq[i - 1]]["title"])}</a>' if i > 0 else ''
            next_ = f'<a class="next" href="{topic_pages[seq[i + 1]]}"><small>successivo →</small>{plain(topics[seq[i + 1]]["title"])}</a>' if i + 1 < len(seq) else ''
            body.append(f'<nav class="pager">{prev_}{next_}</nav>')
        (SITE / 't' / topic_pages[k]).write_text(page(plain(v['title']), '\n'.join(body), 1, forum == 'oliforum', crumbs.get(k, '')), encoding='utf8')

    # index
    def chips(links):
        out = []
        for label, href in links:
            k = key_of(href)
            if k in topics:
                out.append(f'<a href="t/{topic_pages[k]}" title="{plain(topics[k]["title"])}">{html.escape(label)}</a>')
            else:
                out.append(f'<a class="ext" href="{html.escape(local_relative("olifis", href))}" title="su OliForum">{html.escape(label)}</a>')
        return '<div class="chips">' + ''.join(out) + '</div>'

    def table(rows):
        return '<table class="years">' + ''.join(f'<tr><td class="y">{y}</td><td>{chips(l)}</td></tr>' for y, l in rows) + '</table>'

    listed = {key_of(h) for _, links in idx['fisica'] + idx['matematica'] + idx['chimica_biologia'] for _, h in links}
    listed |= {('olifis', t) for t in extra['orali'] + extra['preparazione']}
    listed |= {('olifis', e['t']) for sec in SECTIONS for e in selections.get(sec['key'], [])}
    listed |= set(staff_keys)
    li = lambda ts: ''.join(f'<li><a href="t/{topic_pages[("olifis", t)]}">{plain(topics[("olifis", t)]["title"])}</a></li>'
                            for t in ts if ('olifis', t) in topics)
    orali, preparazione = li(extra['orali']), li(extra['preparazione'])
    others = [k for k in topics if k not in listed and k != ('olifis', INDEX_TOPIC)]
    others_html = ''
    if others:
        items = ''.join(f'<li><a href="t/{topic_pages[k]}">{plain(topics[k]["title"])}</a></li>' for k in sorted(others, key=lambda k: plain(topics[k]['title'])))
        others_html = f'<h2>Altre discussioni collegate</h2><p class="notes">Segnalate nelle risposte alla discussione originale, ma non nell\'elenco per anno.</p><ul class="others">{items}</ul>'
    total_posts = sum(len(v['posts']) for v in topics.values())
    n_problems = len(order)
    years = sorted(y for y, _ in idx['fisica'] if y[0].isdigit())  # oldest first, for the range shown
    notes = rw.content('olifis', idx['notes']).replace('href="olifis-', 'href="t/olifis-').replace('href="oliforum-', 'href="t/oliforum-')
    body = f"""<h1>Problemi di ammissione alla Normale, con le soluzioni</h1>
<p class="lede">Le discussioni del forum delle Olimpiadi di Fisica sui problemi delle prove di ammissione alla Scuola Normale Superiore di Pisa, raccolte anno per anno.</p>
<div class="stats"><span><b>{len(topics) - 1}</b>discussioni</span><span><b>{total_posts:,}</b>messaggi</span><span><b>{years[0][:4]}–{years[-1][-4:]}</b>anni</span></div>
<h2>Fisica</h2>
{table(idx['fisica'])}
<h2>Fisica per chimica e biologia</h2>
<p class="notes">I problemi di fisica delle prove di ammissione ai corsi di chimica e di biologia.</p>
{table(idx['chimica_biologia'])}
<h2>Orali</h2>
<p class="notes">Domande e problemi raccontati da chi ha fatto l'esame orale di ammissione.</p>
<ul class="others">{orali}</ul>
<h2>Prepararsi all'ammissione</h2>
<p class="notes">Consigli su come e quanto prepararsi, chiesti e dati sul forum.</p>
<ul class="others">{preparazione}</ul>
<h2>Matematica</h2>
<details><summary>Elenco dei problemi di matematica ({sum(len(l) for _, l in idx['matematica'])})</summary>
<p>Le soluzioni di matematica sono sul forum delle Olimpiadi di Matematica, che resta aperto: i link portano lì.</p>
{table(idx['matematica'])}</details>
{others_html}
<h2>Da dove viene</h2>
<div class="notes"><p>Copia statica della discussione <a href="t/olifis-{INDEX_TOPIC}.html">«Soluzioni SNS sui forum Olimat e Olifis»</a>, aperta da Pigkappa nel 2009, e di tutte le discussioni a cui rimanda, più le altre discussioni della sezione Problemi teorici con SNS nel titolo. Archiviata il {time.strftime('%d/%m/%Y')}.</p>
<p>Note dalla discussione originale:</p><p>{notes}</p></div>
"""
    body = body.replace(f'{total_posts:,}', f'{total_posts:,}'.replace(',', '.'))
    (SITE / 'sns.html').write_text(page('Problemi di ammissione alla Normale · ' + SITE_NAME, body, 0, False), encoding='utf8')

    # pages of chosen discussions, one per forum section
    def years_of(k):
        ys = sorted({int(m) for p in topics[k]['posts'] for m in re.findall(r'\b(20\d\d|19\d\d)\b', p['date'])})
        return '' if not ys else (str(ys[0]) if ys[0] == ys[-1] else f'{ys[0]}–{ys[-1]}')

    # the relay: every problem in order, in blocks of 50
    def staff_title(k):
        return plain(re.sub(r'^\s*(?:Re:\s*)?\d{1,3}\s*(?:[-.:)°]\s*)?', '', html.unescape(re.sub('<[^>]+>', '', topics[k]['title'])))) or plain(topics[k]['title'])
    blocks, cur, last = [], [], 0
    for e in staff['problemi']:
        k = ('olifis', e['t'])
        if k not in topics:
            continue
        n = int(e['n']) if e['n'].isdigit() else last
        if cur and (n - 1) // 50 != (last - 1) // 50:
            blocks.append(cur); cur = []
        cur.append(f'<li><span class="num">{html.escape(e["n"]) if e["n"].isdigit() else "s.n."}</span>'
                   f'<a href="t/{topic_pages[k]}">{staff_title(k)}</a></li>')
        last = n
    if cur:
        blocks.append(cur)
    nums = [int(e['n']) for e in staff['problemi'] if e['n'].isdigit()]
    block_html = ''
    for b in blocks:
        first = re.search(r'class="num">(\d+)', b[0])[1]
        lastn = [m for x in b for m in re.findall(r'class="num">(\d+)', x)][-1]
        block_html += f'<h2>Problemi {first}–{lastn}</h2><ol class="relay">' + ''.join(b) + '</ol>'
    prime = ''.join(f'<li><a href="t/{topic_pages[("olifis", t)]}">{plain(topics[("olifis", t)]["title"])}</a>'
                    f'<span class="when">{years_of(("olifis", t))} · {len(topics[("olifis", t)]["posts"])} messaggi</span></li>'
                    for t in staff['prime_staffette'] if ('olifis', t) in topics)
    staff_n = sum(1 for e in staff['problemi'] if ('olifis', e['t']) in topics)
    first_k, last_k = ('olifis', staff['problemi'][0]['t']), ('olifis', staff['problemi'][-1]['t'])
    sbody = f"""<h1>La staffetta</h1>
<p class="lede">Chi risolve un problema ne propone uno nuovo. Dal {years_of(first_k)[:4]} al {years_of(last_k)[-4:]} la staffetta del forum è arrivata
al problema {max(nums)}: qui ci sono tutti, in ordine.</p>
<p class="meta">{staff_n} discussioni. Il 18 è stato usato due volte; dopo il 328 tre problemi sono stati proposti senza numero (s.n.) e la numerazione riprende dal 330.</p>
<h2>Le prime staffette</h2>
<p class="notes">Prima della staffetta numerata, nel 2010 e 2011, ce n'erano tre in un'unica discussione ciascuna.</p>
<ul class="picks">{prime}</ul>
{block_html}"""
    (SITE / 'staffetta.html').write_text(page(f'La staffetta · {SITE_NAME}', sbody, 0, False), encoding='utf8')

    # pages of chosen discussions
    def picks(entries, head):
        groups = {}
        for e in entries:
            groups.setdefault(e.get('tema', ''), []).append(e)
        out = []
        for tema, es in groups.items():
            if tema:
                out.append(f'<{head} class="tema">{html.escape(tema)}</{head}>')
            items = []
            for e in es:
                k = ('olifis', e['t'])
                items.append(f'<li><a href="t/{topic_pages[k]}">{plain(topics[k]["title"])}</a>'
                             f'<span class="when">{years_of(k)} · {len(topics[k]["posts"])} messaggi</span>'
                             f'<p>{html.escape(e.get("nota", ""))}</p></li>')
            out.append('<ul class="picks">' + ''.join(items) + '</ul>')
        return ''.join(out)

    cards = []
    for pg in PAGES:
        secs = [s for s in SECTIONS if s['key'] in pg['keys']]
        total = 0
        body_parts = []
        for s in secs:
            entries = [e for e in selections.get(s['key'], []) if ('olifis', e['t']) in topics]
            total += len(entries)
            if len(secs) > 1:
                body_parts.append(f'<h2 class="part">{s["title"]}</h2><p class="notes">{s["lede"]}</p>'
                                  + picks(entries, 'h3'))
            else:
                body_parts.append(picks(entries, 'h2'))
        pbody = f'<h1>{pg["title"]}</h1><p class="lede">{pg["lede"]}</p>' + ''.join(body_parts)
        (SITE / pg['page']).write_text(page(f'{pg["title"]} · {SITE_NAME}', pbody, 0, False), encoding='utf8')
        cards.append((pg, total))
    for old in ('olimpiadi.html', 'caffe.html'):  # pages of an earlier layout
        if (SITE / old).exists():
            (SITE / old).unlink()

    card = lambda href, title, text, count: (f'<a class="card" href="{href}"><b>{title}</b><span>{text}</span>'
                                             f'<span class="count">{count} discussioni</span></a>')
    sns_card = card('sns.html', 'Problemi di ammissione alla Normale',
                    f'I problemi di fisica delle prove di ammissione alla Scuola Normale Superiore di Pisa dal 1960 al {years[-1][-4:]}, quasi tutti risolti e discussi, anno per anno.',
                    len(order))
    staff_card = card('staffetta.html', 'La staffetta', f'Chi risolve un problema ne propone uno nuovo: tutti i {max(nums)} problemi, in ordine.', staff_n + len(staff['prime_staffette']))
    other_cards = staff_card + ''.join(card(p['page'], p['title'], p['card'], n) for p, n in cards if n)
    home = f"""<h1>{SITE_NAME}</h1>
<p class="lede">Dal 2009 al {FORUM_STATS['last']} il forum delle Olimpiadi di Fisica, su forum.olifis.it, è stato il luogo dove studenti, insegnanti ed ex olimpionici
discutevano di problemi di fisica, di gare e di tutto il resto. Questa è una copia statica di una selezione delle sue discussioni, fatta
prima della chiusura del forum.</p>
<div class="cards">{sns_card}{other_cards}</div>
<h2>Il forum in numeri</h2>
<div class="stats"><span><b>{FORUM_STATS['topics']}</b>discussioni</span><span><b>{FORUM_STATS['posts']}</b>messaggi</span><span><b>{FORUM_STATS['members']}</b>iscritti</span><span><b>2009–{FORUM_STATS['last']}</b>anni</span></div>
<h2>Questo archivio</h2>
<div class="notes"><p>Ogni pagina riproduce una discussione del forum com'era, con autori, date, formule e immagini. Le formule sono mostrate con MathJax.
Alcune immagini, caricate su servizi esterni che le hanno cancellate, sono andate perse.</p></div>
"""
    (SITE / 'index.html').write_text(page(SITE_NAME, home, 0, False), encoding='utf8')

    report = {'topics': len(topics), 'posts': total_posts, 'errors': errors,
              'images_missing': sorted(rw.missing_images)}
    (RAW / 'report.json').write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding='utf8')
    print(f'site: {len(topics)} topics, {total_posts} posts; {len(errors)} topic errors, {len(rw.missing_images)} images not saved')
    for k, e in errors.items():
        print('  ', k, e)


if __name__ == '__main__':
    main()
