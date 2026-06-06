@echo off
REM === 切到脚本所在目录（不管从哪儿启动都能找到 ib_paper_trader.py） ===
cd /d "%~dp0"

REM === 建一个 logs 目录存日志（如果已存在就跳过） ===
if not exist "logs" (
    mkdir "logs"
)

REM === 运行策略，连 IB Gateway 纸交易端口 4002 ===
REM 如果 python 已经在 PATH 里，用这一行：
python "ib_paper_trader.py" --ib-host 127.0.0.1 --ib-port 4002 --ib-client-id 1 >> "logs\%DATE:~0,4%%DATE:~5,2%%DATE:~8,2%.log" 2>&1

REM 如果上面那行报 “python 不是内部或外部命令”，
REM 就把上面那行注释掉，改用下面这种写法（示例路径，自己改成你的 python.exe 路径）：
REM "C:\Users\Shawn\AppData\Local\Programs\Python\Python311\python.exe" "ib_paper_trader.py" --ib-host 127.0.0.1 --ib-port 4002 --ib-client-id 1 >> "logs\%DATE:~0,4%%DATE:~5,2%%DATE:~8,2%.log" 2>&1

REM 为了防止窗口一闪而过，暂停一下，方便你看输出
pause
