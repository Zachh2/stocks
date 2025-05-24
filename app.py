from flask import Flask, jsonify
from flask_restx import Api, Resource, Namespace
import cloudscraper
from bs4 import BeautifulSoup
import re
import logging
import time
from fake_useragent import UserAgent
from cachetools import TTLCache

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
api = Api(app, version='1.3', title='Grow A Garden Stock API',
          description='API to scrape stock data from VulcanValues Grow A Garden page')
ns = api.namespace('stocks', description='Stock operations')

# Cache setup
cache = TTLCache(maxsize=100, ttl=300)

def scrape_stock_data():
    cache_key = f"stock_data_{int(time.time() // 300)}"
    if cache_key in cache:
        logger.info("Returning cached stock data")
        return cache[cache_key]

    url = f"https://vulcanvalues.com/grow-a-garden/stock?_={int(time.time())}"
    ua = UserAgent()
    headers = {
        'User-Agent': ua.random,
        'Accept': 'text/html',
        'Referer': 'https://vulcanvalues.com/'
    }

    scraper = cloudscraper.create_scraper()
    max_retries = 3
    retry_delay = 5

    for attempt in range(max_retries):
        try:
            logger.info(f"Attempt {attempt + 1}: Fetching {url}")
            time.sleep(2)
            response = scraper.get(url, headers=headers, timeout=15)
            response.raise_for_status()

            if 'text/html' not in response.headers.get('Content-Type', '').lower():
                return {
                    'error': 'Invalid response from server.',
                    'details': f"Non-HTML content received.",
                    'status': 502
                }

            if 'cf-browser-verification' in response.text.lower():
                return {
                    'error': 'Blocked by Cloudflare.',
                    'details': 'Cloudflare browser verification page detected.',
                    'status': 403
                }

            soup = BeautifulSoup(response.text, 'lxml')
            stock_data = {
                'gear_stock': {'items': [], 'updates_in': 'Unknown'},
                'egg_stock': {'items': [], 'updates_in': 'Unknown'},
                'seeds_stock': {'items': [], 'updates_in': 'Unknown'}
            }

            stock_grid = soup.find('div', class_=re.compile(r'grid.*grid-cols'))
            if not stock_grid:
                for div in soup.find_all('div'):
                    if div.find('h2', text=re.compile(r'GEAR|EGG|SEEDS', re.I)):
                        stock_grid = div
                        break
                if not stock_grid:
                    return {
                        'error': 'Stock grid not found.',
                        'details': 'Page structure may have changed.',
                        'status': 404
                    }

            for section in stock_grid.find_all('div', recursive=False):
                title_tag = section.find('h2')
                if not title_tag:
                    continue
                title = title_tag.text.strip().upper()

                countdown = 'Unknown'
                countdown_p = section.find('p', class_=re.compile(r'text-yellow'))
                if countdown_p:
                    countdown_span = countdown_p.find('span', id=re.compile(r'countdown-(gear|egg|seeds)'))
                    if countdown_span:
                        countdown = countdown_span.text.strip()

                items_list = section.find('ul', class_=re.compile(r'space-y-\d+'))
                if not items_list:
                    continue

                item_dict = {}
                for item in items_list.find_all('li', class_=re.compile(r'bg-gray')):
                    try:
                        name_span = item.find('span')
                        name = name_span.contents[0].strip() if name_span else 'Unknown'

                        qty_span = name_span.find('span', class_=re.compile(r'text-gray')) if name_span else None
                        qty_match = re.search(r'\d+', qty_span.text.strip()) if qty_span else None
                        quantity = int(qty_match.group()) if qty_match else 0

                        if name in item_dict:
                            item_dict[name]['quantity'] += quantity
                        else:
                            item_dict[name] = {'name': name, 'quantity': quantity}
                    except Exception as e:
                        logger.error(f"Failed parsing item: {e}")
                        continue

                stock_section = {
                    'items': list(item_dict.values()),
                    'updates_in': countdown
                }

                if 'GEAR' in title:
                    stock_data['gear_stock'] = stock_section
                elif 'EGG' in title:
                    stock_data['egg_stock'] = stock_section
                elif 'SEEDS' in title:
                    stock_data['seeds_stock'] = stock_section

            if not any(stock_data[s]['items'] for s in stock_data):
                return {
                    'error': 'No stock data found.',
                    'details': 'Empty stock sections.',
                    'status': 204
                }

            cache[cache_key] = stock_data
            return stock_data

        except Exception as e:
            logger.error(f"Error: {e}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                headers['User-Agent'] = ua.random
                continue
            return {
                'error': 'Failed after multiple attempts.',
                'details': str(e),
                'status': 500
            }

# Flask endpoints
@ns.route('/all')
class AllStocks(Resource):
    def get(self):
        data = scrape_stock_data()
        if 'error' in data:
            return data, data.get('status', 500)
        return jsonify(data)

@ns.route('/gear')
class GearStock(Resource):
    def get(self):
        data = scrape_stock_data()
        if 'error' in data:
            return data, data.get('status', 500)
        return jsonify(data.get('gear_stock', {'items': [], 'updates_in': 'Unknown'}))

@ns.route('/egg')
class EggStock(Resource):
    def get(self):
        data = scrape_stock_data()
        if 'error' in data:
            return data, data.get('status', 500)
        return jsonify(data.get('egg_stock', {'items': [], 'updates_in': 'Unknown'}))

@ns.route('/seeds')
class SeedsStock(Resource):
    def get(self):
        data = scrape_stock_data()
        if 'error' in data:
            return data, data.get('status', 500)
        return jsonify(data.get('seeds_stock', {'items': [], 'updates_in': 'Unknown'}))

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8080)
