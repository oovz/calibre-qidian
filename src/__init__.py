import re
import time
from queue import Queue, Empty
from urllib.parse import urlparse, unquote, urlencode
from urllib.request import Request, urlopen

from calibre.ebooks.metadata.book.base import Metadata
from calibre.ebooks.metadata.sources.base import Source
from calibre.ebooks.chardet import xml_to_unicode
from calibre.utils.cleantext import clean_ascii_chars
from lxml import etree
from lxml.html import tostring

# note that string passed-in will need to be url-encoded
QIDIAN_SEARCH_URL = "https://www.qidian.com/so/%s.html"
QIDIAN_BOOK_URL_OLD = "https://book.qidian.com/info/%s/"
QIDIAN_BOOK_URL = 'https://www.qidian.com/book/%s/'
QIDIAN_BOOK_URL_PATTERN = re.compile("https://.*.qidian.com/book/(\\d+)/?")
QIDIAN_BOOKCOVER_URL_OLD = 'https://bookcover.yuewen.com/qdbimg/349573/%s/'
# note that without '/' the webserver will return the latest full-size cover image
QIDIAN_BOOKCOVER_URL = 'https://bookcover.yuewen.com/qdbimg/349573/%s'

PROVIDER_ID = "qidian"
PROVIDER_VERSION = (1, 1, 0)
PROVIDER_AUTHOR = 'Otaro'

def parse_html(raw):
    try:
        from html5_parser import parse
    except ImportError:
        # Old versions of calibre
        import html5lib
        return html5lib.parse(raw, treebuilder='lxml', namespaceHTMLElements=False)
    else:
        return parse(raw)

# a metadata download plugin
class Qidian(Source):
    name = 'Qidian.com'  # Name of the plugin
    description = 'Downloads metadata and covers from Qidian.'
    supported_platforms = ['windows', 'osx', 'linux']  # Platforms this plugin will run on
    author = PROVIDER_AUTHOR  # The author of this plugin
    version = PROVIDER_VERSION  # The version number of this plugin
    minimum_calibre_version = (5, 0, 0)
    capabilities = frozenset(['identify', 'cover'])
    touched_fields = frozenset([
        'title', 'authors', 'identifier:' + PROVIDER_ID, 
        'comments', 'publisher', 'languages', 'tags'
    ])  # published date (pubdate) is currently disabled
    has_html_comments = True
    supports_gzip_transfer_encoding = True
    can_get_multiple_covers = True

    def __init__(self, *args, **kwargs):
        Source.__init__(self, *args, **kwargs)

    def get_book_url(self, identifiers):
        qidian_id = identifiers.get(PROVIDER_ID, None)
        if qidian_id:
            return (PROVIDER_ID, qidian_id, QIDIAN_BOOK_URL % qidian_id)
        return None
    
    def get_book_url_name(self, idtype, idval, url):
        return "起点中文网"
    
    def get_cached_cover_url(self, identifiers):
        qidian_id = identifiers.get(PROVIDER_ID, None)
        if qidian_id:
            return QIDIAN_BOOKCOVER_URL % qidian_id
        return None
    
    def id_from_url(self, url):
        res = QIDIAN_BOOK_URL_PATTERN.findall(url)
        if len(res) == 1:
            return res[0]
        return None

    def identify(
            self,
            log,
            result_queue,
            abort,
            title=None,
            authors=None,
            identifiers={},
            timeout=30):

        qidian_id = identifiers.get(PROVIDER_ID, None)
        if qidian_id:
            url = QIDIAN_BOOK_URL_OLD % qidian_id
            log.info('identify with qidian id (%s) from url: %s' % (qidian_id, url))
            br = self.browser
            try:
                raw = br.open_novisit(url, timeout=timeout).read().strip()
            except Exception as e:
                log.exception(e)
                return None

            raw = clean_ascii_chars(xml_to_unicode(raw, strip_encoding_pats=True, resolve_entities=True)[0])

            try:
                root = parse_html(raw)
            except Exception as e:
                log.exception(e)
                return None
            
            title = root.xpath('//em[@id="bookName"]')[0].text
            author = root.xpath('//a[@class="writer"]')[0].text
            desc = tostring(root.xpath('//div[@class="book-intro"]')[0], method='html', encoding='utf-8').strip()
            tags = list(map(lambda elem: elem.text, root.xpath('//p[contains(@class, "tag")]/a[contains(@class, "red")]')))

            mi = Metadata(title, [author])
            mi.identifiers = { PROVIDER_ID: qidian_id }
            mi.comments = desc
            mi.publisher = "起点中文网"
            mi.language = 'zh_CN'
            mi.tags = tags
            mi.url = QIDIAN_BOOK_URL % qidian_id
            mi.cover = QIDIAN_BOOKCOVER_URL % qidian_id

            result_queue.put(mi)

            return None

        log.error('Note: Qidian.com blocks requests from non-browser agents. Please manually identify the book and put the id in the metadata editor.')
        return None

        # qidian is smart enough to search with title+author, even with misspelling
        normalized_authors = [] if authors is None else authors
        normalized_title = [] if title is None else [title]
        search_url = QIDIAN_SEARCH_URL % "".join(normalized_title + normalized_authors)
        log.info(
            "identify with title and/or author (%s) from url: %s"
            % ("".join(normalized_title + normalized_authors), search_url)
        )

        br = self.browser
        try:
            raw = br.open_novisit(search_url, timeout=timeout).read().strip()
        except Exception as e:
            log.exception(e)
            return None

        raw = clean_ascii_chars(
            xml_to_unicode(raw, strip_encoding_pats=True, resolve_entities=True)[0]
        )

        try:
            root = parse_html(raw)
        except Exception as e:
            log.exception(e)
            return None
        
        books = root.xpath('//li[contains(@class, "res-book-item")]')
        log.info("found %d books from the search" % len(books))

        for i, book in enumerate(books):
            qidian_id = book.get('data-bid', None)
            if qidian_id is None:
                log.error('invalid book with no data-bid from search: %s', search_url)
                continue
            
            # we'll use the search page result for the sake of speed
            # with the trade-off of no tags and un-formatted desc
            bTitle = book.xpath('//h3[@class="book-info-title"]/a')[i].get('title', '').replace('在线阅读','')
            bAuthor = book.xpath('//p[@class="author"]/a[@class="name"]')[i].text
            bDesc = tostring(book.xpath('//p[contains(@class, "intro")]')[i], method='html', encoding='utf-8').strip()

            mi = Metadata(bTitle, [bAuthor])
            mi.identifiers = { PROVIDER_ID: qidian_id }
            mi.comments = bDesc
            mi.publisher = "起点中文网"
            mi.language = 'zh_CN'
            mi.tags = []
            mi.url = QIDIAN_BOOK_URL % qidian_id
            mi.cover = QIDIAN_BOOKCOVER_URL % qidian_id

            log.info("[%d] id (%s) title (%s) author (%s)" % (i, qidian_id, bTitle, bAuthor))

            result_queue.put(mi)
        
    def download_cover(
        self,
        log,
        result_queue,
        abort,
        title=None,
        authors=None,
        identifiers={},
        timeout=30,
        get_best_cover=False):
        
        qidian_id = identifiers.get(PROVIDER_ID, None)
        if qidian_id is None:
            log.info('No id found, running identify')
            rq = Queue()
            self.identify(
                log,
                rq,
                abort,
                title=title,
                authors=authors,
                identifiers=identifiers
            )
            if abort.is_set():
                return

            results = []
            while True:
                try:
                    results.append(rq.get_nowait())
                except Empty:
                    break

            if len(results) == 0:
                log.info('no result after running identify')
                return

            results.sort(
                key=self.identify_results_keygen(
                    title=title, authors=authors, identifiers=identifiers
                )
            )

            # get the first result
            qidian_id = results[0].identifiers.get(PROVIDER_ID, None)
        
        if qidian_id is None:
            log.info('No id found after running identify')
            return

        cover_url = QIDIAN_BOOKCOVER_URL % qidian_id
        br = self.browser
        log('Downloading latest cover from:', cover_url)
        try:
            time.sleep(1)
            cdata = br.open_novisit(cover_url, timeout=timeout).read()
            if cdata:
                result_queue.put((self, cdata))
        except:
            log.exception('Failed to download latest cover from:', cover_url)

        if get_best_cover is False:
            old_cover_url = QIDIAN_BOOKCOVER_URL_OLD % qidian_id
            br = self.browser
            log('Downloading old cover from:', old_cover_url)
            try:
                time.sleep(1)
                cdata = br.open_novisit(old_cover_url, timeout=timeout).read()
                if cdata:
                    result_queue.put((self, cdata))
            except:
                log.exception('Failed to download old cover from:', old_cover_url)


if __name__ == "__main__":
    # To run these test use: calibre-debug -e ./__init__.py
    from calibre.ebooks.metadata.sources.test import (
        test_identify_plugin, title_test, authors_test
    )

    test_identify_plugin(
        Qidian.name, [
            ({
                 'identifiers': {
                     'qidian': '1025325277'
                 },
             }, [title_test('我们生活在南京', exact=True),
                 authors_test(['天瑞说符'])]),
            # ({
            #      'title': '一世之尊'
            #  }, [title_test('一世之尊', exact=True),
            #      authors_test(['爱潜水的乌贼'])]),
        ]
    )
