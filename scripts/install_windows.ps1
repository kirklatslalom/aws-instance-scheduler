function Convert-SecureString {
    param (
        $String
    )
    $Ptr = [System.Runtime.InteropServices.Marshal]::SecureStringToCoTaskMemUnicode($String)
    $result = [System.Runtime.InteropServices.Marshal]::PtrToStringUni($Ptr)
    [System.Runtime.InteropServices.Marshal]::ZeroFreeCoTaskMemUnicode($Ptr)
    return $result
}

function Test-ExecutionPolicy {
    if ($(Get-ExecutionPolicy) -eq "Restricted"){
      Write-Host "ExecutionPolicy is set to Restricted.  Please run in an Admin PowerScript shell"
      exit 1
    }
}

function Test-IsAdmin {
    try {
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
        $principal = New-Object Security.Principal.WindowsPrincipal -ArgumentList $identity
        return $principal.IsInRole( [Security.Principal.WindowsBuiltInRole]::Administrator )
    } catch {
        throw "Failed to determine if the current user has elevated privileges. The error was: '{0}'." -f $_
    }

    <#
        .SYNOPSIS
            Checks if the current Powershell instance is running with elevated privileges or not.
        .EXAMPLE
            PS C:\> Test-IsAdmin
        .OUTPUTS
            System.Boolean
                True if the current Powershell is elevated, false if not.
    #>
}

# Check sanity
Test-ExecutionPolicy
if ($(Test-IsAdmin) -ne $true) {
  Write-Host "User doesn't have elevated privileges.  Please run in an Admin PowerScript shell"
  exit 1
}

# Install Chocolatey
Set-ExecutionPolicy Bypass -Scope Process -Force; [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072; iex ((New-Object System.Net.WebClient).DownloadString('https://chocolatey.org/install.ps1'))
refreshenv

# gcloud install
(New-Object Net.WebClient).DownloadFile("https://dl.google.com/dl/cloudsdk/channels/rapid/GoogleCloudSDKInstaller.exe", "$env:Temp\GoogleCloudSDKInstaller.exe")

& $env:Temp\GoogleCloudSDKInstaller.exe /S

# Install terraform
choco install terraform make git base64 -y
refreshenv

# Apparently it takes awhile for this binary to exist
while (!(Test-Path "C:\Program Files (x86)\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.ps1")) { Start-Sleep 10 }

# Make sure powershell component is there
& "C:\Program Files (x86)\Google\Cloud SDK\google-cloud-sdk.staging\bin\gcloud.ps1" components install powershell

# Try to auth
#Start-Process 'C:\Program Files (x86)\Google\Cloud SDK\google-cloud-sdk\bin\gcloud' init -Wait
#& "C:\Program Files (x86)\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.ps1" init

# more auth
#& "C:\Program Files (x86)\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.ps1" auth application-default login
