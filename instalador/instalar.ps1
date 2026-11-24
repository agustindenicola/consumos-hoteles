# Instala o actualiza Consumos Hoteles.
# - Si ya está instalada (se encuentra por el ícono del escritorio): reemplaza solo el programa; los datos no se tocan.
# - Si no: la copia a Documentos\Consumos Hoteles (con los datos iniciales) y crea el ícono del escritorio.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$src = Join-Path $here "programa"
$guia = Join-Path (Split-Path -Parent $here) "Guía completa - Consumos Hoteles.pdf"
$desk = if ($env:INSTALAR_ESCRITORIO) { $env:INSTALAR_ESCRITORIO } else { [Environment]::GetFolderPath("Desktop") }
$docs = if ($env:INSTALAR_DOCUMENTOS) { $env:INSTALAR_DOCUMENTOS } else { [Environment]::GetFolderPath("MyDocuments") }
$lnk = Join-Path $desk "Consumos Hoteles.lnk"

function Fin($msg, $ok) {
    Write-Host ""
    Write-Host ("  " + $msg) -ForegroundColor $(if ($ok) { "Green" } else { "Yellow" })
    Write-Host ""
    if (-not $env:SIN_PAUSA) { Read-Host "  Presioná Enter para cerrar" | Out-Null }
    exit $(if ($ok) { 0 } else { 1 })
}
function Programa($dir) {   # carpeta que tiene ConsumosHoteles.exe, o $null
    if (-not $dir) { return $null }
    foreach ($c in @($dir, (Join-Path $dir "programa"))) {
        if (Test-Path (Join-Path $c "ConsumosHoteles.exe")) { return (Resolve-Path $c).Path }
    }
    return $null
}
function Icono($exe) {
    New-Item -ItemType Directory -Force -Path $desk | Out-Null
    $s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
    $s.TargetPath = $exe; $s.WorkingDirectory = (Split-Path -Parent $exe); $s.IconLocation = "$exe,0"
    $s.Description = "Consumos de agua, luz y gas de los hoteles"; $s.Save()
}

Write-Host ""
Write-Host "  CONSUMOS HOTELES" -ForegroundColor Cyan
Write-Host ""
if (-not (Test-Path (Join-Path $src "ConsumosHoteles.exe"))) {
    Fin "Faltan archivos. Descomprimí el zip completo (clic derecho > Extraer todo) y volvé a probar." $false
}

# ¿ya está instalada?
$destino = $null
if (Test-Path $lnk) { $destino = Programa (Split-Path -Parent ((New-Object -ComObject WScript.Shell).CreateShortcut($lnk).TargetPath)) }
if (-not $destino) { $destino = Programa (Join-Path $docs "Consumos Hoteles") }
if (-not $destino) { $destino = Programa (Join-Path $docs "Entrega - Consumos Hoteles") }
if ($destino -and ((Resolve-Path $destino).Path -eq (Resolve-Path $src).Path)) { $destino = $null }

# cerrar la aplicación si está abierta
$p = Get-Process -Name "ConsumosHoteles" -ErrorAction SilentlyContinue
if ($p) { Write-Host "  Cerrando la aplicación..."; $p | Stop-Process -Force; Start-Sleep -Seconds 2 }

try {
    if ($destino) {
        # ---------- ACTUALIZAR
        Write-Host "  Ya tenías la aplicación. Se actualiza sin tocar tus datos."
        $db = Join-Path $destino "datos\consumos.db"
        if (Test-Path $db) {
            $resp = Join-Path $destino "datos\respaldos"
            New-Item -ItemType Directory -Force -Path $resp | Out-Null
            Copy-Item $db (Join-Path $resp ("antes-de-actualizar-" + (Get-Date -Format "yyyy-MM-dd_HHmm") + ".db")) -Force
        }
        Copy-Item (Join-Path $src "ConsumosHoteles.exe") $destino -Force
        $int = Join-Path $destino "_internal"
        if (Test-Path $int) { Remove-Item $int -Recurse -Force }
        Copy-Item (Join-Path $src "_internal") $int -Recurse -Force
        if ((Split-Path -Leaf $destino) -eq "programa" -and (Test-Path $guia)) {
            Copy-Item $guia (Split-Path -Parent $destino) -Force
        }
        $exe = Join-Path $destino "ConsumosHoteles.exe"
        Icono $exe
        $msg = "¡Listo! La aplicación está actualizada y tiene todos tus datos."
    } else {
        # ---------- INSTALAR
        $base = Join-Path $docs "Consumos Hoteles"
        Write-Host "  Instalando en: $base"
        New-Item -ItemType Directory -Force -Path $base | Out-Null
        Copy-Item $src (Join-Path $base "programa") -Recurse -Force
        if (Test-Path $guia) { Copy-Item $guia $base -Force }
        $exe = Join-Path $base "programa\ConsumosHoteles.exe"
        Icono $exe
        $msg = "¡Listo! En el escritorio tenés el ícono 'Consumos Hoteles'."
    }
} catch {
    Fin ("No se pudo terminar: " + $_.Exception.Message) $false
}

if (-not $env:SIN_PAUSA) { Start-Process $exe -WorkingDirectory (Split-Path -Parent $exe) }
Fin ($msg + " Ya podés borrar esta carpeta.") $true
