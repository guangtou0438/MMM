"""Open-Meteo daily summaries with bounded HTTP calls and persistent caching."""
import hashlib
import json
import httpx
from ..agent.store import encode, timestamp


class WeatherProvider:
    def __init__(self, store):
        self.store = store

    def forecast(self, location, day):
        key = encode([location.casefold().strip(), str(day)])
        cached = self.store.rows('SELECT * FROM weather_cache WHERE key=?', (key,))
        if cached and timestamp()-cached[0]['queried'] < 3600:
            return json.loads(cached[0]['summary'])
        with httpx.Client(timeout=10) as client:
            geo = client.get('https://geocoding-api.open-meteo.com/v1/search', params={'name': location, 'count': 3, 'language': 'en'})
            geo.raise_for_status()
            places = geo.json().get('results', [])
            if len(places) != 1:
                raise ValueError('地点不存在或有多个匹配，请补充州或国家')
            place = places[0]
            response = client.get('https://api.open-meteo.com/v1/forecast', params={
                'latitude': place['latitude'], 'longitude': place['longitude'], 'timezone': place['timezone'],
                'start_date': str(day), 'end_date': str(day),
                'daily': 'precipitation_probability_max,temperature_2m_max,temperature_2m_min,wind_speed_10m_max'})
            response.raise_for_status()
            daily = response.json()['daily']
        summary = {'location': location, 'date': str(day), 'provider': 'Open-Meteo', 'queried': timestamp(),
                   'rain': daily['precipitation_probability_max'][0], 'high': daily['temperature_2m_max'][0],
                   'low': daily['temperature_2m_min'][0], 'wind': daily['wind_speed_10m_max'][0]}
        if any(summary[k] is None for k in ('rain', 'high', 'low', 'wind')):
            raise ValueError('天气预报数据不完整')
        summary['response_hash'] = hashlib.sha256(response.content).hexdigest()
        with self.store.connection() as db:
            db.execute('INSERT OR REPLACE INTO weather_cache VALUES(?,?,?,?)', (key, encode(summary), timestamp(), summary['response_hash']))
        return summary
