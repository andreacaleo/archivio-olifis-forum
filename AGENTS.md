# archivio-olifis-forum

Static archive of the forum of the Italian Physics Olympiads (forum.olifis.it), run by AIF. Started on 2026-11-24, when Dennis Luigi Censi (AIF, head of the Olympiads project) proposed closing the forum. The site is in Italian.

What is archived: the topic "Soluzioni SNS sui forum Olimat e Olifis" (t=228, opened by Andrea as Pigkappa in 2009), an index of the admission problems of the Scuola Normale Superiore, and every topic it links to.

## Layout

| Path | Holds |
|---|---|
| `build_archive.py` | Fetches the topics and builds the site. Usage in its docstring |
| `site/` | The site: plain HTML, works offline. Open `site/index.html` |
| `raw/` | Pages as fetched from the forums, so the site can be rebuilt without asking them again. Not committed |

## Decisions

- **Fetching follows each forum's `robots.txt`:** 2 seconds between requests (olifis.it asks for it). From OliForum (www.oliforum.it, the Maths Olympiads forum) only the first page of each topic and no attachments, because its `robots.txt` disallows `&start=` and `download/file.php`. OliForum is not closing.
- **Physics only.** The Maths list in t=228 links to OliForum and stays as links to it.
- **Formulas:** codecogs images are turned back into LaTeX and shown with MathJax 3 (`tex-svg-full.js`, kept in `site/assets/`). A filter before rendering balances braces and drops unmatched `\left`/`\right`. Formulas still unreadable are shown as their source text.
- **Images:** saved locally. imgur blocks the UK, so imgur images come from the Wayback Machine. 21 images (imageshack, tinypic, postimg) are lost: no copy on archive.org.
- **Private until AIF agrees.** The site is to be offered to AIF for olifis.it, or published on GitHub Pages with their agreement.
