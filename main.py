###########
# IMPORTS #
###########

from opensky_api import OpenSkyApi, TokenManager
import geocoder
import csv
import json
import os
import time
import urllib.request

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
import asyncio
from contextlib import asynccontextmanager

##########
# CONFIG #
##########

RADIUS_DEG = 1.0
POLL_INTERVAL = 10  # seconds

AIRPORT_TYPES = {'large_airport', 'medium_airport', 'small_airport'}
AIRPORTS_CSV_URL = 'https://davidmegginson.github.io/ourairports-data/airports.csv'
AIRPORTS_CSV_PATH = 'airports.csv'

# API Credentials
api = OpenSkyApi(token_manager=TokenManager.from_json_file('credentials.json'))

# IP-based location
g = geocoder.ip('me')
LAT_IP = g.latlng[0]
LON_IP = g.latlng[1]

##################
# AERODROME DATA #
##################

def _get_aerodromes_sync(lat_center: float, lon_center: float, radius_deg=None, airport_types=None) -> list[dict]:
    """
    Get aerodromes within a specified radius of a given latitude and longitude.

    :param lat_center: Latitude of the center point
    :param lon_center: Longitude of the center point
    :param radius_deg: Radius in degrees to search for aerodromes
    :param airport_types: Set of airport types to filter by
    :return: List of aerodromes within the specified radius
    """

    if airport_types is None:
        airport_types = AIRPORT_TYPES

    if radius_deg is None:
        radius_deg = RADIUS_DEG

    # Download airport data CSV if not already present
    if not os.path.exists(AIRPORTS_CSV_PATH):
        urllib.request.urlretrieve(AIRPORTS_CSV_URL, AIRPORTS_CSV_PATH)

    aerodromes = []

    with open(AIRPORTS_CSV_PATH, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)

        for row in reader:
            if row['type'] not in airport_types:
                continue
            if not row['ident'] or not row['latitude_deg'] or not row['longitude_deg']:
                continue

            lat = float(row['latitude_deg'])
            lon = float(row['longitude_deg'])

            if abs(lat - lat_center) <= radius_deg and abs(lon - lon_center) <= radius_deg:
                label = row['icao_code'] if row['icao_code'] else row['ident']
                aerodromes.append({'lon': lon, 'lat': lat, 'label': label})

    return aerodromes

async def get_aerodromes(lat_center: float, lon_center: float, radius_deg=None, airport_types=None) -> list[dict]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _get_aerodromes_sync, lat_center, lon_center, radius_deg, airport_types)

####################
# OPENSKY API CALL #
####################

def _fetch_aircraft_sync(lat: float, lon: float, radius_deg: float) -> list[dict]:
    """
    Fetch aircraft data from the OpenSky API within a specified radius of a given latitude and longitude.

    :param lat: Latitude of the center point
    :param lon: Longitude of the center point
    :param radius_deg: Radius in degrees to search for aircraft
    :return: List of aircraft data within the specified radius
    """
    
    try:
        states = api.get_states(
            bbox = (lat - radius_deg, lat + radius_deg, lon - radius_deg, lon + radius_deg)
            )
    except Exception as e:
        print(f"Error fetching aircraft data: {e}")
        return []

    if states is None:
        print("[DEBUG] api.get_states() returned None")
        return []

    if states.states is None:
        print("[DEBUG] No state data available from OpenSky API")
        return []

    aircraft = []

    for s in states.states:
        aircraft.append({
            'icao24':         s.icao24,
            'callsign':       s.callsign.strip() if s.callsign else 'N/A',
            'lat':            s.latitude,
            'lon':            s.longitude,
            'altitude':       round(s.geo_altitude * 3.28084) if s.geo_altitude is not None else (round(s.baro_altitude * 3.28084) if s.baro_altitude is not None else None),
            'speed':          round(s.velocity * 1.94384) if s.velocity is not None else None,
            'heading':        round(s.true_track) if s.true_track is not None else None,
            'vertical_rate':  round(s.vertical_rate, 1) if s.vertical_rate is not None else None,
            'on_ground':      s.on_ground,
            'origin_country': s.origin_country,
        })

    return aircraft

async def fetch_aircraft(lat: float, lon: float, radius_deg: float) -> list[dict]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _fetch_aircraft_sync, lat, lon, radius_deg)

########################
# MUTABLE SERVER STATE #
########################

state = {
    'lat': LAT_IP,
    'lon': LON_IP, 
    'aircraft': [],
    'aerodromes': [],
}

connected_clients: list[WebSocket] = []

#######################
# WEBSOCKET BROADCAST #
#######################

async def broadcast(message: dict):
    """
    Broadcast a message to all connected WebSocket clients.

    :param message: The message to broadcast
    """
    if not connected_clients:
        print("[DEBUG] No connected clients to broadcast to.")
        return

    data = json.dumps(message)
    disconnected_clients = []

    for client in connected_clients:
        try:
            await client.send_text(data)
        except WebSocketDisconnect:
            disconnected_clients.append(client)
        except Exception as e:
            print(f"[DEBUG] Error sending message to client: {e}")
            disconnected_clients.append(client)

    for client in disconnected_clients:
        connected_clients.remove(client)
        print("[DEBUG] Removed disconnected client.")

###################
# BACKGROUND TASKS#
###################

async def poll_aircraft():
    """
    Poll aircraft data from the OpenSky API at regular intervals and broadcast updates to connected clients.
    """

    fail_count = 0

    while True:
        lat = state['lat']
        lon = state['lon']
        radius = RADIUS_DEG

        aircraft = await fetch_aircraft(lat, lon, radius)

        if aircraft or fail_count == 0:
            fail_count = 0

            state['aircraft'] = aircraft

            await broadcast({
                'type': 'aircraft_update',
                'data': aircraft,
                'interval': POLL_INTERVAL
                })
        else:
            fail_count += 1
            backoff = min(fail_count * 10, 60)
            print(f"[DEBUG] Failed to fetch aircraft data. Consecutive failures: {fail_count}")
            print(f"[DEBUG] Backing off for {backoff} seconds before retrying.")
            await asyncio.sleep(backoff)

        await asyncio.sleep(POLL_INTERVAL)

###############
# APP STARTUP #
###############

@asynccontextmanager
async def lifespan(app: FastAPI):
    state['aerodromes'] = await get_aerodromes(state['lat'], state['lon'], RADIUS_DEG, AIRPORT_TYPES)
    asyncio.create_task(poll_aircraft())
    yield

app = FastAPI(lifespan=lifespan)
app.mount('/static', StaticFiles(directory='static'), name='static')

##########
# ROUTES #
##########