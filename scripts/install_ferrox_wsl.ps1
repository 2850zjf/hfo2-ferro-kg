param(
    [string]$Distro = "Ubuntu-24.04",
    [string]$ProxyUrl = "http://172.19.128.1:7891"
)

$ErrorActionPreference = "Stop"
$FerroXCommit = "ac4606e83318bde632ab1e7d9a18f48f64e48f57"

if ($ProxyUrl -notmatch '^http://[0-9.]+:[0-9]+$') {
    throw "ProxyUrl must be an HTTP proxy URL such as http://172.19.128.1:7891"
}
$ProxyPrefix = "export http_proxy='$ProxyUrl' https_proxy='$ProxyUrl' HTTP_PROXY='$ProxyUrl' HTTPS_PROXY='$ProxyUrl'; "

function Invoke-WslBash {
    param(
        [Parameter(Mandatory = $true)] [string]$Command,
        [Parameter(Mandatory = $true)] [string]$Step
    )
    Write-Host "==> $Step"
    & wsl.exe -d $Distro -- bash -lc ($ProxyPrefix + $Command)
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE"
    }
}

# Stop before sudo/build work if WSL is still trapped behind a Windows
# localhost/Fake-IP proxy. This also prevents a false success message.
Invoke-WslBash `
    -Step "Checking Ubuntu and GitHub connectivity" `
    -Command 'curl --fail --silent --show-error --location --max-time 30 https://archive.ubuntu.com/ubuntu/ -o /dev/null && curl --fail --silent --show-error --location --max-time 30 https://github.com/ -o /dev/null'

Invoke-WslBash `
    -Step "Installing CPU build dependencies" `
    -Command "sudo env http_proxy='$ProxyUrl' https_proxy='$ProxyUrl' HTTP_PROXY='$ProxyUrl' HTTPS_PROXY='$ProxyUrl' apt-get update && sudo env http_proxy='$ProxyUrl' https_proxy='$ProxyUrl' HTTP_PROXY='$ProxyUrl' HTTPS_PROXY='$ProxyUrl' apt-get install -y build-essential cmake git libopenmpi-dev openmpi-bin curl"

Invoke-WslBash `
    -Step "Preparing installation directories" `
    -Command 'sudo mkdir -p /opt/ferrox-src /opt/ferrox && sudo chown -R "$(id -u):$(id -g)" /opt/ferrox-src /opt/ferrox'

$checkoutCommand = "if [ ! -d /opt/ferrox-src/.git ]; then find /opt/ferrox-src -mindepth 1 -maxdepth 1 -exec rm -rf -- {} + && git clone https://github.com/AMReX-Microelectronics/FerroX.git /opt/ferrox-src; fi && git -C /opt/ferrox-src fetch --all --tags && git -C /opt/ferrox-src checkout --detach $FerroXCommit"
Invoke-WslBash -Step "Checking out pinned FerroX source" -Command $checkoutCommand

Invoke-WslBash `
    -Step "Configuring the OpenMP CPU build" `
    -Command 'cmake -S /opt/ferrox-src -B /opt/ferrox-src/build -DCMAKE_BUILD_TYPE=Release -DFerroX_COMPUTE=OMP -DCMAKE_INSTALL_PREFIX=/opt/ferrox'
Invoke-WslBash `
    -Step "Building FerroX" `
    -Command 'cmake --build /opt/ferrox-src/build --parallel "$(nproc)"'
Invoke-WslBash `
    -Step "Installing FerroX" `
    -Command 'cmake --install /opt/ferrox-src/build'

$installedExecutable = (& wsl.exe -d $Distro -- bash -lc "find /opt/ferrox/bin -maxdepth 1 -type f -name 'main3d*.ex' -print -quit").Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($installedExecutable)) {
    throw "FerroX executable was not found after installation"
}
& wsl.exe -d $Distro -- ln -sfn $installedExecutable /opt/ferrox/bin/ferrox
if ($LASTEXITCODE -ne 0) {
    throw "Creating the stable FerroX executable link failed"
}

$installedCommit = (& wsl.exe -d $Distro -- git -C /opt/ferrox-src rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $installedCommit -ne $FerroXCommit) {
    throw "Installed FerroX commit verification failed"
}

Write-Host "FerroX installation verified."
Write-Host "Pinned commit: $installedCommit"
Write-Host "Executable: /opt/ferrox/bin/ferrox"
