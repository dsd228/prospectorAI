@echo off                                    
title IG Scraper Backend :8765              
cd /d "D:\prospectorAI\"                               
call "D:\prospectorAI\venv\Scripts\activate.bat"       
echo.                                       
echo  IG Scraper Backend - DiazUX           
echo  Puerto: http://localhost:8765          
echo  Cerrando esta ventana lo detiene.     
echo.                                       
python ig_backend.py                        
echo.                                       
echo  Backend detenido.                     
pause                                       
