"""Public, portable timestamp-test utilities; compatible with the pinned Python 3.10."""


def iso8601_milliseconds(value):
    import datetime
    import re
    if not isinstance(value,str):raise AssertionError('API timestamp must be an ISO-8601 string')
    match=re.fullmatch(r'(\d{4}-\d\d-\d\d)[T ](\d\d:\d\d:\d\d)(?:\.(\d{1,9}))?(Z|[+-]\d\d:\d\d)?',value)
    if match is None:raise AssertionError('API timestamp is not ISO-8601')
    day,clock,fraction,zone=match.groups()
    fraction=((fraction or '')+'000000')[:6]
    zone='+00:00' if zone in (None,'Z') else zone
    parsed=datetime.datetime.fromisoformat(day+'T'+clock+'.'+fraction+zone)
    delta=parsed.astimezone(datetime.timezone.utc)-datetime.datetime(1970,1,1,tzinfo=datetime.timezone.utc)
    return (delta.days*86400+delta.seconds)*1000+delta.microseconds//1000


def observe_timestamp_update(before,mutate,timeout_seconds=3.0):
    """mutate must perform/assert a successful changed write and return its timestamp string."""
    import time
    if not 0<timeout_seconds<=5:raise ValueError('Use a finite timestamp observation bound of at most five seconds')
    first=iso8601_milliseconds(before);deadline=time.monotonic()+timeout_seconds
    while True:
        value=mutate();current=iso8601_milliseconds(value)
        if current>first:return value
        if current<first:raise AssertionError('updatedAt moved backwards')
        if time.monotonic()>=deadline:raise AssertionError('Successful changed writes did not refresh updatedAt')
        time.sleep(min(0.02,max(0,deadline-time.monotonic())))


def public_utility():
    import inspect
    return {'policy':'portable-public-timestamp-tests-v1',
        'source':inspect.getsource(iso8601_milliseconds)+'\n\n'+inspect.getsource(observe_timestamp_update),
        'contract':'Timestamps remain strings. Compare parsed values at millisecond precision. '
            'ISO fraction width and UTC designator spelling are not implementation requirements. '
            'Do not silently accept numbers, parse errors, failed writes, backwards time, or a non-advancing timestamp. '
            'A mutation callback must assert the required successful status and changed resource values on every call.'}
