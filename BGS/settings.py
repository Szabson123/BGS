from pathlib import Path
from dotenv import load_dotenv
import os

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(os.path.join(BASE_DIR, '.env'))


SECRET_KEY = os.getenv('SECRET_KEY')
DEBUG = os.getenv('DEBUG')

AUTH_USER_MODEL = 'user.CustomUser'

ALLOWED_HOSTS = []

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    'drf_spectacular',
    'rest_framework',
    'corsheaders',
    'django_filters',

    'user',
    'ur',
    'operatorknowlage',
    'doc',
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'querycount.middleware.QueryCountMiddleware',
]

CORS_ALLOW_CREDENTIALS = True

CORS_ALLOWED_ORIGINS = [
    'http://localhost:5173',
]

CSRF_TRUSTED_ORIGINS = [
    "http://localhost:5173",
]

ROOT_URLCONF = 'BGS.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'BGS.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('DB_NAME'),
        'USER': os.getenv('DB_USER'),
        'PASSWORD': os.getenv('DB_PASSWORD'),
        'HOST': os.getenv('DB_HOST'),
        'PORT': os.getenv('DB_PORT'),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True

STATIC_URL = 'static/'

MEDIA_URL = '/media/'

MEDIA_ROOT = os.path.join(BASE_DIR, 'media')


REST_FRAMEWORK = {
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'DEFAULT_FILTER_BACKENDS': ['django_filters.rest_framework.DjangoFilterBackend']
}

SPECTACULAR_SETTINGS = {
    'TITLE': 'BGS',
    'DESCRIPTION': 'BGS docs',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
}


# --- BGS::DOC – konwersja arkuszy do PDF (mikroserwis BGS-converter) ---
# Pusty URL = konwersja wyłączona (wersja dostaje status błędu z informacją o braku konfiguracji)
DOC_CONVERTER_URL = os.getenv('DOC_CONVERTER_URL', '').rstrip('/')
DOC_CONVERTER_API_KEY = os.getenv('DOC_CONVERTER_API_KEY', '')
# Adres Django widziany z konwertera – tam wraca wynik
DOC_CONVERTER_CALLBACK_BASE = os.getenv('DOC_CONVERTER_CALLBACK_BASE', 'http://127.0.0.1:8000').rstrip('/')
# Po tylu minutach zlecenie „w toku” uznajemy za zgubione i można je ponowić
DOC_CONVERTER_STALE_MINUTES = int(os.getenv('DOC_CONVERTER_STALE_MINUTES', '15'))

# --- UR::Cycle Guard – stanowisko uznajemy za offline, gdy master nie raportował przez tyle sekund ---
CYCLE_GUARD_OFFLINE_SECONDS = int(os.getenv('CYCLE_GUARD_OFFLINE_SECONDS', '30'))
# Inny komputer z tym samym kluczem w tym oknie = klucz działa na dwóch komputerach naraz (a nie wymiana komputera)
CYCLE_GUARD_KEY_CONFLICT_SECONDS = int(os.getenv('CYCLE_GUARD_KEY_CONFLICT_SECONDS', '120'))
# Klucz serwisu BGS-ur-eclipse-sync (NumQA z Eclipse zeruje liczniki kanałów); pusty = endpointy wyłączone
CYCLE_GUARD_ECLIPSE_API_KEY = os.getenv('CYCLE_GUARD_ECLIPSE_API_KEY', '')
