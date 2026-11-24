# Actualiza Consumos Hoteles sin tocar los datos de la clienta.
# Busca la instalación por el acceso directo del escritorio; si no lo encuentra, pide elegir la carpeta.
# Reemplaza solo ConsumosHoteles.exe, _internal y LEEME.txt. La carpeta "datos" no se toca.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$nuevo = Join-Path $here "programa nuevo"

function Fin($msg, $ok) {
    Write-Host ""
    Write-Host ("  " + $msg) -ForegroundColor $(if ($ok) { "Green" } else { "Yellow" })
    Write-Host ""
    if (-not $env:SIN_PAUSA) { Read-Host "  Presioná Enter para cerrar" | Out-Null }
    exit $(if ($ok) { 0 } else { 1 })
}

# Devuelve la carpeta "programa" (la que tiene ConsumosHoteles.exe), o $null
function Programa($dir) {
    if (-not $dir) { return $null }
    foreach ($c in @($dir, (Join-Path $dir "programa"))) {
        if (Test-Path (Join-Path $c "ConsumosHoteles.exe")) { return (Resolve-Path $c).Path }
    }
    return $null
}

Write-Host ""
Write-Host "  ACTUALIZAR CONSUMOS HOTELES" -ForegroundColor Cyan
Write-Host "  Tus datos (facturas, huéspedes, alertas) no se tocan."
Write-Host ""

if (-not (Test-Path (Join-Path $nuevo "ConsumosHoteles.exe"))) {
    Fin "No encuentro la carpeta 'programa nuevo'. Descomprimí el zip completo y volvé a probar." $false
}

# 1) dónde está instalada
$destino = Programa $env:DESTINO_APP
if (-not $destino) {
    $escritorios = @([Environment]::GetFolderPath("Desktop"), (Join-Path $env:USERPROFILE "Desktop"), (Join-Path $env:OneDrive "Escritorio"), (Join-Path $env:OneDrive "Desktop"))
    foreach ($d in $escritorios) {
        if (-not $d) { continue }
        $lnk = Join-Path $d "Consumos Hoteles.lnk"
        if (Test-Path $lnk) {
            $t = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk).TargetPath
            $destino = Programa (Split-Path -Parent $t)
            if ($destino) { break }
        }
    }
}
if (-not $destino) {
    Write-Host "  No encontré el ícono del escritorio. Elegí la carpeta donde guardaste la aplicación"
    Write-Host "  (se llama 'Entrega - Consumos Hoteles')."
    Add-Type -AssemblyName System.Windows.Forms
    $f = New-Object System.Windows.Forms.FolderBrowserDialog
    $f.Description = "Elegí la carpeta 'Entrega - Consumos Hoteles' (la que usás hoy)"
    $o = New-Object System.Windows.Forms.Form -Property @{TopMost = $true}
    if ($f.ShowDialog($o) -eq "OK") { $destino = Programa $f.SelectedPath }
    if (-not $destino) { Fin "En esa carpeta no está la aplicación. No se cambió nada." $false }
}
if ((Resolve-Path $destino).Path -eq (Resolve-Path $nuevo).Path) {
    Fin "Esa es la carpeta de la actualización, no la de tu aplicación. No se cambió nada." $false
}
Write-Host "  Aplicación encontrada en:"
Write-Host "    $destino"
Write-Host ""

# 2) cerrar la aplicación si está abierta
$p = Get-Process -Name "ConsumosHoteles" -ErrorAction SilentlyContinue
if ($p) {
    Write-Host "  Cerrando la aplicación..."
    $p | Stop-Process -Force
    Start-Sleep -Seconds 2
}

# 3) copia de seguridad de los datos, por las dudas
$datos = Join-Path $destino "datos"
$db = Join-Path $datos "consumos.db"
if (Test-Path $db) {
    $resp = Join-Path $datos "respaldos"
    New-Item -ItemType Directory -Force -Path $resp | Out-Null
    $copia = Join-Path $resp ("antes-de-actualizar-" + (Get-Date -Format "yyyy-MM-dd_HHmm") + ".db")
    Copy-Item $db $copia -Force
    Write-Host "  Copia de seguridad de tus datos: listo."
}

# 4) reemplazar solo el programa
try {
    Copy-Item (Join-Path $nuevo "ConsumosHoteles.exe") (Join-Path $destino "ConsumosHoteles.exe") -Force
    $int = Join-Path $destino "_internal"
    if (Test-Path $int) { Remove-Item $int -Recurse -Force }
    Copy-Item (Join-Path $nuevo "_internal") $int -Recurse -Force
    if (Test-Path (Join-Path $nuevo "LEEME.txt")) { Copy-Item (Join-Path $nuevo "LEEME.txt") $destino -Force }
    # la guía nueva va en la carpeta de afuera (al lado de "programa")
    $guia = Join-Path $here "Guía de uso - Consumos Hoteles.pdf"
    if ((Test-Path $guia) -and ((Split-Path -Leaf $destino) -eq "programa")) {
        Copy-Item $guia (Split-Path -Parent $destino) -Force
    }
} catch {
    Fin ("No se pudo terminar: " + $_.Exception.Message + "  Cerrá la aplicación y volvé a probar.") $false
}

Write-Host "  Programa actualizado." -ForegroundColor Green
if (-not $env:SIN_PAUSA) {
    Start-Process (Join-Path $destino "ConsumosHoteles.exe") -WorkingDirectory $destino
    Fin "¡Listo! La aplicación se abre sola con todos tus datos." $true
}
Fin "Listo." $true
