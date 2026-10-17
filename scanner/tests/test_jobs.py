import threading,time
from scanner import jobs

def test_pause_finishes_running_tasks_and_stops_new_dispatch():
    jobs.STOP.clear();jobs.PAUSE.clear();jobs.STATE.update(started=time.time(),active={})
    started=[];outputs=[];gate=threading.Event()
    def calculate(item):started.append(item);gate.wait(2);return item
    def run():
        for item,result,error in jobs.execute(list(range(6)),calculate,2):outputs.append(result)
    thread=threading.Thread(target=run);thread.start()
    end=time.monotonic()+2
    while len(started)<2 and time.monotonic()<end:time.sleep(.01)
    jobs.PAUSE.set();gate.set()
    end=time.monotonic()+2
    while len(outputs)<2 and time.monotonic()<end:time.sleep(.01)
    assert len(outputs)==2 and len(started)==2
    jobs.PAUSE.clear();thread.join(3)
    assert not thread.is_alive() and sorted(outputs)==list(range(6))
