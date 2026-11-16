param(
    [Parameter(Mandatory)] [string] $Sdk,
    [Parameter(Mandatory)] [string] $Keystore,
    [string] $Alias = 'shiliao-preview',
    [string] $Output = (Join-Path $PSScriptRoot 'build/Shiliao-Android.apk'),
    [switch] $Instrumentation
)

$ErrorActionPreference = 'Stop'
$platform = (Resolve-Path (Join-Path $Sdk 'platforms/android-35/android.jar')).Path
$tools = (Resolve-Path (Join-Path $Sdk 'build-tools/35.0.0')).Path
$Keystore = (Resolve-Path -LiteralPath $Keystore).Path
if (!$env:SHILIAO_APK_PASSWORD) { throw 'Set SHILIAO_APK_PASSWORD to the keystore password.' }

function Invoke-BuildTool([string] $Program, [string[]] $Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed ($LASTEXITCODE)" }
}

$scratch = Join-Path $PSScriptRoot ('build/' + [Guid]::NewGuid().ToString('N'))
$classes = Join-Path $scratch 'classes'
$dex = Join-Path $scratch 'dex'
$resources = Join-Path $scratch 'resources.zip'
$unsigned = Join-Path $scratch 'unsigned.apk'
$aligned = Join-Path $scratch 'aligned.apk'
$bytecode = Join-Path $scratch 'classes.jar'
New-Item -ItemType Directory -Path $classes,$dex -Force | Out-Null

$compile = @('--release','8','-encoding','UTF-8','-classpath',$platform,'-d',$classes)
$compile += @(Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot 'src') -Filter '*.java' -Recurse | ForEach-Object FullName)
$manifest = Join-Path $PSScriptRoot 'AndroidManifest.xml'
if ($Instrumentation) {
    $compile += @(Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot 'test') -Filter '*.java' -Recurse | ForEach-Object FullName)
    $manifest = Join-Path $PSScriptRoot 'test/AndroidManifest.xml'
}
Invoke-BuildTool 'javac' $compile
Invoke-BuildTool 'jar' @('--create','--file',$bytecode,'-C',$classes,'.')
Invoke-BuildTool (Join-Path $tools 'd8.bat') @('--release','--min-api','26','--lib',$platform,'--output',$dex,$bytecode)
Invoke-BuildTool (Join-Path $tools 'aapt2.exe') @('compile','--dir',(Join-Path $PSScriptRoot 'res'),'-o',$resources)
Invoke-BuildTool (Join-Path $tools 'aapt2.exe') @('link','--manifest',$manifest,'-I',$platform,'-o',$unsigned,$resources)
Invoke-BuildTool 'jar' @('--update','--file',$unsigned,'-C',$dex,'classes.dex')
Invoke-BuildTool (Join-Path $tools 'zipalign.exe') @('-f','4',$unsigned,$aligned)
$out = [IO.Path]::GetFullPath($Output)
New-Item -ItemType Directory -Path (Split-Path $out) -Force | Out-Null
Invoke-BuildTool (Join-Path $tools 'apksigner.bat') @('sign','--ks',$Keystore,'--ks-key-alias',$Alias,'--ks-pass','env:SHILIAO_APK_PASSWORD','--out',$out,$aligned)
Invoke-BuildTool (Join-Path $tools 'apksigner.bat') @('verify','--verbose',$out)
Get-Item -LiteralPath $out | Select-Object FullName,Length
