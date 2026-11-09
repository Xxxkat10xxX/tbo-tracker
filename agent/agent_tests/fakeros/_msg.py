class Msg:
    _defaults = {}

    def __init__(self, **kw):
        self.__dict__.update(self._defaults)
        self.__dict__.update(kw)

    def __repr__(self):
        return "%s(%s)" % (type(self).__name__, self.__dict__)
