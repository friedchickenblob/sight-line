# Run ONCE in an Administrator PowerShell. Lets phones reach the alert server through Windows Firewall.
# Without this, the phone cannot connect (the first-run "Allow access" popup often only opens it for
# Private networks, and a hotspot network is frequently classed as Public).
#
#   Set-ExecutionPolicy -Scope Process Bypass; .\allow-firewall.ps1
#
# Remove it later with:  Remove-NetFirewallRule -DisplayName "Phone alert server (TCP 8765)"
param([int]$Port = 8765)

$name = "Phone alert server (TCP $Port)"
if (Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue) {
    Write-Host "Rule already exists: $name"
} else {
    # LocalSubnet keeps it off the wider internet; hotspot subnets count as local subnets.
    New-NetFirewallRule -DisplayName $name -Direction Inbound -Protocol TCP -LocalPort $Port `
        -Action Allow -Profile Any -RemoteAddress LocalSubnet | Out-Null
    Write-Host "Added: $name (any network profile, local subnet only)"
}
