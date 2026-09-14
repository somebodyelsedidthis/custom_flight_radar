###########
# IMPORTS #
###########

from opensky_api import OpenSkyApi, TokenManager
import geocoder
import csv
import json
import os
import time

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