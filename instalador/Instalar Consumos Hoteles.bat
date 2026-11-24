@echo off
chcp 65001 >nul
title Instalar Consumos Hoteles
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0archivos (no tocar)\instalar.ps1"
