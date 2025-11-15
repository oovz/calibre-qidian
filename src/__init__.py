import re
import time
import base64
from queue import Queue, Empty
from urllib.parse import urlparse, unquote, urlencode, quote, parse_qs
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
M_QIDIAN_BOOK_URL = 'https://m.qidian.com/book/%s/'
# Pattern to extract ID from both old and new Qidian URL formats
QIDIAN_BOOK_URL_PATTERN = re.compile(r"(?:qidian\.com/book/|book\.qidian\.com/info/|m\.qidian\.com/book/)(\d+)")
QIDIAN_BOOKCOVER_URL_OLD = 'https://bookcover.yuewen.com/qdbimg/349573/%s/'
# note that without '/' the webserver will return the latest full-size cover image
QIDIAN_BOOKCOVER_URL = 'https://bookcover.yuewen.com/qdbimg/349573/%s'

# Bing search URL with site: operator to limit search to Qidian
BING_SEARCH_URL = 'https://www.bing.com/search?q=%s+site%%253Awww.qidian.com'
# XPath for Bing search results
BING_SEARCH_RESULTS_XPATH = '//ol[@id="b_results"]/li[@class="b_algo"]//h2/a'

PROVIDER_ID = "qidian"
PROVIDER_VERSION = (1, 4, 0)
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
    name = '起点中文网'  # Name of the plugin
    description = 'Downloads metadata and covers from Qidian (qidian.com)'
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

    def _get_browser(self):
        br = self.browser
        try:
            headers = dict(getattr(br, 'addheaders', []))
        except Exception:
            headers = {}
        headers['User-Agent'] = 'Mozilla/5.0 (Linux; Android 13; Pixel 6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Mobile Safari/537.36'
        headers.setdefault('Accept', 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8')
        headers.setdefault('Accept-Language', 'zh-CN,zh;q=0.9,en;q=0.8')
        br.addheaders = list(headers.items())
        return br

    def _first_text(self, values, default=None):
        for v in values:
            if v:
                v = v.strip()
                if v:
                    return v
        return default

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
        
    def extract_real_url_from_ck(self, href, log):
        """
        Extract the real URL from a Bing click tracking (CK) link.
        These links contain an encoded target URL in the 'u' parameter.
        If not a CK link, returns the original URL.
        """
        try:
            # Check if this is a Bing tracking link
            if 'bing.com/ck/' in href:
                log.info(f'Detected Bing click tracking URL: {href}')
                
                # Parse the URL and extract query parameters
                parsed_url = urlparse(href)
                query_params = parse_qs(parsed_url.query)
                
                # Look for the 'u' parameter containing the encoded URL
                if 'u' in query_params and query_params['u']:
                    encoded_url = query_params['u'][0]
                    
                    # Bing adds 'a1' prefix to the base64 encoding
                    if encoded_url.startswith('a1'):
                        encoded_url = encoded_url[2:]  # Remove the 'a1' prefix
                    
                    # Decode from base64
                    try:
                        # Add padding if needed
                        padding_needed = len(encoded_url) % 4
                        if padding_needed:
                            encoded_url += '=' * (4 - padding_needed)
                            
                        real_url = base64.b64decode(encoded_url).decode('utf-8')
                        log.info(f"Extracted real URL: {real_url}")
                        return real_url
                    except Exception as e:
                        log.error(f"Failed to decode base64 URL: {e}")
            
            # If not a CK link or decoding fails, return the original URL
            return href
        except Exception as e:
            log.error(f"Error processing URL {href}: {e}")
            return href
        
    def search_bing_for_qidian(self, title, author, log, timeout=30):
        """Search Bing for books on Qidian based on title and author"""
        # Build search terms
        search_terms = []
        
        if not title:
            log.error('Title is required for searching')
            return []
            
        search_terms.append(title)
        
        if author:
            search_terms.append(author)
        
        query = " ".join(search_terms)
        encoded_query = quote(query)
        
        search_url = BING_SEARCH_URL % encoded_query
        
        log.info(f'Searching Bing with query: {query}')
        log.info(f'Search URL: {search_url}')
        
        br = self._get_browser()
        try:
            raw = br.open_novisit(search_url, timeout=timeout).read().strip()
            raw = clean_ascii_chars(xml_to_unicode(raw, strip_encoding_pats=True, resolve_entities=True)[0])
            
            root = parse_html(raw)
            
            # Use the specific XPath for Bing search results
            search_results = root.xpath(BING_SEARCH_RESULTS_XPATH, method='html', encoding='utf-8')
            
            log.info(f'Found {len(search_results)} search results from Bing')
            
            # Process results to find valid Qidian book pages
            found_ids = []
            
            for result in search_results:
                href = result.get('href', '')
                
                # Process the href to handle Bing click tracking links
                href = self.extract_real_url_from_ck(href, log)

                # Extract all text from the element, including text in <strong> tags
                result_text = "".join(result.xpath('.//text()', method='html', encoding='utf-8')).strip()
                
                log.info(f'Examining search result: "{result_text}" -> {href}')
                
                # Skip if not a Qidian URL
                if 'qidian.com' not in href:
                    continue
                    
                # Skip category pages, search pages, rank pages, etc.
                if any(x in href for x in ['search', 'category', 'rank', 'forum', 'user']):
                    continue
                
                # Extract book ID from URL
                qidian_id = self.id_from_url(href)
                
                if qidian_id:
                    log.info(f'Found book ID: {qidian_id} from URL: {href}')
                    found_ids.append((qidian_id, href, result_text))
            
            return found_ids
            
        except Exception as e:
            log.exception(f'Error searching Bing: {e}')
            return []

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
            url = M_QIDIAN_BOOK_URL % qidian_id
            log.info('identify with qidian id (%s) from mobile url: %s' % (qidian_id, url))
            br = self._get_browser()
            try:
                raw = br.open_novisit(url, timeout=timeout).read().strip()
            except Exception as e:
                log.exception(e)
                for url_tpl in (QIDIAN_BOOK_URL_OLD, QIDIAN_BOOK_URL):
                    try:
                        url = url_tpl % qidian_id
                        log.info('Trying fallback URL: %s' % url)
                        raw = br.open_novisit(url, timeout=timeout).read().strip()
                        break
                    except Exception as e2:
                        log.exception(e2)
                else:
                    return None

            raw = clean_ascii_chars(xml_to_unicode(raw, strip_encoding_pats=True, resolve_entities=True)[0])

            try:
                root = parse_html(raw)
            except Exception as e:
                log.exception(e)
                return None

            title = self._first_text(
                root.xpath('//meta[@property="og:novel:book_name"]/@content') +
                root.xpath('//meta[@property="og:title"]/@content')
            )
            author = self._first_text(
                root.xpath('//meta[@property="og:novel:author"]/@content')
            )
            desc = self._first_text(
                root.xpath('//meta[@property="og:description"]/@content') +
                root.xpath('//meta[@name="description"]/@content')
            )
            category = self._first_text(
                root.xpath('//meta[@property="og:novel:category"]/@content')
            )
            status = self._first_text(
                root.xpath('//meta[@property="og:novel:status"]/@content')
            )
            tags = []
            if category:
                tags.append(category)
            if status:
                tags.append(status)

            if not title or not author:
                log.error('Failed to extract title/author from Qidian mobile page for id %s' % qidian_id)
                return None

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
        
        # If we have other identifiers, give up
        if identifiers:
            log.info("Other identifiers found, giving up")
            return None

        # If we don't have ID, ensure we have a title
        if not title:
            log.error('Title is required for search')
            return None
            
        # We have title, proceed with search
        log.info('Searching for book using Bing')
        author = authors[0] if authors and len(authors) > 0 else None
        
        # First try with both title and author if both are available
        if author:
            log.info(f'Searching with title "{title}" and author "{author}"')
            search_results = self.search_bing_for_qidian(title, author, log, timeout)
            
            # If we got no results with author included, retry with just title
            if not search_results:
                log.info(f'No results found with title and author, retrying with title only: "{title}"')
                search_results = self.search_bing_for_qidian(title, None, log, timeout)
        else:
            # Search with title only
            log.info(f'Searching with title only: "{title}"')
            search_results = self.search_bing_for_qidian(title, None, log, timeout)
        
        if not search_results:
            log.error('No matching books found on Qidian via Bing search')
            return None
            
        log.info(f'Found {len(search_results)} potential books')
        
        # Process each found book ID (limit to first 3)
        for i, (book_id, book_url, result_text) in enumerate(search_results[:3]):
            if abort.is_set():
                break
                
            log.info(f'Processing book {i+1} with ID {book_id}')
            
            # Fetch book details using the existing identify method
            book_identifiers = {PROVIDER_ID: book_id}
            
            # Create a temporary queue to get results from the identify call
            temp_queue = Queue()
            
            # Recursively call identify with the found ID
            self.identify(log, temp_queue, abort, identifiers=book_identifiers, timeout=timeout)
            
            # Check if we got a result
            results = []
            while True:
                try:
                    results.append(temp_queue.get_nowait())
                except Empty:
                    break
            
            for mi in results:
                # Verify title/author if they were provided
                book_title = mi.title.lower() if mi.title else ''
                book_authors = [a.lower() for a in mi.authors] if mi.authors else []
                
                # Basic verification logic
                title_match = title.lower() in book_title or book_title in title.lower()
                author_match = not author or any(author.lower() in ba or ba in author.lower() for ba in book_authors)
                
                if title_match and author_match:
                    log.info(f'Found matching book: {mi.title} by {", ".join(mi.authors)}')
                    result_queue.put(mi)
                else:
                    log.info(f'Skipping non-matching book: {mi.title} by {", ".join(mi.authors)}')
        
        return None

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
        br = self._get_browser()
        log('Downloading latest cover from:', cover_url)
        try:
            time.sleep(1)
            cdata = br.open_novisit(cover_url, timeout=timeout).read()
            if cdata:
                result_queue.put((self, cdata))
        except:
            log.exception('Failed to download latest cover from:', cover_url)

        # @TODO: implement a comparison method for get_best_cover
        old_cover_url = QIDIAN_BOOKCOVER_URL_OLD % qidian_id
        br = self._get_browser()
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
            ({
                 'title': '一世之尊'
             }, [title_test('一世之尊', exact=True),
                 authors_test(['爱潜水的乌贼'])]),
        ]
    )
