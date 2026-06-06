# run_paper_trader.ps1
Set-Location "C:\Strategies\TQQQ Kelly"

$timestamp = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
$logFile   = "logs\paper_trader_$timestamp.log"

python ib_paper_trader.py --config live_trader.yaml >> $logFile 2>&1
