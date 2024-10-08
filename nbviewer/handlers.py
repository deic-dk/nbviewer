# -----------------------------------------------------------------------------
#  Copyright (C) Jupyter Development Team
#
#  Distributed under the terms of the BSD License.  The full license is in
#  the file COPYING, distributed as part of this software.
# -----------------------------------------------------------------------------
from tornado import web

from .providers import _load_handler_from_location
from .providers import provider_handlers
from .providers import provider_uri_rewrites
from .providers.base import BaseHandler
from .providers.base import format_prefix
from .utils import transform_ipynb_uri
from .utils import url_path_join

import requests
import json

# -----------------------------------------------------------------------------
# Handler classes
# -----------------------------------------------------------------------------


class Custom404(BaseHandler):
    """Render our 404 template"""

    def prepare(self):
        # skip parent prepare() step, just render the 404
        raise web.HTTPError(404)


class IndexHandler(BaseHandler):
    """Render the index"""

    def render_index_template(self, **namespace):
        # Refresh from sciencedata
        req = requests.get("https://sciencedata.dk/remote.php/notebooks")
        sectionsJson = req.text
        sections = json.loads(sectionsJson)
        req.close()
        # check if the JSON has a 'sections' field, otherwise assume it is just a list of sections,
        # and provide the defaults of the other fields
        if "sections" not in self.frontpage_setup:
            self.frontpage_setup = {
                "title": "ScienceNotebooks",
                "subtitle": "Scientific Jupyter notebooks - shared",
                "show_input": True,
                "sections": sections
            }
        return self.render_template(
            "index.html",
            title=self.frontpage_setup.get("title", None),
            subtitle=self.frontpage_setup.get("subtitle", None),
            image=self.frontpage_setup.get("image", None),
            text=self.frontpage_setup.get("text", None),
            show_input=self.frontpage_setup.get("show_input", True),
            sections=self.frontpage_setup.get("sections", {}),
            url=self.request.full_url(),
            noheader=True,
            **namespace
        )

    def get(self):
        self.finish(self.render_index_template())


class FAQHandler(BaseHandler):
    """Render the markdown FAQ page"""

    def get(self):
        self.finish(self.render_template("faq.md"))


class CreateHandler(BaseHandler):
    """handle creation via frontpage form

    only redirects to the appropriate URL
    """

    uri_rewrite_list = None

    def post(self):
        # curl -X POST -F "nb=@notebook.ipynb" https://localhost:5000/create/
        if self.request.files.get('nb', None):
            import uuid
            uploadFile = self.request.files['nb'][0]
            filename = uuid.uuid4().hex + uploadFile['filename']
            self.log.info("nb %s", filename)
            localfile_path = self.settings.get("localfile_path", "")
            filepath = localfile_path+"/"+filename
            fileObj = open(filepath, 'wb')
            fileObj.write(uploadFile['body'])
            self.log.info("notebook %s", filepath)
            redirect_url = "/localfile/"+filename
        else:
            value = self.get_argument("gistnorurl", "")
            redirect_url = transform_ipynb_uri(value, self.get_provider_rewrites())
            self.log.info("gistnorurl %s", value)
        self.log.info("create %s", redirect_url)
        self.redirect(url_path_join(self.base_url, redirect_url))

    def get_provider_rewrites(self):
        # storing this on a class attribute is a little icky, but is better
        # than the global this was refactored from.
        if self.uri_rewrite_list is None:
            # providers is a list of module import paths
            providers = self.settings["provider_rewrites"]

            type(self).uri_rewrite_list = provider_uri_rewrites(providers)
        return self.uri_rewrite_list


# -----------------------------------------------------------------------------
# Default handler URL mapping
# -----------------------------------------------------------------------------


def format_handlers(formats, urlspecs, **handler_settings):
    """
    Tornado handler URLSpec of form (route, handler_class, initalize_kwargs)
    https://www.tornadoweb.org/en/stable/web.html#tornado.web.URLSpec
    kwargs passed to initialize are None by default but can be added
    https://www.tornadoweb.org/en/stable/web.html#tornado.web.RequestHandler.initialize
    """
    urlspecs = [
        (prefix + url, handler, {"format": format, "format_prefix": prefix})
        for format in formats
        for url, handler, initialize_kwargs in urlspecs
        for prefix in [format_prefix + format]
    ]
    for handler_setting in handler_settings:
        if handler_settings[handler_setting]:
            # here we modify the URLSpec dict to have the key-value pairs from
            # handler_settings in NBViewer.init_tornado_application
            for urlspec in urlspecs:
                urlspec[2][handler_setting] = handler_settings[handler_setting]
    return urlspecs


def init_handlers(formats, providers, base_url, localfiles, **handler_kwargs):
    """
    `handler_kwargs` is a dict of dicts: first dict is `handler_names`, which
    specifies the handler_classes to load for the providers, the second
    is `handler_settings` (see comments in format_handlers)
    Only `handler_settings` should get added to the initialize_kwargs in the
    handler URLSpecs, which is why we pass only it to `format_handlers`
    but both it and `handler_names` to `provider_handlers`
    """
    handler_settings = handler_kwargs["handler_settings"]
    handler_names = handler_kwargs["handler_names"]

    create_handler = _load_handler_from_location(handler_names["create_handler"])
    custom404_handler = _load_handler_from_location(handler_names["custom404_handler"])
    faq_handler = _load_handler_from_location(handler_names["faq_handler"])
    index_handler = _load_handler_from_location(handler_names["index_handler"])

    # If requested endpoint matches multiple routes, it only gets handled by handler
    # corresponding to the first matching route. So order of URLSpecs in this list matters.
    pre_providers = [
        ("/?", index_handler, {}),
        ("/index.html", index_handler, {}),
        (r"/faq/?", faq_handler, {}),
        (r"/create/?", create_handler, {}),
        # don't let super old browsers request data-uris
        (r".*/data:.*;base64,.*", custom404_handler, {}),
    ]

    post_providers = [(r"/(robots\.txt|favicon\.ico)", web.StaticFileHandler, {})]

    # Add localfile handlers if the option is set
    if localfiles:
        # Put local provider first as per the comment at
        # https://github.com/jupyter/nbviewer/pull/727#discussion_r144448440.
        providers.insert(0, "nbviewer.providers.local")

    handlers = provider_handlers(providers, **handler_kwargs)

    raw_handlers = (
        pre_providers
        + handlers
        + format_handlers(formats, handlers, **handler_settings)
        + post_providers
    )

    new_handlers = []
    for handler in raw_handlers:
        pattern = url_path_join(base_url, handler[0])
        new_handler = tuple([pattern] + list(handler[1:]))
        new_handlers.append(new_handler)
    new_handlers.append((r".*", custom404_handler, {}))

    return new_handlers
