[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Windows.Forms
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
if ($args[0] -eq 'files') {
    $dialog = New-Object System.Windows.Forms.OpenFileDialog
    $dialog.Title = '选择已下载的权重（将复制并自动归位，保留原文件）'
    $dialog.Filter = 'Model files|*.safetensors;*.gguf;*.pth'
    $dialog.Multiselect = $true
    if ($dialog.ShowDialog($owner) -eq 'OK') { ConvertTo-Json -InputObject @($dialog.FileNames) -Compress }
    else { '[]' }
} else {
    $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
    $dialog.Description = '选择 models 根目录或包含已下载权重的文件夹'
    $dialog.ShowNewFolderButton = $true
    if ($dialog.ShowDialog($owner) -eq 'OK') { ConvertTo-Json -InputObject @($dialog.SelectedPath) -Compress }
    else { '[]' }
}
$owner.Dispose()
