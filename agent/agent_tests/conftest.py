"""Тесты бортового агента: работают без ROS (поддельные rospy/MAVROS в fakeros/) и под Python 3.8."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for p in (os.path.join(HERE, "fakeros"), HERE, os.path.dirname(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)
