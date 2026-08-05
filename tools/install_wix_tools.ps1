[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$packages = @(
    @{
        Id = 'WiXToolset.WiXCLI'
        Name = 'WiX Toolset Command-Line Tools'
    },
    @{
        Id = 'WiXToolset.WiXAdditionalTools'
        Name = 'WiX Toolset Additional Tools'
    }
)

if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    throw 'winget is required but was not found on PATH.'
}

foreach ($package in $packages) {
    Write-Host "Checking $($package.Name) ($($package.Id))..."

    $listOutput = winget list --id $package.Id --exact --accept-source-agreements 2>$null | Out-String
    if ($LASTEXITCODE -eq 0 -and $listOutput -match [regex]::Escape($package.Id)) {
        Write-Host "  Already installed."
        continue
    }

    Write-Host "  Installing..."
    winget install --id $package.Id --exact --accept-package-agreements --accept-source-agreements
}

$wixCommand = Get-Command wix.exe -ErrorAction SilentlyContinue
if (-not $wixCommand) {
    $wixCommand = Get-Command wix -ErrorAction SilentlyContinue
}

if ($wixCommand) {
    Write-Host "WiX CLI found at: $($wixCommand.Source)"
    try {
        & $wixCommand.Source --version
    }
    catch {
        Write-Warning "WiX was found but '--version' did not run cleanly in the current session."
    }
}
else {
    Write-Warning 'WiX packages were installed but wix.exe is not visible on PATH yet. Open a new terminal and try again.'
}
