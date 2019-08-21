from .base import BaseHandler


class Index(BaseHandler):
    """Landing page from which a user can select an experiment to unpack.
    """
    def get(self):
        return self.render('index.html')
