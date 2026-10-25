param(
    [string]$SdkRoot = $env:ANDROID_HOME,
    [string]$JdkRoot = $env:JAVA_HOME,
    [string]$BuildToolsVersion = '35.0.0',
    [string]$Platform = 'android-34'
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (-not $SdkRoot) { throw 'Set ANDROID_HOME or pass -SdkRoot.' }
if (-not $JdkRoot) { throw 'Set JAVA_HOME or pass -JdkRoot.' }
$sdk = (Resolve-Path -LiteralPath $SdkRoot).Path
$jdk = (Resolve-Path -LiteralPath $JdkRoot).Path
$tools = Join-Path $sdk "build-tools\$BuildToolsVersion"
$androidJar = Join-Path $sdk "platforms\$Platform\android.jar"
$javac = Join-Path $jdk 'bin\javac.exe'
$jar = Join-Path $jdk 'bin\jar.exe'
$java = Join-Path $jdk 'bin\java.exe'
$aapt = Join-Path $tools 'aapt.exe'
$zipalign = Join-Path $tools 'zipalign.exe'
$d8Jar = Join-Path $tools 'lib\d8.jar'
$signerJar = Join-Path $tools 'lib\apksigner.jar'
$build = Join-Path $PSScriptRoot 'build'
$keystore = Join-Path $build 'helper-debug.keystore'
foreach ($required in @($androidJar,$javac,$jar,$java,$aapt,$zipalign,$d8Jar,$signerJar,$keystore)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Missing local tool or existing signing key: $required. Restore the existing key; do not rotate it."
    }
}
# Only the existing helper signing identity is used. No downloads or Gradle daemon.
$build = (Resolve-Path -LiteralPath $build).Path
$stagedAndroidJar = Join-Path $build 'android.jar'
# Native aapt on this machine cannot open the non-ASCII SDK directory.
Copy-Item -LiteralPath $androidJar -Destination $stagedAndroidJar -Force
$androidJar = $stagedAndroidJar
function Invoke-LocalTool([string]$ToolPath, [string[]]$ToolArgs) {
    & $ToolPath @ToolArgs
    if ($LASTEXITCODE -ne 0) { throw "Local build tool failed: $(Split-Path -Leaf $ToolPath)" }
}
$commonSources = @(Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot 'common-src') -Recurse -File -Filter '*.java' |
    Sort-Object FullName | ForEach-Object FullName)
function Build-Apk([string]$Name, [string]$SourceRoot, [string]$Manifest, [string]$OutputName) {
    $classes = Join-Path $build "$Name-classes"
    $dex = Join-Path $build "$Name-dex"
    foreach ($generated in @($classes,$dex)) {
        if (Test-Path -LiteralPath $generated) {
            $resolved = (Resolve-Path -LiteralPath $generated).Path
            if (-not $resolved.StartsWith($build + [IO.Path]::DirectorySeparatorChar,
                    [StringComparison]::OrdinalIgnoreCase) -or $resolved -eq $build) {
                throw 'Generated directory escaped the helper build directory.'
            }
            Remove-Item -LiteralPath $resolved -Recurse -Force
        }
        New-Item -ItemType Directory -Path $generated | Out-Null
    }
    $sources = @(Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot $SourceRoot) -Recurse -File -Filter '*.java' |
        Sort-Object FullName | ForEach-Object FullName)
    $classJar = Join-Path $build "$Name-classes.jar"
    $unsigned = Join-Path $build "$Name-unsigned.apk"
    $aligned = Join-Path $build "$Name-aligned.apk"
    $apk = Join-Path $build $OutputName
    $manifestDirectory = Join-Path $build "$Name-manifest"
    New-Item -ItemType Directory -Force -Path $manifestDirectory | Out-Null
    $stagedManifest = Join-Path $manifestDirectory 'AndroidManifest.xml'
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $Manifest) -Destination $stagedManifest -Force
    Invoke-LocalTool $javac (@('--release','8','-encoding','UTF-8','-classpath',$androidJar,'-d',$classes) + $commonSources + $sources)
    Invoke-LocalTool $jar @('--create','--file',$classJar,'-C',$classes,'.')
    Invoke-LocalTool $java @('-cp',$d8Jar,'com.android.tools.r8.D8','--min-api','24','--lib',$androidJar,'--output',$dex,$classJar)
    Invoke-LocalTool $aapt @('package','-f','-M',$stagedManifest,'-I',$androidJar,'-F',$unsigned)
    Push-Location -LiteralPath $dex
    try { Invoke-LocalTool $aapt @('add',$unsigned,'classes.dex') }
    finally { Pop-Location }
    Invoke-LocalTool $zipalign @('-f','4',$unsigned,$aligned)
    Invoke-LocalTool $java @('-jar',$signerJar,'sign','--ks',$keystore,'--ks-key-alias','xhstexthelper',
        '--ks-pass','pass:android','--key-pass','pass:android','--out',$apk,$aligned)
    Invoke-LocalTool $java @('-jar',$signerJar,'verify','--verbose',$apk)
    Invoke-LocalTool $zipalign @('-c','4',$apk)
    Write-Output "APK: $apk"
    Write-Output "SHA256: $((Get-FileHash -LiteralPath $apk -Algorithm SHA256).Hash.ToLowerInvariant())"
}
Build-Apk 'input' 'src' 'AndroidManifest.xml' 'mirrow-xhs-text-helper-debug.apk'
Build-Apk 'signals' 'host-src' 'SignalsManifest.xml' 'mirrow-xhs-signals-debug.apk'
