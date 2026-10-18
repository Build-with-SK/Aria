"""Parse bounded, untrusted feed XML without DTDs or entity declarations."""
from xml.etree import ElementTree as ET

MAX_XML_BYTES = 8 * 1024 * 1024


class _FeedTreeBuilder(ET.TreeBuilder):
    def doctype(self, name, pubid, system):
        raise ET.ParseError("DTD/entity declarations are forbidden in feed XML")


def feed_xml(body):
    # Check encoded size too: UTF-16 input must not bypass a byte substring
    # filter. The parser reports DOCTYPE structurally, before expansion.
    size = len(body.encode("utf-8")) if isinstance(body, str) else len(body)
    if size > MAX_XML_BYTES:
        raise ET.ParseError("feed XML exceeds the byte limit")
    return ET.fromstring(body, parser=ET.XMLParser(target=_FeedTreeBuilder()))
