from _msg import Msg


class State(Msg):
    _defaults = {"connected": True, "armed": False, "mode": "POSCTL"}


class ExtendedState(Msg):
    _defaults = {"landed_state": 1}


class HomePosition(Msg):
    pass


class StatusText(Msg):
    _defaults = {"severity": 4, "text": ""}


class WaypointList(Msg):
    _defaults = {"current_seq": 0, "waypoints": []}


class WaypointReached(Msg):
    _defaults = {"wp_seq": 0}


class Waypoint(Msg):
    _defaults = {"frame": 3, "command": 16, "is_current": False, "autocontinue": True, "param1": 0.0, "param2": 0.0,
                 "param3": 0.0, "param4": 0.0, "x_lat": 0.0, "y_long": 0.0, "z_alt": 0.0}
