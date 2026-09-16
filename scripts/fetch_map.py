import requests
import json

url = "https://github.com/simonepri/geo-maps/releases/download/v0.6.0/countries-coastline-10km.geo.json"
print(f"Downloading from {url}...")
r = requests.get(url)
r.raise_for_status()

print("Parsing JSON...")
data = r.json()

target_countries = ['GBR', 'IRL']
filtered_features = []

for feature in data['features']:
    # Check ID or properties for ISO code
    iso_a3 = feature.get('properties', {}).get('A3') or feature.get('id')
    if iso_a3 in target_countries:
        print(f"Found {iso_a3}")
        filtered_features.append(feature)

output = {
    "type": "FeatureCollection",
    "features": filtered_features
}

with open("uk.json", "w") as f:
    json.dump(output, f)

print(f"Saved {len(filtered_features)} features to uk.json")
