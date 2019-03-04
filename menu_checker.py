import datetime
import json
import os
import urllib.parse
import urllib.request

from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse

app = Flask(__name__)

CLIENT_ID = os.environ.get('FOURSQUARE_CLIENT_ID')
CLIENT_SECRET = os.environ.get('FOURSQUARE_CLIENT_SECRET')
VENUE_BASE_URL = os.environ.get('FOURSQUARE_VENUE_BASE_URL')
ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID')
AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN')
TWILIO_MAXIMUM_MESSAGE_SIZE = 1600


class Restaurant:
    def __init__(self, restaurant_id: str, restaurant_name: str, restaurant_address: str):
        self.id = restaurant_id
        self.name = restaurant_name
        self.address = restaurant_address

    def __str__(self):
        return '{}\n{}'.format(self.name, self.address)


class Dish:
    def __init__(self, dish_name: str, dish_info: str, dish_note='none'):
        self.name = dish_name
        self.info = dish_info
        self.note = dish_note

    def __str__(self):
        if self.info != '':
            return '{}: {}'.format(self.name, self.info)
        return self.name

    def set_note(self, new_note):
        self.note = new_note


class Menu:
    def __init__(self, restaurant: str):
        self.restaurant = restaurant
        self.dishes = []

    def __str__(self):
        result = '*{}*\n'.format(self.restaurant)
        for dish in self.dishes:
            result += '- ' + str(dish) + '\n'
        return result

    def simplified_menu(self):
        return '{}: '.format(self.restaurant) + '; '.join([dish.name for dish in self.dishes])

    def add_dish(self, dish: Dish):
        self.dishes.append(dish)


def build_url(url: str, query_parameters: [(str, str)]) -> str:
    return url + urllib.parse.urlencode(query_parameters)


def get_base_query_parameters() -> [(str, str)]:
    date = datetime.datetime.today().strftime('%Y%m%d')
    return [('client_id', CLIENT_ID), ('client_secret', CLIENT_SECRET), ('v', date)]


def call_api(url: str) -> dict:
    response = urllib.request.urlopen(url)
    data = response.read()
    json_text = data.decode('utf-8')
    response.close()
    return json.loads(json_text)


def build_restaurant_search_url(location: str, query: str) -> str:
    search_url = VENUE_BASE_URL + 'search?'
    query_parameters = get_base_query_parameters()
    query_parameters.append(('near', location))
    query_parameters.append(('query', query))
    return build_url(search_url, query_parameters)


def get_restaurants(result: dict) -> [Restaurant]:
    restaurants = []
    for venue in result['response']['venues']:
        restaurants.append(Restaurant(venue['id'], venue['name'], '\n'.join(venue['location']['formattedAddress'])))
    return restaurants


def build_menu_url(restaurant: Restaurant) -> str:
    menu_url = VENUE_BASE_URL + restaurant.id + '/menu?'
    query_parameters = get_base_query_parameters()
    return build_url(menu_url, query_parameters)


def get_menu_items(restaurant: Restaurant) -> Menu:
    menu_url = build_menu_url(restaurant)
    result = call_api(menu_url)
    flat_menu = Menu(restaurant.name)
    dish_set = set()
    if is_positive(result['response']['menu']['menus']['count']):
        menus = result['response']['menu']['menus']['items']
        for menu in menus:
            if is_positive(menu['entries']['count']):
                sections = menu['entries']['items']
                for section in sections:
                    if 'Drinks' in section['name'] or 'Beverages' in section['name']:
                        continue
                    if is_positive(section['entries']['count']):
                        dishes = section['entries']['items']
                        for dish in dishes:
                            info = '' if 'description' not in dish else dish['description']
                            if dish['name'] not in dish_set:
                                flat_menu.add_dish(Dish(dish['name'], info.lower().strip().rstrip('.')))
                                dish_set.add(dish['name'])
    return flat_menu


def is_positive(number: int) -> bool:
    return number > 0


def filter_menu_items(original_menu: Menu, safe_words: [str], danger_words: [str]) -> Menu:
    filtered_menu = Menu(original_menu.restaurant)
    for dish in original_menu.dishes:
        has_danger_word = False
        dish_name = normalize(dish.name)
        dish_info = normalize(dish.info)
        if any(word in dish_name for word in safe_words) or any(word in dish_info for word in safe_words):
            filtered_menu.add_dish(Dish(dish.name, format_dish(dish_info), 'has safe word'))
        else:
            for word in danger_words:
                if word in dish_name or word in dish_info:
                    has_danger_word = True
                    break
            if not has_danger_word:
                filtered_menu.add_dish(Dish(dish.name, format_dish(dish_info), 'has no danger words'))
    return filtered_menu


def format_dish(info: str) -> str:
    return ' '.join(info.split()).replace('.', ';').rstrip(';')


def normalize(text: str) -> str:
    return text.lower().strip()


def check_menu(query: str, location: str) -> str:
    search_url = build_restaurant_search_url(location, query)
    try:
        result = call_api(search_url)
        restaurants = get_restaurants(result)
        if len(restaurants) == 0:
            return 'No Results Found: We could not find any restaurants with the text you sent. Please try again.'
    except urllib.error.HTTPError:
        return 'We could not find the restaurant you are looking for. Please try again later.'
    try:
        menu = get_menu_items(restaurants[0])
    except urllib.error.HTTPError:
        return 'Unfortunately, we could not create a filtered menu for {}. ' \
               'Try checking the online menu for more information. {}'.\
            format(restaurants[0].name, fetch_menu_url(restaurants[0].name))
    if len(menu.dishes) == 0:
        return 'Unfortunately, {} does not have a menu. Visit the restaurant for a full menu!\n{}'.\
            format(restaurants[0].name, restaurants[0].address)
    with open('meat_words.txt', 'r') as f:
        vegetarian_words = ['vegan', 'vegetarian']
        meat_words = [word.strip() for word in f.readlines()]
        result = filter_menu_items(menu, vegetarian_words, meat_words)
        if is_too_large(str(result)):
            return result.simplified_menu()
        return str(result)


def fetch_menu_url(restaurant_name: str) -> str:
    modified_name = "-".join(restaurant_name.lower().split())
    return 'places.singleplatform.com/{}/menu'.format(modified_name)


def is_too_large(message: str) -> bool:
    return len(message) > TWILIO_MAXIMUM_MESSAGE_SIZE


@app.route('/', methods=['GET'])
def welcome():
    return 'Welcome to Your Personalized Menu Checker!'


@app.route('/sms', methods=['GET', 'POST'])
def sms_reply():
    body = request.values.get('Body', None)
    response = MessagingResponse()
    if ' @ ' in body:
        parts = body.split(' @ ')
        query, location = parts[:-1], parts[-1]
    else:
        query = body
        location = '{}, {}'.format(request.values.get('FromCity', 'Irvine'), request.values.get('FromState', 'CA'))
    result = check_menu(query, location)
    if is_too_large(result):
        result = result[:(TWILIO_MAXIMUM_MESSAGE_SIZE-100)].strip() + ' ...'
    response.message(result)
    return str(response)


if __name__ == '__main__':
   port = int(os.environ.get("PORT", 8080))
   app.run(debug=True, host='0.0.0.0', port=port)

