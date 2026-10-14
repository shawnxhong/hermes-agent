"""Deterministic email presentation for the demo's queued Markdown bodies.

No model rewrite, remote resources, or interpretation of literal backslash escapes.
Raw HTML is displayed as text. Only parser-generated, allowlisted HTML survives.
"""
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlsplit

import markdown


_STYLES = {
    'p': 'margin:0 0 12px;',
    'ul': 'margin:8px 0 14px;padding-left:24px;list-style-type:disc;',
    'ol': 'margin:8px 0 14px;padding-left:24px;',
    'li': 'margin:0 0 6px;line-height:1.5;',
    'h1': 'font-size:24px;margin:20px 0 12px;',
    'h2': 'font-size:20px;margin:18px 0 10px;',
    'h3': 'font-size:18px;margin:16px 0 8px;',
    'table': 'border-collapse:collapse;margin:12px 0;',
    'th': 'border:1px solid #cccccc;padding:6px;text-align:left;',
    'td': 'border:1px solid #cccccc;padding:6px;vertical-align:top;',
    'pre': 'white-space:pre-wrap;font-family:Consolas,monospace;background-color:#f4f4f4;padding:12px;',
    'code': 'font-family:Consolas,monospace;',
    'blockquote': 'border-left:3px solid #cccccc;margin:12px 0;padding-left:12px;',
}
_ALLOWED = frozenset(('p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'strong', 'em',
                      'ul', 'ol', 'li', 'blockquote', 'pre', 'code', 'a', 'br',
                      'hr', 'table', 'thead', 'tbody', 'tr', 'th', 'td'))
_BLOCKS = frozenset(('p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote',
                     'pre', 'ul', 'ol', 'table'))


def _safe_url(value):
    # Reject controls/whitespace rather than relying on clients to agree on parsing.
    if not value or any(ord(c) <= 32 or ord(c) == 127 for c in value):
        return False
    try:
        parsed = urlsplit(value)
        return (parsed.scheme.lower() in {'http', 'https'} and bool(parsed.netloc)
                or parsed.scheme.lower() == 'mailto' and bool(parsed.path))
    except ValueError:
        return False


class _EmailHTML(HTMLParser):
    """Allowlist generated markup and derive the matching plain-text alternative."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.html = []
        self.text = []
        self.links = []
        self.lists = []
        self.pre = 0
        self.after_br = False

    def _break(self, count=1):
        trailing = 0
        for piece in reversed(self.text):
            trailing += len(piece) - len(piece.rstrip('\n'))
            if piece.strip('\n') or trailing >= count:
                break
        if trailing < count:
            self.text.append('\n' * (count - trailing))

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'img':
            # No external image requests; keep useful alt text.
            self.handle_data(attrs.get('alt', ''))
            return
        if tag not in _ALLOWED:
            return
        safe = ''
        if tag in _STYLES:
            safe += ' style="' + _STYLES[tag] + '"'
        if tag == 'a':
            href = attrs.get('href', '')
            href = href if _safe_url(href) else ''
            self.links.append(href)
            if href:
                safe += ' href="' + escape(href, quote=True) + '"'
        if tag in _BLOCKS:
            self._break(2)
        if tag in {'ul', 'ol'}:
            start = attrs.get('start', '1')
            start = int(start) if start.isdecimal() and len(start) <= 9 else 1
            self.lists.append([tag, start])
            if tag == 'ol':
                safe += f' start="{start}"'
        if tag == 'li':
            self._break()
            if self.lists and self.lists[-1][0] == 'ol':
                marker = str(self.lists[-1][1]) + '. '
                self.lists[-1][1] += 1
            else:
                marker = '- '
            self.text.append('  ' * max(0, len(self.lists) - 1) + marker)
        if tag == 'tr':
            self._break()
        if tag == 'br':
            self.text.append('\n')
        if tag == 'hr':
            self._break(2)
            self.text.append('---\n\n')
        if tag == 'pre':
            self.pre += 1
        self.html.append('<' + tag + safe + '>')
        self.after_br = tag == 'br'

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag not in _ALLOWED or tag in {'br', 'hr'}:
            return
        self.html.append('</' + tag + '>')
        if tag == 'a' and self.links:
            href = self.links.pop()
            if href:
                self.text.append(' <' + href + '>')
        if tag == 'pre':
            self.pre -= 1
        if tag in {'ul', 'ol'} and self.lists:
            self.lists.pop()
        if tag in {'td', 'th'}:
            self.text.append('\t')
        if tag in _BLOCKS:
            self._break(2)
        elif tag in {'li', 'tr'}:
            self._break()

    def handle_data(self, data):
        self.html.append(escape(data))
        if self.after_br and not self.pre and data.startswith('\n'):
            data = data[1:]
        self.after_br = False
        # Markdown's formatting whitespace between tags is not document text.
        if self.pre or data.strip():
            self.text.append(data)


def render_email(body):
    """Return (readable plain text, HTML) from Unicode Markdown.

    Real single line breaks become <br>; blank lines delimit paragraphs. Literal
    ``\\n`` stays literal. Raw tags are escaped before the generated HTML filter.
    """
    parser = markdown.Markdown(extensions=['tables', 'fenced_code', 'nl2br'])
    parser.preprocessors.deregister('html_block')
    parser.inlinePatterns.deregister('html')
    rendered = parser.convert(body.replace('\r\n', '\n').replace('\r', '\n'))
    filtered = _EmailHTML()
    filtered.feed(rendered)
    filtered.close()
    html = ('<!doctype html><html><head><meta charset="utf-8"></head>'
            '<body><div style="font-family:Arial,sans-serif;font-size:14px;'
            'line-height:1.5;color:#222222;">' + ''.join(filtered.html)
            + '</div></body></html>')
    return ''.join(filtered.text).strip('\n') + '\n', html


def normalize_email_body(value):
    """Convert Markdown blocks (or legacy strings) to a bounded text body.

    Legacy callers may still supply a string. No escape decoding, sentence
    guessing, or model rewrite is performed. Keep real newlines inside blocks;
    empty array items still represent blank lines.
    """
    if isinstance(value, list):
        if not 1 <= len(value) <= 4096 or not all(
            isinstance(block, str) for block in value
        ):
            raise ValueError('Provide an array of Markdown text blocks.')
        body = '\n'.join(value)
    elif isinstance(value, str):
        body = value
    else:
        raise ValueError('Body must be Markdown text or an array of Markdown blocks.')
    if not body.strip() or len(body) > 65536:
        raise ValueError('Body must contain 1 to 65536 characters including line breaks.')
    return body
