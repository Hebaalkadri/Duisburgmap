from flask import Flask, render_template, request, jsonify, send_from_directory
import requests
import re
import pandas as pd
import os
import json
from datetime import datetime, timedelta

app = Flask(__name__)

# Define the GeoJSON URL with the street data (base URL without resultOffset)
GEOJSON_URL = 'https://geoportal2.duisburg.de/arcgisserver/rest/services/Masterportal/WFS_Fachdaten_MP/MapServer/67/query'

# Dictionary to map Wohnlage descriptions to numeric ratings
WOHNLAGE_RATINGS = {
    'sehr gut': 1,
    'gut': 2,
    'mittel': 3,
    'einfach': 4
}

# Define color mapping for wohnlage ratings
WOHNLAGE_COLORS = {
    'sehr gut': '#C500FF',
    'gut': '#FF0000',
    'mittel': '#E69800',
    'einfach': '#9C9C9C'
}

# Cache file paths
DATA_DIR = 'data'
GEOJSON_CACHE_FILE = os.path.join(DATA_DIR, 'duisburg_wohnlagen.json')
CSV_CACHE_FILE = os.path.join(DATA_DIR, 'duisburg_wohnlagen.csv')
CACHE_DURATION = timedelta(days=7)  # Cache data for 7 days

# Create data directory if it doesn't exist
os.makedirs(DATA_DIR, exist_ok=True)

def is_cache_valid(file_path):
    """Check if the cache file exists and is recent enough"""
    if not os.path.exists(file_path):
        return False
    
    file_time = datetime.fromtimestamp(os.path.getmtime(file_path))
    return datetime.now() - file_time < CACHE_DURATION

def fetch_duisburg_data():
    """
    Fetches data from Duisburg API using the approach from the original notebook.
    Returns a list of dictionaries with address information.
    """
    print("Starting to fetch data from Duisburg GeoPortal API...")
    
    # Das wird eine Liste mit Dictionaries. Jedes Dictionary wird die Angaben für eine Adresse enthalten.
    data = []

    # Das sind die URL-Parameter, sie sind an eine SQL-Syntax angelehnt
    payload = {
        'where': '1=1',
        'outFields': '*',
        'returnGeometry': True,
        'returnIdsOnly': False,
        'returnCountOnly': False,
        'resultRecordCount': 1000,
        'f': 'geojson'
    }

    # Den URL-Parameter resultOffset passen wir für jeden API-Request an, wir starten bei 0
    result_offset = 0

    # Wir loopen so lange, bis die API uns keine Daten mehr liefert
    while True:
        # Wir setzen den URL-Parameter resultOffset für unseren nächsten Request fest
        payload['resultOffset'] = result_offset
        
        print(f"Fetching batch with offset {result_offset}...")

        # Wir setzen unseren Request ab und nehmen die Daten entgegen
        try:
            response = requests.get(GEOJSON_URL, params=payload, timeout=60)
            raw = response.json()
        except Exception as e:
            print(f"Error fetching data at offset {result_offset}: {e}")
            break

        # Sobald die API uns keine Daten mehr liefert, beenden wir die Schleife
        features = raw.get('features', [])
        print(f"Received {len(features)} features from offset {result_offset}")
        
        if len(features) == 0:
            print(f"End of data reached at offset {result_offset}")
            break

        # Für jede Adresse nutzen wir die properties-Variablen sowie die Geokoordinaten
        for feature in features:
            properties = feature['properties']
            
            # Extract coordinates based on geometry type
            try:
                if feature['geometry']['type'] == 'Point':
                    properties['LON'] = feature['geometry']['coordinates'][0]
                    properties['LAT'] = feature['geometry']['coordinates'][1]
                else:
                    # Handle other geometry types if needed
                    properties['LON'] = feature['geometry']['coordinates'][0]
                    properties['LAT'] = feature['geometry']['coordinates'][1]
            except (IndexError, KeyError) as e:
                print(f"Error extracting coordinates: {e}")
                continue
                
            # Wir fügen das Dictionary mit den Angaben für eine Adresse zu unserer Liste data hinzu
            data.append(properties)
        
        # Increment offset for next batch
        result_offset += 1000
    
    return data, raw.get('features', [])

def load_cached_data():
    """Load data from cache if available, otherwise fetch from API"""
    if is_cache_valid(GEOJSON_CACHE_FILE):
        try:
            with open(GEOJSON_CACHE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                feature_count = len(data.get('features', []))
                print(f"Loaded {feature_count} features from cache")
                
                # Check if the data seems complete (at least 80,000 features)
                if feature_count >= 80000:
                    return data
                else:
                    print(f"Cache contains only {feature_count} features, refetching")
        except Exception as e:
            print(f"Error reading cache file: {e}")
    
    # If cache is invalid, incomplete, or reading failed, fetch from API
    return fetch_and_cache_data()

def fetch_and_cache_data():
    """Fetch data from API and cache it using the successful approach from the Jupyter notebook"""
    try:
        print("Starting to fetch all address data from API (this may take a while)...")
        
        # Use the merged function to fetch data
        data_list, features = fetch_duisburg_data()
        
        print(f"Fetching complete. Total features: {len(features)}")
        
        # Create GeoJSON structure
        complete_geojson = {
            "type": "FeatureCollection",
            "features": features
        }
        
        # Cache the GeoJSON data
        with open(GEOJSON_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(complete_geojson, f, ensure_ascii=False)
        
        # Die Liste mit Dictionaries wandeln wir in einen Pandas-DataFrame um
        df = pd.DataFrame(data_list)
        
        # Add rating and color columns
        df['RATING'] = df['WOHNLAGE'].map(WOHNLAGE_RATINGS).fillna(0)
        df['COLOR'] = df['WOHNLAGE'].map(WOHNLAGE_COLORS).fillna('#CCCCCC')
        
        # Cache the CSV data
        df.to_csv(CSV_CACHE_FILE, index=False, encoding='utf-8')
        
        print(f"Data saved. DataFrame shape: {df.shape}")
        
        return complete_geojson
    
    except Exception as e:
        print(f"Error in fetch_and_cache_data: {e}")
        # If we have an older cache, use it even if it's expired
        if os.path.exists(GEOJSON_CACHE_FILE):
            print("Using existing cache file instead")
            with open(GEOJSON_CACHE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        # Otherwise return an empty GeoJSON structure
        return {"type": "FeatureCollection", "features": []}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/static/<path:path>')
def send_static(path):
    return send_from_directory('static', path)

@app.route('/geojson')
def get_geojson():
    """Get GeoJSON data from cache or API"""
    try:
        data = load_cached_data()
        # Add a count to the response
        count = len(data.get('features', []))
        return jsonify({"type": "FeatureCollection", "features": data.get('features', []), "count": count})
    except Exception as e:
        return jsonify({'error': f'Failed to fetch data: {str(e)}'}), 500

@app.route('/stats')
def get_stats():
    """Get statistics about the loaded data"""
    try:
        # Check if we have a valid cache
        if os.path.exists(GEOJSON_CACHE_FILE):
            with open(GEOJSON_CACHE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                feature_count = len(data.get('features', []))
                
                # If we have CSV data, get more detailed stats
                if os.path.exists(CSV_CACHE_FILE):
                    df = pd.read_csv(CSV_CACHE_FILE, encoding='utf-8')
                    wohnlage_counts = df['WOHNLAGE'].value_counts().to_dict() if 'WOHNLAGE' in df.columns else {}
                    ortsteil_counts = df['ORTSTEILTX'].value_counts().to_dict() if 'ORTSTEILTX' in df.columns else {}
                    
                    return jsonify({
                        'total_features': feature_count,
                        'csv_rows': len(df),
                        'wohnlage_counts': wohnlage_counts,
                        'ortsteil_counts': ortsteil_counts,
                        'cache_date': datetime.fromtimestamp(os.path.getmtime(GEOJSON_CACHE_FILE)).isoformat()
                    })
                
                return jsonify({
                    'total_features': feature_count,
                    'cache_date': datetime.fromtimestamp(os.path.getmtime(GEOJSON_CACHE_FILE)).isoformat()
                })
        
        return jsonify({
            'error': 'No data available yet',
            'cache_exists': os.path.exists(GEOJSON_CACHE_FILE),
            'csv_exists': os.path.exists(CSV_CACHE_FILE)
        })
    except Exception as e:
        return jsonify({'error': f'Error getting stats: {str(e)}'}), 500

@app.route('/evaluate', methods=['POST'])
def evaluate():
    """Suche nach einem Straßennamen und gib maximal 10 Treffer zurück"""
    try:
        street_name = request.form.get('street_name', '')
        
        if not street_name:
            return jsonify({
                'matches': [],
                'count': 0,
                'message': 'Bitte geben Sie einen Straßennamen ein'
            })
        
        # Überprüfen, ob wir zwischengespeicherte CSV-Daten haben
        if os.path.exists(CSV_CACHE_FILE):
            try:
                df = pd.read_csv(CSV_CACHE_FILE, encoding='utf-8')
                
                # Ausgefeiltere Straßennamensuche
                df['address_lower'] = df['ADRESSE'].str.lower()
                street_name_lower = street_name.lower()
                
                # Extrahiere nur die Straßennamen ohne Hausnummern
                def extract_street_name(address):
                    if isinstance(address, str):
                        parts = address.split(',')[0]  # Entferne Postleitzahl und Stadt
                        return re.sub(r'\s+\d+.*$', '', parts)  # Entferne Hausnummern
                    return ''
                
                df['street_name'] = df['ADRESSE'].apply(extract_street_name)
                df['street_name_lower'] = df['street_name'].str.lower()
                
                # Finde Übereinstimmungen
                exact_matches = df[df['street_name_lower'] == street_name_lower]
                contains_matches = df[df['street_name_lower'].str.contains(street_name_lower, na=False)]
                
                # Kombiniere und entferne Duplikate
                matches_df = pd.concat([exact_matches, contains_matches]).drop_duplicates()
                
                if not matches_df.empty:
                    # Sortiere zuerst nach exakter Übereinstimmung, dann nach Adresse
                    matches_df['exact_match'] = matches_df['street_name_lower'] == street_name_lower
                    matches_df = matches_df.sort_values(['exact_match', 'ADRESSE'], ascending=[False, True])
                    
                    # Begrenze auf 10 Ergebnisse
                    matches_df = matches_df.head(10)
                    
                    matches = []
                    for _, row in matches_df.iterrows():
                        try:
                            # Hole Bewertung und Farbe, entweder aus Spalten oder Mappings
                            if 'RATING' in row:
                                rating = int(row['RATING']) if not pd.isna(row['RATING']) else 0
                            else:
                                rating = WOHNLAGE_RATINGS.get(row['WOHNLAGE'], 0)
                                
                            if 'COLOR' in row:
                                color = row['COLOR']
                            else:
                                color = WOHNLAGE_COLORS.get(row['WOHNLAGE'], '#CCCCCC')
                            
                            matches.append({
                                'address': row['ADRESSE'],
                                'wohnlage': row['WOHNLAGE'],
                                'rating': rating,
                                'color': color,
                                'ortsteil': row['ORTSTEILTX'] if 'ORTSTEILTX' in row else '',
                                'coordinates': [float(row['LON']), float(row['LAT'])]
                            })
                        except (ValueError, TypeError) as e:
                            print(f"Fehler bei der Verarbeitung der Zeile: {e}")
                            continue
                    
                    return jsonify({
                        'matches': matches,
                        'count': len(matches),
                        'limited': True,
                        'total_found': len(contains_matches) + len(exact_matches) - len(matches)
                    })
            except Exception as e:
                print(f"Fehler bei der Verarbeitung der CSV-Daten: {e}")
                # Fallback auf GeoJSON, wenn CSV-Verarbeitung fehlschlägt
        
        # Wenn kein CSV, keine Übereinstimmungen oder CSV-Verarbeitung fehlgeschlagen, hole aus der API
        data = load_cached_data()
        matches = []
        
        for feature in data['features']:
            props = feature['properties']
            address = props.get('ADRESSE', '')
            if not address:
                continue
                
            # Extrahiere nur den Straßennamen ohne Hausnummern
            street_pattern = re.compile(r'^(.*?)(?:\s+\d+.*)?$')
            match = street_pattern.match(address)
            if not match:
                continue
                
            address_street = match.group(1).lower()
            
            if street_name.lower() in address_street or address_street in street_name.lower():
                wohnlage = props.get('WOHNLAGE', 'Unbekannt')
                numeric_rating = WOHNLAGE_RATINGS.get(wohnlage, 0)
                color = WOHNLAGE_COLORS.get(wohnlage, '#CCCCCC')
                
                try:
                    # Koordinaten basierend auf Geometrietyp ermitteln
                    if feature['geometry']['type'] == 'MultiPolygon':
                        coordinates = feature['geometry']['coordinates'][0][0][0]
                    elif feature['geometry']['type'] == 'Polygon':
                        coordinates = feature['geometry']['coordinates'][0][0]
                    else:  # Point oder andere Geometrie
                        coordinates = feature['geometry']['coordinates']
                    
                    matches.append({
                        'address': address,
                        'wohnlage': wohnlage,
                        'rating': numeric_rating,
                        'color': color,
                        'ortsteil': props.get('ORTSTEILTX', ''),
                        'coordinates': coordinates
                    })
                except (IndexError, TypeError) as e:
                    print(f"Fehler beim Abrufen der Koordinaten für {address}: {e}")
                    continue
        
        if matches:
            # Sortiere Ergebnisse nach Relevanz (exakte Übereinstimmung zuerst)
            matches.sort(key=lambda x: 1 if street_name.lower() == x['address'].lower() else 2)
            
            # Speichere die Gesamtanzahl der Ergebnisse vor dem Begrenzen
            total_found = len(matches)
            
            # Begrenze auf 10 Ergebnisse
            matches = matches[:10]
            
            return jsonify({
                'matches': matches,
                'count': len(matches),
                'limited': total_found > 10,
                'total_found': total_found
            })
        else:
            return jsonify({
                'matches': [],
                'count': 0,
                'message': 'Straße nicht in der Datenbank gefunden'
            })
            
    except Exception as e:
        return jsonify({
            'matches': [],
            'count': 0,
            'message': f'Fehler: {str(e)}'
        }), 500

@app.route('/districts', methods=['GET'])
def get_districts():
    """Get a list of all districts (Ortsteile) in Duisburg"""
    try:
        if os.path.exists(CSV_CACHE_FILE):
            df = pd.read_csv(CSV_CACHE_FILE, encoding='utf-8')
            districts = df['ORTSTEILTX'].dropna().unique().tolist()
            districts.sort()
            return jsonify({
                'districts': districts,
                'count': len(districts)
            })
        else:
            # If no CSV, use API data
            data = load_cached_data()
            districts = set()
            
            for feature in data['features']:
                district = feature['properties'].get('ORTSTEILTX', '')
                if district:
                    districts.add(district)
            
            districts_list = sorted(list(districts))
            return jsonify({
                'districts': districts_list,
                'count': len(districts_list)
            })
    
    except Exception as e:
        return jsonify({
            'districts': [],
            'count': 0,
            'message': f'Error: {str(e)}'
        }), 500

@app.route('/stats.html')
def stats_page():
    """Render the statistics page"""
    return render_template('stats.html')

# Route to export CSV files (similar to what your original notebook did)
@app.route('/export')
def export_data():
    """Export data to CSV files (complete and compact versions)"""
    try:
        if not os.path.exists(CSV_CACHE_FILE):
            # If no CSV file exists, fetch data
            fetch_and_cache_data()
            
        # Read the full CSV
        df = pd.read_csv(CSV_CACHE_FILE, encoding='utf-8')
        
        # Create complete version
        export_path_complete = os.path.join(DATA_DIR, 'duisburg_wohnlagen_complete.csv')
        df.to_csv(export_path_complete, index=False)
        
        # Create compact version with selected columns
        columns_of_interest = ['ADRESSE', 'WOHNLAGE', 'LON', 'LAT']
        export_path_compact = os.path.join(DATA_DIR, 'duisburg_wohnlagen_compact.csv')
        df[columns_of_interest].to_csv(export_path_compact, index=False)
        
        return jsonify({
            'success': True,
            'message': 'Data exported successfully',
            'files': {
                'complete': export_path_complete,
                'compact': export_path_compact
            }
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Error exporting data: {str(e)}'
        }), 500

if __name__ == '__main__':
    app.run(debug=True)