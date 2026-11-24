# archivio-olifis-forum

Static archive of the forum of the Italian Physics Olympiads (forum.olifis.it), run by AIF. Started on 2026-11-24, when Dennis Luigi Censi (AIF, head of the Olympiads project) proposed closing the forum. The site is in Italian.

Private GitHub repo `andreacaleo/archivio-olifis-forum`. Commit freely, push only when Andrea says "push".

What is archived so far: the topic "Soluzioni SNS sui forum Olimat e Olifis" (t=228, opened by Andrea as Pigkappa in 2009), an index of the admission problems of the Scuola Normale Superiore, and every topic it links to. Plus, listed in `extra_topics.json` with their year and problem number, the other topics in the Problemi teorici section with SNS in the title: physics problems go in the main table, problems from the chemistry and biology admission tests in their own section, orals in their own section. Topics left out are listed there with the reason. Other interesting topics are to be added.

The site is "Archivio del forum Olifis", not an archive of the SNS. The home page has four sections, in this order:
1. Problemi di ammissione alla Normale (`sns.html`), most recent year first.
2. La staffetta (`staffetta.html`): every problem of the numbered relay, 1 to 330, listed in `staffetta.json`, plus the three single-thread relays of 2010-2011. Relay problems that are also admission problems follow the relay for previous / next.
3. Altri problemi teorici (`problemi-teorici.html`): at most 30 chosen discussions not in the relay or the admission problems.
4. Olimpiadi di Fisica e Caffè (`olimpiadi-caffe.html`): at most 10 chosen discussions from each section.

The choices for 3 and 4, with the line shown for each, are in `selezioni.json`; they were made by reading the discussions, not by length or views. Posts listed under `escludi` there (spam) are left out.

## Layout

| Path | Holds |
|---|---|
| `build_archive.py` | Fetches the topics and builds the site. Usage in its docstring |
| `site/` | The site: plain HTML, works offline. Open `site/index.html` |
| `download_sections.py` | Downloads whole forum sections (all topics and the forum's attachments) to `raw/sezioni/<section>/`. Run on 2026-11-24 for Olimpiadi di Fisica, Problemi teorici and Caffè: 1,979 topics, all complete |
| `selezioni.json` | The discussions chosen from those sections |
| `raw/` | Pages as fetched from the forums, so the site can be rebuilt without asking them again. Not committed |

## Decisions

- **Fetching follows each forum's `robots.txt`:** 2 seconds between requests (olifis.it asks for it). From OliForum (www.oliforum.it, the Maths Olympiads forum) only the first page of each topic and no attachments, because its `robots.txt` disallows `&start=` and `download/file.php`. OliForum is not closing.
- **Physics only.** The Maths list in t=228 links to OliForum and stays as links to it.
- **Formulas:** codecogs images are turned back into LaTeX and shown with MathJax 3 (`tex-svg-full.js`, kept in `site/assets/`). A filter before rendering balances braces and drops unmatched `\left`/`\right`. Formulas still unreadable are shown as their source text.
- **Images and attachments:** saved locally, attachments included (they sit in a box after the post text). imgur blocks the UK, so imgur images come from the Wayback Machine. Images that cannot be saved are listed in `raw/img_failed.json` and not tried again unless `--retry-images` is given. Lost for good: imageshack, tinypic and postimg images, and some forum attachments that the forum itself answers with 404 (none on archive.org). imgur images that archive.org does not have are listed in `imgur_list.txt`: pushing a change to it runs the workflow `.github/workflows/fetch_imgur.yml`, which downloads them on GitHub's servers in the US and commits them to the branch `imgur`. Copy that branch's files to `raw/imgur/` (`git fetch origin imgur`, then `git show origin/imgur:<file>`) and the build uses them.
- **Private until AIF agrees.** The site is to be offered to AIF for olifis.it, or published on GitHub Pages with their agreement.
