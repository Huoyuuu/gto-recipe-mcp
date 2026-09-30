param(
    [string]$Instance = "E:\Games\HMCL-3.5.8\.minecraft\versions\GregTech Odyssey"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$project = $PSScriptRoot
$work = Join-Path $project "build"
$classes = Join-Path $work "classes"
$dist = Join-Path $project "dist"
$minecraftRoot = Split-Path -Parent (Split-Path -Parent $Instance)
$libraries = Join-Path $minecraftRoot "libraries"
$gtocore = Join-Path $Instance "mods\gtocore-forge-1.20.1-0.5.6-beta.jar"
$gtceu = Join-Path $work "gtceu-26.7.3.jar"
$gtolib = Join-Path $work "gtolib-26.7.4.jar"

New-Item -ItemType Directory -Force -Path $classes, $dist | Out-Null

if (-not (Test-Path -LiteralPath $gtceu)) {
    Push-Location $work
    try {
        jar xf $gtocore "META-INF/jarjar/gtceu-1.20.1-forge-1.20.1-26.7.3.jar"
        Move-Item -LiteralPath "META-INF\jarjar\gtceu-1.20.1-forge-1.20.1-26.7.3.jar" -Destination $gtceu
    } finally {
        Pop-Location
    }
}
if (-not (Test-Path -LiteralPath $gtolib)) {
    Push-Location $work
    try {
        jar xf $gtocore "META-INF/jarjar/gtolib-forge-1.20.1-26.7.4.jar"
        Move-Item -LiteralPath "META-INF\jarjar\gtolib-forge-1.20.1-26.7.4.jar" -Destination $gtolib
    } finally {
        Pop-Location
    }
}

$classpath = @(
    (Join-Path $libraries "net\minecraft\client\1.20.1-20230612.114412\client-1.20.1-20230612.114412-srg.jar"),
    (Join-Path $libraries "net\minecraftforge\forge\1.20.1-47.4.20\forge-1.20.1-47.4.20-universal.jar"),
    (Join-Path $libraries "net\minecraftforge\eventbus\6.2.33\eventbus-6.2.33.jar"),
    (Join-Path $libraries "net\minecraftforge\fmlloader\1.20.1-47.4.20\fmlloader-1.20.1-47.4.20.jar"),
    (Join-Path $libraries "net\minecraftforge\fmlcore\1.20.1-47.4.20\fmlcore-1.20.1-47.4.20.jar"),
    (Join-Path $libraries "net\minecraftforge\javafmllanguage\1.20.1-47.4.20\javafmllanguage-1.20.1-47.4.20.jar"),
    (Join-Path $libraries "net\minecraftforge\mergetool\1.1.5\mergetool-1.1.5-api.jar"),
    (Join-Path $libraries "com\mojang\datafixerupper\6.0.8\datafixerupper-6.0.8.jar"),
    (Join-Path $libraries "com\mojang\brigadier\1.1.8\brigadier-1.1.8.jar"),
    (Join-Path $libraries "com\google\code\gson\gson\2.10\gson-2.10.jar"),
    (Join-Path $libraries "it\unimi\dsi\fastutil\8.5.9\fastutil-8.5.9.jar"),
    (Join-Path $libraries "com\google\guava\guava\31.1-jre\guava-31.1-jre.jar"),
    (Join-Path $libraries "org\apache\commons\commons-lang3\3.12.0\commons-lang3-3.12.0.jar"),
    (Join-Path $work "stubs"),
    $gtocore,
    $gtceu,
    $gtolib
) -join ";"

# compile-only annotation stubs (not packaged into the mod jar)
javac --release 17 -d (Join-Path $work "stubs") (Get-ChildItem -Recurse (Join-Path $project "stubs") -Filter *.java).FullName
Remove-Item -LiteralPath $classes -Recurse -Force
New-Item -ItemType Directory -Force -Path $classes | Out-Null
javac --release 17 -encoding UTF-8 -cp $classpath -d $classes `
    (Join-Path $project "src\local\gto\recipeexporter\GtoRecipeExporter.java")
if ($LASTEXITCODE -ne 0) {
    throw "javac failed with exit code $LASTEXITCODE"
}

Copy-Item -LiteralPath (Join-Path $project "META-INF") -Destination $classes -Recurse -Force
Copy-Item -LiteralPath (Join-Path $project "pack.mcmeta") -Destination $classes -Force
$jarPath = Join-Path $dist "gto-recipe-exporter-1.0.0.jar"
if (Test-Path -LiteralPath $jarPath) {
    Remove-Item -LiteralPath $jarPath -Force
}
Push-Location $classes
try {
    jar --create --file $jarPath .
    if ($LASTEXITCODE -ne 0) {
        throw "jar failed with exit code $LASTEXITCODE"
    }
} finally {
    Pop-Location
}
Write-Output $jarPath
