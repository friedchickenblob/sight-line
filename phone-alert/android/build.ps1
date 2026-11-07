# Builds SightLineAlert.apk with the plain Android SDK tools (no Gradle, no downloads).
#
#   cd phone-alert\android
#   .\build.ps1
#
# Output: build\SightLineAlert.apk  (signed with your Android debug key, installable on any
# modern phone once "install unknown apps" is allowed for the app you open it from).
#
# Needs: Android SDK (platform android-34 or newer + build-tools) and a JDK 17+; Android Studio
# ships both. Override the guesses with ANDROID_HOME and JAVA_HOME if yours live elsewhere.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

function Newest($dir, $pattern) {
    Get-ChildItem $dir -Directory -Filter $pattern | Sort-Object { [version]($_.Name -replace '[^\d\.]', '') } -ErrorAction SilentlyContinue | Select-Object -Last 1
}

$sdk = if ($env:ANDROID_HOME) { $env:ANDROID_HOME } elseif ($env:ANDROID_SDK_ROOT) { $env:ANDROID_SDK_ROOT } else { "$env:LOCALAPPDATA\Android\Sdk" }
if (-not (Test-Path $sdk)) { throw "Android SDK not found at $sdk. Install Android Studio or set ANDROID_HOME." }

$bt = Get-ChildItem "$sdk\build-tools" -Directory | Sort-Object { [version]$_.Name } | Select-Object -Last 1
$plat = Get-ChildItem "$sdk\platforms" -Directory -Filter "android-*" |
    Where-Object { $_.Name -match '^android-\d+$' } | Sort-Object { [int]($_.Name -replace 'android-', '') } | Select-Object -Last 1
if (-not $bt -or -not $plat) { throw "Need build-tools and a platform in $sdk (SDK Manager in Android Studio)." }
$api = [int]($plat.Name -replace 'android-', '')
if ($api -lt 34) { throw "Need platform android-34 or newer (found $($plat.Name))." }

$jdk = $null
foreach ($c in @($env:JAVA_HOME, "$env:ProgramFiles\Android\Android Studio\jbr", "$env:ProgramFiles\Android\Android Studio1\jbr")) {
    if ($c -and (Test-Path "$c\bin\javac.exe")) { $jdk = $c; break }
}
if (-not $jdk) { throw "No JDK with javac found. Install Android Studio or set JAVA_HOME to a JDK 17+." }
$env:JAVA_HOME = $jdk

$btp = $bt.FullName
$androidJar = "$($plat.FullName)\android.jar"
Write-Host "SDK $sdk | build-tools $($bt.Name) | $($plat.Name) | JDK $jdk"

function Run($exe) {
    & $exe @args
    if ($LASTEXITCODE -ne 0) { throw "$exe failed (exit $LASTEXITCODE)" }
}

$build = "$here\build"
if (Test-Path $build) { Remove-Item $build -Recurse -Force }
New-Item -ItemType Directory -Path "$build\gen", "$build\classes", "$build\dex" | Out-Null

$src = "$here\app\src\main"
Write-Host "1/6 resources"
Run "$btp\aapt2.exe" compile --dir "$src\res" -o "$build\res.zip"
Run "$btp\aapt2.exe" link -o "$build\base.apk" -I $androidJar --manifest "$src\AndroidManifest.xml" `
    --java "$build\gen" --min-sdk-version 26 --target-sdk-version $api --version-code 1 --version-name 1.0 `
    --auto-add-overlay "$build\res.zip"

Write-Host "2/6 compile Java"
$sources = @(Get-ChildItem "$src\java", "$build\gen" -Recurse -Filter *.java | ForEach-Object { $_.FullName })
Run "$jdk\bin\javac.exe" -source 8 -target 8 '-Xlint:-options' -encoding UTF-8 -bootclasspath "$androidJar;$btp\core-lambda-stubs.jar" -d "$build\classes" @sources

Write-Host "3/6 dex"
$classes = @(Get-ChildItem "$build\classes" -Recurse -Filter *.class | ForEach-Object { $_.FullName })
Run "$btp\d8.bat" --release --min-api 26 --lib $androidJar --output "$build\dex" @classes

Write-Host "4/6 package"
Push-Location "$build\dex"
Run "$btp\aapt.exe" add "$build\base.apk" classes.dex | Out-Null
Pop-Location
Run "$btp\zipalign.exe" -f -p 4 "$build\base.apk" "$build\aligned.apk"

Write-Host "5/6 sign"
$ks = "$env:USERPROFILE\.android\debug.keystore"
if (-not (Test-Path $ks)) {
    New-Item -ItemType Directory -Force "$env:USERPROFILE\.android" | Out-Null
    Run "$jdk\bin\keytool.exe" -genkeypair -keystore $ks -alias androiddebugkey -storepass android -keypass android `
        -keyalg RSA -keysize 2048 -validity 10000 -dname "CN=Android Debug,O=Android,C=US"
}
Run "$btp\apksigner.bat" sign --ks $ks --ks-key-alias androiddebugkey --ks-pass pass:android --key-pass pass:android `
    --out "$build\SightLineAlert.apk" "$build\aligned.apk"
Run "$btp\apksigner.bat" verify "$build\SightLineAlert.apk"

Write-Host "6/6 done"
Remove-Item "$build\base.apk", "$build\aligned.apk", "$build\SightLineAlert.apk.idsig" -ErrorAction SilentlyContinue
$apk = Get-Item "$build\SightLineAlert.apk"
Write-Host ("Built {0} ({1:N0} KB)" -f $apk.FullName, ($apk.Length / 1KB))
