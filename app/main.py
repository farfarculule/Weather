from fastapi import FastAPI, Request, Response
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import httpx
import sqlite3
from datetime import datetime
import logging
from urllib.parse import quote, unquote
import os

# Настройка логирования для отладки
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Логируем запуск приложения и содержимое текущей директории
logger.debug(f"Starting application... Current directory: {os.getcwd()}")
logger.debug(f"Contents of /app: {os.listdir('/app')}")

# Создаём приложение FastAPI
app = FastAPI()

# Указываем абсолютный путь к шаблонам
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# Подключаем папку static для статических файлов (изображения и т.д.)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

# Логируем содержимое папки static для отладки
logger.debug(f"Contents of /app/static: {os.listdir(os.path.join(BASE_DIR, 'static'))}")
logger.debug(f"Contents of /app/static/images: {os.listdir(os.path.join(BASE_DIR, 'static', 'images'))}")

# Регистрируем фильтры для шаблонов
# Форматирование даты из базы данных
templates.env.filters['format_date'] = lambda date_str: datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S').strftime(
    '%d.%m %H:%M')
# Форматирование времени из Open-Meteo
templates.env.filters['format_datetime'] = lambda date_str: datetime.strptime(date_str, '%Y-%m-%dT%H:%M').strftime(
    '%d.%m %H:%M')
# Преобразование строки в datetime
templates.env.filters['strptime'] = lambda date_str, fmt: datetime.strptime(date_str, fmt)
# Получение timestamp
templates.env.filters['timestamp'] = lambda dt: dt.timestamp()

# Словарь для преобразования weather_code в текстовое описание
WEATHER_CODES = {
    0: "Солнечно",
    1: "Солнечно",
    2: "Облачно",
    3: "Облачно",
    45: "Облачно",
    48: "Облачно",
    51: "Дождь",
    53: "Дождь",
    55: "Дождь",
    61: "Дождь",
    63: "Дождь",
    65: "Дождь",
    71: "Снег",
    73: "Снег",
    75: "Снег",
    80: "Дождь",
    81: "Дождь",
    82: "Дождь",
    95: "Гроза",
    96: "Гроза",
    99: "Гроза"
}

# Регистрируем фильтр для преобразования weather_code в описание
templates.env.filters['weather_description'] = lambda code: WEATHER_CODES.get(code, "Неизвестно")

# Указываем путь к базе данных
DB_DIR = os.path.join(BASE_DIR, "db")
os.makedirs(DB_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, "history.db")


# Инициализация базы данных SQLite
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS search_history
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  city TEXT NOT NULL,
                  timestamp TEXT NOT NULL)''')
    conn.commit()
    conn.close()


init_db()


# Функция для добавления города в историю поиска
def add_to_history(city: str):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c.execute("INSERT INTO search_history (city, timestamp) VALUES (?, ?)", (city, timestamp))
        conn.commit()
        conn.close()
        logger.debug(f"Successfully added {city} to search history")
    except Exception as e:
        logger.error(f"Failed to add {city} to search history: {str(e)}")
        raise


# Функция для получения последних 5 записей из истории поиска
def get_history():
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT city, timestamp FROM search_history ORDER BY timestamp DESC LIMIT 5")
        history = c.fetchall()
        conn.close()
        logger.debug(f"History fetched: {history}")
        return history
    except Exception as e:
        logger.error(f"Failed to fetch history: {str(e)}")
        return []


# Функция для получения статистики по городам
def get_stats():
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT city, COUNT(*) as count FROM search_history GROUP BY city ORDER BY count DESC LIMIT 10")
        stats = c.fetchall()
        conn.close()
        logger.debug(f"Statistics fetched: {stats}")
        return stats
    except Exception as e:
        logger.error(f"Failed to fetch statistics: {str(e)}")
        return []


# Главный маршрут для отображения прогноза погоды
@app.get("/", response_class=HTMLResponse)
async def index(request: Request, city: str = None):
    logger.debug("Accessed / route with city: %s", city)
    weather_data = None
    error = None
    weather_condition = "main"
    last_city = unquote(request.cookies.get("last_city", ""))
    history = get_history()
    current_time = datetime.now().strftime("%Y-%m-%dT%H:%M")

    if city:
        # Если в строке есть запятые, берём только первую часть (основное название города) для запроса
        city_for_query = city.split(",")[0].strip() if "," in city else city
        logger.debug(f"Original city: {city}, City for query: {city_for_query}")
        async with httpx.AsyncClient() as client:
            try:
                # Поиск координат города через Nominatim
                geo_url = f"https://nominatim.openstreetmap.org/search?q={quote(city_for_query)}&format=json&addressdetails=1&accept-language=ru&limit=1"
                logger.debug(f"Sending request to Nominatim: {geo_url}")
                geo_response = await client.get(
                    geo_url,
                    headers={"User-Agent": "WeatherApp/1.0"}
                )
                geo_response.raise_for_status()
                geo_data = geo_response.json()
                logger.debug(f"Geo response for {city_for_query}: {geo_data}")
                if not geo_data:
                    error = f"Город {city_for_query} не найден"
                else:
                    latitude = float(geo_data[0]["lat"])
                    longitude = float(geo_data[0]["lon"])
                    logger.debug(f"Coordinates for {city_for_query}: lat={latitude}, lon={longitude}")
                    # Запрашиваем погоду
                    weather_url = (
                        f"https://api.open-meteo.com/v1/forecast?"
                        f"latitude={latitude}&longitude={longitude}&"
                        f"current_weather=true&"
                        f"hourly=temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code&"
                        f"temperature_unit=celsius&windspeed_unit=kmh&precipitation_unit=mm"
                    )
                    logger.debug(f"Sending request to Open-Meteo: {weather_url}")
                    weather_response = await client.get(weather_url)
                    weather_response.raise_for_status()
                    weather_data = weather_response.json()
                    logger.debug(f"Weather response for {city_for_query}: {weather_data}")
                    if "current_weather" in weather_data:
                        weather_code = weather_data["current_weather"]["weathercode"]
                        weather_description = WEATHER_CODES.get(weather_code, "Неизвестно").lower()
                        logger.debug(
                            f"Weather code: {weather_code}, Raw Description: {WEATHER_CODES.get(weather_code, 'Not in dict')}, Final Description: {weather_description}")
                        weather_condition_map = {
                            "солнечно": "sun",
                            "облачно": "cloud",
                            "дождь": "rain",
                            "снег": "snow",
                            "гроза": "storm",
                            "неизвестно": "main"
                        }
                        weather_condition = weather_condition_map.get(weather_description, "main")
                        logger.debug(f"Set weather_condition to: {weather_condition}")
                        # Добавляем город в историю поиска
                        add_to_history(city)
                    else:
                        logger.debug("No current_weather in weather_data")
            except httpx.HTTPStatusError as e:
                error = f"HTTP ошибка при запросе к API: {str(e)}"
                logger.error(f"HTTP error: {str(e)}")
            except httpx.RequestError as e:
                error = f"Ошибка при запросе к API: {str(e)}"
                logger.error(f"API request failed: {str(e)}")
            except Exception as e:
                error = f"Произошла ошибка: {str(e)}"
                logger.error(f"Unexpected error: {str(e)}")

    if not city or not weather_data or error:
        weather_condition = "main"
        logger.debug("No city or error, setting weather_condition to 'main'")

    logger.debug(f"Rendering template with weather_condition: {weather_condition}")
    response = templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "weather": weather_data,
            "city": city or last_city,
            "error": error,
            "history": history,
            "current_time": current_time,
            "weather_condition": weather_condition
        }
    )
    if city:
        response.set_cookie(key="last_city", value=quote(city))
    return response


# Маршрут для автодополнения городов
@app.get("/autocomplete", response_class=HTMLResponse)
async def autocomplete(request: Request, city: str):
    logger.debug(f"Accessed /autocomplete with city: {city}")
    if not city:
        return "<div id='suggestions'></div>"

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                f"https://nominatim.openstreetmap.org/search?q={quote(city)}&format=json&addressdetails=1&accept-language=ru&limit=5",
                headers={"User-Agent": "WeatherApp/1.0"}
            )
            response.raise_for_status()
            data = response.json()
            logger.debug(f"Autocomplete response for {city}: {data}")
            cities = []
            if data:
                for result in data[:5]:

                    full_name = result["display_name"]
                    cities.append(full_name)
        except httpx.RequestError as e:
            logger.error(f"Autocomplete API request failed: {str(e)}")
            cities = []

    suggestions = "".join(
        [f"<div hx-get='/?city={quote(city)}' hx-swap='innerHTML' hx-target='#content'>{city}</div>" for city in
         cities])
    return f"<div id='suggestions'>{suggestions}</div>"
@app.get("/stats", response_class=HTMLResponse)
async def stats(request: Request):
    logger.debug("Accessed /stats route")
    stats = get_stats()
    return templates.TemplateResponse(
        "stats.html",
        {"request": request, "stats": stats}
    )
