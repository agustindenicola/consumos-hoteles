@echo off
chcp 65001 >nul
title Actualizar Consumos Hoteles
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0actualizar.ps1"
