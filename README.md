# Consumos Hoteles

Aplicación de escritorio (servidor local + interfaz web) para controlar los consumos de agua, electricidad y gas de una cadena de hoteles. Desarrollada a medida para una gestora de 4 hoteles en Buenos Aires y en uso diario.

> Versión pública anonimizada: los nombres de los hoteles, razones sociales, números de cuenta y logos del cliente fueron reemplazados por datos de ejemplo.

## Qué hace

- **Lectura automática de facturas PDF** de AySA, Edesur y MetroGAS: período, consumo, importe y vencimiento. Detecta el hotel por el número de cuenta y descarta duplicados y notas de débito informativas.
- **Consumo por huésped** (persona-noche) comparado con referencias del sector (UTGRA 2022), con mínimos, máximos y promedios.
- **Validación de datos**: si un valor es muy distinto a lo habitual, la API responde `422` y la interfaz pide confirmación antes de guardar.
- **Informes PDF y Excel** configurables (secciones, gráficos, períodos) con vista previa.
- **Seguridad de los datos**: respaldo diario automático, recuperación si la base se daña, copia externa (Drive / OneDrive / pendrive) y papelera con retención de 90 días.
- **Instalador y actualizador** en PowerShell que actualizan el programa sin tocar los datos del cliente.

## Stack

Python · Flask · Waitress · SQLite · pypdfium2 · ReportLab · matplotlib · openpyxl · Chart.js · PyInstaller

## Cómo correrlo

```bash
pip install -r requirements.txt
python run.py
```

Se abre en `http://127.0.0.1:8765`. Para generar el ejecutable de Windows: `build.bat`.

## Estructura

```
app/
  server.py    API REST y rutas de la interfaz
  parsers.py   extracción de datos de las facturas PDF
  core.py      cálculos de consumo e indicadores
  db.py        esquema SQLite, migraciones y respaldos
  reports.py   informes PDF / Excel
  static/      interfaz web (HTML, CSS, JS)
instalador/    instalador de un clic
actualizador/  actualización sin pérdida de datos
```
