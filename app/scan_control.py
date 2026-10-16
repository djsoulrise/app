"""Cooperative cancellation shared by metadata and audio workers."""


class ScanCancelled(Exception):
    def __init__(self, result=None):
        super().__init__('Skeniranje zaustavljeno; zavrseni rezultati su sacuvani.')
        self.result = result


def check_stop(stop_event):
    if stop_event is not None and stop_event.is_set():
        raise ScanCancelled()
