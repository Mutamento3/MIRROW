"""Lightweight localhost preview; never imports MIRROW main or chat databases."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .api import router

app = FastAPI(title='MIRROW workshop isolated preview')
app.add_middleware(CORSMiddleware, allow_origins=['http://127.0.0.1:5175', 'http://localhost:5175'], allow_methods=['*'], allow_headers=['*'])
app.include_router(router)
