#!/bin/bash
# Startup command pour Azure App Service (Python).
# Dans le portail Azure : App Service > Configuration > Startup Command
# bash startup.sh
gunicorn -w 2 -k uvicorn.workers.UvicornWorker app.main:app --bind=0.0.0.0:8000 --timeout 120
