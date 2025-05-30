from django import template
from django.utils import safestring
from django.template.defaultfilters import stringfilter
import re
import random
import json as jsonlib

register = template.Library()

@register.filter
def likerttext(value):
    likertdict = { -3: "Strongly oppose",
                   -2: "Moderately oppose",
                   -1: "Slightly oppose",
                    0: "Neither support nor oppose",
                    1: "Slightly support",
                    2: "Moderately support",
                    3: "Strongly support"}
    return likertdict.get(value)

@register.filter
def ordinalhtml(value):
    """
    Converts an integer to its ordinal as HTML. 1 is '1<sup>st</sup>',
    and so on.
    """
    try:
        value = int(value)
    except ValueError:
        return value
    t = ('th', 'st', 'nd', 'rd', 'th', 'th', 'th', 'th', 'th', 'th')
    if value % 100 in (11, 12, 13): # special case
        return safestring.mark_safe("%d<sup>%s</sup>" % (value, t[0]))
    return safestring.mark_safe('%d<sup>%s</sup>' % (value, t[value % 10]))

@register.filter(is_safe=True)
@stringfilter
def markdown(value, trusted=False):
    # Renders the string using CommonMark in safe mode, which blocks
    # raw HTML in the input and also some links using a blacklist,
    # plus a second pass filtering using a whitelist for allowed
    # tags and URL schemes.

    import cmarkgfm
    from cmarkgfm.cmark import Options as cmarkgfmOptions

    html = cmarkgfm.github_flavored_markdown_to_html(value,
        options=cmarkgfmOptions.CMARK_OPT_UNSAFE if trusted else 0)

    import html5lib, urllib.parse
    def filter_url(url):
        try:
            urlp = urllib.parse.urlparse(url)
        except Exception as e:
            # invalid URL
            return None
        if urlp.scheme not in ("http", "https") and (not trusted or urlp.scheme not in ("data",)):
            return None
        return url
    valid_tags = set('strong em a code p h1 h2 h3 h4 h5 h6 pre br hr img ul ol li span blockquote'.split())
    valid_tags = set('{http://www.w3.org/1999/xhtml}' + tag for tag in valid_tags)
    dom = html5lib.HTMLParser().parseFragment(html)
    for node in dom.iter():
        if node.tag not in valid_tags and node.tag != 'DOCUMENT_FRAGMENT':
            node.tag = '{http://www.w3.org/1999/xhtml}span'
        for name, val in list(node.attrib.items()):
            if name.lower() in ("href", "src"):
                val = filter_url(val)
                if val is None:
                    node.attrib.pop(name)
                else:
                    node.set(name, val)
            else:
                # No other attributes are permitted.
                node.attrib.pop(name)

    # If there is an h1 in the output, demote all of the headings
    # so we don't create something that interfere's with the page h1.
    hash1 = False
    for node in dom.iter():
      if node.tag in ("h1", "{http://www.w3.org/1999/xhtml}h1"):
        hash1 = True
    if hash1:
      for node in dom.iter():
        m = re.match("(\{http://www.w3.org/1999/xhtml\})?h(\d)$", node.tag)
        if m:
          node.tag = (m.group(1) or "") + "h" + str(int(m.group(2))+1)

    html = html5lib.serialize(dom, quote_attr_values="always", omit_optional_tags=False, alphabetical_attributes=True)

    return safestring.mark_safe(html)

@register.filter(is_safe=True)
def json(value):
    return safestring.mark_safe(jsonlib.dumps(value))

@register.filter(is_safe=True)
@stringfilter
def stripfinalperiod(value):
    if value.endswith("."):
        value = value[:-1]
    return value

@register.filter
def mult(value, operand):
    return float(value) * float(operand)

@register.filter
def div(value, operand):
    return float(value) / float(operand)

