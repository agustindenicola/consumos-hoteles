@echo off
pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --clean --onedir --name "ConsumosHoteles" --icon recursos/icono.ico --add-data "app/static;app/static" --hidden-import waitress --exclude-module tkinter --exclude-module pymupdf --exclude-module fitz run.py
copy /Y LEEME.txt dist\ConsumosHoteles\
echo Listo: dist\ConsumosHoteles
pause
