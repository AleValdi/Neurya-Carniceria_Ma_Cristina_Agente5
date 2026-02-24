@echo off
title Conciliacion Bancaria - Agente5
cd /d "C:\Tools\Agente5"
call venv\Scripts\activate.bat
streamlit run app.py --server.address 0.0.0.0 --server.headless false --browser.gatherUsageStats false
pause
