"""Поддельный rospy для тестов агента без ROS: подписки, сервисы и публикация в одном процессе."""
_subs, _services = {}, {}


class ROSException(Exception):
    pass


def init_node(name, anonymous=False, disable_signals=False):
    pass


class Subscriber:
    def __init__(self, topic, msg_type, callback, queue_size=None):
        _subs.setdefault(topic, []).append(callback)


def publish(topic, msg):
    for cb in list(_subs.get(topic, [])):
        cb(msg)


def wait_for_service(name, timeout=None):
    if name not in _services:
        raise ROSException("timeout exceeded while waiting for service %s" % name)


class ServiceProxy:
    def __init__(self, name, srv_type=None):
        self.name = name

    def __call__(self, **kw):
        h = _services.get(self.name)
        if h is None:
            raise ROSException("service [%s] unavailable" % self.name)
        return h(**kw)


def reset():
    _subs.clear()
    _services.clear()
