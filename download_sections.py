"""Download every topic of three sections of the Olifis forum, as the forum serves them, so nothing is lost when it closes.

Sections (forum id -> folder under raw/sezioni/):
    f=3   olimpiadi-di-fisica
    f=12  problemi-teorici
    f=6   caffe

Each folder gets:
    forum_s{start}.html   the section's listing pages
    t{id}_s{start}.html   every page of every topic, raw bytes as served
    allegati/{id}_{name}  the forum's own attachments (file.php?id=N), named from Content-Disposition
    index.json            each topic: id, title, replies in the listing, pages fetched, posts parsed,
                          attachments, error; plus the attachments that are missing (404) or failed

Usage (from the repo root):
    python -I -u download_sections.py              # all three sections
    python -I -u download_sections.py 6 3          # only these forum ids

It only downloads: it builds no site pages. It is resumable: files already on disk are not fetched again,
and attachments recorded as missing in index.json are not tried again. Stop it and run it again at will.
Politeness: one request at a time to olifis.it, at least 2 seconds apart (robots.txt Crawl-delay: 2).
Errors (429, 5xx, timeouts) wait 60 s and retry; after many failures in a row it stops.
Topics of f=12 already fetched by build_archive.py (raw/olifis/) and its listing (raw/forum12/) are copied, not fetched.
"""
import html, importlib.util, json, math, re, shutil, sys, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location('build_archive', ROOT / 'build_archive.py')
ba = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ba)

BASE = 'https://www.olifis.it/forum.olifis.it/'
OUT = ROOT / 'raw' / 'sezioni'
SECTIONS = {3: 'olimpiadi-di-fisica', 12: 'problemi-teorici', 6: 'caffe'}
DELAY = 2.0
BACKOFF = 60
RETRIES = 3
MAX_FAILURES_IN_A_ROW = 8


class Gone(Exception):
    """The forum answered 404 (or another 4xx): the thing is not there."""


class Stop(Exception):
    """Too many errors in a row: stop rather than keep asking the forum."""


_last = 0.0
_failures = 0


def fetch(url):
    """One polite request: returns (bytes, headers). Raises Gone on 4xx, Stop after repeated errors."""
    global _last, _failures
    for attempt in range(RETRIES + 1):
        wait = _last + DELAY - time.time()
        if wait > 0:
            time.sleep(wait)
        try:
            req = urllib.request.Request(url, headers=ba.UA)
            with urllib.request.urlopen(req, timeout=40) as r:
                b, headers = r.read(), r.headers
            _last = time.time()
            _failures = 0
            return b, headers
        except urllib.error.HTTPError as e:
            _last = time.time()
            if e.code != 429 and e.code < 500:
                _failures = 0
                raise Gone(f'HTTP {e.code}')
            err = f'HTTP {e.code}'
        except Exception as e:  # timeouts, connection resets
            _last = time.time()
            err = f'{type(e).__name__}: {e}'
        _failures += 1
        print(f'    ! {err} on {url} (attempt {attempt + 1}, {_failures} failures in a row)')
        if _failures >= MAX_FAILURES_IN_A_ROW:
            raise Stop(f'{_failures} failed requests in a row, last: {err}')
        if attempt < RETRIES:
            time.sleep(BACKOFF)
    raise RuntimeError(err)


def write(path, b):
    """Write through a temporary file, so a stopped run never leaves half a file that would be skipped later."""
    tmp = path.with_name('.part_' + path.name)
    tmp.write_bytes(b)
    tmp.replace(path)


# ---------- listing ----------

def listing_starts(h, f):
    starts = {0}
    for href in re.findall(r'href="([^"]*viewforum\.php\?[^"]*start=\d+[^"]*)"', h):
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(html.unescape(href)).query)
        if q.get('f') == [str(f)] and q.get('start', [''])[0].isdigit():
            starts.add(int(q['start'][0]))
    last = max(starts)
    return list(range(0, last + 1, 25))


def parse_listing(h):
    """[(topic id, title, replies)] for the rows of a listing page."""
    rows = []
    parts = re.split(r'<li class="row\b', h)[1:]
    for p in parts:
        m = re.search(r'<a href="[^"]*viewtopic\.php\?(?:[^"]*?&amp;)?t=(\d+)[^"]*" class="topictitle">(.*?)</a>', p, re.S)
        if not m:
            continue
        r = re.search(r'<dd class="posts">\s*(\d+)', p) or re.search(r'Risposte: <strong>(\d+)</strong>', p)
        title = html.unescape(re.sub('<[^>]+>', '', m[2])).strip()
        rows.append((int(m[1]), title, int(r[1]) if r else None))
    return rows


def get_listing(f, folder):
    first = folder / 'forum_s0.html'
    old = ba.RAW / f'forum{f}'
    if not first.exists() and (old / 's0.html').exists():
        for p in old.glob('s*.html'):
            shutil.copyfile(p, folder / f'forum_{p.name}')
    if not first.exists():
        write(first, fetch(f'{BASE}viewforum.php?f={f}')[0])
    starts = listing_starts(ba.decode(first.read_bytes()), f)
    topics = {}
    for s in starts:
        p = folder / f'forum_s{s}.html'
        if not p.exists():
            write(p, fetch(f'{BASE}viewforum.php?f={f}&start={s}')[0])
        for t, title, replies in parse_listing(ba.decode(p.read_bytes())):
            topics.setdefault(t, (title, replies))
    return starts, topics


# ---------- topics ----------

def topic_starts(h, t):
    """Page starts of a topic, from the pagination links of one of its pages (sid stripped)."""
    starts = {0}
    for href in re.findall(r'href="([^"]*viewtopic\.php\?[^"]*start=\d+[^"]*)"', h):
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(html.unescape(href)).query)
        if q.get('t') == [str(t)] and q.get('start', [''])[0].isdigit():
            starts.add(int(q['start'][0]))
    return max(starts)


def get_topic(t, replies, folder):
    """Fetch (or copy) every page of topic t. Returns the list of page files."""
    expected = math.ceil((replies + 1) / 10) if replies is not None else None
    old = [ba.RAW / 'olifis' / f't{t}_s{s}.html' for s in range(0, 10 * (expected or 0), 10)]
    if expected and all(p.exists() for p in old):
        for p in old:
            dest = folder / p.name
            if not dest.exists():
                shutil.copyfile(p, dest)
    first = folder / f't{t}_s0.html'
    if not first.exists():
        write(first, fetch(f'{BASE}viewtopic.php?t={t}')[0])
    last = topic_starts(ba.decode(first.read_bytes()), t)
    if expected:
        last = max(last, 10 * (expected - 1))
    files = []
    for s in range(0, last + 1, 10):
        p = folder / f't{t}_s{s}.html'
        if not p.exists():
            write(p, fetch(f'{BASE}viewtopic.php?t={t}&start={s}')[0])
        files.append(p)
    return files


ATTACH_RE = re.compile(r'download/file\.php\?(?:[^"\'<>\s]*?&amp;|[^"\'<>\s]*?&)?id=(\d+)')


def attachment_ids(posts):
    ids = []
    for p in posts:
        for m in ATTACH_RE.finditer(p['content']):
            if int(m[1]) not in ids:
                ids.append(int(m[1]))
    return ids


def filename(headers, aid):
    cd = headers.get('Content-Disposition', '') or ''
    name = None
    m = re.search(r"filename\*\s*=\s*([^']*)'[^']*'([^;]+)", cd)
    if m:
        name = urllib.parse.unquote(m[2].strip().strip('"'), encoding=m[1] or 'utf-8', errors='replace')
    if not name:
        m = re.search(r'filename\s*=\s*"([^"]*)"', cd) or re.search(r'filename\s*=\s*([^;]+)', cd)
        if m:
            raw = m[1].strip()
            try:
                name = urllib.parse.unquote(raw.encode('latin-1').decode('utf-8'))
            except Exception:
                name = urllib.parse.unquote(raw)
    if not name:
        name = 'file'
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', name).strip(' .') or 'file'
    return f'{aid}_{name[:150]}'


def get_attachment(aid, adir, missing):
    """'saved', 'missing' or 'failed: ...'."""
    if list(adir.glob(f'{aid}_*')):
        return 'saved'
    if aid in missing:
        return 'missing'
    try:
        b, headers = fetch(f'{BASE}download/file.php?id={aid}')
    except Gone as e:
        missing[aid] = str(e)
        return 'missing'
    ct = (headers.get('Content-Type') or '').lower()
    if 'text/html' in ct and not headers.get('Content-Disposition'):
        missing[aid] = 'forum answered with a page, not the file'
        return 'missing'
    adir.mkdir(exist_ok=True)
    write(adir / filename(headers, aid), b)
    return 'saved'


# ---------- main ----------

def section(f, name):
    folder = OUT / name
    folder.mkdir(parents=True, exist_ok=True)
    adir = folder / 'allegati'
    index_file = folder / 'index.json'
    prev = json.loads(index_file.read_text(encoding='utf8')) if index_file.exists() else {}
    missing = {int(k): v for k, v in prev.get('attachments_missing', {}).items()}
    failed = {}
    print(f'== f={f} {name}')
    listing_pages, listed = get_listing(f, folder)
    print(f'   {len(listing_pages)} listing pages, {len(listed)} topics')
    out = []
    t0 = time.time()

    def save():
        index_file.write_text(json.dumps({
            'forum': f, 'section': name, 'listing_pages': len(listing_pages),
            'topics_listed': len(listed), 'topics': out,
            'pages': sum(x['pages'] for x in out), 'posts': sum(x['posts'] for x in out),
            'attachments_saved': len(list(adir.glob('*'))) if adir.exists() else 0,
            'attachments_missing': {str(k): v for k, v in sorted(missing.items())},
            'attachments_failed': {str(k): v for k, v in sorted(failed.items())},
        }, indent=1, ensure_ascii=False), encoding='utf8')

    for n, (t, (title, replies)) in enumerate(listed.items(), 1):
        rec = {'id': t, 'title': title, 'replies': replies, 'pages': 0, 'posts': 0, 'attachments': [], 'error': None}
        try:
            files = get_topic(t, replies, folder)
            rec['pages'] = len(files)
            posts, seen = [], set()
            for p in files:
                tt, ps = ba.parse_page(ba.decode(p.read_bytes()))
                if tt and not rec.get('page_title'):
                    rec['page_title'] = html.unescape(re.sub('<[^>]+>', '', tt)).strip()
                for q in ps:
                    if q['id'] not in seen:
                        seen.add(q['id'])
                        posts.append(q)
            rec['posts'] = len(posts)
            if not posts:
                rec['error'] = 'no posts on the page (deleted, moved or not readable)'
            elif replies is not None and len(posts) != replies + 1:
                rec['warning'] = f'{len(posts)} posts parsed, listing says {replies + 1}'
            for aid in attachment_ids(posts):
                try:
                    st = get_attachment(aid, adir, missing)
                except Gone as e:
                    st = f'failed: {e}'
                except Stop:
                    raise
                except Exception as e:
                    st = f'failed: {e}'
                if st.startswith('failed'):
                    failed[aid] = st
                rec['attachments'].append({'id': aid, 'status': st.split(':')[0]})
        except Stop:
            out.append(rec)
            save()
            raise
        except Gone as e:
            rec['error'] = str(e)
        except Exception as e:
            rec['error'] = f'{type(e).__name__}: {e}'
        out.append(rec)
        if n % 10 == 0 or n == len(listed):
            save()
            el = time.time() - t0
            print(f'   {n}/{len(listed)} topics, {sum(x["pages"] for x in out)} pages, {sum(x["posts"] for x in out)} posts, '
                  f'{len(missing)} attachments missing, {el / 60:.1f} min')
    save()
    errs = [x for x in out if x['error'] or x.get('warning')]
    print(f'   done: {len(out)} topics, {len(errs)} with a note')
    for x in errs:
        print(f'     t={x["id"]}: {x["error"] or x["warning"]}')


def main():
    wanted = [int(a) for a in sys.argv[1:]] or list(SECTIONS)
    start = time.time()
    try:
        for f in wanted:
            section(f, SECTIONS[f])
    except Stop as e:
        print('STOPPED:', e)
        sys.exit(1)
    print(f'all done in {(time.time() - start) / 60:.1f} min')


if __name__ == '__main__':
    main()
