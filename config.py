import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    MYSQL_HOST = os.getenv('MYSQL_HOST', 'localhost')
    MYSQL_USER = os.getenv('MYSQL_USER', 'root')
    MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD', 'Ljh488606319!')
    MYSQL_DATABASE = os.getenv('MYSQL_DATABASE', 'rehab_platform')
    SECRET_KEY = os.getenv('SECRET_KEY', 'your-secret-key')