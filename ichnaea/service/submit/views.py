from collections import defaultdict
import datetime

from colander import iso8601
from cornice import Service
from pyramid.httpexceptions import HTTPNoContent
from sqlalchemy.sql import and_, or_

from ichnaea.content.models import (
    MapStat,
    MAPSTAT_TYPE,
    User,
)
from ichnaea.models import (
    Measure,
    normalize_wifi_key,
    RADIO_TYPE,
)
from ichnaea.decimaljson import (
    dumps,
    encode_datetime,
    to_precise_int,
)
from ichnaea.service.error import (
    error_handler,
    MSG_ONE_OF,
)
from ichnaea.service.submit.schema import SubmitSchema
from ichnaea.service.submit.tasks import (
    insert_cell_measure,
    insert_wifi_measure,
)
from ichnaea.service.submit.utils import process_score


def configure_submit(config):
    config.scan('ichnaea.service.submit.views')


def process_mapstat_keyed(factor, stat_key, measures, session):
    tiles = defaultdict(int)
    # aggregate to tiles, according to factor
    for measure in measures:
        tiles[(measure.lat / factor, measure.lon / factor)] += 1
    query = session.query(MapStat.lat, MapStat.lon).filter(
        MapStat.key == stat_key)
    # dynamically construct a (lat, lon) in (list of tuples) filter
    # as MySQL isn't able to use indexes on such in queries
    lat_lon = []
    for (lat, lon) in tiles.keys():
        lat_lon.append(and_((MapStat.lat == lat), (MapStat.lon == lon)))
    query = query.filter(or_(*lat_lon))
    result = query.all()
    prior = {}
    for r in result:
        prior[(r[0], r[1])] = True
    tile_count = 0
    for (lat, lon), value in tiles.items():
        old = prior.get((lat, lon), False)
        if old:
            stmt = MapStat.__table__.update().where(
                MapStat.lat == lat).where(
                MapStat.lon == lon).where(
                MapStat.key == stat_key).values(
                value=MapStat.value + value)
        else:
            tile_count += 1
            stmt = MapStat.__table__.insert(
                on_duplicate='value = value + %s' % int(value)).values(
                lat=lat, lon=lon, key=stat_key, value=value)
        session.execute(stmt)
    return tile_count


def process_mapstat(measures, session, userid=None):
    # 10x10 meter tiles
    tile_count = process_mapstat_keyed(
        1000, MAPSTAT_TYPE['location'], measures, session)
    if userid is not None and tile_count > 0:
        process_score(userid, tile_count, session, key='new_location')
    # 10x10 km tiles
    process_mapstat_keyed(
        1000000, MAPSTAT_TYPE['location_10km'], measures, session)


def process_user(nickname, session):
    userid = None
    if (3 <= len(nickname) <= 128):
        # automatically create user objects and update nickname
        if isinstance(nickname, str):
            nickname = nickname.decode('utf-8', 'ignore')
        rows = session.query(User).filter(User.nickname == nickname)
        old = rows.first()
        if not old:
            user = User(nickname=nickname)
            session.add(user)
            session.flush()
            userid = user.id
        else:
            userid = old.id
    return (userid, nickname)


def process_time(measure, utcnow, utcmin):
    try:
        measure['time'] = iso8601.parse_date(measure['time'])
    except (iso8601.ParseError, TypeError):
        if measure['time']:  # pragma: no cover
            # ignore debug log for empty values
            pass
        measure['time'] = utcnow
    else:
        # don't accept future time values or
        # time values more than 60 days in the past
        if measure['time'] > utcnow or measure['time'] < utcmin:
            measure['time'] = utcnow
    return measure


def process_measure(data, utcnow, session, userid=None):
    measure = Measure()
    measure.created = utcnow
    measure.time = data['time']
    measure.lat = to_precise_int(data['lat'])
    measure.lon = to_precise_int(data['lon'])
    measure.accuracy = data['accuracy']
    measure.altitude = data['altitude']
    measure.altitude_accuracy = data['altitude_accuracy']
    measure.radio = RADIO_TYPE.get(data['radio'], -1)
    # get measure.id set
    session.add(measure)
    session.flush()
    measure_data = dict(
        id=measure.id, created=encode_datetime(measure.created),
        lat=measure.lat, lon=measure.lon, time=encode_datetime(measure.time),
        accuracy=measure.accuracy, altitude=measure.altitude,
        altitude_accuracy=measure.altitude_accuracy,
        radio=measure.radio,
    )
    if data.get('cell'):
        insert_cell_measure.delay(measure_data, data['cell'], userid=userid)
        measure.cell = dumps(data['cell'])
    if data.get('wifi'):
        # filter out old-style sha1 hashes
        too_long_keys = False
        for w in data['wifi']:
            w['key'] = key = normalize_wifi_key(w['key'])
            if len(key) > 12:
                too_long_keys = True
                break
        if not too_long_keys:
            insert_wifi_measure.delay(
                measure_data, data['wifi'], userid=userid)
            measure.wifi = dumps(data['wifi'])
    return measure


def check_cell_or_wifi(data, request):
    cell = data.get('cell', ())
    wifi = data.get('wifi', ())
    if not any(wifi) and not any(cell):
        request.errors.add('body', 'body', MSG_ONE_OF)


def submit_validator(request):
    if len(request.errors):
        return
    for item in request.validated['items']:
        if not check_cell_or_wifi(item, request):
            # quit on first Error
            return


submit = Service(
    name='submit',
    path='/v1/submit',
    description="Submit a measurement result for a location.",
)


@submit.post(renderer='json', accept="application/json",
             schema=SubmitSchema, error_handler=error_handler,
             validators=submit_validator)
def submit_post(request):
    session = request.db_master_session
    session_objects = []

    nickname = request.headers.get('X-Nickname', '')
    userid, nickname = process_user(nickname, session)

    utcnow = datetime.datetime.utcnow().replace(tzinfo=iso8601.UTC)
    utcmin = utcnow - datetime.timedelta(60)

    points = 0
    measures = []
    for item in request.validated['items']:
        item = process_time(item, utcnow, utcmin)
        measure = process_measure(item, utcnow, session, userid=userid)
        measures.append(measure)
        points += 1

    request.registry.heka_client.incr("items.uploaded",
                                      count=len(request.validated['items']))

    if userid is not None:
        process_score(userid, points, session)
    if measures:
        process_mapstat(measures, session, userid=userid)

    session.add_all(session_objects)
    session.commit()
    return HTTPNoContent()
