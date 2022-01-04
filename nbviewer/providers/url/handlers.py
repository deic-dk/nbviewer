# -----------------------------------------------------------------------------
#  Copyright (C) Jupyter Development Team
#
#  Distributed under the terms of the BSD License.  The full license is in
#  the file COPYING, distributed as part of this software.
# -----------------------------------------------------------------------------
from urllib import robotparser
from urllib.parse import urlparse, parse_qsl

from tornado import httpclient
from tornado import web
from tornado.escape import url_unescape

from .. import _load_handler_from_location
from ...utils import quote
from ...utils import response_text
from ..base import cached
from ..base import RenderingHandler
from ..base import BaseHandler

from webdav3.client import Client
from webdav3.exceptions import RemoteResourceNotFound

import os
import sys
import re
import urllib.parse

class URLHandler(RenderingHandler):
    """Renderer for /url or /urls"""

    async def get_notebook_data(self, secure, netloc, url):
        proto = "http" + secure
        netloc = url_unescape(netloc)

        remote_url = u"{}://{}/{}".format(proto, netloc, quote(url))

        if "?" in self.request.uri:
            link, query = self.request.uri.rsplit("?", 1)
        else:
            query = ""

        if query:
            remote_url = remote_url + "?" + query
        if not url.endswith(".ipynb"):
            # this is how we handle relative links (files/ URLs) in notebooks
            # if it's not a .ipynb URL and it is a link from a notebook,
            # redirect to the original URL rather than trying to render it as a notebook
            refer_url = self.request.headers.get("Referer", "").split("://")[-1]
            if refer_url.startswith(self.request.host + "/url"):
                self.redirect(remote_url)
                return

        parse_result = urlparse(remote_url)

        robots_url = parse_result.scheme + "://" + parse_result.netloc.strip("/") + "/robots.txt"

        public = False  # Assume non-public

        try:
            robots_response = await self.fetch(robots_url)
            robotstxt = response_text(robots_response)
            rfp = robotparser.RobotFileParser()
            rfp.set_url(robots_url)
            rfp.parse(robotstxt.splitlines())
            public = rfp.can_fetch("*", remote_url)
        except httpclient.HTTPError as e:
            self.log.debug(
                "Robots.txt not available for {}".format(remote_url), exc_info=True
            )
            public = True
        except Exception as e:
            self.log.error(e)

        return remote_url, public

    async def deliver_notebook(self, remote_url, public, url):

        bare_url = re.sub(r'\?.*$', '', remote_url)
        response = await self.fetch(bare_url)

        try:
            nbjson = response_text(response, encoding="utf-8")
        except UnicodeDecodeError:
            self.log.error("Notebook is not utf8: %s", remote_url, exc_info=True)
            raise web.HTTPError(400)

        # https://sciencedata.dk/public/9e48cffd2f1fa3fada752877f490d5d1/sddk.ipynb
        # https://sciencedata.dk/shared/9e48cffd2f1fa3fada752877f490d5d1?files=sddk.ipynb&download
        self.log.warn("remote_url: %s", remote_url, exc_info=True)
        download_url = re.sub(r'/public/([^/]+)/(.+\.ipynb)\?*.*$', r'/shared/\1?files=\2&download', remote_url)
        webdav_url = re.sub(r'(\.ipynb)\?*.*$', r'\1', remote_url)
        import_url = 'https://sciencedata.dk/index.php/apps/importer/importer.php?url='+urllib.parse.quote(urllib.parse.quote(webdav_url, safe="/:#?"), safe="")
        close_url = re.sub(r'/[^/]+\.ipynb(\?*.*)$', r'/\1', self.request.uri)+'&noindex=true'
        await self.finish_notebook(
            nbjson,
            download_url=download_url,
            import_url=import_url,
            close_url=close_url,
            msg="file from url: %s" % remote_url,
            public=public,
            request=self.request,
        )

    @cached
    async def get(self, secure, netloc, url):
        remote_url, public = await self.get_notebook_data(secure, netloc, url)
        await self.deliver_notebook(remote_url, public, url)

class WebDavTreeHandler(BaseHandler):
    """list files in a webdav directory"""

    def render_treelist_template(
        self,
        entries,
        breadcrumbs,
        url,
        **namespace
    ):
        """
        breadcrumbs: list of dict
            Breadcrumb 'name' and 'url' to render as links at the top of the notebook page
        """
        return self.render_template(
            "davlist.html",
            entries=entries,
            breadcrumbs=breadcrumbs,
            url=url,
            **namespace
        )

    @cached
    async def get(self, secure, netloc, url):
        proto = "http" + secure
        netloc = url_unescape(netloc)

        base_url = ""

        if "?" in self.request.uri:
            link, query = self.request.uri.rsplit("?", 1)
        else:
            query = ""

        query_str = ""
        if query:
            query_str = "&"+query
            if "&base_url=" in query_str:
                parsed_query = dict(parse_qsl(query))
                base_url = parsed_query['base_url']

        remote_url = u"{}://{}/".format(proto, netloc)

        webdav_options = {
         'webdav_hostname': remote_url
        }
        webdav_client = Client(webdav_options)

        files = []
        self.log.warn("URL: %s", url, exc_info=True)
        files = webdav_client.list(url.strip("/")+"/", get_info=True)
        self.log.warn("Listed: %s", files, exc_info=True)
        #if(len(files)>0):
        #    files.pop(0)

        if not base_url:
            base_url = "/"+url
            base_url_query = "base_url="+base_url
        else:
            base_url_query = ""
            query_str = query_str.strip("&")

        base_url_full = u"/url{secure}/{netloc}/{base_url}".format(secure=secure, netloc=netloc, base_url=base_url.lstrip("/"))
        path = ("/"+url).replace(base_url, "", 1)
        dir_path = path.rsplit("/", 1)[0]
        base_url_full_stripped = re.sub(r'\?.*$', '', base_url_full)
        breadcrumbs = [{"url": base_url_full, "name": netloc}]
        breadcrumbs.extend(self.breadcrumbs(dir_path, base_url_full_stripped, "?"+re.sub(r'&noindex=true', '', query)))

        entries = []
        dirs = []
        ipynbs = []
        others = []
        for file in files:
            e = {}
            name = os.path.basename(file["path"].strip("/"))
            e["name"] = name
            if file["isdir"]:
                query_str = re.sub(r'&noindex=true', '', query_str)
                e["url"] = u"/url{secure}/{netloc}/{url}{name}/?{base_url_query}{query_str}".format(
                    secure=secure, netloc=netloc, url=url, name=name, base_url_query=base_url_query, query_str=query_str
                )
                e["class"] = "fa-folder-open"
                dirs.append(e)
            elif file["path"].endswith(".ipynb"):
                if query:
                    query = "?"+query[1:]
                e["url"] = u"/url{secure}/{netloc}/{url}{name}?{base_url_query}{query_str}".format(
                    secure=secure, netloc=netloc, url=url, name=name, base_url_query=base_url_query, query_str=query_str
                )
                e["class"] = "fa-book"
                ipynbs.append(e)
                if file["path"].endswith("/index.ipynb"):
                    if query:
                        parsed_query = dict(parse_qsl(query))
                    if (not query) or (not 'noindex' in parsed_query):
                        self.redirect(e["url"])
            else:
                e["url"] = u"{remote_url}{url}{name}".format(
                    remote_url=remote_url, url=url, name=name
                )
                e["class"] = "fa-share"
                others.append(e)

        entries.extend(dirs)
        entries.extend(ipynbs)
        entries.extend(others)

        html = self.render_treelist_template(
            entries=entries,
            breadcrumbs=breadcrumbs,
            url=url
        )
        await self.cache_and_finish(html)

def default_handlers(handlers=[], **handler_names):
    """Tornado handlers"""

    url_handler = _load_handler_from_location(handler_names["url_handler"])
    tree_handler = _load_handler_from_location(handler_names["url_tree_handler"])

    return handlers + [
        (r"/url(?P<secure>[s]?)/(?P<netloc>[^/]+)/(?P<url>.*[^/])", url_handler, {}),
        (r"/url(?P<secure>[s]?)/(?P<netloc>[^/]+)/(?P<url>.*/)", tree_handler, {})
    ]

def uri_rewrites(rewrites=[]):
    return rewrites + [("^http(s?)://(.*)$", u"/url{0}/{1}"), ("^(.*)$", u"/url/{0}")]
