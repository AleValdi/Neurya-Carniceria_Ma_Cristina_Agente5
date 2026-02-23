@echo off
title Conciliacion Bancaria - Agente5
cd /d "%~dp0"
call venv\Scripts\activate.bat
streamlit run app.py --server.headless false --browser.gatherUsageStats false
pause
